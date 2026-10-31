"""Independent regression checks for task effects, recovery and approval boundaries."""

import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from remote_operator.control import (
    SOURCE,
    Operation,
    Operator,
    Validation,
    digest,
    read_source,
    write_source,
)
from remote_operator.scripted import REFERENCE, ScriptedModel


class FakeValidator:
    """Controller test double; never used by the application or CLI."""

    def __init__(self, passed=True):
        self.passed = passed

    def validate(self, workspace, candidate_digest, *, cancelled=None):
        return Validation(
            self.passed,
            candidate_digest,
            0 if self.passed else 1,
            "TEST DOUBLE, not executed validation",
            "test-double",
        )


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.seed = self.root / "seed.py"
        self.seed.write_text("# initial source\n", encoding="utf-8")
        self.operator = Operator(self.root / "runtime", self.seed, FakeValidator())
        self.task = self.operator.submit("Implement the issue import feature")
        self.task_id = self.task["id"]

    def tearDown(self):
        self.directory.cleanup()

    def ready(self):
        return self.operator.run(self.task_id, ScriptedModel(), inject_failure=True)

    def test_recovery_keeps_edit_and_applies_only_after_approval(self):
        task = self.ready()
        target = self.operator.folder(self.task_id, "target")
        self.assertEqual(read_source(target), "# initial source\n")
        events = self.operator.store.events(self.task_id)
        self.assertEqual(sum(event["kind"] == "recovered" for event in events), 1)
        writes = [
            event
            for event in events
            if event["kind"] == "tool_completed" and event["payload"]["name"] == "write_file"
        ]
        self.assertEqual(len(writes), 1)
        result = self.operator.approve(self.task_id, task["approval"]["nonce"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(read_source(target), REFERENCE)
        with self.assertRaises(ValueError):
            self.operator.approve(self.task_id, task["approval"]["nonce"])

    def test_approval_consumed_emits_debug_log(self):
        task = self.ready()
        with self.assertLogs("remote_operator.control", level=logging.DEBUG) as captured:
            result = self.operator.approve(self.task_id, task["approval"]["nonce"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(captured.records), 1)
        record = captured.records[0]
        self.assertEqual(record.levelname, "DEBUG")
        self.assertIn(self.task_id, record.getMessage())

    def test_candidate_change_invalidates_approval(self):
        task = self.ready()
        write_source(self.operator.folder(self.task_id, "candidate"), "# changed\n")
        with self.assertRaises(ValueError):
            self.operator.approve(self.task_id, task["approval"]["nonce"])
        self.assertEqual(
            read_source(self.operator.folder(self.task_id, "target")), "# initial source\n"
        )
        self.assertEqual(self.operator.store.get(self.task_id)["status"], "review_required")

    def test_line_ending_change_invalidates_exact_byte_approval(self):
        task = self.ready()
        candidate = self.operator.folder(self.task_id, "candidate") / SOURCE
        candidate.write_bytes(candidate.read_bytes().replace(b"\n", b"\r\n"))
        with self.assertRaises(ValueError):
            self.operator.approve(self.task_id, task["approval"]["nonce"])
        self.assertEqual(
            read_source(self.operator.folder(self.task_id, "target")), "# initial source\n"
        )

    def test_target_change_invalidates_approval(self):
        task = self.ready()
        target = self.operator.folder(self.task_id, "target")
        write_source(target, "# external edit\n")
        with self.assertRaises(ValueError):
            self.operator.approve(self.task_id, task["approval"]["nonce"])
        self.assertEqual(read_source(target), "# external edit\n")

    def test_validation_change_invalidates_approval(self):
        task = self.ready()
        changed = self.operator.store.get(self.task_id)
        changed["evidence"]["output"] = "changed"
        self.operator.store.save(changed, "test_change")
        with self.assertRaises(ValueError):
            self.operator.approve(self.task_id, task["approval"]["nonce"])

    def test_wrong_expired_and_other_task_approval(self):
        task = self.ready()
        for nonce, now in (
            ("wrong", None),
            (task["approval"]["nonce"], task["approval"]["expires"] + 1),
        ):
            with self.assertRaises(ValueError):
                self.operator.approve(self.task_id, nonce, now=now)
        other = self.operator.submit("Another task")
        other = self.operator.run(other["id"], ScriptedModel())
        with self.assertRaises(ValueError):
            self.operator.approve(other["id"], task["approval"]["nonce"])

    def test_non_ascii_and_non_string_nonces_refused_without_mutation(self):
        from remote_operator.scripted import REFERENCE as _REFERENCE

        task = self.ready()
        valid = task["approval"]["nonce"]
        before_events = self.operator.store.events(self.task_id)
        before_candidate = read_source(self.operator.folder(self.task_id, "candidate"))
        before_target = read_source(self.operator.folder(self.task_id, "target"))
        bad_nonces = [
            "é" * len(valid),
            "a" * (len(valid) - 1) + "é",
            "\ud800" * len(valid),
            "x" * len(valid),
            None,
            12345,
            b"x" * len(valid),
            ["x" * len(valid)],
        ]
        for bad in bad_nonces:
            with self.subTest(nonce=repr(bad)):
                try:
                    self.operator.approve(self.task_id, bad)
                except ValueError as exc:
                    self.assertEqual(str(exc), "Invalid or expired approval")
                    self.assertNotIn(repr(bad)[:20].strip("'[]"), str(exc))
                else:
                    self.fail("bad nonce must raise ValueError")
                current = self.operator.store.get(self.task_id)
                self.assertEqual(current["status"], "awaiting_approval")
                self.assertEqual(current["approval"]["nonce"], valid)
        self.assertEqual(
            read_source(self.operator.folder(self.task_id, "candidate")), before_candidate
        )
        self.assertEqual(read_source(self.operator.folder(self.task_id, "target")), before_target)
        self.assertEqual(self.operator.store.events(self.task_id), before_events)
        result = self.operator.approve(self.task_id, valid)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(read_source(self.operator.folder(self.task_id, "target")), _REFERENCE)

    def test_failed_validation_cannot_request_approval(self):
        self.operator.validator = FakeValidator(False)
        with self.assertRaisesRegex(ValueError, "passing validation"):
            self.ready()
        self.assertIsNone(self.operator.store.get(self.task_id)["approval"])

    def test_reject_prevents_application(self):
        task = self.ready()
        self.operator.reject(self.task_id)
        with self.assertRaises(ValueError):
            self.operator.approve(self.task_id, task["approval"]["nonce"])

    def test_reject_emits_debug_log(self):
        self.ready()
        with self.assertLogs("remote_operator.control", level=logging.DEBUG) as captured:
            result = self.operator.reject(self.task_id)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(len(captured.records), 1)
        record = captured.records[0]
        self.assertEqual(record.levelname, "DEBUG")
        self.assertIn(self.task_id, record.getMessage())

    def test_restart_cannot_blindly_replay_running_edit(self):
        task = self.operator.store.get(self.task_id)
        task["status"] = "running"
        task["pending"] = {
            "name": "write_file",
            "arguments": {"path": SOURCE, "content": REFERENCE},
        }
        self.operator.store.save(task, "tool_started")
        restarted = Operator(self.root / "runtime", self.seed, FakeValidator())
        with self.assertRaises(ValueError):
            restarted.run(self.task_id, ScriptedModel())
        with self.assertRaises(ValueError):
            restarted.reconcile(self.task_id)
        write_source(restarted.folder(self.task_id, "candidate"), REFERENCE)
        reconciled = restarted.reconcile(self.task_id)
        self.assertEqual(reconciled["status"], "paused")
        self.assertEqual(reconciled["history"][-1]["result"]["digest"], digest(REFERENCE))
        self.assertEqual(
            restarted.run(self.task_id, ScriptedModel())["status"], "awaiting_approval"
        )

    def test_crash_after_apply_is_reconciled_without_replay(self):
        task = self.ready()
        task["status"] = "applying"
        self.operator.store.save(task, "apply_started")
        target = self.operator.folder(self.task_id, "target")
        write_source(target, REFERENCE)
        reconciled = self.operator.reconcile(self.task_id)
        self.assertEqual(reconciled["status"], "completed")
        self.assertIsNone(reconciled["approval"])

    def test_event_cursor_and_restart_preserve_history(self):
        self.ready()
        events = self.operator.store.events(self.task_id)
        cursor = events[2]["sequence"]
        restarted = Operator(self.root / "runtime", self.seed, FakeValidator())
        self.assertEqual(restarted.store.events(self.task_id, cursor), events[3:])

    def test_traversal_tools_refused_before_write(self):
        class BadModel:
            name = "invalid-test"

            def next_operation(self, history, *, prompt):
                from remote_operator.control import Operation

                return Operation("write_file", {"path": "../outside.py", "content": "bad"})

        with self.assertRaises(ValueError):
            self.operator.run(self.task_id, BadModel())
        self.assertFalse((self.root / "outside.py").exists())

    def test_source_symlink_refused(self):
        candidate = self.operator.folder(self.task_id, "candidate")
        (candidate / SOURCE).unlink()
        try:
            (candidate / SOURCE).symlink_to(self.seed)
        except OSError:
            self.skipTest("Creating symlinks requires platform permission")
        with self.assertRaises(ValueError):
            read_source(candidate)

    def test_independent_controller_cannot_mutate_while_first_owns_execution(self):
        other = Operator(self.root / "runtime", self.seed, FakeValidator())
        with self.operator.lock:
            with self.assertRaisesRegex(ValueError, "active"):
                other.run(self.task_id, ScriptedModel())
            with self.assertRaisesRegex(ValueError, "active"):
                other.submit("Competing task")
        self.assertEqual(other.store.get(self.task_id)["status"], "queued")

    @unittest.skipUnless(__import__("os").name == "nt", "Windows junction test")
    def test_workspace_junction_refused(self):
        import subprocess

        candidate = self.operator.folder(self.task_id, "candidate")
        (candidate / SOURCE).unlink()
        candidate.rmdir()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / SOURCE).write_text("# untouched\n", encoding="utf-8")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(candidate), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with self.assertRaises(ValueError):
            write_source(candidate, "# forbidden\n")
        self.assertEqual((outside / SOURCE).read_text(), "# untouched\n")

    def test_original_task_reaches_actor_after_transport_recovery(self):
        prompts = []

        class ObservingModel(ScriptedModel):
            def next_operation(self, history, *, prompt):
                prompts.append(prompt)
                return super().next_operation(history, prompt=prompt)

        self.operator.run(self.task_id, ObservingModel(), inject_failure=True)
        self.assertGreater(len(prompts), 1)
        self.assertEqual(set(prompts), {self.task["prompt"]})

    def test_empty_and_oversized_prompts_refused(self):
        for prompt in ("", " ", "a" * 4001):
            with self.assertRaises(ValueError):
                self.operator.submit(prompt)

    def test_unknown_tool_error_includes_action_name(self):
        class BadToolModel:
            name = "bad-tool-model"

            def next_operation(self, history, *, prompt):
                return Operation("evil_tool", {})

        with self.assertRaises(ValueError) as ctx:
            self.operator.run(self.task_id, BadToolModel())
        msg = str(ctx.exception)
        self.assertIn("evil_tool", msg)
        self.assertIn("Unknown or invalid tool", msg)

    def test_control_timeout_emits_debug_log(self):
        with mock.patch("remote_operator.control.time.monotonic", return_value=9999):
            with self.assertLogs("remote_operator.control", level=logging.DEBUG) as captured:
                with self.assertRaises(TimeoutError):
                    self.operator._check_control(self.task_id, deadline=0)
        self.assertEqual(len(captured.records), 1)
        record = captured.records[0]
        self.assertEqual(record.levelname, "DEBUG")
        self.assertIn(self.task_id, record.getMessage())


if __name__ == "__main__":
    unittest.main()
