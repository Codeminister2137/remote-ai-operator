"""Runner security configuration and fail-closed availability checks."""

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


if __name__ == "__main__":
    unittest.main()
