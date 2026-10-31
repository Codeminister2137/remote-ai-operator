"""Judge identity for hosted evaluation: signup gated by access code, login bound to stable subjects.

Judges are the only hosted users who may configure their own inference
allowance. Accounts carry a username plus a salted password hash; a successful
login maps to the stable subject ``judge-<username>``, so a returning judge
keeps the same isolated workspace. Signup requires the shared access code, so
registration never widens the deployment's access boundary. Password checks are
throttled per username (process-local, like session revocation).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{3,32}")
SUBJECT_PREFIX = "judge-"
Pbkdf2_ITERATIONS = 600_000
MAX_FAILURES = 10
LOCKOUT_SECONDS = 900


class JudgeAuthError(ValueError):
    """A judge credential check failed; never reveals which part was wrong."""


class JudgeLockedOut(JudgeAuthError):
    """Too many failed attempts; the username is temporarily locked."""


class JudgeAccounts:
    """Persist judge credentials in one host-owned SQLite file."""

    def __init__(self, path: Path, *, after_commit=None, clock=None):
        """``after_commit`` runs after each committed write (volume commit)."""
        self.path = path
        self._after_commit = after_commit
        self._clock = clock or time.time
        self._throttle: dict[str, list] = {}
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS judge_accounts (
                    username TEXT PRIMARY KEY,
                    salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created REAL NOT NULL
                )
                """
            )
        # No durability commit here: schema creation is idempotent and every
        # credential write commits through signup.

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

    @staticmethod
    def _check_username(username: str) -> None:
        if not isinstance(username, str) or not USERNAME_PATTERN.fullmatch(username):
            raise JudgeAuthError("Username must be 3-32 letters, digits, - or _")

    @staticmethod
    def _check_password(password: str) -> None:
        if (
            not isinstance(password, str)
            or not 8 <= len(password) <= 256
            or any(ord(char) < 32 or ord(char) == 127 for char in password)
        ):
            raise JudgeAuthError("Password must be 8-256 characters without control codes")

    @staticmethod
    def subject(username: str) -> str:
        JudgeAccounts._check_username(username)
        return SUBJECT_PREFIX + username

    def signup(self, username: str, password: str) -> dict:
        """Create a judge account; raises JudgeAuthError when taken/invalid."""
        self._check_username(username)
        self._check_password(password)
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), Pbkdf2_ITERATIONS
        ).hex()
        created = self._clock()
        try:
            with self.connect() as db:
                db.execute(
                    "INSERT INTO judge_accounts VALUES (?,?,?,?)",
                    (username, salt, digest, created),
                )
        except sqlite3.IntegrityError:
            raise JudgeAuthError("Username is taken") from None
        self._durable()
        return {"username": username, "subject": SUBJECT_PREFIX + username}

    def verify(self, username: str, password: str) -> dict:
        """Check credentials with per-username throttling; raises JudgeAuthError."""
        self._check_username(username)
        if not isinstance(password, str):
            raise JudgeAuthError("Access refused")
        now = self._clock()
        with self._lock:
            state = self._throttle.get(username, [0, 0.0])
            if state[1] > now:
                raise JudgeLockedOut("Too many attempts; try later")
        with self.connect() as db:
            row = db.execute(
                "SELECT salt, password_hash FROM judge_accounts WHERE username=?", (username,)
            ).fetchone()
        candidate = (
            hashlib.pbkdf2_hmac(
                "sha256", password.encode(), bytes.fromhex(row[0]), Pbkdf2_ITERATIONS
            ).hex()
            if row
            else secrets.token_hex(32)
        )
        # Dummy comparison on unknown users keeps timing uniform.
        expected = row[1] if row else secrets.token_hex(32)
        if not hmac.compare_digest(candidate, expected):
            with self._lock:
                fails, _ = self._throttle.get(username, [0, 0.0])
                fails += 1
                locked_until = now + LOCKOUT_SECONDS if fails >= MAX_FAILURES else 0.0
                self._throttle[username] = [fails, locked_until]
            raise JudgeAuthError("Access refused")
        with self._lock:
            self._throttle.pop(username, None)
        return {"username": username, "subject": SUBJECT_PREFIX + username}
