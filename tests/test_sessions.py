"""Session-key lifetime and principal-isolation checks;all model traffic is mocked."""

import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import httpx

from remote_operator.credentials import SessionKeys, SessionUnavailable
from remote_operator.meta import InferenceGrant, MetaFailure, MetaModel
from remote_operator.scoped import ScopedOperators
from remote_operator.scripted import ScriptedModel
from remote_operator.store import Store

KEY_A = "synthetic-session-key-for-A"
KEY_B = "synthetic-session-key-for-B"
ROOT = Path(__file__).resolve().parents[1]


class SessionKeysTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.keys = SessionKeys(lifetime=30, capacity=2, clock=lambda: self.now)

    def test_tokens_are_distinct_from_keys_and_subjects_cannot_borrow_other_keys(self):
        token_a = self.keys.open("subjectA", KEY_A)
        token_b = self.keys.open("subjectB", KEY_B)
        self.assertNotIn(KEY_A, token_a)
        self.assertNotEqual(token_a, token_b)
        self.assertEqual(self.keys.subject(token_a), "subjectA")
        self.assertEqual(self.keys.key_for(token_a, subject="subjectA"), KEY_A)
        with self.assertRaises(SessionUnavailable):
            self.keys.key_for(token_b, subject="subjectA")
        self.assertNotIn(KEY_A, repr(self.keys))
        self.assertNotIn(KEY_A, repr(self.keys._entries))

    def test_expiry_at_boundary_denies_borrow_and_purges_reference(self):
        token = self.keys.open("subjectA", KEY_A)
        self.now = 130
        with self.assertRaises(SessionUnavailable):
            self.keys.key_for(token, subject="subjectA")
        self.assertEqual(self.keys._entries, {})

    def test_explicit_purge_and_revoke_remove_only_selected_credentials(self):
        a = self.keys.open("subjectA", KEY_A)
        b = self.keys.open("subjectB", KEY_B)
        self.keys.revoke(a)
        self.keys.revoke(a)
        self.assertEqual(self.keys.key_for(b, subject="subjectB"), KEY_B)
        self.now = 131
        self.assertEqual(self.keys.purge_expired(), 1)
        self.keys.clear()
        with self.assertRaises(SessionUnavailable):
            self.keys.subject(b)

    def test_restart_does_not_restore_keys_and_reentry_keeps_trusted_subject(self):
        token = self.keys.open("subjectA", KEY_A)
        restarted = SessionKeys()
        with self.assertRaises(SessionUnavailable):
            restarted.subject(token)
        new_token = restarted.open("subjectA", KEY_A)
        self.assertEqual(restarted.subject(new_token), "subjectA")
        self.assertNotEqual(token, new_token)

    def test_capacity_is_finite_and_expired_slots_can_be_reused(self):
        self.keys.open("subjectA", KEY_A)
        self.keys.open("subjectB", KEY_B)
        with self.assertRaisesRegex(ValueError, "capacity"):
            self.keys.open("subjectC", KEY_B)
        self.now = 130
        self.keys.open("subjectC", KEY_B)
        self.assertEqual(len(self.keys._entries), 1)

    def test_concurrent_creation_cannot_exceed_capacity(self):
        def create(i):
            try:
                return self.keys.open("subject" + str(i), KEY_A)
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=8) as executor:
            tokens = list(executor.map(create, range(12)))
        self.assertEqual(sum(t is not None for t in tokens), 2)

    def test_bad_configuration_and_header_injection_are_rejected_without_echo(self):
        for value in (0, -1, True, float("inf"), float("nan"), 3601):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SessionKeys(lifetime=value)
        for key in ("short", KEY_A + "\n", KEY_A + " ", "x" * 8193):
            with self.subTest(key_type=type(key)), self.assertRaises(ValueError) as exc:
                self.keys.open("subjectA", key)
            self.assertNotIn(KEY_A, str(exc.exception))
        # A keyless session is a supported hosted state,not an invalid key.
        token = self.keys.open("subjectA")
        self.assertFalse(self.keys.has_key(token, subject="subjectA"))
        for token in ("../../", None, "x" * 300):
            with self.assertRaises(SessionUnavailable):
                self.keys.subject(token)
        for subject in ("../other", "a/b", "", "x" * 129):
            with self.assertRaises(ValueError):
                self.keys.open(subject, KEY_A)

    def test_attach_rejects_nonfinite_deadlines_for_new_slots_without_consuming_capacity(self):
        self.keys.open("subjectA", KEY_A)
        external = "X" * 40
        invalid = [float("nan"), float("inf"), float("-inf")]
        invalid.extend([True, False, "130", ["130"], {"at": 130}, 10**1000, object()])
        for value in invalid:
            with self.subTest(value=repr(value)):
                before = dict(self.keys._entries)
                with self.assertRaises(SessionUnavailable) as exc:
                    self.keys.attach(external, KEY_B, subject="subjectB", expires_at=value)
                self.assertNotIn(repr(value), str(exc.exception))
                self.assertEqual(self.keys._entries, before)
                self.assertEqual(len(self.keys._entries), 1)
        # The slot is still free for a valid finite deadline.
        self.keys.attach(external, KEY_B, subject="subjectB", expires_at=self.now + 10)
        self.assertEqual(self.keys.key_for(external, subject="subjectB"), KEY_B)
        self.assertEqual(len(self.keys._entries), 2)

    def test_attach_rejects_nonfinite_deadlines_for_open_sessions_without_replacing_key(self):
        token = self.keys.open("subjectA", KEY_A)
        original_expires = self.keys._entries[self.keys._identity(token)].expires
        for value in (float("nan"), float("inf"), float("-inf"), True, 10**1000, "130", [130]):
            with self.subTest(value=repr(value)):
                with self.assertRaises(SessionUnavailable) as exc:
                    self.keys.attach(token, KEY_B, expires_at=value)
                self.assertNotIn(repr(value), str(exc.exception))
        entry = self.keys._entries[self.keys._identity(token)]
        self.assertEqual(entry.key, KEY_A)
        self.assertEqual(entry.expires, original_expires)
        self.assertEqual(self.keys.key_for(token, subject="subjectA"), KEY_A)
        # Advancing far beyond the normal lifetime still uses the original expiry.
        self.now = 1000
        with self.assertRaises(SessionUnavailable):
            self.keys.key_for(token, subject="subjectA")

    def test_attach_accepts_finite_and_default_deadlines(self):
        token = self.keys.open("subjectA", KEY_A)
        self.keys.attach(token, KEY_B, expires_at=self.now + 20)
        self.assertEqual(self.keys.key_for(token, subject="subjectA"), KEY_B)
        self.keys.attach(token, KEY_B, expires_at=float(self.now + 20))
        self.assertEqual(self.keys.key_for(token, subject="subjectA"), KEY_B)
        keyless = self.keys.open("subjectB")
        self.keys.attach(keyless, KEY_B)
        self.assertEqual(self.keys.key_for(keyless, subject="subjectB"), KEY_B)
        external = "Y" * 40
        self.keys.revoke(token)
        self.keys.attach(external, KEY_B, subject="subjectC", expires_at=self.now + 10)
        self.assertEqual(self.keys.key_for(external, subject="subjectC"), KEY_B)


class SessionMetaTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name) / "tasks.sqlite3")
        self.now = 1
        self.keys = SessionKeys(lifetime=30, clock=lambda: self.now)
        self.token = self.keys.open("subjectA", KEY_A)
        self.grant = InferenceGrant(
            "session-fixture",
            "muse-spark-fixture",
            time.time() + 300,
            5,
            100000,
            1024,
            "1",
            "1",
            "1",
            True,
            True,
        )
        self.requests = []

    def actor(self, handler=None):
        def serve(request):
            self.requests.append(request)
            if handler:
                return handler(request)
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "status": "completed",
                            "call_id": "fixture-call",
                            "name": "read_file",
                            "arguments": '{"path":"issue_import.py"}',
                        }
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
                },
            )

        return MetaModel(
            "",
            self.grant,
            self.store,
            key_source=lambda: self.keys.key_for(self.token, subject="subjectA"),
            transport=httpx.MockTransport(serve),
        )

    def test_each_request_borrows_key_then_actor_drops_it(self):
        actor = self.actor()
        actor.next_operation([], prompt="fixture")
        self.assertEqual(self.requests[0].headers["Authorization"], "Bearer " + KEY_A)
        self.assertEqual(actor._key, "")
        self.assertNotIn(KEY_A, repr(actor.__dict__))
        self.assertNotIn(KEY_A, json.dumps(actor.last_receipt))
        self.assertNotIn(KEY_A, self.requests[0].content.decode())

    def test_revoked_session_sends_no_next_request_and_no_stale_receipt(self):
        actor = self.actor()
        actor.next_operation([], prompt="fixture")
        self.keys.revoke(self.token)
        with self.assertRaises(SessionUnavailable):
            actor.next_operation([], prompt="fixture")
        self.assertEqual(len(self.requests), 1)
        self.assertIsNone(actor.last_receipt)
        self.assertEqual(actor._key, "")
        with self.store.connect() as db:
            self.assertEqual(db.execute("select requests from allowances").fetchone()[0], 1)

    def test_expired_session_does_not_reserve_or_fall_back_to_other_credentials(self):
        actor = self.actor()
        self.now = 31
        with self.assertRaises(SessionUnavailable):
            actor.next_operation([], prompt="fixture")
        self.assertEqual(self.requests, [])
        with self.store.connect() as db:
            self.assertEqual(db.execute("select count(*) from allowances").fetchone()[0], 0)

    def test_error_echo_is_redacted_and_key_dropped_on_failure(self):
        actor = self.actor(lambda request: httpx.Response(401, json={"error": {"message": KEY_A}}))
        with self.assertRaises(MetaFailure) as exc:
            actor.next_operation([], prompt="fixture")
        self.assertNotIn(KEY_A, str(exc.exception))
        self.assertEqual(actor._key, "")
        self.assertNotIn(KEY_A, json.dumps(actor.last_receipt))

    def test_session_key_source_cannot_be_combined_with_static_owner_key(self):
        with self.assertRaises(ValueError):
            MetaModel(KEY_B, self.grant, self.store, key_source=lambda: KEY_A)

    def test_provider_echo_cannot_survive_in_receipts_or_retained_history(self):
        actor = self.actor(
            lambda request: httpx.Response(
                200,
                json={
                    "status": "completed",
                    "model": KEY_A,
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "Echo " + KEY_A}],
                        }
                    ],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                },
            )
        )
        operation = actor.next_operation([], prompt="fixture")
        self.assertNotIn(KEY_A, operation.summary)
        self.assertNotIn(KEY_A, json.dumps(actor.last_receipt))
        self.assertNotIn(KEY_A, json.dumps(actor._turns))


class ScopedOperatorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.factory = Mock(return_value=Mock())
        self.operators = ScopedOperators(
            self.root / "users", ROOT / "sample/issue_import.py", self.factory
        )

    def test_other_principal_cannot_read_or_change_known_task_id(self):
        a = self.operators.for_subject("subjectA")
        b = self.operators.for_subject("subjectB")
        task = a.submit("fixture task")
        task_id = task["id"]
        self.assertEqual(b.store.recent_tasks(), [])
        for action in (
            lambda: b.review(task_id),
            lambda: b.cancel(task_id),
            lambda: b.reject(task_id),
            lambda: b.approve(task_id, "unknown"),
            lambda: b.reconcile(task_id),
            lambda: b.revalidate(task_id),
            lambda: b.sample(task_id, "csv"),
            lambda: b.run(task_id, ScriptedModel()),
        ):
            with self.assertRaises(KeyError):
                action()
        self.assertEqual(a.store.get(task_id)["status"], "queued")
        self.assertEqual(len(a.store.events(task_id)), 1)
        self.assertNotEqual(a.root, b.root)

    def test_same_trusted_subject_reopens_store_without_recreating_task_or_budget(self):
        a = self.operators.for_subject("subjectA")
        task = a.submit("fixture task")
        a.store.reserve_allowance("fixture", "policy", 5, "0.1", "1")
        reopened = self.operators.for_subject("subjectA")
        self.assertEqual(reopened.store.get(task["id"])["status"], "queued")
        with reopened.store.connect() as db:
            self.assertEqual(
                db.execute("select requests,reserved_usd from allowances").fetchone(), (1, "0.1")
            )

    def test_request_paths_cannot_choose_parent_or_another_principal_directory(self):
        for subject in ("../subjectB", "C:\\private", "subjectA/../../subjectB"):
            with self.assertRaises(ValueError):
                self.operators.for_subject(subject)
        self.factory.assert_not_called()
        self.assertFalse((self.root / "users").exists())

    def test_linked_visitors_root_is_refused(self):
        import subprocess

        outside = self.root / "outside"
        outside.mkdir()
        linked = self.root / "linked-users"
        try:
            linked.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            # Directory symlinks may need elevation on Windows; a junction does not
            # and the guard must refuse both.
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(linked), str(outside)],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                self.skipTest("Creating a directory link requires platform permission")
        operators = ScopedOperators(linked, ROOT / "sample/issue_import.py", self.factory)
        with self.assertRaisesRegex(ValueError, "Linked principal"):
            operators.for_subject("subjectA")
        self.factory.assert_not_called()


class StoreModuleDocstringTests(unittest.TestCase):
    def test_module_docstring(self):
        import remote_operator.store as store_module

        expected = "Transactional local task records with ordered, host-owned events."
        self.assertIsNotNone(store_module.__doc__)
        self.assertTrue(store_module.__doc__.strip())
        self.assertEqual(store_module.__doc__, expected)

    def test_unknown_task_error_includes_identifier(self):
        store = Store(Path(tempfile.mkdtemp()) / "tasks.sqlite3")
        synthetic_id = "synthetic-id-12345"
        with self.assertRaises(KeyError) as ctx:
            store.get(synthetic_id)
        self.assertIn(synthetic_id, str(ctx.exception))

    def test_recent_tasks_limit_error_includes_value(self):
        store = Store(Path(tempfile.mkdtemp()) / "tasks.sqlite3")
        bad_limit = 999
        with self.assertRaises(ValueError) as ctx:
            store.recent_tasks(limit=bad_limit)
        self.assertIn(str(bad_limit), str(ctx.exception))


class StoreLoggingTests(unittest.TestCase):
    def test_store_creation_emits_debug_log(self):
        import remote_operator.store as store_module

        path = Path(tempfile.mkdtemp()) / "tasks.sqlite3"
        with self.assertLogs(store_module.__name__, level="DEBUG") as logs:
            Store(path)
        self.assertTrue(any("session store loaded" in m for m in logs.output))
        self.assertTrue(any(str(path) in m for m in logs.output))


if __name__ == "__main__":
    unittest.main()
