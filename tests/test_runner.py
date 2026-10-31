"""Runner security configuration and fail-closed availability checks."""

import logging
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from remote_operator.control import digest
from remote_operator.runner import DockerValidator


class RunnerTests(unittest.TestCase):
    def test_command_contains_required_isolation_and_no_secret_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = DockerValidator(root).command(root, "owned-name")
            for option in (
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--user=65534:65534",
                "--memory=128m",
                "--pids-limit=64",
                "--pull=never",
            ):
                self.assertIn(option, command)
            self.assertFalse(any(item in command for item in ("-e", "--env", "--privileged")))
            self.assertEqual(sum("readonly" in item for item in command), 2)

    def test_cleanup_failure_retains_actual_diagnostic_and_test_output(self):
        import subprocess
        from unittest.mock import Mock

        def launch(command, **kwargs):
            kwargs["stdout"].write(b"original validation output\n")
            kwargs["stdout"].flush()
            return Mock(poll=Mock(return_value=0), wait=Mock(return_value=0))

        with tempfile.TemporaryDirectory() as directory:
            cleanup = subprocess.CompletedProcess([], 1, stdout=b"daemon cleanup detail")
            with (
                patch("remote_operator.runner.subprocess.Popen", side_effect=launch),
                patch("remote_operator.runner.subprocess.run", return_value=cleanup),
            ):
                validator = DockerValidator(Path(directory))
                with patch.object(validator, "policy_digest", return_value="fixture-policy"):
                    result = validator.validate(Path(directory), digest("x"))
        self.assertFalse(result.passed)
        self.assertIn("daemon cleanup detail", result.output)
        self.assertIn("original validation output", result.output)

    def test_missing_docker_never_passes_or_executes_source_on_host(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("remote_operator.runner.subprocess.Popen", side_effect=FileNotFoundError),
                patch("remote_operator.runner.subprocess.run", side_effect=FileNotFoundError),
            ):
                validator = DockerValidator(Path(directory))
                with patch.object(validator, "policy_digest", return_value="fixture-policy"):
                    result = validator.validate(Path(directory), digest("x"))
            self.assertFalse(result.passed)
            self.assertIsNone(result.exit_code)

    def test_runner_timeout_must_be_finite_positive_and_bounded(self):
        for value in (0, -1, float("inf"), float("nan"), True, 121):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DockerValidator(Path("."), timeout=value)

    def test_invalid_timeout_error_mentions_offending_value(self):
        # Regression: error message must include the specific invalid timeout value
        for value in (0, -1, 121):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as cm:
                    DockerValidator(Path("."), timeout=value)
                self.assertIn(str(value), str(cm.exception))
        # Check non-numeric edge case still mentions type/value
        with self.assertRaises(ValueError) as cm:
            DockerValidator(Path("."), timeout=float("nan"))
        self.assertIn("nan", str(cm.exception).lower())

    def test_deadline_policy_changes_invalidate_old_approval_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("run_checks.py", "test_import.py", "preview.py"):
                (root / name).write_text("# fixed policy", encoding="utf-8")
            self.assertNotEqual(
                DockerValidator(root, timeout=2).policy_digest(),
                DockerValidator(root, timeout=3).policy_digest(),
            )

    def test_sandbox_bootstrap_help_exits_zero_and_preserves_normal_behavior(self):
        # --help should print usage and exit 0 without requiring stdin JSON
        for flag in ("--help", "-h"):
            result = subprocess.run(
                [sys.executable, "-I", "-B", "src/remote_operator/sandbox_bootstrap.py", flag],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            combined = result.stdout.lower() + result.stderr.lower()
            self.assertIn("usage", combined)
        # Normal invocation without --help must still attempt JSON bootstrap path
        # (no help text, non-zero or JSON error, not a silent success)
        result = subprocess.run(
            [sys.executable, "-I", "-B", "src/remote_operator/sandbox_bootstrap.py"],
            input="",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Usage: sandbox_bootstrap.py", result.stdout)

    def test_public_api_docstrings_exist_and_behavior_unchanged(self):
        """Regression: docstring-only change preserves public API and behavior."""
        import inspect

        from remote_operator.runner import DockerValidator, Execution, SourceValidator

        # Public classes and methods remain exposed with docstrings
        for cls in (Execution, SourceValidator, DockerValidator):
            self.assertIsNotNone(cls.__doc__)
            self.assertGreater(len(cls.__doc__.strip()), 0)
        for name in ("policy_digest", "validate", "preview", "policy_settings", "command"):
            obj = getattr(SourceValidator, name, None) or getattr(DockerValidator, name, None)
            self.assertIsNotNone(obj)
            self.assertIsNotNone(obj.__doc__)
            self.assertGreater(len(obj.__doc__.strip()), 0)
        # Signatures unchanged
        self.assertEqual(
            inspect.signature(DockerValidator.command).parameters["entry"].default, "run_checks.py"
        )
        # Behavior unchanged: invalid entry still raises
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                DockerValidator(root).command(root, "n", entry="evil.py")

    def test_runner_start_emits_debug_log_without_changing_behavior(self):
        from unittest.mock import Mock

        def launch(command, **kwargs):
            kwargs["stdout"].write(b"original validation output\n")
            kwargs["stdout"].flush()
            return Mock(poll=Mock(return_value=0), wait=Mock(return_value=0))

        with tempfile.TemporaryDirectory() as directory:
            cleanup = subprocess.CompletedProcess([], 0, stdout=b"")
            with (
                patch("remote_operator.runner.subprocess.Popen", side_effect=launch),
                patch("remote_operator.runner.subprocess.run", return_value=cleanup),
            ):
                validator = DockerValidator(Path(directory))
                with patch.object(validator, "policy_digest", return_value="fixture-policy"):
                    with self.assertLogs("remote_operator.runner", level="DEBUG") as cm:
                        result = validator.validate(Path(directory), digest("x"))
                    self.assertTrue(any("runner starting" in m.lower() for m in cm.output))
                    # semantics preserved: still fails closed without matching report
                    self.assertFalse(result.passed)
                    self.assertIn("original validation output", result.output)

    def test_runner_stop_emits_debug_log_without_changing_behavior(self):
        from unittest.mock import Mock

        def launch(command, **kwargs):
            kwargs["stdout"].write(b"original validation output\n")
            kwargs["stdout"].flush()
            return Mock(poll=Mock(return_value=0), wait=Mock(return_value=0))

        with tempfile.TemporaryDirectory() as directory:
            cleanup = subprocess.CompletedProcess([], 0, stdout=b"")
            with (
                patch("remote_operator.runner.subprocess.Popen", side_effect=launch),
                patch("remote_operator.runner.subprocess.run", return_value=cleanup),
            ):
                validator = DockerValidator(Path(directory))
                with patch.object(validator, "policy_digest", return_value="fixture-policy"):
                    with self.assertLogs("remote_operator.runner", level=logging.DEBUG) as cm:
                        result = validator.validate(Path(directory), digest("x"))
                    self.assertTrue(any("runner stopped" in m.lower() for m in cm.output))
                    self.assertFalse(result.passed)
                    self.assertIn("original validation output", result.output)

    def test_sandbox_start_emits_debug_log_without_changing_behavior(self):
        """Regression: sandbox bootstrap emits exactly one debug log on start, semantics intact."""
        source = Path("src/remote_operator/sandbox_bootstrap.py").read_text(encoding="utf-8")
        # Exactly one debug log line
        debug_lines = [l for l in source.splitlines() if "logging.debug" in l and "sandbox starting" in l.lower()]
        self.assertEqual(len(debug_lines), 1, f"expected exactly one sandbox starting debug line, got {debug_lines}")
        self.assertEqual(source.count("logging.debug"), 1)
        # Isolated runner semantics, resource limits and path boundaries intact
        self.assertIn("RLIMIT_CPU", source)
        self.assertIn("RLIMIT_AS", source)
        self.assertIn("no-new-privileges", source)
        self.assertIn('"/workspace/issue_import.py"', source)
        self.assertIn('request["entry"]', source)
        # Help flag still works and normal entry still fails without valid stdin
        result_help = subprocess.run(
            [sys.executable, "-I", "-B", "src/remote_operator/sandbox_bootstrap.py", "--help"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result_help.returncode, 0)
        self.assertIn("usage", (result_help.stdout + result_help.stderr).lower())


class DaemonHealTests(unittest.TestCase):
    def test_healthy_daemon_never_launches_desktop(self):
        from unittest.mock import Mock

        from remote_operator import runner

        probe = subprocess.CompletedProcess([], 0, stdout=b"29.2.1")
        with (
            patch("remote_operator.runner.subprocess.run", return_value=probe) as run,
            patch("remote_operator.runner.subprocess.Popen") as popen,
        ):
            self.assertTrue(runner.ensure_docker_daemon(timeout=0.05))
        run.assert_called_once()
        popen.assert_not_called()
        self.assertIsInstance(Mock(), Mock)

    def test_down_daemon_starts_desktop_and_polls_until_ready(self):
        from unittest.mock import Mock

        from remote_operator import runner

        down = subprocess.CompletedProcess([], 1, stdout=b"Cannot connect")
        up = subprocess.CompletedProcess([], 0, stdout=b"29.2.1")
        with (
            patch("remote_operator.runner.subprocess.run", side_effect=[down, down, up]),
            patch("remote_operator.runner.subprocess.Popen") as popen,
            patch("remote_operator.runner.sys.platform", "win32"),
            patch("remote_operator.runner.time.sleep") as nap,
            patch("remote_operator.runner.time.monotonic", side_effect=[100.0, 101.0, 102.0]),
        ):
            self.assertTrue(runner.ensure_docker_daemon(timeout=60))
        popen.assert_called_once()
        self.assertEqual(popen.call_args[0][0], [runner.DOCKER_DESKTOP_WINDOWS])
        self.assertEqual(nap.call_count, 2)
        self.assertIsInstance(Mock(), Mock)

    def test_down_daemon_gives_up_after_timeout_without_hanging(self):
        from remote_operator import runner

        down = subprocess.CompletedProcess([], 1, stdout=b"Cannot connect")
        with (
            patch("remote_operator.runner.subprocess.run", return_value=down),
            patch("remote_operator.runner.subprocess.Popen"),
            patch("remote_operator.runner.sys.platform", "win32"),
            patch("remote_operator.runner.time.sleep"),
            patch("remote_operator.runner.time.monotonic", side_effect=[100.0, 200.0]),
        ):
            self.assertFalse(runner.ensure_docker_daemon(timeout=60))

    def test_non_windows_never_launches_desktop(self):
        from remote_operator import runner

        down = subprocess.CompletedProcess([], 1, stdout=b"Cannot connect")
        with (
            patch("remote_operator.runner.subprocess.run", return_value=down),
            patch("remote_operator.runner.subprocess.Popen") as popen,
            patch("remote_operator.runner.sys.platform", "linux"),
        ):
            self.assertFalse(runner.ensure_docker_daemon(timeout=0.05))
        popen.assert_not_called()

    def test_run_retries_once_after_successful_heal(self):
        from unittest.mock import Mock

        from remote_operator.runner import DockerValidator, Execution

        stale = Execution(1, "failed to connect to the docker API", "cleanup failed")
        healed = Execution(0, "OPERATOR_CHECKS {}", None)
        with tempfile.TemporaryDirectory() as directory:
            validator = DockerValidator(Path(directory))
            with (
                patch.object(DockerValidator, "_execute_once", side_effect=[stale, healed]) as once,
                patch("remote_operator.runner.ensure_docker_daemon", return_value=True),
            ):
                result = validator._run(Path(directory))
        self.assertEqual(once.call_count, 2)
        self.assertEqual(result, healed)
        self.assertIsInstance(Mock(), Mock)

    def test_run_reports_actionable_failure_when_heal_fails(self):
        from remote_operator.runner import DockerValidator, Execution

        stale = Execution(1, "failed to connect to the docker API", None)
        with tempfile.TemporaryDirectory() as directory:
            validator = DockerValidator(Path(directory))
            with (
                patch.object(DockerValidator, "_execute_once", return_value=stale) as once,
                patch("remote_operator.runner.ensure_docker_daemon", return_value=False),
            ):
                result = validator._run(Path(directory))
        self.assertEqual(once.call_count, 1)
        self.assertIn("could not be started automatically", result.failure)
        self.assertIn("failed to connect", result.output)

    def test_healthy_output_never_triggers_heal(self):
        from remote_operator.runner import DockerValidator, Execution

        fine = Execution(0, "OPERATOR_CHECKS {}", None)
        with tempfile.TemporaryDirectory() as directory:
            validator = DockerValidator(Path(directory))
            with (
                patch.object(DockerValidator, "_execute_once", return_value=fine) as once,
                patch("remote_operator.runner.ensure_docker_daemon") as heal,
            ):
                result = validator._run(Path(directory))
        self.assertEqual(once.call_count, 1)
        heal.assert_not_called()
        self.assertEqual(result, fine)


if __name__ == "__main__":
    unittest.main()
