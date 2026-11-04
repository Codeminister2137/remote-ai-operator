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
import shutil
import subprocess
import tempfile
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
        self.assertIn(
            "MODEL KEY: saved (used only when the serve is explicitly started in Meta mode).",
            text,
        )
        self.assertIn(
            "MODEL KEY: none (Meta mode needs a key plus an explicit Meta-mode serve).", text
        )

    def test_model_key_prompt_does_not_promise_activation_on_restart(self):
        text = INSTALL_PS1.read_text(encoding="utf-8")
        self.assertNotIn("used after the server restarts", text)
        self.assertIn("Saving the key does not switch modes by itself", text)

    def test_health_wait_names_its_actual_timeout(self):
        text = INSTALL_PS1.read_text(encoding="utf-8")
        self.assertIn("up to 15 seconds", text)
        self.assertIn("did not answer within 15 seconds", text)
        self.assertNotIn("30 seconds", text)

    def test_wrapper_paths_survive_embedded_quotes(self):
        text = INSTALL_PS1.read_text(encoding="utf-8")
        self.assertIn(".Replace('__REPO__', $Root.Replace(\"'\", \"''\"))", text)
        self.assertIn(".Replace('__VENVPYTHON__', $VenvPython.Replace(\"'\", \"''\"))", text)

    def test_changed_work_roots_restart_the_serve(self):
        text = INSTALL_PS1.read_text(encoding="utf-8")
        self.assertIn("$rootsChanged = (($validRoots -join ';')", text)
        self.assertIn("-or $rootsChanged", text)

    def test_model_key_prompt_does_not_echo(self):
        # A plain Read-Host echoes the typed key to the console; the secure
        # prompt keeps it out of scrollback and screen shares.
        text = INSTALL_PS1.read_text(encoding="utf-8")
        self.assertIn("-AsSecureString", text)
        self.assertIn("SecureStringToBSTR", text)
        self.assertIn("ZeroFreeBSTR", text)
        self.assertNotIn("(Read-Host 'Meta API key?", text)


class WrapperQuotingTests(unittest.TestCase):
    """Quoted install paths must stay inert string content in the wrapper."""

    PROBE = r"""
$ErrorActionPreference = 'Stop'
$installPath = $args[0]
$work = $args[1]
$text = [System.IO.File]::ReadAllText($installPath)
$start = $text.IndexOf('function Write-StartWrapper')
$end = $text.IndexOf('function Register-ServeTask')
if ($start -lt 0 -or $end -le $start) { throw 'Write-StartWrapper not found' }
Invoke-Expression ($text.Substring($start, $end - $start))
function Assert-Parses($path) {
  $errors = $null
  [void][System.Management.Automation.PSParser]::Tokenize(
    [System.IO.File]::ReadAllText($path), [ref]$errors)
  if ($errors.Count -ne 0) { throw "wrapper has $($errors.Count) parse errors" }
}
$root = Join-Path $work "o'brien"
$result = Write-StartWrapper $root 8765 (Join-Path $root '.venv\Scripts\python.exe')
Assert-Parses $result.Path
$generated = [System.IO.File]::ReadAllText($result.Path)
$wanted = '$RepoRoot = ''' + $root.Replace("'", "''") + ''''
if ($generated -notmatch [regex]::Escape($wanted)) { throw 'RepoRoot line not safely quoted' }
$evil = Join-Path $work "x'; Write-Host PWNED; #"
$result2 = Write-StartWrapper $evil 8765 (Join-Path $work 'python.exe')
Assert-Parses $result2.Path
$gen2 = [System.IO.File]::ReadAllText($result2.Path)
if ($gen2 -notmatch "x'';") { throw 'quote not escaped; statement could inject' }
Write-Host 'QUOTE-OK'
"""

    def test_quoted_paths_stay_inside_wrapper_strings(self):
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if shell is None:
            self.skipTest("powershell unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "quote_probe.ps1"
            probe.write_text(self.PROBE, encoding="utf-8")
            proc = subprocess.run(
                [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(probe)]
                + [str(INSTALL_PS1), tmp],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        output = (proc.stdout + proc.stderr).strip()
        self.assertEqual(proc.returncode, 0, "quote probe failed:\n" + output[-2000:])
        self.assertIn("QUOTE-OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
