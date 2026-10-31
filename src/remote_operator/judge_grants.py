"""Judge-owned inference allowances for hosted evaluation.

A judge configures their own finite grant (request count and USD cap) against
their own provider key; the owner never pays for judge inference. The grant
lives in the judge's own isolated task store, so allowances, reservations and
expiry never cross judges. The model stays server-pinned: judges choose limits,
never the model. Expired grants read as absent and the session falls back to
the labelled offline reference until the judge sets fresh limits.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal, InvalidOperation

from remote_operator.meta import InferenceGrant

GRANT_ROW_ID = "grant"
GRANT_DAYS = 30
DEFAULT_MAX_INPUT_BYTES = 262144
DEFAULT_MAX_OUTPUT_TOKENS = 16384
DEFAULT_INPUT_USD_PER_MILLION = "1.25"
DEFAULT_OUTPUT_USD_PER_MILLION = "4.25"


class JudgeGrantBridge:
    """Store one judge's self-configured grant in their own task database."""

    def __init__(self, store):
        self._store = store
        with self._store.connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS judge_grant (
                    id TEXT PRIMARY KEY,
                    record TEXT NOT NULL
                )
                """
            )

    def get(self) -> dict | None:
        """Return the stored grant record, or None when absent."""
        with self._store.connect() as db:
            row = db.execute(
                "SELECT record FROM judge_grant WHERE id=?", (GRANT_ROW_ID,)
            ).fetchone()
        if row is None:
            return None
        record = json.loads(row[0])
        return record if isinstance(record, dict) else None

    def set(self, params: dict, *, model: str, now: float | None = None) -> dict:
        """Validate judge-supplied limits and persist the grant record.

        Raises ValueError on any invalid limit. Submission records the judge's
        explicit acceptance of local spending limits for their own key.
        """
        if not isinstance(params, dict):
            raise TypeError("Grant limits are required")
        current = time.time() if now is None else now
        max_requests = params.get("max_requests")
        approved_usd = params.get("approved_usd")
        if type(max_requests) is not int or not 1 <= max_requests <= 100:
            raise ValueError("max_requests must be an integer between 1 and 100")
        try:
            budget = Decimal(approved_usd) if isinstance(approved_usd, str) else Decimal("nan")
        except InvalidOperation:
            raise ValueError("approved_usd must be a decimal string") from None
        if not budget.is_finite() or budget <= 0:
            raise ValueError("approved_usd must be positive and finite")
        max_output_tokens = params.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 16384:
            raise ValueError("max_output_tokens must be an integer between 1 and 16384")
        prices = {}
        for name, default in (
            ("input_usd_per_million", DEFAULT_INPUT_USD_PER_MILLION),
            ("output_usd_per_million", DEFAULT_OUTPUT_USD_PER_MILLION),
        ):
            value = params.get(name, default)
            try:
                number = Decimal(value) if isinstance(value, str) else Decimal("nan")
            except InvalidOperation:
                raise ValueError(f"{name} must be a decimal string") from None
            if not number.is_finite() or number <= 0:
                raise ValueError(f"{name} must be positive and finite")
            prices[name] = value
        record = {
            "max_requests": max_requests,
            "max_input_bytes": DEFAULT_MAX_INPUT_BYTES,
            "max_output_tokens": max_output_tokens,
            "approved_usd": approved_usd,
            **prices,
            "updated": current,
        }
        with self._store.connect() as db:
            db.execute(
                "INSERT INTO judge_grant VALUES (?,?) ON CONFLICT(id) DO UPDATE SET record=?",
                (GRANT_ROW_ID, json.dumps(record), json.dumps(record)),
            )
        self._store._durable()
        return record

    def effective(self, subject: str, model: str, *, now: float | None = None):
        """Return a validated InferenceGrant, or None when unusable.

        Only judge subjects resolve here; anonymous visitors always read None.
        Records that fail validation (including expiry) read as None so the
        session falls back to the offline reference.
        """
        if not subject.startswith("judge-"):
            return None
        record = self.get()
        if record is None:
            return None
        current = time.time() if now is None else now
        updated = record.get("updated", 0)
        if not isinstance(updated, (int, float)) or updated > current:
            return None
        try:
            grant = InferenceGrant(
                grant_id=subject,
                model=model,
                expires_at=updated + GRANT_DAYS * 86400,
                max_requests=record["max_requests"],
                max_input_bytes=record["max_input_bytes"],
                max_output_tokens=record["max_output_tokens"],
                approved_usd=record["approved_usd"],
                input_usd_per_million=record["input_usd_per_million"],
                output_usd_per_million=record["output_usd_per_million"],
                credits_verified=True,
                local_spending_limit_accepted=True,
            )
            grant.check()
        except (ValueError, TypeError, KeyError):
            return None
        return grant
