"""Server-side Meta speech-to-text: transcribe short dictation clips.

One-shot file flow only (``POST /api/stt/transcribe``). The browser uploads a
clip plus a language tag; this module reserves shared allowance, converts the
clip to 16 kHz mono WAV, forwards it to Meta ASR with the caller's own session
key, and returns source-language text. Audio bytes and transcripts are never
persisted, logged, or stored in receipts: only coarse counters and redacted
provider diagnostics leave this module.

Wire shape follows Meta's batch ASR contract (multipart ``request`` JSON +
``audio`` WAV; JSON ``{transcript, turns[]}`` reply, turns empty in
PUSH_TO_TALK; languageBias takes language *names*). Not live-verified: no
real Meta call has been made from this tree yet.
"""

import io
import json
import logging
import shutil
import subprocess
import threading
import time
import uuid
import wave
from decimal import Decimal
from email.parser import BytesParser
from email.policy import HTTP

import httpx

from remote_operator.control import TransportFailure
from remote_operator.meta import grant_fingerprint

logger = logging.getLogger(__name__)

STT_MODEL = "muse-voice-transcribe-1.0"
STT_ENDPOINT = "https://api.meta.ai/v1/asr/transcribe"
STT_MODE = "PUSH_TO_TALK"
AUDIO_USD_PER_HOUR = "0.18"
# Worst-case reservation per request: a full 60 s clip at $0.18/hour.
AUDIO_RESERVATION_USD = str(Decimal(60) * Decimal(AUDIO_USD_PER_HOUR) / Decimal(3600))
MAX_DURATION_S = 60
MAX_AUDIO_BYTES = 4 * 1024 * 1024
# Middleware body cap for /api/stt/*: one max-size clip plus multipart framing.
STT_BODY_MAX_BYTES = MAX_AUDIO_BYTES + 65536
TARGET_RATE = 16000
REQUEST_TIMEOUT_S = 60
MAX_CONCURRENT_TRANSCRIPTIONS = 4
DETAIL_CHAR_LIMIT = 700

ACCEPTED_AUDIO_TYPES = frozenset({"audio/webm", "audio/wav", "audio/mp4"})
# Day-one set; maps request tags to Meta languageBias names.
LANGUAGE_BIAS = {"en": "English", "pl": "Polish"}

_SEMAPHORE = threading.Semaphore(MAX_CONCURRENT_TRANSCRIPTIONS)


class SttFailure(TransportFailure):
    """A bounded transcription diagnostic with its HTTP mapping."""

    def __init__(self, category, detail, http_status, retry_after=None):
        self.category = category
        self.detail = detail
        self.http_status = http_status
        self.retry_after = retry_after
        super().__init__(f"stt {category}: {detail}")


def _redact(text, key):
    if isinstance(text, str) and key:
        return text.replace(key, "[redacted]")
    return text


def parse_transcribe_form(content_type, body):
    """Split a multipart/transcribe upload into (audio, audio_type, language).

    Raises SttFailure (422) for a missing audio part, missing language, or a
    body that is not multipart at all. The optional ``input`` hint field is
    accepted and ignored: it only ever routes text client-side.
    """
    if not content_type or "multipart/form-data" not in content_type.split(";")[0]:
        raise SttFailure(
            "request", "multipart/form-data with audio and language is required", 422
        )
    try:
        raw = b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + bytes(body)
        message = BytesParser(policy=HTTP).parsebytes(raw)
    except (ValueError, TypeError):
        raise SttFailure("request", "Upload could not be parsed", 422) from None
    if not message.is_multipart():
        raise SttFailure(
            "request", "multipart/form-data with audio and language is required", 422
        )
    audio, audio_type, language = None, None, None
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name == "audio" and audio is None:
            audio = part.get_payload(decode=True) or b""
            audio_type = (part.get_content_type() or "").split(";")[0].strip().lower()
        elif name == "language" and language is None:
            payload = part.get_payload(decode=True) or b""
            try:
                language = payload.decode("utf-8").strip()
            except UnicodeDecodeError:
                raise SttFailure("request", "language tag is not decodable", 422) from None
    if not audio:
        raise SttFailure("request", "audio file is required", 422)
    if language is None or not language:
        raise SttFailure("request", "language tag is required", 422)
    return audio, audio_type, language


def _decode_wav(audio_bytes):
    """Return (pcm, duration) for ready-made WAV, else None when ffmpeg is needed.

    Only 16 kHz mono 16-bit PCM passes straight through; anything else still
    needs a converting decode. Reads every frame so truncated data fails here
    instead of billing a partial clip. Raises SttFailure (422) for corrupt WAV.
    """
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as clip:
            channels = clip.getnchannels()
            width = clip.getsampwidth()
            rate = clip.getframerate()
            if (channels, width, rate) != (1, 2, TARGET_RATE):
                return None
            frames = clip.readframes(clip.getnframes())
    except (wave.Error, EOFError):
        raise SttFailure("request", "Audio could not be decoded", 422) from None
    if not frames:
        raise SttFailure("request", "Audio clip is empty", 422) from None
    return frames, len(frames) / 2 / TARGET_RATE


def _run_ffmpeg(audio_bytes, timeout=30):
    """Decode/convert any clip to 16 kHz mono raw PCM, capped at 61 s."""
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise FileNotFoundError("ffmpeg is not installed")
    return subprocess.run(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            "pipe:0",
            "-ar",
            str(TARGET_RATE),
            "-ac",
            "1",
            "-f",
            "s16le",
            "-t",
            str(MAX_DURATION_S + 1),
            "pipe:1",
        ],
        input=audio_bytes,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def decode_audio(audio_bytes, content_type, *, run_ffmpeg=None):
    """Validate and convert an upload to (pcm_bytes, duration_seconds).

    Raises SttFailure: 413 over the byte cap, 422 for unsupported/corrupt/empty
    audio or audio over the duration cap, 502 when conversion itself is
    unavailable (no ffmpeg). Never logs audio content.
    """
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime not in ACCEPTED_AUDIO_TYPES:
        raise SttFailure(
            "request",
            "Unsupported audio type; send webm, wav or mp4",
            422,
        )
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise SttFailure("too_large", "Audio exceeds 4 MB limit", 413)
    if mime == "audio/wav":
        ready = _decode_wav(audio_bytes)
        if ready is not None:
            pcm, duration = ready
            if duration > MAX_DURATION_S:
                raise SttFailure("request", "Audio exceeds 60 s limit", 422)
            return pcm, duration
    runner = run_ffmpeg or _run_ffmpeg
    try:
        completed = runner(audio_bytes)
    except FileNotFoundError:
        raise SttFailure(
            "unavailable", "Audio conversion is unavailable on this server", 502
        ) from None
    except subprocess.TimeoutExpired:
        raise SttFailure("unavailable", "Transcription provider unavailable", 502) from None
    pcm = completed.stdout or b""
    if completed.returncode != 0 or not pcm:
        raise SttFailure("request", "Audio could not be decoded", 422) from None
    duration = len(pcm) / 2 / TARGET_RATE
    if duration > MAX_DURATION_S:
        raise SttFailure("request", "Audio exceeds 60 s limit", 422)
    return pcm, duration


def _wav_bytes(pcm):
    """Wrap 16 kHz mono s16le PCM in a RIFF/WAVE container for Meta."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(TARGET_RATE)
        clip.writeframes(pcm)
    return buffer.getvalue()


def _provider_detail(raw, key):
    try:
        data = json.loads(raw or b"")
    except ValueError:
        data = None
    detail = "No structured provider detail"
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            detail = error["message"]
        elif isinstance(data.get("message"), str):
            detail = data["message"]
    return str(_redact(detail, key))[:DETAIL_CHAR_LIMIT]


def _transcript_text(data):
    """Extract utterance text from a Meta ASR JSON reply.

    PUSH_TO_TALK answers carry the top-level ``transcript`` (turns empty);
    turn modes carry ``turns[].transcript``. Accept either; empty string means
    nothing heard. Raises SttFailure (502) for a malformed reply.
    """
    if not isinstance(data, dict):
        raise SttFailure("protocol", "Malformed provider response", 502)
    transcript = data.get("transcript")
    if isinstance(transcript, str):
        return transcript
    turns = data.get("turns")
    if not isinstance(turns, list):
        raise SttFailure("protocol", "Malformed provider response", 502)
    parts = []
    for turn in turns:
        if isinstance(turn, str):
            parts.append(turn)
        elif isinstance(turn, dict) and isinstance(turn.get("transcript"), str):
            parts.append(turn["transcript"])
        else:
            raise SttFailure("protocol", "Malformed provider response", 502)
    return " ".join(part for part in (text.strip() for text in parts) if part)


def transcribe_audio(
    *,
    key_source,
    grant,
    store,
    audio_bytes,
    content_type,
    language,
    transport=None,
    run_ffmpeg=None,
    session_id=None,
):
    """Reserve allowance, convert, forward to Meta ASR, and parse the reply.

    Consumes one shared request count plus a fixed worst-case USD reservation
    (operation ``audio``) before anything is decoded or sent. Failed calls stay
    consumed, like chat reservations. Raises SttFailure with the HTTP mapping
    from the endpoint contract; lets grant/store ValueErrors (fingerprint or
    exhaustion) propagate for the caller's 409 mapping.
    """
    grant.check()
    bias = LANGUAGE_BIAS.get((language or "").strip().lower())
    if bias is None:
        raise SttFailure(
            "request",
            "Unsupported language; send one of: " + ", ".join(sorted(LANGUAGE_BIAS)),
            422,
        )
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime not in ACCEPTED_AUDIO_TYPES:
        raise SttFailure("request", "Unsupported audio type; send webm, wav or mp4", 422)
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise SttFailure("too_large", "Audio exceeds 4 MB limit", 413)
    if not _SEMAPHORE.acquire(blocking=False):
        raise SttFailure("busy", "Transcription busy; retry or use browser dictation", 429)
    try:
        key = key_source()
        if not isinstance(key, str) or not key:
            raise ValueError("Session credential unavailable")
        allowance = store.reserve_allowance(
            grant.grant_id,
            grant_fingerprint(grant),
            grant.max_requests,
            AUDIO_RESERVATION_USD,
            grant.approved_usd,
            operation="audio",
        )
        pcm, duration = decode_audio(audio_bytes, content_type, run_ffmpeg=run_ffmpeg)
        request_payload = {
            "model": STT_MODEL,
            "audioEncoding": "WAV",
            "mode": STT_MODE,
            "languageBias": [bias] if bias == "English" else [bias, "English"],
        }
        # Random per-request correlation id; never a user, subject or task id.
        correlation = session_id or uuid.uuid4().hex
        billed_seconds = int(duration)
        deadline = time.monotonic() + REQUEST_TIMEOUT_S
        try:
            with httpx.Client(
                transport=transport,
                timeout=httpx.Timeout(REQUEST_TIMEOUT_S),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.post(
                    STT_ENDPOINT,
                    params={"sessionId": correlation},
                    headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
                    files={
                        "request": (None, json.dumps(request_payload), "application/json"),
                        "audio": ("clip.wav", _wav_bytes(pcm), "audio/wav"),
                    },
                )
                raw = response.read()
                if time.monotonic() >= deadline:
                    raise SttFailure("timeout", "Response deadline reached", 502)
        except httpx.TimeoutException as exc:
            raise SttFailure("timeout", type(exc).__name__, 502) from None
        except httpx.HTTPError as exc:
            raise SttFailure("transport", type(exc).__name__, 502) from None
        if response.status_code == 429:
            raise SttFailure(
                "rate_limit",
                "Transcription busy; retry or use browser dictation",
                429,
                retry_after=response.headers.get("retry-after"),
            )
        if response.status_code in (401, 403):
            raise SttFailure(
                "authentication",
                "Provider refused the session key; re-attach and retry",
                409,
            )
        if response.status_code in (400, 413):
            raise SttFailure("request", _provider_detail(raw, key), 422)
        if response.status_code != 200:
            if response.status_code >= 500:
                raise SttFailure(
                    "availability", "Transcription provider unavailable", 502
                )
            raise SttFailure("request", _provider_detail(raw, key), 422)
        try:
            data = json.loads(raw)
        except ValueError:
            raise SttFailure("protocol", "Malformed provider response", 502) from None
        text = _transcript_text(data)
        billed_usd = str(
            Decimal(billed_seconds) * Decimal(AUDIO_USD_PER_HOUR) / Decimal(3600)
        )
        logger.info(
            "stt transcribed %.1fs audio in %d billed seconds", duration, billed_seconds
        )
        return {
            "text": text,
            "language": (language or "").strip().lower(),
            "duration_seconds": round(duration, 1),
            "billed_seconds": billed_seconds,
            "receipt": {
                "provider": "meta",
                "model": STT_MODEL,
                "operation": "audio",
                "reservation_usd_estimate": AUDIO_RESERVATION_USD,
                "billed_usd_actual": billed_usd,
                "local_allowance": allowance,
                "spending_control": "local_estimate_not_provider_cap",
            },
        }
    finally:
        _SEMAPHORE.release()
