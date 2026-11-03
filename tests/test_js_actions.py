"""UI behavior checks: approve gating, submit reasons, hosted plan note.

Executes the real ``app.js`` against DOM stubs and drives ``actions()``
states (frontend-audit batch, Nov 3). Also covers mode-aware key-note
funding copy and inline run/supervision scope hints.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "js_actions.cjs"


class JsActionsTests(unittest.TestCase):
    def test_actions_states(self):
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
            "js actions test failed:\n" + output[-2000:],
        )
        self.assertIn("ACTIONS-OK", proc.stdout)
