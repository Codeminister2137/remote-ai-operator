"""Bounded in-memory visitor credentials;not an account or persistence service."""

import hashlib
import logging
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class SessionUnavailable(ValueError):
    def __init__(self, detail="Session unavailable; reconnect before model work"):
        super().__init__(detail)


class SessionCapacity(ValueError):
    """The process cannot hold another attached visitor key."""

    def __init__(self):
        super().__init__("Session capacity reached;disconnect an existing session first")


def valid_subject(subject):
    """Subjects must come from trusted authentication,not request workspace paths."""
    if not isinstance(subject, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", subject):
        raise ValueError('Invalid authenticated subject; expected format "[A-Za-z0-9_-]{1,128}"')
    return subject


def valid_key(api_key):
    """Accept only a bounded printable credential without whitespace."""
    if (
        not isinstance(api_key, str)
        or not 16 <= len(api_key) <= 8192
        or any(not 33 <= ord(char) <= 126 for char in api_key)
    ):
        raise ValueError("Provider key must be a bounded printable credential without whitespace")
    return api_key


@dataclass(slots=True)
class _Entry:
    subject: str
    expires: float
    key: str | None = field(default=None, repr=False)


class SessionKeys:
    """Keep keys only in this process;all persistent identity/budget state is external.

    A session may exist without a provider key,which lets a hosted visitor browse
    the offline reference before attaching their own credentials. Expiry blocks
    further use of the session. Purge/revoke drops references;Python cannot
    guarantee forensic memory erasure or revoke a request already sent.
    """

    def __init__(self, *, lifetime=1800, capacity=32, clock=time.monotonic):
        if (
            type(lifetime) not in (int, float)
            or not math.isfinite(lifetime)
            or not 0 < lifetime <= 3600
        ):
            raise ValueError("Session lifetime must be finite,positive and at most one hour")
        if type(capacity) is not int or not 1 <= capacity <= 128:
            raise ValueError("Session capacity must be between1 and128")
        self.lifetime = lifetime
        self.capacity = capacity
        self._clock = clock
        self._entries = {}
        self._lock = threading.Lock()

    def __repr__(self):
        return "SessionKeys(credentials=redacted)"

    @property
    def clock(self):
        """The monotonic/wall clock all expiry comparisons in this store use."""
        return self._clock

    @staticmethod
    def _identity(token):
        """Hash a generated or externally signed token;dotted tokens stay opaque."""
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{40,256}", token):
            raise SessionUnavailable()
        return hashlib.sha256(token.encode()).digest()

    def _purge(self):
        expired = [
            token for token, entry in self._entries.items() if entry.expires <= self._clock()
        ]
        for token in expired:
            del self._entries[token]
        return len(expired)

    def _live(self, token):
        entry = self._entries.get(self._identity(token))
        if entry is None:
            raise SessionUnavailable()
        return entry

    def purge_expired(self):
        with self._lock:
            return self._purge()

    def open(self, subject, api_key=None):
        valid_subject(subject)
        if api_key is not None:
            valid_key(api_key)
        with self._lock:
            self._purge()
            if len(self._entries) >= self.capacity:
                raise SessionCapacity()
            token = secrets.token_urlsafe(32)
            identity = self._identity(token)
            if identity in self._entries:
                raise ValueError("Session identifier collision")
            self._entries[identity] = _Entry(subject, self._clock() + self.lifetime, api_key)
            return token

    def subject(self, token):
        with self._lock:
            self._purge()
            return self._live(token).subject

    def attach(self, token, api_key, *, subject=None, expires_at=None):
        """Bind a provider key to a verified session;never returned to callers.

        An externally signed token has no slot yet, so the first attachment
        creates one. Keyless sessions stay stateless, which keeps capacity bound
        to stored credentials rather than visitor count.
        """
        valid_key(api_key)
        if subject is not None:
            valid_subject(subject)
        if expires_at is not None:
            if type(expires_at) not in (int, float):
                raise SessionUnavailable("Invalid session deadline;reconnect to continue")
            try:
                finite = math.isfinite(expires_at)
            except OverflowError:
                raise SessionUnavailable("Invalid session deadline;reconnect to continue")
            if not finite:
                raise SessionUnavailable("Invalid session deadline;reconnect to continue")
        deadline = self._clock() + self.lifetime if expires_at is None else expires_at
        if deadline <= self._clock():
            raise SessionUnavailable("Session expired;reconnect to continue")
        identity = self._identity(token)
        with self._lock:
            self._purge()
            entry = self._entries.get(identity)
            if entry is None:
                if subject is None:
                    raise SessionUnavailable()
                if len(self._entries) >= self.capacity:
                    raise SessionCapacity()
                self._entries[identity] = _Entry(subject, deadline, api_key)
                return
            if subject is not None and not secrets.compare_digest(entry.subject, subject):
                raise SessionUnavailable()
            entry.expires = min(entry.expires, deadline)
            entry.key = api_key

    def drop_key(self, token, *, subject):
        valid_subject(subject)
        with self._lock:
            self._purge()
            entry = self._entries.get(self._identity(token))
            if entry is None:
                return
            if not secrets.compare_digest(entry.subject, subject):
                raise SessionUnavailable()
            entry.key = None

    def has_key(self, token, *, subject):
        valid_subject(subject)
        with self._lock:
            self._purge()
            entry = self._entries.get(self._identity(token))
            if entry is None:
                return False
            if not secrets.compare_digest(entry.subject, subject):
                raise SessionUnavailable()
            return entry.key is not None

    def key_for(self, token, *, subject):
        valid_subject(subject)
        with self._lock:
            self._purge()
            entry = self._live(token)
            if not secrets.compare_digest(entry.subject, subject):
                raise SessionUnavailable()
            if entry.key is None:
                logger.debug("missing credential for subject '%s'", subject)
                raise SessionUnavailable(
                    f"No visitor model key is attached to this session for subject '{subject}'"
                )
            return entry.key

    def revoke(self, token):
        with self._lock:
            self._entries.pop(self._identity(token), None)

    def clear(self):
        with self._lock:
            self._entries.clear()
