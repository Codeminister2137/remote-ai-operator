"""Hosted multi-subject drain: execute=true queues and actually drains.

Hosted serve runs a sweeper over subject workspaces, so hosted approvals with
execute=true reach an action state instead of queueing forever. execute=false
stays available as planning-only, and execute=true without a drain stays 409
(the safety net pinned in tests/test_hosted_planning_only.py).
All model traffic is mocked; no inference, no network.
"""

import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import httpx

from remote_operator.control import Validation
from remote_operator.credentials import SessionKeys, SessionUnavailable
from remote_operator.hosted import HostedSessions
from remote_operator.meta import InferenceGrant
from remote_operator.scoped import ScopedOperators
from remote_operator.scripted import ScriptedModel
from remote_operator.supervisor import Supervisor
from remote_operator.supervisor_runner import (
    KEY_MISSING_NOTE,
    HostedDrainStatus,
    start_hosted_drain_worker,
    sweep_hosted_once,
)
from remote_operator.web import HOSTED_PLANNING_ONLY_MESSAGE, create_hosted_app

CODE = "demo-access-code-0001"
KEY = "synthetic-visitor-key-0001"
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


def make_actor_factory(borrowed, *, fail=None):
    """Live-route actor double: borrows the session key per request."""

    class FixtureActor:
        name = "fixture-live-actor"

        def __init__(self, store, key_source):
            self.store = store
            self.key_source = key_source
            self._scripted = ScriptedModel()
            self.last_receipt = None

        def next_operation(self, history, *, prompt):
            borrowed.append(self.key_source())
            if fail is not None:
                raise fail
            return self._scripted.next_operation(history, prompt=prompt)

    def factory(store, key_source):
        return FixtureActor(store, key_source)

    return factory


class HostedDrainHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.scoped = ScopedOperators(
            self.root / "visitors", ROOT / "sample/issue_import.py", Validator
        )
        self.sessions = HostedSessions(CODE)
        self.drain_status = HostedDrainStatus()
        self._clients = []
        self.auth = {"X-Operator-Request": "1"}

    async def asyncTearDown(self):
        for client in self._clients:
            await client.aclose()
        self.directory.cleanup()

    async def _serve(self, **kwargs):
        kwargs.setdefault("hosted_drain", self.drain_status)
        app = create_hosted_app(self.scoped, self.sessions, **kwargs)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        )
        self._clients.append(client)
        opened = await client.post("/api/session", json={"code": CODE})
        self.assertEqual(opened.status_code, 200, opened.text)
        return client

    async def _planned(self, client, topic="Ship it"):
        opened = await client.post(
            "/api/supervisor/threads", headers=self.auth, json={"topic": topic}
        )
        self.assertEqual(opened.status_code, 200, opened.text)
        thread_id = opened.json()["id"]
        message = await client.post(
            f"/api/supervisor/threads/{thread_id}/messages",
            headers=self.auth,
            json={"text": "It must handle retries"},
        )
        self.assertEqual(message.status_code, 200, message.text)
        plan = await client.post(
            f"/api/supervisor/threads/{thread_id}/plan", headers=self.auth
        )
        self.assertEqual(plan.status_code, 200, plan.text)
        return thread_id, plan.json()["nonce"]

    async def _runs(self, client):
        response = await client.get("/api/supervisor/runs", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        return {run["run_id"]: run for run in response.json()["runs"]}

    async def test_execute_true_queues_when_drain_enabled(self):
        client = await self._serve()
        thread_id, nonce = await self._planned(client)
        queued = await client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": nonce, "execute": True},
        )
        self.assertEqual(queued.status_code, 200, queued.text)
        self.assertEqual(queued.json()["status"], "queued")
        run_id = queued.json()["run_id"]
        self.assertTrue(run_id)
        runs = await self._runs(client)
        self.assertEqual(runs[run_id]["status"], "queued")

    async def test_execute_true_is_409_only_without_drain(self):
        client = await self._serve(hosted_drain=None)
        thread_id, nonce = await self._planned(client)
        refused = await client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": nonce, "execute": True},
        )
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["detail"], HOSTED_PLANNING_ONLY_MESSAGE)
        self.assertEqual(await self._runs(client), {})

    async def test_execute_false_stays_planning_only_with_drain(self):
        client = await self._serve()
        thread_id, nonce = await self._planned(client)
        approved = await client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": nonce, "execute": False},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertIsNone(approved.json()["run_id"])
        self.assertEqual(await self._runs(client), {})
        counts = sweep_hosted_once(self.scoped, self.sessions)
        self.assertEqual(counts["drained"], 0)

    async def test_sweep_drains_hosted_run_to_approval_then_completed(self):
        client = await self._serve()
        thread_id, nonce = await self._planned(client)
        queued = await client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": nonce, "execute": True},
        )
        run_id = queued.json()["run_id"]
        counts = sweep_hosted_once(self.scoped, self.sessions)
        self.assertEqual(counts, {"swept": 1, "drained": 1, "skipped": 0, "busy": 0,
                                  "key_missing": 0})
        runs = await self._runs(client)
        self.assertEqual(runs[run_id]["status"], "awaiting_approval")
        detail = (
            await client.get(f"/api/supervisor/runs/{run_id}", headers=self.auth)
        ).json()
        task_id = detail["task_id"]
        review = await client.get(f"/api/tasks/{task_id}", headers=self.auth)
        task_nonce = review.json()["task"]["approval"]["nonce"]
        approved = await client.post(
            f"/api/tasks/{task_id}/approve", headers=self.auth, json={"nonce": task_nonce}
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        sweep_hosted_once(self.scoped, self.sessions)
        runs = await self._runs(client)
        self.assertEqual(runs[run_id]["status"], "completed")

    async def test_sweep_is_fair_across_subjects(self):
        first = await self._serve()
        second = await self._serve()
        for client, topic in ((first, "Alpha"), (second, "Beta")):
            for index in range(2):
                thread_id, nonce = await self._planned(client, f"{topic} {index}")
                queued = await client.post(
                    f"/api/supervisor/threads/{thread_id}/approve",
                    headers=self.auth,
                    json={"nonce": nonce, "execute": True},
                )
                self.assertEqual(queued.status_code, 200, queued.text)
        sweep_hosted_once(self.scoped, self.sessions, max_jobs_per_subject=1)
        for client in (first, second):
            statuses = sorted(run["status"] for run in (await self._runs(client)).values())
            self.assertEqual(statuses, ["awaiting_approval", "queued"])
        sweep_hosted_once(self.scoped, self.sessions)
        for client in (first, second):
            statuses = {run["status"] for run in (await self._runs(client)).values()}
            self.assertEqual(statuses, {"awaiting_approval"})

    async def test_busy_subject_is_skipped_without_stalling_others(self):
        first = await self._serve()
        second = await self._serve()
        for client in (first, second):
            thread_id, nonce = await self._planned(client)
            await client.post(
                f"/api/supervisor/threads/{thread_id}/approve",
                headers=self.auth,
                json={"nonce": nonce, "execute": True},
            )
        subject = self.sessions.subject(first.cookies.get("operator_session"))
        held = self.scoped.for_subject(subject)
        self.assertTrue(held.lock.acquire(blocking=False))
        try:
            counts = sweep_hosted_once(self.scoped, self.sessions)
        finally:
            held.lock.release()
        self.assertEqual(counts["busy"], 1)
        first_runs = await self._runs(first)
        self.assertEqual(
            {run["status"] for run in first_runs.values()}, {"queued"}
        )
        second_runs = await self._runs(second)
        self.assertEqual(
            {run["status"] for run in second_runs.values()}, {"awaiting_approval"}
        )
        sweep_hosted_once(self.scoped, self.sessions)
        first_runs = await self._runs(first)
        self.assertEqual(
            {run["status"] for run in first_runs.values()}, {"awaiting_approval"}
        )

    async def test_subjects_cannot_read_each_others_runs(self):
        first = await self._serve()
        second = await self._serve()
        thread_id, nonce = await self._planned(first)
        queued = await first.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": nonce, "execute": True},
        )
        run_id = queued.json()["run_id"]
        self.assertEqual(await self._runs(second), {})
        missing = await second.get(f"/api/supervisor/runs/{run_id}", headers=self.auth)
        self.assertEqual(missing.status_code, 404)
        sweep_hosted_once(self.scoped, self.sessions)
        self.assertEqual(await self._runs(second), {})
        missing = await second.get(f"/api/supervisor/runs/{run_id}", headers=self.auth)
        self.assertEqual(missing.status_code, 404)

    async def test_subjects_cannot_read_each_others_tasks(self):
        first = await self._serve()
        second = await self._serve()
        created = await first.post(
            "/api/tasks", headers=self.auth, json={"prompt": "first subject work"}
        )
        self.assertEqual(created.status_code, 200, created.text)
        task_id = created.json()["id"]
        missing = await second.get(f"/api/tasks/{task_id}", headers=self.auth)
        self.assertEqual(missing.status_code, 404, missing.text)
        listed = await second.get("/api/tasks", headers=self.auth)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertNotIn(task_id, [task["id"] for task in listed.json()])
        sweep_hosted_once(self.scoped, self.sessions)
        missing = await second.get(f"/api/tasks/{task_id}", headers=self.auth)
        self.assertEqual(missing.status_code, 404, missing.text)

    async def test_keyless_live_job_parks_and_resumes_on_reattach(self):
        borrowed = []
        factory = make_actor_factory(borrowed)
        live_grant = grant()
        client = await self._serve(grant=live_grant, actor_factory=factory)
        attached = await client.post(
            "/api/session/key", headers=self.auth, json={"api_key": KEY}
        )
        self.assertEqual(attached.status_code, 200, attached.text)
        thread_id, nonce = await self._planned(client)
        detached = await client.delete("/api/session/key", headers=self.auth)
        self.assertEqual(detached.status_code, 200, detached.text)
        queued = await client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": nonce, "execute": True},
        )
        run_id = queued.json()["run_id"]
        counts = sweep_hosted_once(
            self.scoped, self.sessions, grant=live_grant, actor_factory=factory
        )
        self.assertEqual(counts["key_missing"], 1)
        detail = (
            await client.get(f"/api/supervisor/runs/{run_id}", headers=self.auth)
        ).json()
        self.assertEqual(detail["status"], "waiting_on_key")
        self.assertEqual(detail["note"], KEY_MISSING_NOTE)
        self.assertNotIn("task_id", detail)
        reattached = await client.post(
            "/api/session/key", headers=self.auth, json={"api_key": KEY}
        )
        self.assertEqual(reattached.status_code, 200, reattached.text)
        runs = await self._runs(client)
        self.assertEqual(runs[run_id]["status"], "queued")
        sweep_hosted_once(
            self.scoped, self.sessions, grant=live_grant, actor_factory=factory
        )
        runs = await self._runs(client)
        self.assertEqual(runs[run_id]["status"], "awaiting_approval")
        self.assertTrue(borrowed)
        self.assertEqual(set(borrowed), {KEY})

    async def test_restart_recovered_workspace_still_drains_scripted(self):
        client = await self._serve()
        thread_id, nonce = await self._planned(client)
        queued = await client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": nonce, "execute": True},
        )
        run_id = queued.json()["run_id"]
        # Fresh process state: empty subject registry, fresh session store.
        restarted = ScopedOperators(
            self.root / "visitors", ROOT / "sample/issue_import.py", Validator
        )
        directories = [
            path for path in (self.root / "visitors").iterdir() if path.is_dir()
        ]
        self.assertEqual(len(directories), 1)
        self.assertIsNone(restarted.subject_for_directory(directories[0]))
        sweep_hosted_once(restarted, HostedSessions(CODE))
        runs = await self._runs(client)
        self.assertEqual(runs[run_id]["status"], "awaiting_approval")

    async def test_stray_files_and_dirs_do_not_stall_the_sweep(self):
        client = await self._serve()
        thread_id, nonce = await self._planned(client)
        queued = await client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": nonce, "execute": True},
        )
        run_id = queued.json()["run_id"]
        (self.root / "visitors" / "junk.txt").write_text("sidecar", encoding="utf-8")
        (self.root / "visitors" / "not-a-workspace").mkdir()
        counts = sweep_hosted_once(self.scoped, self.sessions)
        self.assertEqual(counts["drained"], 1)
        self.assertEqual(counts["skipped"], 1)
        runs = await self._runs(client)
        self.assertEqual(runs[run_id]["status"], "awaiting_approval")

    async def test_worker_thread_drains_hosted_run(self):
        client = await self._serve()
        thread_id, nonce = await self._planned(client)
        queued = await client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": nonce, "execute": True},
        )
        run_id = queued.json()["run_id"]
        stop = threading.Event()
        worker = start_hosted_drain_worker(
            self.scoped, self.sessions, interval_seconds=0.05, stop_event=stop
        )
        try:
            self.assertTrue(worker.daemon)
            deadline = time.monotonic() + 10.0
            seen = "queued"
            while time.monotonic() < deadline:
                seen = (await self._runs(client))[run_id]["status"]
                if seen == "awaiting_approval":
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(seen, "awaiting_approval")
            self.assertTrue(worker.is_alive())
        finally:
            stop.set()
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())


class HostedRevocationTests(unittest.TestCase):
    """A key lost mid-drain parks the job; re-attach resumes without replay."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.scoped = ScopedOperators(
            self.root / "visitors", ROOT / "sample/issue_import.py", Validator
        )
        self.sessions = HostedSessions(CODE)
        self.live_grant = grant()
        token = self.sessions.open(CODE)
        self.subject = self.sessions.subject(token)
        self.sessions.attach_key(token, KEY)
        self.operator = self.scoped.for_subject(self.subject)
        supervisor = Supervisor(self.operator)
        thread = supervisor.open_thread("Ship it")
        supervisor.post_message(ScriptedModel(), thread["id"], "Handle retries")
        plan = supervisor.write_plan(thread["id"])
        self.run_id = supervisor.approve_plan(thread["id"], plan["nonce"])["run_id"]

    def tearDown(self):
        self.directory.cleanup()

    def _job(self):
        path = self.operator.root / "supervisor_runs" / f"{self.run_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_revoked_key_parks_then_resumes(self):
        revoked = make_actor_factory([], fail=SessionUnavailable("revoked mid-drain"))
        counts = sweep_hosted_once(
            self.scoped, self.sessions, grant=self.live_grant, actor_factory=revoked
        )
        self.assertEqual(counts["key_missing"], 1)
        job = self._job()
        self.assertEqual(job["status"], "waiting_on_key")
        self.assertEqual(job["note"], KEY_MISSING_NOTE)
        task = self.operator.store.get(job["task_id"])
        self.assertEqual(task["status"], "paused")
        self.assertEqual(task["history"], [])
        borrowed = []
        working = make_actor_factory(borrowed)
        sweep_hosted_once(
            self.scoped, self.sessions, grant=self.live_grant, actor_factory=working
        )
        job = self._job()
        self.assertEqual(job["status"], "awaiting_approval")
        resumed = self.operator.store.get(job["task_id"])
        self.assertEqual(resumed["status"], "awaiting_approval")
        self.assertEqual(
            [entry["name"] for entry in resumed["history"]],
            ["read_file", "write_file", "validate"],
        )
        self.assertEqual(set(borrowed), {KEY})


class SubjectKeySourceTests(unittest.TestCase):
    def test_borrow_returns_key_and_honors_revocation(self):
        store = SessionKeys(lifetime=30, capacity=4, clock=time.monotonic)
        token = store.open("visitor-test-0001", KEY)
        source = store.key_source_for_subject("visitor-test-0001")
        self.assertIsNotNone(source)
        self.assertEqual(source(), KEY)
        self.assertEqual(store.subjects_with_keys(), ["visitor-test-0001"])
        store.revoke(token)
        self.assertIsNone(store.key_source_for_subject("visitor-test-0001"))
        with self.assertRaises(SessionUnavailable):
            source()

    def test_unknown_subject_has_no_source(self):
        store = SessionKeys(lifetime=30, capacity=4, clock=time.monotonic)
        self.assertIsNone(store.key_source_for_subject("visitor-missing-1"))
        self.assertEqual(store.subjects_with_keys(), [])

    def test_hosted_sessions_borrow_per_call(self):
        sessions = HostedSessions(CODE)
        token = sessions.open(CODE)
        subject = sessions.subject(token)
        self.assertIsNone(sessions.key_source_for_subject(subject))
        sessions.attach_key(token, KEY)
        source = sessions.key_source_for_subject(subject)
        self.assertEqual(source(), KEY)
        self.assertEqual(sessions.subjects_with_keys(), [subject])
        sessions.close(token)
        with self.assertRaises(SessionUnavailable):
            source()


class ScopedRegistryTests(unittest.TestCase):
    def test_subject_roundtrip_and_directory_operators(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            scoped = ScopedOperators(
                root / "visitors", ROOT / "sample/issue_import.py", Validator
            )
            operator = scoped.for_subject("visitor-test-0001")
            task = operator.submit("isolated work")
            self.assertEqual(task["status"], "queued")
            directory = operator.root
            self.assertEqual(
                scoped.subject_for_directory(directory), "visitor-test-0001"
            )
            twin = scoped.operator_for_directory(directory)
            self.assertEqual(twin.store.get(task["id"])["prompt"], "isolated work")
            with self.assertRaises(ValueError):
                scoped.operator_for_directory(root / "visitors" / "not-a-workspace")
            with self.assertRaises(ValueError):
                scoped.operator_for_directory(root / "elsewhere" / ("a" * 64))

    def test_unknown_directory_has_no_subject(self):
        with tempfile.TemporaryDirectory() as name:
            scoped = ScopedOperators(
                Path(name) / "visitors", ROOT / "sample/issue_import.py", Validator
            )
            other = ScopedOperators(
                Path(name) / "visitors", ROOT / "sample/issue_import.py", Validator
            )
            operator = scoped.for_subject("visitor-test-0002")
            self.assertIsNone(other.subject_for_directory(operator.root))


class HostedSweepBudgetTests(unittest.TestCase):
    def test_invalid_budgets_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            scoped = ScopedOperators(
                Path(name) / "visitors", ROOT / "sample/issue_import.py", Validator
            )
            sessions = HostedSessions(CODE)
            for bad_subjects in (0, 257, True, "32"):
                with self.assertRaises(ValueError):
                    sweep_hosted_once(scoped, sessions, max_subjects=bad_subjects)
            for bad_jobs in (0, 101, False):
                with self.assertRaises(ValueError):
                    sweep_hosted_once(
                        scoped, sessions, max_jobs_per_subject=bad_jobs
                    )
            with self.assertRaises(ValueError):
                start_hosted_drain_worker(scoped, sessions, interval_seconds=0)

    def test_status_rotation_and_snapshot(self):
        status = HostedDrainStatus()
        first = status.claim_slice(3, 2)
        second = status.claim_slice(3, 2)
        self.assertEqual(first, [0, 1])
        self.assertEqual(second, [2, 0])
        self.assertEqual(status.claim_slice(0, 2), [])
        status.record_hosted_pass(
            {"swept": 2, "drained": 1, "skipped": 0, "busy": 1, "key_missing": 1}
        )
        snap = status.snapshot(True)
        self.assertTrue(snap["alive"])
        self.assertEqual(snap["subjects_swept"], 2)
        self.assertEqual(snap["subjects_drained"], 1)
        self.assertEqual(snap["subjects_busy"], 1)
        self.assertEqual(snap["subjects_key_missing"], 1)


if __name__ == "__main__":
    unittest.main()
