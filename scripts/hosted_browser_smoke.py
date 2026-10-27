"""Optional owned browser check for hosted visitor sessions.

Starts the hosted application on localhost with a synthetic access code, drives a
headless browser through access refusal, entry, one scripted task and session
exit, and checks cross-session isolation and header protection from Node. No
model key, grant or inference is involved.
"""

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
output = root / ".runtime" / ("hosted-browser-" + uuid.uuid4().hex)
output.mkdir(parents=True)
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
access_code = secrets.token_urlsafe(24)
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
                "--hosted",
                "--port",
                str(port),
            ],
            env={**os.environ, "OPERATOR_VISITOR_CODE": access_code},
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
                            "Owned hosted server failed; inspect process log"
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
                    "origin": f"http://127.0.0.1:{port}",
                    "accessCode": access_code,
                    "output": str(output),
                }
            ),
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                [args.node, str(root / "scripts/hosted-browser-smoke.mjs"), str(config)],
                timeout=180,
                check=False,
            )
            if result.returncode:
                raise RuntimeError("Hosted browser smoke failed; inspect retained evidence")
        finally:
            config.unlink(missing_ok=True)
        print("Hosted browser evidence:", output)
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
