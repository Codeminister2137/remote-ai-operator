"""Baseten event-model contracts exercised with local HTTPX fixtures, never live inference."""

import argparse
import json
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import httpx

from remote_operator.baseten import (
    ENDPOINT,
    MODEL,
    BasetenFailure,
    BasetenGrant,
    BasetenModel,
)
from remote_operator.control import Operator
from remote_operator.store import Store


def chat_response(name="read_file", arguments=None, call_id="call_1", **extra):
    return {
        "model": MODEL,
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(
                                    arguments
                                    if arguments is not None
                                    else {"path": "issue_import.py"}
                                ),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        **extra,
    }


def content_response(text, **extra):
    return {
        "model": MODEL,
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        **extra,
    }


class BasetenContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = Store(self.root / "tasks.sqlite3")
        self.grant = BasetenGrant(
            "synthetic-fixture-only",
            MODEL,
            time.time() + 300,
            5,
            100000,
            1024,
            "1",
            "1",
            "1",
            True,
            True,
        )
        self.requests = []

    def tearDown(self):
        self.directory.cleanup()

    def actor(self, handler=None, grant=None, **kwargs):
        def serve(request):
            self.requests.append(request)
            if handler is not None:
                return handler(request)
            return httpx.Response(200, json=chat_response())

        return BasetenModel(
            "fixture-secret",
            grant if grant is not None else self.grant,
            self.store,
            transport=httpx.MockTransport(serve),
            **kwargs,
        )

    def test_request_targets_event_model_and_endpoint(self):
        actor = self.actor()
        operation = actor.next_operation([], prompt="task")
        self.assertEqual(operation.name, "read_file")
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(str(request.url), ENDPOINT)
        self.assertEqual(request.headers["Authorization"], "Bearer fixture-secret")
        body = json.loads(request.content)
        self.assertEqual(body["model"], "meta-models/muse-spark-open-1026")
        self.assertEqual(body["max_tokens"], 1024)
        self.assertEqual(body["tool_choice"], "auto")
        self.assertNotIn("reasoning_effort", body)
        self.assertEqual(
            [tool["function"]["name"] for tool in body["tools"]],
            ["read_file", "write_file", "validate", "finish", "ask_owner"],
        )
        self.assertEqual([message["role"] for message in body["messages"]], ["system", "user"])
        self.assertEqual(actor.name, "Baseten / " + MODEL)

    def test_explicit_reasoning_effort_is_sent(self):
        def serve(request):
            body = json.loads(request.content)
            self.assertEqual(body["reasoning_effort"], "low")
            return httpx.Response(200, json=chat_response())

        actor = self.actor(serve, reasoning_effort="low")
        actor.next_operation([], prompt="task")
        self.assertEqual(actor.last_receipt["reasoning_effort"], "low")
        default = self.actor()
        default.next_operation([], prompt="task")
        self.assertEqual(default.last_receipt["reasoning_effort"], "provider_default")
        for effort in ("Low", "low!", "", "x" * 17, 3):
            with self.subTest(effort=effort), self.assertRaises(ValueError):
                BasetenModel("fixture-secret", self.grant, self.store, reasoning_effort=effort)

    def test_tool_call_returns_operation_and_receipt(self):
        actor = self.actor()
        operation = actor.next_operation([], prompt="task")
        self.assertEqual(operation.name, "read_file")
        self.assertEqual(operation.arguments, {"path": "issue_import.py"})
        self.assertEqual(operation.call_id, "call_1")
        receipt = actor.last_receipt
        self.assertEqual(receipt["provider"], "baseten")
        self.assertEqual(receipt["endpoint"], ENDPOINT)
        self.assertEqual(receipt["model"], MODEL)
        self.assertEqual(receipt["grant_id"], "synthetic-fixture-only")
        self.assertEqual(receipt["spending_control"], "local_estimate_not_provider_cap")
        self.assertEqual(receipt["reasoning_effort"], "provider_default")
        self.assertEqual(receipt["continuity"], "initial")
        self.assertEqual(receipt["finish_reason"], "tool_calls")
        self.assertEqual(receipt["reported_model"], MODEL)
        self.assertEqual(
            receipt["usage"],
            {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )
        self.assertEqual(receipt["local_allowance"]["requests_used"], 1)

    def test_history_replays_as_observable_tool_pairs(self):
        bodies = []

        def serve(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json=chat_response())

        actor = self.actor(serve)
        history = [
            {
                "name": "read_file",
                "arguments": {"path": "issue_import.py"},
                "call_id": "call_9",
                "result": {"content": "seed"},
            }
        ]
        actor.next_operation(history, prompt="task")
        messages = bodies[0]["messages"]
        self.assertEqual(
            [message["role"] for message in messages],
            ["system", "user", "assistant", "tool"],
        )
        replayed = messages[2]["tool_calls"][0]
        self.assertEqual(replayed["id"], "call_9")
        self.assertEqual(replayed["function"]["name"], "read_file")
        self.assertEqual(messages[3]["tool_call_id"], "call_9")
        self.assertEqual(actor.last_receipt["continuity"], "observable_history_only")

    def test_content_response_finishes_and_empty_is_refused(self):
        actor = self.actor(lambda request: httpx.Response(200, json=content_response("Done.")))
        operation = actor.next_operation([], prompt="task")
        self.assertEqual(operation.name, "finish")
        self.assertEqual(operation.arguments, {})
        self.assertEqual(operation.summary, "Done.")
        empty = self.actor(lambda request: httpx.Response(200, json=content_response("")))
        with self.assertRaises(BasetenFailure) as stopped:
            empty.next_operation([], prompt="task")
        self.assertEqual(stopped.exception.category, "protocol")

    def test_length_finish_is_refused_as_incomplete(self):
        payload = content_response("Truncated")
        payload["choices"][0]["finish_reason"] = "length"
        actor = self.actor(lambda request: httpx.Response(200, json=payload))
        with self.assertRaises(BasetenFailure) as stopped:
            actor.next_operation([], prompt="task")
        self.assertEqual(stopped.exception.category, "incomplete")
        self.assertEqual(actor.last_receipt["finish_reason"], "length")

    def test_grant_requires_exact_event_model(self):
        for model in ("meta-models/muse-spark-open", "muse-spark-fixture", "", None):
            with self.subTest(model=model):
                grant = BasetenGrant(
                    "synthetic-fixture-only",
                    model,
                    time.time() + 300,
                    5,
                    100000,
                    1024,
                    "1",
                    "1",
                    "1",
                    True,
                    True,
                )
                with self.assertRaisesRegex(ValueError, "exactly"):
                    grant.check()
        self.grant.check()

    def test_grant_requires_verified_credits_limits_expiry_and_bounds(self):
        base = asdict(self.grant)
        cases = [
            ({**base, "credits_verified": False}, "Verified credits"),
            ({**base, "local_spending_limit_accepted": False}, "Verified credits"),
            ({**base, "grant_id": "bad id!"}, "Explicit grant"),
            ({**base, "expires_at": time.time() - 1}, "expired"),
            ({**base, "max_requests": 0}, "finite inference allowance"),
            ({**base, "max_requests": 101}, "finite inference allowance"),
            ({**base, "max_input_bytes": 0}, "finite inference allowance"),
            ({**base, "max_output_tokens": 16385}, "finite inference allowance"),
            ({**base, "approved_usd": "0"}, "Positive finite"),
            ({**base, "input_usd_per_million": "-1"}, "Positive finite"),
            ({**base, "output_usd_per_million": "nan"}, "Positive finite"),
        ]
        for fields, message in cases:
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, message):
                BasetenGrant(**fields).check()

    def test_missing_grant_or_key_is_denied_before_transport(self):
        def explode(request):
            self.fail("Network attempted")

        transport = httpx.MockTransport(explode)
        for actor in (
            BasetenModel("fixture-secret", None, self.store, transport=transport),
            BasetenModel("", self.grant, self.store, transport=transport),
        ):
            with self.assertRaisesRegex(ValueError, "disabled"):
                actor.next_operation([], prompt="task")
        self.assertEqual(self.requests, [])

    def test_input_byte_cap_is_refused_before_transport(self):
        def explode(request):
            self.fail("Network attempted")

        tiny = BasetenGrant(**{**asdict(self.grant), "max_input_bytes": 16})
        actor = BasetenModel(
            "fixture-secret", tiny, self.store, transport=httpx.MockTransport(explode)
        )
        with self.assertRaisesRegex(ValueError, "byte limit"):
            actor.next_operation([], prompt="task")

    def test_allowance_exhausts_across_calls_without_resending(self):
        single = BasetenGrant(**{**asdict(self.grant), "max_requests": 1})
        actor = self.actor(grant=single)
        actor.next_operation([], prompt="task")
        with self.assertRaisesRegex(ValueError, "allowance exhausted"):
            actor.next_operation([], prompt="task")
        self.assertEqual(len(self.requests), 1)
        status = single.budget_status(self.store)
        self.assertEqual(status["provider"], "baseten")
        self.assertEqual(status["model"], MODEL)
        self.assertEqual(status["requests_used"], 1)
        self.assertEqual(status["requests_limit"], 1)

    def test_http_error_mapping(self):
        cases = [
            (401, "authentication"),
            (403, "authentication"),
            (429, "rate_limit"),
            (500, "availability"),
            (400, "request"),
        ]
        for status, category in cases:
            with self.subTest(status=status):

                def serve(request, status=status):
                    return httpx.Response(status, json={"error": {"message": "synthetic refusal"}})

                actor = self.actor(serve)
                with self.assertRaises(BasetenFailure) as stopped:
                    actor.next_operation([], prompt="task")
                self.assertEqual(stopped.exception.category, category)
                self.assertEqual(stopped.exception.status, status)
                self.assertIn("synthetic refusal", str(stopped.exception))
                self.assertNotIn("fixture-secret", str(stopped.exception))

    def test_credential_is_redacted_and_echoes_are_refused(self):
        leaked = content_response("Summary mentions fixture-secret inline.")
        actor = self.actor(lambda request: httpx.Response(200, json=leaked))
        operation = actor.next_operation([], prompt="task")
        self.assertNotIn("fixture-secret", operation.summary)
        self.assertNotIn("fixture-secret", json.dumps(actor.last_receipt))
        echoed_id = chat_response(call_id="fixture-secret")
        bad_id = self.actor(lambda request: httpx.Response(200, json=echoed_id))
        with self.assertRaises(BasetenFailure):
            bad_id.next_operation([], prompt="task")
        echoed_args = chat_response(name="ask_owner", arguments={"question": "fixture-secret?"})
        bad_args = self.actor(lambda request: httpx.Response(200, json=echoed_args))
        with self.assertRaises(BasetenFailure):
            bad_args.next_operation([], prompt="task")

    def test_unknown_tool_and_malformed_calls_are_refused(self):
        payloads = [
            chat_response(name="delete_everything"),
            {**chat_response(), "choices": []},
            content_response(["block", "content"]),
        ]
        double = chat_response()
        double["choices"][0]["message"]["tool_calls"].append(
            double["choices"][0]["message"]["tool_calls"][0]
        )
        payloads.append(double)
        ragged = chat_response()
        ragged["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = 42
        payloads.append(ragged)
        for payload in payloads:
            with self.subTest(payload=payload):

                def serve(request, payload=payload):
                    return httpx.Response(200, json=payload)

                with self.assertRaises(BasetenFailure):
                    self.actor(serve).next_operation([], prompt="task")

    def test_reasoning_fields_are_never_retained(self):
        payload = chat_response()
        payload["choices"][0]["message"]["reasoning_content"] = "hidden synthetic thought"
        payload["choices"][0]["message"]["reasoning"] = ["hidden synthetic thought"]
        payload["choices"][0]["message"]["reasoning_details"] = [{"text": "hidden"}]
        actor = self.actor(lambda request: httpx.Response(200, json=payload))
        operation = actor.next_operation([], prompt="task")
        self.assertEqual(operation.name, "read_file")
        dumped = json.dumps(
            {"operation": [operation.name, operation.arguments], "receipt": actor.last_receipt}
        )
        self.assertNotIn("hidden synthetic thought", dumped)
        self.assertNotIn("reasoning_content", dumped)

    def test_malformed_usage_is_refused(self):
        for usage in (
            {"prompt_tokens": "10", "completion_tokens": 20, "total_tokens": 30},
            {"prompt_tokens": 10, "completion_tokens": -1, "total_tokens": 30},
            {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
                "completion_tokens_details": {"reasoning_tokens": 21},
            },
        ):
            with self.subTest(usage=usage):
                payload = chat_response(usage=usage)

                def serve(request, payload=payload):
                    return httpx.Response(200, json=payload)

                with self.assertRaises(BasetenFailure):
                    self.actor(serve).next_operation([], prompt="task")


class BasetenActivationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        seed = self.root / "seed.py"
        seed.write_text("# seed\n", encoding="utf-8")
        self.operator = Operator(self.root / "runtime", seed, None)

    def tearDown(self):
        self.directory.cleanup()

    def grant_path(self):
        grant = BasetenGrant(
            "fixture-only",
            MODEL,
            time.time() + 60,
            1,
            20000,
            1024,
            "0.10",
            "1",
            "1",
            True,
            True,
        )
        path = self.root / "baseten-grant.json"
        path.write_text(json.dumps(asdict(grant)), encoding="utf-8")
        return path

    def test_key_without_mode_and_grant_keeps_scripted_default(self):
        from remote_operator.cli import model_factory
        from remote_operator.scripted import ScriptedModel

        args = argparse.Namespace(model_mode="scripted", grant=None, allow_live_inference=False)
        with patch.dict("os.environ", {"BASETEN_HACKATHON_API_KEY": "fixture-key"}):
            self.assertIs(model_factory(args, self.operator), ScriptedModel)

    def test_scripted_mode_rejects_live_options(self):
        from remote_operator.cli import model_factory

        args = argparse.Namespace(
            model_mode="scripted", grant=self.root / "grant.json", allow_live_inference=True
        )
        with self.assertRaisesRegex(ValueError, "baseten"):
            model_factory(args, self.operator)

    def test_missing_flag_or_grant_does_not_activate(self):
        from remote_operator.cli import model_factory

        for allowed, grant in ((False, None), (True, None), (False, self.root / "g.json")):
            args = argparse.Namespace(
                model_mode="baseten", grant=grant, allow_live_inference=allowed
            )
            with self.subTest(allowed=allowed, grant=grant), self.assertRaises(ValueError):
                model_factory(args, self.operator)

    def test_unknown_mode_is_refused(self):
        from remote_operator.cli import model_factory

        args = argparse.Namespace(model_mode="openrouter", grant=None, allow_live_inference=False)
        with self.assertRaisesRegex(ValueError, "Unknown model mode"):
            model_factory(args, self.operator)

    def test_explicit_grant_builds_actor_but_does_not_make_a_request(self):
        from remote_operator.cli import model_factory

        args = argparse.Namespace(
            model_mode="baseten", grant=self.grant_path(), allow_live_inference=True
        )
        with (
            patch.dict("os.environ", {"BASETEN_HACKATHON_API_KEY": "fixture-key"}),
            patch("httpx.Client") as http,
        ):
            actor = model_factory(args, self.operator)()
            self.assertIsInstance(actor, BasetenModel)
            self.assertEqual(actor.grant.model, MODEL)
            http.assert_not_called()

    def test_missing_event_key_is_refused_without_personal_fallback(self):
        from remote_operator.cli import model_factory

        args = argparse.Namespace(
            model_mode="baseten", grant=self.grant_path(), allow_live_inference=True
        )
        with patch.dict(
            "os.environ",
            {"BASETEN_API_KEY": "synthetic-personal-credential-only"},
            clear=False,
        ):
            import os

            os.environ.pop("BASETEN_HACKATHON_API_KEY", None)
            with self.assertRaisesRegex(ValueError, "BASETEN_HACKATHON_API_KEY"):
                model_factory(args, self.operator)

    def test_budget_supplier_is_bound_to_the_same_frozen_grant_as_execution(self):
        from remote_operator.cli import configure_models

        path = self.grant_path()
        args = argparse.Namespace(model_mode="baseten", grant=path, allow_live_inference=True)
        with (
            patch.dict("os.environ", {"BASETEN_HACKATHON_API_KEY": "fixture-key"}),
            patch("httpx.Client") as http,
        ):
            factory, supplier = configure_models(args, self.operator)
            path.write_text("not JSON anymore", encoding="utf-8")
            status = supplier()
            actor = factory()
        self.assertEqual(actor.grant.model, MODEL)
        self.assertEqual(status["provider"], "baseten")
        self.assertEqual(status["limit_usd"], "0.10")
        http.assert_not_called()

    def test_grant_file_bounds_and_legacy_cap_are_refused(self):
        from remote_operator.cli import model_factory

        oversized = self.root / "big-grant.json"
        oversized.write_text('{"grant_id": "' + "x" * 9000 + '"}', encoding="utf-8")
        args = argparse.Namespace(model_mode="baseten", grant=oversized, allow_live_inference=True)
        with (
            patch.dict("os.environ", {"BASETEN_HACKATHON_API_KEY": "fixture-key"}),
            self.assertRaisesRegex(ValueError, "8192"),
        ):
            model_factory(args, self.operator)
        legacy = self.root / "legacy-grant.json"
        payload = asdict(
            BasetenGrant(
                "fixture-only",
                MODEL,
                time.time() + 60,
                1,
                20000,
                1024,
                "0.10",
                "1",
                "1",
                True,
                True,
            )
        )
        payload["provider_spend_cap_verified"] = True
        legacy.write_text(json.dumps(payload), encoding="utf-8")
        args = argparse.Namespace(model_mode="baseten", grant=legacy, allow_live_inference=True)
        with (
            patch.dict("os.environ", {"BASETEN_HACKATHON_API_KEY": "fixture-key"}),
            self.assertRaisesRegex(ValueError, "Legacy provider-cap"),
        ):
            model_factory(args, self.operator)

    def test_run_records_model_and_endpoint_receipts(self):
        from remote_operator.control import Validation

        class Validator:
            def validate(self, workspace, candidate_digest, *, cancelled=None):
                return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")

        seed = self.root / "seed.py"
        operator = Operator(self.root / "receipt-runtime", seed, Validator())
        parked = chat_response(name="ask_owner", arguments={"question": "Which header?"})
        actor = BasetenModel(
            "fixture-secret",
            BasetenGrant(
                "receipt-fixture",
                MODEL,
                time.time() + 60,
                5,
                100000,
                1024,
                "1",
                "1",
                "1",
                True,
                True,
            ),
            operator.store,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=parked)),
        )
        task = operator.submit("Implement CSV import per docs/sample-task.md")
        result = operator.run(task["id"], actor)
        self.assertEqual(result["status"], "waiting_on_owner")
        receipts = [
            event["payload"]
            for event in operator.store.events(task["id"])
            if event["kind"] == "inference_receipt"
        ]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["provider"], "baseten")
        self.assertEqual(receipts[0]["model"], MODEL)
        self.assertEqual(receipts[0]["endpoint"], ENDPOINT)
        self.assertEqual(receipts[0]["grant_id"], "receipt-fixture")
        self.assertIsNotNone(receipts[0]["usage"])


class BasetenWebConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_baseten_mode_is_reported_live(self):
        from remote_operator.control import Validation
        from remote_operator.web import create_app

        class Validator:
            def validate(self, workspace, candidate_digest, *, cancelled=None):
                return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")

        directory = tempfile.TemporaryDirectory()
        try:
            root = Path(directory.name)
            seed = root / "seed.py"
            seed.write_text("# seed\n", encoding="utf-8")
            operator = Operator(root / "runtime", seed, Validator())
            token = "owner-test-credential-" + "x" * 32
            app = create_app(operator, token, mode="baseten")
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://localhost"
            )
            try:
                config = await client.get(
                    "/api/config", headers={"Authorization": "Bearer " + token}
                )
            finally:
                await client.aclose()
            self.assertEqual(
                config.json(),
                {"mode": "baseten", "live_inference": True, "https_origin": None},
            )
        finally:
            directory.cleanup()
