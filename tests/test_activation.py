"""A key alone cannot activate a paid route or change server-owned model choice."""

import argparse
import io
import json
import logging
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch

from remote_operator.cli import main, model_factory
from remote_operator.control import Operator
from remote_operator.meta import InferenceGrant, MetaModel
from remote_operator.scripted import ScriptedModel


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        seed = self.root / "seed.py"
        seed.write_text("# seed\n", encoding="utf-8")
        self.operator = Operator(self.root / "runtime", seed, None)

    def tearDown(self):
        self.directory.cleanup()

    def test_key_without_mode_and_grant_keeps_scripted_default(self):
        args = argparse.Namespace(model_mode="scripted", grant=None, allow_live_inference=False)
        with patch.dict("os.environ", {"META_API_KEY": "fixture-key"}):
            self.assertIs(model_factory(args, self.operator), ScriptedModel)

    def test_missing_flag_or_grant_does_not_activate(self):
        for allowed, grant in ((False, None), (True, None), (False, self.root / "grant.json")):
            args = argparse.Namespace(model_mode="meta", grant=grant, allow_live_inference=allowed)
            with self.subTest(allowed=allowed, grant=grant), self.assertRaises(ValueError):
                model_factory(args, self.operator)

    def test_explicit_grant_builds_actor_but_does_not_make_a_request(self):
        grant = InferenceGrant(
            "fixture-only",
            "muse-spark-fixture",
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
        path = self.root / "grant.json"
        path.write_text(json.dumps(asdict(grant)), encoding="utf-8")
        args = argparse.Namespace(model_mode="meta", grant=path, allow_live_inference=True)
        with (
            patch.dict("os.environ", {"META_API_KEY": "fixture-key"}),
            patch("httpx.Client") as http,
        ):
            actor = model_factory(args, self.operator)()
            self.assertIsInstance(actor, MetaModel)
            http.assert_not_called()

    def test_paused_demo_exits_nonzero_but_retains_review_json(self):
        operator = Mock()
        operator.submit.return_value = {"id": "fixture"}
        operator.review.return_value = {"task": {"id": "fixture", "status": "paused"}}
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch("sys.argv", ["remote-ai-operator", "demo"]),
            patch("remote_operator.cli.build_operator", return_value=operator),
            patch("remote_operator.cli.configure_models", return_value=(lambda: object(), None)),
            patch("sys.stdout", output),
            patch("sys.stderr", errors),
            self.assertRaises(SystemExit) as stopped,
        ):
            main()
        self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(json.loads(output.getvalue())["task"]["status"], "paused")
        self.assertIn("did not reach verified review", errors.getvalue())

    def test_budget_supplier_is_bound_to_the_same_frozen_grant_as_execution(self):
        from remote_operator.cli import configure_models

        grant = InferenceGrant(
            "budget-fixture",
            "muse-spark-fixture",
            time.time() + 60,
            3,
            20000,
            1024,
            "1",
            "1",
            "1",
            True,
            True,
        )
        path = self.root / "budget-grant.json"
        path.write_text(json.dumps(asdict(grant)), encoding="utf-8")
        args = argparse.Namespace(model_mode="meta", grant=path, allow_live_inference=True)
        with (
            patch.dict("os.environ", {"META_API_KEY": "fixture-key"}),
            patch("httpx.Client") as http,
        ):
            factory, supplier = configure_models(args, self.operator)
            path.write_text("not JSON anymore", encoding="utf-8")
            status = supplier()
            actor = factory()
        self.assertEqual(actor.grant, grant)
        self.assertEqual(status["limit_usd"], "1")
        http.assert_not_called()

    def test_missing_key_error_names_slot_without_echoing_value(self):
        from remote_operator.credentials import SessionKeys

        raw_key = "sk-test-fake-key-1234567890abcdef"
        subject = "test-user"
        store = SessionKeys(lifetime=60, capacity=4, clock=time.monotonic)
        # Create a session without a key to trigger empty slot path
        token = store.open(subject)
        # Also verify dropped-key path does not leak value
        token_with_key = store.open(subject, raw_key)
        store.drop_key(token_with_key, subject=subject)
        for tok in (token, token_with_key):
            with self.subTest(token=tok):
                with self.assertRaises(Exception) as ctx:
                    store.key_for(tok, subject=subject)
                msg = str(ctx.exception)
                # Must name the slot / subject identifier
                self.assertIn(subject, msg)
                self.assertIn("subject", msg.lower())
                # Must not echo credential value
                self.assertNotIn(raw_key, msg)

    def test_missing_credential_emits_debug_log_without_leaking_key(self):
        from remote_operator.credentials import SessionKeys

        raw_key = "sk-test-fake-key-1234567890abcdef"
        subject = "test-user"
        store = SessionKeys(lifetime=60, capacity=4, clock=time.monotonic)
        token = store.open(subject)
        with self.assertLogs("remote_operator.credentials", level=logging.DEBUG) as captured:
            with self.assertRaises(Exception):
                store.key_for(token, subject=subject)
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured.records[0].levelname, "DEBUG")
        log_msg = captured.records[0].getMessage()
        self.assertNotIn(raw_key, log_msg)
