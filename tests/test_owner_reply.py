"""Owner replies route to the shared thread, never to demo tasks."""

import tempfile
import unittest
from pathlib import Path


class OwnerReplyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx

        from remote_operator.control import Operator, Validation
        from remote_operator.web import create_app

        class Validator:
            def validate(self, workspace, candidate_digest, *, cancelled=None):
                return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")

        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        seed = root / "seed.py"
        seed.write_text("# seed\n", encoding="utf-8")
        self.operator = Operator(root / "runtime", seed, Validator())
        self.token = "owner-test-credential-" + "x" * 32
        app = create_app(self.operator, self.token)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        )
        self.auth = {"Authorization": "Bearer " + self.token}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    async def test_reply_routes_to_shared_thread(self):
        first = await self.client.post(
            "/api/tasks", headers=self.auth, json={"prompt": "REPLY: hello there"}
        )
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertTrue(body["reply"])
        self.assertEqual(body["status"], "replied")
        thread_id = body["thread_id"]
        thread = await self.client.get(f"/api/supervisor/threads/{thread_id}", headers=self.auth)
        self.assertEqual(thread.status_code, 200)
        texts = [m["text"] for m in thread.json()["thread"]["history"]]
        self.assertIn("hello there", texts)
        stored = await self.client.get(f"/api/tasks/{thread_id}", headers=self.auth)
        self.assertEqual(stored.status_code, 409)
        listed = await self.client.get("/api/supervisor/threads", headers=self.auth)
        topics = {t["id"]: t["topic"] for t in listed.json()}
        self.assertEqual(topics[thread_id], "Owner replies")

    async def test_second_reply_appends_same_thread(self):
        first = await self.client.post(
            "/api/tasks", headers=self.auth, json={"prompt": "REPLY: one"}
        )
        second = await self.client.post(
            "/api/tasks", headers=self.auth, json={"prompt": "reply: two"}
        )
        self.assertEqual(first.json()["thread_id"], second.json()["thread_id"])
        thread = await self.client.get(
            f"/api/supervisor/threads/{first.json()['thread_id']}", headers=self.auth
        )
        history = thread.json()["thread"]["history"]
        user_texts = [m["text"] for m in history if m["role"] == "user"]
        self.assertEqual(user_texts, ["one", "two"])

    async def test_reply_like_without_colon_stays_a_task(self):
        created = await self.client.post(
            "/api/tasks", headers=self.auth, json={"prompt": "Reply to the review"}
        )
        self.assertEqual(created.status_code, 200)
        self.assertNotIn("reply", created.json())
        self.assertEqual(created.json()["status"], "queued")

    def test_submit_refuses_reply_prefix(self):
        with self.assertRaises(ValueError):
            self.operator.submit("REPLY: direct call")


if __name__ == "__main__":
    unittest.main()
