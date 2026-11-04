"""Project-task checks: path admission, staging, approval and apply boundaries."""

import os
import tempfile
import unittest
from pathlib import Path

from remote_operator.control import (
    MAX_SOURCE_BYTES,
    Operation,
    Operator,
    ProjectOperator,
    digest,
    read_project_file,
    resolve_project_path,
    stage_project_file,
    staged_project_files,
)


class FakeValidator:
    """Project test double; project tasks never call it."""

    def validate(self, workspace, candidate_digest, *, cancelled=None):
        raise AssertionError("project tasks run no automated checks")


class ScriptedOps:
    """Model test double replaying a fixed operation list, then finishing."""

    name = "test-script"

    def __init__(self, operations):
        self.operations = list(operations)
        self.seen_roots = []

    def next_operation(self, history, *, prompt, project_root=None):
        self.seen_roots.append(project_root)
        if len(history) >= len(self.operations):
            return Operation("finish", {})
        return self.operations[len(history)]


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "repo"
        (self.root / "app").mkdir(parents=True)
        (self.root / "app" / "main.py").write_bytes(b"print('hi')\n")

    def tearDown(self):
        self.directory.cleanup()

    def test_nested_read_roundtrip(self):
        self.assertEqual(read_project_file(self.root, "app/main.py"), "print('hi')\n")

    def test_refuses_traversal_absolute_and_drives(self):
        for rel in (
            "../escape.py",
            "app/../../escape.py",
            "..",
            "/etc/hosts",
            "C:/win.py",
            "C:\\win.py",
            "a\\b.py",
            "notes.txt:Zone.Identifier",
            "",
            "x" * 513,
        ):
            with self.subTest(rel=rel), self.assertRaises(ValueError):
                resolve_project_path(self.root, rel)

    def test_refuses_dot_segments_and_secrets(self):
        for rel in (
            ".env",
            ".git/config",
            "app/.hidden.py",
            "./app/main.py",
            "app/./main.py",
            "notes.pem",
            "tls.key",
            "credentials.json",
            "id_rsa",
            "a/id_ed25519",
        ):
            with self.subTest(rel=rel), self.assertRaises(ValueError):
                resolve_project_path(self.root, rel)

    def test_refuses_trailing_dot_and_space_segments(self):
        # Windows strips trailing dots/spaces, so "credentials.json." would
        # otherwise read credentials.json past the secret-name rules.
        for rel in (
            "notes.txt.",
            "notes.txt ",
            "credentials.json.",
            "credentials.json ",
            "tls.key.",
            "id_rsa ",
            "app./main.py",
            "app /main.py",
        ):
            with self.subTest(rel=rel), self.assertRaises(ValueError):
                resolve_project_path(self.root, rel)
        staging = Path(self.directory.name) / "staging"
        with self.assertRaises(ValueError):
            stage_project_file(staging, "notes.txt.", "x\n")

    def test_refuses_resolved_aliases_of_hidden_and_secret_files(self):
        # 8.3 short names (ENV~1 -> .env) alias hidden/secret files to
        # innocent-looking names; the resolved path must be re-checked.
        if os.name != "nt":
            self.skipTest("8.3 short names are Windows-only")
        import ctypes

        (self.root / ".env").write_text("OPERATOR_ACCESS_TOKEN=x\n", encoding="utf-8")
        (self.root / "credentials.json").write_text("{}\n", encoding="utf-8")
        probed = False
        for name in (".env", "credentials.json"):
            buffer = ctypes.create_unicode_buffer(512)
            length = ctypes.windll.kernel32.GetShortPathNameW(str(self.root / name), buffer, 512)
            short = Path(buffer.value).name if length else name
            if short == name or not (self.root / short).exists():
                continue  # Short names are disabled on this volume.
            probed = True
            with self.subTest(alias=short), self.assertRaises(ValueError):
                resolve_project_path(self.root, short)
            with self.subTest(alias=short), self.assertRaises(ValueError):
                read_project_file(self.root, short)
        if not probed:
            self.skipTest("short names disabled on this volume")

    def test_refuses_links(self):
        outside = Path(self.directory.name) / "outside.py"
        outside.write_text("outside\n", encoding="utf-8")
        try:
            (self.root / "link.py").symlink_to(outside)
        except OSError:
            self.skipTest("symlinks need privileges on this machine")
        with self.assertRaises(ValueError):
            resolve_project_path(self.root, "link.py")
        inner = self.root / "app" / "inner.py"
        inner.symlink_to(self.root / "app" / "main.py")
        with self.assertRaises(ValueError):
            resolve_project_path(self.root, "app/inner.py")

    def test_read_refuses_missing_directory_binary_and_oversize(self):
        with self.assertRaises(ValueError):
            read_project_file(self.root, "app/nope.py")
        with self.assertRaises(ValueError):
            read_project_file(self.root, "app")
        (self.root / "blob.bin").write_bytes(b"\xff\xfe\x00bad")
        with self.assertRaises(ValueError):
            read_project_file(self.root, "blob.bin")
        (self.root / "big.txt").write_bytes(b"x" * (MAX_SOURCE_BYTES + 1))
        with self.assertRaises(ValueError):
            read_project_file(self.root, "big.txt")

    def test_stage_roundtrip_and_listing(self):
        staging = Path(self.directory.name) / "staging"
        first = stage_project_file(staging, "app/main.py", "print('v2')\n")
        self.assertEqual(first, digest("print('v2')\n"))
        stage_project_file(staging, "notes.txt", "hello\n")
        self.assertEqual(staged_project_files(staging), ["app/main.py", "notes.txt"])
        with self.assertRaises(ValueError):
            stage_project_file(staging, "../escape.py", "x\n")


class ProjectTaskTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.work = base / "repo"
        (self.work / "app").mkdir(parents=True)
        (self.work / "app" / "main.py").write_bytes(b"print('v1')\n")
        seed = base / "seed.py"
        seed.write_text("# seed\n", encoding="utf-8")
        self.operator = ProjectOperator(
            base / "runtime", seed, FakeValidator(), work_roots=[self.work]
        )

    def tearDown(self):
        self.directory.cleanup()

    def submit(self, prompt="Improve the app"):
        return self.operator.submit(prompt, mode="project", project=str(self.work))

    def test_base_operator_refuses_project_mode(self):
        base = Path(self.directory.name)
        seed = base / "seed.py"
        plain = Operator(base / "plain-runtime", seed, FakeValidator())
        with self.assertRaises(ValueError):
            plain.submit("x", mode="project", project=str(self.work))
        with self.assertRaises(ValueError):
            plain.submit("x", project=str(self.work))

    def test_submit_requires_known_mode_and_registered_root(self):
        with self.assertRaises(ValueError):
            self.operator.submit("x", mode="project")
        with self.assertRaises(ValueError):
            self.operator.submit("x", mode="project", project=str(self.work / "app"))
        with self.assertRaises(ValueError):
            self.operator.submit("x", mode="weird", project=str(self.work))

    def test_work_root_overlap_refused(self):
        base = Path(self.directory.name)
        seed = base / "seed.py"
        with self.assertRaises(ValueError):
            ProjectOperator(base / "rt", seed, FakeValidator(), work_roots=[])
        with self.assertRaises(ValueError):
            ProjectOperator(base / "rt", seed, FakeValidator(), work_roots=[base / "missing"])
        runtime = base / "rt2"
        runtime.mkdir()
        with self.assertRaises(ValueError):
            ProjectOperator(runtime, seed, FakeValidator(), work_roots=[runtime])
        with self.assertRaises(ValueError):
            ProjectOperator(runtime, seed, FakeValidator(), work_roots=[runtime / "inner"])

    def test_run_passes_project_root_to_model(self):
        task = self.submit()
        model = ScriptedOps(
            [
                Operation("read_file", {"path": "app/main.py"}),
                Operation("write_file", {"path": "app/main.py", "content": "print('v2')\n"}),
                Operation("validate", {}),
            ]
        )
        self.operator.run(task["id"], model)
        stored = self.operator.store.get(task["id"])
        self.assertTrue(model.seen_roots)
        for seen in model.seen_roots:
            self.assertEqual(seen, stored["project_root"])

    def test_execution_stopped_carries_failure_detail(self):
        from remote_operator.scripted import ScriptedModel

        task = self.submit()
        with self.assertRaises(ValueError):
            self.operator.run(task["id"], ScriptedModel())
        stored = self.operator.store.get(task["id"])
        self.assertEqual(stored["status"], "paused")
        events = self.operator.store.events(task["id"])
        stopped = [item for item in events if item["kind"] == "execution_stopped"]
        self.assertEqual(len(stopped), 1)
        self.assertIn("live inference", stopped[0]["payload"]["detail"])

    def test_project_tool_error_returns_to_model_for_recovery(self):
        task = self.submit()
        model = ScriptedOps(
            [
                Operation("read_file", {"path": "notes.txt"}),
                Operation("write_file", {"path": "notes.txt", "content": "- call mom\n"}),
                Operation("validate", {}),
            ]
        )
        result = self.operator.run(task["id"], model)
        self.assertEqual(result["status"], "awaiting_approval")
        self.assertIn("error", result["history"][0]["result"])
        review = self.operator.review(task["id"])
        self.assertEqual([entry["path"] for entry in review["files"]], ["notes.txt"])

    def test_project_protocol_violation_still_stops_loudly(self):
        task = self.submit()
        model = ScriptedOps([Operation("evil_tool", {})])
        with self.assertRaises(ValueError):
            self.operator.run(task["id"], model)

    def test_scripted_reference_refuses_project_tasks(self):
        from remote_operator.scripted import ScriptedModel

        with self.assertRaisesRegex(ValueError, "live inference"):
            ScriptedModel().next_operation([], prompt="x", project_root=str(self.work))
        operation = ScriptedModel().next_operation([], prompt="x")
        self.assertEqual(operation.name, "read_file")

    def test_lifecycle_stages_and_applies(self):
        task = self.submit()
        ops = [
            Operation("read_file", {"path": "app/main.py"}),
            Operation("write_file", {"path": "app/main.py", "content": "print('v2')\n"}),
            Operation("validate", {}),
        ]
        result = self.operator.run(task["id"], ScriptedOps(ops))
        self.assertEqual(result["status"], "awaiting_approval")
        kinds = [entry["name"] for entry in result["history"]]
        self.assertEqual(kinds, ["read_file", "write_file", "validate"])
        self.assertEqual(result["history"][0]["result"], {"content": "print('v1')\n"})
        self.assertEqual(result["history"][2]["result"]["checks"], "none-configured")
        self.assertEqual(
            (self.work / "app" / "main.py").read_text(encoding="utf-8"), "print('v1')\n"
        )
        review = self.operator.review(task["id"])
        self.assertEqual([entry["path"] for entry in review["files"]], ["app/main.py"])
        self.assertIn("-print('v1')", review["diff"])
        self.assertIn("+print('v2')", review["diff"])
        approval = result["approval"]
        self.assertEqual(approval["checks"], "none")
        done = self.operator.approve(task["id"], approval["nonce"])
        self.assertEqual(done["status"], "completed")
        self.assertEqual(
            (self.work / "app" / "main.py").read_text(encoding="utf-8"), "print('v2')\n"
        )
        with self.assertRaises(ValueError):
            self.operator.approve(task["id"], approval["nonce"])

    def test_new_file_in_existing_directory_applies(self):
        task = self.submit()
        ops = [Operation("write_file", {"path": "app/extra.py", "content": "X=1\n"})]
        result = self.operator.run(task["id"], ScriptedOps(ops))
        self.operator.approve(task["id"], result["approval"]["nonce"])
        self.assertEqual((self.work / "app" / "extra.py").read_text(encoding="utf-8"), "X=1\n")

    def test_finish_without_staged_changes_refuses(self):
        task = self.submit()
        with self.assertRaises(ValueError):
            self.operator.run(task["id"], ScriptedOps([]))
        self.assertEqual(self.operator.store.get(task["id"])["status"], "paused")

    def test_staged_tamper_invalidates_approval(self):
        task = self.submit()
        ops = [Operation("write_file", {"path": "app/main.py", "content": "print('v2')\n"})]
        result = self.operator.run(task["id"], ScriptedOps(ops))
        staging = self.operator.folder(task["id"], "staging")
        (staging / "app" / "main.py").write_text("print('evil')\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.operator.approve(task["id"], result["approval"]["nonce"])
        self.assertEqual(self.operator.store.get(task["id"])["status"], "review_required")
        self.assertEqual(
            (self.work / "app" / "main.py").read_text(encoding="utf-8"), "print('v1')\n"
        )

    def test_base_change_invalidates_approval(self):
        task = self.submit()
        ops = [Operation("write_file", {"path": "app/main.py", "content": "print('v2')\n"})]
        result = self.operator.run(task["id"], ScriptedOps(ops))
        (self.work / "app" / "main.py").write_text("print('v1b')\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.operator.approve(task["id"], result["approval"]["nonce"])
        self.assertEqual(self.operator.store.get(task["id"])["status"], "review_required")

    def test_sample_and_revalidate_refused(self):
        task = self.submit()
        ops = [Operation("write_file", {"path": "app/main.py", "content": "print('v2')\n"})]
        result = self.operator.run(task["id"], ScriptedOps(ops))
        with self.assertRaises(ValueError):
            self.operator.sample(task["id"], "id\n")
        with self.assertRaises(ValueError):
            self.operator.revalidate(task["id"])
        self.operator.approve(task["id"], result["approval"]["nonce"])
        with self.assertRaises(ValueError):
            self.operator.sample(task["id"], "id\n")

    def test_apply_refuses_runtime_overlap(self):
        base = Path(self.directory.name)
        seed = base / "seed.py"
        runtime = self.work / "store"
        runtime.mkdir()
        operator = ProjectOperator(base / "rt3", seed, FakeValidator(), work_roots=[self.work])
        operator.root = runtime  # simulate a runtime nested in the work root
        operator.store = operator.store.__class__(runtime / "tasks.sqlite3")
        task = operator.submit("x", mode="project", project=str(self.work))
        ops = [Operation("write_file", {"path": "store/evil.py", "content": "x\n"})]
        result = operator.run(task["id"], ScriptedOps(ops))
        with self.assertRaises(ValueError):
            operator.approve(task["id"], result["approval"]["nonce"])

    def test_reconcile_pending_write_matches_staged(self):
        task = self.submit()
        staging = self.operator.folder(task["id"], "staging")
        stage_project_file(staging, "app/main.py", "print('v2')\n")
        stored = self.operator.store.get(task["id"])
        stored["status"] = "reconciliation_required"
        stored["pending"] = {
            "name": "write_file",
            "arguments": {"path": "app/main.py", "content": "print('v2')\n"},
        }
        self.operator.store.save(stored, "tool_started", {"name": "write_file"})
        reconciled = self.operator.reconcile(task["id"])
        self.assertEqual(reconciled["status"], "paused")
        self.assertEqual(reconciled["history"][-1]["name"], "write_file")

    def test_reconcile_pending_write_mismatch_refuses(self):
        task = self.submit()
        staging = self.operator.folder(task["id"], "staging")
        stage_project_file(staging, "app/main.py", "print('other')\n")
        stored = self.operator.store.get(task["id"])
        stored["status"] = "reconciliation_required"
        stored["pending"] = {
            "name": "write_file",
            "arguments": {"path": "app/main.py", "content": "print('v2')\n"},
        }
        self.operator.store.save(stored, "tool_started", {"name": "write_file"})
        with self.assertRaises(ValueError):
            self.operator.reconcile(task["id"])


class ProjectWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx

        from remote_operator.web import create_app

        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.work = base / "repo"
        self.work.mkdir()
        (self.work / "notes.txt").write_text("hi\n", encoding="utf-8")
        seed = base / "seed.py"
        seed.write_text("# seed\n", encoding="utf-8")
        self.operator = ProjectOperator(
            base / "runtime", seed, FakeValidator(), work_roots=[self.work]
        )
        self.token = "owner-test-credential-" + "y" * 32
        app = create_app(self.operator, self.token)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        )
        self.auth = {"Authorization": "Bearer " + self.token}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    async def test_work_roots_listed_and_unknown_root_refused(self):
        response = await self.client.get("/api/work-roots", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"roots": [str(self.work.resolve())]})
        bad = await self.client.post(
            "/api/tasks",
            headers=self.auth,
            json={"prompt": "x", "mode": "project", "project": str(self.work / "nope")},
        )
        self.assertEqual(bad.status_code, 409)

    async def test_plain_operator_reports_no_roots(self):
        import httpx

        from remote_operator.web import create_app

        base = Path(self.directory.name)
        seed = base / "seed.py"
        plain = Operator(base / "plain-rt", seed, FakeValidator())
        app = create_app(plain, self.token)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        )
        try:
            response = await client.get("/api/work-roots", headers=self.auth)
            self.assertEqual(response.json(), {"roots": []})
            denied = await client.post(
                "/api/tasks",
                headers=self.auth,
                json={"prompt": "x", "mode": "project", "project": str(self.work)},
            )
            self.assertEqual(denied.status_code, 409)
        finally:
            await client.aclose()


class BuildOperatorTests(unittest.TestCase):
    def test_work_roots_select_project_operator(self):
        import tempfile

        from remote_operator.cli import build_operator

        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            (base / "sample").mkdir()
            (base / "sample" / "issue_import.py").write_text("# sample\n")
            (base / "tests" / "acceptance").mkdir(parents=True)
            (base / "tests" / "acceptance" / "run_checks.py").write_text("# checks\n")
            work = base / "work"
            work.mkdir()
            plain = build_operator(base, base / "rt")
            self.assertIs(type(plain), Operator)
            project = build_operator(base, base / "rt", [work])
            self.assertIs(type(project), ProjectOperator)
            self.assertEqual(project.work_roots, (work.resolve(),))


if __name__ == "__main__":
    unittest.main()
