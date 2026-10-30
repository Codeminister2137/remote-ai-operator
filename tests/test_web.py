"""Local API identity, request-boundary and approval regression checks."""

import tempfile
import unittest
from pathlib import Path


class WebTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_unauthorized_and_cross_origin_refused(self):
        response = await self.client.post("/api/tasks", json={"prompt": "task"})
        self.assertEqual(response.status_code, 401)
        response = await self.client.post(
            "/api/tasks",
            headers={**self.auth, "Origin": "https://attacker.invalid"},
            json={"prompt": "task"},
        )
        self.assertEqual(response.status_code, 403)

    async def test_api_workflow_rejects_replayed_approval(self):
        response = await self.client.post(
            "/api/tasks", headers=self.auth, json={"prompt": "Implement sample"}
        )
        path = "/api/tasks/" + response.json()["id"]
        result = await self.client.post(
            path + "/run", headers=self.auth, json={"inject_failure": True}
        )
        self.assertEqual(result.status_code, 200, result.text)
        review = (await self.client.get(path, headers=self.auth)).json()
        nonce = review["task"]["approval"]["nonce"]
        self.assertIn("import_issues", review["diff"])
        applied = await self.client.post(
            path + "/approve", headers=self.auth, json={"nonce": nonce}
        )
        self.assertEqual(applied.status_code, 200)
        replay = await self.client.post(path + "/approve", headers=self.auth, json={"nonce": nonce})
        self.assertEqual(replay.status_code, 409)

    async def test_unicode_nonce_refused_without_echo_and_valid_still_applies(self):
        response = await self.client.post(
            "/api/tasks", headers=self.auth, json={"prompt": "Implement sample"}
        )
        path = "/api/tasks/" + response.json()["id"]
        result = await self.client.post(path + "/run", headers=self.auth, json={})
        self.assertEqual(result.status_code, 200, result.text)
        review = (await self.client.get(path, headers=self.auth)).json()
        nonce = review["task"]["approval"]["nonce"]
        bad = "é" * len(nonce)
        refused = await self.client.post(path + "/approve", headers=self.auth, json={"nonce": bad})
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertNotIn(bad, refused.text)
        pending = (await self.client.get(path, headers=self.auth)).json()
        self.assertEqual(pending["task"]["status"], "awaiting_approval")
        self.assertEqual(pending["task"]["approval"]["nonce"], nonce)
        applied = await self.client.post(
            path + "/approve", headers=self.auth, json={"nonce": nonce}
        )
        self.assertEqual(applied.status_code, 200, applied.text)

    async def test_request_limits_unknown_tasks_and_extra_arguments(self):
        response = await self.client.post(
            "/api/tasks", headers=self.auth, json={"prompt": "task", "provider": "paid"}
        )
        self.assertEqual(response.status_code, 422)
        response = await self.client.post("/api/tasks", headers=self.auth, content="x" * 8193)
        self.assertEqual(response.status_code, 413)
        response = await self.client.get("/api/tasks/missing", headers=self.auth)
        self.assertEqual(response.status_code, 404)

    async def test_static_ui_is_safe_and_no_store(self):
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Load execution mode before submitting", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        javascript = (await self.client.get("/app.js")).text
        self.assertNotIn("innerHTML", javascript)
        self.assertNotIn("localStorage", javascript)

    async def test_mode_is_server_owned_and_queued_cancellation_is_authenticated(self):
        config = await self.client.get("/api/config", headers=self.auth)
        self.assertEqual(
            config.json(), {"mode": "scripted", "live_inference": False, "https_origin": None}
        )
        task = (
            await self.client.post("/api/tasks", headers=self.auth, json={"prompt": "task"})
        ).json()
        path = "/api/tasks/" + task["id"]
        self.assertEqual((await self.client.post(path + "/cancel", json={})).status_code, 401)
        result = await self.client.post(path + "/cancel", headers=self.auth, json={})
        self.assertEqual(result.json()["status"], "cancelled")
        self.assertEqual(
            (await self.client.post(path + "/run", headers=self.auth, json={})).status_code, 409
        )
        self.assertEqual(
            (
                await self.client.post(
                    path + "/sample", headers=self.auth, json={"csv_text": "id,title,status\n"}
                )
            ).status_code,
            409,
        )

    async def test_streamed_bodies_are_bounded_without_requiring_content_length(self):
        async def small():
            yield b'{"prompt":'
            yield b'"streamed request"}'

        response = await self.client.post(
            "/api/tasks", headers={**self.auth, "Content-Type": "application/json"}, content=small()
        )
        self.assertEqual(response.status_code, 200, response.text)

        async def large():
            for _ in range(3):
                yield b"x" * 4096

        response = await self.client.post("/api/tasks", headers=self.auth, content=large())
        self.assertEqual(response.status_code, 413)

    async def test_explicit_https_proxy_origin_does_not_trust_arbitrary_forwarded_hosts(self):
        import httpx

        from remote_operator.web import create_app

        app = create_app(self.operator, self.token, public_origin="https://operator.example.test")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://operator.example.test"
        ) as client:
            response = await client.post(
                "/api/tasks",
                headers={**self.auth, "Origin": "https://operator.example.test"},
                json={"prompt": "proxy task"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            response = await client.post(
                "/api/tasks",
                headers={**self.auth, "Origin": "https://attacker.invalid"},
                json={"prompt": "blocked"},
            )
            self.assertEqual(response.status_code, 403)
            response = await client.get(
                "/api/config",
                headers={
                    **self.auth,
                    "Host": "attacker.invalid",
                    "X-Forwarded-Host": "operator.example.test",
                },
            )
            self.assertEqual(response.status_code, 403)
        with self.assertRaises(ValueError):
            create_app(self.operator, self.token, public_origin="http://public.example.test")

    async def test_recent_task_collection_is_owner_only_and_bounded(self):
        from unittest.mock import patch

        self.assertEqual((await self.client.get("/api/tasks")).status_code, 401)
        self.assertEqual((await self.client.get("/api/tasks", headers=self.auth)).json(), [])
        with patch.object(
            self.operator, "run", side_effect=AssertionError("Listing must not run a task")
        ):
            first = self.operator.submit("First")
            second = self.operator.submit("<img src=x onerror=window.pwned=true>" + "x" * 200)
            third = self.operator.submit("Third")
            first["status"] = "paused"
            self.operator.store.save(first, "test_status_update")
            before = self.operator.store.events(first["id"])
            response = await self.client.get("/api/tasks?limit=2", headers=self.auth)
            self.assertEqual(response.status_code, 200)
            self.assertEqual([item["id"] for item in response.json()], [third["id"], second["id"]])
            for item in response.json():
                self.assertEqual(set(item), {"id", "prompt", "status"})
                self.assertLessEqual(len(item["prompt"]), 160)
            full = (await self.client.get("/api/tasks", headers=self.auth)).json()
            self.assertEqual(full[-1]["status"], "paused")
            self.assertEqual(self.operator.store.events(first["id"]), before)
        for value in ("0", "51", "1000000", "bad"):
            with self.subTest(limit=value):
                self.assertEqual(
                    (
                        await self.client.get("/api/tasks?limit=" + value, headers=self.auth)
                    ).status_code,
                    422,
                )
        response = await self.client.get(
            "/api/tasks", headers={**self.auth, "Origin": "https://untrusted.example"}
        )
        self.assertEqual(response.status_code, 403)

    async def test_recent_store_reads_persisted_creation_order_without_schema_change(self):
        from remote_operator.store import Store

        first = self.operator.submit("Older")
        newer = self.operator.submit("Newer")
        self.operator.store.save(first, "saved_again")
        reopened = Store(self.operator.store.path)
        self.assertEqual(
            [item["id"] for item in reopened.recent_tasks()], [newer["id"], first["id"]]
        )
        self.assertEqual(len(reopened.recent_tasks(limit=1)), 1)
        for limit in (False, 0, -1, 51, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                reopened.recent_tasks(limit=limit)

    async def test_allowance_metadata_authentication_does_not_construct_actor(self):
        from unittest.mock import Mock

        import httpx

        from remote_operator.web import create_app

        supplier = Mock(return_value={"enabled": True, "remaining_usd_estimate": "0.75"})
        factory = Mock(side_effect=AssertionError("Metadata must not instantiate actor"))
        app = create_app(
            self.operator, self.token, model_factory=factory, mode="meta", allowance_status=supplier
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            self.assertEqual((await client.get("/api/allowance")).status_code, 401)
            supplier.assert_not_called()
            self.assertEqual(
                (await client.get("/api/allowance", headers=self.auth)).json(),
                supplier.return_value,
            )
            self.assertEqual(
                (
                    await client.get(
                        "/api/allowance",
                        headers={**self.auth, "Origin": "https://untrusted.example"},
                    )
                ).status_code,
                403,
            )
        self.assertEqual(supplier.call_count, 1)
        factory.assert_not_called()
        self.assertEqual(
            (await self.client.get("/api/allowance", headers=self.auth)).json(), {"enabled": False}
        )

    async def test_interrupted_write_reconciliation_is_authenticated_read_only_and_never_resumes(
        self,
    ):
        from unittest.mock import patch

        from remote_operator.control import SOURCE, read_source, write_source
        from remote_operator.scripted import REFERENCE

        task = self.operator.submit("Interrupted source write fixture")
        workspace = self.operator.folder(task["id"], "candidate")
        write_source(workspace, REFERENCE)
        task["status"] = "reconciliation_required"
        task["pending"] = {
            "name": "write_file",
            "arguments": {"path": SOURCE, "content": REFERENCE},
        }
        self.operator.store.save(task, "fixture_interrupted")
        path = "/api/tasks/" + task["id"] + "/reconcile"
        self.assertEqual((await self.client.post(path, json={})).status_code, 401)
        self.assertTrue(self.operator.lock.acquire(blocking=False))
        try:
            self.assertEqual(
                (await self.client.post(path, headers=self.auth, json={})).status_code, 409
            )
        finally:
            self.operator.lock.release()
        self.assertEqual(self.operator.store.get(task["id"])["status"], "reconciliation_required")
        with (
            patch.object(
                self.operator, "run", side_effect=AssertionError("No automatic execution")
            ),
            patch(
                "remote_operator.control.write_source",
                side_effect=AssertionError("No mutation replay"),
            ),
        ):
            response = await self.client.post(path, headers=self.auth, json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "paused")
        self.assertIsNone(response.json()["pending"])
        self.assertEqual(len(response.json()["history"]), 1)
        self.assertEqual(read_source(workspace), REFERENCE)
        self.assertEqual(
            (await self.client.post(path, headers=self.auth, json={})).status_code, 409
        )

    async def test_interrupted_apply_reconciliation_inspects_completed_effect_without_replay(self):
        from unittest.mock import patch

        from remote_operator.control import digest, write_source
        from remote_operator.scripted import REFERENCE, ScriptedModel

        task = self.operator.submit("Interrupted apply fixture")
        ready = self.operator.run(task["id"], ScriptedModel())
        ready["status"] = "applying"
        write_source(self.operator.folder(task["id"], "target"), REFERENCE)
        self.operator.store.save(ready, "fixture_apply_crash")
        with patch(
            "remote_operator.control.write_source", side_effect=AssertionError("No repeated apply")
        ):
            response = await self.client.post(
                "/api/tasks/" + task["id"] + "/reconcile", headers=self.auth, json={}
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "completed")
        self.assertEqual(response.json()["applied_digest"], digest(REFERENCE))
        self.assertIsNone(response.json()["approval"])

    async def test_unknown_path_returns_404_with_path_and_no_internal_leak(self):
        missing = "/api/does-not-exist-404-regression"
        response = await self.client.get(missing, headers=self.auth)
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertIn(missing, body.get("detail", ""))
        text = response.text.lower()
        for leak in ("traceback", "stack", "internal", "exception"):
            self.assertNotIn(leak, text)

    def test_public_handlers_have_docstrings(self):
        """Regression: public handlers must retain descriptive docstrings."""
        import ast
        import pathlib

        source = pathlib.Path("src/remote_operator/web.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        required = {"_origin", "create_app", "create_hosted_app"}
        found = {}
        handlers = {
            "index",
            "javascript",
            "stylesheet",
            "submit",
            "list_tasks",
            "review",
            "events",
            "run",
            "approve",
            "reject",
            "reconcile",
            "cancel",
            "sample",
            "revalidate",
            "allowance",
            "submit_external",
            "list_external",
            "review_external",
            "approve_external",
            "reject_external",
            "cancel_external",
            "configuration",
            "open_session",
            "close_session",
            "attach_key",
            "detach_key",
            "owner",
            "visitor",
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name in required or node.name in handlers
            ):
                doc = ast.get_docstring(node)
                found[node.name] = doc
        for name in required:
            self.assertIn(name, found, f"missing handler {name}")
            self.assertTrue(found[name], f"{name} missing docstring")
        # Ensure at least 10 handlers documented
        documented = [k for k, v in found.items() if v]
        self.assertGreaterEqual(len(documented), 10, f"only {documented} documented")
