# Reviewer walkthrough

Run from the complete source checkout or from the extracted source archive. A built
wheel alone does not include the sample workspace and protected checks. No private
application, IDE account or provider key is required for the offline workflow.
Python 3.14, uv and Docker with a running Linux container engine are required for the
tested development setup.

## Install

```text
uv sync --locked
docker pull python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
uv run --locked remote-ai-operator demo --inject-failure
```

The CLI prints a fresh task record, diff, validation evidence and exact-action nonce.
Expected status: awaiting_approval; passed: true and 7 protected checks. The actor is
explicitly SCRIPTED OFFLINE REFERENCE. This proves controller behaviour, not live
Meta authorship. Review first; use the printed ID/nonce with the review and approve
commands in README.md. Never paste approval nonces into public issues or logs.

Each task gets separate candidate and target directories. Start a new task for a
fresh seed instead of deleting past evidence. A paused task is a safe stop, not a
successful demo; inspect its events and validation output before continuing.
If Docker is unavailable, the app refuses validation; it does not run generated code
on the host. Prepare the pinned image before disconnecting the container network.

## Browser workflow

Set OPERATOR_ACCESS_TOKEN to a new random secret of at least 32 characters in your
shell. Then run:

```text
uv run --locked remote-ai-operator serve
```

1. Open http://127.0.0.1:8765 and enter your token. Connect and inspect the mode.
2. Select **New task**, review the registered sample request and create/run it.
3. Read selected-task activity, expand older entries if needed and inspect the diff.
4. Review validation, then approve the exact change. Run the approved importer:
   two issues are accepted; the duplicate and missing title produce two errors.
5. Switch saved tasks or reload/reconnect. Selecting work never runs or approves it.

The default server binds loopback. For phone use, follow docs/phone-access.md and
configure your own approved private transport. No shared reviewer account or public
endpoint is supplied by this source checkout. Do not publish the owner credential.

## Hosted judge demo

A deployed scripted demo runs at
`https://kubajaworski999--hosted.us-east.modal.run`. Judges receive the shared access
code out-of-band; it is never in this repository. Enter the code once and the browser
holds only a signed session cookie. The demo starts on the labelled scripted
reference at zero inference cost, isolates each visitor's tasks and can be left at any
time. A visitor Meta key can be attached only when the owner has enabled a finite
judge grant; until then the key endpoint refuses. The hosted app has no access to the
owner's PC or private source. See docs/modal-hosting.md and ADR-0004/0005.

The same mode runs locally: set OPERATOR_VISITOR_CODE and OPERATOR_SESSION_SECRET, then
`remote-ai-operator serve --hosted`. It issues a signed, server-generated visitor
session and isolates visitor task stores. The Modal deployment adds single-writer
durable state with an explicit `--strategy recreate` deploy; with a stable signing
secret and runtime root a restart resumes the subject and its recorded task state.
Logout is process-local.

## Real Meta and automated checks

For live Meta coding, follow docs/meta-tooling.md. Supply your own authorized key,
verified credits, current price assumptions and finite private grant. The example
grant is deliberately inactive. The phone spending panel is read-only; edit private
configuration deliberately on the server. Never use a new grant to erase usage.
See docs/live-acceptance.md for completed real-model evidence and its limitations.

Run the automated commands in README.md; enable OPERATOR_DOCKER_TESTS=1 for actual
container checks. The optional browser script requires Node 24+ and Edge/Chromium,
uses a fresh local scripted server and closes its own processes. It spends no
model credits. Inspect BUILD_LOG.md and DEMO.md for new-work attribution and the
recording plan. A physical-phone Meta task and approval are owner-confirmed; the
three-minute recording remains a separate acceptance gate.
