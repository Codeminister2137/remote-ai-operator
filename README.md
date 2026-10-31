# Remote AI Operator

A standalone prototype for supervised coding with host-generated validation and
approval of an exact change. Started during the October 26–November 4, 2026 event.

**Default: clearly labelled scripted offline reference.** The prepared Meta Responses
adapter requires explicit live activation and verified finite credit admission;
a small live source-edit/test run is verified (see docs/live-acceptance.md). The application provides controlled
edits, SQLite receipts, isolated tests, cancellation, exact-change approval and a
working sample import-results screen. Scripted remote-phone operation is verified;
the complete real Meta HTTPS browser flow and owner-confirmed physical-phone Meta
submission/review/approval are verified. Broader repository tools and release work
remain unfinished. No private packages are required.

For a short independent walkthrough, see [Reviewer guide](docs/reviewer-guide.md).

## Prerequisites

- Python 3.14 for the default uv development environment (library minimum: 3.12).
- Docker with a running Linux-container engine (Docker Desktop on Windows).
- Run the commands below from this checkout. Docker must be available on PATH.

Install uv, then synchronize the independent environment from the project lockfile:

```text
uv sync --locked
docker pull python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
```

If uv is installed as a Python module, use `python -m uv` instead of `uv` throughout.
Network access is needed for dependencies/image preparation. Actual validation
uses `--pull=never` and no container network. No model credentials are needed.
The default runner image is pinned to the tested manifest digest above.

## Offline workflow

```text
uv run --locked remote-ai-operator demo --inject-failure
uv run --locked remote-ai-operator review TASK_ID
uv run --locked remote-ai-operator approve TASK_ID APPROVAL_NONCE
```

Review the printed diff, evidence and nonce before issuing the approval command.
The scripted actor installs a fixed reference candidate; the controller injects
one labelled failure after its completed edit. Recovery does not repeat that edit.
Docker executes the protected sample tests. Approval applies one reviewed source
file only to `.runtime/TASK_ID/target/issue_import.py`, leaving the seed unchanged.
The nonce expires after ten minutes. A changed candidate, target or receipt
invalidates approval. Protected-test and runner-image changes also require fresh validation. Do not share printed runtime records containing approvals.

`reject TASK_ID` refuses the pending change. `reconcile TASK_ID` inspects an
interrupted effect without replaying it. Restart does not automatically resume
work or restore approval authority. Unknown effects require manual inspection.
Use `cancel TASK_ID` to request stopping new effects. A started atomic application
cannot be revoked. In-flight model I/O stops at its bounded request boundary;
Docker cancellation terminates the owned container. `Run fresh validation` replaces
a stale or expired approval nonce without accepting an old review.

Supervisor planning threads (`/api/supervisor/...`, see
docs/remote-supervisor.md) turn chat into an approved run job; `supervisor-drain`
reconciles interrupted jobs and runs queued ones with the selected model actor,
stopping at the same human approval boundary. `--notify` writes owner-only
receipts; `--notify-live` sends them over stdlib SMTP.

## Local review interface

Set `OPERATOR_ACCESS_TOKEN` to a unique random value of at least 32 characters in
your shell, then run `uv run --locked remote-ai-operator serve`. Open `http://127.0.0.1:8765` and
enter that token, then select **Connect and load execution mode**. Review the mode before submitting. It remains in page memory; it is not stored in browser storage.
Keep this server bound to localhost. It is the local development instance; the
supported network deployment is the hosted visitor mode below. Do not expose this
server through forwarding or an unreviewed proxy.
Desktop and simulated mobile layouts have been exercised in a real browser; the owner has verified the scripted workflow on a physical phone over Wi-Fi and mobile data. See docs/phone-access.md for exact HTTPS-origin preparation.

After connecting, the **Tasks** chooser lists the latest 20 saved tasks. With no bookmark,
the newest task loads automatically. Selecting one only loads its recorded state;
it never starts inference or approves a change. A running task resumes progress
polling after reconnect. Manual task IDs remain an optional recovery control.

API: POST `/api/tasks`; authenticated GET `/api/tasks?limit=20` returns only bounded
id/prompt/status summaries (limit 1..50); GET `/api/tasks/{id}` and `/events?after=SEQUENCE`;
POST `/run`, `/approve`, `/reject`, `/reconcile`, `/cancel`, `/revalidate`, `/sample`. Every API request requires
`Authorization: Bearer TOKEN`. The server selects the actor; scripted mode is default. See docs/meta-tooling.md for guarded Meta activation. No endpoint
accepts a provider, billing source, shell command or arbitrary workspace path.

## Hosted visitor mode

`serve --hosted` starts a separate application for visitors instead of the owner
token. Set `OPERATOR_VISITOR_CODE` to 16-256 printable characters, then:

```text
uv run --locked remote-ai-operator serve --hosted
```

A visitor enters that code once; the server issues a signed session cookie and a
server-generated subject, so one visitor's task IDs are not readable or actionable
from another session. Set `OPERATOR_SESSION_SECRET` (at least 32 random characters) to
keep a subject stable across a process restart; without it the token is still
unforgeable but sessions end with the process. Hosted sessions start on the labelled
scripted reference at zero inference cost. A visitor Meta key can be attached for
that session only and detached at any time; it is never used as identity, never
persisted and never falls back to another key. Real Meta work additionally requires
an owner-configured grant; without one the key endpoint refuses. Judges instead
register with the access code, log in with their own password, and set their
own finite limits against their own key; the server pins the model via
`OPERATOR_JUDGE_MODEL` and the owner pays nothing (see docs/modal-hosting.md).

This mode is a localhost preview of the session flow. A hosted judge deployment is
live at `https://kubajaworski999--hosted.us-east.modal.run`; the access code is
supplied separately and never committed. It uses the single-writer volume described
in ADR-0005; `modal deploy --strategy recreate modal_app.py` is the only supported
deploy. Logout is process-local: a retained token stays valid until expiry after a
restart, so rotate the signing secret to invalidate all sessions. See
[docs/modal-hosting.md](docs/modal-hosting.md), ADR-0004 and ADR-0005.

## Tests

```text
uv run --locked python -m unittest discover -s tests -p "test_*.py" -v
# Opt in to real Docker execution:
# PowerShell: $env:OPERATOR_DOCKER_TESTS = "1"
# POSIX: export OPERATOR_DOCKER_TESTS=1
uv run --locked python -m unittest discover -s tests -p "test_*.py" -v
uv run --locked ruff check .
uv run --locked ruff format --check .
```

Unit checks use an explicitly labelled validation double where appropriate. The
Docker integration test independently proves red seed -> reference candidate ->
protected tests -> recovery -> exact apply -> actual applied-source tests. A
scripted actor or passing API test does not establish model coding quality.

## Exercise the result

After approving, use **Run approved import**. The applied Python function executes
inside Docker and returns accepted issues plus row errors to the sample table.
The target digest must still match the approved bytes. Generated code is never
imported into the web server. The sample has one existing issue with ID `existing`;
its import results are transient, with no extra domain database or external service.

## Optional real-browser acceptance

With Node 24+ and Edge/Chromium installed, run:

```text
uv run --locked python scripts/browser_smoke.py
```

Use `--node PATH` or `--browser PATH` when necessary. The check uses an isolated
browser profile and temporary local server, exercises the actual approval/results
flow, verifies desktop/mobile overflow and saves private screenshots under .runtime.
It closes its owned processes. This is simulated mobile width, not a phone/VPN test.

The hosted session flow has its own owned browser check, which starts the hosted
application with a synthetic access code and needs no key or grant:

```text
uv run --locked python scripts/hosted_browser_smoke.py
```

It verifies wrong-code refusal, scripted mode after entry, session reload, session
isolation and header protection between two synthetic visitors, no browser storage,
and session exit.

## Security and limits

One active controller is guarded across processes. Generated source runs as a
non-root user with a read-only filesystem and mounts, no network, dropped Linux
capabilities and finite CPU/memory/process/time/output limits. No model credentials
or Docker socket are mounted. This is a demo containment boundary, not a guarantee
against all malicious code or container-runtime vulnerabilities. Test outcomes
remain evidence to review, not a proof that arbitrary code is trustworthy.

The first slice applies one source file atomically with write-ahead state and
explicit crash reconciliation. It does not claim transactional multi-file changes.
No distributed queue, global process resurrection, desktop control or PC wake is implemented. Do not use the prototype on private repositories yet.

Runtime state is private and ignored. Deleting `.runtime` removes local task state;
keep it if you need receipts/reconciliation. This standalone repository is the public
submission tree; private handoffs, plans and credentials are not included. See BUILD_LOG.md, docs/architecture.md,
docs/decisions.md and docs/sample-task.md for scope and provenance.

## Spending and interrupted actions

The phone shows the configured server allowance, reserved cost estimate, request
count and expiry. **Refresh spending** is read-only. Reservations include failed
requests and are not a provider invoice or accountwide billing cap. Configure
limits in the private grant before starting the server; the phone cannot raise them.
Unavailable, expired or exhausted allowances block new inference in the UI while
preserving local review and validation. Server admission remains authoritative.
Authenticated GET `/api/allowance` reports metadata without starting a model call.

**Inspect interrupted action** checks saved effects after an interrupted operation.
It never resumes inference, replays an edit or approves a change. Active execution
is refused. Matching saved bytes can establish a paused or completed state;
mismatched bytes require manual inspection. Then review the state before choosing
the next action.

The pinned Docker image includes GNU `timeout`; each validation/preview command
also has an internal kill deadline if its host controller disappears. The host
watchdog and explicit cleanup remain. Custom runner images must provide GNU
`timeout`. Runner deadline changes invalidate pending validation approvals, requiring
fresh validation; completed records are preserved.

The Tasks section has an explicit **New task** control. It clears selected progress,
review evidence and sample results without deleting or cancelling saved work.
**Create and run new task** creates a separate task. The empty saved-task option
also opens this draft; refreshing the saved list keeps the draft selected.
Progress uses numbers local to the selected task, showing its latest 20 entries
by default. **Show all activity** expands that task's history. Internal global
receipt cursors remain unchanged and are not used as display numbers.

Modal is selected for hosted judging. The optional Sandbox runner is implemented and
live-accepted on the real runtime; the hosted access-code session flow, signed
resumable sessions and single-writer durable state are implemented, covered by local
tests and live-verified at `https://kubajaworski999--hosted.us-east.modal.run`
(scripted mode, no judge grant enabled). Personal local Docker hosting remains the
default. See [docs/modal-hosting.md](docs/modal-hosting.md) for evidence and limits.

## License

Remote AI Operator's original source and documentation are licensed under
[Apache-2.0](LICENSE). Attribution is in [NOTICE](NOTICE). Third-party dependencies
retain their own licenses. This applies to this standalone repository; separately
maintained private products are not included in this distribution.

