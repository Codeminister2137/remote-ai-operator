# Private phone access preparation

The application binds 127.0.0.1. Keep that binding. An exact HTTPS --public-origin
can be configured for an explicitly approved private reverse proxy. The name does
not open any port or install a tunnel; it only admits that origin's Host/Origin.
Bearer authentication and exact-action approval still apply. Forwarded identity or
Host headers never grant permission. Body limits count streamed bytes as well as
Content-Length, so proxy chunking does not bypass limits or break legitimate input.

Recommended personal deployment is Tailscale Serve, with the PC and phone enrolled
and access policy scoped to this service. Setup needs the owner's device sign-in
and any installer administrator approval. Do not publish via Funnel or a public
proxy as an automatic fallback. Local operation requires neither a VPN nor a private
account. This repository contains no machine-specific tailnet configuration.

After approved installation/sign-in and HTTPS consent, use the actual Serve URL:

```text
uv run --locked remote-ai-operator serve --public-origin https://YOUR-NODE.YOUR-TAILNET.ts.net
```

Set OPERATOR_ACCESS_TOKEN privately before starting. Configure Serve to proxy only
the chosen loopback port. Open the HTTPS URL on the enrolled phone and enter the
owner token. Task IDs can be bookmarked in URL fragments; credentials are never
included there or stored in browser storage. Physical phone, VPN policy and HTTPS
acceptance must be verified before claiming remote readiness.

## Phone UI tour (current)

The served UI is one page with four views (Demo, Supervise, Review,
Activity) behind a view bar. Everything below works at phone width with
no horizontal overflow.

- **Setup wizard.** Hosted sessions show a 4-step wizard (access code →
  account → spending limits → key or scripted) with inline validation,
  per-step status and **Go to …** buttons that focus the right field.
  The Access panel collapses after connecting; owner-token sessions skip
  the wizard and connect directly.
- **🔔 Inbox bell.** In the view bar next to Activity. The badge counts
  unread owner messages and parked owner questions; tapping it polls
  once and jumps to the newest unread item (thread in Supervise, or
  parked task in Review). Opening a thread or task marks its items read.
- **Chat.** Supervisor threads render as bubbles (you right, assistant
  left) with a typing indicator while the assistant replies and
  first-seen timestamps under each turn. Times are stamped by the page
  when first shown — the server keeps no per-message timestamps.
- **Dictation.** Request, chat-message and answer fields offer a 🎤
  Dictate button. In Meta mode with a key attached, clips are recorded
  and transcribed by Meta in ~55 s auto-slices against the shared
  allowance (audio and transcripts are never stored); otherwise the
  browser's own speech recognition fills the field. The button always
  names the backend actually used. Dictation only fills the field — you
  review and press Send. Polish dictation tags the text `pl` so the
  server normalizes it to working English at ingestion (original kept).
- **Split diff.** The Review view toggles Split (old vs new) and Unified
  diff, with an evidence card (`PASS n/7 · runner · duration · exit`),
  a PASS/FAIL approval checklist and a footer chip counting down the
  review window. **Load sample review** (Demo step 2) fetches a
  ready-made review; **Try it in Demo step 3** runs the approved
  importer and jumps to its result table.
- **Judge sessions** hide the owner-only Worker & machine and Pending
  actions from Workbench sections; those are prior-predecessor surfaces
  outside the judged runtime. The footer build chip names the serving
  commit for bug reports.

> PLACEHOLDER (parent): phone-width screenshots not captured here — no
> headless browser in this environment (`chromium` absent). Suggested:
> wizard + bell badge, chat bubbles, dictation row, split diff with
> evidence card. See docs/judge-card.md for the full capture list.

The PC must be awake. Wake/sleep integration is deferred; a sleeping PC needs an
independently powered reachable wake sender. Do not expose router administration,
forward raw wake packets publicly or modify firmware to make this demo work.

No remote service is activated by the setup instructions or repository tests.

## Docker resilience

Task validation runs in a local Docker container. The server starts Docker
Desktop automatically when a validation finds the daemon down (Windows only,
one retry per validation), and Docker Desktop auto-starts at sign-in via the
per-user Run key. If validation still reports Docker unreachable, start Docker
Desktop on the PC and tap Run fresh validation.

## HTTPS certificate consent

Tailscale HTTPS certificates publish the certified machine and tailnet DNS name
in public Certificate Transparency logs. The service remains restricted to the
tailnet. Review or rename a sensitive machine name before requesting a certificate.
Enable HTTPS in the owner-administered DNS settings only after accepting that
publication. See https://tailscale.com/docs/how-to/set-up-https-certificates.
Private HTTP over Tailscale avoids public certificate issuance, but browsers do
not treat it as HTTPS and this application's proxy-origin policy currently requires
HTTPS. It is a separate deployment choice, not an automatic fallback.