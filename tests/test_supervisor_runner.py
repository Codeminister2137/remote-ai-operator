"""Supervisor runner: drain, reconcile/resume, cancellation and notify hook."""

import json
import tempfile
import unittest
from pathlib import Path

from remote_operator.control import SOURCE, Operation, Operator, Validation, write_source
from remote_operator.scripted import ScriptedModel
from remote_operator.supervisor import Supervisor
from remote_operator.supervisor_runner import SupervisorRunner


class FakeValidator:
    """Runner test double; never used by the application or CLI."""

    def validate(self, workspace, candidate_digest, *, cancelled=None):
        return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")


class LoopModel:
    """Never finishes; exhausts the model call allowance."""

    name = "loop-test-double"

    def next_operation(self, history, *, prompt):
        return Operation("read_file", {"path": SOURCE})


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        seed = root / "seed.py"
        seed.write_text("# seed\n", encoding="utf-8")
        self.operator = Operator(root / "runtime", seed, FakeValidator())
        self.supervisor = Supervisor(self.operator)
        self.runner = SupervisorRunner(self.operator)
        self.sent = []
        self.notifying = SupervisorRunner(self.operator, notify_send=self.sent.append)

    def tearDown(self):
        self.directory.cleanup()

    def _approved(self, topic="topic", requirement="do the thing"):
        thread = self.supervisor.open_thread(topic)
        self.supervisor.post_message(ScriptedModel(), thread["id"], requirement)
        plan = self.supervisor.write_plan(thread["id"])
        queued = self.supervisor.approve_plan(thread["id"], plan["nonce"])
        return thread["id"], queued["run_id"]

    def _job_path(self, run_id):
        return self.operator.root / "supervisor_runs" / f"{run_id}.json"

    def _job(self, run_id):
        return json.loads(self._job_path(run_id).read_text(encoding="utf-8"))

    def test_drain_full_lifecycle_to_awaiting_approval(self):
        thread_id, run_id = self._approved()
        outcomes = self.runner.drain(ScriptedModel())
        self.assertEqual(outcomes, [{"run_id": run_id, "status": "awaiting_approval"}])
        job = self._job(run_id)
        self.assertEqual(job["status"], "awaiting_approval")
        self.assertNotIn("plan_truncated", job)
        task = self.operator.store.get(job["task_id"])
        self.assertEqual(task["status"], "awaiting_approval")
        kinds = [event["kind"] for event in self.operator.store.events(thread_id)]
        self.assertIn("run_started", kinds)
        self.assertIn("run_finished", kinds)

    def test_reconcile_requeues_claim_without_task(self):
        _, run_id = self._approved()
        job = self._job(run_id)
        job["status"] = "running"
        self._job_path(run_id).write_text(json.dumps(job), encoding="utf-8")
        outcomes = self.runner.reconcile_jobs()
        self.assertEqual(outcomes, [{"run_id": run_id, "action": "requeued"}])
        outcomes = self.runner.drain(ScriptedModel())
        self.assertEqual(outcomes, [{"run_id": run_id, "status": "awaiting_approval"}])

    def test_reconcile_resumes_interrupted_task_without_replay(self):
        _thread_id, run_id = self._approved()
        task = self.operator.submit("interrupted")
        candidate = self.operator.folder(task["id"], "candidate")
        write_source(candidate, "# interrupted edit\n")
        content = "# interrupted edit\n"
        task["status"] = "running"
        task["pending"] = {"name": "write_file", "arguments": {"path": SOURCE, "content": content}}
        self.operator.store.save(task, "test_interrupt", {})
        job = self._job(run_id)
        job["status"] = "running"
        job["task_id"] = task["id"]
        self._job_path(run_id).write_text(json.dumps(job), encoding="utf-8")
        outcomes = self.runner.reconcile_jobs()
        self.assertEqual(outcomes, [{"run_id": run_id, "action": "requeued"}])
        resumed = self.operator.store.get(task["id"])
        self.assertEqual(resumed["status"], "paused")
        self.assertIsNone(resumed["pending"])
        self.assertEqual(resumed["history"][-1]["name"], "write_file")
        outcomes = self.runner.drain(ScriptedModel())
        self.assertEqual(outcomes, [{"run_id": run_id, "status": "awaiting_approval"}])
        kinds = [event["kind"] for event in self.operator.store.events(task["id"])]
        self.assertIn("reconciled", kinds)

    def test_thread_cancel_cancels_queued_job(self):
        thread_id, run_id = self._approved()
        self.operator.store.request_cancel(thread_id)
        outcomes = self.runner.drain(ScriptedModel())
        self.assertEqual(outcomes, [{"run_id": run_id, "status": "cancelled"}])
        self.assertNotIn("task_id", self._job(run_id))

    def test_allowance_exhaustion_fails_job(self):
        _, run_id = self._approved()
        outcomes = self.runner.drain(LoopModel())
        self.assertEqual(outcomes, [{"run_id": run_id, "status": "failed"}])
        self.assertIn("allowance exhausted", self._job(run_id)["error"])

    def test_busy_operator_leaves_job_queued(self):
        _, run_id = self._approved()
        with self.operator.lock:
            outcomes = self.runner.drain(ScriptedModel())
        self.assertEqual(outcomes, [{"run_id": run_id, "status": "queued", "busy": True}])
        self.assertEqual(self._job(run_id)["status"], "queued")

    def test_malformed_job_file_skipped(self):
        _, run_id = self._approved()
        (self.operator.root / "supervisor_runs" / "zz.json").write_text(
            "not json{", encoding="utf-8"
        )
        outcomes = self.runner.drain(ScriptedModel())
        self.assertEqual(outcomes, [{"run_id": run_id, "status": "awaiting_approval"}])
        skipped = [o for o in self.runner.reconcile_jobs() if o["action"] == "skipped"]
        self.assertEqual(len(skipped), 1)

    def test_waiting_job_refreshes_after_owner_approval(self):
        _, run_id = self._approved()
        self.runner.drain(ScriptedModel())
        task_id = self._job(run_id)["task_id"]
        nonce = self.operator.review(task_id)["task"]["approval"]["nonce"]
        self.operator.approve(task_id, nonce)
        outcomes = self.runner.refresh_waiting()
        self.assertEqual(outcomes, [{"run_id": run_id, "status": "completed"}])

    def test_notify_hook_called_on_terminal_outcome(self):
        _, run_id = self._approved()
        self.notifying.drain(ScriptedModel())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["run_id"], run_id)
        self.assertEqual(self.sent[0]["status"], "awaiting_approval")
        self.assertIn("notify_receipt", self._job(run_id))

    def test_notify_failure_recorded_without_breaking_drain(self):
        def failing(summary):
            raise OSError("smtp down")

        _, run_id = self._approved()
        runner = SupervisorRunner(self.operator, notify_send=failing)
        outcomes = runner.drain(ScriptedModel())
        self.assertEqual(outcomes, [{"run_id": run_id, "status": "awaiting_approval"}])
        self.assertIn("smtp down", self._job(run_id)["notify_error"])

    def test_invalid_max_jobs_rejected(self):
        with self.assertRaises(ValueError):
            self.runner.drain(ScriptedModel(), max_jobs=0)


if __name__ == "__main__":
    unittest.main()
