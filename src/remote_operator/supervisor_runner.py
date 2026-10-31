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
import sqlite3
import time
from pathlib import Path

from remote_operator.supervisor import RUNS_DIRNAME

MAX_PROMPT_CHARS = 4000
ALLOWANCE_EXHAUSTED = "Model call allowance exhausted"
OPERATOR_BUSY = "Another operator action is active"
TASK_NOT_READY = "Task is not ready; reconcile uncertain effects first"
TERMINAL_JOB = {"completed", "failed", "cancelled"}


class SupervisorRunner:
    """Drain queued supervisor jobs against one operator's store and lock."""

    def __init__(self, operator, *, notify_send=None, notify_on=None):
        """``notify_send`` maps a job summary dict to a notification receipt.

        It is called for terminal job outcomes in ``notify_on``; any error it
        raises is recorded on the job and never breaks the drain.
        """
        self.operator = operator
        self.notify_send = notify_send
        self.notify_on = (
            {"completed", "failed", "cancelled", "awaiting_approval"}
            if notify_on is None
            else set(notify_on)
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

    @staticmethod
    def _write_job(path: Path, job: dict) -> None:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(job), encoding="utf-8")
        tmp.replace(path)

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
        self._thread_event(job, "run_finished", {"run_id": job.get("run_id", path.stem)})
        if status in self.notify_on and self.notify_send is not None:
            try:
                receipt = self.notify_send(
                    {
                        "run_id": job.get("run_id", path.stem),
                        "thread_id": job.get("thread_id", ""),
                        "status": status,
                        "error": job.get("error"),
                    }
                )
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
