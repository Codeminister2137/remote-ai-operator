"""Bounded task execution and approvals tied to inspected bytes and evidence."""

import difflib
import hashlib
import json
import logging
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

logger = logging.getLogger(__name__)

SOURCE = "issue_import.py"
ADMITTED_SUFFIXES = (".js", ".html")
MAX_SOURCE_BYTES = 32_768
TASK_APPROVAL_SECONDS = 3600


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def admitted_file(name: str) -> str:
    """Return ``name`` when it is SOURCE or an admitted single-segment basename.

    Besides the registered sample source, only single-segment basenames ending
    in .js/.html are admitted. Separators, parent references, absolute or
    drive-qualified paths, empty names and other extensions are refused before
    any filesystem access.
    """
    if name == SOURCE:
        return name
    if (
        not isinstance(name, str)
        or not name
        or "/" in name
        or "\\" in name
        or ".." in name
        or ":" in name
        or os.path.isabs(name)
        or name != Path(name).name
        or not name.endswith(ADMITTED_SUFFIXES)
    ):
        raise ValueError("File path is not admitted")
    return name


def safe_path(root: Path, name: str) -> Path:
    """Reject links/junctions at the registered workspace and one controlled file."""
    target = root / admitted_file(name)
    for path in (root, target):
        if path.is_symlink() or path.is_junction():
            raise ValueError("Linked workspace/source is forbidden")
    return target


def safe_file(root: Path) -> Path:
    """Reject links/junctions at the registered workspace and controlled source."""
    return safe_path(root, SOURCE)


def read_named(root: Path, name: str) -> str:
    path = safe_path(root, name)
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Source exceeds allowed size")
    return path.read_bytes().decode("utf-8")


def read_source(root: Path) -> str:
    return read_named(root, SOURCE)


def write_named(root: Path, name: str, content: str) -> None:
    admitted = admitted_file(name)
    if not isinstance(content, str) or len(content.encode()) > MAX_SOURCE_BYTES:
        raise ValueError("Invalid source size/type")
    destination = safe_path(root, admitted)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content.encode())
        safe_path(root, admitted)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_source(root: Path, source: str) -> None:
    write_named(root, SOURCE, source)


PROJECT_DENY_BASENAMES = frozenset(
    {"credentials.json", "secrets.json", "id_rsa", "id_ed25519", "id_ecdsa"}
)
PROJECT_DENY_SUFFIXES = (".pem", ".key")
MAX_PROJECT_PATH_CHARS = 512


def resolve_project_path(root: Path, rel: str) -> Path:
    """Resolve ``rel`` inside ``root`` for project-file tasks, or refuse it.

    Unlike :func:`admitted_file` (single-segment sample names), project paths
    may nest in subdirectories, but every segment must be an explicit plain
    name: absolute paths, drive letters, parent references, dot-segments and
    secret-looking basenames are refused before any filesystem access. The
    resolved path must stay inside ``root`` and neither it nor any directory
    between ``root`` and its parent may be a link or junction.
    """
    root = Path(root).resolve()
    if not isinstance(rel, str) or not rel or len(rel) > MAX_PROJECT_PATH_CHARS:
        raise ValueError("Project path is not admitted")
    if "\\" in rel:
        raise ValueError("Project path is not admitted")
    candidate = Path(rel)
    if candidate.is_absolute() or ":" in rel:
        raise ValueError("Project path is not admitted")
    # Split textually: Path parsing would silently drop "." segments.
    segments = rel.split("/")
    if not segments:
        raise ValueError("Project path is not admitted")
    for segment in segments:
        if segment in {"", ".", ".."} or segment.startswith("."):
            raise ValueError("Project path is not admitted")
    lowered = segments[-1].lower()
    if lowered in PROJECT_DENY_BASENAMES or lowered.endswith(PROJECT_DENY_SUFFIXES):
        raise ValueError("Project path is not admitted")
    full = (root / candidate).resolve()
    if full != root and root not in full.parents:
        raise ValueError("Project path escapes the registered root")
    node = full if full.is_dir() else full.parent
    while True:
        if node.is_symlink() or node.is_junction():
            raise ValueError("Linked project path is forbidden")
        if node == root:
            break
        node = node.parent
    if full.exists() and (full.is_symlink() or full.is_junction()):
        raise ValueError("Linked project path is forbidden")
    return full


def read_project_file(root: Path, rel: str) -> str:
    """Read one admitted text file from a registered project root."""
    path = resolve_project_path(root, rel)
    if not path.is_file():
        raise ValueError("Project path is not a readable file")
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Project file exceeds allowed size")
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Project file is not decodable text") from None


def stage_project_file(staging: Path, rel: str, content: str) -> str:
    """Atomically stage ``content`` under ``staging`` mirroring ``rel``.

    ``rel`` must already be admitted by :func:`resolve_project_path`; the join
    is re-checked for containment so a validated relative path can never
    escape the staging directory.
    """
    resolve_project_path(staging, rel)
    if not isinstance(content, str) or len(content.encode()) > MAX_SOURCE_BYTES:
        raise ValueError("Invalid staged size/type")
    staging = staging.resolve()
    destination = (staging / Path(rel)).resolve()
    if destination != staging and staging not in destination.parents:
        raise ValueError("Staged path escapes the staging directory")
    if destination.exists() and not destination.is_file():
        raise ValueError("Staged path is not a plain file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content.encode())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return digest(content)


def staged_project_files(staging: Path) -> list[str]:
    """List staged files as POSIX relative paths in sorted order."""
    if not staging.is_dir():
        return []
    found = []
    for path in sorted(staging.rglob("*")):
        if path.is_file() and not path.is_symlink():
            found.append(path.relative_to(staging).as_posix())
    return found


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

    def submit(self, prompt: str, *, mode: str = "sample", project: str | None = None) -> dict:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            raise ValueError("Task requires 1-4000 characters")
        if prompt.strip()[:6].upper() == "REPLY:":
            raise ValueError("REPLY prompts route to the owner thread, not tasks")
        if mode != "sample" or project is not None:
            raise ValueError("Project development is not configured on this serve")
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
                "mode": "sample",
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
                if task.get("status") == "waiting_on_owner":
                    self.store.save(
                        task, "question_asked", {"question": task.get("pending_question", "")}
                    )
                    return task
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

    def _read_spec(self) -> str:
        """Serve the registered task specification (read-only, never writable)."""
        spec = self.seed.parent.parent / "docs" / "sample-task.md"
        try:
            text = spec.read_text(encoding="utf-8")
        except OSError:
            raise ValueError("Task specification is not available") from None
        if len(text.encode()) > MAX_SOURCE_BYTES:
            raise ValueError("Task specification exceeds size limit")
        return text

    def _check_control(self, task_id, deadline):
        if self.store.cancelled(task_id):
            raise RunCancelled()
        if time.monotonic() >= deadline:
            logger.debug("control timeout for task %s", task_id)
            raise TimeoutError("Task deadline reached; late output cannot authorize effects")

    def _execute(self, task: dict, operation: Operation, *, deadline=float("inf")):
        workspace = self.folder(task["id"], "candidate")
        if operation.name in {"read_file", "write_file"}:
            expected = {"path"} if operation.name == "read_file" else {"path", "content"}
            if set(operation.arguments) != expected:
                raise ValueError("Invalid file operation arguments")
            if operation.name == "read_file" and operation.arguments["path"] in {
                "sample-task.md",
                "docs/sample-task.md",
            }:
                return {"content": self._read_spec()}
            try:
                name = admitted_file(operation.arguments["path"])
            except ValueError:
                logger.debug(
                    "rejected %s path: %r", operation.name, operation.arguments.get("path")
                )
                raise
            if operation.name == "read_file":
                return {"content": read_named(workspace, name)}
            write_named(workspace, name, operation.arguments["content"])
            task["evidence"] = None
            return {"digest": digest(read_named(workspace, name))}
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
        if operation.name == "ask_owner":
            return self._park_question(task, operation)
        raise ValueError(f"Unknown or invalid tool: {operation.name}")

    @staticmethod
    def _park_question(task: dict, operation: Operation) -> dict:
        """Park the task on an owner question; shared by sample and project flows."""
        question = (
            operation.arguments.get("question", "") if isinstance(operation.arguments, dict) else ""
        )
        if not isinstance(question, str) or not question.strip() or len(question) > 500:
            raise ValueError("ask_owner requires a 1-500 character question")
        task["pending_question"] = question.strip()
        task["status"] = "waiting_on_owner"
        return {"parked": True}

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
            "expires": time.time() + TASK_APPROVAL_SECONDS,
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
            if not isinstance(nonce, str):
                raise ValueError("Invalid or expired approval")  # noqa: TRY004
            try:
                nonce.encode("ascii")
            except UnicodeEncodeError:
                raise ValueError("Invalid or expired approval")
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
            logger.debug("approval consumed for task %s", task_id)
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
            logger.debug("rejected task %s", task_id)
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
                    try:
                        name = admitted_file(pending["arguments"].get("path", SOURCE))
                    except ValueError:
                        raise ValueError(
                            "Uncertain edit differs; manual inspection required"
                        ) from None
                    actual = digest(read_named(self.folder(task_id, "candidate"), name))
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

    def answer(self, task_id: str, text: str, *, language: str = "en", translator=None) -> dict:
        """Record the owner's answer and resume a parked task as paused.

        Non-English answers are normalized to working English before the
        task resumes; on translation failure nothing is stored.
        """
        from remote_operator.translate import normalize_input

        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("Answer requires 1-2000 characters")
        with self.lock:
            task = self.store.get(task_id)
            if task.get("status") != "waiting_on_owner":
                raise ValueError("Task has no pending owner question")
            if self.store.cancelled(task_id):
                raise ValueError("Task was cancelled")
            normalized, receipt = normalize_input(translator, text.strip(), language)
            task["history"].append(
                {
                    "name": "owner_answer",
                    "result": {
                        "text": normalized.working,
                        "source_text": normalized.source,
                        "source_lang": normalized.lang,
                        "translated": normalized.translated,
                        "translator": normalized.translator_id,
                    },
                }
            )
            if receipt is not None:
                self.store.save(task, "inference_receipt", receipt)
            task["pending_question"] = None
            task["status"] = "paused"
            self.store.save(task, "question_answered")
            return task

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
        if task.get("supervisor_thread"):
            raise ValueError("Task id is a planning thread; use the thread routes")
        candidate_dir = self.folder(task_id, "candidate")
        candidate = read_source(candidate_dir)
        parts = [
            "".join(
                difflib.unified_diff(
                    task["base_source"].splitlines(True),
                    candidate.splitlines(True),
                    fromfile="base/" + SOURCE,
                    tofile="candidate/" + SOURCE,
                )
            )
        ]
        for name in sorted(path.name for path in candidate_dir.iterdir() if path.is_file()):
            if name == SOURCE:
                continue
            try:
                admitted = admitted_file(name)
            except ValueError:
                continue
            parts.append(
                "".join(
                    difflib.unified_diff(
                        [],
                        read_named(candidate_dir, admitted).splitlines(True),
                        fromfile="base/" + admitted,
                        tofile="candidate/" + admitted,
                    )
                )
            )
        return {"task": task, "diff": "".join(parts)}


class ProjectOperator(Operator):
    """An :class:`Operator` that can also develop inside real local repositories.

    Sample tasks behave exactly as on the base class. Project tasks (``mode``
    ``"project"``) read and stage files inside one registered work root chosen
    at submission; nothing is written to the root until the owner approves the
    staged diff with the task nonce. Project tasks run no automated checks:
    approvals record ``checks: "none"`` and the owner is the reviewer.

    Only the local serve builds this class (``--work-root``). Hosted visitor
    serves keep the base class, where project submission is refused.
    """

    def __init__(self, root: Path, seed: Path, validator: Validator, *, work_roots, durable=None):
        super().__init__(root, seed, validator, durable=durable)
        if not work_roots:
            raise ValueError("Project development needs at least one work root")
        resolved = []
        for candidate in work_roots:
            path = Path(candidate).resolve()
            if not path.is_dir():
                raise ValueError("Work root is not a directory")
            if path == self.root or self.root in path.parents:
                raise ValueError("Work root must not overlap the runtime store")
            resolved.append(path)
        self.work_roots = tuple(dict.fromkeys(resolved))

    def submit(self, prompt: str, *, mode: str = "sample", project: str | None = None) -> dict:
        if mode == "sample" and project is None:
            return super().submit(prompt)
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            raise ValueError("Task requires 1-4000 characters")
        if prompt.strip()[:6].upper() == "REPLY:":
            raise ValueError("REPLY prompts route to the owner thread, not tasks")
        if mode != "project":
            raise ValueError("Unknown task mode")
        target = self._select_root(project)
        with self.lock:
            task_id = uuid.uuid4().hex
            staging = self.root / task_id / "staging"
            staging.mkdir(parents=True)
            task = {
                "id": task_id,
                "prompt": prompt,
                "status": "queued",
                "mode": "project",
                "project_root": str(target),
                "base_digest": "",
                "base_source": "",
                "history": [],
                "pending": None,
                "evidence": None,
                "approval": None,
                "injected": False,
                "calls": 0,
                "applied_digest": None,
            }
            self.store.save(task, "submitted", {"mode": "project"})
            return task

    def _select_root(self, project: str | None) -> Path:
        """Return the registered work root matching ``project``, or refuse."""
        if not isinstance(project, str) or not project:
            raise ValueError("Project tasks require a registered work root")
        try:
            candidate = Path(project).resolve()
        except OSError:
            raise ValueError("Unknown work root") from None
        for root in self.work_roots:
            if candidate == root:
                return root
        raise ValueError("Unknown work root")

    def _task_root(self, task: dict) -> Path:
        """Return the task's work root, which must still be registered."""
        try:
            candidate = Path(task["project_root"]).resolve()
        except (KeyError, OSError):
            raise ValueError("Task has no registered work root") from None
        if candidate not in self.work_roots:
            raise ValueError("Task work root is no longer registered")
        return candidate

    def _execute(self, task: dict, operation: Operation, *, deadline=float("inf")):
        if task.get("mode") != "project":
            return super()._execute(task, operation, deadline=deadline)
        return self._execute_project(task, operation)

    def _execute_project(self, task: dict, operation: Operation) -> dict:
        root = self._task_root(task)
        staging = self.folder(task["id"], "staging")
        if operation.name in {"read_file", "write_file"}:
            expected = {"path"} if operation.name == "read_file" else {"path", "content"}
            if set(operation.arguments) != expected:
                raise ValueError("Invalid file operation arguments")
            rel = operation.arguments["path"]
            if operation.name == "read_file":
                try:
                    return {"content": read_project_file(root, rel)}
                except ValueError:
                    logger.debug("rejected project read: %r", rel)
                    raise
            try:
                staged_digest = stage_project_file(staging, rel, operation.arguments["content"])
            except ValueError:
                logger.debug("rejected project write: %r", rel)
                raise
            task["evidence"] = None
            return {"staged": rel, "digest": staged_digest}
        if operation.name == "validate" and not operation.arguments:
            return {
                "passed": False,
                "checks": "none-configured",
                "detail": (
                    "This serve runs no automated checks for project files; "
                    "the owner reviews the staged diff before anything is applied."
                ),
            }
        if operation.name == "ask_owner":
            return self._park_question(task, operation)
        raise ValueError(f"Unknown or invalid tool: {operation.name}")

    def _prepare_approval(self, task: dict) -> None:
        if task.get("mode") != "project":
            return super()._prepare_approval(task)
        staging = self.folder(task["id"], "staging")
        root = self._task_root(task)
        staged = staged_project_files(staging)
        if not staged:
            raise ValueError("No staged project changes")
        files = []
        for rel in staged:
            staged_digest = digest((staging / rel).read_bytes().decode("utf-8"))
            try:
                base_digest = digest(read_project_file(root, rel))
                exists = True
            except ValueError:
                base_digest = None
                exists = False
            files.append(
                {
                    "path": rel,
                    "staged_digest": staged_digest,
                    "base_digest": base_digest,
                    "exists": exists,
                }
            )
        task["approval"] = {
            "nonce": secrets.token_urlsafe(24),
            "expires": time.time() + TASK_APPROVAL_SECONDS,
            "files": files,
            "checks": "none",
            "evidence_digest": digest("none"),
        }
        task["status"] = "awaiting_approval"
        self.store.save(task, "approval_requested", {"files": [entry["path"] for entry in files]})

    def approve(self, task_id: str, nonce: str, *, now: float | None = None) -> dict:
        peek = self.store.get(task_id)
        if peek.get("mode") != "project":
            return super().approve(task_id, nonce, now=now)
        with self.lock:
            task = self.store.get(task_id)
            approval = task["approval"]
            if task["status"] != "awaiting_approval" or not approval:
                raise ValueError("No pending approval")
            if not isinstance(nonce, str):
                raise ValueError("Invalid or expired approval")  # noqa: TRY004
            try:
                nonce.encode("ascii")
            except UnicodeEncodeError:
                raise ValueError("Invalid or expired approval")
            if (
                not secrets.compare_digest(approval["nonce"], nonce)
                or (time.time() if now is None else now) >= approval["expires"]
            ):
                raise ValueError("Invalid or expired approval")
            if self.store.cancelled(task_id):
                raise ValueError("Cancellation invalidated this approval")
            root = self._task_root(task)
            staging = self.folder(task_id, "staging")
            recorded = approval.get("files") or []
            if staged_project_files(staging) != sorted(entry["path"] for entry in recorded):
                task["status"] = "review_required"
                task["approval"] = None
                self.store.save(task, "approval_invalidated")
                raise ValueError("Reviewed staged files changed")
            for entry in recorded:
                try:
                    staged_text = (staging / entry["path"]).read_bytes().decode("utf-8")
                except (OSError, UnicodeDecodeError):
                    staged_text = None
                if staged_text is None or digest(staged_text) != entry["staged_digest"]:
                    task["status"] = "review_required"
                    task["approval"] = None
                    self.store.save(task, "approval_invalidated")
                    raise ValueError("Reviewed staged files changed")
                try:
                    current_base = digest(read_project_file(root, entry["path"]))
                except ValueError:
                    current_base = None
                if current_base != entry["base_digest"]:
                    task["status"] = "review_required"
                    task["approval"] = None
                    self.store.save(task, "approval_invalidated")
                    raise ValueError("Reviewed project files changed")
            logger.debug("approval consumed for project task %s", task_id)
            task["status"] = "applying"
            self.store.save(task, "apply_started")
            applied = []
            for entry in recorded:
                destination = resolve_project_path(root, entry["path"])
                if destination == self.root or self.root in destination.parents:
                    raise ValueError("Apply target overlaps the runtime store")
                if not destination.parent.is_dir():
                    raise ValueError("Apply target parent directory is missing")
                staged_text = (staging / entry["path"]).read_bytes().decode("utf-8")
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(
                        dir=destination.parent, delete=False
                    ) as stream:
                        temporary = Path(stream.name)
                        stream.write(staged_text.encode())
                    resolve_project_path(root, entry["path"])
                    os.replace(temporary, destination)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                applied.append({"path": entry["path"], "digest": digest(staged_text)})
            task["status"] = "completed"
            task["applied_digest"] = digest(
                json.dumps(applied, sort_keys=True, separators=(",", ":"))
            )
            task["approval"] = None
            self.store.save(task, "applied", {"files": applied})
            return task

    def reconcile(self, task_id: str) -> dict:
        peek = self.store.get(task_id)
        if peek.get("mode") != "project":
            return super().reconcile(task_id)
        with self.lock:
            task = self.store.get(task_id)
            if task["status"] == "applying":
                approved = task["approval"]
                root = self._task_root(task)
                applied = []
                complete = bool(approved and approved.get("files"))
                for entry in approved.get("files") or []:
                    try:
                        current = digest(read_project_file(root, entry["path"]))
                    except ValueError:
                        current = None
                    if current != entry["staged_digest"]:
                        complete = False
                        break
                    applied.append({"path": entry["path"], "digest": current})
                task["status"] = "completed" if complete else "review_required"
                if complete:
                    task["applied_digest"] = digest(
                        json.dumps(applied, sort_keys=True, separators=(",", ":"))
                    )
            elif task["status"] in {"running", "reconciliation_required"}:
                pending = task["pending"]
                if pending and pending["name"] == "write_file":
                    root = self._task_root(task)
                    staging = self.folder(task["id"], "staging")
                    try:
                        rel = pending["arguments"].get("path", "")
                        resolve_project_path(root, rel)
                        actual = digest((staging / rel).read_bytes().decode("utf-8"))
                    except (ValueError, OSError, UnicodeDecodeError):
                        raise ValueError(
                            "Uncertain edit differs; manual inspection required"
                        ) from None
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
            task["approval"] = None
            task["evidence"] = None
            self.store.save(task, "reconciled", {"status": task["status"]})
            return task

    def revalidate(self, task_id):
        if self.store.get(task_id).get("mode") == "project":
            raise ValueError("Project tasks have no automated checks to re-run")
        return super().revalidate(task_id)

    def sample(self, task_id, csv_text):
        if self.store.get(task_id).get("mode") == "project":
            raise ValueError("CSV samples apply to the registered sample task only")
        return super().sample(task_id, csv_text)

    def review(self, task_id: str) -> dict:
        task = self.store.get(task_id)
        if task.get("mode") != "project":
            return super().review(task_id)
        if task.get("supervisor_thread"):
            raise ValueError("Task id is a planning thread; use the thread routes")
        root = self._task_root(task)
        staging = self.folder(task_id, "staging")
        files = []
        parts = []
        for rel in staged_project_files(staging):
            try:
                base_text = read_project_file(root, rel)
            except ValueError:
                base_text = ""
            staged_text = (staging / rel).read_bytes().decode("utf-8")
            parts.append(
                "".join(
                    difflib.unified_diff(
                        base_text.splitlines(True),
                        staged_text.splitlines(True),
                        fromfile="base/" + rel,
                        tofile="candidate/" + rel,
                    )
                )
            )
            files.append(
                {
                    "path": rel,
                    "base_digest": digest(base_text) if base_text else None,
                    "staged_digest": digest(staged_text),
                }
            )
        return {"task": task, "root": str(root), "files": files, "diff": "".join(parts)}
