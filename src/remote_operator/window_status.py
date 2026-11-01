"""Agent window/activity status for the owner UI.

The agent maintains ``window-status.json`` at the operator root, rewriting it
at slice boundaries and on every heartbeat: which slice is active, its phase,
when it started, the last and next heartbeat, sleep state, a short note, and
the active agents/workers list. The loader below only reads; it never raises.
A missing file means no agent activity (``{"running": False}``) and malformed
content yields safe defaults.
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


def _clean_agents(value) -> list:
    """Keep only well-formed agent entries (name/task/status strings)."""
    if not isinstance(value, list):
        return []
    cleaned = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        task = entry.get("task", "")
        state = entry.get("status", "")
        if not isinstance(name, str) or not name:
            continue
        cleaned.append(
            {
                "name": name,
                "task": task if isinstance(task, str) else "",
                "status": state if isinstance(state, str) else "",
            }
        )
    return cleaned


def load_window_status(root) -> dict:
    """Return the agent window status stored under ``root`` (never raises)."""
    defaults = {"running": False, **dict.fromkeys(FIELDS, ""), "agents": []}
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
    status["agents"] = _clean_agents(data.get("agents", []))
    return status
