"""Optional Modal validation backend;never selected or connected implicitly.

This module provides :class:`ModalValidator`, an optional remote validation
backend that executes only explicitly authorized, registered sources inside a
fresh network-blocked Modal Sandbox. It is never selected implicitly.
"""

import hashlib
import importlib
import json
import math
import time
import uuid
from pathlib import Path

from remote_operator.control import read_source
from remote_operator.runner import DEFAULT_IMAGE, Execution, SourceValidator


class ModalValidator(SourceValidator):
    """Submit only registered source/checks to a fresh network-blocked Sandbox.

        This validator extends :class:`SourceValidator` to run checks in an
    explicitly authorized, ephemeral Modal Sandbox with networking blocked.

        Security boundaries:
            * Only files registered via :meth:`request` (workspace issue source and
              known protected checks) are transferred to the sandbox.
            * The sandbox is created fresh per run with ``block_network=True``,
              no secrets/env/volumes forwarded, and capped CPU/memory.
            * Remote execution requires explicit opt-in via
              ``allow_remote_execution=True``; otherwise no SDK call is made.
            * Execution is bounded by timeout/deadline policies and bounded
              supervisor receipts; untrusted model/repository text is treated as
              data and validated before acceptance.
    """

    runner_name = "modal_sandbox"

    def __init__(self, checks: Path, app, *, timeout=30, allow_remote_execution=False, sdk=None):
        """Initialize a Modal-backed validator.

        Args:
            checks: Path to the directory containing registered check scripts
                (``run_checks.py``, ``test_import.py``, ``preview.py``).
            app: Modal App identifier used when creating the Sandbox.
            timeout: Maximum seconds allowed for the supervisor/child run
                inside the sandbox (host wrapper adds a small grace).
            allow_remote_execution: Must be explicitly ``True`` to permit any
                remote SDK call. When ``False`` (default) runs return a
                failure without contacting Modal, enforcing explicit
                authorization.
            sdk: Optional injected Modal SDK double (e.g., for tests). When
                ``None`` the real ``modal`` package is imported lazily.

        Returns:
            None. Initializes base :class:`SourceValidator` state, the target
            app handle, authorization flag, optional SDK override, and an
            internal diagnostics buffer.

        Security boundaries:
            Does not perform network I/O; stores only the authorization flag
            and local paths. Actual sandbox creation is deferred to
            :meth:`_run` and gated on explicit authorization.
        """
        super().__init__(checks, DEFAULT_IMAGE, timeout)
        self.app = app
        self.allow_remote_execution = allow_remote_execution
        self._sdk = sdk
        self._diagnostics = []

    @staticmethod
    def bootstrap():
        """Return the trusted sandbox supervisor source text.

        Reads ``sandbox_bootstrap.py`` located alongside this module, which
        defines the in-sandbox supervisor that enforces timeouts, output
        caps, file immutability, and receipt formatting.

        Args:
            None.

        Returns:
            str: UTF-8 source of the supervisor bootstrap script.

        Security boundaries:
            Refuses to read through symlinks/junctions to avoid linked
            supervisor substitution. The returned text is hashed into
            :meth:`policy_settings` so supervisor changes invalidate prior
            approvals. No network access is performed.
        """
        path = Path(__file__).with_name("sandbox_bootstrap.py")
        if path.is_symlink() or path.is_junction():
            raise ValueError("Linked sandbox supervisor refused")
        return path.read_text(encoding="utf-8")

    def policy_settings(self):
        """Return policy settings describing the Modal sandbox configuration.

        Extends the base image/timeout policy with Modal-specific guards used
        for approval binding and audit.

        Args:
            None.

        Returns:
            dict: Mapping containing ``backend``, ``image``, ``timeout_seconds``,
            ``deadline_policy``, ``bootstrap_sha256`` (hash of
            :meth:`bootstrap`), ``network`` (``"blocked"``), CPU/memory
            limits, uid, and output byte limits.

        Security boundaries:
            Exposes the supervisor hash and network-blocked, resource-capped
            intent so any change to the supervisor or sandbox limits
            invalidates prior approvals. Does not transmit secrets or enable
            network egress.
        """
        return {
            "backend": self.runner_name,
            "image": self.image,
            "timeout_seconds": self.timeout,
            "deadline_policy": "modal-supervisor-v1",
            "bootstrap_sha256": hashlib.sha256(self.bootstrap().encode()).hexdigest(),
            "network": "blocked",
            "cpu": [0.25, 1],
            "memory_mib": [128, 256],
            "uid": 65534,
            "output_bytes": 65_536,
        }

    def request(self, workspace, entry, payload):
        """Build a bounded request payload for the network-blocked sandbox.

        Collects only explicitly registered files: the workspace
        ``issue_import.py`` and the protected checks from ``self.checks``.
        Validates entry name and file sizes before serialization.

        Args:
            workspace: Path to the workspace directory containing
                ``issue_import.py`` to be validated.
            entry: Protected runner entry name; must be one of
                ``"run_checks.py"`` or ``"preview.py"``.
            payload: Bytes payload forwarded to the supervisor (JSON-encoded
                by caller); decoded as UTF-8 for inclusion in the request.

        Returns:
            bytes: JSON-encoded request containing ``files``, ``entry``,
            ``payload`` (as string), and ``timeout`` suitable for writing to
            the sandbox stdin.

        Security boundaries:
            * Enforces allowlist of entries; unknown entries are rejected.
            * Transfers only registered files; no arbitrary host paths are
              included. Each protected check is checked for symlink/junction
              and size cap (65_536 bytes).
            * Enforces overall request size cap (524_288 bytes) to prevent
              unbounded upload. No network access beyond preparing bytes.
        """
        if entry not in {"run_checks.py", "preview.py"}:
            raise ValueError(f"Unknown protected runner entry: {entry} (operation: request)")
        files = {"/workspace/issue_import.py": read_source(workspace)}
        for name in ("run_checks.py", "test_import.py", "preview.py"):
            path = self.checks / name
            if path.is_symlink() or path.is_junction() or path.stat().st_size > 65_536:
                raise ValueError("Invalid protected check input")
            files["/checks/" + name] = path.read_text(encoding="utf-8")
        request = json.dumps(
            {
                "files": files,
                "entry": entry,
                "payload": payload.decode("utf-8"),
                "timeout": self.timeout,
            }
        ).encode()
        if len(request) > 524_288:
            raise ValueError("Sandbox input allowance exceeded")
        return request

    def _run(self, workspace, *, entry="run_checks.py", payload=b"", cancelled=None):
        """Execute registered checks in a fresh network-blocked Modal Sandbox.

        Creates an ephemeral sandbox with networking blocked and no secrets
        forwarded, streams the bounded request from :meth:`request`, waits
        for the trusted supervisor receipt, validates it, and ensures cleanup.

        Args:
            workspace: Path to workspace containing the candidate source.
            entry: Protected entry script to run inside the sandbox
                (default ``"run_checks.py"``).
            payload: Optional bytes payload forwarded to the entry via the
                supervisor.
            cancelled: Optional callable returning truthy when cancellation
                is requested; checked before launch and during wait.

        Returns:
            Execution: Result with ``code``, ``output``, and ``failure``
            fields. Failures include authorization missing, cancellation,
            deadline, invalid receipt, supervisor non-zero exit, or SDK
            errors (reported by phase without leaking raw SDK messages).

        Security boundaries:
            * Requires ``allow_remote_execution is True`` (explicit
              authorization); otherwise returns immediately without SDK use.
            * Only registered files via :meth:`request` are sent; network is
              blocked (``block_network=True``), env/secrets/volumes empty,
              and OIDC/ports disabled.
            * Supervisor output is treated as untrusted: size-capped,
              JSON-validated (exact keys/types), and output-capped before
              acceptance. Child output is bounded by the supervisor.
            * Diagnostics are isolated; user-visible errors expose only phase
              and exception type, not raw SDK messages that may contain
              account data. Sandbox is always terminated and verified.
        """
        if self.allow_remote_execution is not True:
            return Execution(
                None, "", "Modal execution requires explicit finite compute authorization"
            )
        if cancelled and cancelled():
            return Execution(None, "", "Runner cancelled before launch")
        self._diagnostics.clear()
        request = self.request(workspace, entry, payload)
        sandbox = None
        result = Execution(None, "", "Sandbox execution did not complete")
        phase = "load optional SDK"
        try:
            sdk = self._sdk or importlib.import_module("modal")
            phase = "prepare pinned image"
            image = sdk.Image.from_registry(self.image)
            phase = "create Sandbox"
            sandbox = sdk.Sandbox.create(
                "python",
                "-I",
                "-B",
                "-c",
                self.bootstrap(),
                app=self.app,
                name="remote-operator-" + uuid.uuid4().hex,
                image=image,
                timeout=math.ceil(self.timeout) + 10,
                cpu=(0.25, 1),
                memory=(128, 256),
                block_network=True,
                env={},
                secrets=[],
                volumes={},
                include_oidc_identity_token=False,
                encrypted_ports=[],
                unencrypted_ports=[],
                h2_ports=[],
                verbose=False,
            )
            phase = "send registered inputs"
            sandbox.stdin.write(request)
            sandbox.stdin.write_eof()
            sandbox.stdin.drain()
            deadline = time.monotonic() + self.timeout + 10
            phase = "wait for supervisor"
            while sandbox.poll() is None:
                if cancelled and cancelled():
                    result = Execution(None, "", "Runner cancelled")
                    break
                if time.monotonic() >= deadline:
                    result = Execution(None, "", "Sandbox host deadline reached")
                    break
                time.sleep(0.05)
            else:
                phase = "read bounded supervisor receipt"
                # The trusted root supervisor alone writes this stream. Generated
                # child output is captured and capped before serializing the receipt.
                raw = sandbox.stdout.read()
                if len(raw.encode()) > 524_288:
                    raise ValueError("Supervisor receipt allowance exceeded")
                report = json.loads(raw)
                if (
                    set(report) != {"code", "output", "failure"}
                    or type(report["code"]) is not int
                    or not isinstance(report["output"], str)
                    or len(report["output"].encode()) > 196_608
                    or (
                        report["failure"] is not None
                        and (
                            not isinstance(report["failure"], str) or len(report["failure"]) > 1000
                        )
                    )
                ):
                    raise ValueError("Invalid supervisor receipt")
                if sandbox.poll() != 0:
                    raise ValueError("Sandbox supervisor failed")
                result = Execution(report["code"], report["output"], report["failure"])
        except Exception as exc:  # noqa: BLE001 -- isolate optional SDK failures;never report success
            self._diagnostics.append((phase, exc))
            # SDK diagnostics may contain account/configuration data. Report the
            # exact failing phase/type without returning raw SDK messages to users.
            result = Execution(
                None, "", f"Modal runner failed during {phase} ({type(exc).__name__})"
            )
        finally:
            if sandbox is not None:
                try:
                    sandbox.terminate(wait=True)
                    if sandbox.poll() is None:
                        raise ValueError("Sandbox still running after termination")
                except Exception as exc:  # noqa: BLE001 -- isolate optional SDK failures;never report success
                    self._diagnostics.append(("terminate Sandbox", exc))
                    result = Execution(
                        result.code,
                        result.output,
                        (result.failure + "; " if result.failure else "")
                        + f"Sandbox cleanup could not be verified ({type(exc).__name__})",
                    )
        return result
