"""Owner-notification gap fix: attended run-event mail plus agent inbox.

Regression cover for the 2026-11-02 incident (an owner thread message sat
invisible behind a scripted ack): key run events mail in every supervision
mode whenever SMTP is configured, missing config stays silent, and an external
supervisor can poll ``GET /api/supervisor/inbox`` for exactly what needs
attention. The mail transport is always stubbed; no test sends real mail.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from remote_operator import settings as run_settings
from remote_operator.control import Operation, Operator, Validation
from remote_operator.scripted import ScriptedModel
from remote_operator.supervisor import Supervisor
from remote_operator.supervisor_inbox import read_inbox
from remote_operator.supervisor_runner import (
    SupervisorRunner,
    format_run_mail,
    job_had_owner_questions,
    notify_plan,
)

SMTP_ENV = {
    "OPERATOR_OWNER_EMAIL": "owner@example.test",
    "OPERATOR_SMTP_HOST": "smtp.example.test",
}
NO_SMTP_ENV = {"OPERATOR_OWNER_EMAIL": "", "OPERATOR_SMTP_HOST": ""}


class Validator:
    """Notify-fix test double; never used by the application or CLI."""

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


class LoopModel:
    """Never finishes; exhausts the model call allowance."""

    name = "loop-test-double"

    def next_operation(self, history, *, prompt):
        return Operation("read_file", {"path": "issue_import.py"})


def _operator(root):
    root.mkdir(parents=True, exist_ok=True)
    seed = root / "seed.py"
    seed.write_text("# seed\n", encoding="utf-8")
    return Operator(root / "runtime", seed, Validator())


def _approved(supervisor, topic="topic"):
    thread = supervisor.open_thread(topic)
    supervisor.post_message(ScriptedModel(), thread["id"], "do the thing")
    plan = supervisor.write_plan(thread["id"])
    queued = supervisor.approve_plan(thread["id"], plan["nonce"])
    return thread["id"], queued["run_id"]


def _job(operator, run_id):
    path = operator.root / "supervisor_runs" / f"{run_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _planned_sender(operator, supervision):
    """Return (sender, notify_on) exactly as the serve drain worker would."""
    run_settings.save_settings(operator.root, supervision)
    with mock.patch.dict(os.environ, SMTP_ENV, clear=False):
        return notify_plan(operator)


class AttendedMailTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.operator = _operator(Path(self.directory.name))
        self.supervisor = Supervisor(self.operator)

    def tearDown(self):
        self.directory.cleanup()

    def _drain_with_stub(self, runner, model, calls):
        with mock.patch(
            "remote_operator.notify.send_owner_mail",
            side_effect=lambda **kw: calls.append(kw) or {"sent": True},
        ):
            return runner.drain(model)

    def test_waiting_on_owner_mails_in_attended(self):
        _, run_id = _approved(self.supervisor)
        sender, notify_on = _planned_sender(self.operator, "attended")
        self.assertIsNotNone(sender)
        calls = []
        outcomes = self._drain_with_stub(
            SupervisorRunner(self.operator, notify_send=sender, notify_on=notify_on),
            AskOnceModel(),
            calls,
        )
        self.assertEqual(outcomes, [{"run_id": run_id, "status": "waiting_on_owner"}])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["to"], "owner@example.test")
        self.assertIn("waiting_on_owner", calls[0]["subject"])
        self.assertFalse(calls[0]["dry_run"])
        self.assertIn("answer in Review & approve", calls[0]["body"])

    def test_failed_mails_in_attended(self):
        _, run_id = _approved(self.supervisor)
        sender, notify_on = _planned_sender(self.operator, "attended")
        calls = []
        outcomes = self._drain_with_stub(
            SupervisorRunner(self.operator, notify_send=sender, notify_on=notify_on),
            LoopModel(),
            calls,
        )
        self.assertEqual(outcomes, [{"run_id": run_id, "status": "failed"}])
        self.assertEqual(len(calls), 1)
        self.assertIn("failed", calls[0]["subject"])
        self.assertIn("Error:", calls[0]["body"])

    def test_completed_with_questions_mails_in_attended(self):
        _, run_id = _approved(self.supervisor)
        sender, notify_on = _planned_sender(self.operator, "attended")
        runner = SupervisorRunner(self.operator, notify_send=sender, notify_on=notify_on)
        calls = []
        with mock.patch(
            "remote_operator.notify.send_owner_mail",
            side_effect=lambda **kw: calls.append(kw) or {"sent": True},
        ):
            runner.drain(AskOnceModel())
            task_id = _job(self.operator, run_id)["task_id"]
            self.operator.answer(task_id, "JSON, please")
            runner.resume_answered(task_id)
            runner.drain(AskOnceModel())
            nonce = self.operator.review(task_id)["task"]["approval"]["nonce"]
            self.operator.approve(task_id, nonce)
            refreshed = runner.refresh_waiting()
        self.assertEqual(refreshed, [{"run_id": run_id, "status": "completed"}])
        self.assertTrue(_job(self.operator, run_id)["had_questions"])
        subjects = [call["subject"] for call in calls]
        self.assertEqual(len(calls), 2)
        self.assertIn("waiting_on_owner", subjects[0])
        self.assertIn("completed", subjects[1])
        self.assertIn("after your answers", calls[1]["body"])

    def test_plain_completed_silent_in_attended(self):
        _, run_id = _approved(self.supervisor)
        sender, notify_on = _planned_sender(self.operator, "attended")
        runner = SupervisorRunner(self.operator, notify_send=sender, notify_on=notify_on)
        calls = []
        with mock.patch(
            "remote_operator.notify.send_owner_mail",
            side_effect=lambda **kw: calls.append(kw) or {"sent": True},
        ):
            runner.drain(ScriptedModel())
            task_id = _job(self.operator, run_id)["task_id"]
            nonce = self.operator.review(task_id)["task"]["approval"]["nonce"]
            self.operator.approve(task_id, nonce)
            refreshed = runner.refresh_waiting()
        self.assertEqual(refreshed, [{"run_id": run_id, "status": "completed"}])
        self.assertFalse(_job(self.operator, run_id)["had_questions"])
        self.assertEqual(calls, [])

    def test_unattended_still_mails_plain_completed(self):
        _, run_id = _approved(self.supervisor)
        sender, notify_on = _planned_sender(self.operator, "unattended")
        runner = SupervisorRunner(self.operator, notify_send=sender, notify_on=notify_on)
        calls = []
        with mock.patch(
            "remote_operator.notify.send_owner_mail",
            side_effect=lambda **kw: calls.append(kw) or {"sent": True},
        ):
            runner.drain(ScriptedModel())
            task_id = _job(self.operator, run_id)["task_id"]
            nonce = self.operator.review(task_id)["task"]["approval"]["nonce"]
            self.operator.approve(task_id, nonce)
            refreshed = runner.refresh_waiting()
        self.assertEqual(refreshed, [{"run_id": run_id, "status": "completed"}])
        subjects = [call["subject"] for call in calls]
        self.assertEqual(len(calls), 2)
        self.assertIn("awaiting_approval", subjects[0])
        self.assertIn("completed", subjects[1])

    def test_missing_config_silent_in_both_modes(self):
        for supervision in ("attended", "unattended"):
            with self.subTest(supervision=supervision):
                operator = _operator(Path(self.directory.name) / supervision)
                supervisor = Supervisor(operator)
                _, run_id = _approved(supervisor)
                run_settings.save_settings(operator.root, supervision)
                with (
                    mock.patch.dict(os.environ, NO_SMTP_ENV, clear=False),
                    self.assertLogs("remote_operator.supervisor_runner", level="WARNING"),
                ):
                    sender, notify_on = notify_plan(operator)
                self.assertIsNone(sender)
                runner = SupervisorRunner(operator, notify_send=sender, notify_on=notify_on)
                outcomes = runner.drain(AskOnceModel())
                self.assertEqual(
                    outcomes, [{"run_id": run_id, "status": "waiting_on_owner"}]
                )
                job = _job(operator, run_id)
                self.assertNotIn("notify_receipt", job)
                self.assertNotIn("notify_error", job)
                self.assertFalse((operator.root / "notify_receipts").exists())


class QuestionSignalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.operator = _operator(Path(self.directory.name))

    def tearDown(self):
        self.directory.cleanup()

    def test_missing_task_reads_as_no_questions(self):
        self.assertFalse(job_had_owner_questions(self.operator, {}))
        self.assertFalse(
            job_had_owner_questions(self.operator, {"task_id": "0" * 32})
        )

    def test_answer_marks_task_as_questioned(self):
        task = self.operator.submit("demo task")
        job = {"task_id": task["id"]}
        self.assertFalse(job_had_owner_questions(self.operator, job))
        self.operator.run(task["id"], AskOnceModel())
        self.assertFalse(job_had_owner_questions(self.operator, job))
        self.operator.answer(task["id"], "JSON")
        self.assertTrue(job_had_owner_questions(self.operator, job))

    def test_completed_mail_names_answered_questions(self):
        asked = format_run_mail(
            {"run_id": "ab12", "status": "completed", "had_questions": True}, "T"
        )
        self.assertIn("after your answers", asked)
        self.assertIn("No action needed.", asked)
        plain = format_run_mail(
            {"run_id": "ab12", "status": "completed", "had_questions": False}, "T"
        )
        self.assertNotIn("after your answers", plain)
        self.assertIn("No action needed.", plain)


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.operator = _operator(Path(self.directory.name))
        self.supervisor = Supervisor(self.operator)

    def tearDown(self):
        self.directory.cleanup()

    def test_cursor_semantics_across_all_item_types(self):
        _, run_id = _approved(self.supervisor)
        watched = self.supervisor.open_thread("watched")
        self.supervisor.post_message(ScriptedModel(), watched["id"], "watch this")
        parked = self.operator.submit("needs input")
        self.operator.run(parked["id"], AskOnceModel())
        SupervisorRunner(self.operator).drain(LoopModel())
        first = read_inbox(self.operator, since=0)
        kinds = [item["type"] for item in first["items"]]
        self.assertEqual(
            kinds, ["owner_message", "owner_message", "parked_question", "run"]
        )
        approval_note, message, question, run = first["items"]
        self.assertEqual(approval_note["text"], "do the thing")
        self.assertEqual(message["thread_id"], watched["id"])
        self.assertEqual(message["text"], "watch this")
        self.assertEqual(question["task_id"], parked["id"])
        self.assertEqual(question["question"], "Which format?")
        self.assertIsNone(question["run_id"])
        self.assertFalse(question["answered"])
        self.assertEqual(run["run_id"], run_id)
        self.assertEqual(run["status"], "failed")
        self.assertTrue(run["error"])
        seqs = [item["seq"] for item in first["items"]]
        self.assertEqual(seqs, sorted(seqs))
        cursor = first["cursor"]
        self.assertGreaterEqual(cursor, max(seqs))
        self.assertEqual(read_inbox(self.operator, since=cursor)["items"], [])
        follow = self.supervisor.open_thread("follow-up")
        self.supervisor.post_message(ScriptedModel(), follow["id"], "second need")
        second = read_inbox(self.operator, since=cursor)
        self.assertEqual(len(second["items"]), 1)
        self.assertEqual(second["items"][0]["text"], "second need")
        self.assertGreater(second["cursor"], cursor)

    def test_owner_message_text_is_per_event(self):
        thread = self.supervisor.open_thread("topic")
        self.supervisor.post_message(ScriptedModel(), thread["id"], "alpha")
        self.supervisor.post_message(ScriptedModel(), thread["id"], "beta")
        inbox = read_inbox(self.operator, since=0)
        texts = [item["text"] for item in inbox["items"]]
        self.assertEqual(texts, ["alpha", "beta"])

    def test_parked_question_links_run_and_tracks_answer(self):
        _, run_id = _approved(self.supervisor)
        SupervisorRunner(self.operator).drain(AskOnceModel())
        task_id = _job(self.operator, run_id)["task_id"]
        inbox = read_inbox(self.operator, since=0)
        questions = [i for i in inbox["items"] if i["type"] == "parked_question"]
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["run_id"], run_id)
        self.assertFalse(questions[0]["answered"])
        self.operator.answer(task_id, "JSON")
        again = read_inbox(self.operator, since=0)
        questions = [i for i in again["items"] if i["type"] == "parked_question"]
        self.assertEqual(len(questions), 1)
        self.assertTrue(questions[0]["answered"])

    def test_completed_run_listed_without_error(self):
        _, run_id = _approved(self.supervisor)
        runner = SupervisorRunner(self.operator)
        runner.drain(ScriptedModel())
        task_id = _job(self.operator, run_id)["task_id"]
        nonce = self.operator.review(task_id)["task"]["approval"]["nonce"]
        self.operator.approve(task_id, nonce)
        runner.refresh_waiting()
        inbox = read_inbox(self.operator, since=0)
        runs = [i for i in inbox["items"] if i["type"] == "run"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["run_id"], run_id)
        self.assertEqual(runs[0]["status"], "completed")
        self.assertIsNone(runs[0]["error"])
        self.assertFalse(runs[0]["had_questions"])

    def test_non_terminal_runs_excluded(self):
        _, first = _approved(self.supervisor, topic="first")
        SupervisorRunner(self.operator).drain(ScriptedModel())
        self.assertEqual(_job(self.operator, first)["status"], "awaiting_approval")
        thread_id, second = _approved(self.supervisor, topic="second")
        self.operator.store.request_cancel(thread_id)
        SupervisorRunner(self.operator).drain(ScriptedModel())
        self.assertEqual(_job(self.operator, second)["status"], "cancelled")
        inbox = read_inbox(self.operator, since=0)
        self.assertEqual([i for i in inbox["items"] if i["type"] == "run"], [])

    def test_unknown_run_event_skipped_cursor_advances(self):
        thread = self.supervisor.open_thread("topic")
        self.supervisor.post_message(ScriptedModel(), thread["id"], "hello")
        stored = self.operator.store.get(thread["id"])
        self.operator.store.save(stored, "run_finished", {"run_id": "0" * 32})
        inbox = read_inbox(self.operator, since=0)
        self.assertEqual([i["type"] for i in inbox["items"]], ["owner_message"])
        self.assertEqual(read_inbox(self.operator, since=inbox["cursor"])["items"], [])

    def test_since_and_limit_validated(self):
        with self.assertRaises(ValueError):
            read_inbox(self.operator, since=-1)
        with self.assertRaises(ValueError):
            read_inbox(self.operator, since=True)
        with self.assertRaises(ValueError):
            read_inbox(self.operator, since="3")
        with self.assertRaises(ValueError):
            read_inbox(self.operator, limit=0)
        with self.assertRaises(ValueError):
            read_inbox(self.operator, limit=101)


class InboxRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx

        from remote_operator.web import create_app

        self.directory = tempfile.TemporaryDirectory()
        self.operator = _operator(Path(self.directory.name))
        self.supervisor = Supervisor(self.operator)
        self.token = "owner-test-credential-" + "x" * 32
        app = create_app(self.operator, self.token)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        )
        self.auth = {"Authorization": "Bearer " + self.token}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    async def test_inbox_roundtrip(self):
        denied = await self.client.get("/api/supervisor/inbox?since=0")
        self.assertEqual(denied.status_code, 401)
        thread = self.supervisor.open_thread("topic")
        self.supervisor.post_message(ScriptedModel(), thread["id"], "owner ping")
        first = await self.client.get("/api/supervisor/inbox?since=0", headers=self.auth)
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertIn("cursor", body)
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["type"], "owner_message")
        self.assertEqual(body["items"][0]["text"], "owner ping")
        second = await self.client.get(
            f"/api/supervisor/inbox?since={body['cursor']}", headers=self.auth
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["items"], [])

    async def test_inbox_rejects_bad_cursor(self):
        bad = await self.client.get("/api/supervisor/inbox?since=-1", headers=self.auth)
        self.assertEqual(bad.status_code, 422)
        nonsense = await self.client.get(
            "/api/supervisor/inbox?since=abc", headers=self.auth
        )
        self.assertEqual(nonsense.status_code, 422)


if __name__ == "__main__":
    unittest.main()
