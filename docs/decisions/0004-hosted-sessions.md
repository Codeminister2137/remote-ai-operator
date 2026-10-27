# 0004 - Hosted visitor sessions with a shared access code

Status: accepted 2026-10-27; amended 2026-10-27 for signed resumable sessions. Access-code
transport for judges remains assumed to be submission text or an event channel.
Durable hosted state is accepted separately in 0005.

## Context

The hosted demo needs to let an external visitor run the workflow on a synthetic
sample without receiving the owner's local bearer token, and without the owner
operating the PC. ADR-0003 already accepts session-only visitor keys and isolated
workspaces. The missing piece was a login boundary that:

- creates a server-generated principal that cannot be chosen by the browser;
- keeps the local single-owner token application unchanged;
- adds no external identity provider, account database or persisted credential;
- costs nothing when a visitor only browses the labelled offline reference.

## Owner decision and rationale

The owner accepted a shared access code exchanged for an opaque server-generated
visitor session, with visitor Meta keys optional and session-only. Rationale given:
the judge/demo audience is small and invitation-like, an identity provider would
add an unrelated account dependency and personal-data processor to a prototype,
and a per-visit secret can be rotated by changing one server setting.

## Alternatives considered

- External identity provider (GitHub/Google OAuth). Real per-person identity and
  revocation, but adds a new external processor, client registration, redirect
  handling and account dependencies for a synthetic demo. Rejected for this scope.
- Per-visitor invitation codes with persisted records. Better attribution and
  revocation granularity, but introduces a persistent credential schema, issuance
  flow and storage of secrets that the accepted session-only model avoids.
- No login, random-workspace cookie. Lowest effort, but any internet client could
  consume host Modal compute and workspace files. Rejected: admission must be
  explicit.
- Shared access code exchanged for an opaque session cookie (chosen). Small,
  provider-neutral, rotatable, and compatible with a later per-visitor upgrade.

## Accepted implementation

- `OPERATOR_VISITOR_CODE` must be 16-256 printable ASCII characters. Comparison
  uses `secrets.compare_digest`; refusal never reveals which part was wrong.
- `POST /api/session` exchanges the code for a signed, expiry-bound session token
  set as the `operator_session` cookie(HttpOnly, SameSite=Strict, Secure when an
  HTTPS origin is configured, `Max-Age` from the session lifetime).
- The token carries the server-generated subject, expiry and a random nonce, and is
  signed with HMAC-SHA256 under `OPERATOR_SESSION_SECRET` (at least 32 bytes). No
  session record or credential is persisted. Canonical base64url encoding is
  required, so a re-encoded signature cannot evade revocation keyed on the token.
- With a stable secret a browser resumes the same subject after a container restart
  and reaches its committed task store(0005). Without one, sessions end with the
  process; signing still prevents forgery.
- The subject is generated server-side(`visitor-` plus random hex). Browsers never
  supply a subject, workspace or task-store path.
- Non-GET hosted routes additionally require `X-Operator-Request: 1`, which page
  script cannot add cross-origin without a CORS preflight. Existing exact-origin
  checking remains.
- Sessions are keyless by default and run the labelled scripted reference. A
  visitor key can be attached, replaced, or detached at any time; the hosted app only
  selects the Meta actor when a grant is configured and a key is attached to that
  session. A missing, expired or dropped key never falls back to owner funds.
- `DELETE /api/session` revokes the token for the current process and clears the
  cookie. Key slots are created only when a visitor attaches a key, so capacity
  bounds attached credentials rather than visitor count.

## Consequences and remaining limits

- Any holder of the code can create sessions; there is no per-person identity,
  attribution or selective revocation. Rotating the code invalidates new logins,
  not already-issued sessions; restarting or clearing sessions is the hard reset.
- The access code must be delivered out-of-band (never in the public repository or
  URL). Delivery mechanism for judges is still unresolved.
- Failed-attempt throttling is not implemented; the accepted code length is the
  primary brute-force defense. Add throttling if the endpoint is exposed to broad
  traffic.
- Logout is process-local. A copied or retained signed token stays cryptographically
  valid until it expires after a process restart; there is no server-side revocation
  store. Rotating `OPERATOR_SESSION_SECRET` invalidates every session (and keeps task
  state). This is accepted for an invitation-like demo, not for durable revocation.
- Anyone who learns the signing secret can mint sessions. Treat it as a private
  deployment secret with the same care as the access code.
- Durable task/allowance recovery across cold starts is accepted in 0005. It relies on
  a single container and a stable signing secret; it is not distributed-state or
  multi-writer safety.
