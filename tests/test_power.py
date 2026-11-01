"""Power scheduling survives cancel/reschedule cycles without helpers."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from remote_operator import power


class PowerRescheduleTests(unittest.TestCase):
    def test_schedule_after_cancel_clears_cancel_file(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with mock.patch.object(power.subprocess, "Popen", return_value=mock.Mock(pid=1)):
                first = power.schedule_sleep(root, 60)
            self.assertEqual(first["status"], "sleep_scheduled")
            self.assertTrue(power.cancel_sleep(root))
            self.assertTrue((root / power.CANCEL_FILENAME).exists())
            with (
                mock.patch.object(power.subprocess, "Popen", return_value=mock.Mock(pid=2)),
                mock.patch.object(power.time, "sleep") as nap,
            ):
                second = power.schedule_sleep(root, 120)
            # The old helper got its grace window; the new helper starts clean.
            nap.assert_called_once_with(power.CANCEL_GRACE_SECONDS)
            self.assertFalse((root / power.CANCEL_FILENAME).exists())
            self.assertEqual(second["status"], "sleep_scheduled")
            self.assertEqual(second["delay_seconds"], 120)
            self.assertEqual(second["process_id"], 2)

    def test_first_schedule_skips_grace(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with (
                mock.patch.object(power.subprocess, "Popen", return_value=mock.Mock(pid=1)),
                mock.patch.object(power.time, "sleep") as nap,
            ):
                power.schedule_sleep(root, 60)
            nap.assert_not_called()
            self.assertFalse((root / power.CANCEL_FILENAME).exists())

    def test_reschedule_replaces_pending(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with mock.patch.object(power.subprocess, "Popen", return_value=mock.Mock(pid=1)):
                power.schedule_sleep(root, 60)
            with (
                mock.patch.object(power.subprocess, "Popen", return_value=mock.Mock(pid=2)),
                mock.patch.object(power.time, "sleep"),
            ):
                second = power.schedule_sleep(root, 120)
            status = power.sleep_status(root)
            self.assertEqual(status["status"], "sleep_scheduled")
            self.assertEqual(status["process_id"], 2)
            self.assertEqual(second["delay_seconds"], 120)


if __name__ == "__main__":
    unittest.main()
