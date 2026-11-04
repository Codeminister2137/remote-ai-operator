"""Agent inbox: pollable owner-attention queue over the global event log.

An external supervisor polls ``GET /api/supervisor/inbox?since=<seq>`` to learn
exactly what needs attention: new owner thread messages, parked owner
questions, and failed/completed runs. The cursor is the global ``events``
sequence, so unrelated activity still advances it; each item carries its own
``seq`` for stable resume. All enrichment is best-effort: a missing thread,
task or run file skips that item without failing the poll.
"""

import json
import sqlite3

from remote_operator.supervisor import RUNS_DIRNAME

INBOX_KINDS = ("thread_message", "question_asked", "run_finished")
INBOX_RUN_STATUSES = ("failed", "completed")
DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def read_inbox(operator, *, since: int = 0, limit: int = DEFAULT_LIMIT) -> dict:
    """Return ``{"cursor": seq, "items": [...]}`` with attention items after ``since``.

    ``cursor`` is the current tip of the global event log (even when no item
    matched, so the poller always advances). Items are ordered by ``seq``.
    """
    if type(since) is not int or since < 0:
        raise ValueError("since must be a nonnegative integer")
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be an integer between 1 and {MAX_LIMIT}")
    with operator.store.connect() as db:
        rows = db.execute(
            "SELECT sequence,task_id,kind,payload FROM events WHERE sequence>? "
            "AND kind IN (?,?,?) ORDER BY sequence LIMIT ?",
            (since, *INBOX_KINDS, limit),
        ).fetchall()
        tip = db.execute("SELECT MAX(sequence) FROM events").fetchone()[0] or 0
    items = []
    for seq, task_id, kind, payload in rows:
        try:
            data = json.loads(payload)
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if kind == "thread_message":
            item = _owner_message(operator, seq, task_id, data)
        elif kind == "question_asked":
            item = _parked_question(operator, seq, task_id, data)
        else:
            item = _terminal_run(operator, seq, task_id, data)
        if item is not None:
            items.append(item)
    return {"cursor": tip, "items": items}


def _user_positions(history: list) -> list[int]:
    """Positions of user turns in a thread history."""
    return [
        pos
        for pos, turn in enumerate(history)
        if isinstance(turn, dict) and turn.get("role") == "user"
    ]


def _user_turn_index(operator, thread_id: str, seq: int, user_count: int) -> int:
    """0-based index of the user turn that produced the event at ``seq``.

    Falls back to the latest user turn when the event log cannot be
    counted; never raises.
    """
    if user_count <= 0:
        return 0
    try:
        with operator.store.connect() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM events WHERE task_id=? AND kind='thread_message'"
                " AND sequence<=?",
                (thread_id, seq),
            ).fetchone()[0]
    except (OSError, sqlite3.Error, ValueError):
        return user_count - 1
    if isinstance(count, int) and 1 <= count <= user_count:
        return count - 1
    return user_count - 1


def _user_turn_at(operator, thread_id: str, seq: int, history: list) -> str:
    """Return the user turn that produced the thread_message event at ``seq``.

    Falls back to the latest user turn when the event log cannot be counted;
    never raises.
    """
    positions = _user_positions(history)
    if not positions:
        return ""
    index = _user_turn_index(operator, thread_id, seq, len(positions))
    turn = history[positions[index]]
    return str(turn.get("text", ""))[:500] if isinstance(turn, dict) else ""


def _in_reply_to(operator, thread_id: str, seq: int, history: list) -> str:
    """Latest assistant turn before the answered user turn (may be empty).

    Gives pollers the question/options the owner replied to, so terse
    answers ("do A and C") stay interpretable. Best-effort; never raises.
    """
    try:
        positions = _user_positions(history)
        if not positions:
            return ""
        index = _user_turn_index(operator, thread_id, seq, len(positions))
        for turn in reversed(history[: positions[index]]):
            if isinstance(turn, dict) and turn.get("role") == "assistant":
                return str(turn.get("text", ""))[:500]
    except (OSError, sqlite3.Error, ValueError, IndexError):
        pass
    return ""


def _owner_message(operator, seq: int, thread_id: str, payload: dict) -> dict | None:
    try:
        thread = operator.store.get(thread_id)
    except (KeyError, ValueError, OSError, sqlite3.Error):
        return None
    if not isinstance(thread, dict) or not thread.get("supervisor_thread"):
        return None
    return {
        "seq": seq,
        "type": "owner_message",
        "thread_id": thread_id,
        "topic": str(thread.get("prompt", ""))[:160],
        "text": _user_turn_at(operator, thread_id, seq, thread.get("history") or []),
        "in_reply_to": _in_reply_to(operator, thread_id, seq, thread.get("history") or []),
        "route": str(payload.get("route", ""))[:32],
    }


def _linked_run(operator, task_id: str) -> str | None:
    """Return the run id linked to ``task_id``, preferring the parked one."""
    try:
        paths = sorted((operator.root / RUNS_DIRNAME).glob("*.json"))
    except OSError:
        return None
    fallback = None
    for path in paths:
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(job, dict) or job.get("task_id") != task_id:
            continue
        if job.get("status") == "waiting_on_owner":
            return job.get("run_id", path.stem)
        fallback = fallback or job.get("run_id", path.stem)
    return fallback


def _parked_question(operator, seq: int, task_id: str, payload: dict) -> dict | None:
    try:
        task = operator.store.get(task_id)
    except (KeyError, ValueError, OSError, sqlite3.Error):
        return None
    if not isinstance(task, dict):
        return None
    question = payload.get("question", "") or task.get("pending_question", "") or ""
    return {
        "seq": seq,
        "type": "parked_question",
        "task_id": task_id,
        "question": str(question)[:500],
        "run_id": _linked_run(operator, task_id),
        "answered": task.get("status") != "waiting_on_owner",
    }


def _terminal_run(operator, seq: int, thread_id: str, payload: dict) -> dict | None:
    run_id = payload.get("run_id", "")
    if not isinstance(run_id, str) or not run_id:
        return None
    try:
        job = json.loads(
            (operator.root / RUNS_DIRNAME / f"{run_id}.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(job, dict):
        return None
    # Event-time status: a run that passed through awaiting_approval before
    # completing must not surface twice. Old payloads without status fall back
    # to the current job status.
    status = payload.get("status") or job.get("status")
    if status not in INBOX_RUN_STATUSES:
        return None
    error = job.get("error")
    return {
        "seq": seq,
        "type": "run",
        "run_id": job.get("run_id", run_id),
        "thread_id": job.get("thread_id", thread_id),
        "status": status,
        "error": str(error)[:1000] if error else None,
        "had_questions": bool(job.get("had_questions", False)),
    }
