# Meta transport and live admission

The prepared adapter uses direct Meta Responses API function tools, not a chatbot
wrapper. Meta chooses source reads/edits, interprets isolated test feedback and can
repair within the same bounded task. Host code enforces permissions, validation,
cancellation and exact-action approval. Never accept model narration as proof.

## Current verification

HTTPX MockTransport tests cover function calls, usage, errors, encrypted continuation,
credit admission and controller integration. Mock tests do not establish real-model quality.
The first live coding trial authenticated and read source, then paused when its
next response hit the 4096 output-token cap. No edit or validation occurred;
A later approved repair phase produced a Meta-authored candidate passing all 7 protected
Docker checks. Owner-approved application and applied-target sandbox execution
also passed; see live-acceptance.md.
Scripted mode is the default and its actor
installs a known reference. It must stay visibly labelled in demonstrations.

## Before live use

Obtain actual credits, approved model/route, current prices and a finite owner
allowance. Verify available credit and expiry; review attached payment methods and
possible charges beyond credits. Meta offers spending alerts, not a provider hard
cap. An owner must explicitly accept local-only spending controls.
The included grant.example.json is deliberately invalid and cannot activate calls.
Copy it into private .runtime/grant.json, then fill it only with verified owner
choices. Expiry is a Unix UTC timestamp. Use a fresh grant ID for a genuinely new
allowance; changing a consumed grant's limits does not reset it. Do not create a
new grant merely to retry a failed or exhausted task.

Keep META_API_KEY in the server environment, never in the grant, browser or runner.
A key alone does not enable inference. Both --model-mode meta and
--allow-live-inference are required, in addition to the grant:

```text
uv run --locked remote-ai-operator demo --model-mode meta --grant .runtime/grant.json --allow-live-inference --inject-failure
uv run --locked remote-ai-operator serve --model-mode meta --grant .runtime/grant.json --allow-live-inference
```

The application cannot independently certify the account facts asserted in a grant.
Check those facts before marking them verified. Local cost reservations estimate
input at serialized bytes plus framing headroom and reserve the output cap at the
configured prices. This is conservative admission, not exact billing or a substitute
for a provider cap. Meta has no hard usage cap; alerts do not stop billing.
Failed and timed-out calls remain consumed across
restarts. Provider-reported usage is recorded separately, including on incomplete
responses when available; missing usage stays unknown. No automatic billing/provider
escalation or account privacy changes. Contributor/training-mode models are refused.

## User-configured local spending limits

Set `approved_usd` in the private grant JSON to your chosen USD allowance, and set
`local_spending_limit_accepted` to true only after reviewing these limitations.
The limits are currently configured in that file, not editable from the phone UI.
`max_requests`, input/output bounds and expiry provide additional independent stops.
No arbitrary phone request or model response can raise the server-owned allowance.
The example stays invalid by default. Legacy `provider_spend_cap_verified` grants
are refused; no old boolean is silently reinterpreted as consent.

One grant ID shares a durable reservation ledger across tasks/processes using the
same runtime database. Each request reserves before sending; failed/unknown calls
remain charged against the allowance. Increasing limits or changing settings under
a consumed ID is refused. A new ID or another runtime/database is a separate budget,
not an account-wide cap. Keep one runtime for a shared allowance. Back up its database;
deleting it loses local consumption history. Do not issue a new ID to bypass a limit.

Receipts include cumulative reserved estimates, remaining local allowance and
request count. These are distinct from provider-reported token usage and actual
billing; reservations are not released based on missing/partial usage. Price and
byte-to-token assumptions may differ from billed costs. These controls bound this
application's admitted calls, not other clients or the provider's final invoice.
See [the accepted decision](decisions/0001-local-spending-limits.md).

## State and error behaviour

Requests use store=false, encrypted reasoning replay and one function call per turn.
Encrypted continuation stays in the current actor's memory; it is never included
in task records or receipts. Cold resume reconstructs visible tool pairs and labels
its continuity as observable_history_only. It does not claim preserved reasoning.
No required task/history is trimmed to fit a budget. Context overflow stops.

HTTP redirects are not followed. Authentication, rate-limit, protocol, privacy and
transport failures preserve category/status and sanitized provider detail; they
pause the workflow rather than silently retry or switch processors. Incomplete
output is not executed. Only the explicit injected demo failure automatically
recovers in this initial route policy. Cancellation discards late model actions;
a current synchronous HTTP request may take its bounded timeout to finish.

The initial direct adapter does not establish compatibility with partner endpoints.
When actual grants arrive, inspect the offered Meta model/endpoint, terms, price
and tool capability before choosing a partner adapter. Advertised credits are not
usable grants, and one provider's credits cannot pay another provider's requests.

## References checked 2026-10-26

- https://dev.meta.ai/docs/protocols/responses
- https://dev.meta.ai/docs/tool-calling
- https://dev.meta.ai/docs/pricing-rate-limits

Meta documents stateless encrypted replay for agent loops, function-tool pairing,
parallel_tool_calls=false and tool_choice=auto. Live contract/quality acceptance is
still required; documentation and fixtures alone do not establish account behaviour.

## Output budgets and first live trial

Reasoning tokens and visible output share max_output_tokens. A completed source
read followed by incomplete/max_output_tokens is partial protocol evidence, not a
completed coding task. Never execute a truncated tool call or automatically raise
the owner's cap. Preserve consumed requests and reservations. Future receipts
retain numeric reasoning/cache counts without private reasoning text; counts absent
from an older receipt remain unknown. Explicit effort can tune the trade-off; an
unset effort means provider default, not a known fixed level. Demo CLI returns 2
when it pauses before verified review, while preserving JSON for inspection.

References checked 2026-10-27:
https://dev.meta.ai/docs/reasoning
https://dev.meta.ai/docs/protocols/responses
The initial 8192-output continuation exposed an overly short 20-second client read
timeout. Read timeout is now 60 seconds; the controller still checks its 120-second
workflow deadline before permitting model output to cause effects. A client timeout
does not establish the provider's final result or billing. Unknown usage retains
its full reservation. No automatic live retry or cap increase is implemented.
The timeout-corrected 8192 trial produced source and passed the deliberate injected
interruption, then its full encrypted continuation exceeded the initial 32KiB
serialized-input bound before another request. Subsequent host-only Docker checks
rejected the saved candidate: tuple return format and empty-input behaviour differed
from the public API. The original task instructions omitted the precise return
shape. Instructions and seed documentation now supply it; protected tests and the
original model candidate are unchanged. This is an instruction-harness defect and
a failed candidate, not evidence of completed live coding. Increasing input bounds
or renewing an expired grant requires owner approval; history is never trimmed to
force a fit. Overflow errors now report required/allowed byte counts without content.
## Read-only phone allowance

GET `/api/allowance` requires owner authentication. It reports the frozen server
grant's model, expiry, request count, USD limit and conservative reserved/remaining
estimates. Scripted mode returns enabled=false. Reading absent ledger state does
not create a grant row; reading expired state remains possible. Invalid or changed
ledger fingerprints fail closed. No key, raw prompt or provider account balance is
returned. Configure grants locally and restart deliberately to change allowance;
the browser cannot change billing settings or create a new grant.
