"""Meta transcription contracts with stubbed transport and decode, never live ASR."""

import io
import json
import re
import tempfile
import time
import unittest
import wave
from email.parser import BytesParser
from email.policy import HTTP
from pathlib import Path

import httpx

from remote_operator.meta import InferenceGrant
from remote_operator.store import Store
from remote_operator.stt import (
    AUDIO_RESERVATION_USD,
    STT_BODY_MAX_BYTES,
    SttFailure,
    decode_audio,
    parse_transcribe_form,
    transcribe_audio,
)


def wav_bytes(seconds, *, rate=16000, channels=1, width=2):
    """Render a silent PCM WAV clip of the requested shape."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(channels)
        clip.setsampwidth(width)
        clip.setframerate(rate)
        clip.writeframes(b"\x00" * int(seconds * rate * channels * width))
    return buffer.getvalue()


def grant(max_requests=5, approved_usd="1"):
    return InferenceGrant(
        "stt-fixture-only",
        "muse-spark-fixture",
        time.time() + 300,
        max_requests,
        100000,
        1024,
        approved_usd,
        "1",
        "1",
        True,
        True,
    )


def meta_request_parts(request):
    """Split a captured Meta ASR multipart request into its named parts."""
    raw = (
        b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n"
        + request.content
    )
    message = BytesParser(policy=HTTP).parsebytes(raw)
    parts = {}
    for part in message.iter_parts():
        parts[part.get_param("name", header="content-disposition")] = part
    return parts


class TranscribeContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "tasks.sqlite3")
        self.grant = grant()
        self.captured = []

    def tearDown(self):
        self.directory.cleanup()

    def transcribe(self, audio, handler, *, language="pl", content_type="audio/wav"):
        def serve(request):
            self.captured.append(request)
            return handler(request)

        return transcribe_audio(
            key_source=lambda: "fixture-secret",
            grant=self.grant,
            store=self.store,
            audio_bytes=audio,
            content_type=content_type,
            language=language,
            transport=httpx.MockTransport(serve),
        )

    def usage(self):
        from remote_operator.meta import grant_fingerprint

        return self.store.allowance_status(
            self.grant.grant_id,
            grant_fingerprint(self.grant),
            self.grant.max_requests,
            self.grant.approved_usd,
        )

    def test_success_sends_meta_contract_and_returns_receipt(self):
        clip = wav_bytes(4.2)
        with self.assertLogs("remote_operator.stt", level="INFO") as captured_logs:
            result = self.transcribe(
                clip, lambda request: httpx.Response(200, json={"transcript": "cześć"})
            )
        self.assertEqual(result["text"], "cześć")
        self.assertEqual(result["language"], "pl")
        self.assertEqual(result["duration_seconds"], 4.2)
        self.assertEqual(result["billed_seconds"], 4)
        receipt = result["receipt"]
        self.assertEqual(receipt["provider"], "meta")
        self.assertEqual(receipt["model"], "muse-voice-transcribe-1.0")
        self.assertEqual(receipt["operation"], "audio")
        self.assertEqual(receipt["reservation_usd_estimate"], AUDIO_RESERVATION_USD)
        self.assertEqual(receipt["spending_control"], "local_estimate_not_provider_cap")
        self.assertEqual(
            receipt["local_allowance"]["operations"]["audio"]["requests_used"], 1
        )
        self.assertNotIn("fixture-secret", json.dumps(result))
        self.assertNotIn("cześć", json.dumps(receipt))
        self.assertNotIn("fixture-secret", "\n".join(captured_logs.output))
        request = self.captured[0]
        self.assertEqual(request.url.host, "api.meta.ai")
        self.assertEqual(request.url.path, "/v1/asr/transcribe")
        self.assertTrue(re.fullmatch(r"[0-9a-f]{32}", request.url.params["sessionId"]))
        self.assertEqual(request.headers["authorization"], "Bearer fixture-secret")
        self.assertEqual(request.headers["accept"], "application/json")
        parts = meta_request_parts(request)
        payload = json.loads(parts["request"].get_payload(decode=True))
        self.assertEqual(
            payload,
            {
                "model": "muse-voice-transcribe-1.0",
                "audioEncoding": "WAV",
                "mode": "PUSH_TO_TALK",
                "languageBias": ["Polish", "English"],
            },
        )
        with wave.open(io.BytesIO(parts["audio"].get_payload(decode=True)), "rb") as forwarded:
            self.assertEqual(
                (forwarded.getnchannels(), forwarded.getsampwidth(), forwarded.getframerate()),
                (1, 2, 16000),
            )
            self.assertAlmostEqual(
                forwarded.getnframes() / 16000, 4.2, places=1
            )
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_turns_reply_joins_in_order_and_empty_means_nothing_heard(self):
        clip = wav_bytes(1)
        result = self.transcribe(
            clip,
            lambda request: httpx.Response(
                200, json={"turns": [{"transcript": "hello"}, {"transcript": "world"}]}
            ),
        )
        self.assertEqual(result["text"], "hello world")
        empty = self.transcribe(
            clip, lambda request: httpx.Response(200, json={"transcript": "", "turns": []})
        )
        self.assertEqual(empty["text"], "")

    def test_english_bias_is_singleton_and_request_is_validated_first(self):
        clip = wav_bytes(1)
        result = self.transcribe(
            clip,
            lambda request: httpx.Response(200, json={"transcript": "hi"}),
            language="en",
        )
        payload = json.loads(
            meta_request_parts(self.captured[0])["request"].get_payload(decode=True)
        )
        self.assertEqual(payload["languageBias"], ["English"])
        self.assertEqual(result["language"], "en")
        for bad in ("xx", "", "  ", "en-US", None):
            with self.subTest(language=bad), self.assertRaises(SttFailure) as raised:
                self.transcribe(clip, lambda request: httpx.Response(200), language=bad)
            self.assertEqual(raised.exception.http_status, 422)
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.usage()["requests_used"], 1)

    def test_reserve_happens_before_send_and_failures_stay_consumed(self):
        def fail(request):
            raise httpx.ConnectError("fixture offline", request=request)

        with self.assertRaises(SttFailure) as raised:
            self.transcribe(wav_bytes(1), fail)
        self.assertEqual(raised.exception.http_status, 502)
        status = self.usage()
        self.assertEqual(status["requests_used"], 1)
        self.assertEqual(status["operations"]["audio"]["requests_used"], 1)
        self.assertEqual(status["operations"]["chat"]["requests_used"], 0)

    def test_provider_statuses_map_to_the_endpoint_contract(self):
        self.grant = grant(max_requests=10)
        clip = wav_bytes(1)
        cases = [
            (401, {}, 409, "session key"),
            (403, {}, 409, "session key"),
            (400, {"error": {"message": "bad wav fixture-secret"}}, 422, "[redacted]"),
            (400, {"message": "plain message shape"}, 422, "plain message shape"),
            (413, {}, 422, "No structured provider detail"),
            (500, {}, 502, "provider unavailable"),
        ]
        for status, body, mapped, hint in cases:
            with self.subTest(status=status):
                with self.assertRaises(SttFailure) as raised:
                    self.transcribe(
                        clip, lambda request, s=status, b=body: httpx.Response(s, json=b)
                    )
                self.assertEqual(raised.exception.http_status, mapped)
                self.assertIn(hint, raised.exception.detail)
                self.assertNotIn("fixture-secret", raised.exception.detail)
        with self.assertRaises(SttFailure) as limited:
            self.transcribe(
                clip,
                lambda request: httpx.Response(
                    429, json={}, headers={"retry-after": "7"}
                ),
            )
        self.assertEqual(limited.exception.http_status, 429)
        self.assertEqual(limited.exception.retry_after, "7")

    def test_timeouts_and_malformed_replies_are_502(self):
        clip = wav_bytes(1)

        def timeout(request):
            raise httpx.ReadTimeout("fixture slow", request=request)

        with self.assertRaises(SttFailure) as raised:
            self.transcribe(clip, timeout)
        self.assertEqual(raised.exception.http_status, 502)
        for body in (b"not json", b'{"unrelated": true}', b'{"turns": [{"nope": 1}]}'):
            with self.subTest(body=body):
                with self.assertRaises(SttFailure) as raised:
                    self.transcribe(
                        clip, lambda request, b=body: httpx.Response(200, content=b)
                    )
                self.assertEqual(raised.exception.http_status, 502)

    def test_allowance_exhaustion_sends_nothing(self):
        self.grant = grant(max_requests=1)
        clip = wav_bytes(1)
        self.transcribe(clip, lambda request: httpx.Response(200, json={"transcript": "a"}))
        with self.assertRaisesRegex(ValueError, "allowance exhausted"):
            self.transcribe(
                clip, lambda request: httpx.Response(200, json={"transcript": "b"})
            )
        self.assertEqual(len(self.captured), 1)

    def test_busy_transcriber_refuses_without_consuming(self):
        from remote_operator import stt

        for _ in range(stt.MAX_CONCURRENT_TRANSCRIPTIONS):
            self.assertTrue(stt._SEMAPHORE.acquire(blocking=False))
        try:
            with self.assertRaises(SttFailure) as raised:
                self.transcribe(
                    wav_bytes(1), lambda request: httpx.Response(200, json={})
                )
        finally:
            for _ in range(stt.MAX_CONCURRENT_TRANSCRIPTIONS):
                stt._SEMAPHORE.release()
        self.assertEqual(raised.exception.http_status, 429)
        self.assertEqual(self.captured, [])
        self.assertEqual(self.usage()["requests_used"], 0)

    def test_shared_bucket_reports_spend_by_operation(self):
        from remote_operator.meta import grant_fingerprint

        fingerprint = grant_fingerprint(self.grant)
        self.store.reserve_allowance(
            self.grant.grant_id, fingerprint, self.grant.max_requests, "0.01",
            self.grant.approved_usd,
        )
        self.transcribe(
            wav_bytes(1), lambda request: httpx.Response(200, json={"transcript": "a"})
        )
        status = self.usage()
        self.assertEqual(status["requests_used"], 2)
        self.assertEqual(status["operations"]["chat"]["requests_used"], 1)
        self.assertEqual(status["operations"]["audio"]["requests_used"], 1)
        self.assertEqual(status["operations"]["chat"]["reserved_usd_estimate"], "0.01")
        self.assertEqual(
            status["operations"]["audio"]["reserved_usd_estimate"], AUDIO_RESERVATION_USD
        )
        with self.assertRaisesRegex(ValueError, "Unknown allowance operation"):
            self.store.reserve_allowance(
                self.grant.grant_id, fingerprint, 5, "0.01", "1", operation="video"
            )

    def test_grant_and_key_problems_stay_409_shaped(self):
        expired = grant()
        object.__setattr__(expired, "expires_at", time.time() - 1)
        with self.assertRaises(ValueError):
            transcribe_audio(
                key_source=lambda: "fixture-secret",
                grant=expired,
                store=self.store,
                audio_bytes=wav_bytes(1),
                content_type="audio/wav",
                language="en",
                transport=httpx.MockTransport(lambda request: httpx.Response(200)),
            )
        with self.assertRaises(ValueError):
            transcribe_audio(
                key_source=lambda: "",
                grant=self.grant,
                store=self.store,
                audio_bytes=wav_bytes(1),
                content_type="audio/wav",
                language="en",
                transport=httpx.MockTransport(lambda request: httpx.Response(200)),
            )
        self.assertEqual(self.usage()["requests_used"], 0)


class DecodeContracts(unittest.TestCase):
    def test_ready_wav_passes_through_without_ffmpeg(self):
        pcm, duration = decode_audio(
            wav_bytes(2.5), "audio/wav", run_ffmpeg=lambda data: self.fail("ffmpeg used")
        )
        self.assertEqual(len(pcm), int(2.5 * 16000 * 2))
        self.assertAlmostEqual(duration, 2.5, places=2)

    def test_duration_and_type_guards_reject_before_billing(self):
        with self.assertRaises(SttFailure) as too_long:
            decode_audio(wav_bytes(60.5), "audio/wav")
        self.assertEqual(too_long.exception.http_status, 422)
        self.assertIn("60 s", too_long.exception.detail)
        edge, _ = decode_audio(wav_bytes(60), "audio/wav")
        self.assertTrue(edge)
        with self.assertRaises(SttFailure) as too_big:
            decode_audio(b"\x00" * (4 * 1024 * 1024 + 1), "audio/webm")
        self.assertEqual(too_big.exception.http_status, 413)
        for content_type in ("video/mp4", "text/plain", "", None):
            with self.subTest(content_type=content_type):
                with self.assertRaises(SttFailure) as bad:
                    decode_audio(b"data", content_type)
                self.assertEqual(bad.exception.http_status, 422)
        with self.assertRaises(SttFailure) as corrupt:
            decode_audio(b"not a wav file", "audio/wav")
        self.assertEqual(corrupt.exception.http_status, 422)
        with self.assertRaises(SttFailure) as empty:
            decode_audio(wav_bytes(0), "audio/wav")
        self.assertEqual(empty.exception.http_status, 422)

    def test_other_shapes_convert_through_ffmpeg(self):
        calls = []

        class Completed:
            returncode = 0
            stdout = b"\x01\x02" * 16000
            stderr = b""

        def fake_ffmpeg(data):
            calls.append(data)
            return Completed()

        pcm, duration = decode_audio(
            wav_bytes(1, rate=8000), "audio/wav", run_ffmpeg=fake_ffmpeg
        )
        self.assertEqual(calls and len(calls), 1)
        self.assertEqual(pcm, Completed.stdout)
        self.assertAlmostEqual(duration, 1.0, places=2)
        pcm, _ = decode_audio(b"fake-webm", "audio/webm; codecs=opus", run_ffmpeg=fake_ffmpeg)
        self.assertEqual(len(calls), 2)
        self.assertEqual(pcm, Completed.stdout)

    def test_ffmpeg_outcomes_map_cleanly(self):
        def missing(data):
            raise FileNotFoundError("no ffmpeg")

        with self.assertRaises(SttFailure) as unavailable:
            decode_audio(b"fake-webm", "audio/webm", run_ffmpeg=missing)
        self.assertEqual(unavailable.exception.http_status, 502)

        class Failed:
            returncode = 1
            stdout = b""
            stderr = b"invalid data"

        with self.assertRaises(SttFailure) as corrupt:
            decode_audio(
                b"fake-webm", "audio/webm", run_ffmpeg=lambda data: Failed()
            )
        self.assertEqual(corrupt.exception.http_status, 422)

        class Endless:
            returncode = 0
            stdout = b"\x00" * 61 * 16000 * 2
            stderr = b""

        with self.assertRaises(SttFailure) as too_long:
            decode_audio(b"fake-webm", "audio/webm", run_ffmpeg=lambda data: Endless())
        self.assertEqual(too_long.exception.http_status, 422)

        import subprocess

        def slow(data):
            raise subprocess.TimeoutExpired("ffmpeg", 30)

        with self.assertRaises(SttFailure) as timed_out:
            decode_audio(b"fake-webm", "audio/webm", run_ffmpeg=slow)
        self.assertEqual(timed_out.exception.http_status, 502)


class FormContracts(unittest.TestCase):
    def test_form_parts_split_and_input_hint_is_ignored(self):
        boundary = "test-boundary"
        body = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="audio"; filename="clip.webm"\r\n'
            b"Content-Type: audio/webm; codecs=opus\r\n\r\n"
            b"fake-audio-bytes\r\n"
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="language"\r\n\r\n'
            b"pl\r\n"
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="input"\r\n\r\n'
            b"answer\r\n"
            b"--test-boundary--\r\n"
        )
        audio, audio_type, language = parse_transcribe_form(
            "multipart/form-data; boundary=" + boundary, body
        )
        self.assertEqual(audio, b"fake-audio-bytes")
        self.assertEqual(audio_type, "audio/webm")
        self.assertEqual(language, "pl")

    def test_form_problems_are_422(self):
        with self.assertRaises(SttFailure) as not_multipart:
            parse_transcribe_form("application/json", b"{}")
        self.assertEqual(not_multipart.exception.http_status, 422)
        boundary = "multipart/form-data; boundary=test-boundary"
        missing_audio = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="language"\r\n\r\n'
            b"en\r\n--test-boundary--\r\n"
        )
        with self.assertRaises(SttFailure) as no_audio:
            parse_transcribe_form(boundary, missing_audio)
        self.assertEqual(no_audio.exception.http_status, 422)
        self.assertIn("audio", no_audio.exception.detail)
        missing_language = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="audio"; filename="c.wav"\r\n'
            b"Content-Type: audio/wav\r\n\r\n"
            b"bytes\r\n--test-boundary--\r\n"
        )
        with self.assertRaises(SttFailure) as no_language:
            parse_transcribe_form(boundary, missing_language)
        self.assertEqual(no_language.exception.http_status, 422)


class SttRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from remote_operator.control import Operator, Validation
        from remote_operator.web import create_app

        class Validator:
            def validate(self, workspace, candidate_digest, *, cancelled=None):
                return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")

        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        seed = root / "seed.py"
        seed.write_text("# seed\n", encoding="utf-8")
        self.operator = Operator(root / "runtime", seed, Validator())
        self.token = "owner-test-credential-" + "x" * 32
        self.auth = {"Authorization": "Bearer " + self.token}
        self.grant = grant()
        self.captured = []

        def serve(request):
            self.captured.append(request)
            status = getattr(self, "meta_status", 200)
            body = getattr(self, "meta_body", {"transcript": "route transcript"})
            headers = getattr(self, "meta_headers", {})
            return httpx.Response(status, json=body, headers=headers)

        app = create_app(
            self.operator,
            self.token,
            model_factory=lambda: None,
            mode="meta",
            allowance_status=lambda: self.grant.budget_status(self.operator.store),
            stt_grant=self.grant,
            stt_key="fixture-secret",
            stt_transport=httpx.MockTransport(serve),
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    async def post_clip(self, audio, *, language="pl", content_type="audio/wav", **kwargs):
        return await self.client.post(
            "/api/stt/transcribe",
            headers=self.auth,
            files={"audio": ("clip", audio, content_type)},
            data={"language": language},
            **kwargs,
        )

    async def test_owner_transcribe_round_trip_and_spend_split(self):
        response = await self.post_clip(wav_bytes(2))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["text"], "route transcript")
        self.assertEqual(body["receipt"]["operation"], "audio")
        self.assertNotIn("fixture-secret", response.text)
        allowance = (await self.client.get("/api/allowance", headers=self.auth)).json()
        self.assertEqual(allowance["requests_used"], 1)
        self.assertEqual(allowance["operations"]["audio"]["requests_used"], 1)
        with self.operator.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    async def test_route_auth_and_grant_gates(self):
        from remote_operator.web import create_app

        keyless = create_app(self.operator, self.token)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=keyless), base_url="http://localhost"
        ) as client:
            refused = await client.post(
                "/api/stt/transcribe",
                headers=self.auth,
                files={"audio": ("c", wav_bytes(1), "audio/wav")},
                data={"language": "en"},
            )
            self.assertEqual(refused.status_code, 409)
            self.assertIn("browser dictation", refused.json()["detail"])
        anonymous = await self.client.post(
            "/api/stt/transcribe",
            files={"audio": ("c", wav_bytes(1), "audio/wav")},
            data={"language": "en"},
        )
        self.assertEqual(anonymous.status_code, 401)

    async def test_route_error_mapping_and_retry_after(self):
        bad = await self.post_clip(wav_bytes(1), language="xx")
        self.assertEqual(bad.status_code, 422)
        oversized = await self.post_clip(b"\x00" * (4 * 1024 * 1024 + 1))
        self.assertEqual(oversized.status_code, 413)
        self.assertIn("4 MB", oversized.json()["detail"])
        # Pre-reserve validation failures consume nothing; a post-reserve decode
        # failure stays consumed like any failed call.
        corrupt = await self.post_clip(b"not a wav file")
        self.assertEqual(corrupt.status_code, 422)
        self.meta_status, self.meta_body = 429, {}
        self.meta_headers = {"retry-after": "9"}
        try:
            limited = await self.post_clip(wav_bytes(1))
        finally:
            del self.meta_status, self.meta_body, self.meta_headers
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers["retry-after"], "9")
        self.assertEqual(
            (await self.client.get("/api/allowance", headers=self.auth)).json()[
                "operations"
            ]["audio"]["requests_used"],
            2,
        )

    async def test_body_cap_exempts_only_the_audio_route(self):
        oversized = b"\x00" * (STT_BODY_MAX_BYTES + 1)
        refused = await self.client.post(
            "/api/stt/transcribe",
            headers=self.auth,
            content=oversized,
        )
        self.assertEqual(refused.status_code, 413)
        small = await self.client.post("/api/tasks", headers=self.auth, content="x" * 65537)
        self.assertEqual(small.status_code, 413)
        accept = await self.client.post(
            "/api/tasks", headers=self.auth, json={"prompt": "still capped small"}
        )
        self.assertEqual(accept.status_code, 200)


class HostedSttTests(unittest.IsolatedAsyncioTestCase):
    CODE = "visitor-access-code-16"

    async def asyncSetUp(self):
        from remote_operator.control import Validation
        from remote_operator.hosted import HostedSessions
        from remote_operator.scoped import ScopedOperators
        from remote_operator.web import create_hosted_app

        class Validator:
            def validate(self, workspace, candidate_digest, *, cancelled=None):
                return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")

        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        project = Path(__file__).resolve().parent.parent
        self.scoped = ScopedOperators(root / "visitors", project / "sample/issue_import.py", Validator)
        self.sessions = HostedSessions(self.CODE)
        self.grant = grant()
        app = create_hosted_app(
            self.scoped,
            self.sessions,
            self.grant,
            stt_transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"transcript": "hosted hi"})
            ),
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        )
        self.auth = {"X-Operator-Request": "1"}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    async def enter(self):
        return await self.client.post("/api/session", json={"code": self.CODE})

    async def post_clip(self, **kwargs):
        params = {
            "headers": self.auth,
            "files": {"audio": ("clip", wav_bytes(1), "audio/wav")},
            "data": {"language": "en"},
        }
        params.update(kwargs)
        return await self.client.post("/api/stt/transcribe", **params)

    async def test_keyless_session_falls_back_and_keyed_session_transcribes(self):
        await self.enter()
        keyless = await self.post_clip()
        self.assertEqual(keyless.status_code, 409)
        self.assertIn("browser dictation", keyless.json()["detail"])
        attached = await self.client.post(
            "/api/session/key", headers=self.auth, json={"api_key": "visitor-fixture-key"}
        )
        self.assertEqual(attached.status_code, 200, attached.text)
        done = await self.post_clip()
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(done.json()["text"], "hosted hi")
        self.assertNotIn("visitor-fixture-key", done.text)

    async def test_missing_header_and_dead_session_map_cleanly(self):
        await self.enter()
        await self.client.post(
            "/api/session/key", headers=self.auth, json={"api_key": "visitor-fixture-key"}
        )
        no_header = await self.client.post(
            "/api/stt/transcribe",
            files={"audio": ("clip", wav_bytes(1), "audio/wav")},
            data={"language": "en"},
        )
        self.assertEqual(no_header.status_code, 403)
        await self.client.delete("/api/session", headers=self.auth)
        dead = await self.post_clip()
        self.assertEqual(dead.status_code, 401)


if __name__ == "__main__":
    unittest.main()
