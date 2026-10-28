"""External pending-action bridge: digest-only contract, no source leakage."""

import hashlib
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from remote_operator.external_actions import ExternalActionBridge
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


if __name__ == "__main__":
    unittest.main()
