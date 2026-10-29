"""Public one-use action resolution regressions; standard-library only."""

import concurrent.futures
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from remote_operator.external_actions import ExternalActionBridge
from remote_operator.store import Store


class OneUseResolution(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.bridge = ExternalActionBridge(Store(Path(self.folder.name) / "state.sqlite3"))
        value = hashlib.sha256(b"public synthetic action").hexdigest()
        self.action = self.bridge.submit("Synthetic action", value, value, value)

    def test_competing_resolutions_have_exactly_one_winner(self):
        for scenario in ("approve", "mixed", "cancel"):
            with self.subTest(scenario=scenario):
                value = hashlib.sha256(scenario.encode()).hexdigest()
                action = self.bridge.submit("Concurrent synthetic action", value, value, value)
                start = threading.Barrier(8, timeout=5)

                def resolve(number, *, start=start, scenario=scenario, action=action):
                    start.wait()
                    method = (
                        scenario
                        if scenario != "mixed"
                        else ("approve", "reject", "cancel")[number % 3]
                    )
                    try:
                        result = (
                            self.bridge.approve(action.id, action.nonce)
                            if method == "approve"
                            else getattr(self.bridge, method)(action.id)
                        )
                        return result.status
                    except ValueError:
                        return None

                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(resolve, range(8)))
                winners = [value for value in results if value is not None]
                self.assertEqual(
                    len(winners), 1, f"{scenario}: multiple successful transitions {winners}"
                )
                self.assertEqual(self.bridge.get(action.id).status, winners[0])

    def test_wrong_nonce_cannot_consume_the_action(self):
        with self.assertRaises(ValueError):
            self.bridge.approve(self.action.id, "incorrect")
        self.assertEqual(self.bridge.get(self.action.id).status, "submitted")
        self.assertEqual(self.bridge.approve(self.action.id, self.action.nonce).status, "approved")
        with self.assertRaises(ValueError):
            self.bridge.cancel(self.action.id)

    def test_approved_actions_stay_terminal(self):
        self.bridge.approve(self.action.id, self.action.nonce)
        later = self.action.expires + 1
        self.assertEqual(self.bridge.get(self.action.id, now=later).status, "approved")
        self.assertEqual(self.bridge.recent(now=later)[0].status, "approved")


class ExpiredResolution(unittest.TestCase):
    def test_expired_actions_cannot_be_rejected_or_cancelled(self):
        for method in ("reject", "cancel"):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as folder:
                bridge = ExternalActionBridge(Store(Path(folder) / "state.sqlite3"))
                digest = hashlib.sha256(b"synthetic expired action").hexdigest()
                action = bridge.submit(
                    "Expired synthetic",
                    digest,
                    digest,
                    digest,
                    expires_seconds=30,
                    now=time.time() - 120,
                )
                with self.assertRaises(ValueError):
                    getattr(bridge, method)(action.id)
                self.assertEqual(bridge.get(action.id).status, "expired")


class ExpiryDurability(unittest.TestCase):
    def test_expiry_commit_and_durable_callback_survive_operation_refusal(self):
        for operation in ("approve", "reject", "cancel"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as folder:
                commits = []
                store = Store(
                    Path(folder) / "state.sqlite3",
                    after_commit=lambda commits=commits: commits.append(True),
                )
                bridge = ExternalActionBridge(store)
                digest = hashlib.sha256(b"synthetic expiry durability").hexdigest()
                action = bridge.submit(
                    "Expired synthetic",
                    digest,
                    digest,
                    digest,
                    expires_seconds=30,
                    now=time.time() - 120,
                )
                before = len(commits)
                with self.assertRaises(ValueError):
                    if operation == "approve":
                        bridge.approve(action.id, action.nonce)
                    else:
                        getattr(bridge, operation)(action.id)
                # Do not call get(): that would itself expire a rolled-back row.
                with store.connect() as db:
                    state = json.loads(
                        db.execute(
                            "SELECT record FROM external_actions WHERE id=?", (action.id,)
                        ).fetchone()[0]
                    )
                self.assertEqual(state["status"], "expired")
                self.assertGreater(len(commits), before)
