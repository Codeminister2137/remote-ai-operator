"""Worker that consumes approved supervisor run jobs with reconcile/resume.

Each approved plan becomes one job file under ``supervisor_runs/`` (see
:mod:`remote_operator.supervisor`). The worker turns a job into one operator
task whose prompt is the approved plan, runs it with the caller's model actor,
and mirrors the outcome back into the job file plus thread events.

Interrupted jobs (worker killed, PC slept) are reconciled, never replayed:
:func:`Operator.reconcile` inspects the linked task's registered bytes first,
then the job is requeued for a normal resume. Cancellation on the planning
thread propagates to the linked task at slice boundaries. Approval stays
human: the worker stops at ``awaiting_approval`` and only refreshes the job
once the owner approves the task through the normal task routes.
"""

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from remote_operator.credentials import SessionUnavailable
from remote_operator.scripted import ScriptedModel
from remote_operator.supervisor import RUNS_DIRNAME

MAX_PROMPT_CHARS = 4000
ALLOWANCE_EXHAUSTED = "Model call allowance exhausted"
OPERATOR_BUSY = "Another operator action is active"
TASK_NOT_READY = "Task is not ready; reconcile uncertain effects first"
TERMINAL_JOB = {"completed", "failed", "cancelled"}
DRAIN_WORKER_INTERVAL_SECONDS = 30.0
KEY_MISSING_NOTE = "Provider key detached; re-attach the key to resume"
KEY_RESUMED_NOTE = "Key re-attached; resuming"
HOSTED_MAX_SUBJECTS_PER_PASS = 32
HOSTED_MAX_JOBS_PER_SUBJECT = 5
# Unattended mails every terminal run outcome; attended mails only the run
# events that need the owner (plus completed-after-questions, which always
# mails when a sender is configured, in every mode).
UNATTENDED_NOTIFY_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "awaiting_approval", "waiting_on_owner"}
)
ATTENDED_NOTIFY_STATUSES = frozenset({"failed", "waiting_on_owner"})

logger = logging.getLogger(__name__)


class SupervisorRunner:
    """Drain queued supervisor jobs against one operator's store and lock."""

    def __init__(self, operator, *, notify_send=None, notify_on=None):
        """``notify_send`` maps a job summary dict to a notification receipt.

        It is called for terminal job outcomes in ``notify_on``, plus completed
        runs that involved owner questions (in every mode); any error it
        raises is recorded on the job and never breaks the drain.
        """
        self.operator = operator
        self.notify_send = notify_send
        self.notify_on = (
            set(UNATTENDED_NOTIFY_STATUSES) if notify_on is None else set(notify_on)
        )

    @property
    def _runs(self) -> Path:
        folder = self.operator.root / RUNS_DIRNAME
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    @staticmethod
    def _read_job(path: Path) -> dict | None:
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return job if isinstance(job, dict) else None

    def _write_job(self, path: Path, job: dict) -> None:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(job), encoding="utf-8")
        tmp.replace(path)
        self.operator.store._durable()

    def _thread_event(self, job: dict, kind: str, payload: dict) -> None:
        """Append a thread event; bookkeeping must not fail a finished run."""
        try:
            thread_id = job.get("thread_id", "")
            thread = self.operator.store.get(thread_id)
            if thread.get("id") != thread_id or not thread.get("supervisor_thread"):
                raise ValueError("Unknown supervisor thread")
            self.operator.store.save(thread, kind, payload)
        except (KeyError, ValueError, OSError, sqlite3.Error) as exc:
            job["events_note"] = f"{kind} not recorded: {exc}"[:300]

    def _finish_job(self, path: Path, job: dict, status: str, *, error: str | None = None) -> dict:
        job["status"] = status
        job["finished"] = time.time()
        if error is not None:
            job["error"] = error[:1000]
        else:
            job.pop("error", None)
        self._thread_event(
            job, "run_finished", {"run_id": job.get("run_id", path.stem), "status": status}
        )
        summary = {
            "run_id": job.get("run_id", path.stem),
            "thread_id": job.get("thread_id", ""),
            "status": status,
            "error": job.get("error"),
            "had_questions": job_had_owner_questions(self.operator, job),
        }
        job["had_questions"] = summary["had_questions"]
        wants = status in self.notify_on or (
            status == "completed" and summary["had_questions"]
        )
        if wants and self.notify_send is not None:
            try:
                receipt = self.notify_send(summary)
                job["notify_receipt"] = receipt
            except Exception as exc:  # noqa: BLE001 - notification must not break the drain
                job["notify_error"] = f"{type(exc).__name__}: {exc}"[:300]
        self._write_job(path, job)
        return {"run_id": job.get("run_id", path.stem), "status": status}

    def reconcile_jobs(self) -> list[dict]:
        """Requeue jobs left running by an interrupted worker; never replays."""
        outcomes = []
        for path in sorted(self._runs.glob("*.json")):
            job = self._read_job(path)
            if job is None:
                outcomes.append(
                    {"run_id": path.stem, "action": "skipped", "reason": "unreadable job file"}
                )
                continue
            if job.get("status") != "running":
                continue
            outcomes.append(self._reconcile_job(path, job))
        return outcomes

    def _reconcile_job(self, path: Path, job: dict) -> dict:
        run_id = job.get("run_id", path.stem)
        task_id = job.get("task_id")
        if not task_id:
            job["status"] = "queued"
            job["note"] = "Claimed but never started; requeued"
            self._write_job(path, job)
            return {"run_id": run_id, "action": "requeued"}
        try:
            task = self.operator.store.get(task_id)
        except KeyError:
            return self._finish_job(path, job, "failed", error="Linked task record lost")
        if task["status"] in {"running", "reconciliation_required"}:
            try:
                task = self.operator.reconcile(task_id)
            except ValueError as exc:
                return self._finish_job(path, job, "failed", error=f"Reconcile refused: {exc}")
        status = task["status"]
        if status in {"completed", "cancelled", "rejected"}:
            return self._finish_job(path, job, "completed" if status == "completed" else status)
        if status == "awaiting_approval":
            return self._finish_job(path, job, "awaiting_approval")
        if status == "paused":
            job["status"] = "queued"
            job["note"] = "Interrupted run reconciled; will resume"
            self._write_job(path, job)
            return {"run_id": run_id, "action": "requeued"}
        return self._finish_job(path, job, "failed", error=f"Task needs owner review: {status}")

    def refresh_waiting(self) -> list[dict]:
        """Mirror owner-approved tasks back into waiting jobs; no model calls."""
        outcomes = []
        for path in sorted(self._runs.glob("*.json")):
            job = self._read_job(path)
            if job is None or job.get("status") != "awaiting_approval":
                continue
            try:
                task = self.operator.store.get(job.get("task_id", ""))
            except KeyError:
                outcomes.append(
                    self._finish_job(path, job, "failed", error="Linked task record lost")
                )
                continue
            if task["status"] == "completed":
                outcomes.append(self._finish_job(path, job, "completed"))
            elif task["status"] in {"cancelled", "rejected"}:
                outcomes.append(self._finish_job(path, job, task["status"]))
        return outcomes

    def resume_answered(self, task_id: str) -> list[dict]:
        """Requeue jobs parked on an owner question the owner just answered."""
        outcomes = []
        for path in sorted(self._runs.glob("*.json")):
            job = self._read_job(path)
            if (
                job is None
                or job.get("task_id") != task_id
                or job.get("status") != "waiting_on_owner"
            ):
                continue
            job["status"] = "queued"
            job["note"] = "Owner answered; resuming"
            self._write_job(path, job)
            outcomes.append({"run_id": job.get("run_id", path.stem), "status": "queued"})
        return outcomes

    def resume_waiting_on_key(self) -> list[dict]:
        """Requeue jobs parked for a missing provider key; the key is back."""
        outcomes = []
        for path in sorted(self._runs.glob("*.json")):
            job = self._read_job(path)
            if job is None or job.get("status") != "waiting_on_key":
                continue
            job["status"] = "queued"
            job["note"] = KEY_RESUMED_NOTE
            self._write_job(path, job)
            outcomes.append({"run_id": job.get("run_id", path.stem), "status": "queued"})
        return outcomes

    def park_keyless_live_jobs(self) -> list[dict]:
        """Park live-planned queued jobs while no provider key is attached.

        Jobs whose planning thread used live inference are parked as
        ``waiting_on_key`` so they are never silently downgraded to the
        offline reference. Scripted-planned jobs are left queued for the
        normal scripted drain. Parking never fails a job.
        """
        outcomes = []
        for path in sorted(self._runs.glob("*.json")):
            job = self._read_job(path)
            if job is None or job.get("status") not in {"queued", "paused"}:
                continue
            if not _job_needs_live(self.operator, job):
                continue
            job["status"] = "waiting_on_key"
            job["note"] = KEY_MISSING_NOTE
            self._thread_event(
                job, "run_parked", {"run_id": job.get("run_id", path.stem)}
            )
            self._write_job(path, job)
            outcomes.append(
                {"run_id": job.get("run_id", path.stem), "status": "waiting_on_key"}
            )
        return outcomes

    def drain(self, model, *, max_jobs: int = 10) -> list[dict]:
        """Run up to ``max_jobs`` queued jobs, oldest first; one worker only."""
        if type(max_jobs) is not int or not 1 <= max_jobs <= 100:
            raise ValueError("max_jobs must be an integer between 1 and 100")
        outcomes = []
        started = 0
        for path in sorted(self._runs.glob("*.json")):
            if started >= max_jobs:
                break
            job = self._read_job(path)
            if job is None or job.get("status") not in {"queued", "paused"}:
                continue
            started += 1
            outcome = self._run_claimed(path, job, model)
            outcomes.append(outcome)
            if outcome.get("busy"):
                break
        return outcomes

    def _run_claimed(self, path: Path, job: dict, model) -> dict:
        job["status"] = "running"
        job["attempts"] = int(job.get("attempts", 0)) + 1
        job.pop("finished", None)
        self._write_job(path, job)
        self._thread_event(job, "run_started", {"run_id": job.get("run_id", path.stem)})
        task_id = job.get("task_id")
        if self.operator.store.cancelled(job.get("thread_id", "")):
            if task_id:
                try:
                    self.operator.store.request_cancel(task_id)
                except (KeyError, ValueError):
                    pass
            return self._finish_job(path, job, "cancelled", error="Thread cancelled")
        if not task_id:
            plan = job.get("plan", "")
            prompt = f"Approved plan {job.get('plan_digest', '')}:\n{plan}"
            if len(prompt) > MAX_PROMPT_CHARS:
                prompt = prompt[:MAX_PROMPT_CHARS]
                job["plan_truncated"] = True
            try:
                task = self.operator.submit(prompt)
            except ValueError as exc:
                if OPERATOR_BUSY in str(exc):
                    return self._release_claim(path, job)
                return self._finish_job(path, job, "failed", error=str(exc))
            task_id = task["id"]
            job["task_id"] = task_id
            self._write_job(path, job)
        try:
            task = self.operator.run(task_id, model)
        except TimeoutError as exc:
            return self._after_timeout(path, job, task_id, str(exc))
        except SessionUnavailable:
            # Logout, revocation or expiry mid-drain: park the job so a
            # re-attached key resumes it. Never a failure: the linked task is
            # paused by the run boundary and resumes without replay.
            return self._park_key_missing(path, job)
        except ValueError as exc:
            message = str(exc)
            if OPERATOR_BUSY in message or TASK_NOT_READY in message:
                return self._release_claim(path, job)
            return self._finish_job(path, job, "failed", error=message)
        except Exception as exc:  # noqa: BLE001 - one job must not kill the drain
            return self._finish_job(path, job, "failed", error=f"{type(exc).__name__}: {exc}")
        status = task["status"]
        if status == "awaiting_approval":
            return self._finish_job(path, job, "awaiting_approval")
        if status == "waiting_on_owner":
            return self._finish_job(path, job, "waiting_on_owner")
        if status == "paused":
            job["status"] = "paused"
            job["note"] = "Transport paused; will resume on the next drain"
            self._write_job(path, job)
            return {"run_id": job.get("run_id", path.stem), "status": "paused"}
        if status in {"cancelled", "rejected"}:
            return self._finish_job(path, job, status)
        if status == "completed":
            return self._finish_job(path, job, "completed")
        return self._finish_job(path, job, "failed", error=f"Unexpected task status: {status}")

    def _release_claim(self, path: Path, job: dict) -> dict:
        """Return a job to the queue when another operator action is active."""
        job["status"] = "queued"
        job["note"] = "Operator busy; retry on the next drain"
        self._write_job(path, job)
        return {"run_id": job.get("run_id", path.stem), "status": "queued", "busy": True}

    def _park_key_missing(self, path: Path, job: dict) -> dict:
        """Park a job whose session key vanished mid-drain; never fails it."""
        job["status"] = "waiting_on_key"
        job["note"] = KEY_MISSING_NOTE
        self._thread_event(job, "run_parked", {"run_id": job.get("run_id", path.stem)})
        self._write_job(path, job)
        return {
            "run_id": job.get("run_id", path.stem),
            "status": "waiting_on_key",
            "key_missing": True,
        }

    def _after_timeout(self, path: Path, job: dict, task_id: str, message: str) -> dict:
        if ALLOWANCE_EXHAUSTED in message:
            return self._finish_job(path, job, "failed", error=message)
        try:
            task = self.operator.store.get(task_id)
            if task["status"] in {"running", "reconciliation_required"}:
                task = self.operator.reconcile(task_id)
        except (KeyError, ValueError) as exc:
            return self._finish_job(path, job, "failed", error=f"Interrupted run lost: {exc}")
        if task["status"] == "paused":
            job["status"] = "queued"
            job["note"] = "Deadline interrupted the run; reconciled and requeued"
            self._write_job(path, job)
            return {"run_id": job.get("run_id", path.stem), "status": "queued"}
        return self._finish_job(
            path, job, "failed", error=f"Task needs owner review: {task['status']}"
        )


def _job_needs_live(operator, job: dict) -> bool:
    """Return True when the job's planning thread used live inference.

    Best-effort routing hint: a missing or unreadable thread reads as False
    (scripted drain) and never breaks the sweep.
    """
    try:
        thread = operator.store.get(job.get("thread_id", ""))
    except (KeyError, ValueError, OSError, sqlite3.Error):
        return False
    if not isinstance(thread, dict):
        return False
    history = thread.get("history") or []
    return any(
        isinstance(turn, dict)
        and turn.get("role") == "assistant"
        and turn.get("route") == "meta"
        for turn in history
    )


class DrainWorkerStatus:
    """Thread-safe pass counters for one supervised drain worker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.last_pass: float | None = None
        self.passes = 0
        self.errors = 0
        self.restarts = 0

    def record_pass(self, ok: bool) -> None:
        with self._lock:
            self.last_pass = time.time()
            self.passes += 1
            if not ok:
                self.errors += 1

    def record_restart(self) -> None:
        with self._lock:
            self.restarts += 1

    def snapshot(self, alive: bool) -> dict:
        with self._lock:
            return {
                "alive": alive,
                "last_pass": self.last_pass,
                "passes": self.passes,
                "errors": self.errors,
                "restarts": self.restarts,
            }


def _check_interval(interval_seconds: float) -> None:
    if type(interval_seconds) not in (int, float) or not interval_seconds > 0:
        raise ValueError("interval_seconds must be a positive number")


def job_had_owner_questions(operator, job: dict) -> bool:
    """Return True when the linked task parked on an owner question.

    Best-effort bookkeeping: a missing or unreadable task reads as False and
    never breaks the drain. An answered question leaves an ``owner_answer``
    history entry, which survives completion.
    """
    try:
        task = operator.store.get(job.get("task_id", ""))
    except (KeyError, ValueError, OSError, sqlite3.Error):
        return False
    if not isinstance(task, dict):
        return False
    history = task.get("history") or []
    return any(
        isinstance(entry, dict) and entry.get("name") == "owner_answer" for entry in history
    )


def _thread_topic(operator, thread_id: str) -> str:
    """Best-effort thread title for mails; never breaks sending."""
    try:
        thread = operator.store.get(thread_id)
    except (KeyError, ValueError, OSError):
        return ""
    if not isinstance(thread, dict) or not thread.get("supervisor_thread"):
        return ""
    prompt = thread.get("prompt", "")
    return str(prompt).strip().splitlines()[0][:120] if str(prompt).strip() else ""


_RUN_MAIL_ACTIONS = {
    "awaiting_approval": (
        "It finished and needs your review. Open the operator page, find the "
        "run in Review & approve, and approve or reject the exact change."
    ),
    "waiting_on_owner": (
        "It parked itself with a question for you. Open the operator page, "
        "answer in Review & approve, and the run resumes by itself."
    ),
    "completed": "It is done. No action needed.",
    "failed": "It failed. No action needed unless you want to start it again.",
    "cancelled": "It was cancelled. No action needed.",
}


def format_run_mail(summary: dict, topic: str) -> str:
    """Human-readable run mail: headline, action, then raw details."""
    run = str(summary.get("run_id", ""))[:8]
    status = str(summary.get("status", "unknown"))
    head = f"Run {run} ({topic})" if topic else f"Run {run}"
    if status == "completed" and summary.get("had_questions"):
        action = "It is done after your answers. No action needed."
    else:
        action = _RUN_MAIL_ACTIONS.get(status, "No action needed.")
    lines = [f"{head}: {status}.", "", action]
    error = summary.get("error")
    if status == "failed" and error:
        lines += ["", f"Error: {error}"[:1000]]
    lines += ["", "Details:", json.dumps(summary, indent=2, default=str)]
    return "\n".join(lines)


def notify_plan(operator) -> tuple:
    """Return ``(notify_send, notify_on)`` for the serve drain worker.

    Key run events (``waiting_on_owner``, ``failed``, completed-after-questions)
    mail in every supervision mode whenever SMTP is configured; unattended
    additionally mails every other terminal outcome. Missing SMTP config means
    a silent skip (``None`` sender): never a crash and never dry-run receipt
    spam. Never raises for settings or config problems.
    """
    from remote_operator import notify as notify_mail
    from remote_operator import settings as run_settings
    from remote_operator.notify import load_smtp_config

    try:
        config = load_smtp_config()
    except (ValueError, OSError):
        logger.warning("supervision without SMTP settings; not notifying")
        return None, set(UNATTENDED_NOTIFY_STATUSES)
    if run_settings.load_settings(operator.root)["supervision"] == "unattended":
        notify_on = set(UNATTENDED_NOTIFY_STATUSES)
    else:
        notify_on = set(ATTENDED_NOTIFY_STATUSES)
    receipts = operator.root / "notify_receipts"

    def _send(summary):
        return notify_mail.send_owner_mail(
            to=config["owner"],
            subject=f"[operator] run {summary['status']}: {summary['run_id'][:8]}",
            body=format_run_mail(
                summary, _thread_topic(operator, summary.get("thread_id", ""))
            ),
            config=config,
            dry_run=False,
            receipts_dir=receipts,
        )

    return _send, notify_on


def start_drain_worker(
    operator,
    model_factory,
    *,
    interval_seconds: float = DRAIN_WORKER_INTERVAL_SECONDS,
    max_jobs: int = 10,
    stop_event: threading.Event | None = None,
    status: DrainWorkerStatus | None = None,
) -> threading.Thread:
    """Run queued supervisor jobs in a daemon thread while serve lives.

    The first pass runs immediately (startup backlog after reboot or wake),
    then reconcile + drain + refresh repeats every ``interval_seconds``.
    One bad pass is logged and skipped; the thread only stops on
    ``stop_event`` or process exit. Shares the serve ``operator`` the same
    way request handlers already do; the operator lock serializes writers.
    """
    _check_interval(interval_seconds)
    stop = stop_event or threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            try:
                sender, notify_on = notify_plan(operator)
                runner = SupervisorRunner(operator, notify_send=sender, notify_on=notify_on)
                runner.reconcile_jobs()
                runner.drain(model_factory(), max_jobs=max_jobs)
                runner.refresh_waiting()
            except Exception:
                logger.exception("supervisor drain pass failed")
                if status is not None:
                    status.record_pass(False)
            else:
                if status is not None:
                    status.record_pass(True)
            stop.wait(interval_seconds)

    thread = threading.Thread(target=_loop, name="supervisor-drain", daemon=True)
    thread.start()
    return thread


def supervise_drain_worker(
    operator,
    model_factory,
    *,
    interval_seconds: float = DRAIN_WORKER_INTERVAL_SECONDS,
    max_jobs: int = 10,
    stop_event: threading.Event | None = None,
    status: DrainWorkerStatus | None = None,
) -> tuple[threading.Thread, DrainWorkerStatus]:
    """Keep one drain worker alive, restarting it when its thread dies.

    A per-pass guard already survives ordinary errors; the monitor covers
    the remaining case (a fatal escaping the worker thread), which would
    otherwise silently stop all draining. Returns the monitor thread and
    the shared status. The worker shares ``stop_event`` for clean shutdown.
    """
    _check_interval(interval_seconds)
    holder = status or DrainWorkerStatus()
    stop = stop_event or threading.Event()
    state: dict = {"worker": None}

    def _monitor() -> None:
        while not stop.is_set():
            worker = state["worker"]
            if worker is None or not worker.is_alive():
                if worker is not None:
                    holder.record_restart()
                    logger.warning("supervisor drain worker died; restarting")
                state["worker"] = start_drain_worker(
                    operator,
                    model_factory,
                    interval_seconds=interval_seconds,
                    max_jobs=max_jobs,
                    stop_event=stop,
                    status=holder,
                )
            stop.wait(interval_seconds)

    monitor = threading.Thread(target=_monitor, name="supervisor-drain-monitor", daemon=True)
    monitor.start()
    return monitor, holder


class HostedDrainStatus(DrainWorkerStatus):
    """Pass counters plus per-subject buckets for the hosted sweeper."""

    def __init__(self) -> None:
        super().__init__()
        self.subjects_swept = 0
        self.subjects_drained = 0
        self.subjects_skipped = 0
        self.subjects_busy = 0
        self.subjects_key_missing = 0
        self._offset = 0

    def record_hosted_pass(self, counts: dict) -> None:
        with self._lock:
            self.subjects_swept += counts.get("swept", 0)
            self.subjects_drained += counts.get("drained", 0)
            self.subjects_skipped += counts.get("skipped", 0)
            self.subjects_busy += counts.get("busy", 0)
            self.subjects_key_missing += counts.get("key_missing", 0)

    def claim_slice(self, total: int, limit: int) -> list[int]:
        """Return up to ``limit`` rotating indices over ``total`` subjects."""
        with self._lock:
            if total <= 0:
                return []
            start = self._offset % total
            taken = min(limit, total)
            indices = [(start + index) % total for index in range(taken)]
            self._offset = (start + taken) % total
            return indices

    def snapshot(self, alive: bool) -> dict:
        # Inline the base fields: super().snapshot() would re-acquire the
        # non-reentrant lock.
        with self._lock:
            return {
                "alive": alive,
                "last_pass": self.last_pass,
                "passes": self.passes,
                "errors": self.errors,
                "restarts": self.restarts,
                "subjects_swept": self.subjects_swept,
                "subjects_drained": self.subjects_drained,
                "subjects_skipped": self.subjects_skipped,
                "subjects_busy": self.subjects_busy,
                "subjects_key_missing": self.subjects_key_missing,
            }


def _check_hosted_budgets(max_subjects: int, max_jobs_per_subject: int) -> None:
    if type(max_subjects) is not int or not 1 <= max_subjects <= 256:
        raise ValueError("max_subjects must be an integer between 1 and 256")
    if type(max_jobs_per_subject) is not int or not 1 <= max_jobs_per_subject <= 100:
        raise ValueError("max_jobs_per_subject must be an integer between 1 and 100")


def _hosted_grant(operator, subject, grant, judge_model):
    """Resolve the shared owner grant first, else the subject's judge self-grant."""
    if grant is not None:
        return grant
    if judge_model is None or not subject or not subject.startswith("judge-"):
        return None
    from remote_operator.judge_grants import JudgeGrantBridge

    try:
        return JudgeGrantBridge(operator.store).effective(subject, judge_model)
    except Exception:  # noqa: BLE001 - unreadable grant reads as absent
        logger.debug("judge grant unreadable; falling back to scripted drain")
        return None


def _hosted_actor(operator, active_grant, key_source, actor_factory):
    if actor_factory is not None:
        return actor_factory(operator.store, key_source)
    from remote_operator.meta import MetaModel

    return MetaModel("", active_grant, operator.store, key_source=key_source)


def _sweep_subject(scoped, sessions, directory, *, grant, judge_model, actor_factory,
                   max_jobs_per_subject) -> tuple[str, dict]:
    """Drain one subject workspace; returns (bucket, detail).

    Sequential reconcile, key-gated drain, refresh against that subject's own
    lock only. Never holds two subject locks: each operator call acquires and
    releases before the next begins.
    """
    subject = scoped.subject_for_directory(directory)
    operator = scoped.operator_for_directory(directory)
    runner = SupervisorRunner(operator)
    runner.reconcile_jobs()
    active = _hosted_grant(operator, subject, grant, judge_model)
    key_source = sessions.key_source_for_subject(subject) if subject else None
    key_missing = False
    if active is not None and key_source is not None:
        runner.resume_waiting_on_key()

        def build():
            return _hosted_actor(operator, active, key_source, actor_factory)
    else:
        parked = runner.park_keyless_live_jobs()
        key_missing = bool(parked)
        build = ScriptedModel
    outcomes = []
    for _ in range(max_jobs_per_subject):
        batch = runner.drain(build(), max_jobs=1)
        if not batch:
            break
        outcomes.extend(batch)
        if any(outcome.get("key_missing") for outcome in batch):
            key_missing = True
        if batch[0].get("busy"):
            break
    runner.refresh_waiting()
    busy = any(outcome.get("busy") for outcome in outcomes)
    drained = any(outcome.get("status") not in {"queued"} for outcome in outcomes)
    bucket = "busy" if busy else "drained" if drained else "swept"
    return bucket, {"outcomes": outcomes, "key_missing": key_missing}


def sweep_hosted_once(
    scoped,
    sessions,
    *,
    grant=None,
    judge_model=None,
    actor_factory=None,
    max_subjects: int = HOSTED_MAX_SUBJECTS_PER_PASS,
    max_jobs_per_subject: int = HOSTED_MAX_JOBS_PER_SUBJECT,
    status: HostedDrainStatus | None = None,
) -> dict:
    """Drain queued hosted jobs across subject workspaces, one pass.

    One daemon sweeper iterates subject directories round-robin with bounded
    per-pass budgets; per-subject passes run sequentially so the existing
    single-writer-per-directory invariant holds. A busy or unreadable subject
    is skipped with a counter and never stalls the sweep. Scripted-planned
    jobs drain on the labelled offline reference; live-planned jobs without
    an attached key park as ``waiting_on_key`` and resume on re-attach.
    Outcomes surface through each subject's own run-status APIs; no mail.
    """
    _check_hosted_budgets(max_subjects, max_jobs_per_subject)
    try:
        entries = sorted(scoped.root.iterdir())
    except OSError:
        entries = []
    directories = [path for path in entries if path.is_dir()]
    if isinstance(status, HostedDrainStatus):
        picked = [directories[index] for index in status.claim_slice(len(directories), max_subjects)]
    else:
        picked = directories[:max_subjects]
    counts = {"swept": 0, "drained": 0, "skipped": 0, "busy": 0, "key_missing": 0}
    for directory in picked:
        try:
            bucket, detail = _sweep_subject(
                scoped,
                sessions,
                directory,
                grant=grant,
                judge_model=judge_model,
                actor_factory=actor_factory,
                max_jobs_per_subject=max_jobs_per_subject,
            )
        except Exception:  # one subject must not stall the sweep
            logger.exception("hosted drain skipped a subject workspace")
            counts["skipped"] += 1
            continue
        counts["swept"] += 1
        if bucket == "busy":
            counts["busy"] += 1
        elif bucket == "drained":
            counts["drained"] += 1
        if detail["key_missing"]:
            counts["key_missing"] += 1
    if isinstance(status, HostedDrainStatus):
        status.record_hosted_pass(counts)
    return counts


def start_hosted_drain_worker(
    scoped,
    sessions,
    *,
    grant=None,
    judge_model=None,
    actor_factory=None,
    interval_seconds: float = DRAIN_WORKER_INTERVAL_SECONDS,
    max_subjects: int = HOSTED_MAX_SUBJECTS_PER_PASS,
    max_jobs_per_subject: int = HOSTED_MAX_JOBS_PER_SUBJECT,
    stop_event: threading.Event | None = None,
    status: HostedDrainStatus | None = None,
) -> threading.Thread:
    """Sweep hosted subject workspaces in a daemon thread while serve lives.

    Mirrors :func:`start_drain_worker`: the first pass runs immediately
    (startup backlog after reboot or wake), then repeats every
    ``interval_seconds``. One bad pass is logged and skipped; the thread only
    stops on ``stop_event`` or process exit.
    """
    _check_interval(interval_seconds)
    _check_hosted_budgets(max_subjects, max_jobs_per_subject)
    stop = stop_event or threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            try:
                sweep_hosted_once(
                    scoped,
                    sessions,
                    grant=grant,
                    judge_model=judge_model,
                    actor_factory=actor_factory,
                    max_subjects=max_subjects,
                    max_jobs_per_subject=max_jobs_per_subject,
                    status=status,
                )
            except Exception:
                logger.exception("hosted drain pass failed")
                if status is not None:
                    status.record_pass(False)
            else:
                if status is not None:
                    status.record_pass(True)
            stop.wait(interval_seconds)

    thread = threading.Thread(target=_loop, name="hosted-drain", daemon=True)
    thread.start()
    return thread


def supervise_hosted_drain_worker(
    scoped,
    sessions,
    *,
    grant=None,
    judge_model=None,
    actor_factory=None,
    interval_seconds: float = DRAIN_WORKER_INTERVAL_SECONDS,
    max_subjects: int = HOSTED_MAX_SUBJECTS_PER_PASS,
    max_jobs_per_subject: int = HOSTED_MAX_JOBS_PER_SUBJECT,
    stop_event: threading.Event | None = None,
    status: HostedDrainStatus | None = None,
) -> tuple[threading.Thread, HostedDrainStatus]:
    """Keep one hosted drain worker alive, restarting it when its thread dies."""
    _check_interval(interval_seconds)
    _check_hosted_budgets(max_subjects, max_jobs_per_subject)
    holder = status or HostedDrainStatus()
    stop = stop_event or threading.Event()
    state: dict = {"worker": None}

    def _monitor() -> None:
        while not stop.is_set():
            worker = state["worker"]
            if worker is None or not worker.is_alive():
                if worker is not None:
                    holder.record_restart()
                    logger.warning("hosted drain worker died; restarting")
                state["worker"] = start_hosted_drain_worker(
                    scoped,
                    sessions,
                    grant=grant,
                    judge_model=judge_model,
                    actor_factory=actor_factory,
                    interval_seconds=interval_seconds,
                    max_subjects=max_subjects,
                    max_jobs_per_subject=max_jobs_per_subject,
                    stop_event=stop,
                    status=holder,
                )
            stop.wait(interval_seconds)

    monitor = threading.Thread(target=_monitor, name="hosted-drain-monitor", daemon=True)
    monitor.start()
    return monitor, holder
