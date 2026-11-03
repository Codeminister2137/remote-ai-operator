"""Server-side native-language -> English normalization for planning input.

Placement is server acceptance: the Supervisor translates at ingestion, inside
the lock, BEFORE a user turn is appended to history, so planning prompts,
plans and runs always consume English working text. The verbatim source is
preserved next to it with its language tag and translator id.

English posts exactly as before (no provider call, no spend). Non-English
input without a configured translator fails closed with 409 (block with
retry): nothing is stored.
"""

import json
import logging
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Protocol

import httpx

from remote_operator.control import digest
from remote_operator.meta import ENDPOINT

logger = logging.getLogger(__name__)

ALLOWED_LANGUAGES = ("en", "pl")
LANGUAGE_NAMES = {"en": "English", "pl": "Polish"}
MAX_TRANSLATION_CHARS = 4000
TRANSLATION_MAX_OUTPUT_TOKENS = 2048
TRANSLATION_TIMEOUT_SECONDS = 60
TRANSLATION_RESPONSE_LIMIT = 262144

UNAVAILABLE_MESSAGE = (
    "Translation unavailable; attach a session key and retry, or send English text."
)
REFUSED_MESSAGE = "Translation provider refused the session key; re-attach and retry."
RATE_LIMITED_MESSAGE = "Translation rate limited; retry shortly."
PROVIDER_DOWN_MESSAGE = "Translation provider unavailable."
MALFORMED_MESSAGE = "Malformed translation response."
EMPTY_MESSAGE = "Translation provider returned an empty translation."


class TranslationError(Exception):
    """A typed translation failure mapped to an HTTP status by the web layer."""

    def __init__(self, status: int, message: str, *, headers: dict | None = None):
        super().__init__(message)
        self.status = status
        self.headers = dict(headers or {})


def normalize_language(tag: str) -> str:
    """Validate a BCP47 tag against the day-one allowlist; returns lowercase."""
    if not isinstance(tag, str) or tag.lower() not in ALLOWED_LANGUAGES:
        raise ValueError(f"Unsupported language: {tag!r}; supported languages: en, pl")
    return tag.lower()


@dataclass(frozen=True)
class TranslationResult:
    """English working text plus provenance for one translated input."""

    text: str
    source_lang: str
    translator_id: str


@dataclass(frozen=True)
class NormalizedText:
    """One ingestion input split into working text plus preserved source."""

    working: str
    source: str
    lang: str
    translated: bool
    translator_id: str


class Translator(Protocol):
    """Narrow engine interface; the free-later swap implements this too."""

    @property
    def id(self) -> str: ...

    def supported_languages(self) -> list[str]: ...

    def needs_key(self) -> bool: ...

    def estimate_usd(self, text: str) -> str: ...

    def translate(self, text: str, source_lang: str) -> TranslationResult: ...


def normalize_input(
    translator: Translator | None, text: str, language: str
) -> tuple[NormalizedText, dict | None]:
    """Validate and translate one ingestion input.

    Returns the normalized text plus the metering receipt (None when no
    provider call ran). Raises TranslationError(409) for non-English input
    without a translator; nothing is stored by callers on failure.
    """
    lang = normalize_language(language)
    if lang == "en":
        return NormalizedText(text, text, "en", False, "none"), None
    if translator is None:
        raise TranslationError(409, UNAVAILABLE_MESSAGE)
    result = translator.translate(text, lang)
    receipt = getattr(translator, "last_receipt", None)
    return (
        NormalizedText(result.text, text, result.source_lang, True, result.translator_id),
        receipt,
    )


def translation_manifest(thread: dict) -> dict:
    """Summarize translation provenance for review UI and run audit.

    Readers treat missing keys as legacy English; no backfill is performed.
    """
    langs: list[str] = []
    translators: list[str] = []
    untranslated = False

    def note(lang, translator_id, translated):
        nonlocal untranslated
        if lang in (None, "en"):
            return
        if translated:
            if lang not in langs:
                langs.append(lang)
            if translator_id not in translators:
                translators.append(translator_id)
        else:
            untranslated = True

    prompt_lang = thread.get("prompt_lang", "en")
    prompt_translator = thread.get("prompt_translator", "none")
    note(prompt_lang, prompt_translator, prompt_translator != "none")
    for turn in thread.get("history", []):
        if not isinstance(turn, dict) or turn.get("role") != "user":
            continue
        note(turn.get("source_lang", "en"), turn.get("translator", "none"), turn.get("translated"))
    return {"langs": langs, "translators": translators, "untranslated": untranslated}


class MetaTranslator:
    """Token-priced translation over the existing Meta Responses chat route.

    Metering reuses the run grant: the same grant id, fingerprint and
    ``allowances`` ledger row as model runs, so judges stay self-paying for
    their own non-English input under one spending view.
    """

    def __init__(self, key_source, grant, store, *, transport=None):
        """``key_source`` resolves the session key per call; it is never logged."""
        self._key_source = key_source
        self.grant = grant
        self.store = store
        self._transport = transport
        self.last_receipt: dict | None = None

    @property
    def id(self) -> str:
        return "meta:" + self.grant.model if self.grant is not None else "meta:unconfigured"

    def supported_languages(self) -> list[str]:
        return list(ALLOWED_LANGUAGES)

    def needs_key(self) -> bool:
        return True

    def estimate_usd(self, text: str) -> str:
        """Return the reservation this input would consume, without spending."""
        if self.grant is None:
            raise TranslationError(409, UNAVAILABLE_MESSAGE)
        payload = self._payload(text, "pl")
        encoded = json.dumps(payload).encode()
        return str(self._reservation(len(encoded)))

    def translate(self, text: str, source_lang: str) -> TranslationResult:
        self.last_receipt = None
        lang = normalize_language(source_lang)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Translation requires non-empty text")
        if len(text) > MAX_TRANSLATION_CHARS:
            raise ValueError("Translation input exceeds 4000 characters")
        if lang == "en":
            return TranslationResult(text=text, source_lang="en", translator_id="none")
        if self.grant is None:
            raise TranslationError(409, UNAVAILABLE_MESSAGE)
        try:
            self.grant.check()
        except ValueError:
            raise TranslationError(
                409, "Translation unavailable; the spending grant is invalid or expired."
            ) from None
        try:
            key = self._key_source()
        except (ValueError, KeyError, TypeError, AttributeError):
            raise TranslationError(409, UNAVAILABLE_MESSAGE) from None
        if not isinstance(key, str) or not key:
            raise TranslationError(409, UNAVAILABLE_MESSAGE)
        payload = self._payload(text, lang)
        encoded = json.dumps(payload).encode()
        if len(encoded) > self.grant.max_input_bytes:
            raise ValueError("Translation input exceeds the admitted byte limit")
        reserve = self._reservation(len(encoded))
        try:
            allowance = self.store.reserve_allowance(
                self.grant.grant_id,
                digest(json.dumps(asdict(self.grant), sort_keys=True)),
                self.grant.max_requests,
                str(reserve),
                self.grant.approved_usd,
            )
        except ValueError as exc:
            raise TranslationError(409, str(exc)) from None
        self.last_receipt = {
            "provider": "meta",
            "model": self.grant.model,
            "grant_id": self.grant.grant_id,
            "op": "translate",
            "translator": self.id,
            "source_lang": lang,
            "chars_in": len(text),
            "reservation_usd_estimate": str(reserve),
            "local_allowance": allowance,
            "spending_control": "local_estimate_not_provider_cap",
            "usage": None,
        }
        raw = self._send(payload, key)
        working = self._parse(raw, key)
        self.last_receipt["chars_out"] = len(working)
        return TranslationResult(text=working, source_lang=lang, translator_id=self.id)

    def _output_cap(self) -> int:
        return min(self.grant.max_output_tokens, TRANSLATION_MAX_OUTPUT_TOKENS)

    def _reservation(self, encoded_len: int) -> Decimal:
        return (
            Decimal(encoded_len + 2048) * Decimal(self.grant.input_usd_per_million)
            + Decimal(self._output_cap()) * Decimal(self.grant.output_usd_per_million)
        ) / Decimal(1_000_000)

    def _payload(self, text: str, lang: str) -> dict:
        return {
            "model": self.grant.model,
            "instructions": (
                f"Translate the following {LANGUAGE_NAMES[lang]} text to English. "
                "Return only the translation, no commentary."
            ),
            "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}],
            "store": False,
            "max_output_tokens": self._output_cap(),
        }

    def _send(self, payload: dict, key: str) -> bytes:
        request_deadline = time.monotonic() + TRANSLATION_TIMEOUT_SECONDS
        try:
            with (
                httpx.Client(
                    transport=self._transport,
                    timeout=httpx.Timeout(TRANSLATION_TIMEOUT_SECONDS),
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream(
                    "POST", ENDPOINT, headers={"Authorization": "Bearer " + key}, json=payload
                ) as response,
            ):
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() >= request_deadline:
                        raise TranslationError(502, PROVIDER_DOWN_MESSAGE)
                    raw.extend(chunk)
                    if len(raw) > TRANSLATION_RESPONSE_LIMIT:
                        raise TranslationError(502, MALFORMED_MESSAGE)
                if response.status_code in (401, 403):
                    raise TranslationError(409, REFUSED_MESSAGE)
                if response.status_code == 429:
                    headers = {}
                    retry_after = response.headers.get("retry-after")
                    if retry_after:
                        headers["Retry-After"] = retry_after
                    raise TranslationError(429, RATE_LIMITED_MESSAGE, headers=headers)
                if response.status_code != 200:
                    raise TranslationError(502, PROVIDER_DOWN_MESSAGE)
                return bytes(raw)
        except httpx.TimeoutException:
            raise TranslationError(502, PROVIDER_DOWN_MESSAGE) from None
        except httpx.HTTPError:
            raise TranslationError(502, PROVIDER_DOWN_MESSAGE) from None

    def _parse(self, raw: bytes, key: str) -> str:
        try:
            data = json.loads(raw)
        except ValueError:
            raise TranslationError(502, MALFORMED_MESSAGE) from None
        if not isinstance(data, dict):
            raise TranslationError(502, MALFORMED_MESSAGE)
        usage = data.get("usage")
        if usage is not None:
            if not isinstance(usage, dict) or any(
                type(usage.get(name)) is not int or usage[name] < 0
                for name in ("input_tokens", "output_tokens", "total_tokens")
            ):
                raise TranslationError(502, MALFORMED_MESSAGE)
            self.last_receipt["usage"] = {
                name: usage[name] for name in ("input_tokens", "output_tokens", "total_tokens")
            }
        if data.get("store") is True:
            raise TranslationError(502, PROVIDER_DOWN_MESSAGE)
        if data.get("status") != "completed":
            raise TranslationError(502, PROVIDER_DOWN_MESSAGE)
        output = data.get("output")
        if not isinstance(output, list):
            raise TranslationError(502, MALFORMED_MESSAGE)
        parts = []
        for item in output:
            if not isinstance(item, dict):
                raise TranslationError(502, MALFORMED_MESSAGE)
            kind = item.get("type")
            if kind == "message":
                content = item.get("content")
                if not isinstance(content, list):
                    raise TranslationError(502, MALFORMED_MESSAGE)
                for part in content:
                    if (
                        isinstance(part, dict)
                        and part.get("type") == "output_text"
                        and isinstance(part.get("text"), str)
                    ):
                        parts.append(part["text"])
            elif kind not in ("reasoning",):
                raise TranslationError(502, MALFORMED_MESSAGE)
        working = "".join(parts).replace(key, "[redacted]")
        if not working.strip():
            raise TranslationError(502, EMPTY_MESSAGE)
        return working


def translate_text(
    key_source, grant, store, text: str, source_lang: str, *, transport=None
) -> tuple[TranslationResult, dict | None]:
    """Translate one planning input; returns the result plus its receipt."""
    translator = MetaTranslator(key_source, grant, store, transport=transport)
    result = translator.translate(text, source_lang)
    return result, translator.last_receipt


def translator_from_model(model):
    """Build the metered translator for a planning actor, or None when offline.

    Scripted/offline actors yield None: English posts as today while
    non-English input fails closed with 409 (block with retry).
    """
    if type(model).__name__ == "ScriptedModel":
        return None
    grant = getattr(model, "grant", None)
    store = getattr(model, "store", None)
    credential = getattr(model, "session_credential", None)
    if grant is None or store is None or not callable(credential):
        return None
    return MetaTranslator(
        credential, grant, store, transport=getattr(model, "http_transport", None)
    )
