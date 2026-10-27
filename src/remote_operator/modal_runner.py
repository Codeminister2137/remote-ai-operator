"""Optional Modal validation backend;never selected or connected implicitly."""

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
    """Submit only registered source/checks to a fresh network-blocked Sandbox."""

    runner_name = "modal_sandbox"

    def __init__(self, checks: Path, app, *, timeout=30, allow_remote_execution=False, sdk=None):
        super().__init__(checks, DEFAULT_IMAGE, timeout)
        self.app = app
        self.allow_remote_execution = allow_remote_execution
        self._sdk = sdk
        self._diagnostics = []

    @staticmethod
    def bootstrap():
        path = Path(__file__).with_name("sandbox_bootstrap.py")
        if path.is_symlink() or path.is_junction():
            raise ValueError("Linked sandbox supervisor refused")
        return path.read_text(encoding="utf-8")

    def policy_settings(self):
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
        if entry not in {"run_checks.py", "preview.py"}:
            raise ValueError("Unknown protected runner entry")
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
