"""Worker pill staleness: an old agent heartbeat must not show Working.

Regression test for the Nov-3 defect where a two-day-old
``window-status.json`` kept the nav pill on "Working" with no work running.
Executes the real ``app.js`` against DOM stubs and drives
``refreshWindowStatus`` with stale then fresh payloads.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "js_pill_stale.cjs"


class JsPillStaleTests(unittest.TestCase):
    def test_stale_heartbeat_idles_pill(self):
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
            "js pill stale test failed:\n" + output[-2000:],
        )
        self.assertIn("PILL-OK", proc.stdout)
