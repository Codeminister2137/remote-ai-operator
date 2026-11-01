"""Run notification mails read as prose first, JSON details last."""

import unittest

from remote_operator.supervisor_runner import format_run_mail


class RunMailTests(unittest.TestCase):
    def test_awaiting_approval_names_action(self):
        body = format_run_mail(
            {"run_id": "68ad1fe2" + "0" * 24, "status": "awaiting_approval", "error": None},
            "Fix login bug",
        )
        self.assertIn("Run 68ad1fe2 (Fix login bug): awaiting_approval.", body)
        self.assertIn("Review & approve", body)
        self.assertIn("Details:", body)
        self.assertIn('"status": "awaiting_approval"', body)

    def test_waiting_on_owner_names_action(self):
        body = format_run_mail({"run_id": "ab12", "status": "waiting_on_owner", "error": None}, "")
        self.assertIn("Run ab12: waiting_on_owner.", body)
        self.assertIn("answer in Review & approve", body)

    def test_terminal_states_need_no_action(self):
        for status in ("completed", "cancelled"):
            body = format_run_mail({"run_id": "ab12", "status": status, "error": None}, "T")
            self.assertIn("No action needed.", body)

    def test_failed_carries_error(self):
        body = format_run_mail({"run_id": "ab12", "status": "failed", "error": "boom"}, "T")
        self.assertIn("Error: boom", body)

    def test_unknown_status_falls_back(self):
        body = format_run_mail({"run_id": "ab12", "status": "weird", "error": None}, "T")
        self.assertIn("Run ab12 (T): weird.", body)
        self.assertIn("No action needed.", body)


if __name__ == "__main__":
    unittest.main()
