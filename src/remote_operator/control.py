"""Bounded task execution and approvals tied to inspected bytes and evidence."""

import difflib
import hashlib
import json
import os
import secrets
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from remote_operator.locking import ProcessLock
from remote_operator.store import Store

SOURCE = "issue_import.py"
MAX_SOURCE_BYTES = 32_768


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def safe_file(root: Path) -> Path:
    """Reject links/junctions at the registered workspace and controlled source."""
    for path in (root, root / SOURCE):
        if path.is_symlink() or path.is_junction():
            raise ValueError("Linked workspace/source is forbidden")
    return root / SOURCE


def read_source(root: Path) -> str:
    path = safe_file(root)
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Source exceeds allowed size")
    return path.read_bytes().decode("utf-8")


def write_source(root: Path, source: str) -> None:
    if not isinstance(source, str) or len(source.encode()) > MAX_SOURCE_BYTES:
        raise ValueError("Invalid source size/type")
    destination = safe_file(root)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(source.encode())
        safe_file(root)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class Validation:
    """Host observation of one candidate, separate from model narration."""

    passed: bool
    candidate_digest: str
    exit_code: int | None
    output: str
    runner: str
    checks_digest: str | None = None


class Validator(Protocol):
    def validate(self, workspace: Path, candidate_digest: str, *, cancelled=None) -> Validation: ...


@dataclass(frozen=True)
class Operation:
    name: str
    arguments: dict
    call_id: str | None = None
    summary: str = ""


class Model(Protocol):
    name: str

    def next_operation(self, history: list[dict], *, prompt: str) -> Operation: ...


class TransportFailure(Exception):
    """A failed inference boundary, not a failed repository mutation."""


class RunCancelled(Exception):
    """The owner requested stopping new task effects."""


class Operator:
    """One active controller with explicit reconciliation after interrupted effects."""

    def __init__(self, root: Path, seed: Path, validator: Validator, *, durable=None):
        """``durable`` runs after each committed write; hosted single-writer volumes pass commit here."""
        self.root = root.resolve()
        self.seed = seed
        self.validator = validator
        self.store = Store(self.root / "tasks.sqlite3", after_commit=durable)
        self.lock = ProcessLock(self.root / "operator.lock")

    def submit(self, prompt: str) -> dict:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            raise ValueError("Task requires 1-4000 characters")
        with self.lock:
            task_id = uuid.uuid4().hex
            folder = self.root / task_id
            workspace, target = folder / "candidate", folder / "target"
            workspace.mkdir(parents=True)
            target.mkdir()
            source = self.seed.read_text(encoding="utf-8")
            write_source(workspace, source)
            write_source(target, source)
            task = {
                "id": task_id,
                "prompt": prompt,
                "status": "queued",
                "base_digest": digest(source),
                "base_source": source,
                "history": [],
                "pending": None,
                "evidence": None,
                "approval": None,
                "injected": False,
                "calls": 0,
                "applied_digest": None,
            }
            self.store.save(task, "submitted", {"mode": "not_started"})
            return task

    def folder(self, task_id: str, kind: str) -> Path:
        # Never interpolate untrusted IDs into paths before looking up the task.
        task = self.store.get(task_id)
        if task["id"] != task_id or not task_id.isalnum() or len(task_id) != 32:
            raise ValueError("Invalid task identifier")
        return self.root / task_id / kind

    def run(self, task_id: str, model: Model, *, inject_failure: bool = False) -> dict:
        if not self.lock.acquire(blocking=False):
            raise ValueError("Another operator action is active")
        try:
            task = self.store.get(task_id)
            if task["status"] not in {"queued", "paused"}:
                raise ValueError("Task is not ready; reconcile uncertain effects first")
            if task["pending"]:
                raise ValueError("Uncertain operation requires reconciliation")
            task["status"] = "running"
            self.store.save(task, "started", {"model": model.name})
            deadline = time.monotonic() + 120
            while task["calls"] < 12:
                self._check_control(task_id, deadline)
                task["calls"] += 1
                self.store.save(task, "inference_requested", {"model": model.name})
                synthetic_failure = False
                try:
                    if (
                        inject_failure
                        and not task["injected"]
                        and task["history"]
                        and task["history"][-1]["name"] == "write_file"
                    ):
                        task["injected"] = True
                        synthetic_failure = True
                        raise TransportFailure("Injected transport failure; no request sent")
                    try:
                        operation = model.next_operation(task["history"], prompt=task["prompt"])
                    finally:
                        receipt = getattr(model, "last_receipt", None)
                        if receipt is not None:
                            self.store.save(task, "inference_receipt", receipt)
                except TransportFailure as exc:
                    task["status"] = "paused"
                    self.store.save(
                        task,
                        "transport_failed",
                        {"injected": synthetic_failure, "detail": str(exc)[:1000]},
                    )
                    # Only the deliberate one-shot injection recovers automatically here.
                    self._check_control(task_id, deadline)
                    if not synthetic_failure:
                        return task
                    task["status"] = "running"
                    self.store.save(
                        task,
                        "recovered",
                        {"method": "same_route", "completed_tools": len(task["history"])},
                    )
                    inject_failure = False
                    continue
                self._check_control(task_id, deadline)
                if operation.summary:
                    self.store.save(task, "model_summary", {"text": operation.summary[:2000]})
                if operation.name == "finish" and not operation.arguments:
                    self._prepare_approval(task)
                    return task
                task["pending"] = {"name": operation.name, "arguments": operation.arguments}
                self.store.save(task, "tool_started", {"name": operation.name})
                result = self._execute(task, operation, deadline=deadline)
                task["history"].append(
                    {
                        "name": operation.name,
                        "arguments": operation.arguments,
                        "call_id": operation.call_id,
                        "result": result,
                    }
                )
                task["pending"] = None
                self.store.save(task, "tool_completed", {"name": operation.name, "result": result})
            raise TimeoutError("Model call allowance exhausted")
        except RunCancelled:
            task["status"] = "cancelled"
            task["approval"] = None
            if (task.get("pending") or {}).get("name") in {"validate", "read_file"}:
                task["pending"] = None
            self.store.save(task, "cancelled", {"effects_preserved": True})
            return task
        except (Exception, KeyboardInterrupt):
            if "task" in locals() and task["status"] == "running":
                task["status"] = "reconciliation_required" if task["pending"] else "paused"
                self.store.save(task, "execution_stopped")
            raise
        finally:
            self.lock.release()

    def _check_control(self, task_id, deadline):
        if self.store.cancelled(task_id):
            raise RunCancelled()
        if time.monotonic() >= deadline:
            raise TimeoutError("Task deadline reached; late output cannot authorize effects")

    def _execute(self, task: dict, operation: Operation, *, deadline=float("inf")):
        workspace = self.folder(task["id"], "candidate")
        if operation.name in {"read_file", "write_file"}:
            expected = {"path"} if operation.name == "read_file" else {"path", "content"}
            if set(operation.arguments) != expected or operation.arguments["path"] != SOURCE:
                raise ValueError("Only the registered sample source is accessible")
            if operation.name == "read_file":
                return {"content": read_source(workspace)}
            write_source(workspace, operation.arguments["content"])
            task["evidence"] = None
            return {"digest": digest(read_source(workspace))}
        if operation.name == "validate" and not operation.arguments:
            before = digest(read_source(workspace))
            evidence = self.validator.validate(
                workspace,
                before,
                cancelled=lambda: self.store.cancelled(task["id"]) or time.monotonic() >= deadline,
            )
            if evidence.candidate_digest != before or digest(read_source(workspace)) != before:
                raise ValueError("Candidate changed during validation")
            task["evidence"] = asdict(evidence)
            self._check_control(task["id"], deadline)
            return task["evidence"]
        raise ValueError("Unknown or invalid tool")

    def _prepare_approval(self, task: dict) -> None:
        candidate = digest(read_source(self.folder(task["id"], "candidate")))
        evidence = task["evidence"]
        if not evidence or not evidence["passed"] or evidence["candidate_digest"] != candidate:
            raise ValueError("Fresh passing validation is required; " + json.dumps(evidence)[:1800])
        if candidate == task["base_digest"]:
            raise ValueError("No candidate change")
        policy = getattr(self.validator, "policy_digest", lambda: None)()
        if evidence.get("checks_digest") != policy:
            raise ValueError("Validation policy changed; fresh validation required")
        task["approval"] = {
            "nonce": secrets.token_urlsafe(24),
            "expires": time.time() + 600,
            "candidate_digest": candidate,
            "checks_digest": policy,
            "evidence_digest": digest(json.dumps(evidence, sort_keys=True)),
        }
        task["status"] = "awaiting_approval"
        self.store.save(task, "approval_requested", {"candidate_digest": candidate})

    def approve(self, task_id: str, nonce: str, *, now: float | None = None) -> dict:
        with self.lock:
            task = self.store.get(task_id)
            approval = task["approval"]
            if task["status"] != "awaiting_approval" or not approval:
                raise ValueError("No pending approval")
            if (
                not secrets.compare_digest(approval["nonce"], nonce)
                or (time.time() if now is None else now) >= approval["expires"]
            ):
                raise ValueError("Invalid or expired approval")
            if self.store.cancelled(task_id):
                raise ValueError("Cancellation invalidated this approval")
            source = read_source(self.folder(task_id, "candidate"))
            target = self.folder(task_id, "target")
            if (
                approval.get("checks_digest")
                != getattr(self.validator, "policy_digest", lambda: None)()
                or digest(source) != approval["candidate_digest"]
                or digest(read_source(target)) != task["base_digest"]
                or digest(json.dumps(task["evidence"], sort_keys=True))
                != approval["evidence_digest"]
            ):
                task["status"] = "review_required"
                task["approval"] = None
                self.store.save(task, "approval_invalidated")
                raise ValueError("Reviewed source, target or evidence changed")
            task["status"] = "applying"
            self.store.save(task, "apply_started")
            write_source(target, source)
            task["status"] = "completed"
            task["applied_digest"] = digest(source)
            task["approval"] = None
            self.store.save(task, "applied", {"digest": digest(source)})
            return task

    def cancel(self, task_id):
        task = self.store.get(task_id)
        if task["status"] in {"completed", "applying", "rejected"}:
            raise ValueError("Cannot cancel an applied or rejected operation")
        self.store.request_cancel(task_id)
        if self.lock.acquire(blocking=False):
            try:
                task = self.store.get(task_id)
                if task["status"] not in {
                    "completed",
                    "applying",
                    "rejected",
                    "running",
                    "reconciliation_required",
                }:
                    task["status"] = "cancelled"
                    task["approval"] = None
                    self.store.save(task, "cancelled")
                    return task
            finally:
                self.lock.release()
        return {"id": task_id, "status": "cancellation_requested"}

    def reject(self, task_id: str) -> dict:
        with self.lock:
            task = self.store.get(task_id)
            if task["status"] in {"completed", "applying", "rejected"}:
                raise ValueError("Task cannot be rejected in this state")
            task["status"] = "rejected"
            task["approval"] = None
            self.store.save(task, "rejected")
            return task

    def reconcile(self, task_id: str) -> dict:
        """Inspect registered source bytes; never replay an uncertain operation."""
        with self.lock:
            task = self.store.get(task_id)
            if task["status"] == "applying":
                target_digest = digest(read_source(self.folder(task_id, "target")))
                approved = task["approval"]
                task["status"] = (
                    "completed"
                    if approved and target_digest == approved["candidate_digest"]
                    else "review_required"
                )
            elif task["status"] in {"running", "reconciliation_required"}:
                pending = task["pending"]
                if pending and pending["name"] == "write_file":
                    actual = digest(read_source(self.folder(task_id, "candidate")))
                    if actual != digest(pending["arguments"].get("content", "")):
                        raise ValueError("Uncertain edit differs; manual inspection required")
                    task["history"].append(
                        {
                            "name": "write_file",
                            "arguments": pending["arguments"],
                            "result": {"digest": actual},
                        }
                    )
                elif pending:
                    raise ValueError("Uncertain non-write operation needs manual inspection")
                task["pending"] = None
                task["status"] = "paused"
            else:
                raise ValueError("Task has no interrupted operation")
            if task["status"] == "completed":
                task["applied_digest"] = target_digest
            task["approval"] = None
            task["evidence"] = None
            self.store.save(task, "reconciled", {"status": task["status"]})
            return task

    def revalidate(self, task_id):
        with self.lock:
            task = self.store.get(task_id)
            if (
                task["status"] not in {"awaiting_approval", "review_required", "paused"}
                or task["pending"]
            ):
                raise ValueError("Task is not available for fresh validation")
            if digest(read_source(self.folder(task_id, "target"))) != task["base_digest"]:
                raise ValueError("Base target changed; start a fresh task after inspection")
            self._check_control(task_id, float("inf"))
            task["approval"] = None
            task["pending"] = {"name": "validate", "arguments": {}}
            task["status"] = "running"
            self.store.save(task, "revalidation_started")
            try:
                self._execute(task, Operation("validate", {}))
                task["pending"] = None
                self._prepare_approval(task)
                return task
            except RunCancelled:
                task["status"], task["pending"] = "cancelled", None
                self.store.save(task, "cancelled")
                return task
            except Exception:
                task["status"], task["pending"] = "review_required", None
                self.store.save(task, "revalidation_failed")
                raise

    def sample(self, task_id, csv_text):
        with self.lock:
            task = self.store.get(task_id)
            if task["status"] != "completed" or not task.get("applied_digest"):
                raise ValueError("Apply a verified change before exercising the sample")
            result = self.validator.preview(
                self.folder(task_id, "target"), csv_text, task["applied_digest"]
            )
            self.store.save(
                task,
                "sample_executed",
                {
                    "target_digest": task["applied_digest"],
                    "accepted": len(result["issues"]),
                    "errors": len(result["errors"]),
                },
            )
            return result

    def review(self, task_id: str) -> dict:
        task = self.store.get(task_id)
        candidate = read_source(self.folder(task_id, "candidate"))
        return {
            "task": task,
            "diff": "".join(
                difflib.unified_diff(
                    task["base_source"].splitlines(True),
                    candidate.splitlines(True),
                    fromfile="base/" + SOURCE,
                    tofile="candidate/" + SOURCE,
                )
            ),
        }
