"""Serve drain worker: automatic queued-job drain without the CLI."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from remote_operator.control import Operator, Validation
from remote_operator.scripted import ScriptedModel
from remote_operator.supervisor import Supervisor
from remote_operator.supervisor_runner import SupervisorRunner, start_drain_worker


class FakeValidator:
    """Worker test double; never used by the application or CLI."""

    def validate(self, workspace, candidate_digest, *, cancelled=None):
        return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")


class DrainWorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        seed = root / "seed.py"
        seed.write_text("# seed\n", encoding="utf-8")
        self.operator = Operator(root / "runtime", seed, FakeValidator())
        self.supervisor = Supervisor(self.operator)
        self.stop = threading.Event()
        self.threads = []

    def tearDown(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=5)
        self.directory.cleanup()

    def _approved(self):
        thread = self.supervisor.open_thread("topic")
        self.supervisor.post_message(ScriptedModel(), thread["id"], "do the thing")
        plan = self.supervisor.write_plan(thread["id"])
        queued = self.supervisor.approve_plan(thread["id"], plan["nonce"])
        return thread["id"], queued["run_id"]

    def _job_path(self, run_id):
        return self.operator.root / "supervisor_runs" / f"{run_id}.json"

    def _job(self, run_id):
        return json.loads(self._job_path(run_id).read_text(encoding="utf-8"))

    def _wait_status(self, run_id, statuses, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._job(run_id)["status"] in statuses:
                return self._job(run_id)
            time.sleep(0.05)
        return self._job(run_id)

    def test_worker_drains_queued_job_without_cli(self):
        _, run_id = self._approved()
        self.assertEqual(self._job(run_id)["status"], "queued")
        self.threads.append(
            start_drain_worker(
                self.operator, ScriptedModel, interval_seconds=0.05, stop_event=self.stop
            )
        )
        job = self._wait_status(run_id, {"awaiting_approval"})
        self.assertEqual(job["status"], "awaiting_approval")
        task = self.operator.store.get(job["task_id"])
        self.assertEqual(task["status"], "awaiting_approval")

    def test_worker_reconciles_stale_running_job_at_startup(self):
        _, run_id = self._approved()
        job = self._job(run_id)
        job["status"] = "running"
        self._job_path(run_id).write_text(json.dumps(job), encoding="utf-8")
        self.threads.append(
            start_drain_worker(
                self.operator, ScriptedModel, interval_seconds=0.05, stop_event=self.stop
            )
        )
        job = self._wait_status(run_id, {"awaiting_approval"})
        self.assertEqual(job["status"], "awaiting_approval")

    def test_worker_survives_a_bad_pass(self):
        self.threads.append(
            start_drain_worker(
                self.operator, ScriptedModel, interval_seconds=0.05, stop_event=self.stop
            )
        )
        with (
            mock.patch.object(SupervisorRunner, "drain", side_effect=RuntimeError("boom")),
            self.assertLogs("remote_operator.supervisor_runner", level="ERROR"),
        ):
            time.sleep(0.3)
        thread = self.threads[0]
        self.assertTrue(thread.is_alive())
        self.assertTrue(thread.daemon)

    def test_worker_stops_on_event(self):
        thread = start_drain_worker(
            self.operator, ScriptedModel, interval_seconds=60, stop_event=self.stop
        )
        self.threads.append(thread)
        self.stop.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())

    def test_invalid_interval_rejected(self):
        with self.assertRaises(ValueError):
            start_drain_worker(self.operator, ScriptedModel, interval_seconds=0)
        with self.assertRaises(ValueError):
            start_drain_worker(self.operator, ScriptedModel, interval_seconds=-1)


if __name__ == "__main__":
    unittest.main()
