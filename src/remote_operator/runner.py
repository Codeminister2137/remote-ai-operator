"""Disposable Docker execution for validation and the resulting sample feature."""

import hashlib
import json
import logging
import math
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from remote_operator.control import Validation, digest, read_source

logger = logging.getLogger(__name__)

DEFAULT_IMAGE = (
    "python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704"
)


@dataclass(frozen=True)
class Execution:
    """Result of an isolated runner execution."""

    code: int | None
    output: str
    failure: str | None


DOCKER_DESKTOP_WINDOWS = "C:/Program Files/Docker/Docker/Docker Desktop.exe"
DAEMON_DOWN_MARKERS = (
    "failed to connect to the docker api",
    "cannot connect to the docker daemon",
    "error during connect",
    "dockerdesktoplinuxengine",
)
DAEMON_START_TIMEOUT = 180.0


def daemon_ready() -> bool:
    """Check whether the Docker daemon answers without starting anything."""
    try:
        probe = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def ensure_docker_daemon(timeout: float = DAEMON_START_TIMEOUT) -> bool:
    """Start Docker Desktop on Windows when the daemon is down, then wait.

    Keeps phone-driven validation working after boot, sleep/wake, or a quit
    Docker Desktop without owner intervention. Returns True once the daemon
    answers, False when it cannot be started (non-Windows, missing binary,
    or still down after the timeout).
    """
    if daemon_ready():
        return True
    if sys.platform != "win32":
        return False
    try:
        subprocess.Popen(
            [DOCKER_DESKTOP_WINDOWS],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        return False
    logger.info("docker daemon down; started Docker Desktop, waiting up to %.0fs", timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(5)
        if daemon_ready():
            return True
    return False


def daemon_down_failure(text: str) -> str | None:
    """Actionable failure when CLI output shows an unreachable daemon."""
    lowered = text.lower()
    if any(marker in lowered for marker in DAEMON_DOWN_MARKERS):
        return (
            "Docker daemon unreachable during validation. Start Docker Desktop "
            "on the PC (it auto-starts at sign-in), then run fresh validation."
        )
    return None


class SourceValidator:
    """Shared exact-source evidence checks for explicit isolated runner backends."""

    runner_name = "unconfigured"

    def __init__(self, checks: Path, image: str, timeout: float):
        """Initialize a source validator with policy checks and isolation settings.

        Args:
            checks: Directory containing validation policy scripts.
            image: Container image reference used by concrete backends.
            timeout: Execution deadline in seconds; must be finite, positive, and at most 120.
        """
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not 0 < timeout <= 120
        ):
            raise ValueError(
                f"Runner timeout must be finite,positive and at most120 seconds: {timeout!r}"
            )
        self.checks = checks.resolve()
        self.image = image
        self.timeout = float(timeout)

    @staticmethod
    def _report(output, marker):
        reports = [line[len(marker) :] for line in output.splitlines() if line.startswith(marker)]
        try:
            return json.loads(reports[0]) if len(reports) == 1 else None
        except ValueError:
            return None

    def policy_digest(self):
        """Compute a fingerprint of the current validation policy."""
        fingerprint = hashlib.sha256(json.dumps(self.policy_settings(), sort_keys=True).encode())
        for name in ("run_checks.py", "test_import.py", "preview.py"):
            path = self.checks / name
            if path.is_symlink() or path.is_junction():
                raise ValueError("Linked validation policy refused")
            payload = path.read_bytes()
            fingerprint.update(name.encode() + b"\0" + str(len(payload)).encode() + b"\0" + payload)
        return fingerprint.hexdigest()

    def validate(self, workspace: Path, candidate_digest: str, *, cancelled=None) -> Validation:
        """Run isolated checks and return a validation result.

        Args:
            workspace: Candidate workspace directory to validate.
            candidate_digest: Expected digest of the candidate source.
            cancelled: Optional callable returning True if the run should be cancelled.

        Returns:
            A Validation indicating whether the candidate passed isolated checks.
        """
        policy = self.policy_digest()
        result = self._run(workspace, cancelled=cancelled)
        report = self._report(result.output, "OPERATOR_CHECKS ")
        passed = (
            result.failure is None
            and result.code == 0
            and report == {"tests": 7, "failures": 0, "errors": 0, "skipped": 0}
            and self.policy_digest() == policy
            and digest(read_source(workspace)) == candidate_digest
        )
        output = result.output if result.failure is None else result.failure + "\n" + result.output
        return Validation(passed, candidate_digest, result.code, output, self.runner_name, policy)

    def preview(self, workspace, csv_text, expected_digest):
        """Generate a preview from CSV text inside the isolated runner.

        Args:
            workspace: Workspace whose source digest must match expected_digest.
            csv_text: CSV content to preview, up to 8192 bytes.
            expected_digest: Digest the workspace must currently have.

        Returns:
            Dictionary with issues, errors, existing items, and target_digest.
        """
        if not isinstance(csv_text, str) or len(csv_text.encode()) > 8192:
            raise ValueError("Sample CSV must be at most 8192 bytes")
        if digest(read_source(workspace)) != expected_digest:
            raise ValueError("Applied target changed; sample preview refused")
        existing = [{"id": "existing", "title": "Original issue", "status": "open"}]
        payload = json.dumps({"csv_text": csv_text, "existing": existing}).encode()
        result = self._run(workspace, entry="preview.py", payload=payload)
        report = self._report(result.output, "OPERATOR_PREVIEW ")
        if result.failure or result.code != 0 or not isinstance(report, dict):
            raise ValueError("Isolated preview failed: " + (result.failure or result.output)[:1000])
        if set(report) != {"issues", "errors"} or any(
            not isinstance(report[k], list) or len(report[k]) > 500 for k in report
        ):
            raise ValueError("Invalid sample result")
        for issue in report["issues"]:
            if (
                not isinstance(issue, dict)
                or set(issue) != {"id", "title", "status"}
                or any(not isinstance(v, str) or len(v) > 8192 for v in issue.values())
                or issue["status"] not in {"open", "closed"}
            ):
                raise ValueError("Invalid sample issue")
        for error in report["errors"]:
            if (
                not isinstance(error, dict)
                or set(error) != {"row", "message"}
                or type(error["row"]) is not int
                or error["row"] < 2
                or not isinstance(error["message"], str)
                or len(error["message"]) > 1000
            ):
                raise ValueError("Invalid sample error")
        if digest(read_source(workspace)) != expected_digest:
            raise ValueError("Applied target changed during preview")
        return {**report, "existing": existing, "target_digest": expected_digest}

    def policy_settings(self):
        """Return settings that contribute to the policy digest."""
        raise NotImplementedError

    def _run(self, workspace, *, entry="run_checks.py", payload=b"", cancelled=None):
        raise NotImplementedError


class DockerValidator(SourceValidator):
    """Execute protected entry points with the existing local Docker isolation."""

    runner_name = "docker"

    def __init__(self, checks: Path, image: str = DEFAULT_IMAGE, timeout: float = 30):
        """Initialize a Docker-backed validator.

        Args:
            checks: Directory containing validation policy scripts.
            image: Docker image reference to run.
            timeout: Execution deadline in seconds.
        """
        super().__init__(checks, image, timeout)

    def policy_settings(self):
        """Return Docker-specific settings included in the policy digest."""
        # Preserve existing Docker evidence fingerprints across this extraction.
        return {
            "image": self.image,
            "timeout_seconds": self.timeout,
            "deadline_policy": "container-kill-v1",
        }

    def command(self, workspace: Path, name: str, *, entry="run_checks.py", interactive=False):
        """Build the docker run command for an isolated execution.

        Args:
            workspace: Workspace directory to mount read-only.
            name: Container name to use.
            entry: Protected entry script to execute in /checks.
            interactive: Whether to include interactive stdin handling.

        Returns:
            List of command arguments for subprocess execution.
        """
        if entry not in {"run_checks.py", "preview.py"}:
            raise ValueError("Unknown protected runner entry")
        for path in (workspace.resolve(), self.checks):
            if "," in str(path):
                raise ValueError("Docker mount paths cannot contain commas")
        return [
            "docker",
            "run",
            "--name",
            name,
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=64",
            "--memory=128m",
            "--cpus=1",
            "--user=65534:65534",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
            "--mount",
            f"type=bind,src={workspace.resolve()},dst=/workspace,readonly",
            "--mount",
            f"type=bind,src={self.checks},dst=/checks,readonly",
            "--workdir=/workspace",
            *(["--interactive"] if interactive else []),
            self.image,
            "timeout",
            "--signal=KILL",
            f"{self.timeout}s",
            "python",
            "-I",
            "-B",
            "/checks/" + entry,
        ]

    def _execute_once(self, workspace, *, entry="run_checks.py", payload=b"", cancelled=None):
        logger.debug("runner starting entry=%s", entry)
        name = "remote-operator-" + uuid.uuid4().hex
        failure, code, process = None, None, None
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as input_file:
            input_file.write(payload)
            input_file.seek(0)
            try:
                if cancelled and cancelled():
                    return Execution(None, "", "Runner cancelled before launch")
                process = subprocess.Popen(
                    self.command(workspace, name, entry=entry, interactive=bool(payload)),
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    stdin=input_file if payload else subprocess.DEVNULL,
                )
                deadline = time.monotonic() + self.timeout
                while process.poll() is None:
                    if cancelled and cancelled():
                        failure = "Runner cancelled"
                        break
                    if time.monotonic() >= deadline or os.fstat(output.fileno()).st_size > 65_536:
                        failure = "Runner deadline/output allowance reached"
                        break
                    time.sleep(0.05)
                if failure:
                    process.kill()
                code = process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as exc:
                failure = f"Runner unavailable ({type(exc).__name__}): {str(exc)[:1000]}"
                if process and process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            finally:
                try:
                    cleanup = subprocess.run(
                        ["docker", "rm", "--force", name],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        timeout=10,
                        check=False,
                    )
                    if cleanup.returncode and b"No such container" not in cleanup.stdout:
                        failure = (
                            ((failure + "; ") if failure else "")
                            + "Container cleanup could not be verified: "
                            + cleanup.stdout.decode("utf-8", errors="replace")[:1000]
                        )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    failure = (
                        (failure + "; ") if failure else ""
                    ) + f"Container cleanup could not be verified: {type(exc).__name__}"
            if os.fstat(output.fileno()).st_size > 65_536:
                failure = ((failure + "; ") if failure else "") + "Runner output allowance reached"
            output.seek(0)
            text = output.read(65_536).decode("utf-8", errors="replace")
        if code and not failure:
            failure = daemon_down_failure(text)
        logger.debug("runner stopped entry=%s code=%s failure=%s", entry, code, failure)
        return Execution(code, text, failure)

    def _run(self, workspace, *, entry="run_checks.py", payload=b"", cancelled=None):
        """Run once; heal a down daemon and retry once on daemon-down evidence."""
        first = self._execute_once(workspace, entry=entry, payload=payload, cancelled=cancelled)
        stale = daemon_down_failure((first.output or "") + "\n" + (first.failure or ""))
        if not stale or (cancelled and cancelled()):
            return first
        if not ensure_docker_daemon():
            return Execution(
                first.code,
                first.output,
                "Docker Desktop is not running and could not be started "
                "automatically. Start Docker Desktop on the PC, then run again.",
            )
        return self._execute_once(workspace, entry=entry, payload=payload, cancelled=cancelled)
