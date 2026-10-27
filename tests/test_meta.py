"""Meta contracts exercised with local HTTPX fixtures, never actual inference."""

import json
import tempfile
import time
import unittest
from dataclasses import asdict, replace
from pathlib import Path

import httpx

from remote_operator.control import Operator
from remote_operator.meta import InferenceGrant, MetaFailure, MetaModel
from remote_operator.store import Store


def response(name="read_file", arguments=None, call_id="call_1", **extra):
    return {
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "status": "completed",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(
                    arguments if arguments is not None else {"path": "issue_import.py"}
                ),
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        **extra,
    }


class MetaContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = Store(self.root / "tasks.sqlite3")
        self.grant = InferenceGrant(
            "synthetic-fixture-only",
            "muse-spark-fixture",
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

    def actor(self, handler=None, grant=None):
        def serve(request):
            self.requests.append(json.loads(request.content))
            return handler(request) if handler else httpx.Response(200, json=response())

        return MetaModel(
            "fixture-secret", grant or self.grant, self.store, transport=httpx.MockTransport(serve)
        )

    def test_explicit_reasoning_effort_is_sent_and_default_is_unchanged(self):
        def serve(request):
            payload = json.loads(request.content)
            self.assertEqual(payload["reasoning"], {"effort": "low"})
            return httpx.Response(200, json=response())

        actor = MetaModel(
            "fixture-secret",
            self.grant,
            self.store,
            transport=httpx.MockTransport(serve),
            reasoning_effort="low",
        )
        actor.next_operation([], prompt="task")
        self.assertEqual(actor.last_receipt["reasoning_effort"], "low")
        self.actor().next_operation([], prompt="task")
        self.assertNotIn("reasoning", self.requests[-1])
        for effort in ("none", "invalid"):
            with self.subTest(effort=effort), self.assertRaises(ValueError):
                MetaModel("fixture-secret", self.grant, self.store, reasoning_effort=effort)

    def test_missing_grant_is_denied_before_transport(self):
        actor = MetaModel(
            "fixture-secret",
            None,
            self.store,
            transport=httpx.MockTransport(lambda request: self.fail("Network attempted")),
        )
        with self.assertRaisesRegex(ValueError, "disabled"):
            actor.next_operation([], prompt="task")

    def test_encrypted_continuation_stays_transient_and_full_tool_pair_replays(self):
        def serve(request):
            body = response(call_id=f"call_{len(self.requests)}")
            body["output"].insert(
                0,
                {
                    "type": "reasoning",
                    "id": "rs_fixture",
                    "encrypted_content": "opaque-fixture",
                    "summary": [],
                },
            )
            return httpx.Response(200, json=body)

        actor = self.actor(serve)
        first = actor.next_operation([], prompt="original task")
        actor.next_operation(
            [
                {
                    "name": first.name,
                    "arguments": first.arguments,
                    "call_id": first.call_id,
                    "result": {"content": "# source"},
                }
            ],
            prompt="original task",
        )
        request = self.requests[-1]
        self.assertFalse(request["store"])
        self.assertEqual(request["include"], ["reasoning.encrypted_content"])
        self.assertFalse(request["parallel_tool_calls"])
        self.assertIn("max_output_tokens", request)
        self.assertEqual(request["input"][-1]["call_id"], first.call_id)
        self.assertIn("opaque-fixture", json.dumps(request["input"]))
        self.assertNotIn("opaque-fixture", json.dumps(actor.last_receipt))
        self.assertEqual(actor.last_receipt["usage"]["total_tokens"], 30)

    def test_cold_actor_replays_visible_history_with_honest_continuity(self):
        actor = self.actor()
        history = [
            {
                "name": "read_file",
                "arguments": {"path": "issue_import.py"},
                "call_id": "old_call",
                "result": {"content": "# retained"},
            }
        ]
        operation = actor.next_operation(history, prompt="original")
        self.assertEqual(actor.last_receipt["continuity"], "observable_history_only")
        history.append(
            {
                "name": operation.name,
                "arguments": operation.arguments,
                "call_id": operation.call_id,
                "result": {"content": "# again"},
            }
        )
        actor.next_operation(history, prompt="original")
        self.assertEqual(self.requests[-1]["input"][1]["call_id"], "old_call")

    def test_failed_http_request_consumes_allowance_across_actor_restart(self):
        grant = replace(self.grant, max_requests=1)
        actor = self.actor(
            lambda request: httpx.Response(
                429, json={"error": {"message": "Fixture quota cause fixture-secret"}}
            ),
            grant,
        )
        with self.assertRaises(MetaFailure) as failure:
            actor.next_operation([], prompt="task")
        self.assertEqual(failure.exception.category, "rate_limit")
        self.assertNotIn("fixture-secret", str(failure.exception))
        with self.assertRaisesRegex(ValueError, "exhausted"):
            self.actor(grant=grant).next_operation([], prompt="task")
        self.assertEqual(len(self.requests), 1)

    def test_authentication_and_redirects_do_not_retry_or_change_destination(self):
        for code in (401, 403, 302):
            with self.subTest(code=code):
                grant = replace(self.grant, grant_id=f"fixture-{code}")
                actor = self.actor(
                    lambda request, code=code: httpx.Response(
                        code,
                        headers={"Location": "https://other.invalid"},
                        json={"error": {"message": "refused"}},
                    ),
                    grant,
                )
                with self.assertRaises(MetaFailure) as failure:
                    actor.next_operation([], prompt="task")
                self.assertEqual(failure.exception.status, code)
        self.assertEqual(len(self.requests), 3)

    def test_expiry_training_mode_input_and_money_limits_refuse_before_request(self):
        grants = [
            replace(self.grant, expires_at=time.time() - 1),
            replace(self.grant, model="muse-spark-contributor"),
            replace(self.grant, max_input_bytes=10),
            replace(self.grant, approved_usd="0.00000001"),
            replace(self.grant, credits_verified=False),
            replace(self.grant, credits_verified="false"),
            replace(self.grant, local_spending_limit_accepted="true"),
            replace(self.grant, local_spending_limit_accepted=False),
        ]
        for grant in grants:
            with self.subTest(grant=grant), self.assertRaises(ValueError):
                self.actor(grant=grant).next_operation([], prompt="task")
        self.assertEqual(self.requests, [])

    def test_obsolete_provider_cap_grant_does_not_silently_activate(self):
        legacy = asdict(self.grant)
        legacy.pop("local_spending_limit_accepted")
        legacy["provider_spend_cap_verified"] = True
        with self.assertRaises(TypeError):
            InferenceGrant(**legacy)
        self.assertEqual(self.requests, [])

    def test_money_limit_persists_in_new_store_after_failed_request(self):
        grant = replace(self.grant, approved_usd="0.006")
        actor = self.actor(
            lambda request: httpx.Response(503, json={"error": {"message": "fixture"}}), grant
        )
        with self.assertRaises(MetaFailure):
            actor.next_operation([], prompt="task")
        allowance = actor.last_receipt["local_allowance"]
        self.assertEqual(allowance["requests_used"], 1)
        self.assertEqual(allowance["limit_usd"], "0.006")
        self.assertEqual(actor.last_receipt["spending_control"], "local_estimate_not_provider_cap")
        restarted = MetaModel(
            "fixture-secret",
            grant,
            Store(self.root / "tasks.sqlite3"),
            transport=httpx.MockTransport(lambda request: self.fail("Over-budget network request")),
        )
        with self.assertRaisesRegex(ValueError, "exhausted"):
            restarted.next_operation([], prompt="task")
        self.assertIsNone(restarted.last_receipt)
        self.assertEqual(len(self.requests), 1)

    def test_parallel_reservations_cannot_spend_the_same_remaining_budget(self):
        from concurrent.futures import ThreadPoolExecutor

        def reserve(_):
            store = Store(self.root / "tasks.sqlite3")
            try:
                return store.reserve_allowance("parallel", "same-policy", 12, "0.60", "1.00")
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(reserve, range(2)))
        accepted = [outcome for outcome in outcomes if outcome is not None]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["remaining_usd_estimate"], "0.40")

    def test_consumed_grant_cannot_be_reset_by_changing_settings(self):
        self.actor().next_operation([], prompt="task")
        with self.assertRaisesRegex(ValueError, "cannot be changed"):
            self.actor(grant=replace(self.grant, max_requests=6)).next_operation([], prompt="task")
        self.assertEqual(len(self.requests), 1)

    def test_malformed_incomplete_and_parallel_outputs_are_not_executed(self):
        cases = [
            response(output=[]),
            response(status="incomplete", incomplete_details={"reason": "max_output_tokens"}),
            response(output=[response()["output"][0]] * 2),
            response(
                output=[
                    {"type": "function_call", "call_id": "x", "name": "shell", "arguments": "{}"}
                ]
            ),
        ]
        for index, body in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(MetaFailure):
                self.actor(
                    lambda request, body=body: httpx.Response(200, json=body),
                    replace(self.grant, grant_id=f"malformed-{index}"),
                ).next_operation([], prompt="task")

    def test_coding_request_has_sixty_second_read_budget(self):
        def inspect(request):
            self.assertEqual(request.extensions["timeout"]["read"], 60)
            return httpx.Response(200, json=response())

        self.assertEqual(self.actor(inspect).next_operation([], prompt="task").name, "read_file")

    def test_timeout_preserves_category_without_credential_or_request_dump(self):
        def timeout(request):
            raise httpx.ReadTimeout("fixture-secret raw private request", request=request)

        with self.assertRaises(MetaFailure) as failure:
            self.actor(timeout).next_operation([], prompt="task")
        self.assertEqual(failure.exception.category, "timeout")
        self.assertNotIn("fixture-secret", str(failure.exception))

    def test_controller_records_usage_without_persisting_encrypted_reasoning(self):
        from remote_operator.control import Validation
        from remote_operator.scripted import REFERENCE

        operations = [
            ("read_file", {"path": "issue_import.py"}),
            ("write_file", {"path": "issue_import.py", "content": REFERENCE}),
            ("validate", {}),
            ("finish", {}),
        ]

        def serve(request):
            index = len(self.requests) - 1
            name, arguments = operations[index]
            body = response(name, arguments, call_id=f"call_{index}")
            body["output"].insert(
                0, {"type": "reasoning", "encrypted_content": "private-ciphertext", "summary": []}
            )
            return httpx.Response(200, json=body)

        class Validator:
            def validate(self, workspace, candidate_digest, *, cancelled=None):
                return Validation(True, candidate_digest, 0, "fixture", "test-double")

        seed = self.root / "seed.py"
        seed.write_text("# seed\n", encoding="utf-8")
        operator = Operator(self.root / "runtime", seed, Validator())
        task = operator.submit("Implement the sample")
        result = operator.run(task["id"], self.actor(serve))
        self.assertEqual(result["status"], "awaiting_approval")
        records = json.dumps(operator.store.events(task["id"])) + json.dumps(result)
        self.assertNotIn("private-ciphertext", records)
        self.assertNotIn("fixture-secret", records)
        receipts = [
            item
            for item in operator.store.events(task["id"])
            if item["kind"] == "inference_receipt"
        ]
        self.assertEqual(len(receipts), 4)
        self.assertEqual(receipts[-1]["payload"]["usage"]["total_tokens"], 30)

    def test_incomplete_output_retains_numeric_reasoning_and_cache_details_only(self):
        body = response(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        body["usage"]["output_tokens_details"] = {"reasoning_tokens": 19, "private": "never-save"}
        body["usage"]["input_tokens_details"] = {"cached_tokens": 5}
        actor = self.actor(lambda request, body=body: httpx.Response(200, json=body))
        with self.assertRaises(MetaFailure) as failure:
            actor.next_operation([], prompt="task")
        self.assertEqual(failure.exception.category, "incomplete")
        self.assertEqual(actor.last_receipt["response_status"], "incomplete")
        self.assertEqual(actor.last_receipt["reasoning_effort"], "provider_default")
        self.assertEqual(
            actor.last_receipt["usage"]["output_tokens_details"], {"reasoning_tokens": 19}
        )
        self.assertEqual(actor.last_receipt["usage"]["input_tokens_details"], {"cached_tokens": 5})
        self.assertNotIn("never-save", json.dumps(actor.last_receipt))

    def test_malformed_usage_breakdown_is_not_trusted(self):
        for index, value in enumerate((-1, 21, "19", True)):
            body = response()
            body["usage"]["output_tokens_details"] = {"reasoning_tokens": value}
            actor = self.actor(
                lambda request, body=body: httpx.Response(200, json=body),
                replace(self.grant, grant_id=f"usage-detail-{index}"),
            )
            with self.subTest(value=value), self.assertRaises(MetaFailure):
                actor.next_operation([], prompt="task")

    def test_partial_usage_is_preserved_and_unexpected_storage_is_refused(self):
        actor = self.actor(
            lambda request: httpx.Response(
                200,
                json=response(
                    status="incomplete", incomplete_details={"reason": "max_output_tokens"}
                ),
            )
        )
        with self.assertRaises(MetaFailure) as failure:
            actor.next_operation([], prompt="task")
        self.assertEqual(failure.exception.category, "incomplete")
        self.assertEqual(actor.last_receipt["usage"]["total_tokens"], 30)
        actor = self.actor(lambda request: httpx.Response(200, json=response(store=True)))
        with self.assertRaises(MetaFailure) as failure:
            actor.next_operation([], prompt="task")
        self.assertEqual(failure.exception.category, "privacy")

    def test_budget_snapshot_reads_without_creating_allowance_or_network(self):
        from unittest.mock import patch

        with patch("httpx.Client", side_effect=AssertionError("Metadata must not make requests")):
            status = self.grant.budget_status(self.store)
        self.assertTrue(status["enabled"])
        self.assertEqual(status["scope"], "configured_server_allowance")
        self.assertEqual(status["remaining_usd_estimate"], "1")
        self.assertEqual(status["requests_used"], 0)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM allowances").fetchone()[0], 0)
        self.assertNotIn("fixture-secret", json.dumps(status))

    def test_budget_snapshot_keeps_failed_reservations_and_reports_expiry(self):
        from decimal import Decimal
        from unittest.mock import patch

        actor = self.actor(
            lambda request: httpx.Response(503, json={"error": {"message": "fixture"}})
        )
        with self.assertRaises(MetaFailure):
            actor.next_operation([], prompt="task")
        first = self.grant.budget_status(Store(self.root / "tasks.sqlite3"))
        second = self.grant.budget_status(self.store)
        self.assertEqual(first, second)
        self.assertEqual(first["requests_used"], 1)
        self.assertEqual(
            Decimal(first["remaining_usd_estimate"]),
            Decimal(1) - Decimal(actor.last_receipt["reservation_usd_estimate"]),
        )
        with patch("remote_operator.meta.time.time", return_value=self.grant.expires_at + 1):
            expired = self.grant.budget_status(self.store)
        self.assertTrue(expired["expired"])
        self.assertEqual(expired["reserved_usd_estimate"], first["reserved_usd_estimate"])
        with self.assertRaisesRegex(ValueError, "cannot be changed"):
            replace(self.grant, approved_usd="10").budget_status(self.store)
        self.assertEqual(len(self.requests), 1)

    def test_budget_snapshot_refuses_invalid_ledger_amounts(self):
        for index, value in enumerate(("-1", "NaN", "Infinity")):
            grant_id = f"invalid-budget-{index}"
            self.store.reserve_allowance(grant_id, "policy", 3, "0.1", "1")
            with self.store.connect() as db:
                db.execute("UPDATE allowances SET reserved_usd=? WHERE id=?", (value, grant_id))
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.allowance_status(grant_id, "policy", 3, "1")
