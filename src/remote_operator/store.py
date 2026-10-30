"""Transactional local task records with ordered, host-owned events."""

import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path


class Store:
    """Persist one local operator's tasks; never restore live execution implicitly."""

    def __init__(self, path: Path, *, after_commit=None):
        """Open the task database.

        ``after_commit`` runs after each committed write transaction. Hosted
        single-writer deployments pass their volume commit there so a crash
        cannot roll back a recorded reservation, approval or applied effect.
        Read-only queries never call it.
        """
        self.path = path
        self._after_commit = after_commit
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, record TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS allowances (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                    requests INTEGER NOT NULL, reserved_usd TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS controls (
                    task_id TEXT PRIMARY KEY, cancelled INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
                );
            """)
        self._durable()

    def _durable(self):
        if self._after_commit is not None:
            self._after_commit()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, task_id: str) -> dict:
        """Return the task record for ``task_id``.

        Raises ``KeyError`` when the task is unknown.
        """
        with self.connect() as db:
            row = db.execute("SELECT record FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown task: {task_id}")
        return json.loads(row[0])

    def recent_tasks(self, limit: int = 20) -> list[dict]:
        """Return summaries for the most recent tasks.

        ``limit`` is clamped to the inclusive range 1..50. Each summary
        contains ``id``, a truncated ``prompt`` preview and ``status``.
        """
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit must be an integer between 1 and 50")
        with self.connect() as db:
            rows = db.execute(
                "SELECT record FROM tasks ORDER BY rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        summaries = []
        for (body,) in rows:
            task = json.loads(body)
            summaries.append(
                {"id": task["id"], "prompt": task["prompt"][:160], "status": task["status"]}
            )
        return summaries

    def save(self, task: dict, kind: str, payload: dict | None = None) -> None:
        """Persist ``task`` and append an ordered event of ``kind``.

        Enforces the cancellation boundary for ``apply_started`` inside a
        single transaction so a raced cancellation invalidates the approval.
        """
        with self.connect() as db:
            if kind == "apply_started":
                db.execute("BEGIN IMMEDIATE")
                control = db.execute(
                    "SELECT cancelled FROM controls WHERE task_id=?", (task["id"],)
                ).fetchone()
                if control and control[0]:
                    raise ValueError("Cancellation invalidated this approval")
            db.execute(
                "INSERT INTO tasks VALUES (?,?) ON CONFLICT(id) DO UPDATE SET record=?",
                (task["id"], json.dumps(task), json.dumps(task)),
            )
            db.execute(
                "INSERT INTO events(task_id,kind,payload) VALUES (?,?,?)",
                (task["id"], kind, json.dumps(payload or {})),
            )
        self._durable()

    def events(self, task_id: str, after: int = 0) -> list[dict]:
        """Return ordered events for ``task_id`` after ``after`` sequence.

        Validates that the task exists before querying its event log.
        """
        self.get(task_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT sequence,kind,payload FROM events WHERE task_id=? AND sequence>? "
                "ORDER BY sequence",
                (task_id, after),
            ).fetchall()
        return [
            {"sequence": seq, "kind": kind, "payload": json.loads(body)} for seq, kind, body in rows
        ]

    def reserve_allowance(self, grant_id, fingerprint, maximum, reservation, approved):
        """Consume before sending; failed calls remain consumed across restarts."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT fingerprint,requests,reserved_usd FROM allowances WHERE id=?", (grant_id,)
            ).fetchone()
            if row and row[0] != fingerprint:
                raise ValueError("Consumed grant settings cannot be changed")
            used, reserved = (row[1], Decimal(row[2])) if row else (0, Decimal(0))
            next_reserved = reserved + Decimal(reservation)
            if used >= maximum or next_reserved > Decimal(approved):
                raise ValueError("Inference allowance exhausted; no request sent")
            db.execute(
                "INSERT INTO allowances VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE "
                "SET requests=excluded.requests,reserved_usd=excluded.reserved_usd",
                (grant_id, fingerprint, used + 1, str(next_reserved)),
            )
            status = {
                "requests_used": used + 1,
                "requests_limit": maximum,
                "reserved_usd_estimate": str(next_reserved),
                "limit_usd": str(approved),
                "remaining_usd_estimate": str(Decimal(approved) - next_reserved),
            }
        self._durable()
        return status

    def allowance_status(self, grant_id, fingerprint, maximum, approved):
        """Return current allowance usage without consuming a request."""
        with self.connect() as db:
            row = db.execute(
                "SELECT fingerprint,requests,reserved_usd FROM allowances WHERE id=?", (grant_id,)
            ).fetchone()
        if row and row[0] != fingerprint:
            raise ValueError("Consumed grant settings cannot be changed")
        used, reserved = (row[1], Decimal(row[2])) if row else (0, Decimal(0))
        limit = Decimal(approved)
        if used < 0 or not reserved.is_finite() or reserved < 0 or not limit.is_finite():
            raise ValueError("Invalid allowance ledger amount")
        remaining = limit - reserved
        if remaining < 0:
            remaining = Decimal(0)
        return {
            "requests_used": used,
            "requests_limit": maximum,
            "reserved_usd_estimate": str(reserved),
            "limit_usd": str(limit),
            "remaining_usd_estimate": str(remaining),
        }

    def request_cancel(self, task_id):
        """Record a cancellation request for ``task_id``.

        Rejects cancellation when the task is already applying or terminal.
        """
        self.get(task_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = json.loads(
                db.execute("SELECT record FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
            )
            if current["status"] in {"applying", "completed", "rejected"}:
                raise ValueError("Action already started or is terminal; cancellation refused")
            db.execute(
                "INSERT INTO controls VALUES (?,1) ON CONFLICT(task_id) DO UPDATE SET cancelled=1",
                (task_id,),
            )
            db.execute(
                "INSERT INTO events(task_id,kind,payload) VALUES (?,?,?)",
                (task_id, "cancellation_requested", "{}"),
            )
        self._durable()

    def cancelled(self, task_id):
        """Return whether ``task_id`` has been marked for cancellation."""
        with self.connect() as db:
            row = db.execute(
                "SELECT cancelled FROM controls WHERE task_id=?", (task_id,)
            ).fetchone()
        return bool(row and row[0])
