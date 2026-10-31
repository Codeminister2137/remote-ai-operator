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