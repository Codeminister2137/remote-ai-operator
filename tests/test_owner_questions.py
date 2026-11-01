"""Owner questions park work without blocking other runs; answers resume it."""

import json
import tempfile
import unittest
from pathlib import Path

from remote_operator.control import Operation, Operator, Validation
from remote_operator.scripted import ScriptedModel
from remote_operator.supervisor import Supervisor
from remote_operator.supervisor_runner import SupervisorRunner


class Validator:
    def validate(self, workspace, candidate_digest, *, cancelled=None):
        return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")


class AskOnceModel:
    """Ask one question on the first call, then follow the scripted flow."""

    name = "ASK ONCE TEST DOUBLE"
    _scripted = ScriptedModel()

    def next_operation(self, history, *, prompt):
        names = [entry["name"] for entry in history]
        if "ask_owner" not in names:
            return Operation("ask_owner", {"question": "Which format?"})
        return self._scripted.next_operation(history, prompt=prompt)


def _operator(root):
    seed = root / "seed.py"
    seed.write_text("# seed\n", encoding="utf-8")
    return Operator(root / "runtime", seed, Validator())


class OwnerQuestionCycleTests(unittest.TestCase):
    def test_ask_parks_answer_resumes_to_approval(self):
        with tempfile.TemporaryDirectory() as name:
            operator = _operator(Path(name))
            task = operator.submit("demo task")
            parked = operator.run(task["id"], AskOnceModel())
            self.assertEqual(parked["status"], "waiting_on_owner")
            self.assertEqual(parked["pending_question"], "Which format?")
            answered = operator.answer(task["id"], "JSON, please")
            self.assertEqual(answered["status"], "paused")
            self.assertIsNone(answered["pending_question"])
            resumed = operator.run(task["id"], AskOnceModel())
            self.assertEqual(resumed["status"], "awaiting_approval")

    def test_answer_validation(self):
        with tempfile.TemporaryDirectory() as name:
            operator = _operator(Path(name))
            task = operator.submit("demo task")
            with self.assertRaises(ValueError):
                operator.answer(task["id"], "no question pending")
            parked = operator.run(task["id"], AskOnceModel())
            self.assertEqual(parked["status"], "waiting_on_owner")
            for bad in ("", " ", "x" * 2001):
                with self.assertRaises(ValueError):
                    operator.answer(task["id"], bad)

    def test_ask_validates_question(self):
        with tempfile.TemporaryDirectory() as name:
            operator = _operator(Path(name))
            task = operator.submit("demo task")
            with self.assertRaises(ValueError):
                operator._execute(task, Operation("ask_owner", {"question": " "}))


class QuestionRunnerTests(unittest.TestCase):
    def _queued(self, operator):
        supervisor = Supervisor(operator)
        thread = supervisor.open_thread("topic")
        supervisor.post_message(ScriptedModel(), thread["id"], "req")
        plan = supervisor.write_plan(thread["id"])
        return supervisor.approve_plan(thread["id"], plan["nonce"])["run_id"]

    def test_drain_mirrors_waiting_and_answer_requeues(self):
        with tempfile.TemporaryDirectory() as name:
            operator = _operator(Path(name))
            run_id = self._queued(operator)
            runner = SupervisorRunner(operator)
            runner.drain(AskOnceModel())
            job_path = operator.root / "supervisor_runs" / f"{run_id}.json"
            job = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(job["status"], "waiting_on_owner")
            task_id = job["task_id"]
            operator.answer(task_id, "JSON")
            resumed = runner.resume_answered(task_id)
            self.assertEqual([r["run_id"] for r in resumed], [run_id])
            job = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(job["status"], "queued")


class AnswerRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx

        from remote_operator.web import create_app

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

    async def test_answer_route_roundtrip(self):
        task = self.operator.submit("demo task")
        parked = self.operator.run(task["id"], AskOnceModel())
        self.assertEqual(parked["status"], "waiting_on_owner")
        denied = await self.client.post(f"/api/tasks/{task['id']}/answer", json={"text": "hi"})
        self.assertEqual(denied.status_code, 401)
        bad = await self.client.post(
            f"/api/tasks/{task['id']}/answer", headers=self.auth, json={"text": ""}
        )
        self.assertEqual(bad.status_code, 422)
        ok = await self.client.post(
            f"/api/tasks/{task['id']}/answer", headers=self.auth, json={"text": "JSON"}
        )
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["status"], "paused")
        again = await self.client.post(
            f"/api/tasks/{task['id']}/answer", headers=self.auth, json={"text": "late"}
        )
        self.assertEqual(again.status_code, 409)


if __name__ == "__main__":
    unittest.main()
