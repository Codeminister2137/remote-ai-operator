"""Single-writer hosted runtime checks: durability hook, volume lifecycle, startup refusal."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

import httpx

from remote_operator.control import Validation
from remote_operator.deployment import build_hosted_application, prepare_runtime
from remote_operator.store import Store

ROOT = Path(__file__).resolve().parents[1]
CODE = "deployment-access-code-0001"
SECRET = "deployment-session-secret-" + "s" * 32


class Validator:
    def validate(self, workspace, candidate_digest, *, cancelled=None):
        return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")


class Volume:
    """Minimal stand-in for modal.Volume; records the calls the runtime makes."""

    def __init__(self):
        self.commits = 0
        self.reloads = 0

    def commit(self):
        self.commits += 1

    def reload(self):
        self.reloads += 1


class DurableStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "tasks.sqlite3"
        self.commits = 0
        self.store = Store(self.path, after_commit=self._commit)

    def _commit(self):
        self.commits += 1

    def tearDown(self):
        self.directory.cleanup()

    def task(self):
        return {"id": "a" * 32, "prompt": "durable", "status": "queued"}

    def test_schema_and_each_committed_write_is_flushed_once(self):
        self.assertEqual(self.commits, 1)
        self.store.save(self.task(), "submitted")
        self.assertEqual(self.commits, 2)
        self.store.save(self.task(), "started")
        self.assertEqual(self.commits, 3)
        status = self.store.reserve_allowance("grant", "fingerprint", 2, "0.10", "1.00")
        self.assertEqual(status["requests_used"], 1)
        self.assertEqual(self.commits, 4)
        self.store.request_cancel(self.task()["id"])
        self.assertEqual(self.commits, 5)

    def test_reads_refused_writes_and_duplicates_do_not_flush(self):
        self.store.save(self.task(), "submitted")
        before = self.commits
        self.store.get(self.task()["id"])
        self.store.recent_tasks()
        self.store.events(self.task()["id"])
        self.store.allowance_status("grant", "fingerprint", 2, "1.00")
        self.store.cancelled(self.task()["id"])
        self.assertEqual(self.commits, before)
        with self.assertRaises(KeyError):
            self.store.get("b" * 32)
        self.assertEqual(self.commits, before)

    def test_failed_reservation_is_not_flushed_and_does_not_consume(self):
        self.store.reserve_allowance("grant", "fingerprint", 1, "0.10", "1.00")
        before = self.commits
        with self.assertRaises(ValueError):
            self.store.reserve_allowance("grant", "fingerprint", 1, "0.10", "1.00")
        self.assertEqual(self.commits, before)
        status = self.store.allowance_status("grant", "fingerprint", 1, "1.00")
        self.assertEqual(status["requests_used"], 1)

    def test_store_without_a_durability_hook_still_works(self):
        plain = Store(Path(self.directory.name) / "plain.sqlite3")
        plain.save(self.task(), "submitted")
        self.assertEqual(plain.get(self.task()["id"])["status"], "queued")


class PreparedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "runtime"

    def tearDown(self):
        self.directory.cleanup()

    def test_runtime_root_is_reloaded_created_and_verified(self):
        volume = Volume()
        prepared = prepare_runtime(self.root, volume)
        self.assertTrue(prepared.is_dir())
        self.assertEqual((volume.reloads, volume.commits), (1, 0))

    def test_existing_store_is_accepted(self):
        subject = self.root / ("c" * 64)
        subject.mkdir(parents=True)
        Store(subject / "tasks.sqlite3").save(
            {"id": "a" * 32, "prompt": "kept", "status": "queued"}, "submitted"
        )
        prepare_runtime(self.root)
        self.assertEqual(len(list(self.root.glob("*/tasks.sqlite3"))), 1)

    def test_corrupt_store_refuses_startup_instead_of_discarding_state(self):
        subject = self.root / ("d" * 64)
        subject.mkdir(parents=True)
        (subject / "tasks.sqlite3").write_bytes(b"not a database" * 100)
        with self.assertRaises(ValueError):
            prepare_runtime(self.root)
        self.assertTrue((subject / "tasks.sqlite3").exists())


class HostedDeploymentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    async def asyncTearDown(self):
        self.directory.cleanup()

    def build(self, volume=None, grant=None, session_secret=None):
        return build_hosted_application(
            runtime_root=self.root / "runtime",
            project_root=ROOT,
            access_code=CODE,
            public_origin=None,
            session_secret=session_secret,
            volume=volume,
            grant=grant,
            validator_factory=Validator,
        )

    async def client(self, application):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://localhost"
        )

    async def test_deployment_app_needs_the_access_code_and_stays_scripted(self):
        volume = Volume()
        application = self.build(volume)
        self.assertEqual(volume.reloads, 1)
        client = await self.client(application)
        async with client:
            config = (await client.get("/api/config")).json()
            self.assertTrue(config["hosted"])
            self.assertFalse(config["authenticated"])
            self.assertFalse(config["model_route_configured"])
            self.assertEqual(
                (await client.post("/api/session", json={"code": "wrong"})).status_code, 403
            )
            entered = await client.post("/api/session", json={"code": CODE})
            self.assertEqual(entered.status_code, 200, entered.text)
            created = await client.post(
                "/api/tasks",
                headers={"X-Operator-Request": "1"},
                json={"prompt": "deployed task"},
            )
            self.assertEqual(created.status_code, 200, created.text)
        self.assertGreater(volume.commits, 0)

    async def test_recorded_state_stays_on_disk_across_a_restart(self):
        first = self.build()
        client = await self.client(first)
        async with client:
            await client.post("/api/session", json={"code": CODE})
            task = (
                await client.post(
                    "/api/tasks",
                    headers={"X-Operator-Request": "1"},
                    json={"prompt": "survives"},
                )
            ).json()
        stores = list((self.root / "runtime" / "visitors").glob("*/tasks.sqlite3"))
        self.assertEqual(len(stores), 1)
        db = sqlite3.connect(stores[0])
        try:
            recorded = db.execute("SELECT record FROM tasks").fetchall()
        finally:
            db.close()
        self.assertEqual(len(recorded), 1)
        self.assertEqual(Store(stores[0]).get(task["id"])["prompt"], "survives")

    async def test_signed_cookie_resumes_the_same_subject_after_a_restart(self):
        first = self.build(session_secret=SECRET)
        client = await self.client(first)
        async with client:
            await client.post("/api/session", json={"code": CODE})
            task = (
                await client.post(
                    "/api/tasks",
                    headers={"X-Operator-Request": "1"},
                    json={"prompt": "resumable"},
                )
            ).json()
            cookie = client.cookies.get("operator_session")
        self.assertTrue(cookie)
        second = self.build(session_secret=SECRET)
        restarted = await self.client(second)
        async with restarted:
            restarted.cookies.set("operator_session", cookie)
            listed = await restarted.get("/api/tasks")
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual([item["id"] for item in listed.json()], [task["id"]])

    async def test_cookie_signed_with_another_secret_is_refused(self):
        first = self.build(session_secret=SECRET)
        client = await self.client(first)
        async with client:
            await client.post("/api/session", json={"code": CODE})
            cookie = client.cookies.get("operator_session")
        second = self.build(session_secret="another-secret-" + "o" * 32)
        restarted = await self.client(second)
        async with restarted:
            restarted.cookies.set("operator_session", cookie)
            self.assertEqual((await restarted.get("/api/tasks")).status_code, 401)

    async def test_shutdown_commits_the_volume(self):
        volume = Volume()
        application = self.build(volume)
        async with application.router.lifespan_context(application):
            pass
        self.assertEqual(volume.commits, 1)

    async def test_missing_sample_is_refused(self):
        with tempfile.TemporaryDirectory() as empty, self.assertRaises(ValueError):
            build_hosted_application(
                runtime_root=self.root / "runtime",
                project_root=Path(empty),
                access_code=CODE,
                public_origin=None,
                validator_factory=Validator,
            )


class SchemaTests(unittest.TestCase):
    def test_task_store_schema_still_matches_the_local_store(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite3"
            Store(path)
            db = sqlite3.connect(path)
            try:
                names = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
            finally:
                db.close()
        self.assertEqual(names - {"sqlite_sequence"}, {"tasks", "allowances", "controls", "events"})
