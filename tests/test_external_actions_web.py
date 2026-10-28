"""HTTP boundary for the neutral external pending-action contract."""

import hashlib
import tempfile
import unittest
from pathlib import Path


class ExternalActionWebTests(unittest.IsolatedAsyncioTestCase):
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

    def _digest(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    async def test_submit_review_approve_reject(self):
        response = await self.client.post(
            "/api/external-actions",
            headers=self.auth,
            json={
                "description": "Add greeting",
                "candidate_digest": self._digest("hello"),
                "base_digest": self._digest("base"),
                "evidence_digest": self._digest("evidence"),
                "expires_seconds": 60,
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "submitted")
        action_id = body["id"]
        nonce = body["nonce"]

        review = await self.client.get(f"/api/external-actions/{action_id}", headers=self.auth)
        self.assertEqual(review.status_code, 200)
        self.assertEqual(review.json()["description"], "Add greeting")

        list_response = await self.client.get("/api/external-actions", headers=self.auth)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.json()), 1)

        approve = await self.client.post(
            f"/api/external-actions/{action_id}/approve",
            headers=self.auth,
            json={"nonce": nonce},
        )
        self.assertEqual(approve.status_code, 200)
        self.assertEqual(approve.json()["status"], "approved")

        replay = await self.client.post(
            f"/api/external-actions/{action_id}/approve",
            headers=self.auth,
            json={"nonce": nonce},
        )
        self.assertEqual(replay.status_code, 409)

    async def test_reject_and_cancel(self):
        response = await self.client.post(
            "/api/external-actions",
            headers=self.auth,
            json={
                "description": "Change color",
                "candidate_digest": self._digest("blue"),
                "base_digest": self._digest("base"),
                "evidence_digest": self._digest("evidence"),
            },
        )
        action_id = response.json()["id"]

        reject = await self.client.post(
            f"/api/external-actions/{action_id}/reject", headers=self.auth
        )
        self.assertEqual(reject.status_code, 200)
        self.assertEqual(reject.json()["status"], "rejected")

        response2 = await self.client.post(
            "/api/external-actions",
            headers=self.auth,
            json={
                "description": "Change size",
                "candidate_digest": self._digest("big"),
                "base_digest": self._digest("base"),
                "evidence_digest": self._digest("evidence"),
            },
        )
        action_id2 = response2.json()["id"]
        cancel = await self.client.post(
            f"/api/external-actions/{action_id2}/cancel", headers=self.auth
        )
        self.assertEqual(cancel.status_code, 200)
        self.assertEqual(cancel.json()["status"], "cancelled")

    async def test_invalid_digest_and_extra_arguments(self):
        response = await self.client.post(
            "/api/external-actions",
            headers=self.auth,
            json={
                "description": "Bad digest",
                "candidate_digest": "short",
                "base_digest": self._digest("base"),
                "evidence_digest": self._digest("evidence"),
            },
        )
        self.assertEqual(response.status_code, 422)

        response2 = await self.client.post(
            "/api/external-actions",
            headers=self.auth,
            json={
                "description": "Extra field",
                "candidate_digest": self._digest("candidate"),
                "base_digest": self._digest("base"),
                "evidence_digest": self._digest("evidence"),
                "extra": "not-allowed",
            },
        )
        self.assertEqual(response2.status_code, 422)

    async def test_unauthorized(self):
        response = await self.client.post("/api/external-actions", json={"description": "x"})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
