"""Owner run settings persisted in the operator runtime directory.

``supervision`` selects attended (default, no mail) or unattended (mail the
owner on terminal run outcomes when SMTP is configured). Never stores
secrets; mail readiness is probed, never echoed.
"""

import json
from pathlib import Path

SETTINGS_FILENAME = "settings.json"
SUPERVISION_MODES = ("attended", "unattended")
DEFAULT_SETTINGS = {"supervision": "attended"}


def settings_path(root: Path) -> Path:
    """Return the settings file path without creating anything."""
    return Path(root) / SETTINGS_FILENAME


def load_settings(root: Path) -> dict:
    """Return persisted settings merged over defaults; corrupt file resets."""
    path = settings_path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)
    if not isinstance(raw, dict):
        return dict(DEFAULT_SETTINGS)
    supervision = raw.get("supervision", DEFAULT_SETTINGS["supervision"])
    if supervision not in SUPERVISION_MODES:
        return dict(DEFAULT_SETTINGS)
    return {"supervision": supervision}


def save_settings(root: Path, supervision: str) -> dict:
    """Persist the supervision mode atomically; returns the saved settings."""
    if supervision not in SUPERVISION_MODES:
        raise ValueError(f"supervision must be one of {SUPERVISION_MODES}")
    path = settings_path(root)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"supervision": supervision}), encoding="utf-8")
    tmp.replace(path)
    return {"supervision": supervision}


def mail_configured() -> bool:
    """Return True when owner-mail SMTP settings are present (no secrets)."""
    from remote_operator.notify import load_smtp_config

    try:
        load_smtp_config()
    except (ValueError, OSError):
        return False
    return True
