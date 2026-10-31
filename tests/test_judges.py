"""Judge BYOK flow: signup, login, self-grant, key attach, isolation."""

import tempfile
import time
import unittest
from pathlib import Path

import httpx

from remote_operator import judge_accounts
from remote_operator.hosted import HostedSessions
from remote_operator.judge_accounts import JudgeAccounts
from remote_operator.meta import InferenceGrant
from remote_operator.scoped import ScopedOperators
from remote_operator.scripted import ScriptedModel
from remote_operator.web import create_hosted_app

CODE = "demo-access-code-0001"
KEY = "synthetic-judge-key-0002"
JUDGE_MODEL = "muse-spark-fixture"


class Validator:
    def validate(self, workspace, candidate_digest, *, cancelled=None):
        from remote_operator.control import Validation

        return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")


def owner_grant():
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


class JudgeFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._real_iterations = judge_accounts.Pbkdf2_ITERATIONS
        judge_accounts.Pbkdf2_ITERATIONS = 1000
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        seed = root / "seed.py"
        seed.write_text("# seed\n", encoding="utf-8")
        self.scoped = ScopedOperators(root / "visitors", seed, Validator)
        self.sessions = HostedSessions(CODE)
        self.accounts = JudgeAccounts(root / "judges.sqlite3")
        self.app = create_hosted_app(
            self.scoped,
            self.sessions,
            None,
            actor_factory=lambda store, key: ScriptedModel(),
            judge_accounts=self.accounts,
            judge_model=JUDGE_MODEL,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://localhost"
        )
        self.auth = {"X-Operator-Request": "1"}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()
        judge_accounts.Pbkdf2_ITERATIONS = self._real_iterations

    async def _judge(self, username="ada", password="correct-horse-99"):
        response = await self.client.post(
            "/api/judges/signup",
            headers=self.auth,
            json={"code": CODE, "username": username, "password": password},
        )
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.post(
            "/api/judges/login",
            headers=self.auth,
            json={"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response

    async def test_signup_requires_access_code_and_unique_name(self):
        response = await self.client.post(
            "/api/judges/signup",
            headers=self.auth,
            json={"code": "wrong", "username": "ada", "password": "correct-horse-99"},
        )
        self.assertEqual(response.status_code, 403)
        await self._judge()
        response = await self.client.post(
            "/api/judges/signup",
            headers=self.auth,
            json={"code": CODE, "username": "ada", "password": "another-pass-99"},
        )
        self.assertEqual(response.status_code, 409)
        response = await self.client.post(
            "/api/judges/signup",
            headers=self.auth,
            json={"code": CODE, "username": "no spaces", "password": "correct-horse-99"},
        )
        self.assertEqual(response.status_code, 409)

    async def test_login_is_stable_and_rejects_wrong_password(self):
        await self._judge()
        first = await self.client.post(
            "/api/tasks", headers=self.auth, json={"prompt": "judge task"}
        )
        self.assertEqual(first.status_code, 200)
        second = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://localhost"
        )
        try:
            response = await second.post(
                "/api/judges/login",
                headers=self.auth,
                json={"username": "ada", "password": "correct-horse-99"},
            )
            self.assertEqual(response.status_code, 200)
            listed = await second.get("/api/tasks?limit=20")
            self.assertEqual([item["id"] for item in listed.json()], [first.json()["id"]])
            response = await second.post(
                "/api/judges/login",
                headers=self.auth,
                json={"username": "ada", "password": "wrong-password-99"},
            )
            self.assertEqual(response.status_code, 403)
        finally:
            await second.aclose()

    async def test_login_throttles_after_ten_failures(self):
        await self._judge(username="grace")
        for _ in range(10):
            response = await self.client.post(
                "/api/judges/login",
                headers=self.auth,
                json={"username": "grace", "password": "wrong-password-99"},
            )
            self.assertEqual(response.status_code, 403)
        response = await self.client.post(
            "/api/judges/login",
            headers=self.auth,
            json={"username": "grace", "password": "wrong-password-99"},
        )
        self.assertEqual(response.status_code, 429)

    async def test_grant_is_judge_only_and_validated(self):
        response = await self.client.post("/api/session", headers=self.auth, json={"code": CODE})
        self.assertEqual(response.status_code, 200)
        response = await self.client.get("/api/judges/grant")
        self.assertEqual(response.status_code, 403)
        await self._judge()
        response = await self.client.get("/api/judges/grant")
        self.assertEqual(response.json(), {"grant": None})
        response = await self.client.put(
            "/api/judges/grant", headers=self.auth, json={"max_requests": 0, "approved_usd": "2"}
        )
        self.assertEqual(response.status_code, 422)
        response = await self.client.put(
            "/api/judges/grant",
            headers=self.auth,
            json={"max_requests": 10, "approved_usd": "not-money"},
        )
        self.assertEqual(response.status_code, 409)
        response = await self.client.put(
            "/api/judges/grant",
            headers=self.auth,
            json={"max_requests": 10, "approved_usd": "2.50"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["grant"]["max_requests"], 10)

    async def test_key_needs_limits_then_routes_meta_on_judge_budget(self):
        await self._judge()
        response = await self.client.post(
            "/api/session/key", headers=self.auth, json={"api_key": KEY}
        )
        self.assertEqual(response.status_code, 409)
        await self.client.put(
            "/api/judges/grant",
            headers=self.auth,
            json={"max_requests": 10, "approved_usd": "2.50"},
        )
        response = await self.client.post(
            "/api/session/key", headers=self.auth, json={"api_key": KEY}
        )
        self.assertEqual(response.status_code, 200)
        config = await self.client.get("/api/config")
        self.assertEqual(config.json()["mode"], "meta")
        self.assertTrue(config.json()["judge"])
        allowance = await self.client.get("/api/allowance")
        self.assertTrue(allowance.json()["enabled"])
        self.assertEqual(allowance.json()["requests_limit"], 10)
        self.assertIn("2.5", allowance.json()["limit_usd"])

    async def test_judges_are_isolated_from_each_other(self):
        await self._judge(username="ada")
        await self.client.put(
            "/api/judges/grant",
            headers=self.auth,
            json={"max_requests": 7, "approved_usd": "1.00"},
        )
        await self.client.post("/api/tasks", headers=self.auth, json={"prompt": "ada task"})
        other = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://localhost"
        )
        try:
            response = await other.post(
                "/api/judges/signup",
                headers=self.auth,
                json={"code": CODE, "username": "bob", "password": "correct-horse-99"},
            )
            self.assertEqual(response.status_code, 200)
            await other.post(
                "/api/judges/login",
                headers=self.auth,
                json={"username": "bob", "password": "correct-horse-99"},
            )
            grant = await other.get("/api/judges/grant")
            self.assertEqual(grant.json(), {"grant": None})
            listed = await other.get("/api/tasks?limit=20")
            self.assertEqual(listed.json(), [])
        finally:
            await other.aclose()

    async def test_owner_grant_takes_precedence_over_self_grant(self):
        app = create_hosted_app(
            self.scoped,
            self.sessions,
            owner_grant(),
            actor_factory=lambda store, key: ScriptedModel(),
            judge_accounts=self.accounts,
            judge_model=JUDGE_MODEL,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        )
        try:
            await client.post(
                "/api/judges/signup",
                headers=self.auth,
                json={"code": CODE, "username": "ada", "password": "correct-horse-99"},
            )
            await client.post(
                "/api/judges/login",
                headers=self.auth,
                json={"username": "ada", "password": "correct-horse-99"},
            )
            await client.put(
                "/api/judges/grant",
                headers=self.auth,
                json={"max_requests": 10, "approved_usd": "2.50"},
            )
            await client.post("/api/session/key", headers=self.auth, json={"api_key": KEY})
            allowance = await client.get("/api/allowance")
            self.assertEqual(allowance.json()["requests_limit"], 5)
        finally:
            await client.aclose()

    async def test_expired_self_grant_reads_as_absent(self):
        from remote_operator.judge_grants import JudgeGrantBridge

        operator = self.scoped.for_subject("judge-ada")
        bridge = JudgeGrantBridge(operator.store)
        start = time.time()
        bridge.set({"max_requests": 3, "approved_usd": "1.00"}, model=JUDGE_MODEL, now=start)
        fresh = bridge.effective("judge-ada", JUDGE_MODEL)
        self.assertIsNotNone(fresh)
        bridge.set(
            {"max_requests": 3, "approved_usd": "1.00"},
            model=JUDGE_MODEL,
            now=start - 31 * 86400,
        )
        self.assertIsNone(bridge.effective("judge-ada", JUDGE_MODEL))
        self.assertIsNone(bridge.effective("visitor-x", JUDGE_MODEL))


if __name__ == "__main__":
    unittest.main()
