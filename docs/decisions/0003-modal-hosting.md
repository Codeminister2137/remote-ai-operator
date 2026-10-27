# 0003 - Modal hosting with a separate personal deployment

Status: hosting target and session-only credentials accepted 2026-10-27; judge access and final allowance pending.

## Owner decision and rationale

The owner selected Modal for the judge/demo deployment because starter credits
are available, additional event credits may follow, and meaningful use of an event
partner fits the intended submission. Additional credits are not assumed granted.
The owner wants visitors to supply their own inference keys, with a small separately
reserved judge allowance, and wants personal self-hosting for later private-product
integration to remain available.

## Alternatives considered

A conventional Linux VM runs the existing Docker adapter with less adaptation,
but has an idle rental cost and does not use the selected partner platform.
Modal supports hosted HTTP applications and managed code-execution Sandboxes;
it adds an execution adapter and demands explicit persistence/scaling work.
The owner chose Modal despite that implementation cost for the stated reasons.

## Accepted boundaries

Keep the standalone controller, Meta transport and approval policy portable.
Retain the local FastAPI/SQLite/Docker path and private phone deployment. Add Modal
as an optional deployment/runner dependency, not a requirement for local users.
Hosted demo code execution uses Modal Sandboxes; do not assume a nested Docker
daemon or silently execute model code in the web/controller process.

The hosted demo uses synthetic registered sample projects. It has no connection
to the owner's PC, private code or private product. Future local integration uses
explicit authenticated interfaces and separately authorized tools/data routing.
A cloud-hosted controller cannot operate a sleeping PC without an independently
reachable execution/wake component; none is introduced by this decision.

## Credential decision and remaining verification

The owner approved session-only visitor keys in browser/server memory, transmitted
over HTTPS to the trusted hosted controller and directly to Meta. Keys are not
persisted; expiry, disconnect or controller restart requires re-entry. Persistent
encrypted storage was rejected for this scope because it adds key-management and
deletion obligations; session re-entry is the accepted trade-off.
Never put provider keys into task history, logs, URLs, validation sandboxes or receipts.

Recommend isolated user workspaces/authorization, bounded access admission and
explicit choice of visitor-funded or judge-funded inference. Never fall back from
a visitor's failed/exhausted key to the owner's key. Visitor-funded Meta calls
still consume host-owned Modal compute credits. The initial Modal development phase
is bounded by the proposed 5 USD credit allowance. Final judge Meta allocation will
be chosen from remaining event credits near submission, separately from the current
20 USD development soft cap. No judge grant is activated now. Mechanism for sharing
judge access remains unverified; private invitations are an option, not a requirement.

Persistence must preserve admission-before-send and exact effects through cold
starts. Existing SQLite/process locks cannot simply be assumed to coordinate
multiple Modal containers. Verify storage/locking/checkpoint and deployment-overlap
semantics before live use; do not claim distributed safety from a container count.
No shared multiwriter SQLite file or new database service is selected by this ADR.

## Official references checked 2026-10-27

- https://modal.com/docs/guide/webhooks - FastAPI/ASGI hosting.
- https://modal.com/docs/guide/sandboxes - managed code execution.
- https://modal.com/docs/guide/sandbox-networking - explicitly block outbound network.
- https://modal.com/docs/guide/volumes - commit/reload persistence semantics.
- https://modal.com/docs/guide/budgets - usage budgets versus net spend limits.

These establish platform capabilities, not account entitlements or implementation
acceptance. Account keys, credit balances and raw diagnostics remain private.

Owner-reported organizer clarification: the three-minute video is the primary judging artifact, and judge sign-in is acceptable. This does not establish a private credential-delivery channel or select an authentication provider. Continue implementation without assuming invitations can be distributed.
