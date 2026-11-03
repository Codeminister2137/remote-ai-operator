"""Hosted planning-only enforcement: approve(execute=true) can never queue a run.

The hosted serve branch starts no drain worker, so a queued hosted job would sit
in queued forever with no error. The hosted approve path therefore refuses
execute=true with 409 and only records planning-only approvals (execute=false).
All model traffic is mocked; no inference, no network.
"""

import tempfile
import unittest
from pathlib import Path

import httpx

from remote_operator.control import Operator, Validation
from remote_operator.hosted import HostedSessions
from remote_operator.scoped import ScopedOperators
from remote_operator.scripted import ScriptedModel
from remote_operator.web import (
    HOSTED_PLANNING_ONLY_MESSAGE,
    create_app,
    create_hosted_app,
)

CODE = "demo-access-code-0001"
ROOT = Path(__file__).resolve().parents[1]


class Validator:
    def validate(self, workspace, candidate_digest, *, cancelled=None):
        return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")


def queued_job_files(root):
    """Job files the drain worker would consume, anywhere under root."""
    return [p for p in root.rglob("*.json") if p.parent.name == "supervisor_runs"]


class HostedPlanningOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        scoped = ScopedOperators(
            self.root / "visitors", ROOT / "sample/issue_import.py", Validator
        )
        self.app = create_hosted_app(scoped, HostedSessions(CODE))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://localhost"
        )
        await self.client.post("/api/session", json={"code": CODE})
        self.auth = {"X-Operator-Request": "1"}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    async def _planned(self):
        opened = await self.client.post(
            "/api/supervisor/threads", headers=self.auth, json={"topic": "Ship it"}
        )
        self.assertEqual(opened.status_code, 200, opened.text)
        thread_id = opened.json()["id"]
        message = await self.client.post(
            f"/api/supervisor/threads/{thread_id}/messages",
            headers=self.auth,
            json={"text": "It must handle retries"},
        )
        self.assertEqual(message.status_code, 200, message.text)
        plan = await self.client.post(
            f"/api/supervisor/threads/{thread_id}/plan", headers=self.auth
        )
        self.assertEqual(plan.status_code, 200, plan.text)
        return thread_id, plan.json()["nonce"]

    async def _runs(self):
        response = await self.client.get("/api/supervisor/runs", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["runs"]

    async def test_hosted_approve_execute_true_is_409_and_queues_nothing(self):
        thread_id, nonce = await self._planned()
        refused = await self.client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": nonce, "execute": True},
        )
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["detail"], HOSTED_PLANNING_ONLY_MESSAGE)
        self.assertEqual(await self._runs(), [])
        self.assertEqual(queued_job_files(self.root), [])

    async def test_hosted_approve_without_execute_field_is_rejected(self):
        # PlanApproval defaults execute to true, so a bare nonce must fail loud too.
        thread_id, nonce = await self._planned()
        refused = await self.client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": nonce},
        )
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertIn("planning-only", refused.json()["detail"])
        self.assertEqual(await self._runs(), [])
        self.assertEqual(queued_job_files(self.root), [])

    async def test_hosted_409_leaves_approval_pending_for_planning_retry(self):
        thread_id, nonce = await self._planned()
        refused = await self.client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": nonce, "execute": True},
        )
        self.assertEqual(refused.status_code, 409, refused.text)
        thread = await self.client.get(
            f"/api/supervisor/threads/{thread_id}", headers=self.auth
        )
        self.assertEqual(thread.json()["thread"]["status"], "planned")
        approved = await self.client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": nonce, "execute": False},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertIsNone(approved.json()["run_id"])
        self.assertEqual(approved.json()["status"], "approved")
        self.assertEqual(await self._runs(), [])
        self.assertEqual(queued_job_files(self.root), [])

    async def test_hosted_approve_execute_false_records_plan_without_queueing(self):
        thread_id, nonce = await self._planned()
        approved = await self.client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": nonce, "execute": False},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertIsNone(approved.json()["run_id"])
        thread = await self.client.get(
            f"/api/supervisor/threads/{thread_id}", headers=self.auth
        )
        self.assertEqual(thread.json()["thread"]["status"], "approved")
        self.assertEqual(await self._runs(), [])
        self.assertEqual(queued_job_files(self.root), [])

    async def test_hosted_planning_only_message_is_ui_ready(self):
        # The 409 detail is shown verbatim by the UI (api()/guarded() in app.js),
        # so it must name the mode and the exact recovery action.
        self.assertIn("planning-only", HOSTED_PLANNING_ONLY_MESSAGE)
        self.assertIn("execute=false", HOSTED_PLANNING_ONLY_MESSAGE)
        self.assertIn("not recorded", HOSTED_PLANNING_ONLY_MESSAGE)


class OwnerApproveUnchangedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        seed = ROOT / "sample/issue_import.py"
        self.operator = Operator(root / "runtime", seed, Validator())
        self.token = "owner-test-credential-" + "x" * 32
        app = create_app(self.operator, self.token, model_factory=ScriptedModel)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        )
        self.auth = {"Authorization": "Bearer " + self.token}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    async def test_owner_approve_execute_true_still_queues(self):
        opened = await self.client.post(
            "/api/supervisor/threads", headers=self.auth, json={"topic": "Ship it"}
        )
        thread_id = opened.json()["id"]
        await self.client.post(
            f"/api/supervisor/threads/{thread_id}/messages",
            headers=self.auth,
            json={"text": "req"},
        )
        plan = await self.client.post(
            f"/api/supervisor/threads/{thread_id}/plan", headers=self.auth
        )
        queued = await self.client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": plan.json()["nonce"], "execute": True},
        )
        self.assertEqual(queued.status_code, 200, queued.text)
        self.assertEqual(queued.json()["status"], "queued")
        self.assertTrue(queued.json()["run_id"])
        runs = await self.client.get("/api/supervisor/runs", headers=self.auth)
        self.assertEqual(
            [run["run_id"] for run in runs.json()["runs"]], [queued.json()["run_id"]]
        )


if __name__ == "__main__":
    unittest.main()
