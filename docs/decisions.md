# Accepted baseline

2026-10-26: owner approved a standalone Python application with FastAPI/Uvicorn,
SQLite, plain browser assets and a Docker runner. Keep model adapters separate
from task policy and controlled execution. The sample is a small issue tracker;
CSV import spans application logic, UI and tests. Consequential action is applying
the exact verified patch to a separate local target, not pushing to an external
account. Start offline; no inference credits or numerical live budget available.

Phone transport: owner selected private Tailscale Serve and accepted HTTPS certificate hostname disclosure after installing the clients. Application bearer authentication remains required. Remote wake is deferred unless a
supported always-on wake endpoint is established; it is not a core dependency.
No public deployment is selected. Private HTTPS Serve setup is authorized. Apache-2.0 was selected for this repository on 2026-10-27; see 0002 below.

Implementation detail: one task at a time; only an explicitly registered sample
workspace can run. The first receipt/approval implementation accepts one source
file per candidate, making atomic application/reconciliation tractable. Broader
multi-file application is a later extension, not an implied crash guarantee.

2026-10-26: owner selected uv to align development tooling with their existing
projects. Use an independent .venv, committed uv.lock, Python 3.14 development
selection and dependency-groups.dev; no shared runtime environment or private
package dependency is introduced.

Implementation details within the approved scope: use direct Meta Responses with
stateless encrypted replay, keep encrypted continuation transient and label cold
visible-history resume honestly. Do not silently activate inference from keys.
Persist allowance consumption in the existing SQLite store. Bind approvals to
protected policy/image as well as source. Sample execution stays inside Docker.
HTTPS origin configuration remains explicit and never opens remote access itself.
2026-10-27: owner accepted user-configurable local inference spending limits.
The earlier provider hard-cap prerequisite was unavailable on direct Meta; the
old grant flag is refused rather than silently reinterpreted. See
[0001](decisions/0001-local-spending-limits.md) for rationale and billing limits.

2026-10-27: owner selected Apache-2.0 for this standalone project. See [0002](decisions/0002-apache-license.md) for actual rationale, alternatives and the private-product boundary.

2026-10-27: owner selected Modal for judge hosting, visitor-owned Meta keys with a small judge reserve, and continued personal self-hosting. See [0003](decisions/0003-modal-hosting.md) for rationale and remaining credential/budget choices. Existing local deployment remains supported.

2026-10-27: owner accepted a shared hosted access code exchanged for an opaque server-generated visitor session, with optional session-only visitor Meta keys, instead of an external identity provider or persisted per-visitor credentials. See [0004](decisions/0004-hosted-sessions.md) for rationale, alternatives and the code-delivery limit. Amended 2026-10-27: sessions are signed and resumable with a stable deployment secret; logout is process-local.

2026-10-27: owner selected single-writer SQLite on a Modal Volume v2 for durable hosted state(`max_containers=1`, recreate deploys, volume commit after each committed write), rather than an ephemeral store or a new distributed database. See [0005](decisions/0005-durable-hosted-state.md) for rationale, alternatives and the non-atomic-commit limits.
