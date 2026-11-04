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

    def test_typo_must_not_wipe_previous_work_roots(self):
        text = INSTALL_PS1.read_text(encoding="utf-8")
        self.assertIn("$validRoots = $existingRoots", text)
        self.assertIn("kept the previous code folder", text)

    def test_prompt_requires_existing_folder(self):
        text = INSTALL_PS1.read_text(encoding="utf-8")
        self.assertIn("The folder must already exist; the installer never creates it.", text)

    def test_summary_recaps_effective_code_folder(self):
        text = INSTALL_PS1.read_text(encoding="utf-8")
        self.assertIn("CODE FOLDER:", text)
        self.assertIn("kept your previous folder because the new answer matched nothing", text)
        self.assertIn("CODE FOLDER: none (sample task only).", text)

    def test_ffmpeg_detection_refreshes_path_and_tolerates_reinstall(self):
        text = INSTALL_PS1.read_text(encoding="utf-8")
        self.assertIn("function Find-FfmpegCommand", text)
        self.assertIn("if ($null -eq (Find-FfmpegCommand)) {", text)
        self.assertIn("ffmpeg was already installed; using it.", text)

    def test_model_key_prompt_saves_without_printing(self):
        text = INSTALL_PS1.read_text(encoding="utf-8")
        self.assertIn("Meta API key? [Enter keeps the saved key / skips]", text)
        self.assertIn("Set-DotEnvValue $envPath 'META_API_KEY' $keyAnswer", text)
        self.assertIn("never printed or shared", text)
        self.assertIn("MODEL KEY: saved (used after the server restarts).", text)
        self.assertIn("MODEL KEY: none (Meta features need one).", text)


if __name__ == "__main__":
    unittest.main()
