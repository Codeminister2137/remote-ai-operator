"""Cancellation is durable and late model responses cannot authorize effects."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from remote_operator.control import Operation, Operator, Validation, read_source
from remote_operator.scripted import ScriptedModel


class CancellationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        seed = self.root / "seed.py"
        seed.write_text("# unchanged\n", encoding="utf-8")

        class Validator:
            def validate(self, workspace, candidate_digest, *, cancelled=None):
                return Validation(True, candidate_digest, 0, "fixture", "test-double")

        self.operator = Operator(self.root / "runtime", seed, Validator())
        self.task = self.operator.submit("Task")
        self.task_id = self.task["id"]

    def tearDown(self):
        self.directory.cleanup()

    def test_cancel_queued_and_pending_approval_prevents_effects(self):
        self.assertEqual(self.operator.cancel(self.task_id)["status"], "cancelled")
        with self.assertRaises(ValueError):
            self.operator.run(self.task_id, ScriptedModel())
        task = self.operator.submit("Second")
        ready = self.operator.run(task["id"], ScriptedModel())
        self.operator.cancel(task["id"])
        with self.assertRaises(ValueError):
            self.operator.approve(task["id"], ready["approval"]["nonce"])

    def test_cancel_during_model_call_discards_late_edit(self):
        entered, release = threading.Event(), threading.Event()
        result = []

        class BlockingModel:
            name = "blocking-fixture"

            def next_operation(self, history, *, prompt):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Test release missing")
                return Operation("write_file", {"path": "issue_import.py", "content": "# late"})

        worker = threading.Thread(
            target=lambda: result.append(self.operator.run(self.task_id, BlockingModel()))
        )
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertEqual(self.operator.cancel(self.task_id)["status"], "cancellation_requested")
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]["status"], "cancelled")
        self.assertEqual(
            read_source(self.operator.folder(self.task_id, "candidate")), "# unchanged\n"
        )

    def test_deadline_after_model_response_prevents_new_edit(self):
        class LateModel:
            name = "late-fixture"

            def next_operation(self, history, *, prompt):
                return Operation("write_file", {"path": "issue_import.py", "content": "# late"})

        with (
            patch("remote_operator.control.time.monotonic", side_effect=[0, 0, 121]),
            self.assertRaises(TimeoutError),
        ):
            self.operator.run(self.task_id, LateModel())
        self.assertEqual(
            read_source(self.operator.folder(self.task_id, "candidate")), "# unchanged\n"
        )

    def test_fresh_validation_replaces_nonce_and_invalidates_old_approval(self):
        first = self.operator.run(self.task_id, ScriptedModel())
        second = self.operator.revalidate(self.task_id)
        self.assertNotEqual(first["approval"]["nonce"], second["approval"]["nonce"])
        with self.assertRaises(ValueError):
            self.operator.approve(self.task_id, first["approval"]["nonce"])

    def test_cancellation_cannot_be_accepted_after_apply_admission(self):
        task = self.operator.store.get(self.task_id)
        task["status"] = "applying"
        self.operator.store.save(task, "fixture_state")
        with self.assertRaises(ValueError):
            self.operator.store.request_cancel(self.task_id)
        self.assertFalse(self.operator.store.cancelled(self.task_id))

    def test_changed_validation_policy_invalidates_reviewed_action(self):
        class PolicyValidator:
            policy = "policy-one"

            def policy_digest(self):
                return self.policy

            def validate(self, workspace, candidate_digest, *, cancelled=None):
                return Validation(True, candidate_digest, 0, "fixture", "test-double", self.policy)

        validator = PolicyValidator()
        self.operator.validator = validator
        task = self.operator.run(self.task_id, ScriptedModel())
        validator.policy = "policy-two"
        with self.assertRaises(ValueError):
            self.operator.approve(self.task_id, task["approval"]["nonce"])
        self.assertEqual(read_source(self.operator.folder(self.task_id, "target")), "# unchanged\n")

    def test_cancel_request_wins_before_atomic_apply_admission(self):
        task = self.operator.run(self.task_id, ScriptedModel())
        self.operator.store.request_cancel(self.task_id)
        with self.assertRaises(ValueError):
            self.operator.approve(self.task_id, task["approval"]["nonce"])
        self.assertEqual(read_source(self.operator.folder(self.task_id, "target")), "# unchanged\n")
