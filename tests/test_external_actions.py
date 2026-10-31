"""External pending-action bridge: digest-only contract, no source leakage."""

import hashlib
import inspect
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from remote_operator.external_actions import ExternalAction, ExternalActionBridge
from remote_operator.scripted import ScriptedModel
from remote_operator.store import Store


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class TestExternalActionBridge(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "store.sqlite3")
        self.bridge = ExternalActionBridge(self.store)

    def test_submit_and_review(self):
        action = self.bridge.submit(
            "Write greeting",
            _digest("hello"),
            _digest("base"),
            _digest("evidence"),
            expires_seconds=60,
        )
        self.assertEqual(action.status, "submitted")
        self.assertEqual(len(action.id), 32)
        self.assertEqual(len(action.nonce), 32)
        reviewed = self.bridge.get(action.id)
        self.assertEqual(reviewed.status, "submitted")

    def test_approve_requires_valid_nonce(self):
        action = self.bridge.submit(
            "Write greeting",
            _digest("hello"),
            _digest("base"),
            _digest("evidence"),
        )
        with self.assertRaises(ValueError):
            self.bridge.approve(action.id, "wrong-nonce")
        approved = self.bridge.approve(action.id, action.nonce)
        self.assertEqual(approved.status, "approved")
        self.assertIsNotNone(approved.resolved_at)

    def test_reject_and_cancel(self):
        action = self.bridge.submit(
            "Write greeting",
            _digest("hello"),
            _digest("base"),
            _digest("evidence"),
        )
        rejected = self.bridge.reject(action.id)
        self.assertEqual(rejected.status, "rejected")

        action2 = self.bridge.submit(
            "Write greeting",
            _digest("hello"),
            _digest("base"),
            _digest("evidence"),
        )
        cancelled = self.bridge.cancel(action2.id)
        self.assertEqual(cancelled.status, "cancelled")

    def test_expiry(self):
        action = self.bridge.submit(
            "Write greeting",
            _digest("hello"),
            _digest("base"),
            _digest("evidence"),
            expires_seconds=30,
            now=time.time(),
        )
        expired = self.bridge.get(action.id, now=action.expires + 1)
        self.assertEqual(expired.status, "expired")

    def test_invalid_digest_rejected(self):
        with self.assertRaises(ValueError):
            self.bridge.submit(
                "Write greeting",
                "not-a-digest",
                _digest("base"),
                _digest("evidence"),
            )

    def test_description_limits(self):
        with self.assertRaises(ValueError):
            self.bridge.submit(
                "",
                _digest("hello"),
                _digest("base"),
                _digest("evidence"),
            )
        with self.assertRaises(ValueError):
            self.bridge.submit(
                "x" * 4001,
                _digest("hello"),
                _digest("base"),
                _digest("evidence"),
            )

    def test_recent_lists_actions(self):
        a1 = self.bridge.submit(
            "First",
            _digest("a1"),
            _digest("base"),
            _digest("evidence"),
        )
        a2 = self.bridge.submit(
            "Second",
            _digest("a2"),
            _digest("base"),
            _digest("evidence"),
        )
        recent = self.bridge.recent(limit=10)
        self.assertEqual([a.id for a in recent], [a2.id, a1.id])

    def test_approve_error_message_includes_action_id(self):
        action = self.bridge.submit(
            "Write greeting",
            _digest("hello"),
            _digest("base"),
            _digest("evidence"),
        )
        approved = self.bridge.approve(action.id, action.nonce)
        # Second approve should fail and message must contain the action id
        with self.assertRaises(ValueError) as ctx:
            self.bridge.approve(action.id, action.nonce)
        msg = str(ctx.exception)
        self.assertIn(action.id, msg)
        self.assertIn(approved.status, msg)

    def test_retry_debug_log_emitted_on_concurrent_update(self):
        action = self.bridge.submit(
            "Write greeting",
            _digest("hello"),
            _digest("base"),
            _digest("evidence"),
            expires_seconds=30,
            now=time.time(),
        )
        # Simulate concurrent update by changing the stored record before expiry handling
        import json

        with self.bridge._store.connect() as db:
            row = db.execute(
                "SELECT record FROM external_actions WHERE id=?", (action.id,)
            ).fetchone()
            data = json.loads(row[0])
            data["status"] = "approved"
            db.execute(
                "UPDATE external_actions SET record=? WHERE id=?",
                (json.dumps(data, sort_keys=True), action.id),
            )
        with self.assertLogs("remote_operator.external_actions", level="DEBUG") as cm:
            expired = self.bridge._expire(action, now_val=action.expires + 5)
        self.assertTrue(any("retry" in m.lower() or "concurrent" in m.lower() for m in cm.output))
        self.assertEqual(expired.status, "approved")

    def test_timeout_debug_log_emitted_on_expiry(self):
        action = self.bridge.submit(
            "Write greeting",
            _digest("hello"),
            _digest("base"),
            _digest("evidence"),
            expires_seconds=30,
            now=time.time(),
        )
        with self.assertLogs("remote_operator.external_actions", level="DEBUG") as cm:
            expired = self.bridge.get(action.id, now=action.expires + 1)
        self.assertEqual(expired.status, "expired")
        self.assertTrue(
            any(
                ("expired" in m.lower() or "timeout" in m.lower()) and action.id in m
                for m in cm.output
            )
        )


class TestScriptedActionErrorMessage(unittest.TestCase):
    def test_unknown_action_error_includes_action_name(self):
        model = ScriptedModel()
        bad_action = "unsupported_action_xyz"
        history = [{"name": bad_action}]
        with self.assertRaises(ValueError) as ctx:
            model.next_operation(history, prompt="test")
        self.assertIn(bad_action, str(ctx.exception))


class TestExternalActionsDocstrings(unittest.TestCase):
    """Regression test ensuring public functions retain docstrings."""

    def test_public_api_has_docstrings(self):
        # Check class and public methods have meaningful docstrings
        self.assertTrue(ExternalAction.__doc__ and len(ExternalAction.__doc__.strip()) > 20)
        self.assertTrue(
            ExternalAction.to_dict.__doc__ and "Returns" in ExternalAction.to_dict.__doc__
        )
        self.assertTrue(
            ExternalActionBridge.__doc__ and len(ExternalActionBridge.__doc__.strip()) > 20
        )
        for name in ("submit", "get", "recent", "approve", "reject", "cancel"):
            method = getattr(ExternalActionBridge, name)
            doc = inspect.getdoc(method)
            self.assertIsNotNone(doc, f"Missing docstring for ExternalActionBridge.{name}")
            self.assertGreater(len(doc.strip()), 30, f"Docstring too short for {name}")
            # Ensure Args/Returns documentation is present
            self.assertIn("Args", doc, f"{name} docstring should document Args")

    def test_docstring_does_not_change_signature(self):
        sig = inspect.signature(ExternalActionBridge.submit)
        self.assertIn("description", sig.parameters)
        self.assertIn("candidate_digest", sig.parameters)
        self.assertIn("expires_seconds", sig.parameters)


if __name__ == "__main__":
    unittest.main()
