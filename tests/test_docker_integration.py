"""Actual isolated execution; opt in after preparing the documented Docker image."""

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from remote_operator.control import Operator, digest, read_source
from remote_operator.runner import DockerValidator
from remote_operator.scripted import REFERENCE, ScriptedModel

PROJECT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("OPERATOR_DOCKER_TESTS") == "1", "Docker integration opt-in")
class DockerIntegration(unittest.TestCase):
    def test_red_seed_recovery_validation_and_exact_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            validator = DockerValidator(PROJECT / "tests" / "acceptance")
            operator = Operator(Path(directory), PROJECT / "sample" / "issue_import.py", validator)
            task = operator.submit("Implement the issue import acceptance specification")
            task_id = task["id"]
            candidate = operator.folder(task_id, "candidate")
            red = validator.validate(candidate, digest(read_source(candidate)))
            self.assertFalse(red.passed, red.output)
            self.assertIn("NotImplementedError", red.output)
            try:
                ready = operator.run(task_id, ScriptedModel(), inject_failure=True)
            except Exception:
                evidence = (
                    PROJECT / ".runtime" / ("failed-integration-" + uuid.uuid4().hex + ".json")
                )
                evidence.parent.mkdir(exist_ok=True)
                evidence.write_text(
                    json.dumps(operator.store.get(task_id), indent=2), encoding="utf-8"
                )
                raise
            self.assertEqual(ready["status"], "awaiting_approval")
            self.assertTrue(ready["evidence"]["passed"], ready["evidence"]["output"])
            applied = operator.approve(task_id, ready["approval"]["nonce"])
            self.assertEqual(applied["status"], "completed")
            target = operator.folder(task_id, "target")
            self.assertEqual(read_source(target), REFERENCE)
            final = validator.validate(target, digest(read_source(target)))
            self.assertTrue(final.passed, final.output)
            self.assertEqual(
                len([item for item in operator.store.events(task_id) if item["kind"] == "applied"]),
                1,
            )

    def test_approved_sample_import_runs_only_in_isolated_target(self):
        with tempfile.TemporaryDirectory() as directory:
            validator = DockerValidator(PROJECT / "tests" / "acceptance")
            operator = Operator(Path(directory), PROJECT / "sample" / "issue_import.py", validator)
            task = operator.submit("Implement import")
            with self.assertRaises(ValueError):
                operator.sample(task["id"], "id,title,status\n1,New,open\n")
            ready = operator.run(task["id"], ScriptedModel())
            operator.approve(task["id"], ready["approval"]["nonce"])
            result = operator.sample(
                task["id"], "id,title,status\nexisting,Duplicate,open\nnew,New,open\nbad,,closed\n"
            )
            self.assertEqual([item["id"] for item in result["issues"]], ["new"])
            self.assertEqual(len(result["errors"]), 2)
            target = operator.folder(task["id"], "target") / "issue_import.py"
            target.write_text("# changed after approval", encoding="utf-8")
            with self.assertRaises(ValueError):
                operator.sample(task["id"], "id,title,status\n")

    def test_cancelled_container_is_removed_without_running_to_completion(self):
        import time

        from remote_operator.control import write_source

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            write_source(workspace, "import time\ntime.sleep(30)\n")
            began = time.monotonic()
            validator = DockerValidator(PROJECT / "tests" / "acceptance")
            result = validator.validate(
                workspace,
                digest(read_source(workspace)),
                cancelled=lambda: time.monotonic() - began > 0.5,
            )
            self.assertFalse(result.passed)
            self.assertIn("cancelled", result.output)
            self.assertLess(time.monotonic() - began, 12)

    def test_container_deadline_works_without_the_controller_watchdog(self):
        import subprocess

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "issue_import.py").write_text(
                "import time\ntime.sleep(60)\n", encoding="utf-8"
            )
            validator = DockerValidator(PROJECT / "tests" / "acceptance", timeout=2)
            name = "remote-operator-" + uuid.uuid4().hex
            try:
                # Deliberately use command directly; _run's host watchdog is absent.
                result = subprocess.run(
                    validator.command(workspace, name), capture_output=True, check=False, timeout=10
                )
                self.assertEqual(result.returncode, 137, result.stdout.decode(errors="replace"))
                state = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Running}}", name],
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
                self.assertEqual(state.returncode, 0)
                self.assertEqual(state.stdout.strip(), b"false")
            finally:
                subprocess.run(
                    ["docker", "rm", "--force", name], capture_output=True, check=False, timeout=10
                )
