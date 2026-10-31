"""Owner-only email notifications over stdlib SMTP; dry-run by default.

Configuration comes from the environment so no address or credential lives in
source or job files. Delivery is fail-closed: anything addressed anywhere but
the configured owner address is refused before any network use, and transport
failures are captured in the receipt instead of breaking the caller.
"""

import json
import os
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path

DEFAULT_SMTP_PORT = 587
SMTP_TIMEOUT_SECONDS = 15
MAX_SUBJECT_CHARS = 300
MAX_BODY_CHARS = 50_000


def load_smtp_config(env=None) -> dict:
    """Read SMTP settings from ``env`` (default: process environment)."""
    source = os.environ if env is None else env
    owner = (source.get("OPERATOR_OWNER_EMAIL") or "").strip()
    host = (source.get("OPERATOR_SMTP_HOST") or "").strip()
    if not owner or not host:
        raise ValueError("Set OPERATOR_OWNER_EMAIL and OPERATOR_SMTP_HOST to notify")
    try:
        port = int(source.get("OPERATOR_SMTP_PORT", str(DEFAULT_SMTP_PORT)))
    except ValueError:
        raise ValueError("OPERATOR_SMTP_PORT must be an integer") from None
    if not 1 <= port <= 65535:
        raise ValueError("OPERATOR_SMTP_PORT must be between 1 and 65535")
    return {
        "owner": owner,
        "host": host,
        "port": port,
        "username": (source.get("OPERATOR_SMTP_USER") or "").strip(),
        "password": source.get("OPERATOR_SMTP_PASS") or "",
        "starttls": (source.get("OPERATOR_SMTP_STARTTLS") or "1") != "0",
    }


def send_owner_mail(
    *,
    to: str,
    subject: str,
    body: str,
    config: dict,
    dry_run: bool = True,
    receipts_dir: Path,
) -> dict:
    """Send one owner notification; returns the persisted receipt."""
    if to != config["owner"]:
        raise ValueError("Refusing to send a notification off the owner address")
    if not isinstance(subject, str) or not subject.strip() or len(subject) > MAX_SUBJECT_CHARS:
        raise ValueError("Notification subject requires 1-300 characters")
    if not isinstance(body, str) or len(body) > MAX_BODY_CHARS:
        raise ValueError("Notification body must be text up to 50000 characters")
    receipt = {
        "to": to,
        "subject": subject,
        "dry_run": bool(dry_run),
        "sent": False,
        "error": None,
        "finished": time.time(),
    }
    if not dry_run:
        try:
            _deliver(config, to, subject, body)
        except (OSError, TimeoutError, ssl.SSLError, smtplib.SMTPException) as exc:
            receipt["error"] = f"{type(exc).__name__}: {exc}"[:500]
        else:
            receipt["sent"] = True
    receipts_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(receipt["finished"]))
    target = receipts_dir / f"notify-{stamp}-{secrets.token_hex(4)}.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(receipt), encoding="utf-8")
    tmp.replace(target)
    receipt["receipt"] = target.name
    return receipt


def _deliver(config: dict, to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = config["username"] or config["owner"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    if config["port"] == 465:
        smtp = smtplib.SMTP_SSL(
            config["host"],
            config["port"],
            timeout=SMTP_TIMEOUT_SECONDS,
            context=ssl.create_default_context(),
        )
    else:
        smtp = smtplib.SMTP(config["host"], config["port"], timeout=SMTP_TIMEOUT_SECONDS)
    with smtp:
        smtp.ehlo()
        if config["port"] != 465 and config["starttls"]:
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if config["username"]:
            smtp.login(config["username"], config["password"])
        smtp.send_message(message)
