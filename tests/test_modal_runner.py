"""Offline Modal lifecycle contracts and supervisor execution in local Docker."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from remote_operator.control import digest
from remote_operator.modal_runner import ModalValidator
from remote_operator.runner import DEFAULT_IMAGE, DockerValidator
from remote_operator.scripted import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
CHECKS = ROOT / "tests" / "acceptance"
PASS_OUTPUT = 'OPERATOR_CHECKS {"tests":7,"failures":0,"errors":0,"skipped":0}\n'


class ModalRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name)
        (self.workspace / "issue_import.py").write_bytes(REFERENCE.encode())
        self.sandbox = Mock()
        self.sandbox.poll.return_value = 0
        self.sandbox.stdout.read.return_value = json.dumps(
            {"code": 0, "output": PASS_OUTPUT, "failure": None}
        )
        self.sdk = SimpleNamespace(
            Image=SimpleNamespace(from_registry=Mock(return_value="image-double")),
            Sandbox=SimpleNamespace(create=Mock(return_value=self.sandbox)),
        )
        self.runner = ModalValidator(
            CHECKS, "app-double", sdk=self.sdk, allow_remote_execution=True
        )

    def test_default_denies_remote_execution_and_optional_sdk_not_required(self):
        runner = ModalValidator(CHECKS, "app-double", sdk=self.sdk)
        result = runner.validate(self.workspace, digest(REFERENCE))
        self.assertFalse(result.passed)
        self.sdk.Sandbox.create.assert_not_called()
        self.assertIn("authorization", result.output)

    def test_only_registered_files_transferred_and_all_sandbox_guards_explicit(self):
        result = self.runner.validate(self.workspace, digest(REFERENCE))
        self.assertTrue(result.passed)
        self.assertEqual(result.runner, "modal_sandbox")
        options = self.sdk.Sandbox.create.call_args.kwargs
        self.assertTrue(options["block_network"])
        self.assertEqual(options["memory"], (128, 256))
        self.assertEqual(options["cpu"], (0.25, 1))
        self.assertEqual(options["secrets"], [])
        self.assertEqual(options["env"], {})
        self.assertEqual(options["volumes"], {})
        self.assertFalse(options["include_oidc_identity_token"])
        self.assertEqual(options["encrypted_ports"], [])
        request = json.loads(self.sandbox.stdin.write.call_args.args[0])
        self.assertEqual(
            set(request["files"]),
            {
                "/workspace/issue_import.py",
                "/checks/run_checks.py",
                "/checks/test_import.py",
                "/checks/preview.py",
            },
        )
        self.sandbox.stdin.write_eof.assert_called_once()
        self.sandbox.stdin.drain.assert_called_once()
        self.sandbox.terminate.assert_called_once_with(wait=True)

    def test_cancellation_before_launch_never_creates_sandbox(self):
        result = self.runner.validate(self.workspace, digest(REFERENCE), cancelled=lambda: True)
        self.assertFalse(result.passed)
        self.sdk.Sandbox.create.assert_not_called()

    def test_inflight_cancellation_terminates_owned_sandbox(self):
        self.sandbox.poll.return_value = None
        checks = iter([False, True])
        self.sandbox.terminate.side_effect = lambda **kwargs: setattr(
            self.sandbox.poll, "return_value", -9
        )
        result = self.runner.validate(
            self.workspace, digest(REFERENCE), cancelled=lambda: next(checks)
        )
        self.assertFalse(result.passed)
        self.assertIn("cancelled", result.output)
        self.sandbox.terminate.assert_called_once_with(wait=True)
        self.sandbox.stdout.read.assert_not_called()

    def test_failed_cleanup_cannot_report_success_and_keeps_test_evidence(self):
        self.sandbox.terminate.side_effect = RuntimeError("sensitive-SDK-error")
        result = self.runner.validate(self.workspace, digest(REFERENCE))
        self.assertFalse(result.passed)
        self.assertIn("OPERATOR_CHECKS", result.output)
        self.assertIn("cleanup could not be verified", result.output)
        self.assertNotIn("sensitive-SDK-error", result.output)

    def test_sdk_failure_is_not_retried_or_switched_to_host(self):
        self.sdk.Sandbox.create.side_effect = RuntimeError("secret-SDK-diagnostic")
        result = self.runner.validate(self.workspace, digest(REFERENCE))
        self.assertFalse(result.passed)
        self.assertIn("create Sandbox (RuntimeError)", result.output)
        self.assertNotIn("secret-SDK", result.output)
        self.sdk.Sandbox.create.assert_called_once()
        self.assertEqual(str(self.runner._diagnostics[0][1]), "secret-SDK-diagnostic")

    def test_bad_receipt_and_changed_candidate_never_pass(self):
        for raw in (
            "not-json",
            json.dumps({"code": True, "output": PASS_OUTPUT, "failure": None}),
            json.dumps({"code": 0, "output": PASS_OUTPUT, "failure": "deadline"}),
        ):
            with self.subTest(raw=raw):
                self.sandbox.stdout.read.return_value = raw
                self.assertFalse(self.runner.validate(self.workspace, digest(REFERENCE)).passed)
        self.sandbox.stdout.read.return_value = json.dumps(
            {"code": 0, "output": PASS_OUTPUT, "failure": None}
        )
        self.assertFalse(self.runner.validate(self.workspace, digest("different source")).passed)

    def test_backend_and_supervisor_policy_changes_invalidate_approval(self):
        original = self.runner.policy_digest()
        self.assertNotEqual(original, DockerValidator(CHECKS).policy_digest())
        with patch.object(self.runner, "bootstrap", return_value="changed supervisor"):
            self.assertNotEqual(original, self.runner.policy_digest())

    def test_host_deadline_terminates_sandbox_without_accepting_receipt(self):
        self.sandbox.poll.return_value = None
        self.sandbox.terminate.side_effect = lambda **kwargs: setattr(
            self.sandbox.poll, "return_value", -9
        )
        with patch("remote_operator.modal_runner.time.monotonic", side_effect=[0, 100]):
            result = self.runner.validate(self.workspace, digest(REFERENCE))
        self.assertFalse(result.passed)
        self.assertIn("host deadline", result.output)
        self.sandbox.stdout.read.assert_not_called()
        self.sandbox.terminate.assert_called_once_with(wait=True)

    def test_excessive_protected_input_refused_before_cloud_call(self):
        checks = self.workspace / "oversize-checks"
        checks.mkdir()
        for name in ("run_checks.py", "test_import.py", "preview.py"):
            (checks / name).write_bytes(b"x" * 65537)
        runner = ModalValidator(checks, "app-double", sdk=self.sdk, allow_remote_execution=True)
        with self.assertRaises(ValueError):
            runner._run(self.workspace)
        self.sdk.Sandbox.create.assert_not_called()

    def test_unknown_entry_and_excessive_check_refuse_before_upload(self):
        with self.assertRaises(ValueError):
            self.runner._run(self.workspace, entry="arbitrary.py")
        self.sdk.Sandbox.create.assert_not_called()

    def test_unknown_entry_message_includes_check_name(self):
        with self.assertRaises(ValueError) as ctx:
            self.runner.request(self.workspace, "arbitrary.py", b"")
        self.assertIn("arbitrary.py", str(ctx.exception))
        self.assertIn("Unknown protected runner entry", str(ctx.exception))
        self.sdk.Sandbox.create.assert_not_called()

    def test_unknown_entry_message_includes_operation_step_context(self):
        with self.assertRaises(ValueError) as ctx:
            self.runner.request(self.workspace, "arbitrary.py", b"")
        msg = str(ctx.exception)
        self.assertIsInstance(ctx.exception, ValueError)
        self.assertIn("arbitrary.py", msg)
        self.assertIn("operation", msg.lower())

    def test_local_backend_does_not_require_modal_installed(self):
        self.assertIsInstance(DockerValidator(CHECKS).policy_digest(), str)
        if importlib.util.find_spec("modal") is None:
            result = ModalValidator(CHECKS, "app-double", allow_remote_execution=True).validate(
                self.workspace, digest(REFERENCE)
            )
            self.assertFalse(result.passed)
            self.assertIn("load optional SDK", result.output)

    def test_docstrings_present_for_public_api(self):
        targets = [
            ModalValidator,
            ModalValidator.__init__,
            ModalValidator.bootstrap,
            ModalValidator.policy_settings,
            ModalValidator.request,
            ModalValidator._run,
        ]
        for obj in targets:
            with self.subTest(obj=obj):
                doc = obj.__doc__
                self.assertIsNotNone(doc)
                self.assertGreater(len(doc.strip()), 20)


@unittest.skipUnless(os.environ.get("OPERATOR_DOCKER_TESTS") == "1", "Docker opt-in required")
class SupervisorDockerTests(unittest.TestCase):
    def supervisor(self, source, entry="run_checks.py", payload="", timeout=3):
        files = {"/workspace/issue_import.py": source}
        files.update(
            {
                "/checks/" + name: (CHECKS / name).read_text(encoding="utf-8")
                for name in ("run_checks.py", "test_import.py", "preview.py")
            }
        )
        bootstrap = ModalValidator.bootstrap()
        name = "operator-supervisor-test-" + uuid.uuid4().hex
        try:
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--name",
                    name,
                    "-i",
                    "--network=none",
                    "--memory=256m",
                    "--pids-limit=80",
                    "--cap-drop=ALL",
                    "--cap-add=SETUID",
                    "--cap-add=SETGID",
                    "--cap-add=KILL",
                    "-e",
                    "META_API_KEY=synthetic-parent-only",
                    "-e",
                    "MODAL_TOKEN_SECRET=synthetic-parent-only",
                    "--security-opt=no-new-privileges",
                    "--pull=never",
                    DEFAULT_IMAGE,
                    "python",
                    "-I",
                    "-B",
                    "-c",
                    bootstrap,
                ],
                input=json.dumps(
                    {"files": files, "entry": entry, "payload": payload, "timeout": timeout}
                ).encode(),
                capture_output=True,
                timeout=20,
                check=False,
            )
        finally:
            cleanup = subprocess.run(
                ["docker", "rm", "-f", "-v", name], capture_output=True, timeout=10, check=False
            )
            self.assertTrue(
                cleanup.returncode == 0 or b"No such container" in cleanup.stderr,
                cleanup.stderr.decode(),
            )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return json.loads(result.stdout)

    def test_reference_checks_and_applied_preview_use_same_supervisor(self):
        result = self.supervisor(REFERENCE)
        self.assertEqual(result["code"], 0)
        self.assertIsNone(result["failure"])
        self.assertIn('"tests": 7', result["output"])
        preview = self.supervisor(
            REFERENCE,
            entry="preview.py",
            payload=json.dumps({"csv_text": "id,title,status\nx,New,OPEN\n", "existing": []}),
        )
        self.assertEqual(preview["code"], 0)
        self.assertIn('"id": "x"', preview["output"])

    def test_generated_child_nonroot_cannot_change_checks_or_read_inherited_secrets(self):
        source = """import os
assert os.getuid() == 65534
assert os.environ.get("META_API_KEY") is None
assert os.environ.get("MODAL_TOKEN_SECRET") is None
for target in ("/checks/run_checks.py", "/workspace/issue_import.py"):
    try:
        open(target,"w")
    except PermissionError:
        continue
    raise AssertionError("protected source writable")
print("CONTAINMENT_CONFIRMED")
raise RuntimeError("probe intentionally stops before fixture tests")
"""
        result = self.supervisor(source)
        self.assertNotEqual(result["code"], 0)
        self.assertIn("CONTAINMENT_CONFIRMED", result["output"])

    def test_infinite_child_and_unbounded_output_are_stopped(self):
        result = self.supervisor("import time\ntime.sleep(60)", timeout=1)
        self.assertIn("deadline", result["failure"])
        result = self.supervisor("import os\nwhile True: os.write(1,b'x'*4096)")
        self.assertIn("output allowance", result["failure"])
        self.assertLessEqual(len(result["output"].encode()), 65_536)


if __name__ == "__main__":
    unittest.main()
