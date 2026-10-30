"""Foreground public development loop: Meta supervisor/implementer controller.

Ordinary Python/dataclasses only. No shell, no execution of generated code,
no dependencies/services/storage schema/network. Only the injected trusted
validator executes isolated generated code; models never supply it.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Mapping

SupervisorAction = Literal["task", "accept", "stop", "decision_needed"]

MAX_TASK_CHARS = 4000
MAX_FILE_BYTES = 32_768
MAX_FILES = 8
MAX_OUTPUT_CHARS = 4000
MAX_SNAPSHOT_BYTES = 131_072


@dataclass(frozen=True)
class Receipt:
    kind: str
    detail: str
    provenance: Literal["host", "model"]


@dataclass(frozen=True)
class SupervisorView:
    objective: str
    snapshot: Mapping[str, bytes | None]
    diff: str
    last_outcome: str
    last_passed: bool
    last_validation_output: str = ""
    budget_left: int = 0
    tasks_used: int = 0
    repairs_used: int = 0
    allowed: tuple[str, ...] = ()


@dataclass(frozen=True)
class SupervisorDecision:
    """Decision emitted by the supervisor for the development loop.

    Represents the untrusted model/supervisor's chosen next action.
    The host validates the action and binds execution to the reviewed
    bytes; changed workspace state invalidates any pending approval.

    Attributes:
        action: Next control action ("task", "accept", "stop",
            "decision_needed").
        task: Task description when action is "task"; bounded non-empty
            string.
        targets: Allowlist subset the implementer may write in this turn;
            must be subset of immutable allowlist.
        reason: Human-readable reason for the decision, truncated by host.
        provenance: Source marker; always "model" for supervisor output.
    """

    action: SupervisorAction
    task: str = ""
    targets: tuple[str, ...] = ()
    reason: str = ""
    provenance: Literal["model"] = "model"


@dataclass(frozen=True)
class ImplementerView:
    objective: str
    task: str
    targets: tuple[str, ...]
    snapshot: Mapping[str, bytes | None]


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of the host-trusted isolated validator.

    Produced only by the injected trusted validator (never by a model)
    after executing candidate bytes in the approved isolated runner.
    Records whether the candidate satisfies acceptance checks.

    Attributes:
        passed: True if validation succeeded and candidate may be accepted.
        output: Bounded diagnostic output / logs from the validator.
        provenance: Source marker; always "host" to distinguish from
            untrusted model output.
    """

    passed: bool
    output: str
    provenance: Literal["host"] = "host"


@dataclass(frozen=True)
class FinalState:
    status: str
    workspace_hash: str
    tasks_used: int
    repairs_used: int
    requests_used: int
    receipts: tuple[Receipt, ...] = field(default_factory=tuple)


SupervisorFn = Callable[[SupervisorView], SupervisorDecision]
ImplementerFn = Callable[[ImplementerView], Mapping[str, str]]
ValidatorFn = Callable[[Mapping[str, bytes]], ValidationResult]
ReceiptSink = Callable[[Receipt], None]
ShouldStop = Callable[[], bool]

_ALLOWED_ACTIONS = ("task", "accept", "stop", "decision_needed")


def _emit(sink: ReceiptSink | None, receipts: list[Receipt], receipt: Receipt) -> None:
    receipts.append(receipt)
    if sink is not None:
        sink(receipt)


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
    except OSError:
        return False
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            if is_junction():
                return True
        except (OSError, NotImplementedError):
            pass
    return False


def _has_link_no_resolve(path: Path) -> bool:
    """Check path and all ancestors without resolving links away."""
    node = path.absolute()
    while True:
        if _is_link(node):
            return True
        parent = node.parent
        if parent == node:
            break
        node = parent
    return False


def _refusal(workspace: Path, rel: str) -> str | None:
    """Return refusal reason, or None if path is lexically safe and link-free."""
    if not isinstance(rel, str) or not rel:
        return "empty-path"
    if not isinstance(workspace, Path):
        return "bad-workspace"
    # Portable POSIX relative allowlist: reject backslashes, colons
    # (Windows drive-relative C:foo, ADS file:stream), control chars.
    for ch in rel:
        if ch == "\\":
            return "backslash-not-portable"
        if ch == ":":
            return "colon-not-portable"
        code = ord(ch)
        if code < 32 or code == 127:
            return "control-char"
    if rel.startswith("/"):
        return "absolute-path"
    if len(rel) > 2 and rel[1] == ":" and rel[2] in "/\\":
        return "absolute-path"
    if rel.startswith("\\\\"):
        return "absolute-path"
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "dotdot-or-empty-segment"
    # Workspace ancestors (absolute, unresolved) must be link-free.
    if _has_link_no_resolve(workspace):
        return "workspace-ancestor-link"
    candidate = (workspace.absolute() / rel).absolute()
    workspace_abs = workspace.absolute()
    node: Path = candidate
    while True:
        if _is_link(node):
            return "symlink-or-junction-in-path"
        if node == workspace_abs:
            break
        parent = node.parent
        if parent == node:
            return "escape-workspace"
        node = parent
        try:
            candidate.relative_to(workspace_abs)
        except ValueError:
            # Walked outside workspace (should not happen after lexical checks).
            return "escape-workspace"
        if node == workspace_abs:
            continue
    return None


def _snapshot(workspace: Path, allowlist: tuple[str, ...]) -> dict[str, bytes | None]:
    if _has_link_no_resolve(workspace):
        raise ValueError("workspace root or ancestor is a link")
    out: dict[str, bytes | None] = {}
    total = 0
    for rel in allowlist:
        reason = _refusal(workspace, rel)
        if reason is not None:
            raise ValueError(f"bad allowlist entry {rel!r}: {reason}")
        target = workspace / rel
        if _is_link(target):
            out[rel] = None
            continue
        try:
            is_file = target.is_file()
        except (OSError, ValueError):
            raise ValueError(f"unreadable path {rel!r}")
        if is_file:
            try:
                data = target.read_bytes()
            except (OSError, ValueError) as exc:
                raise ValueError(f"snapshot read failed {rel!r}: {exc}")
            if len(data) > MAX_FILE_BYTES:
                raise ValueError(f"snapshot file too large {rel!r}")
            total += len(data)
            if total > MAX_SNAPSHOT_BYTES:
                raise ValueError("snapshot too large")
            out[rel] = data
        else:
            out[rel] = None
    return out


def _workspace_hash(snapshot: Mapping[str, bytes | None]) -> str:
    digest = hashlib.sha256()
    for key in sorted(snapshot):
        value = snapshot[key]
        digest.update(key.encode("utf-8"))
        digest.update(b"\x00")
        if value is None:
            digest.update(b"N\x00")
        else:
            digest.update(b"P\x00")
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
        digest.update(b"\x00")
    return digest.hexdigest()


def _decode_text(data: bytes | None) -> list[str]:
    if data is None:
        return []
    return data.decode("utf-8", errors="replace").splitlines(keepends=False)


def _diff_text(before: Mapping[str, bytes | None], after: Mapping[str, bytes | None]) -> str:
    lines: list[str] = []
    for key in sorted(set(before) | set(after)):
        left, right = before.get(key), after.get(key)
        if left != right:
            left_lines = _decode_text(left)
            right_lines = _decode_text(right)
            diff = difflib.unified_diff(
                left_lines, right_lines, fromfile=f"a/{key}", tofile=f"b/{key}"
            )
            chunk = "\n".join(list(diff)[:80])
            if not chunk:
                chunk = f"{key}: {len(left) if left else 0}->{len(right) if right else 0} bytes"
            lines.append(chunk)
            if sum(len(line) + 1 for line in lines) > MAX_OUTPUT_CHARS:
                break
    text = "\n".join(lines)
    return text[:MAX_OUTPUT_CHARS]


def _check_batch(
    batch: object, allowlist: tuple[str, ...], workspace: Path
) -> tuple[dict[str, bytes] | None, str | None]:
    if not isinstance(batch, Mapping):
        return None, "malformed-batch-not-mapping"
    if len(batch) == 0:
        return None, "empty-batch"
    if len(batch) > MAX_FILES:
        return None, "too-many-files"
    staged: dict[str, bytes] = {}
    for key, value in batch.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return None, "nonstring-content"
        if key not in allowlist:
            return None, f"outside-allowlist:{key}"
        reason = _refusal(workspace, key)
        if reason is not None:
            return None, f"{reason}:{key}"
        try:
            raw = value.encode("utf-8")
        except (UnicodeEncodeError, ValueError) as exc:
            return None, f"unencodable:{key}:{type(exc).__name__}"
        if len(raw) > MAX_FILE_BYTES:
            return None, f"oversized:{key}"
        staged[key] = raw
    return staged, None


def run_development_loop(
    *,
    workspace: Path | str,
    objective: str,
    allowlist: tuple[str, ...],
    supervisor: SupervisorFn,
    implementer: ImplementerFn,
    validator: ValidatorFn,
    request_budget: int = 32,
    max_tasks: int = 6,
    max_repairs: int = 4,
    max_no_progress: int = 4,
    receipt_sink: ReceiptSink | None = None,
    should_stop: ShouldStop | None = None,
) -> FinalState:
    """Run the host-controlled supervisor/implementer development loop.

    The host drives the loop, enforcing path/resource/tool boundaries,
    workspace hashing, and approval binding. The supervisor (untrusted)
    proposes tasks or acceptance; the implementer produces candidate file
    batches; the trusted validator (host provenance) checks them in
    isolation.

    Args:
        workspace: Root directory for allowlisted file effects.
        objective: Bounded task objective shown to supervisor/implementer.
        allowlist: Immutable tuple of POSIX-relative file paths that may
            be read/written; entries must be unique and within workspace.
        supervisor: Callable receiving a SupervisorView and returning a
            SupervisorDecision (provenance "model").
        implementer: Callable receiving an ImplementerView and returning
            a mapping of path->utf-8 string content for the candidate
            batch.
        validator: Trusted host callable receiving exact candidate bytes
            and returning a ValidationResult with provenance "host".
        request_budget: Total supervisor+implementer calls allowed.
        max_tasks: Maximum task iterations.
        max_repairs: Maximum repair iterations after failures.
        max_no_progress: Bound on consecutive non-progress turns before
            stopping.
        receipt_sink: Optional callback for host receipts as they are
            emitted.
        should_stop: Optional host poll that, when True, stops the loop
            promptly while preserving receipts/effects.

    Returns:
        FinalState with status, workspace hash, usage counters, and host
        receipts. Provenance: all receipts are host-generated; acceptance
        binds to exact reviewed bytes and validation output.
    """
    receipts: list[Receipt] = []
    workspace = Path(workspace)
    allowlist = tuple(allowlist)
    for name, val in (
        ("request_budget", request_budget),
        ("max_tasks", max_tasks),
        ("max_repairs", max_repairs),
        ("max_no_progress", max_no_progress),
    ):
        if isinstance(val, bool) or not isinstance(val, int):
            raise TypeError(f"{name} must be int")
    if not allowlist or len(allowlist) > MAX_FILES:
        raise ValueError("allowlist must list 1..MAX_FILES fixed files")
    if len(set(allowlist)) != len(allowlist):
        raise ValueError("allowlist entries must be unique")
    if not isinstance(objective, str) or not objective or len(objective) > MAX_TASK_CHARS:
        raise ValueError("objective must be a bounded nonempty string")
    if request_budget <= 0 or max_tasks <= 0 or max_repairs < 0 or max_no_progress <= 0:
        raise ValueError("budgets must be positive")

    def host(kind: str, detail: str) -> None:
        _emit(receipt_sink, receipts, Receipt(kind, detail[:MAX_OUTPUT_CHARS], "host"))

    try:
        current = _snapshot(workspace, allowlist)
    except (OSError, ValueError) as exc:
        host("snapshot-refused", str(exc)[:200])
        return FinalState("stopped-uncertain", "", 0, 0, 0, tuple(receipts))
    baseline = dict(current)
    current_hash = _workspace_hash(current)
    host("snapshot", current_hash)
    requests_used = 0
    tasks_used = 0
    repairs_used = 0
    no_progress = 0
    last_outcome = "none"
    last_passed = False
    last_validation_output = ""
    validated_hash: str | None = None

    def budget_left() -> int:
        return request_budget - requests_used

    def poll_stop() -> bool:
        if should_stop is None:
            return False
        try:
            return bool(should_stop())
        except Exception:
            return True

    while True:
        if poll_stop():
            host("stopped", "host stop callback true")
            return FinalState(
                "stopped",
                _workspace_hash(current),
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        if budget_left() <= 0:
            host("budget-exhausted", "shared request budget exhausted")
            return FinalState(
                "budget-exhausted",
                _workspace_hash(current),
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        if no_progress >= max_no_progress:
            host("no-progress", "no-progress bound hit")
            return FinalState(
                "stopped-no-progress",
                _workspace_hash(current),
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        view = SupervisorView(
            objective=objective,
            snapshot=dict(current),
            diff=_diff_text(baseline, current),
            last_outcome=last_outcome,
            last_passed=last_passed,
            last_validation_output=last_validation_output,
            budget_left=budget_left(),
            tasks_used=tasks_used,
            repairs_used=repairs_used,
            allowed=allowlist,
        )
        requests_used += 1
        host("supervisor-called", f"budget_left={budget_left()}")
        try:
            decision = supervisor(view)
        except Exception as exc:
            host("error-supervisor", f"{type(exc).__name__}")
            return FinalState(
                "error-supervisor",
                current_hash,
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        if poll_stop():
            host("stopped", "host stop callback true")
            return FinalState(
                "stopped",
                current_hash,
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        try:
            fresh = _snapshot(workspace, allowlist)
        except (OSError, ValueError) as exc:
            host("uncertain", f"snapshot failed during supervisor call: {exc}")
            return FinalState(
                "stopped-uncertain",
                current_hash,
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        if _workspace_hash(fresh) != current_hash:
            host("uncertain", "workspace changed during supervisor call")
            return FinalState(
                "stopped-uncertain",
                _workspace_hash(fresh),
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        if not isinstance(decision, SupervisorDecision) or decision.action not in _ALLOWED_ACTIONS:
            host("refused", "malformed supervisor decision")
            no_progress += 1
            continue
        if decision.action == "stop":
            host("stopped", decision.reason[:200] if decision.reason else "supervisor stop")
            return FinalState(
                "stopped", current_hash, tasks_used, repairs_used, requests_used, tuple(receipts)
            )
        if decision.action == "decision_needed":
            host(
                "decision-needed",
                decision.reason[:200] if decision.reason else "supervisor decision_needed",
            )
            return FinalState(
                "decision-needed",
                current_hash,
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        if decision.action == "accept":
            if poll_stop():
                host("stopped", "host stop callback true")
                return FinalState(
                    "stopped",
                    current_hash,
                    tasks_used,
                    repairs_used,
                    requests_used,
                    tuple(receipts),
                )
            if not last_passed or validated_hash is None:
                host("accept-refused", "accept before passing validation")
                no_progress += 1
                last_outcome = "accept-refused"
                continue
            try:
                accept_snap = _snapshot(workspace, allowlist)
            except (OSError, ValueError) as exc:
                host("uncertain", f"snapshot failed at accept: {exc}")
                last_outcome = "accept-stale"
                last_passed = False
                return FinalState(
                    "stopped-uncertain",
                    current_hash,
                    tasks_used,
                    repairs_used,
                    requests_used,
                    tuple(receipts),
                )
            if _workspace_hash(accept_snap) != validated_hash or validated_hash != current_hash:
                host("accept-refused", "stale candidate; validation no longer binds current bytes")
                no_progress += 1
                last_outcome = "accept-stale"
                last_passed = False
                continue
            host("accepted", decision.reason[:200] if decision.reason else validated_hash)
            return FinalState(
                "accepted", current_hash, tasks_used, repairs_used, requests_used, tuple(receipts)
            )
        # action == task
        if (
            not isinstance(decision.task, str)
            or not decision.task
            or len(decision.task) > MAX_TASK_CHARS
        ):
            host("refused", "task text empty or oversized")
            no_progress += 1
            continue
        targets = tuple(decision.targets) if decision.targets else allowlist
        if any(t not in allowlist for t in targets):
            host("refused", "task expands immutable allowlist")
            no_progress += 1
            last_outcome = "task-refused-expansion"
            continue
        if tasks_used >= max_tasks:
            host("stopped", "task bound hit")
            return FinalState(
                "stopped-task-bound",
                current_hash,
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        if last_outcome not in ("none",) and not last_passed:
            if repairs_used >= max_repairs:
                host("stopped", "repair bound hit")
                return FinalState(
                    "stopped-repair-bound",
                    current_hash,
                    tasks_used,
                    repairs_used,
                    requests_used,
                    tuple(receipts),
                )
            repairs_used += 1
        tasks_used += 1
        if poll_stop():
            host("stopped", "host stop callback true")
            return FinalState(
                "stopped", current_hash, tasks_used, repairs_used, requests_used, tuple(receipts)
            )
        if budget_left() <= 0:
            host("budget-exhausted", "no budget for implementer call")
            return FinalState(
                "budget-exhausted",
                current_hash,
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        try:
            hash_before_impl = _workspace_hash(_snapshot(workspace, allowlist))
        except (OSError, ValueError) as exc:
            host("uncertain", f"snapshot failed before implementer call: {exc}")
            return FinalState(
                "stopped-uncertain",
                current_hash,
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        if hash_before_impl != current_hash:
            host("uncertain", "workspace changed before implementer call")
            return FinalState(
                "stopped-uncertain",
                hash_before_impl,
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        requests_used += 1
        host("implementer-called", decision.task[:200])
        try:
            batch = implementer(ImplementerView(objective, decision.task, targets, dict(current)))
        except Exception as exc:
            host("error-implementer", f"{type(exc).__name__}")
            return FinalState(
                "error-implementer",
                current_hash,
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        if poll_stop():
            host("stopped", "host stop callback true")
            return FinalState(
                "stopped",
                current_hash,
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        try:
            hash_after_impl = _workspace_hash(_snapshot(workspace, allowlist))
        except (OSError, ValueError) as exc:
            host("uncertain", f"snapshot failed during implementer call: {exc}")
            return FinalState(
                "stopped-uncertain",
                current_hash,
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        if hash_after_impl != current_hash:
            host("uncertain", "workspace changed during implementer call")
            return FinalState(
                "stopped-uncertain",
                hash_after_impl,
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        try:
            staged, refusal = _check_batch(batch, allowlist, workspace)
        except (OSError, ValueError) as exc:
            host("batch-refused", f"batch check failed: {type(exc).__name__}")
            no_progress += 1
            last_outcome = "batch-refused"
            last_passed = False
            continue
        if staged is None:
            host("batch-refused", refusal or "refused")
            no_progress += 1
            last_outcome = "batch-refused"
            last_passed = False
            continue
        if any(k not in targets for k in staged):
            host("batch-refused", "batch writes outside narrowed targets")
            no_progress += 1
            last_outcome = "batch-refused"
            last_passed = False
            continue
        candidate_before = dict(current)
        try:
            for rel in sorted(staged):
                (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
                (workspace / rel).write_bytes(staged[rel])
        except OSError as exc:
            host("uncertain-partial-write", f"{type(exc).__name__}; effects preserved, no rollback")
            try:
                current = _snapshot(workspace, allowlist)
                current_hash = _workspace_hash(current)
            except (OSError, ValueError):
                current_hash = "unknown"
            return FinalState(
                "uncertain-partial-write",
                current_hash,
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        try:
            current = _snapshot(workspace, allowlist)
        except (OSError, ValueError) as exc:
            host("uncertain", f"snapshot failed after write: {exc}")
            return FinalState(
                "stopped-uncertain",
                _workspace_hash(candidate_before),
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        current_hash = _workspace_hash(current)
        host("diff", _diff_text(baseline, current)[:MAX_OUTPUT_CHARS] or "no-change")
        exact_bytes = dict(current)
        candidate_hash = current_hash
        if poll_stop():
            host("stopped", "host stop callback true")
            return FinalState(
                "stopped", current_hash, tasks_used, repairs_used, requests_used, tuple(receipts)
            )
        try:
            outcome = validator(exact_bytes)
        except Exception as exc:
            host("unknown-validator", f"{type(exc).__name__}; effects preserved, stopping")
            return FinalState(
                "stopped-unknown-validator",
                candidate_hash,
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        if (
            not isinstance(outcome, ValidationResult)
            or not isinstance(outcome.passed, bool)
            or not isinstance(outcome.output, str)
            or outcome.provenance != "host"
        ):
            host("unknown-validator", "malformed validator outcome; effects preserved, stopping")
            return FinalState(
                "stopped-unknown-validator",
                candidate_hash,
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        after_hash = _workspace_hash(_snapshot(workspace, allowlist))
        if after_hash != candidate_hash:
            host("uncertain", "workspace changed during validation")
            current = _snapshot(workspace, allowlist)
            return FinalState(
                "stopped-uncertain",
                _workspace_hash(current),
                tasks_used,
                repairs_used,
                requests_used,
                tuple(receipts),
            )
        last_validation_output = outcome.output[:MAX_OUTPUT_CHARS]
        host("validation", f"passed={outcome.passed} {outcome.output[:200]}")
        if outcome.passed:
            last_outcome = "passed"
            last_passed = True
            validated_hash = candidate_hash
            no_progress = 0
        else:
            last_outcome = "failed"
            last_passed = False
            no_progress = no_progress + 1 if current == candidate_before else 0
