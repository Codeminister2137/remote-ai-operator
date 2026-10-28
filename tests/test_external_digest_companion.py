"""Companion tests for ExternalActionBridge SHA-256 hex digest validation.

Leaves tests/test_external_actions.py unchanged; covers the length-only gap:
submit() must reject 64-character non-hex / non-ASCII digests in every
digest position before persistence, while accepting valid lower/upper/mixed hex.
"""

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from remote_operator.external_actions import ExternalActionBridge
from remote_operator.store import Store


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class TestExternalDigestHexValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "store.sqlite3")
        self.bridge = ExternalActionBridge(self.store)

    def test_valid_lower_upper_mixed_hex_accepted(self):
        lower = _digest("hello")
        upper = lower.upper()
        mixed = "".join(c.upper() if i % 2 else c for i, c in enumerate(lower))
        for candidate, base, evidence in (
            (lower, _digest("base"), _digest("evidence")),
            (upper, _digest("base").upper(), _digest("evidence").upper()),
            (mixed, _digest("base"), _digest("evidence")),
        ):
            action = self.bridge.submit("ok", candidate, base, evidence)
            self.assertEqual(action.status, "submitted")
            self.assertEqual(action.candidate_digest, candidate)

    def test_non_hex_64char_rejected_in_each_position(self):
        good = _digest("good")
        bad = "g" * 64  # length-correct but 'g' is not hex
        for args in (
            (bad, good, good),
            (good, bad, good),
            (good, good, bad),
        ):
            with self.assertRaises(ValueError):
                self.bridge.submit("Write greeting", *args)

    def test_non_ascii_64char_rejected(self):
        good = _digest("good")
        for bad in ("é" * 64, "あ" * 64, "😀" * 64):
            with self.assertRaises(ValueError):
                self.bridge.submit("Write greeting", bad, good, good)
            with self.assertRaises(ValueError):
                self.bridge.submit("Write greeting", good, bad, good)
            with self.assertRaises(ValueError):
                self.bridge.submit("Write greeting", good, good, bad)

    def test_rejection_happens_before_persistence(self):
        good = _digest("good")
        with self.assertRaises(ValueError):
            self.bridge.submit("Write greeting", "z" * 64, good, good)
        self.assertEqual(self.bridge.recent(limit=10), [])

    def test_existing_length_and_type_checks_preserved(self):
        good = _digest("good")
        with self.assertRaises(ValueError):
            self.bridge.submit("Write greeting", "abc", good, good)
        with self.assertRaises(ValueError):
            self.bridge.submit("Write greeting", good, good, good + "00")


if __name__ == "__main__":
    unittest.main()
