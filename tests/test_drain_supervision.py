"""Drain worker stays alive and reports liveness; deaths restart loudly."""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from remote_operator.control import Operator, Validation
from remote_operator.scripted import ScriptedModel
from remote_operator.supervisor_runner import (
    DrainWorkerStatus,
    start_drain_worker,
    supervise_drain_worker,
)


class Validator:
    def validate(self, workspace, candidate_digest, *, cancelled=None):
        return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")


def _operator(root):
    seed = root / "seed.py"
    seed.write_text("# seed\n", encoding="utf-8")
    return Operator(root / "runtime", seed, Validator())


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class DrainSupervisionTests(unittest.TestCase):
    def test_passes_counted_and_timestamped(self):
        with tempfile.TemporaryDirectory() as name:
            operator = _operator(Path(name))
            status = DrainWorkerStatus()
            stop = threading.Event()
            thread = start_drain_worker(
                operator, ScriptedModel, interval_seconds=0.05, stop_event=stop, status=status
            )
            try:
                self.assertTrue(_wait_until(lambda: status.passes >= 3))
                snap = status.snapshot(thread.is_alive())
                self.assertTrue(snap["alive"])
                self.assertIsNotNone(snap["last_pass"])
                self.assertGreaterEqual(snap["passes"], 3)
                self.assertEqual(snap["errors"], 0)
                self.assertEqual(snap["restarts"], 0)
            finally:
                stop.set()
                thread.join(timeout=5)

    def test_failed_pass_counted_not_fatal(self):
        def broken_factory():
            raise ValueError("no actor today")

        with tempfile.TemporaryDirectory() as name:
            operator = _operator(Path(name))
            status = DrainWorkerStatus()
            stop = threading.Event()
            thread = start_drain_worker(
                operator, broken_factory, interval_seconds=0.05, stop_event=stop, status=status
            )
            try:
                self.assertTrue(_wait_until(lambda: status.errors >= 2))
                self.assertTrue(thread.is_alive())
            finally:
                stop.set()
                thread.join(timeout=5)

    def test_dead_worker_restarts(self):
        calls = {"count": 0}
        deaths = []
        real_hook = threading.excepthook

        def flaky_factory():
            calls["count"] += 1
            if calls["count"] == 1:
                raise KeyboardInterrupt("fatal test double")
            return ScriptedModel()

        threading.excepthook = deaths.append
        try:
            with tempfile.TemporaryDirectory() as name:
                operator = _operator(Path(name))
                stop = threading.Event()
                monitor, status = supervise_drain_worker(
                    operator, flaky_factory, interval_seconds=0.05, stop_event=stop
                )
                try:
                    self.assertTrue(_wait_until(lambda: status.restarts >= 1))
                    self.assertTrue(_wait_until(lambda: status.passes >= 1))
                finally:
                    stop.set()
                    monitor.join(timeout=5)
        finally:
            threading.excepthook = real_hook
        self.assertTrue(
            any(isinstance(item.exc_value, KeyboardInterrupt) for item in deaths),
            "expected the fatal to escape the dead worker thread",
        )

    def test_supervise_validates_interval(self):
        with tempfile.TemporaryDirectory() as name:
            operator = _operator(Path(name))
            for bad in (0, -1, True, "30"):
                with self.assertRaises(ValueError):
                    supervise_drain_worker(operator, ScriptedModel, interval_seconds=bad)


class DrainEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx

        from remote_operator.web import create_app

        self.directory = tempfile.TemporaryDirectory()
        self.operator = _operator(Path(self.directory.name))
        self.token = "owner-test-credential-" + "x" * 32
        status = DrainWorkerStatus()
        status.record_pass(True)
        app = create_app(
            self.operator,
            self.token,
            drain_snapshot=lambda: status.snapshot(True),
        )
        plain = create_app(self.operator, self.token)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        )
        self.plain_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=plain), base_url="http://localhost"
        )
        self.auth = {"Authorization": "Bearer " + self.token}

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.plain_client.aclose()
        self.directory.cleanup()

    async def test_drain_reports_snapshot(self):
        denied = await self.client.get("/api/drain")
        self.assertEqual(denied.status_code, 401)
        body = (await self.client.get("/api/drain", headers=self.auth)).json()
        self.assertTrue(body["running"])
        self.assertTrue(body["alive"])
        self.assertEqual(body["passes"], 1)
        self.assertEqual(body["errors"], 0)
        self.assertEqual(body["restarts"], 0)
        self.assertIsNotNone(body["last_pass"])

    async def test_drain_absent_without_worker(self):
        body = (await self.plain_client.get("/api/drain", headers=self.auth)).json()
        self.assertEqual(body, {"running": False})


if __name__ == "__main__":
    unittest.main()
