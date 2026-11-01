"""Owner run settings, planning-only approval, power control, worker notify."""

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx

from remote_operator import power as power_control
from remote_operator import settings as run_settings
from remote_operator.control import Operator, Validation
from remote_operator.hosted import HostedSessions
from remote_operator.scoped import ScopedOperators
from remote_operator.scripted import ScriptedModel
from remote_operator.supervisor import Supervisor
from remote_operator.supervisor_runner import start_drain_worker
from remote_operator.web import create_app, create_hosted_app

ROOT = Path(__file__).resolve().parents[1]
CODE = "demo-access-code-0002"


class Validator:
    def validate(self, workspace, candidate_digest, *, cancelled=None):
        return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")


def _operator(root):
    seed = root / "seed.py"
    seed.write_text("# seed\n", encoding="utf-8")
    return Operator(root / "runtime", seed, Validator())


class SettingsUnitTests(unittest.TestCase):
    def test_roundtrip_and_corrupt_reset(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.assertEqual(run_settings.load_settings(root), {"supervision": "attended"})
            run_settings.save_settings(root, "unattended")
            self.assertEqual(run_settings.load_settings(root), {"supervision": "unattended"})
            (root / "settings.json").write_text("nope{", encoding="utf-8")
            self.assertEqual(run_settings.load_settings(root), {"supervision": "attended"})
            with self.assertRaises(ValueError):
                run_settings.save_settings(root, "yolo")


class PlanningOnlyTests(unittest.TestCase):
    def test_thread_flag_in_task_summaries(self):
        with tempfile.TemporaryDirectory() as name:
            operator = _operator(Path(name))
            thread = Supervisor(operator).open_thread("topic")
            task = operator.submit("plain demo task")
            by_id = {s["id"]: s for s in operator.store.recent_tasks()}
            self.assertTrue(by_id[thread["id"]]["thread"])
            self.assertFalse(by_id[task["id"]]["thread"])

    def test_approve_without_execute_queues_no_run(self):
        with tempfile.TemporaryDirectory() as name:
            operator = _operator(Path(name))
            supervisor = Supervisor(operator)
            thread = supervisor.open_thread("topic")
            supervisor.post_message(ScriptedModel(), thread["id"], "req")
            plan = supervisor.write_plan(thread["id"])
            out = supervisor.approve_plan(thread["id"], plan["nonce"], execute=False)
            self.assertIsNone(out["run_id"])
            self.assertEqual(out["status"], "approved")
            runs = list((operator.root / "supervisor_runs").glob("*.json"))
            self.assertEqual(runs, [])
            stored = operator.store.get(thread["id"])
            self.assertEqual(stored["status"], "approved")


class OwnerRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.operator = _operator(Path(self.directory.name))
        self.token = "owner-test-credential-" + "x" * 32
        app = create_app(self.operator, self.token)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        )
        self.auth = {"Authorization": "Bearer " + self.token}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    async def test_settings_get_put_roundtrip(self):
        get = await self.client.get("/api/settings", headers=self.auth)
        self.assertEqual(get.status_code, 200)
        self.assertEqual(get.json()["supervision"], "attended")
        self.assertIn("mail_configured", get.json())
        put = await self.client.put(
            "/api/settings", headers=self.auth, json={"supervision": "unattended"}
        )
        self.assertEqual(put.status_code, 200, put.text)
        self.assertEqual(put.json()["supervision"], "unattended")
        bad = await self.client.put(
            "/api/settings", headers=self.auth, json={"supervision": "yolo"}
        )
        self.assertEqual(bad.status_code, 409)
        denied = await self.client.get("/api/settings")
        self.assertEqual(denied.status_code, 401)

    async def test_power_schedule_status_cancel(self):
        idle = await self.client.get("/api/power/sleep", headers=self.auth)
        self.assertEqual(idle.json()["status"], "idle")
        with mock.patch.object(
            power_control.subprocess, "Popen", return_value=mock.Mock(pid=4242)
        ) as popen:
            scheduled = await self.client.post(
                "/api/power/sleep", headers=self.auth, json={"delay_seconds": 120}
            )
        self.assertEqual(scheduled.status_code, 200, scheduled.text)
        self.assertEqual(scheduled.json()["status"], "sleep_scheduled")
        self.assertEqual(scheduled.json()["process_id"], 4242)
        popen.assert_called_once()
        status = await self.client.get("/api/power/sleep", headers=self.auth)
        self.assertEqual(status.json()["status"], "sleep_scheduled")
        cancelled = await self.client.post("/api/power/sleep/cancel", headers=self.auth)
        self.assertTrue(cancelled.json()["cancelled"])
        status = await self.client.get("/api/power/sleep", headers=self.auth)
        self.assertEqual(status.json()["status"], "sleep_cancelled")

    async def test_power_rejects_bad_delay(self):
        response = await self.client.post(
            "/api/power/sleep", headers=self.auth, json={"delay_seconds": 5}
        )
        self.assertEqual(response.status_code, 422)

    async def test_settings_reports_approval_window(self):
        response = await self.client.get("/api/settings", headers=self.auth)
        body = response.json()
        self.assertEqual(body["approval_window_minutes"], 10)
        self.assertEqual(body["task_approval_minutes"], 60)

    async def test_plan_approve_accepts_execute_flag(self):
        opened = await self.client.post(
            "/api/supervisor/threads", headers=self.auth, json={"topic": "t"}
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
        approved = await self.client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": plan.json()["nonce"], "execute": False},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertIsNone(approved.json()["run_id"])
        runs = await self.client.get("/api/supervisor/runs", headers=self.auth)
        self.assertEqual(runs.json()["runs"], [])


class HostedRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        scoped = ScopedOperators(root / "visitors", ROOT / "sample/issue_import.py", Validator)
        sessions = HostedSessions(CODE)
        app = create_hosted_app(scoped, sessions)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        )
        await self.client.post("/api/session", json={"code": CODE})
        self.auth = {"X-Operator-Request": "1"}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    async def test_visitors_cannot_touch_settings_or_power(self):
        self.assertEqual(
            (await self.client.get("/api/settings", headers=self.auth)).status_code, 403
        )
        self.assertEqual(
            (
                await self.client.post(
                    "/api/power/sleep", headers=self.auth, json={"delay_seconds": 60}
                )
            ).status_code,
            403,
        )
        self.assertEqual(
            (await self.client.post("/api/power/sleep/cancel", headers=self.auth)).status_code,
            403,
        )


class PowerHelperTests(unittest.TestCase):
    def test_helper_aborts_when_cancel_file_exists(self):
        with tempfile.TemporaryDirectory() as name:
            cancel = Path(name) / "power-sleep.cancel"
            cancel.write_text("cancelled", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-c", power_control._HELPER, str(cancel), repr(time.time() + 30)],
                check=False,
                capture_output=True,
                timeout=15,
            )
            self.assertEqual(proc.returncode, 3)


class WorkerNotifyTests(unittest.TestCase):
    def _drain_one(self, supervision, env, calls):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            operator = _operator(root)
            run_settings.save_settings(operator.root, supervision)
            supervisor = Supervisor(operator)
            thread = supervisor.open_thread("topic")
            supervisor.post_message(ScriptedModel(), thread["id"], "req")
            plan = supervisor.write_plan(thread["id"])
            approved = supervisor.approve_plan(thread["id"], plan["nonce"])
            stop = threading.Event()
            with (
                mock.patch.dict("os.environ", env, clear=False),
                mock.patch(
                    "remote_operator.notify.send_owner_mail",
                    side_effect=lambda **kw: calls.append(kw) or {"sent": True},
                ),
            ):
                worker = start_drain_worker(
                    operator, ScriptedModel, interval_seconds=0.05, stop_event=stop
                )
                deadline = time.monotonic() + 10
                job_path = operator.root / "supervisor_runs" / f"{approved['run_id']}.json"
                while time.monotonic() < deadline:
                    job = json.loads(job_path.read_text(encoding="utf-8"))
                    if job["status"] not in {"queued", "running"}:
                        break
                    time.sleep(0.05)
                stop.set()
                worker.join(timeout=5)
            return job["status"]

    def test_unattended_with_smtp_notifies(self):
        calls = []
        env = {
            "OPERATOR_OWNER_EMAIL": "owner@example.test",
            "OPERATOR_SMTP_HOST": "smtp.example.test",
        }
        status = self._drain_one("unattended", env, calls)
        self.assertEqual(status, "awaiting_approval")
        self.assertTrue(calls)
        self.assertEqual(calls[0]["to"], "owner@example.test")

    def test_attended_never_notifies(self):
        calls = []
        env = {
            "OPERATOR_OWNER_EMAIL": "owner@example.test",
            "OPERATOR_SMTP_HOST": "smtp.example.test",
        }
        status = self._drain_one("attended", env, calls)
        self.assertEqual(status, "awaiting_approval")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
