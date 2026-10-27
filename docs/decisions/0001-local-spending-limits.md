# 0001 - User-configured local inference spending limits

Status: accepted 2026-10-27.

## Context and superseded assumption

The initial admission contract required a verified provider hard spending cap.
Meta's published billing help states its spend limit is an email alert, not a
usage hard cap. Requiring that unavailable feature blocks the direct route, while
setting its verification flag would misrepresent the protection.

## Owner decision and rationale

The owner approved proceeding with local limits because application users should
be able to set their spending limits. Keep server-owned finite grant configuration,
explicit local-control acceptance and durable pre-request reservations. Do not
claim these controls guarantee the provider invoice. The owner's first finite
test allowance is separate from this reusable product policy.

## Alternatives and trade-offs

Local limits permit direct Meta use and let users choose an application allowance.
They depend on correct prices/estimation and the persisted runtime ledger; they
cannot constrain other clients, a new runtime, provider accounting or stolen keys.
Retaining a provider hard-cap requirement gives an independent billing boundary
where supported, but prevents use of this documented direct route. Waiting for a
second provider adds access/dependency work and does not prove its billing controls.

## Contract and consequences

Replace provider_spend_cap_verified with explicit local_spending_limit_accepted.
Legacy grant JSON fails closed and needs deliberate review; no automatic migration.
Credits_verified remains a separate entitlement assertion. Configuration is a
private owner-supplied JSON file; no remote allowance editor is introduced here.

Reservations use exact decimal arithmetic and SQLite BEGIN IMMEDIATE before each
request. Failed calls consume budget; other actors sharing the same database and
grant consume the same allowance. Consumed policy fingerprints cannot be changed.
Receipts expose cumulative reserved estimates separately from reported usage.
No provider fallback, purchases, recharge or training opt-in follows from this
choice. An alert is never described as an enforced provider cap.

Official billing source, checked 2026-10-27:
https://dev.meta.ai/help/billing/spend-limit-alert