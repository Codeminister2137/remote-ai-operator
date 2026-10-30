"""Trusted Linux supervisor for isolated code sandbox execution.

Runs only inside an explicitly isolated, locked-down Linux sandbox
(container/VM with no-new-privileges, read-only mounts, dropped
capabilities). Validates a JSON request from stdin describing allowed
files/entry/timeout, materializes the sandbox filesystem, and executes the
requested entry as an unprivileged, resource-limited child process with
strict CPU/memory/output/timeout limits. Not for use on a host system.
"""

import json
import math
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CHILD_SETUP = r"""
import ctypes, math, os, resource, sys
seconds = math.ceil(float(sys.argv[2]))
resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))
for kind, bound in ((resource.RLIMIT_AS, 134217728), (resource.RLIMIT_FSIZE, 65536),
                    (resource.RLIMIT_NOFILE, 64), (resource.RLIMIT_NPROC, 64),
                    (resource.RLIMIT_CORE, 0)):
    resource.setrlimit(kind, (bound, bound))
if ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0) != 0:
    raise OSError("Cannot set no-new-privileges")
os.execv(sys.executable, [sys.executable, "-I", "-B", "/checks/" + sys.argv[1]])
"""


def main():
    request = json.loads(sys.stdin.buffer.read(524_289))
    files = request["files"]
    expected = {
        "/workspace/issue_import.py",
        "/checks/run_checks.py",
        "/checks/test_import.py",
        "/checks/preview.py",
    }
    if set(files) != expected or request["entry"] not in {"run_checks.py", "preview.py"}:
        raise ValueError("Unregistered sandbox input")
    timeout = request["timeout"]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("Invalid sandbox deadline")
    for name, source in files.items():
        if not isinstance(source, str) or len(source.encode()) > 65_536:
            raise ValueError("Sandbox source allowance exceeded")
        path = Path(name)
        path.parent.mkdir(exist_ok=True)
        path.write_text(source, encoding="utf-8")
        path.chmod(0o444)
    for directory in ("/workspace", "/checks"):
        Path(directory).chmod(0o555)

    output = bytearray()
    failure = None
    with tempfile.TemporaryFile() as input_file:
        input_file.write(request["payload"].encode())
        input_file.seek(0)
        child = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", CHILD_SETUP, request["entry"], str(timeout)],
            cwd="/workspace",
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
            stdin=input_file,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            user=65534,
            group=65534,
            extra_groups=[],
        )
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        try:
            while selector.get_map():
                if time.monotonic() >= deadline:
                    failure = "Sandbox child deadline reached"
                    break
                for key, _ in selector.select(timeout=0.05):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    room = 65_536 - len(output)
                    output.extend(chunk[:room])
                    if len(chunk) > room:
                        failure = "Sandbox child output allowance reached"
                        break
                if failure:
                    break
        finally:
            selector.close()
            # Also remove descendants that outlived their primary child.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            code = child.wait(timeout=3)
            child.stdout.close()
    print(
        json.dumps(
            {"code": code, "output": output.decode("utf-8", errors="replace"), "failure": failure}
        ),
        flush=True,
    )


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Usage: sandbox_bootstrap.py < request.json")
        print("Trusted Linux supervisor - runs only inside an explicitly isolated code sandbox.")
        print("Reads JSON request from stdin with files/entry/timeout/payload; --help shows this message.")
        sys.exit(0)
    main()
