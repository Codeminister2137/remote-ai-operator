# Architecture

A local FastAPI application owns a single foreground task controller and SQLite
state/events. Model adapters return structured repository operations; the host
validates those operations and records actual effects. Docker executes generated
code with protected tests, no network/secrets, bounded resources and no host socket.

The first offline slice uses a labelled scripted adapter through the same control
path intended for Meta. It proves controller behaviour, not model authorship or
quality. Live inference requires an explicit finite grant; a small live Meta coding
trial passed protected checks and owner-approved application (live-acceptance.md).

Candidate source, base source and validation receipts are persisted separately.
Approval binds the candidate/base and receipt digest and expires. Application
uses a write-ahead applying state and an atomic file replacement. Restart never
replays an uncertain operation; reconciliation inspects actual bytes.

HTTP binds localhost. The owner-selected remote deployment is private HTTPS via
Tailscale Serve, plus the application owner token. A future private integration consumes the task API; no private
code is a dependency. Power controls and desktop automation are outside this slice.

The prepared direct Meta adapter uses Responses stateless encrypted replay. Private
continuation is transient; durable history contains visible call arguments/results
and safe usage receipts. An allowance ledger reserves before sending and does not
refund failures or reset on restart. Activation requires an explicit model mode,
live flag, private grant and server-only API key; none is enabled by default.

Cancellation is a separate durable control flag, so the phone can request it while
the execution lease is held. Model output is checked again before effects. Docker
polls cancellation and removes its owned container. Application admission and
cancellation serialize in SQLite; already-started atomic application is not revocable.
Approval binds raw source, base, evidence and protected policy/image fingerprints.
Fresh validation invalidates old nonces. The sample results endpoint executes only
the approved target through a protected Docker entry point; no host import.

The origin allowlist is explicit and defaults to localhost. Preparing an HTTPS
origin does not activate a tunnel or trust forwarded identity headers. The body
limit counts streamed bytes using the locked Starlette middleware. Actual remote
access still requires owner device/account setup and a physical phone acceptance run.
Recent-task discovery reads bounded summaries from the existing SQLite rows in
creation order; it adds no persisted schema, queue or provider call. All summaries
require the same owner authentication. The browser restores saved selection and
polling without auto-running work, keeps tokens only in page memory and rejects
stale task/event responses after selection or credential changes.
Read-only allowance metadata uses the same frozen grant and SQLite ledger as
execution. It performs no reservation, model construction, network call or refund.
The UI refreshes it on connection and receipt updates. Server admission enforces
limits independently of display state. The authenticated recovery control exposes
existing explicit reconciliation under the nonblocking process lock; it does not
add automatic replay or a background recovery service.

Protected runner commands carry an internal GNU timeout kill deadline in addition
to the host watchdog. The approved pinned image supplies this executable. Timeout
and deadline-policy version are included in the validation fingerprint. Host death
may leave stopped-container metadata requiring cleanup; no janitor is introduced.

## Hosted deployment (in progress)

`create_hosted_app` is a separate application from the local owner-token app. Its
login boundary is a shared access code exchanged for an opaque server-generated
session (see ADR-0004); the browser never supplies a subject, workspace or task-store
path. Non-GET routes require the `X-Operator-Request: 1` header in addition to the
exact-origin check, and the session cookie is HttpOnly/SameSite=Strict/Secure on
HTTPS. `HostedSessions` combines that admission with the existing bounded,
memory-only session credentials, so a session exists before any provider key does.

`ScopedOperators` maps each server-generated subject to its own task store, so a
task ID from another visitor is not readable or actionable. Hosted sessions are
keyless by default and run the labelled scripted reference at zero inference cost.
Attaching a visitor Meta key switches that session to Meta with the visitor's own
key through the per-request borrow; without a configured grant the hosted app
refuses to attach keys at all. A dropped or expired key returns the session to the
scripted reference and never falls back to owner funds. The local owner-token
application, CLI and Docker runner are unchanged.

Hosted sessions are signed with a stable deployment secret, so a browser resumes the
same subject after a restart. Task, event, approval and allowance state lives in one
SQLite store per subject on a mounted Modal Volume; the deployment runs exactly one
container(`max_containers=1`, `--strategy recreate`) and commits the volume after each
committed write(ADR-0005). This is single-writer demo durability, not distributed
multi-writer state. Logout is process-local and does not revoke a retained token
across a restart.

## Accepted next deployment target

Modal is selected for a separate hosted sample demo; see ADR-0003. The optional
Modal Sandbox adapter is implemented and live-accepted on the actual runtime
(docs/modal-hosting.md). The hosted web/session layer and single-writer durable
state are implemented, locally tested and live-verified at
`https://kubajaworski999--hosted.us-east.modal.run` in scripted mode; the judge Meta
allowance is not enabled. The current local FastAPI/SQLite/Docker deployment remains
usable for personal supervision. Session-only user keys are approved and implemented;
do not infer working multiuser scale, cloud-to-PC control or distributed recovery from
this target.
