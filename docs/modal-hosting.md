# Optional Modal runner - implementation status

The owner selected Modal hosting. The adapter is implemented and **live-accepted on
the actual Modal runtime**(2026-10-27, receipt summary below). Session-key handling,
hosted access-code sessions, per-visitor isolation and single-writer durable state
are implemented, covered by local tests(`tests/test_hosted.py`,
`tests/test_deployment.py`) and **live-verified on the deployed URL** below.

Install the optional dependency with `uv sync --locked --extra modal` only when
needed. The ordinary local install and CLI still use Docker and do not import or
require Modal. There is no new default CLI route or automatic cloud activation.
An explicitly configured trusted deployment can construct ModalValidator with its
Modal app and allow_remote_execution=True after finite compute authorization.
The flag is an opt-in, not a compute meter; verify account budgets before live use.

The adapter uploads only the registered source and three protected check files
through sandbox stdin. It never uploads the checkout, host environment, provider keys
or private runtime. It uses the pinned Python image, explicit outbound block, no
injected secrets/identity token/volumes/ports, and finite Sandbox CPU/memory/lifetime.
A trusted root supervisor starts the generated-code child as UID/GID 65534 with
no-new-privileges, cleared environment and CPU/address-space/file/process/descriptor
limits. Source/checks are root-owned read-only files. Child output and wall time
are capped before a bounded supervisor receipt is returned. Owned Sandboxes are
terminated and checked; uncertain cleanup cannot yield passing validation.

These are Modal-specific protections, not a claim that the filesystem/runtime
exactly reproduces every Docker flag. Generated code has isolated scratch storage;
the sandbox root filesystem is not configured with Docker's read-only mount flag.
Do not treat local Docker supervisor tests as proof of Modal runtime behaviour.
Live acceptance must test privilege changes, network refusal, deadline/output limits,
protected-file permissions and cleanup on the selected Modal runtime.

Shared SourceValidator code preserves existing exact-source/policy/preview checks.
Docker policy fingerprints remain unchanged by the extraction. Modal policy adds
backend, supervisor hash, resource bounds and network configuration, so evidence and
approvals cannot transfer silently between the two runners.

SDK errors return a safe phase/type to callers. Original exceptions are retained
only in the runner's private in-memory diagnostics for trusted investigation and
are not serialized to task history or public errors. A creation failure can have
unknown remote effects; do not blindly retry. Live operator diagnostics must inspect
actual named resources if creation returned ambiguously.

Offline checks: SDK-shaped lifecycle doubles plus execution of the actual supervisor
inside the existing local Docker image. Tests cover passing checks/preview, non-root
protected-file refusal, absence of synthetic inherited credentials, infinite execution,
output flood, cancellation, malformed receipts, policy changes and cleanup failure.
The optional SDK 1.6.2 was inspected in a separate test environment without cloud
calls. Source/module compatibility passed with Modal absent from the default env.

## Live runtime acceptance (2026-10-27)

Ran once, bounded to 5 Sandbox creations within the authorized 5 USD starter-credit
phase: CPU0.25-1.0, memory 128-256MiB, lifetime<=40s, validation timeout<=30s, zero
inference calls, stop on first failure. All phases passed on the real runtime in
22.1s of wall time:

| Phase | Result |
| --- | --- |
| P1 reference validation |7 protected checks passed on the pinned image |
| P2 applied preview |candidate preview accepted 2 issues and reported 2 row errors |
| P3 containment |non-root child, cleared environment, root-owned read-only checks, network refused |
| P4 deadline |infinite child stopped with the Sandbox child deadline receipt |
| P5 output bound |output flood truncated at exactly 65536 bytes with the output-allowance receipt |
| P6 cleanup |5 creations, 0 running, no owned leftovers |

Recorded candidate, checks, image, pinned Modal policy digest, sandbox identities and
per-phase receipts are private under `.runtime/modal-acceptance/` and are not
published. Acceptance establishes runtime compatibility of these protections on
that date and account region; it is not a guarantee for future images, regions or
SDK versions. No Meta inference or judge allowance was consumed.

References checked 2026-10-27:
https://modal.com/docs/sdk/py/latest/Sandbox
https://modal.com/docs/sdk/py/latest/io_streams
https://modal.com/docs/guide/sandbox-networking
https://modal.com/docs/guide/budgets

## Session-only credential and workspace foundations

SessionKeys stores bounded process-memory credentials for already-authenticated
opaque subjects. Session tokens are separate random credentials; only their hashes
index the in-memory map. Defaults are 30-minute absolute expiry and 32 concurrent
entries, with validated configurable bounds. Every model request borrows the key
through a callback; the Meta actor drops that reference afterward. Revocation or
expiry blocks the next borrow without reserving money or falling back to another key.
Keys are not a login identity or a means to select arbitrary workspace paths.

Expired entries are purged on access/creation or explicit cleanup; hosted integration
must schedule cleanup and clear sessions on shutdown. Removing references is not
a guarantee of forensic memory erasure, and cannot revoke a request already sent.
Transient encrypted continuation remains in the actor while it is alive; after
reauthentication with a fresh actor, cold resume uses visible saved history honestly.

ScopedOperators creates distinct task stores for trusted authenticated subjects.
Knowing another task ID does not grant access to its records, events or actions.
Reopening the same subject preserves its existing tasks and spending ledger.
The login layer supplies the subject; an arbitrary browser field is not trusted.
These local filesystem boundaries are not distributed Modal persistence. Hosted
admission must also cap aggregate Modal compute; visitor-funded Meta calls still
consume the host's Modal credits.

## Hosted session wiring

`create_hosted_app` implements the accepted shared-access-code login(ADR-0004):
`POST /api/session` exchanges `OPERATOR_VISITOR_CODE` for a signed, expiry-bound
session cookie, non-GET routes require `X-Operator-Request: 1`, and every task route
resolves the principal from that cookie. The cookie subject is server-generated and
cannot be chosen by the browser. Hosted sessions start keyless on the labelled
scripted reference; `POST /api/session/key` attaches a visitor-owned Meta key for that
session and `DELETE /api/session/key` returns it to scripted mode. Without a
configured grant the key endpoint refuses instead of implying owner-funded billing.
The local owner-token app is unchanged. See `tests/test_hosted.py`.

The token is HMAC-SHA256 signed under `OPERATOR_SESSION_SECRET` (at least 32 bytes).
With a stable secret a browser resumes the same subject after a container restart;
without one, sessions still cannot be forged but end with the process. Logout is
process-local: a retained token remains valid until expiry after a restart. Rotate
the secret to invalidate all sessions (recorded tasks stay).

Run it locally with `remote-ai-operator serve --hosted` and a set
`OPERATOR_VISITOR_CODE`. This binds localhost only; it is a test harness for the
session flow, not the public deployment. `scripts/hosted_browser_smoke.py` drives the
same flow in an owned browser with two synthetic visitors.

## Durable hosted state and deploy protocol (ADR-0005)

State lives on the `remote-operator-state` Modal Volume v2 at `/data/runtime`.
The design is deliberately single-writer:

- `max_containers=1`, `min_containers=0`, `scaledown_window=300`.
- Deploy only with `modal deploy --strategy recreate modal_app.py`, so an old and a
  new container never write the same SQLite files. Do not use a rolling deploy.
- A secret-only change may be reported as "no changes detected" and leave the old
  secret mounted. To rotate the access code or signing secret, run
  `modal app stop -y remote-operator-hosted` first, then deploy again.
- Startup reloads the volume, then runs `PRAGMA quick_check` over every visitor
  store. A corrupt store refuses startup; nothing is deleted automatically.
- `Store(after_commit=volume.commit)` flushes after schema creation and each
  committed task save, allowance reservation and cancellation. Reads do not commit.
  Shutdown also commits pending writes.
- The `remote-operator-hosted` Modal secret supplies `OPERATOR_VISITOR_CODE`,
  `OPERATOR_PUBLIC_ORIGIN` and `OPERATOR_SESSION_SECRET`. Values stay private and
  outside the repository. `OPERATOR_PUBLIC_ORIGIN` must be the final HTTPS origin so
  the cookie is `Secure`.
- The image contains only `src/remote_operator`, the synthetic `sample` and
  `tests/acceptance`, never the checkout, runtime directory or private plans.

SQLite commit and volume commit are not atomic: a failed `volume.commit()` can leave
a write visible locally but not durable, and there is no retry queue. Keep the volume
exclusive and treat this as demo-grade durability, not a billing ledger.

Deploy-time details verified on Windows and in the Modal image:

- Set `PYTHONIOENCODING=utf-8` (and optionally `PYTHONUTF8=1`) for the deploy command;
  Modal's progress renderer otherwise fails on the Windows console code page.
- `env()` must precede `add_local_*()` in the image chain, or Modal refuses the build.
- `include_source=False` means the entrypoint module must be present in the image.
  `/app` is on `PYTHONPATH` together with `/app/src`, which makes `modal_app.hosted`
  importable. Removing it produces a crash loop with
  `ModuleNotFoundError: No module named 'modal_app'`.
- Modal mounts the volume at `/data` through a symlink to its volume store. The
  principal-workspace guard checks only the caller-selected root and subject
  directory; checking every ancestor refused every hosted workspace.

## Live hosted deployment (2026-10-27)

Deployed and verified at:

```text
https://kubajaworski999--hosted.us-east.modal.run
```

The access code is **not** in the repository or this document; it is supplied
separately to judges (submission text or the event channel, assumed, not confirmed).
Deployment uses the `remote-operator-state` Volume and the
`remote-operator-hosted` secret. The private session-signing secret and access code
live outside both repositories; public evidence omits them.

Live checks (no visitor key, no inference, no model spend; private receipts under
`.runtime/hosted-deploy/receipts/`):

| Phase | Result |
| --- | --- |
| access, isolation, CSRF and scripted task |20 checks passed: wrong code 403; cookie HttpOnly/SameSite=Strict/Secure; missing request header 403; scripted task created and listed; second visitor saw an empty list and 404 for the task ID; tampered cookie 401; key attach 409 without a grant and no key echo |
| cold resume |container stopped(`tasks=0`), next request started a fresh container; same cookie resumed the subject and listed the committed task(4 checks) |
| scripted run and exact approval |7 checks: Modal Sandbox validation passed, approval nonce recorded, approve applied the candidate, `applied`/`apply_started` events recorded; no sandbox container left running |
| cold resume after approval |3 checks: task still `completed`, applied digest and approval/apply history survived a fresh container (restart) |
| idle scale-to-zero resume |container stopped after 5 idle minutes(`tasks=0`, no containers); a fresh request resumed the same session and listed the committed task(4 checks) |
| deployed URL in a real browser |headless Edge ran the whole visitor UI on the live URL: scripted mode, wrong code refused, key controls hidden, task run/exact approval/applied sample(2 accepted issues, 2 expected errors), second-visitor isolation, reload keeps the session, empty browser storage, session exit, no horizontal overflow at desktop/mobile widths(1 sandbox creation, no inference) |

The cold-resume phases used an explicit container stop, which is stronger than an
idle scale-down for crash durability. Genuine idle scale-to-zero was then observed:
the last request finished at 15:29:55 Europe/Warsaw, `modal app list` reported
`tasks=0` and `modal container list` returned no containers at 15:35:39, and a fresh
request at 15:36:00 resumed the same session and listed the committed task(4 checks,
`.runtime/hosted-deploy/receipts/06-idle-resume.json`).

Remaining limits: no login throttling; logout is process-local; the hosted route is
scripted-only until the owner enables a finite judge grant. Do not claim Meta
inference, judge-funded usage or cross-region recovery.

Credential-echo regression checks found and fixed metadata/replay retention:
provider-echoed key strings are redacted from receipts and retained replay items;
a key echoed as a controlled call ID is refused. No key enters repository tools.
All session tests use synthetic keys and local HTTPX fixtures, with no inference.
