"""Neutral external pending-action contract for Workbench-side integrations.

External actions carry only digests and a human-readable description; the actual
source, diff or evidence never enters the operator. A caller (e.g. AI Workbench)
creates an action, polls its status, and applies the real change itself after the
owner approves through the operator phone/web UI.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
import uuid
from dataclasses import asdict, dataclass, replace
from typing import Any

from remote_operator.store import Store

MAX_DESCRIPTION_LENGTH = 4000
DEFAULT_EXPIRY_SECONDS = 600


def _serialize(action: ExternalAction) -> str:
    """Serialize an ExternalAction to a canonical JSON string."""
    return json.dumps(action.to_dict(), sort_keys=True)


@dataclass(frozen=True, slots=True)
class ExternalAction:
    """One pending decision published by an external system.

    Attributes:
        id: 32-character hex identifier generated at submission.
        status: Lifecycle state — ``submitted``, ``approved``, ``rejected``,
            ``cancelled``, or ``expired``.
        description: Human-readable summary of the proposed change.
        candidate_digest: SHA-256 hex digest of the proposed candidate.
        base_digest: SHA-256 hex digest of the base being compared against.
        evidence_digest: SHA-256 hex digest of supporting evidence.
        nonce: Secret token required to approve the action.
        expires: Unix timestamp (seconds) when the submitted action expires.
        created_at: Unix timestamp (seconds) when the action was created.
        resolved_at: Unix timestamp when the action reached a terminal state,
            or ``None`` while still submitted.
    """

    id: str
    status: str
    description: str
    candidate_digest: str
    base_digest: str
    evidence_digest: str
    nonce: str
    expires: float
    created_at: float
    resolved_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a dictionary representation of the action.

        Returns:
            Dict containing all dataclass fields suitable for JSON serialization.
        """
        return asdict(self)


class ExternalActionBridge:
    """Store and resolve external pending actions in the operator database.

    Provides a digest-only contract: callers submit descriptions and SHA-256
    digests, and the bridge persists them until the owner approves, rejects,
    cancels, or the action expires.

    Args:
        store: Underlying :class:`remote_operator.store.Store` instance used
            for durable SQLite persistence.
    """

    def __init__(self, store: Store) -> None:
        self._store = store
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Ensure the ``external_actions`` table exists."""
        with self._store.connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS external_actions (
                    id TEXT PRIMARY KEY,
                    record TEXT NOT NULL
                )
                """
            )

    def submit(
        self,
        description: str,
        candidate_digest: str,
        base_digest: str,
        evidence_digest: str,
        *,
        expires_seconds: int = DEFAULT_EXPIRY_SECONDS,
        now: float | None = None,
    ) -> ExternalAction:
        """Submit a new external action for owner review.

        Validates inputs, generates an id and approval nonce, and persists
        the action with ``submitted`` status.

        Args:
            description: Human-readable summary; must be non-empty and at most
                ``MAX_DESCRIPTION_LENGTH`` characters.
            candidate_digest: 64-character hex SHA-256 digest of the candidate.
            base_digest: 64-character hex SHA-256 digest of the base.
            evidence_digest: 64-character hex SHA-256 digest of evidence.
            expires_seconds: Lifetime in seconds, between 30 and 3600 inclusive.
                Defaults to ``DEFAULT_EXPIRY_SECONDS``.
            now: Optional override for current Unix time (used in tests);
                defaults to ``time.time()`` when ``None``.

        Returns:
            The newly created :class:`ExternalAction` with status ``submitted``.

        Raises:
            ValueError: If description or digests are invalid, or expiry is
                out of range.
        """
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Description is required")
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise ValueError(f"Description exceeds {MAX_DESCRIPTION_LENGTH} characters")
        for digest in (candidate_digest, base_digest, evidence_digest):
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError("Digests must be 64-character hex SHA-256 strings")
            try:
                digest.encode("ascii")
            except UnicodeEncodeError:
                raise ValueError("Digests must be 64-character hex SHA-256 strings")
            if any(c not in "0123456789abcdefABCDEF" for c in digest):
                raise ValueError("Digests must be 64-character hex SHA-256 strings")
        if not isinstance(expires_seconds, int) or not 30 <= expires_seconds <= 3600:
            raise ValueError("Expiry must be between 30 and 3600 seconds")
        created = time.time() if now is None else now
        action = ExternalAction(
            id=uuid.uuid4().hex,
            status="submitted",
            description=description,
            candidate_digest=candidate_digest,
            base_digest=base_digest,
            evidence_digest=evidence_digest,
            nonce=secrets.token_urlsafe(24),
            expires=created + expires_seconds,
            created_at=created,
        )
        with self._store.connect() as db:
            db.execute(
                "INSERT INTO external_actions(id, record) VALUES (?, ?)",
                (action.id, _serialize(action)),
            )
        self._store._durable()
        return action

    def get(self, action_id: str, *, now: float | None = None) -> ExternalAction:
        """Retrieve a single action by identifier.

        Lazily expires the action if it is still ``submitted`` and the current
        time has reached or passed its expiry.

        Args:
            action_id: 32-character alphanumeric action identifier.
            now: Optional override for current Unix time; defaults to
                ``time.time()`` when ``None``.

        Returns:
            The matching :class:`ExternalAction`, potentially transitioned to
            ``expired`` if its deadline has passed.

        Raises:
            ValueError: If the identifier format is invalid.
            KeyError: If no action with the given id exists.
        """
        if not isinstance(action_id, str) or not action_id.isalnum() or len(action_id) != 32:
            raise ValueError("Invalid action identifier")
        with self._store.connect() as db:
            row = db.execute(
                "SELECT record FROM external_actions WHERE id=?", (action_id,)
            ).fetchone()
        if row is None:
            raise KeyError("Unknown external action")
        action = ExternalAction(**json.loads(row[0]))
        now_val = time.time() if now is None else now
        if action.status == "submitted" and now_val >= action.expires:
            action = self._expire(action, now_val)
        return action

    def recent(self, limit: int = 20, *, now: float | None = None) -> list[ExternalAction]:
        """List the most recently submitted actions.

        Lazily expires any returned actions that have passed their deadline.

        Args:
            limit: Maximum number of actions to return, between 1 and 50
                inclusive. Defaults to 20.
            now: Optional override for current Unix time; defaults to
                ``time.time()`` when ``None``.

        Returns:
            List of :class:`ExternalAction` objects ordered from newest to
            oldest (by insertion order).

        Raises:
            ValueError: If ``limit`` is not an int in the allowed range.
        """
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit must be an integer between 1 and 50")
        with self._store.connect() as db:
            rows = db.execute(
                "SELECT record FROM external_actions ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        now_val = time.time() if now is None else now
        actions = []
        for (body,) in rows:
            action = ExternalAction(**json.loads(body))
            if action.status == "submitted" and now_val >= action.expires:
                action = self._expire(action, now_val)
            actions.append(action)
        return actions

    def approve(self, action_id: str, nonce: str, *, now: float | None = None) -> ExternalAction:
        """Approve a pending action.

        Requires the secret nonce issued at submission time. Fails if the
        action has expired, is not in ``submitted`` state, or the nonce is
        incorrect.

        Args:
            action_id: 32-character alphanumeric action identifier.
            nonce: Secret approval token that must match the stored nonce.
            now: Optional override for current Unix time; defaults to
                ``time.time()`` when ``None``.

        Returns:
            The updated :class:`ExternalAction` with status ``approved`` and
            ``resolved_at`` set.

        Raises:
            ValueError: If the identifier or nonce is invalid, the action has
                expired, or it is not awaiting approval.
            KeyError: If no action with the given id exists.
        """
        if not isinstance(action_id, str) or not action_id.isalnum() or len(action_id) != 32:
            raise ValueError("Invalid action identifier")
        now_val = time.time() if now is None else now
        with self._store.connect() as db:
            row = db.execute(
                "SELECT record FROM external_actions WHERE id=?", (action_id,)
            ).fetchone()
            if row is None:
                raise KeyError("Unknown external action")
            old_raw: str = row[0]
            action = ExternalAction(**json.loads(old_raw))
            if action.status == "submitted" and now_val >= action.expires:
                # Expiration must commit before this operation raises; an update
                # in this connection would roll back on the refusal exception.
                self._expire(action, now_val)
                raise ValueError(f"Approval expired for action {action_id}")
            if action.status != "submitted":
                raise ValueError(
                    f"Action {action_id} is not awaiting approval (status={action.status})"
                )
            if now_val >= action.expires:
                raise ValueError(f"Approval expired for action {action_id}")
            if not isinstance(nonce, str):
                raise ValueError("Invalid approval nonce")  # noqa: TRY004
            try:
                nonce_ok = secrets.compare_digest(action.nonce, nonce)
            except TypeError:
                raise ValueError("Invalid approval nonce")
            if not nonce_ok:
                raise ValueError("Invalid approval nonce")
            resolved = replace(
                action,
                status="approved",
                resolved_at=now_val,
            )
            new_raw = _serialize(resolved)
            cur = db.execute(
                "UPDATE external_actions SET record=? WHERE id=? AND record=?",
                (new_raw, resolved.id, old_raw),
            )
            if cur.rowcount != 1:
                current = db.execute(
                    "SELECT record FROM external_actions WHERE id=?", (action_id,)
                ).fetchone()
                if current is None:
                    raise KeyError("Unknown external action")
                current_action = ExternalAction(**json.loads(current[0]))
                if current_action.status != "submitted":
                    raise ValueError(
                        f"Action {action_id} is not awaiting approval (status={current_action.status})"
                    )
                raise ValueError(
                    f"Action {action_id} is not awaiting approval (status={current_action.status})"
                )
        self._store._durable()
        return resolved

    def reject(self, action_id: str, *, now: float | None = None) -> ExternalAction:
        """Reject a pending action.

        Transitions a ``submitted`` action to ``rejected``. Expired or already
        resolved actions cannot be rejected.

        Args:
            action_id: 32-character alphanumeric action identifier.
            now: Optional override for current Unix time; defaults to
                ``time.time()`` when ``None``.

        Returns:
            The updated :class:`ExternalAction` with status ``rejected``.

        Raises:
            ValueError: If the identifier is invalid, the action has expired,
                or it is not in a rejectable state.
            KeyError: If no action with the given id exists.
        """
        if not isinstance(action_id, str) or not action_id.isalnum() or len(action_id) != 32:
            raise ValueError("Invalid action identifier")
        now_val = time.time() if now is None else now
        with self._store.connect() as db:
            row = db.execute(
                "SELECT record FROM external_actions WHERE id=?", (action_id,)
            ).fetchone()
            if row is None:
                raise KeyError("Unknown external action")
            old_raw: str = row[0]
            action = ExternalAction(**json.loads(old_raw))
            if action.status == "submitted" and now_val >= action.expires:
                self._expire(action, now_val)
                raise ValueError("Action cannot be rejected in this state")
            if action.status != "submitted":
                raise ValueError("Action cannot be rejected in this state")
            resolved = replace(
                action,
                status="rejected",
                resolved_at=now_val,
            )
            new_raw = _serialize(resolved)
            cur = db.execute(
                "UPDATE external_actions SET record=? WHERE id=? AND record=?",
                (new_raw, resolved.id, old_raw),
            )
            if cur.rowcount != 1:
                current = db.execute(
                    "SELECT record FROM external_actions WHERE id=?", (action_id,)
                ).fetchone()
                if current is None:
                    raise KeyError("Unknown external action")
                current_action = ExternalAction(**json.loads(current[0]))
                if current_action.status != "submitted":
                    raise ValueError("Action cannot be rejected in this state")
                raise ValueError("Action cannot be rejected in this state")
        self._store._durable()
        return resolved

    def cancel(self, action_id: str, *, now: float | None = None) -> ExternalAction:
        """Cancel a pending action.

        Transitions a ``submitted`` action to ``cancelled``. Expired or already
        resolved actions cannot be cancelled.

        Args:
            action_id: 32-character alphanumeric action identifier.
            now: Optional override for current Unix time; defaults to
                ``time.time()`` when ``None``.

        Returns:
            The updated :class:`ExternalAction` with status ``cancelled``.

        Raises:
            ValueError: If the identifier is invalid, the action has expired,
                or it is not in a cancellable state.
            KeyError: If no action with the given id exists.
        """
        if not isinstance(action_id, str) or not action_id.isalnum() or len(action_id) != 32:
            raise ValueError("Invalid action identifier")
        now_val = time.time() if now is None else now
        with self._store.connect() as db:
            row = db.execute(
                "SELECT record FROM external_actions WHERE id=?", (action_id,)
            ).fetchone()
            if row is None:
                raise KeyError("Unknown external action")
            old_raw: str = row[0]
            action = ExternalAction(**json.loads(old_raw))
            if action.status == "submitted" and now_val >= action.expires:
                self._expire(action, now_val)
                raise ValueError("Action cannot be cancelled in this state")
            if action.status != "submitted":
                raise ValueError("Action cannot be cancelled in this state")
            resolved = replace(
                action,
                status="cancelled",
                resolved_at=now_val,
            )
            new_raw = _serialize(resolved)
            cur = db.execute(
                "UPDATE external_actions SET record=? WHERE id=? AND record=?",
                (new_raw, resolved.id, old_raw),
            )
            if cur.rowcount != 1:
                current = db.execute(
                    "SELECT record FROM external_actions WHERE id=?", (action_id,)
                ).fetchone()
                if current is None:
                    raise KeyError("Unknown external action")
                current_action = ExternalAction(**json.loads(current[0]))
                if current_action.status != "submitted":
                    raise ValueError("Action cannot be cancelled in this state")
                raise ValueError("Action cannot be cancelled in this state")
        self._store._durable()
        return resolved

    def _expire(self, action: ExternalAction, now_val: float | None = None) -> ExternalAction:
        """Mark a submitted action as expired and persist the change."""
        old_raw = _serialize(action)
        resolved_at = time.time() if now_val is None else now_val
        expired = replace(action, status="expired", resolved_at=resolved_at)
        new_raw = _serialize(expired)
        with self._store.connect() as db:
            cur = db.execute(
                "UPDATE external_actions SET record=? WHERE id=? AND record=?",
                (new_raw, expired.id, old_raw),
            )
            if cur.rowcount != 1:
                __import__("logging").getLogger(__name__).debug(
                    "retry/concurrent-update detected for action %s rowcount=%s",
                    action.id,
                    cur.rowcount,
                )
                # Stale read: another writer already resolved this action.
                # Never overwrite the terminal state; return the current record.
                row = db.execute(
                    "SELECT record FROM external_actions WHERE id=?", (action.id,)
                ).fetchone()
                if row is None:
                    raise KeyError("Unknown external action")
                return ExternalAction(**json.loads(row[0]))
        self._store._durable()
        logging.getLogger(__name__).debug("external action %s expired", action.id)
        return expired
