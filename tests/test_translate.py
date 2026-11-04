"""Server-side translation: acceptance, metering, fallback and allowlist.

All provider traffic is stubbed with httpx.MockTransport; these tests make
zero live Meta calls.
"""

import json
import tempfile
import time
import unittest
import uuid
from dataclasses import asdict
from pathlib import Path

import httpx

from remote_operator.control import Operation, Operator, Validation, digest
from remote_operator.meta import InferenceGrant, MetaModel
from remote_operator.scripted import ScriptedModel
from remote_operator.store import Store
from remote_operator.supervisor import Supervisor
from remote_operator.translate import (
    MetaTranslator,
    TranslationError,
    normalize_input,
    normalize_language,
    translate_text,
    translation_manifest,
    translator_from_model,
)


class Validator:
    def validate(self, workspace, candidate_digest, *, cancelled=None):
        return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")


class AskOnceModel:
    """Ask one question on the first call, then follow the scripted flow."""

    name = "ASK ONCE TEST DOUBLE"
    _scripted = ScriptedModel()

    def next_operation(self, history, *, prompt):
        names = [entry["name"] for entry in history]
        if "ask_owner" not in names:
            return Operation("ask_owner", {"question": "Which format?"})
        return self._scripted.next_operation(history, prompt=prompt)


def _operator(root):
    seed = root / "seed.py"
    seed.write_text("# seed\n", encoding="utf-8")
    return Operator(root / "runtime", seed, Validator())


def _grant(max_requests=5, **overrides):
    values = {
        "grant_id": "fixture-translate",
        "model": "muse-spark-fixture",
        "expires_at": time.time() + 300,
        "max_requests": max_requests,
        "max_input_bytes": 100000,
        "max_output_tokens": 1024,
        "approved_usd": "1",
        "input_usd_per_million": "1",
        "output_usd_per_million": "1",
        "credits_verified": True,
        "local_spending_limit_accepted": True,
    }
    values.update(overrides)
    return InferenceGrant(**values)


def _translation_response(text, **extra):
    body = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }
    body.update(extra)
    return body


def _tool_response():
    return {
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "status": "completed",
                "call_id": "call_1",
                "name": "read_file",
                "arguments": json.dumps({"path": "issue_import.py"}),
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }


class LanguageValidationTests(unittest.TestCase):
    def test_allowlist_is_exactly_en_and_pl(self):
        self.assertEqual(normalize_language("en"), "en")
        self.assertEqual(normalize_language("pl"), "pl")
        self.assertEqual(normalize_language("EN"), "en")
        self.assertEqual(normalize_language("Pl"), "pl")
        for bad in ("de", "en-US", "pl-PL", "", "xx", None, 123):
            with self.subTest(tag=bad), self.assertRaises(ValueError):
                normalize_language(bad)

    def test_english_fast_path_needs_no_translator(self):
        normalized, receipt = normalize_input(None, "Hello", "en")
        self.assertEqual(
            (
                normalized.working,
                normalized.source,
                normalized.translated,
                normalized.translator_id,
            ),
            ("Hello", "Hello", False, "none"),
        )
        self.assertIsNone(receipt)

    def test_non_english_without_translator_blocks(self):
        with self.assertRaises(TranslationError) as raised:
            normalize_input(None, "Cześć", "pl")
        self.assertEqual(raised.exception.status, 409)
        self.assertIn("Translation unavailable", str(raised.exception))

    def test_bad_tag_rejected_before_any_spend(self):
        with tempfile.TemporaryDirectory() as name:
            store = Store(Path(name) / "tasks.sqlite3")
            translator = MetaTranslator(lambda: "key", _grant(), store)
            with self.assertRaises(ValueError):
                normalize_input(translator, "Hello", "de")


class MetaTranslatorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = Store(self.root / "tasks.sqlite3")
        self.grant = _grant()
        self.calls = []

    def tearDown(self):
        self.directory.cleanup()

    def translator(self, handler=None, *, grant=None, key="fixture-secret"):
        active = grant if grant is not None else self.grant

        def serve(request):
            self.calls.append(json.loads(request.content))
            if handler is not None:
                return handler(request)
            return httpx.Response(200, json=_translation_response("Hello world"))

        return MetaTranslator(lambda: key, active, self.store, transport=httpx.MockTransport(serve))

    def test_contract_shape(self):
        translator = self.translator()
        self.assertEqual(translator.id, "meta:muse-spark-fixture")
        self.assertEqual(translator.supported_languages(), ["en", "pl"])
        self.assertTrue(translator.needs_key())
        self.assertGreater(float(translator.estimate_usd("Cześć world")), 0)

    def test_success_uses_narrow_chat_call(self):
        result = self.translator().translate("Cześć world", "pl")
        self.assertEqual(
            (result.text, result.source_lang, result.translator_id),
            ("Hello world", "pl", "meta:muse-spark-fixture"),
        )
        (payload,) = self.calls
        self.assertEqual(payload["model"], "muse-spark-fixture")
        self.assertNotIn("tools", payload)
        self.assertIn("Polish", payload["instructions"])
        self.assertIn("English", payload["instructions"])
        self.assertFalse(payload["store"])
        self.assertEqual(payload["max_output_tokens"], 1024)
        self.assertEqual(payload["input"][0]["content"][0]["text"], "Cześć world")

    def test_success_records_receipt_and_reserves_grant(self):
        translator = self.translator()
        translator.translate("Cześć", "pl")
        receipt = translator.last_receipt
        self.assertEqual(receipt["provider"], "meta")
        self.assertEqual(receipt["op"], "translate")
        self.assertEqual(receipt["translator"], "meta:muse-spark-fixture")
        self.assertEqual(receipt["source_lang"], "pl")
        self.assertEqual(receipt["chars_in"], 5)
        self.assertEqual(receipt["chars_out"], len("Hello world"))
        self.assertEqual(receipt["grant_id"], "fixture-translate")
        self.assertIsNotNone(receipt["usage"])
        status = self.store.allowance_status(
            "fixture-translate",
            digest(json.dumps(asdict(self.grant), sort_keys=True)),
            5,
            "1",
        )
        self.assertEqual(status["requests_used"], 1)

    def test_english_passthrough_sends_nothing(self):
        translator = self.translator(handler=lambda request: self.fail("Network attempted"))
        result = translator.translate("Hello", "en")
        self.assertEqual(result.text, "Hello")
        self.assertEqual(result.translator_id, "none")
        self.assertIsNone(translator.last_receipt)
        self.assertEqual(self.calls, [])

    def test_empty_and_over_limit_rejected_without_spend(self):
        translator = self.translator(handler=lambda request: self.fail("Network attempted"))
        for bad in ("", "   "):
            with self.assertRaises(ValueError):
                translator.translate(bad, "pl")
        with self.assertRaises(ValueError):
            translator.translate("x" * 4001, "pl")
        with self.assertRaises(ValueError):
            translator.translate("Cześć", "de")
        self.assertEqual(self.calls, [])

    def test_missing_grant_or_key_blocks_before_transport(self):
        translator = self.translator(grant=None, key="")
        with self.assertRaises(TranslationError) as raised:
            translator.translate("Cześć", "pl")
        self.assertEqual(raised.exception.status, 409)
        translator = self.translator(key="")
        with self.assertRaises(TranslationError) as raised:
            translator.translate("Cześć", "pl")
        self.assertEqual(raised.exception.status, 409)
        self.assertIn("Translation unavailable", str(raised.exception))
        expired = self.translator(grant=_grant(expires_at=time.time() - 1))
        with self.assertRaises(TranslationError) as raised:
            expired.translate("Cześć", "pl")
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(self.calls, [])
        status = self.store.allowance_status(
            "fixture-translate",
            digest(json.dumps(asdict(self.grant), sort_keys=True)),
            5,
            "1",
        )
        self.assertEqual(status["requests_used"], 0)

    def test_key_source_failure_blocks(self):
        translator = MetaTranslator(
            lambda: (_ for _ in ()).throw(KeyError("gone")),
            self.grant,
            self.store,
            transport=httpx.MockTransport(lambda request: self.fail("Network attempted")),
        )
        with self.assertRaises(TranslationError) as raised:
            translator.translate("Cześć", "pl")
        self.assertEqual(raised.exception.status, 409)

    def test_exhausted_allowance_sends_nothing(self):
        translator = self.translator(grant=_grant(max_requests=1))
        translator.translate("Cześć", "pl")
        with self.assertRaises(TranslationError) as raised:
            translator.translate("Cześć", "pl")
        self.assertEqual(raised.exception.status, 409)
        self.assertIn("exhausted", str(raised.exception))
        self.assertEqual(len(self.calls), 1)

    def test_provider_refusal_maps_to_409(self):
        for status in (401, 403):
            translator = self.translator(
                handler=lambda request, status=status: httpx.Response(status, json={"error": {}})
            )
            with self.subTest(status=status), self.assertRaises(TranslationError) as raised:
                translator.translate("Cześć", "pl")
            self.assertEqual(raised.exception.status, 409)
            self.assertIn("refused", str(raised.exception))

    def test_rate_limit_maps_to_429_with_retry_after(self):
        translator = self.translator(
            handler=lambda request: httpx.Response(429, headers={"Retry-After": "7"}, json={})
        )
        with self.assertRaises(TranslationError) as raised:
            translator.translate("Cześć", "pl")
        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(raised.exception.headers, {"Retry-After": "7"})

    def test_provider_outages_map_to_502(self):
        def timeout(request):
            raise httpx.ConnectTimeout("boom")

        cases = [
            lambda request: httpx.Response(500, json={}),
            lambda request: httpx.Response(400, json={}),
            timeout,
            lambda request: httpx.Response(200, content=b"not json"),
            lambda request: httpx.Response(200, json={"status": "completed"}),
            lambda request: httpx.Response(200, json={"status": "incomplete", "output": []}),
            lambda request: httpx.Response(200, json=_translation_response("ok", store=True)),
            lambda request: httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "c",
                            "name": "read_file",
                            "arguments": "{}",
                        }
                    ],
                },
            ),
            lambda request: httpx.Response(
                200, json={"status": "completed", "output": [{"type": "message"}]}
            ),
        ]
        roomy = _grant(max_requests=20)
        for index, handler in enumerate(cases):
            translator = self.translator(handler=handler, grant=roomy)
            with self.subTest(case=index), self.assertRaises(TranslationError) as raised:
                translator.translate("Cześć", "pl")
            self.assertEqual(raised.exception.status, 502)

    def test_empty_translation_of_nonempty_input_is_502(self):
        for text in ("", "   "):
            translator = self.translator(
                handler=lambda request, text=text: httpx.Response(
                    200, json=_translation_response(text)
                )
            )
            with self.subTest(text=text), self.assertRaises(TranslationError) as raised:
                translator.translate("Cześć", "pl")
            self.assertEqual(raised.exception.status, 502)
            self.assertIn("empty", str(raised.exception))

    def test_credential_echo_is_redacted(self):
        translator = self.translator(
            handler=lambda request: httpx.Response(
                200, json=_translation_response("says fixture-secret loudly")
            )
        )
        result = translator.translate("Cześć", "pl")
        self.assertNotIn("fixture-secret", result.text)
        self.assertIn("[redacted]", result.text)

    def test_translate_text_returns_result_and_receipt(self):
        result, receipt = translate_text(
            lambda: "fixture-secret",
            self.grant,
            self.store,
            "Cześć",
            "pl",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=_translation_response("Hi"))
            ),
        )
        self.assertEqual(result.text, "Hi")
        self.assertEqual(receipt["op"], "translate")

    def test_translation_shares_the_run_grant_ledger(self):
        model = MetaModel(
            "fixture-secret",
            self.grant,
            self.store,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=_tool_response())
            ),
        )
        model.next_operation([], prompt="task")
        self.translator().translate("Cześć", "pl")
        status = self.store.allowance_status(
            "fixture-translate",
            digest(json.dumps(asdict(self.grant), sort_keys=True)),
            5,
            "1",
        )
        self.assertEqual(status["requests_used"], 2)


class TranslatorFromModelTests(unittest.TestCase):
    def test_scripted_and_grantless_models_yield_no_translator(self):
        with tempfile.TemporaryDirectory() as name:
            store = Store(Path(name) / "tasks.sqlite3")
            self.assertIsNone(translator_from_model(ScriptedModel()))
            self.assertIsNone(translator_from_model(MetaModel("key", None, store)))
            self.assertIsNone(translator_from_model(object()))

    def test_meta_model_yields_metered_translator(self):
        with tempfile.TemporaryDirectory() as name:
            store = Store(Path(name) / "tasks.sqlite3")
            grant = _grant()
            transport = httpx.MockTransport(
                lambda request: httpx.Response(200, json=_translation_response("Hi"))
            )
            model = MetaModel("fixture-secret", grant, store, transport=transport)
            translator = translator_from_model(model)
            self.assertIsInstance(translator, MetaTranslator)
            self.assertEqual(translator.id, "meta:muse-spark-fixture")
            result = translator.translate("Cześć", "pl")
            self.assertEqual(result.text, "Hi")

    def test_session_credential_helpers(self):
        with tempfile.TemporaryDirectory() as name:
            store = Store(Path(name) / "tasks.sqlite3")
            static = MetaModel("static-key", _grant(), store)
            self.assertEqual(static.session_credential(), "static-key")
            self.assertIsNone(static.http_transport)
            sourced = MetaModel("", _grant(), store, key_source=lambda: "session-key")
            self.assertEqual(sourced.session_credential(), "session-key")
            missing = MetaModel("", _grant(), store, key_source=lambda: "")
            with self.assertRaises(ValueError):
                missing.session_credential()


class SupervisorTranslationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.operator = _operator(Path(self.directory.name))
        self.grant = _grant()
        self.calls = []

    def tearDown(self):
        self.directory.cleanup()

    def supervisor(self, handler=None):
        def serve(request):
            self.calls.append(json.loads(request.content))
            if handler is not None:
                return handler(request)
            return httpx.Response(200, json=_translation_response("Hello world"))

        translator = MetaTranslator(
            lambda: "fixture-secret",
            self.grant,
            self.operator.store,
            transport=httpx.MockTransport(serve),
        )
        return Supervisor(self.operator, translator=translator)

    def test_english_message_posts_exactly_as_before(self):
        supervisor = Supervisor(self.operator)
        posted = supervisor.post_message(ScriptedModel(), supervisor.open_thread("t")["id"], "req")
        self.assertIn("SCRIPTED OFFLINE REFERENCE", posted["reply"])
        thread = supervisor._thread(supervisor.list_threads()[0]["id"])
        (turn, _) = thread["history"]
        self.assertEqual(
            (turn["text"], turn["source_text"], turn["translated"], turn["translator"]),
            ("req", "req", False, "none"),
        )
        self.assertEqual(turn["source_lang"], "en")
        kinds = [event["kind"] for event in self.operator.store.events(thread["id"])]
        self.assertNotIn("inference_receipt", kinds)

    def test_polish_message_without_translator_stores_nothing(self):
        supervisor = Supervisor(self.operator)
        thread_id = supervisor.open_thread("t")["id"]
        with self.assertRaises(TranslationError) as raised:
            supervisor.post_message(ScriptedModel(), thread_id, "Cześć", language="pl")
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(supervisor._thread(thread_id)["history"], [])

    def test_polish_message_is_normalized_before_history(self):
        supervisor = self.supervisor()
        thread_id = supervisor.open_thread("t")["id"]
        supervisor.post_message(ScriptedModel(), thread_id, "Cześć world", language="pl")
        thread = supervisor._thread(thread_id)
        (turn, _) = thread["history"]
        self.assertEqual(
            (turn["text"], turn["source_text"], turn["source_lang"]),
            ("Hello world", "Cześć world", "pl"),
        )
        self.assertTrue(turn["translated"])
        self.assertEqual(turn["translator"], "meta:muse-spark-fixture")
        receipts = [
            event["payload"]
            for event in self.operator.store.events(thread_id)
            if event["kind"] == "inference_receipt"
        ]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["translator"], "meta:muse-spark-fixture")
        self.assertEqual(receipts[0]["source_lang"], "pl")

    def test_translation_failure_stores_no_turn(self):
        supervisor = self.supervisor(handler=lambda request: httpx.Response(500, json={}))
        thread_id = supervisor.open_thread("t")["id"]
        with self.assertRaises(TranslationError) as raised:
            supervisor.post_message(ScriptedModel(), thread_id, "Cześć", language="pl")
        self.assertEqual(raised.exception.status, 502)
        self.assertEqual(supervisor._thread(thread_id)["history"], [])

    def test_bad_language_rejected_before_store(self):
        supervisor = self.supervisor(handler=lambda request: self.fail("Network attempted"))
        with self.assertRaises(ValueError):
            supervisor.open_thread("t", language="de")
        thread_id = supervisor.open_thread("t")["id"]
        with self.assertRaises(ValueError):
            supervisor.post_message(ScriptedModel(), thread_id, "x", language="de")
        self.assertEqual(supervisor._thread(thread_id)["history"], [])
        self.assertEqual(self.calls, [])

    def test_topic_translation_shape_and_manifest(self):
        supervisor = self.supervisor()
        opened = supervisor.open_thread("Cześć temat", language="pl")
        self.assertEqual(opened["topic"], "Hello world")
        thread = supervisor._thread(opened["id"])
        self.assertEqual(
            (thread["prompt"], thread["prompt_source"], thread["prompt_lang"]),
            ("Hello world", "Cześć temat", "pl"),
        )
        self.assertEqual(thread["prompt_translator"], "meta:muse-spark-fixture")
        supervisor.post_message(ScriptedModel(), opened["id"], "Do it")
        plan = supervisor.write_plan(opened["id"])
        self.assertIn("Hello world", plan["markdown"])
        self.assertNotIn("Cześć", plan["markdown"])
        manifest = supervisor._thread(opened["id"])["translation"]
        self.assertEqual(manifest["langs"], ["pl"])
        self.assertEqual(manifest["translators"], ["meta:muse-spark-fixture"])
        self.assertFalse(manifest["untranslated"])
        job_id = supervisor.approve_plan(opened["id"], plan["nonce"])["run_id"]
        job = supervisor.run_status(job_id)
        self.assertEqual(job["translation_manifest"], manifest)
        listed = supervisor.list_runs()[0]
        self.assertEqual(listed["translation_manifest"], manifest)

    def test_owner_reply_path_normalizes_too(self):
        supervisor = self.supervisor()
        posted = supervisor.reply_to_owner_thread(ScriptedModel(), "Cześć", language="pl")
        thread = supervisor._thread(posted["thread_id"])
        self.assertEqual(thread["history"][0]["text"], "Hello world")
        self.assertTrue(thread["history"][0]["translated"])

    def test_legacy_threads_read_as_english(self):
        thread_id = uuid.uuid4().hex
        thread = {
            "id": thread_id,
            "prompt": "legacy",
            "status": "open",
            "supervisor_thread": True,
            "history": [{"role": "user", "text": "old words"}],
            "plan": None,
            "approval": None,
        }
        self.operator.store.save(thread, "test_legacy", {})
        supervisor = Supervisor(self.operator)
        supervisor.write_plan(thread_id)
        manifest = supervisor._thread(thread_id)["translation"]
        self.assertEqual(manifest, {"langs": [], "translators": [], "untranslated": False})

    def test_manifest_flags_untranslated_non_english(self):
        supervisor = Supervisor(self.operator)
        thread_id = supervisor.open_thread("t")["id"]
        supervisor.post_message(ScriptedModel(), thread_id, "req")
        thread = supervisor._thread(thread_id)
        thread["history"][0]["source_lang"] = "pl"
        thread["history"][0]["translated"] = False
        self.operator.store.save(thread, "test_flag", {})
        manifest = translation_manifest(supervisor._thread(thread_id))
        self.assertTrue(manifest["untranslated"])


class AnswerTranslationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.operator = _operator(Path(self.directory.name))
        self.grant = _grant()

    def tearDown(self):
        self.directory.cleanup()

    def _parked(self):
        task = self.operator.submit("demo task")
        parked = self.operator.run(task["id"], AskOnceModel())
        self.assertEqual(parked["status"], "waiting_on_owner")
        return task["id"]

    def _translator(self, handler=None):
        def serve(request):
            if handler is not None:
                return handler(request)
            return httpx.Response(200, json=_translation_response("JSON, please"))

        return MetaTranslator(
            lambda: "fixture-secret",
            self.grant,
            self.operator.store,
            transport=httpx.MockTransport(serve),
        )

    def test_english_answer_shape(self):
        task_id = self._parked()
        answered = self.operator.answer(task_id, "JSON, please")
        self.assertEqual(answered["status"], "paused")
        result = answered["history"][-1]["result"]
        self.assertEqual(
            (result["text"], result["source_text"], result["translated"], result["translator"]),
            ("JSON, please", "JSON, please", False, "none"),
        )

    def test_polish_answer_without_translator_keeps_task_parked(self):
        task_id = self._parked()
        with self.assertRaises(TranslationError) as raised:
            self.operator.answer(task_id, "Po polsku", language="pl")
        self.assertEqual(raised.exception.status, 409)
        task = self.operator.store.get(task_id)
        self.assertEqual(task["status"], "waiting_on_owner")
        self.assertFalse(any(entry.get("name") == "owner_answer" for entry in task["history"]))

    def test_polish_answer_is_normalized_before_resume(self):
        task_id = self._parked()
        answered = self.operator.answer(
            task_id, "Po polsku", language="pl", translator=self._translator()
        )
        self.assertEqual(answered["status"], "paused")
        result = answered["history"][-1]["result"]
        self.assertEqual(
            (result["text"], result["source_text"], result["source_lang"]),
            ("JSON, please", "Po polsku", "pl"),
        )
        self.assertTrue(result["translated"])
        kinds = [event["kind"] for event in self.operator.store.events(task_id)]
        self.assertIn("inference_receipt", kinds)

    def test_bad_language_rejected(self):
        task_id = self._parked()
        with self.assertRaises(ValueError):
            self.operator.answer(task_id, "x", language="de", translator=self._translator())


class TranslateRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from remote_operator.web import create_app

        self.directory = tempfile.TemporaryDirectory()
        self.operator = _operator(Path(self.directory.name))
        self.token = "owner-test-credential-" + "x" * 32
        self.auth = {"Authorization": "Bearer " + self.token}
        self.grant = _grant()
        self.calls = []
        parked = self.operator.submit("parked task")
        self.operator.run(parked["id"], AskOnceModel())
        self.parked_id = parked["id"]
        self.scripted = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.operator, self.token)),
            base_url="http://localhost",
        )

    async def asyncTearDown(self):
        await self.scripted.aclose()
        self.directory.cleanup()

    def _meta_client(self, handler=None):
        from remote_operator.web import create_app

        def serve(request):
            payload = json.loads(request.content)
            if "tools" in payload:
                return httpx.Response(200, json=_tool_response())
            self.calls.append(payload)
            if handler is not None:
                return handler(request)
            return httpx.Response(200, json=_translation_response("Hello world"))

        factory = lambda: MetaModel(
            "fixture-secret",
            self.grant,
            self.operator.store,
            transport=httpx.MockTransport(serve),
        )
        app = create_app(
            self.operator,
            self.token,
            model_factory=factory,
            mode="meta",
            allowance_status=lambda: self.grant.budget_status(self.operator.store),
        )
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        )

    async def test_scripted_polish_message_blocked_with_retry(self):
        opened = await self.scripted.post(
            "/api/supervisor/threads", headers=self.auth, json={"topic": "t"}
        )
        thread_id = opened.json()["id"]
        blocked = await self.scripted.post(
            f"/api/supervisor/threads/{thread_id}/messages",
            headers=self.auth,
            json={"text": "Cześć", "language": "pl"},
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("Translation unavailable", blocked.json()["detail"])
        detail = await self.scripted.get(f"/api/supervisor/threads/{thread_id}", headers=self.auth)
        self.assertEqual(detail.json()["thread"]["history"], [])

    async def test_unknown_language_is_422(self):
        opened = await self.scripted.post(
            "/api/supervisor/threads", headers=self.auth, json={"topic": "t"}
        )
        thread_id = opened.json()["id"]
        for path, body in (
            (f"/api/supervisor/threads/{thread_id}/messages", {"text": "x", "language": "de"}),
            ("/api/supervisor/threads", {"topic": "x", "language": "en-US"}),
            (f"/api/tasks/{self.parked_id}/answer", {"text": "x", "language": "xx"}),
        ):
            response = await self.scripted.post(path, headers=self.auth, json=body)
            self.assertEqual(response.status_code, 422, path)

    async def test_scripted_polish_topic_and_answer_blocked(self):
        blocked = await self.scripted.post(
            "/api/supervisor/threads", headers=self.auth, json={"topic": "Cześć", "language": "pl"}
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(
            (await self.scripted.get("/api/supervisor/threads", headers=self.auth)).json(), []
        )
        answer = await self.scripted.post(
            f"/api/tasks/{self.parked_id}/answer",
            headers=self.auth,
            json={"text": "Po polsku", "language": "pl"},
        )
        self.assertEqual(answer.status_code, 409)
        task = self.operator.store.get(self.parked_id)
        self.assertEqual(task["status"], "waiting_on_owner")

    async def test_meta_polish_message_translates_and_replies(self):
        client = self._meta_client()
        try:
            opened = await client.post(
                "/api/supervisor/threads", headers=self.auth, json={"topic": "t"}
            )
            thread_id = opened.json()["id"]
            posted = await client.post(
                f"/api/supervisor/threads/{thread_id}/messages",
                headers=self.auth,
                json={"text": "Cześć world", "language": "pl"},
            )
            self.assertEqual(posted.status_code, 200, posted.text)
            self.assertEqual(posted.json()["route"], "meta")
            detail = await client.get(f"/api/supervisor/threads/{thread_id}", headers=self.auth)
            (turn, _) = detail.json()["thread"]["history"]
            self.assertEqual(
                (turn["text"], turn["source_text"], turn["source_lang"], turn["translated"]),
                ("Hello world", "Cześć world", "pl", True),
            )
            (payload,) = self.calls
            self.assertNotIn("tools", payload)
            self.assertIn("Polish", payload["instructions"])
            allowance = await client.get("/api/allowance", headers=self.auth)
            self.assertEqual(allowance.json()["requests_used"], 2)
        finally:
            await client.aclose()

    async def test_meta_translation_outage_is_502_with_nothing_stored(self):
        client = self._meta_client(handler=lambda request: httpx.Response(500, json={}))
        try:
            opened = await client.post(
                "/api/supervisor/threads", headers=self.auth, json={"topic": "t"}
            )
            thread_id = opened.json()["id"]
            failed = await client.post(
                f"/api/supervisor/threads/{thread_id}/messages",
                headers=self.auth,
                json={"text": "Cześć", "language": "pl"},
            )
            self.assertEqual(failed.status_code, 502)
            detail = await client.get(f"/api/supervisor/threads/{thread_id}", headers=self.auth)
            self.assertEqual(detail.json()["thread"]["history"], [])
        finally:
            await client.aclose()

    async def test_meta_rate_limit_is_429(self):
        client = self._meta_client(
            handler=lambda request: httpx.Response(429, headers={"Retry-After": "3"}, json={})
        )
        try:
            opened = await client.post(
                "/api/supervisor/threads", headers=self.auth, json={"topic": "t"}
            )
            thread_id = opened.json()["id"]
            limited = await client.post(
                f"/api/supervisor/threads/{thread_id}/messages",
                headers=self.auth,
                json={"text": "Cześć", "language": "pl"},
            )
            self.assertEqual(limited.status_code, 429)
            self.assertEqual(limited.headers.get("retry-after"), "3")
        finally:
            await client.aclose()

    async def test_english_still_posts_on_both_modes(self):
        opened = await self.scripted.post(
            "/api/supervisor/threads", headers=self.auth, json={"topic": "t"}
        )
        thread_id = opened.json()["id"]
        posted = await self.scripted.post(
            f"/api/supervisor/threads/{thread_id}/messages",
            headers=self.auth,
            json={"text": "plain english"},
        )
        self.assertEqual(posted.status_code, 200)
        client = self._meta_client()
        try:
            posted = await client.post(
                f"/api/supervisor/threads/{thread_id}/messages",
                headers=self.auth,
                json={"text": "more english", "language": "en"},
            )
            self.assertEqual(posted.status_code, 200, posted.text)
            self.assertEqual(self.calls, [])
        finally:
            await client.aclose()


class TranslateUiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from remote_operator.web import create_app

        self.directory = tempfile.TemporaryDirectory()
        self.operator = _operator(Path(self.directory.name))
        self.token = "owner-test-credential-" + "x" * 32
        app = create_app(self.operator, self.token)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    async def test_language_select_and_banner_are_served(self):
        page = await self.client.get("/")
        self.assertIn('id="supervisorLang"', page.text)
        self.assertIn('id="supervisorTranslationNote"', page.text)

    async def test_script_tags_language_and_renders_provenance(self):
        script = await self.client.get("/app.js")
        for snippet in (
            'language: element("supervisorLang").value',
            "dictateLangTag",
            "Show working English (",
            "Includes text translated from",
            "translation_manifest",
            "supervisorTranslationNote",
        ):
            self.assertIn(snippet, script.text)
