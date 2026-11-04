"""Supervisor planning threads: lifecycle, approval binding and isolation."""

import tempfile
import time
import unittest
from pathlib import Path


class SupervisorFlowTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_unauthorized_thread_access_refused(self):
        response = await self.client.post("/api/supervisor/threads", json={"topic": "x"})
        self.assertEqual(response.status_code, 401)

    async def test_full_lifecycle_to_queued_run(self):
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
        self.assertIn("SCRIPTED OFFLINE REFERENCE", message.json()["reply"])
        plan = await self.client.post(
            f"/api/supervisor/threads/{thread_id}/plan", headers=self.auth
        )
        self.assertEqual(plan.status_code, 200, plan.text)
        body = plan.json()
        self.assertIn("Ship it", body["markdown"])
        self.assertIn("retries", body["markdown"])
        approved = await self.client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": body["nonce"]},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        run_id = approved.json()["run_id"]
        runs = await self.client.get("/api/supervisor/runs", headers=self.auth)
        self.assertEqual(runs.status_code, 200)
        self.assertEqual(runs.json()["runs"][0]["run_id"], run_id)
        status = await self.client.get(f"/api/supervisor/runs/{run_id}", headers=self.auth)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["thread_id"], thread_id)
        self.assertEqual(status.json()["status"], "queued")

    async def test_wrong_nonce_rejected(self):
        opened = await self.client.post(
            "/api/supervisor/threads", headers=self.auth, json={"topic": "t"}
        )
        thread_id = opened.json()["id"]
        await self.client.post(
            f"/api/supervisor/threads/{thread_id}/messages",
            headers=self.auth,
            json={"text": "req"},
        )
        await self.client.post(f"/api/supervisor/threads/{thread_id}/plan", headers=self.auth)
        denied = await self.client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": "0" * 32},
        )
        self.assertEqual(denied.status_code, 409)

    async def test_unknown_thread_is_not_found(self):
        missing = await self.client.get("/api/supervisor/threads/" + "f" * 32, headers=self.auth)
        self.assertEqual(missing.status_code, 404)
        missing_run = await self.client.get("/api/supervisor/runs/" + "e" * 32, headers=self.auth)
        self.assertEqual(missing_run.status_code, 404)

    async def test_plain_task_id_is_not_a_thread(self):
        created = await self.client.post(
            "/api/tasks", headers=self.auth, json={"prompt": "Implement sample"}
        )
        task_id = created.json()["id"]
        response = await self.client.get(f"/api/supervisor/threads/{task_id}", headers=self.auth)
        self.assertEqual(response.status_code, 409)

    async def test_thread_list_route_needs_auth_and_lists_newest_first(self):
        denied = await self.client.get("/api/supervisor/threads")
        self.assertEqual(denied.status_code, 401)
        empty = await self.client.get("/api/supervisor/threads", headers=self.auth)
        self.assertEqual(empty.json(), [])
        first = await self.client.post(
            "/api/supervisor/threads", headers=self.auth, json={"topic": "first"}
        )
        await self.client.post("/api/tasks", headers=self.auth, json={"prompt": "plain"})
        second = await self.client.post(
            "/api/supervisor/threads", headers=self.auth, json={"topic": "second"}
        )
        listed = await self.client.get("/api/supervisor/threads", headers=self.auth)
        self.assertEqual(
            [item["id"] for item in listed.json()],
            [second.json()["id"], first.json()["id"]],
        )
        self.assertEqual(listed.json()[0]["topic"], "second")
        bad = await self.client.get("/api/supervisor/threads?limit=99", headers=self.auth)
        self.assertEqual(bad.status_code, 422)

    async def test_runs_route_returns_wrapped_list(self):
        opened = await self.client.post(
            "/api/supervisor/threads", headers=self.auth, json={"topic": "t"}
        )
        thread_id = opened.json()["id"]
        await self.client.post(
            f"/api/supervisor/threads/{thread_id}/messages",
            headers=self.auth,
            json={"text": "do it"},
        )
        await self.client.post(
            f"/api/supervisor/threads/{thread_id}/plan", headers=self.auth
        )
        before = await self.client.get("/api/supervisor/runs", headers=self.auth)
        self.assertIn("runs", before.json())
        plan = await self.client.get(
            f"/api/supervisor/threads/{thread_id}", headers=self.auth
        )
        nonce = plan.json()["thread"]["approval"]["nonce"]
        queued = await self.client.post(
            f"/api/supervisor/threads/{thread_id}/approve",
            headers=self.auth,
            json={"nonce": nonce},
        )
        after = await self.client.get("/api/supervisor/runs", headers=self.auth)
        self.assertEqual(
            [run["run_id"] for run in after.json()["runs"]], [queued.json()["run_id"]]
        )

    async def test_served_ui_exposes_supervisor_section(self):
        page = await self.client.get("/")
        for element_id in (
            "supervisorThreads",
            "newThreadRow",
            "supervisorNewTopic",
            "supervisorOpen",
            "supervisorTranscript",
            "supervisorInput",
            "supervisorSend",
            "supervisorPlan",
            "supervisorWritePlan",
            "supervisorApprovePlan",
            "supervisorRuns",
            "supervisorNote",
        ):
            self.assertIn(f'id="{element_id}"', page.text)
        self.assertIn(">Create thread<", page.text)
        self.assertIn("One thread is one planning conversation", page.text)
        self.assertIn('id="newThreadRow" hidden', page.text)
        self.assertIn('id="inboxBell" class="secondary nav-action"', page.text)
        self.assertIn('id="tourStartNav" class="secondary nav-action"', page.text)

    async def test_served_script_marks_task_source_and_polls_window(self):
        script = await self.client.get("/app.js")
        for marker in (
            '"__new__"',
            "taskSource",
            "opened from",
            "startWindowPolling",
            "No pending Workbench actions.",
        ):
            self.assertIn(marker, script.text)

    async def test_served_script_calls_supervisor_endpoints(self):
        script = await self.client.get("/app.js")
        for path in (
            "/api/supervisor/threads",
            "/messages",
            "/plan",
            "/approve",
            "/api/supervisor/runs",
        ):
            self.assertIn(path, script.text)


class SupervisorUnitTests(unittest.TestCase):
    def setUp(self):
        from remote_operator.control import Operator, Validation
        from remote_operator.supervisor import Supervisor

        class Validator:
            def validate(self, workspace, candidate_digest, *, cancelled=None):
                return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")

        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        seed = root / "seed.py"
        seed.write_text("# seed\n", encoding="utf-8")
        operator = Operator(root / "runtime", seed, Validator())
        self.supervisor = Supervisor(operator)

    def tearDown(self):
        self.directory.cleanup()

    def _planned(self):
        from remote_operator.scripted import ScriptedModel

        thread = self.supervisor.open_thread("topic")
        self.supervisor.post_message(ScriptedModel(), thread["id"], "do the thing")
        return thread["id"], self.supervisor.write_plan(thread["id"])

    def test_changed_plan_invalidates_approval(self):
        from remote_operator.control import digest

        thread_id, plan = self._planned()
        thread = self.supervisor._thread(thread_id)
        thread["plan"]["markdown"] += "\nTampered."
        self.supervisor.operator.store.save(thread, "test_tamper", {})
        self.assertNotEqual(digest(thread["plan"]["markdown"]), plan["digest"])
        with self.assertRaises(ValueError):
            self.supervisor.approve_plan(thread_id, plan["nonce"])
        reopened = self.supervisor._thread(thread_id)
        self.assertEqual(reopened["status"], "open")
        self.assertIsNone(reopened["approval"])

    def test_expired_nonce_rejected(self):
        thread_id, plan = self._planned()
        with self.assertRaises(ValueError):
            self.supervisor.approve_plan(thread_id, plan["nonce"], now=time.time() + 3600)

    def test_plan_requires_requirements(self):
        thread = self.supervisor.open_thread("empty")
        with self.assertRaises(ValueError):
            self.supervisor.write_plan(thread["id"])

    def test_expired_plan_can_be_rewritten_but_live_plan_cannot(self):
        thread_id, first = self._planned()
        with self.assertRaises(ValueError):
            self.supervisor.write_plan(thread_id)
        thread = self.supervisor._thread(thread_id)
        thread["approval"]["expires"] = time.time() - 1
        self.supervisor.operator.store.save(thread, "test_expire", {})
        second = self.supervisor.write_plan(thread_id)
        self.assertNotEqual(second["nonce"], first["nonce"])
        self.assertEqual(self.supervisor._thread(thread_id)["status"], "planned")

    def test_malformed_ids_rejected(self):
        with self.assertRaises(ValueError):
            self.supervisor.get_thread("../escape")
        with self.assertRaises(ValueError):
            self.supervisor.run_status("short")

    def test_list_threads_covers_empty_filter_and_limit(self):
        self.assertEqual(self.supervisor.list_threads(), [])
        first = self.supervisor.open_thread("first topic")
        self.operator = self.supervisor.operator
        self.operator.submit("plain task prompt")
        second = self.supervisor.open_thread("second topic")
        listed = self.supervisor.list_threads()
        self.assertEqual([item["id"] for item in listed], [second["id"], first["id"]])
        self.assertEqual(listed[0]["topic"], "second topic")
        self.assertEqual(listed[0]["status"], "open")
        self.assertEqual(len(self.supervisor.list_threads(1)), 1)
        with self.assertRaises(ValueError):
            self.supervisor.list_threads(0)
        with self.assertRaises(ValueError):
            self.supervisor.list_threads(99)

    def test_live_reply_prefers_model_words(self):
        from remote_operator.control import Operation

        class WordyModel:
            name = "wordy-test-model"

            def next_operation(self, history, *, prompt):
                return Operation(
                    "read_file",
                    {"path": "sample-task.md"},
                    summary="Let me look at the task file first.",
                )

        thread = self.supervisor.open_thread("topic")
        reply = self.supervisor.post_message(WordyModel(), thread["id"], "hello")
        self.assertEqual(reply["route"], "meta")
        self.assertIn("Let me look at the task file first.", reply["reply"])
        self.assertIn("Next step: read sample-task.md.", reply["reply"])

    def test_live_reply_template_without_model_words(self):
        from remote_operator.control import Operation

        class QuietModel:
            name = "quiet-test-model"

            def next_operation(self, history, *, prompt):
                return Operation("read_file", {"path": "sample-task.md"})

        thread = self.supervisor.open_thread("topic")
        reply = self.supervisor.post_message(QuietModel(), thread["id"], "hello")
        self.assertEqual(reply["reply"], "[quiet-test-model] Next step: read sample-task.md.")


class HumanizeOperationTests(unittest.TestCase):
    def test_known_operations_read_as_sentences(self):
        from remote_operator.supervisor import humanize_operation

        self.assertEqual(
            humanize_operation("read_file", {"path": "sample-task.md"}),
            "Next step: read sample-task.md.",
        )
        self.assertEqual(
            humanize_operation("ask_owner", {"question": "Which format?"}),
            "Question: Which format?",
        )
        self.assertEqual(humanize_operation("validate", {}), "Next step: run validation.")
        self.assertEqual(
            humanize_operation("finish", {"summary": "All set."}),
            "Planning complete. All set.",
        )

    def test_unknown_and_malformed_operations_never_dump_json(self):
        from remote_operator.supervisor import humanize_operation

        self.assertEqual(humanize_operation("frobnicate", {"x": 1}), "Next step: frobnicate.")
        self.assertEqual(humanize_operation("read_file", {}), "Next step: read a file.")
        self.assertEqual(humanize_operation("read_file", None), "Next step: read a file.")
