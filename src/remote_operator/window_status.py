"""Agent window/activity status for the owner UI.

The agent maintains ``window-status.json`` at the operator root, rewriting it
at slice boundaries and on every heartbeat: which slice is active, its phase,
when it started, the last and next heartbeat, sleep state, and a short note.
The loader below only reads; it never raises. A missing file means no agent
activity (``{"running": False}``) and malformed content yields safe defaults.
"""

import json
from pathlib import Path

STATUS_FILENAME = "window-status.json"

FIELDS = (
    "slice",
    "phase",
    "started_utc",
    "last_heartbeat_utc",
    "next_heartbeat_utc",
    "sleep_state",
    "note",
)


def load_window_status(root) -> dict:
    """Return the agent window status stored under ``root`` (never raises)."""
    defaults = {"running": False, **dict.fromkeys(FIELDS, "")}
    try:
        raw = (Path(root) / STATUS_FILENAME).read_text(encoding="utf-8")
    except OSError:
        return {"running": False}
    try:
        data = json.loads(raw)
    except ValueError:
        return defaults
    if not isinstance(data, dict):
        return defaults
    status = {"running": True}
    for field in FIELDS:
        value = data.get(field, "")
        status[field] = value if isinstance(value, str) else ""
    return status
