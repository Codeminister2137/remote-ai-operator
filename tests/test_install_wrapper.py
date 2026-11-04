"""Regression tests for the installer-generated serve wrapper template.

The wrapper (``.runtime/start-serve.ps1``) starts the server with its output
redirected to ``serve.log``. With ``$ErrorActionPreference = 'Stop'`` in
effect, any stderr line from Python (for example the "supervision without
SMTP settings" notice on a fresh install) becomes a terminating
NativeCommandError that kills the server before it binds its port. The
template must therefore relax the preference before invoking Python while
keeping strict mode for the setup/validation above it.
"""

import re
import unittest
from pathlib import Path

INSTALL_PS1 = Path(__file__).resolve().parent.parent / "scripts" / "install.ps1"


def _template() -> str:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    match = re.search(r"\$template = @'\n(.*?\n)'@", text, re.DOTALL)
    assert match, "wrapper template block not found in scripts/install.ps1"
    return match.group(1)


class WrapperStderrTests(unittest.TestCase):
    def test_stderr_must_not_abort_serve(self):
        template = _template()
        invoke = template.index("& $VenvPython @serveArgs")
        strict = template.index("$ErrorActionPreference = 'Stop'")
        relaxed = template.index("$ErrorActionPreference = 'Continue'")
        self.assertLess(strict, relaxed, "setup validation must stay strict")
        self.assertLess(relaxed, invoke, "stderr must be non-fatal before serve starts")


if __name__ == "__main__":
    unittest.main()
