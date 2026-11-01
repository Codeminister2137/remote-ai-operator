"""Window/agent-activity status route reports the owner machine file."""

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from remote_operator.control import Operator, Validation
from remote_operator.hosted import HostedSessions
from remote_operator.scoped import ScopedOperators
from remote_operator.web import create_app, create_hosted_app
from remote_operator.window_status import load_window_status

ROOT = Path(__file__).resolve().parents[1]
CODE = "demo-access-code-0002"

PAYLOAD = {
    "slice": "2026-11-01 10:00-11:00 UTC",
    "phase": "draining",
    "started_utc": "2026-11-01T10:00:00Z",
    "last_heartbeat_utc": "2026-11-01T10:30:00Z",
    "next_heartbeat_utc": "2026-11-01T10:35:00Z",
    "sleep_state": "awake",
    "note": "slice 3 of 5",
}


class Validator:
    def validate(self, workspace, candidate_digest, *, cancelled=None):
        return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")


def _operator(root):
    seed = root / "seed.py"
    seed.write_text("# seed\n", encoding="utf-8")
    return Operator(root / "runtime", seed, Validator())


class WindowStatusUnitTests(unittest.TestCase):
    def test_missing_file_reports_not_running(self):
        with tempfile.TemporaryDirectory() as name:
            self.assertEqual(load_window_status(Path(name)), {"running": False})

    def test_malformed_file_yields_defaults_never_raises(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "window-status.json").write_text("nope{", encoding="utf-8")
            body = load_window_status(root)
            self.assertFalse(body["running"])
            for field in PAYLOAD:
                self.assertEqual(body[field], "")
            (root / "window-status.json").write_text('["not", "a", "dict"]', encoding="utf-8")
            again = load_window_status(root)
            self.assertFalse(again["running"])
            for field in PAYLOAD:
                self.assertEqual(again[field], "")

    def test_non_string_fields_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "window-status.json").write_text(
                json.dumps({"slice": 42, "phase": None}), encoding="utf-8"
            )
            body = load_window_status(root)
            self.assertTrue(body["running"])
            self.assertEqual(body["slice"], "")
            self.assertEqual(body["phase"], "")
            self.assertEqual(body["note"], "")


class WindowEndpointTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_requires_owner_auth(self):
        denied = await self.client.get("/api/window")
        self.assertEqual(denied.status_code, 401)

    async def test_missing_file_returns_not_running(self):
        body = (await self.client.get("/api/window", headers=self.auth)).json()
        self.assertEqual(body, {"running": False})

    async def test_file_fields_are_returned(self):
        (self.operator.root / "window-status.json").write_text(
            json.dumps(PAYLOAD), encoding="utf-8"
        )
        response = await self.client.get("/api/window", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["running"])
        for field, value in PAYLOAD.items():
            self.assertEqual(body[field], value)

    async def test_malformed_file_yields_defaults(self):
        (self.operator.root / "window-status.json").write_text("nope{", encoding="utf-8")
        response = await self.client.get("/api/window", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["running"])
        for field in PAYLOAD:
            self.assertEqual(body[field], "")

    async def test_served_page_contains_window_status_line(self):
        page = await self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="windowStatus"', page.text)
        javascript = (await self.client.get("/app.js")).text
        self.assertIn("refreshWindowStatus", javascript)


class WindowHostedRefusalTests(unittest.IsolatedAsyncioTestCase):
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

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    async def test_visitors_are_refused(self):
        response = await self.client.get("/api/window")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
