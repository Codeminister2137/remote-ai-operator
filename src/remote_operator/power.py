"""Owner-only workstation power control for the local serve process.

Schedules a delayed Windows sleep through a detached helper that aborts
when the cancel file appears. Hosted deployments must refuse these routes:
judges and visitors never sleep infrastructure. Wake stays enabled so
Wake-on-LAN keeps working after the transition.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

RECEIPT_FILENAME = "power-sleep.json"
CANCEL_FILENAME = "power-sleep.cancel"
MIN_DELAY_SECONDS = 10
MAX_DELAY_SECONDS = 3600
CANCEL_GRACE_SECONDS = 1.2

_HELPER = (
    "import ctypes, sys, time\n"
    "from pathlib import Path\n"
    "cancel = Path(sys.argv[1])\n"
    "due = float(sys.argv[2])\n"
    "while time.time() < due:\n"
    "    time.sleep(1)\n"
    "    if cancel.exists():\n"
    "        sys.exit(3)\n"
    "ctypes.windll.powrprof.SetSuspendState(False, False, False)\n"
)


def _paths(runtime_dir: Path) -> tuple[Path, Path]:
    directory = Path(runtime_dir)
    return directory / RECEIPT_FILENAME, directory / CANCEL_FILENAME


def schedule_sleep(runtime_dir: Path, delay_seconds: int = 60) -> dict:
    """Schedule a cancellable sleep ``delay_seconds`` from now.

    Cancels any previous pending schedule first; only one can exist.
    Returns the receipt dict (also persisted for status reads).
    """
    if (
        type(delay_seconds) is not int
        or not MIN_DELAY_SECONDS <= delay_seconds <= MAX_DELAY_SECONDS
    ):
        raise ValueError(
            f"delay_seconds must be an integer between {MIN_DELAY_SECONDS} and {MAX_DELAY_SECONDS}"
        )
    directory = Path(runtime_dir)
    directory.mkdir(parents=True, exist_ok=True)
    receipt_path, cancel_path = _paths(directory)
    if cancel_sleep(runtime_dir):
        # A previous helper may still be polling; it checks every second,
        # so a 1.2 s grace guarantees it observes the cancel file before
        # the new schedule removes it. Without the unlink, every schedule
        # after a cancel would instantly abort while reporting scheduled.
        time.sleep(CANCEL_GRACE_SECONDS)
    cancel_path.unlink(missing_ok=True)
    due = time.time() + delay_seconds
    creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", _HELPER, str(cancel_path), repr(due)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )
    receipt = {
        "status": "sleep_scheduled",
        "delay_seconds": delay_seconds,
        "due_epoch": due,
        "process_id": proc.pid,
        "cancel_path": CANCEL_FILENAME,
    }
    tmp = receipt_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(receipt), encoding="utf-8")
    tmp.replace(receipt_path)
    return receipt


def cancel_sleep(runtime_dir: Path) -> bool:
    """Cancel a pending scheduled sleep; True when one was pending."""
    _, cancel_path = _paths(Path(runtime_dir))
    if cancel_path.exists():
        return True
    receipt_path, _ = _paths(Path(runtime_dir))
    if not receipt_path.exists():
        return False
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        receipt = {}
    if receipt.get("status") != "sleep_scheduled":
        return False
    cancel_path.write_text("cancelled", encoding="utf-8")
    receipt["status"] = "sleep_cancelled"
    tmp = receipt_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(receipt), encoding="utf-8")
    tmp.replace(receipt_path)
    return True


def sleep_status(runtime_dir: Path) -> dict:
    """Return the last sleep schedule receipt, or idle when none exists."""
    receipt_path, _ = _paths(Path(runtime_dir))
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "idle"}
    if not isinstance(receipt, dict) or "status" not in receipt:
        return {"status": "idle"}
    return receipt
