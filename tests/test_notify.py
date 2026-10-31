"""Owner notifications: dry-run default, fail-closed addressing, receipts."""

import tempfile
import unittest
from pathlib import Path

from remote_operator.notify import load_smtp_config, send_owner_mail


class NotifyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.receipts = Path(self.directory.name) / "receipts"
        self.config = {
            "owner": "owner@example.com",
            "host": "smtp.example.com",
            "port": 587,
            "username": "",
            "password": "",
            "starttls": True,
        }

    def tearDown(self):
        self.directory.cleanup()

    def test_dry_run_writes_receipt_without_sending(self):
        receipt = send_owner_mail(
            to="owner@example.com",
            subject="run finished",
            body="done",
            config=self.config,
            receipts_dir=self.receipts,
        )
        self.assertTrue(receipt["dry_run"])
        self.assertFalse(receipt["sent"])
        self.assertIsNone(receipt["error"])
        self.assertTrue((self.receipts / receipt["receipt"]).is_file())

    def test_off_owner_address_refused(self):
        with self.assertRaises(ValueError):
            send_owner_mail(
                to="stranger@example.com",
                subject="x",
                body="x",
                config=self.config,
                receipts_dir=self.receipts,
            )
        self.assertFalse(self.receipts.exists())

    def test_live_transport_failure_captured(self):
        config = {**self.config, "host": "127.0.0.1", "port": 1}
        receipt = send_owner_mail(
            to="owner@example.com",
            subject="x",
            body="x",
            config=config,
            dry_run=False,
            receipts_dir=self.receipts,
        )
        self.assertFalse(receipt["sent"])
        self.assertTrue(receipt["error"])

    def test_config_requires_owner_and_host(self):
        with self.assertRaises(ValueError):
            load_smtp_config({})
        parsed = load_smtp_config(
            {"OPERATOR_OWNER_EMAIL": " o@example.com ", "OPERATOR_SMTP_HOST": "h"}
        )
        self.assertEqual(parsed["owner"], "o@example.com")
        self.assertEqual(parsed["port"], 587)
        self.assertTrue(parsed["starttls"])
        with self.assertRaises(ValueError):
            load_smtp_config(
                {
                    "OPERATOR_OWNER_EMAIL": "o@example.com",
                    "OPERATOR_SMTP_HOST": "h",
                    "OPERATOR_SMTP_PORT": "nope",
                }
            )

    def test_subject_and_body_guarded(self):
        with self.assertRaises(ValueError):
            send_owner_mail(
                to="owner@example.com",
                subject="",
                body="x",
                config=self.config,
                receipts_dir=self.receipts,
            )
        with self.assertRaises(ValueError):
            send_owner_mail(
                to="owner@example.com",
                subject="x",
                body="x" * 50_001,
                config=self.config,
                receipts_dir=self.receipts,
            )


if __name__ == "__main__":
    unittest.main()
