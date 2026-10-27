"""Optional owned browser smoke: uv run python scripts/browser_smoke.py --node PATH."""

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

from remote_operator.cli import build_operator
from remote_operator.control import SOURCE, write_source
from remote_operator.scripted import REFERENCE, ScriptedModel

parser = argparse.ArgumentParser()
parser.add_argument("--node", default=shutil.which("node"))
parser.add_argument(
    "--browser",
    type=Path,
    default=Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
    / "Microsoft/Edge/Application/msedge.exe",
)
args = parser.parse_args()
if not args.node or not args.browser.is_file():
    parser.error("Node 24+ and an installed Edge/Chromium executable are required")
root = Path.cwd()
output = root / ".runtime" / ("browser-" + uuid.uuid4().hex)
output.mkdir(parents=True)
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
token = secrets.token_urlsafe(32)
# Explicit synthetic crash fixtures for the owned scripted browser server only.

fixture_operator = build_operator(root, output / "tasks")
recovery_fixture = fixture_operator.submit("Browser fixture: interrupted matching edit")
write_source(fixture_operator.folder(recovery_fixture["id"], "candidate"), REFERENCE)
recovery_fixture["status"] = "reconciliation_required"
recovery_fixture["pending"] = {
    "name": "write_file",
    "arguments": {"path": SOURCE, "content": REFERENCE},
}
fixture_operator.store.save(
    recovery_fixture, "browser_fixture", {"role": "synthetic_interrupted_edit"}
)
mismatch_fixture = fixture_operator.submit("Browser fixture: uncertain mismatched edit")
mismatch_fixture["status"] = "reconciliation_required"
mismatch_fixture["pending"] = {
    "name": "write_file",
    "arguments": {"path": SOURCE, "content": REFERENCE},
}
fixture_operator.store.save(
    mismatch_fixture, "browser_fixture", {"role": "synthetic_mismatch_no_write"}
)
apply_fixture = fixture_operator.submit("Browser fixture: stopped after approved target write")
apply_fixture = fixture_operator.run(apply_fixture["id"], ScriptedModel())
write_source(fixture_operator.folder(apply_fixture["id"], "target"), REFERENCE)
apply_fixture["status"] = "applying"
fixture_operator.store.save(
    apply_fixture, "browser_fixture", {"role": "synthetic_apply_before_receipt"}
)
server = browser = None
with (output / "processes.log").open("w", encoding="utf-8") as log:
    try:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "remote_operator.cli",
                "--runtime",
                str(output / "tasks"),
                "serve",
                "--port",
                str(port),
            ],
            env={**os.environ, "OPERATOR_ACCESS_TOKEN": token},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 20
        with httpx.Client(timeout=2) as client:
            while True:
                try:
                    client.get(f"http://127.0.0.1:{port}/").raise_for_status()
                    break
                except httpx.HTTPError:
                    if server.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(
                            "Owned local server failed; inspect process log"
                        ) from None
                    time.sleep(0.1)
        profile = output / "profile"
        browser = subprocess.Popen(
            [
                str(args.browser),
                "--headless=new",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--remote-debugging-port=0",
                "--user-data-dir=" + str(profile),
                "about:blank",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 20
        while not (profile / "DevToolsActivePort").exists():
            if browser.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Owned headless browser failed; inspect process log")
            time.sleep(0.1)
        debug_port = int((profile / "DevToolsActivePort").read_text().splitlines()[0])
        config = output / "config.json"
        config.write_text(
            json.dumps(
                {
                    "debugPort": debug_port,
                    "url": f"http://127.0.0.1:{port}/",
                    "token": token,
                    "output": str(output),
                    "recoveryTask": recovery_fixture["id"],
                    "mismatchTask": mismatch_fixture["id"],
                    "applyingTask": apply_fixture["id"],
                }
            ),
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                [args.node, str(root / "scripts/browser-smoke.mjs"), str(config)],
                timeout=150,
                check=False,
            )
            if result.returncode:
                raise RuntimeError("Browser smoke failed; inspect retained evidence")
        finally:
            config.unlink(missing_ok=True)
        print("Browser evidence:", output)
    finally:
        for process in (browser, server):
            if process is not None:
                try:
                    process.wait(timeout=3 if process is browser else 0.1)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
        print("Owned browser/server processes closed")
