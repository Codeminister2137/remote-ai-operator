"""Connect actions report adjacent success/failure status (feedback standard)."""

import tempfile
import unittest
from pathlib import Path


class ConnectFeedbackTests(unittest.IsolatedAsyncioTestCase):
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

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    async def test_access_section_has_adjacent_status(self):
        page = await self.client.get("/")
        self.assertIn('id="accessStatus"', page.text)

    async def test_connect_reports_success_and_failure_adjacently(self):
        script = await self.client.get("/app.js")
        for snippet in (
            'accessStatus("Connecting',
            'accessStatus("Connected',
            "Connect failed",
        ):
            self.assertIn(snippet, script.text)
