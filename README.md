# Remote AI Operator

**Supervise a coding agent from your phone. Approve the exact change — nothing else.**

> **Judges — try it live (5 minutes, no install):**
> `https://remote-operator--hosted.us-east.modal.run` ·
> entry code `KwWLEbaTQ-YzSDqn4pqEz5hdnVHBShJz` (public by owner decision —
> entry is open, sessions stay isolated per visit).
> Guided path: [Judge quick-start card](docs/judge-card.md)
> (code → wizard → sample → approve → try-it).

This project began as a way to drive my other project, ai-workbench, remotely:
everyday app development from anywhere, and a way to talk to my locally-run
models when away from the PC. It grew into a standalone supervised-coding
operator — and its own chat, approvals, and remote operation were used
throughout this event to build it. Started October 26–November 4, 2026;
everything a judge touches was built during the event.

## Hosted demo vs local install

| | Hosted demo (link above) | Local install (below) |
|---|---|---|
| Fixed sample task (CSV import) | Yes — scripted (free) or live Meta AI with your own key | Yes |
| Supervised runs, chat, dictation, try-it | Yes | Yes |
| Custom work on your own repositories | No — needs local folders | Yes, via `--work-root` |
| Cost to you | $0 on scripted; your own key/limits on Meta | Your own grant/key |
| Install | None | ~15 min, Windows + Docker |

Honest limits: hosted runs the fixed sample only — your key switches that
sample from a recording to a live agent run; it does not unlock custom tasks.
Repository tasks run no automated checks (you are the reviewer). Broader
repository tools and release work remain unfinished. No private packages are
required.

**Default: clearly labelled scripted offline reference** (a fixed pre-written
demo: no AI, no cost, no key needed). The Meta model adapter (Responses API)
requires explicit activation and finite credit admission; a live
source-edit/test run is verified (see docs/live-acceptance.md). Controlled
edits, append-only local records (SQLite receipts), isolated tests,
cancellation, exact-change approval and a working sample import-results
screen. Scripted and real-Meta phone operation are verified on a physical
phone. For a short independent walkthrough, see
[Reviewer guide](docs/reviewer-guide.md).

## Phone operation (served UI)

The served UI is chat-first: one numbered flow from chat to review.
For the 5-minute judge path (code → wizard → sample → approve → try-it),
see [Judge quick-start card](docs/judge-card.md).

- Setup wizard: 4 guided steps (access code → judge account → spending
  limits → key or offline) with inline validation and Go-to buttons.
- Chat: tasks and supervisor threads live side by side, with bubbles
  and a typing indicator. A message starting with `REPLY:` lands in the
  shared Owner replies thread instead of starting a demo task. Anything
  new you write in a thread counts as your reply.
- 🔔 Inbox bell: one badge for owner messages and waiting questions;
  clicking it jumps to the newest unread item.
- Dictation: speech transcribed by Meta in ~55-second pieces charged
  to the shared allowance, or by the browser's own speech recognition
  when Meta is unavailable; fills the field only, never sends.
- Run settings box: Planning only vs Plan + implement run mode, Attended vs
  Unattended supervision, approval time limits (tasks ~60 min, plans ~10 min),
  and PC sleep with confirm, delay and cancel.
- Review and approve: the exact change side-by-side or as one list,
  evidence card, approval checklist, approval countdown and the
  Approve / Reject actions for the selected task or plan.
  **Load sample review** fetches a ready-made review; **Try it in
  Step 3** jumps to the outcome.
- Owner questions: the worker can pause one task with a question
  while other runs continue; answering resumes it.
- The server runs queued work itself every 30 s; no manual command is
  needed. Docker Desktop is revived automatically after sleep/wake.

Details: docs/remote-supervisor.md, docs/owner-questions.md,
docs/supervised-ui-slice.md, docs/file-admission.md.

Provenance: everything above was built during the event (Nov 2026) and is
part of the judged entry. Two owner-only areas belong to an earlier
internal product, are excluded from the judged entry and hidden from
judge visits: Pending actions from Workbench and Worker & machine. See
the provenance table in [Submission draft](docs/submission-draft.md).

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

## Install on a PC, step by step

From an empty Windows PC to a running operator (about 15 minutes).
Every command runs in PowerShell.

1. Install Python 3.12 or newer, then close and reopen PowerShell:

   ```powershell
   winget install -e --id Python.Python.3.12
   python --version
   ```

   If winget is missing, install Python from python.org instead
   (tick "Add to PATH" during setup).
2. Install git, then close and reopen PowerShell:

   ```powershell
   winget install -e --id Git.Git
   git --version
   ```
3. Clone this repository and enter it:

   ```powershell
   git clone https://github.com/Codeminister2137/remote-ai-operator.git
   cd remote-ai-operator
   ```
4. Run the installer:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
   ```

   It creates the environment, generates your access password (shown
   once — save it), offers the Tailscale phone-access step (say no to
   skip it; the PC program still works), and starts the serve
   automatically at sign-in.
5. On your phone: install the Tailscale app, sign in to the same
   account, open the phone URL the installer printed, and enter your
   access password.
6. If something looks wrong: check `.runtime\serve.log` in the project
   folder, or read the plain-language
   [self-host access guide](docs/self-host-access-guide.md) (token
   rotation, uninstall, no-Tailscale alternatives).

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
The nonce expires after sixty minutes. A changed candidate, target or receipt
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

After connecting, the **Recent tasks** list shows the latest 20 saved tasks. With no
task link (no `#task-id` in the address bar), nothing loads automatically —
select a saved task or create a new one. Selecting one only loads its recorded state;
it never starts inference or approves a change. A running task resumes progress
polling after reconnect. Manual task IDs remain an optional recovery control.

API: POST `/api/tasks`; authenticated GET `/api/tasks?limit=20` returns only bounded
id/prompt/status summaries (limit 1..50); GET `/api/tasks/{id}` and
`/api/tasks/{id}/events?after=SEQUENCE`;
POST `/api/tasks/{id}/run`, `/api/tasks/{id}/approve`, `/api/tasks/{id}/reject`,
`/api/tasks/{id}/reconcile`, `/api/tasks/{id}/cancel`, `/api/tasks/{id}/revalidate`,
`/api/tasks/{id}/sample`. Every API request to this local server requires
`Authorization: Bearer TOKEN`. The server selects the actor; scripted mode is default. See docs/meta-tooling.md for guarded Meta activation. No endpoint
accepts a provider, billing source, shell command or arbitrary workspace path.

## Developing real repositories (local serve only)

The sample task above edits a safe practice file. To continue development on a
real local repository instead, register it as a work root when starting the
local serve (repeatable; local serve only, never hosted):

```powershell
$env:OPERATOR_ACCESS_TOKEN = "<at least 32 random characters>"
uv run --locked remote-ai-operator --work-root C:\Users\Jakub\PycharmProjects\my-app serve
```

To add folders later, extend `--work-root` (`OPERATOR_WORK_ROOTS` in
`.env` on installed PCs) and restart the serve. Roots are boot-time
configuration on purpose: no browser control can widen them.

In the UI, the **Work on** picker then offers the registered folder next to the
sample task. A repository task lets the agent read files and stage changes
under that folder; nothing is written until you approve the staged diff, and
the approval re-verifies that neither the staged files nor the repository
changed since review. Unlike the sample task, repository tasks run **no
automated checks** — the UI says so, and you are the reviewer.

First-version limits: paths stay inside the registered folder (no parent
references, absolute paths, dot-files, or secret-looking names such as
`*.pem`/`credentials.json`); new files need an existing folder; files must be
UTF-8 text under 32 KB. The hosted visitor demo never offers repository tasks.

## Hosted visitor mode

`serve --hosted` starts a separate application for visitors instead of the owner
token. Set `OPERATOR_VISITOR_CODE` to 16-256 printable characters, then:

```text
uv run --locked remote-ai-operator serve --hosted
```

A visitor enters that code once; the server issues a signed session cookie and a
server-generated visitor identity, so one visitor's tasks are not readable or
actionable from another visit. Set `OPERATOR_SESSION_SECRET` (at least 32 random
characters) to keep a visit stable across a process restart; without it the token
is still unforgeable but visits end with the process. Hosted sessions start on the labelled
scripted reference at zero inference cost. A visitor Meta key can be attached for
that session only and detached at any time; it is never used as identity, never
persisted and never falls back to another key. Real Meta work additionally requires
a live model route set up by whoever runs the server (already set on the deployed
judge URL below); without one the key endpoint refuses. Judges register with the
access code, log in with their own password, and set their own finite limits
against their own key; the server pins the model via
`OPERATOR_JUDGE_MODEL` and the owner pays nothing (see docs/modal-hosting.md).
Without an attached key, visitors can only run the free scripted reference —
every real model call spends the visitor's own key under their own limits.

This mode is a localhost preview of the session flow. A hosted judge deployment is
live at `https://remote-operator--hosted.us-east.modal.run` with open entry: the
visitor access code is `KwWLEbaTQ-YzSDqn4pqEz5hdnVHBShJz` (public by owner decision 2026-11-03 — entry is
open to everyone; sessions stay isolated per visit). It uses the durable volume described
in ADR-0005, written by one process at a time; `modal deploy --strategy recreate
modal_app.py` is the only supported deploy. Logging out ends only that browser's
visit: a kept token stays valid until expiry after a restart, so rotate the
signing secret to invalidate all visits. See
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
The applied file must still match the approved bytes. Generated code is never
imported into the web server. The sample has one existing issue with ID `existing`;
its import results are transient, with no extra database or external service.

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
count and expiry. **Refresh spending** only reloads the display. Reservations
include failed requests and are not a provider invoice or account-wide billing
cap. Owners configure limits in the private grant before starting the server;
judges set their own limits in the app (Limits step) against their own key.
Unavailable, expired or exhausted allowances block new AI work in the UI while
preserving local review and validation. The server's check is final.
Authenticated GET `/api/allowance` reports metadata without starting a model call.

**Inspect interrupted action** checks saved effects after an interrupted operation.
It never resumes AI work, replays an edit or approves a change. Active execution
is refused. If the saved result matches, it establishes a paused or completed
state; if it doesn't match, inspect manually. Then review the state before
choosing the next action.

The pinned Docker image includes GNU `timeout`; each validation/preview command
also has an internal kill deadline if its host controller disappears. The host
watchdog and explicit cleanup remain. Custom runner images must provide GNU
`timeout`. Runner deadline changes invalidate pending validation approvals, requiring
fresh validation; completed records are preserved.

The **Demo task history** section has an explicit **Start new demo draft** button. It clears selected progress,
review evidence and sample results without deleting or cancelling saved work.
**Create and run new task** creates a separate task. The empty saved-task option
(**New task (no saved task selected)**) also opens this draft; refreshing the saved list keeps the draft selected.
Activity entries are numbered within the selected task, latest 20 first.
**Show all activity** expands that task's history.

Modal is selected for hosted judging. The optional Sandbox test box is implemented
and tested live on the real runtime; the hosted access-code visit flow, signed
visits that survive restarts and durable state written by one process at a time
are implemented, covered by local tests and live-verified at
`https://remote-operator--hosted.us-east.modal.run`
(offline reference, plus a judge-funded live run to completion — see docs/modal-hosting.md).
Personal local Docker hosting remains
the default. See [docs/modal-hosting.md](docs/modal-hosting.md) for evidence and limits.

## License

Remote AI Operator's original source and documentation are licensed under
[Apache-2.0](LICENSE). Attribution is in [NOTICE](NOTICE). Third-party dependencies
retain their own licenses. This applies to this standalone repository; separately
maintained private products are not included in this distribution.

