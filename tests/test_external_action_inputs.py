"""Malformed external-action inputs preserve pending valid approvals."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from remote_operator.external_actions import ExternalActionBridge
from remote_operator.store import Store


class NonceContract(unittest.TestCase):
    def test_malformed_nonce_is_refused_without_consuming_valid_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = ExternalActionBridge(Store(Path(folder) / "state.sqlite3"))
            digest = hashlib.sha256(b"fresh public malformed nonce").hexdigest()
            action = bridge.submit("Synthetic nonce", digest, digest, digest)
            for index, value in enumerate(
                ("\u00e9", "\ud800", None, 17, ["x"], {"nonce": "x"}, b"x", "wrong")
            ):
                with self.subTest(index=index):
                    with self.assertRaises(ValueError):
                        bridge.approve(action.id, value)
                    self.assertEqual(bridge.get(action.id).status, "submitted")
            self.assertEqual(bridge.approve(action.id, action.nonce).status, "approved")
            with self.assertRaises(ValueError):
                bridge.approve(action.id, action.nonce)

    def test_nonstring_identifier_is_refused_consistently(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = ExternalActionBridge(Store(Path(folder) / "state.sqlite3"))
            for value in (None, 17, [], {}, b"a" * 32):
                with self.subTest(value_type=type(value).__name__):
                    with self.assertRaises(ValueError):
                        bridge.get(value)


class ScopedModuleDocstring(unittest.TestCase):
    def test_scoped_module_has_docstring(self):
        import remote_operator.scoped as scoped

        self.assertTrue(scoped.__doc__)
        self.assertTrue(scoped.__doc__.strip())
