"""Hosted visitor sessions:one shared access code,no visitor accounts.

The browser holds only a signed, expiry-bound session token. The subject used to
select a principal's task store is generated here and carried inside the
signature,never accepted from the client,so a browser cannot choose another
visitor's workspace. Provider keys stay in process memory and are never login
identity,storage or a billing fallback.

A deployment with a stable secret(``OPERATOR_SESSION_SECRET``) keeps a browser's
subject across a container restart,so committed task state remains reachable.
Without a stable secret sessions end with the process;the token is still
unforgeable. Signing does not persist any credential.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time

from remote_operator.credentials import SessionKeys, SessionUnavailable, valid_subject

_TOKEN = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_MAX_TOKEN = 512


class AccessRefused(ValueError):
    """A shared access-code check failed;never reveals which part was wrong."""

    def __init__(self):
        super().__init__("Access code refused")


def _encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class HostedSessions:
    """Issue signed sessions after an explicit access-code check."""

    def __init__(
        self,
        access_code,
        *,
        sessions=None,
        secret=None,
        lifetime=1800,
        capacity=32,
        clock=None,
    ):
        if (
            not isinstance(access_code, str)
            or not 16 <= len(access_code) <= 256
            or any(not 33 <= ord(char) <= 126 for char in access_code)
        ):
            raise ValueError("Access code must be 16-256 printable characters")
        if isinstance(secret, str):
            secret = secret.encode()
        secret = secret or secrets.token_bytes(32)
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("Session signing secret must be at least 32 bytes")
        self._code = access_code
        self._secret = secret
        if sessions is None:
            sessions = SessionKeys(lifetime=lifetime, capacity=capacity, clock=clock or time.time)
        self._clock = clock or sessions.clock
        self._revoked = {}
        self._lock = threading.RLock()
        self.sessions = sessions

    def __repr__(self):
        return "HostedSessions(access_code=redacted)"

    @property
    def lifetime(self):
        return self.sessions.lifetime

    @property
    def capacity(self):
        return self.sessions.capacity

    def _signature(self, body):
        return hmac.new(self._secret, body.encode(), hashlib.sha256).digest()

    def _issue(self, subject):
        valid_subject(subject)
        expires = int(self._clock() + self.sessions.lifetime)
        body = _encode(
            json.dumps(
                {"s": subject, "e": expires, "n": secrets.token_urlsafe(8)},
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        )
        return body + "." + _encode(self._signature(body)), expires

    def _claims(self, token):
        """Verify signature and expiry before trusting any value inside the token."""
        if not isinstance(token, str) or len(token) > _MAX_TOKEN or not _TOKEN.fullmatch(token):
            raise SessionUnavailable()
        body, signature = token.split(".")
        try:
            provided = _decode(signature)
            raw = _decode(body)
            claims = json.loads(raw)
        except (ValueError, TypeError, json.JSONDecodeError):
            raise SessionUnavailable() from None
        # Reject non-canonical base64: otherwise a re-encoded signature decodes to
        # the same bytes and would evade revocation keyed on the exact token.
        if not hmac.compare_digest(_encode(provided), signature) or not hmac.compare_digest(
            _encode(raw), body
        ):
            raise SessionUnavailable()
        if not hmac.compare_digest(provided, self._signature(body)):
            raise SessionUnavailable()
        if set(claims) != {"s", "e", "n"} or not isinstance(claims["e"], int):
            raise SessionUnavailable()
        valid_subject(claims["s"])
        if claims["e"] <= self._clock():
            sid = self._identity(token).hex()[:8]
            raise SessionUnavailable(f"Session {sid} expired;reconnect to continue")
        identity = self._identity(token)
        with self._lock:
            revoked = self._revoked.get(identity)
            if revoked is not None:
                if revoked > self._clock():
                    raise SessionUnavailable()
                self._revoked.pop(identity, None)
        return claims["s"], claims["e"], identity

    @staticmethod
    def _identity(token):
        return hashlib.sha256(token.encode()).digest()

    def open(self, code):
        """Exchange the shared code for a signed session token and server subject."""
        if not isinstance(code, str) or not code.isascii():
            raise AccessRefused()
        if not secrets.compare_digest(code, self._code):
            raise AccessRefused()
        token, _ = self._issue("visitor-" + secrets.token_hex(8))
        return token

    def subject(self, token):
        return self._claims(token)[0]

    def attach_key(self, token, api_key):
        with self._lock:
            subject, expires, _ = self._claims(token)
            self.sessions.attach(token, api_key, subject=subject, expires_at=expires)

    def drop_key(self, token, subject):
        self.sessions.drop_key(token, subject=subject)

    def has_key(self, token, subject):
        return self.sessions.has_key(token, subject=subject)

    def key_for(self, token, subject):
        return self.sessions.key_for(token, subject=subject)

    def close(self, token):
        """Revoke this session for the current process and drop its key.

        The signed token stays cryptographically valid until it expires,so a
        deployment secret is the only server-side revocation lever across a
        restart. Browsers remove the cookie immediately.
        """
        with self._lock:
            try:
                _, expires, identity = self._claims(token)
            except SessionUnavailable:
                return
            self._revoked[identity] = expires
            self.sessions.revoke(token)

    def purge_expired(self):
        with self._lock:
            now = self._clock()
            self._revoked = {token: end for token, end in self._revoked.items() if end > now}
            return self.sessions.purge_expired()

    def clear(self):
        with self._lock:
            self._revoked.clear()
            self.sessions.clear()
