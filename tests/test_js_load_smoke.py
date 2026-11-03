"""Load smoke: the served page script must execute its top level cleanly.

Regression test for the Nov-2 guided-rewrite defect where app.js threw
``ReferenceError: Cannot access 'diffMode' before initialization`` at load,
silently killing most of the UI. ``node --check`` cannot catch this class
(syntax-only); this test executes the real file against DOM stubs.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "js_load_smoke.cjs"


class JsLoadSmokeTests(unittest.TestCase):
    def test_app_js_top_level_executes(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node unavailable")
        proc = subprocess.run(
            [node, str(HARNESS)],
            check=False,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = (proc.stdout + proc.stderr).strip()
        self.assertEqual(
            proc.returncode,
            0,
            "js load smoke failed:\n" + output[-2000:],
        )
        self.assertIn("SMOKE-OK", proc.stdout)
