"""Hosted session, isolation and visitor-key checks;all model traffic is mocked."""

import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import httpx

from remote_operator.control import Operator, Validation
from remote_operator.credentials import SessionCapacity, SessionKeys, SessionUnavailable
from remote_operator.hosted import AccessRefused, HostedSessions
from remote_operator.meta import InferenceGrant
from remote_operator.scoped import ScopedOperators
from remote_operator.scripted import ScriptedModel
from remote_operator.web import create_hosted_app

KEY = "synthetic-visitor-key-0001"
CODE = "demo-access-code-0001"
SECRET = "hosted-session-secret-" + "s" * 32
OTHER_SECRET = "different-session-secret-" + "o" * 32
ROOT = Path(__file__).resolve().parents[1]


class Validator:
    def validate(self, workspace, candidate_digest, *, cancelled=None):
        return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")


def grant():
    return InferenceGrant(
        "hosted-fixture",
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


class HostedSessionUnitTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.sessions = HostedSessions(
            CODE,
            sessions=SessionKeys(lifetime=30, capacity=4, clock=lambda: self.now),
            secret=SECRET,
        )

    def restarted(self, secret=SECRET):
        return HostedSessions(
            CODE,
            sessions=SessionKeys(lifetime=30, capacity=4, clock=lambda: self.now),
            secret=secret,
        )

    def test_access_code_is_required_and_never_echoed(self):
        for value in ("", "wrong-code", None, 7):
            with self.subTest(value=value), self.assertRaises(AccessRefused):
                self.sessions.open(value)
        token = self.sessions.open(CODE)
        self.assertNotIn(CODE, token)
        self.assertNotIn(CODE, repr(self.sessions))

    def test_non_ascii_wrong_codes_are_refused_without_echo(self):
        for value in ("démonstration-code-ünïcodé", "demo-access-code-0001\U0001f600", "\ud800"):
            with self.subTest(value=repr(value)), self.assertRaises(AccessRefused) as exc:
                self.sessions.open(value)
            self.assertEqual(str(exc.exception), "Access code refused")
            self.assertNotIn(value, str(exc.exception))
        token = self.sessions.open(CODE)
        self.assertNotIn(CODE, token)

    def test_subjects_are_server_generated_and_distinct(self):
        first = self.sessions.open(CODE)
        second = self.sessions.open(CODE)
        subject_a = self.sessions.subject(first)
        subject_b = self.sessions.subject(second)
        self.assertNotEqual(subject_a, subject_b)
        self.assertTrue(subject_a.startswith("visitor-"))

    def test_session_survives_without_key_and_drops_key_independently(self):
        token = self.sessions.open(CODE)
        subject = self.sessions.subject(token)
        self.assertFalse(self.sessions.has_key(token, subject))
        self.sessions.attach_key(token, KEY)
        self.assertTrue(self.sessions.has_key(token, subject))
        self.assertEqual(self.sessions.key_for(token, subject), KEY)
        self.sessions.drop_key(token, subject)
        self.assertFalse(self.sessions.has_key(token, subject))
        self.assertEqual(self.sessions.subject(token), subject)
        self.assertNotIn(KEY, repr(self.sessions.sessions))

    def test_expired_session_cannot_attach_or_borrow(self):
        token = self.sessions.open(CODE)
        subject = self.sessions.subject(token)
        self.now = 130
        with self.assertRaises(ValueError):
            self.sessions.attach_key(token, KEY)
        self.assertFalse(self.sessions.has_key(token, subject))
        # has_key's own purge already dropped the expired slot.
        self.assertEqual(self.sessions.purge_expired(), 0)

    def test_signed_session_resumes_the_same_subject_after_a_restart(self):
        token = self.sessions.open(CODE)
        subject = self.sessions.subject(token)
        self.sessions.attach_key(token, KEY)
        restarted = self.restarted()
        self.assertEqual(restarted.subject(token), subject)
        # Keys stay process-local; only identity and recorded task state survive.
        self.assertFalse(restarted.has_key(token, subject))
        with self.assertRaises(SessionUnavailable):
            restarted.key_for(token, subject)

    def test_token_signed_with_another_secret_is_refused(self):
        other = HostedSessions(
            CODE,
            sessions=SessionKeys(lifetime=30, capacity=4, clock=lambda: self.now),
            secret=OTHER_SECRET,
        )
        token = other.open(CODE)
        with self.assertRaises(SessionUnavailable):
            self.sessions.subject(token)

    def test_tampered_token_is_refused_without_echoing_it(self):
        token = self.sessions.open(CODE)
        body, signature = token.split(".")
        tampered_body = ("A" if body[0] != "A" else "B") + body[1:]
        tampered_signature = signature[:-1] + ("A" if signature[-1] != "A" else "B")
        for value in (
            token + "x",
            tampered_body + "." + signature,
            body + "." + tampered_signature,
            body,
            "",
            None,
        ):
            with self.subTest(value=type(value)), self.assertRaises(SessionUnavailable) as exc:
                self.sessions.subject(value)
            self.assertNotIn(token, str(exc.exception))

    def test_expired_signed_token_is_refused_before_key_lookup(self):
        token = self.sessions.open(CODE)
        self.now = 130
        with self.assertRaisesRegex(SessionUnavailable, "expired"):
            self.sessions.subject(token)

    def test_expired_session_message_includes_session_context_without_secret(self):
        token = self.sessions.open(CODE)
        self.now = 130
        sid = hashlib.sha256(token.encode()).hexdigest()[:8]
        with self.assertRaises(SessionUnavailable) as exc:
            self.sessions.subject(token)
        msg = str(exc.exception)
        self.assertIn(sid, msg)
        self.assertIn("expired", msg.lower())
        self.assertNotIn(CODE, msg)
        self.assertNotIn(token, msg)

    def test_keyless_sessions_do_not_consume_the_attached_key_capacity(self):
        small = HostedSessions(
            CODE,
            sessions=SessionKeys(lifetime=30, capacity=1, clock=lambda: self.now),
            secret=SECRET,
        )
        tokens = [small.open(CODE) for _ in range(5)]
        self.assertEqual(len({small.subject(token) for token in tokens}), 5)
        small.attach_key(tokens[0], KEY)
        with self.assertRaises(SessionCapacity):
            small.attach_key(tokens[1], KEY + "b")

    def test_logout_is_process_local_and_does_not_survive_a_restart(self):
        token = self.sessions.open(CODE)
        subject = self.sessions.subject(token)
        self.sessions.close(token)
        with self.assertRaises(SessionUnavailable):
            self.sessions.subject(token)
        self.assertEqual(self.restarted().subject(token), subject)

    def test_close_serializes_with_concurrent_purge(self):
        token = self.sessions.open(CODE)
        subject = self.sessions.subject(token)
        self.sessions.attach_key(token, KEY)
        snapshot_done = threading.Event()
        release = threading.Event()
        errors = []

        class BlockingRevoked(dict):
            def items(self):
                snapshot = list(super().items())
                snapshot_done.set()
                if not release.wait(5):
                    raise AssertionError("purge release timeout")
                return iter(snapshot)

        self.sessions._revoked = BlockingRevoked(self.sessions._revoked)

        def run_purge():
            try:
                self.sessions.purge_expired()
            except Exception as exc:  # noqa: BLE001 - captured for assertion
                errors.append(exc)

        def run_close():
            try:
                self.sessions.close(token)
            except Exception as exc:  # noqa: BLE001 - captured for assertion
                errors.append(exc)

        purge_thread = threading.Thread(target=run_purge)
        purge_thread.start()
        try:
            self.assertTrue(snapshot_done.wait(5), "purge did not snapshot")
            close_thread = threading.Thread(target=run_close)
            close_thread.start()
            try:
                # On buggy code close slips in while purge is paused and its
                # revocation is then discarded by the stale replacement. On
                # fixed code close blocks on the RLock until purge finishes.
                close_thread.join(2)
                release.set()
                close_thread.join(5)
                self.assertFalse(close_thread.is_alive(), "close did not finish")
            finally:
                release.set()
                close_thread.join(5)
        finally:
            release.set()
            purge_thread.join(5)
        self.assertFalse(purge_thread.is_alive(), "purge did not finish")
        self.assertEqual(errors, [])
        with self.assertRaises(SessionUnavailable):
            self.sessions.subject(token)
        self.assertFalse(self.sessions.has_key(token, subject))

    def test_close_serializes_with_concurrent_attach(self):
        token = self.sessions.open(CODE)
        subject = self.sessions.subject(token)
        entered = threading.Event()
        release = threading.Event()
        close_finished = threading.Event()
        errors = []
        store = self.sessions.sessions
        original_attach = store.attach

        def blocking_attach(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("attach release timeout")
            return original_attach(*args, **kwargs)

        store.attach = blocking_attach
        try:

            def run_attach():
                try:
                    self.sessions.attach_key(token, KEY)
                except Exception as exc:  # noqa: BLE001 - captured for assertion
                    errors.append(exc)

            def run_close():
                try:
                    self.sessions.close(token)
                except Exception as exc:  # noqa: BLE001 - captured for assertion
                    errors.append(exc)
                finally:
                    close_finished.set()

            attach_thread = threading.Thread(target=run_attach)
            attach_thread.start()
            try:
                self.assertTrue(entered.wait(5), "attach did not reach key store")
                close_thread = threading.Thread(target=run_close)
                close_thread.start()
                try:
                    # While attachment is paused, give close a bounded
                    # completion opportunity. Fixed code keeps close blocked
                    # on the hosted lock until release; the original lets it
                    # slip through before the key store write.
                    self.assertFalse(
                        close_finished.wait(1),
                        "close completed while attach held the lock",
                    )
                    self.assertTrue(close_thread.is_alive(), "close did not block")
                    release.set()
                    close_thread.join(5)
                    self.assertFalse(close_thread.is_alive(), "close did not finish")
                finally:
                    release.set()
                    close_thread.join(5)
            finally:
                release.set()
                attach_thread.join(5)
            self.assertFalse(attach_thread.is_alive(), "attach did not finish")
        finally:
            store.attach = original_attach
            release.set()
        self.assertEqual(errors, [])
        with self.assertRaises(SessionUnavailable):
            self.sessions.subject(token)
        self.assertFalse(self.sessions.has_key(token, subject))
        with self.assertRaises(SessionUnavailable):
            self.sessions.key_for(token, subject)
        fresh = self.sessions.open(CODE)
        fresh_subject = self.sessions.subject(fresh)
        self.sessions.attach_key(fresh, KEY)
        self.assertTrue(self.sessions.has_key(fresh, fresh_subject))

    def test_logout_removes_key_and_purge_prunes_revoked_expiry(self):
        token = self.sessions.open(CODE)
        subject = self.sessions.subject(token)
        self.sessions.attach_key(token, KEY)
        self.sessions.close(token)
        self.assertFalse(self.sessions.has_key(token, subject))
        with self.assertRaises(SessionUnavailable):
            self.sessions.subject(token)
        self.assertEqual(len(self.sessions._revoked), 1)
        self.now = 130
        self.sessions.purge_expired()
        self.assertEqual(dict(self.sessions._revoked), {})

    def test_bad_access_code_configuration_is_refused(self):
        for value in ("short", "x" * 257, "code with\nnewline" + "x" * 20, None):
            with self.subTest(value=type(value)), self.assertRaises(ValueError):
                HostedSessions(value)


class HostedAppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.root = root
        self.scoped = ScopedOperators(root / "visitors", ROOT / "sample/issue_import.py", Validator)
        self.sessions = HostedSessions(CODE)
        self.app = create_hosted_app(self.scoped, self.sessions)
        self.client = self.client_for(self.app, self.sessions)
        self.auth = {"X-Operator-Request": "1"}

    def client_for(self, app, sessions):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    async def enter(self, client=None, code=CODE):
        return await (client or self.client).post("/api/session", json={"code": code})

    async def test_config_is_public_but_tasks_need_a_session(self):
        config = (await self.client.get("/api/config")).json()
        self.assertTrue(config["hosted"])
        self.assertFalse(config["authenticated"])
        self.assertFalse(config["live_inference"])
        self.assertFalse(config["model_route_configured"])
        self.assertEqual((await self.client.get("/api/tasks")).status_code, 401)
        self.assertEqual(
            (await self.client.post("/api/session", json={"code": "wrong-code"})).status_code, 403
        )

    async def test_unicode_wrong_code_is_403_without_cookie_or_echo_then_valid_login(self):
        wrong = "démonstration-code-ünïcodé\U0001f600"
        refused = await self.enter(code=wrong)
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertNotIn("set-cookie", {key.lower() for key in refused.headers})
        self.assertNotIn(wrong, refused.text)
        self.assertFalse((await self.client.get("/api/config")).json()["authenticated"])
        accepted = await self.enter()
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertIn("set-cookie", {key.lower() for key in accepted.headers})
        self.assertTrue((await self.client.get("/api/config")).json()["authenticated"])

    async def test_session_cookie_is_httponly_and_strict_and_state_changes_need_header(self):
        response = await self.enter()
        self.assertEqual(response.status_code, 200, response.text)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertNotIn("secure", cookie)
        session = (await self.client.get("/api/config")).json()
        self.assertTrue(session["authenticated"])
        self.assertEqual(
            (await self.client.post("/api/tasks", json={"prompt": "no header"})).status_code, 403
        )
        created = await self.client.post("/api/tasks", headers=self.auth, json={"prompt": "task"})
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["mode"], "scripted")

    async def test_two_visitors_cannot_reach_each_others_tasks(self):
        first = self.client_for(self.app, self.sessions)
        second = self.client_for(self.app, self.sessions)
        try:
            await self.enter(first)
            await self.enter(second)
            task = (
                await first.post("/api/tasks", headers=self.auth, json={"prompt": "private"})
            ).json()
            path = "/api/tasks/" + task["id"]
            self.assertEqual((await first.get(path)).status_code, 200)
            self.assertEqual((await second.get(path)).status_code, 404)
            self.assertEqual((await second.get(path + "/events")).status_code, 404)
            for suffix in ("/run", "/cancel", "/reject", "/reconcile", "/revalidate"):
                self.assertEqual(
                    (await second.post(path + suffix, headers=self.auth, json={})).status_code, 404
                )
            self.assertEqual(
                (
                    await second.post(path + "/approve", headers=self.auth, json={"nonce": "x"})
                ).status_code,
                404,
            )
            self.assertEqual(
                (
                    await second.post(
                        path + "/sample", headers=self.auth, json={"csv_text": "id,title,status\n"}
                    )
                ).status_code,
                404,
            )
            self.assertEqual((await second.get("/api/tasks")).json(), [])
        finally:
            await first.aclose()
            await second.aclose()

    async def test_scripted_session_runs_offline_reference_without_a_key(self):
        await self.enter()
        task = (
            await self.client.post("/api/tasks", headers=self.auth, json={"prompt": "task"})
        ).json()
        path = "/api/tasks/" + task["id"]
        result = await self.client.post(
            path + "/run", headers=self.auth, json={"inject_failure": True}
        )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["status"], "awaiting_approval")
        self.assertEqual((await self.client.get("/api/allowance")).json(), {"enabled": False})

    async def test_key_attach_enables_visitor_route_and_never_returns_the_key(self):
        app = create_hosted_app(self.scoped, self.sessions, grant())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            await self.enter(client)
            before = (await client.get("/api/config")).json()
            self.assertTrue(before["model_route_configured"])
            self.assertFalse(before["session_key_attached"])
            attached = await client.post(
                "/api/session/key", headers=self.auth, json={"api_key": KEY}
            )
            self.assertEqual(attached.status_code, 200, attached.text)
            self.assertNotIn(KEY, attached.text)
            after = (await client.get("/api/config")).json()
            self.assertTrue(after["session_key_attached"])
            self.assertTrue(after["live_inference"])
            allowance = (await client.get("/api/allowance")).json()
            self.assertTrue(allowance["enabled"])
            self.assertEqual(allowance["requests_used"], 0)
            self.assertNotIn(KEY, json.dumps(allowance))
            detached = await client.delete("/api/session/key", headers=self.auth)
            self.assertEqual(detached.status_code, 200, detached.text)
            self.assertFalse((await client.get("/api/config")).json()["session_key_attached"])
            self.assertEqual(
                (
                    await client.post(
                        "/api/session/key", headers=self.auth, json={"api_key": "short"}
                    )
                ).status_code,
                422,
            )

    async def test_key_attach_refused_when_hosted_route_is_not_configured(self):
        await self.enter()
        response = await self.client.post(
            "/api/session/key", headers=self.auth, json={"api_key": KEY}
        )
        self.assertEqual(response.status_code, 409)
        self.assertNotIn(KEY, response.text)

    async def test_session_close_revokes_cookie_and_blocks_further_use(self):
        response = await self.enter()
        self.assertIsNotNone(response.cookies.get("operator_session"))
        closed = await self.client.delete("/api/session", headers=self.auth)
        self.assertEqual(closed.status_code, 200, closed.text)
        self.assertFalse((await self.client.get("/api/config")).json()["authenticated"])
        self.assertEqual((await self.client.get("/api/tasks")).status_code, 401)

    async def test_cross_origin_and_unknown_host_are_refused_with_cookies(self):
        await self.enter()
        response = await self.client.post(
            "/api/tasks",
            headers={**self.auth, "Origin": "https://attacker.invalid"},
            json={"prompt": "x"},
        )
        self.assertEqual(response.status_code, 403)

    async def test_meta_actor_borrows_session_key_per_request_and_stops_after_revocation(self):
        seen = []

        class Actor:
            name = "fixture-actor"

            def __init__(self, store, key_source):
                self.store = store
                self.key_source = key_source
                self.fallback = ScriptedModel()
                self.last_receipt = None

            def next_operation(self, history, *, prompt):
                seen.append(self.key_source())
                return self.fallback.next_operation(history, prompt=prompt)

        def actor_factory(store, key_source):
            return Actor(store, key_source)

        app = create_hosted_app(self.scoped, self.sessions, grant(), actor_factory=actor_factory)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            await self.enter(client)
            await client.post("/api/session/key", headers=self.auth, json={"api_key": KEY})
            task = (await client.post("/api/tasks", headers=self.auth, json={"prompt": "t"})).json()
            path = "/api/tasks/" + task["id"]
            self.assertEqual(
                (await client.post(path + "/run", headers=self.auth, json={})).status_code, 200
            )
            self.assertTrue(seen)
            self.assertEqual(set(seen), {KEY})
            borrowed = len(seen)
            await client.delete("/api/session/key", headers=self.auth)
            second = (
                await client.post("/api/tasks", headers=self.auth, json={"prompt": "u"})
            ).json()
            response = await client.post(
                "/api/tasks/" + second["id"] + "/run", headers=self.auth, json={}
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(seen), borrowed)
            self.assertEqual(set(seen), {KEY})

    async def test_expired_session_is_rejected_without_recreating_state(self):
        now = 100
        sessions = HostedSessions(
            CODE, sessions=SessionKeys(lifetime=30, capacity=4, clock=lambda: now)
        )
        app = create_hosted_app(self.scoped, sessions)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            await self.enter(client, code=CODE)
            task = (await client.post("/api/tasks", headers=self.auth, json={"prompt": "t"})).json()
            now = 130
            self.assertEqual((await client.get("/api/tasks")).status_code, 401)
            self.assertFalse((await client.get("/api/config")).json()["authenticated"])
            await self.enter(client)
            self.assertEqual((await client.get("/api/tasks/" + task["id"])).status_code, 404)

    async def test_local_owner_app_still_requires_the_bearer_token(self):
        from remote_operator.web import create_app

        operator = Operator(self.root / "local", ROOT / "sample/issue_import.py", Validator())
        app = create_app(operator, "owner-" + "x" * 32, model_factory=ScriptedModel)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            self.assertEqual((await client.get("/api/config")).status_code, 401)
            self.assertEqual((await client.get("/api/tasks")).status_code, 401)
            response = await client.get(
                "/api/config", headers={"Authorization": "Bearer " + "owner-" + "x" * 32}
            )
            self.assertEqual(
                response.json(),
                {"mode": "scripted", "live_inference": False, "https_origin": None},
            )
            self.assertEqual(
                (await client.post("/api/session", json={"code": CODE})).status_code, 404
            )


if __name__ == "__main__":
    unittest.main()
