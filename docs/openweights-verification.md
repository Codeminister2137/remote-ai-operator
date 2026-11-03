# Open-weights verification: Muse Spark Open via Baseten

Bonus lane: **Best Build on Meta Open Weight Models**. This is the single
judge-visible inference path that runs on an eligible Meta open-weight model
through partner inference. No live paid calls were made while building it;
every check below the live gate uses stubbed transport.

## Eligible model and endpoint

- Model (exact, from the hackathon guide): `meta-models/muse-spark-open-1026`
- NOT the docs default `meta-models/muse-spark-open` — the grant check refuses
  anything but the exact event id (`BasetenGrant.check`).
- Endpoint: `https://inference.baseten.co/v1/chat/completions`
  (Baseten Model APIs, OpenAI-compatible Chat Completions, non-streaming).
- Credential: event key `BASETEN_HACKATHON_API_KEY` only. A personal
  `BASETEN_API_KEY` is never read and never substituted.

## What runs where

- `src/remote_operator/baseten.py` — `BasetenModel` actor: system prompt plus
  the registered five tools (`read_file`, `write_file`, `validate`, `finish`,
  `ask_owner`), finite-grant admission, durable allowance via the existing
  `Store.reserve_allowance` ledger, and per-request receipts. Provider
  reasoning fields are never read or retained; only `content`, `tool_calls`
  and `usage` are used.
- `src/remote_operator/cli.py` — `--model-mode baseten` for `demo`, `serve`
  and `supervisor-drain`. Requires `--allow-live-inference` and a verified
  finite `--grant`; supervised runs therefore execute on Spark Open through
  the same drain worker as every other mode.
- `src/remote_operator/web.py` + `app.js` — `/api/config` reports
  `{"mode": "baseten", "live_inference": true}`; the UI banner reads
  "Muse Spark Open · live AI answers (Baseten)" and the allowance panel shows
  the finite grant. No key can activate this path: scripted stays default.
- Receipts: every inference writes an `inference_receipt` task event with
  `provider`, `endpoint`, `model`, `grant_id`, `reported_model` (echoed from
  the provider response), `finish_reason`, `attempts` (1–3; protocol and
  output-limit failures retry inside one reservation) and `usage`. Read them at
  `GET /api/tasks/{id}/events` or from the task store.

## How Baseten confirms the eligible model

1. **Request logs.** In the Baseten dashboard, each request from a bonus run
   appears under Model APIs with model `meta-models/muse-spark-open-1026`.
   The checkout sends no other model string on this route: the payload model
   is the `MODEL` constant and request-level overrides do not exist.
2. **Receipt echo.** `reported_model` in each `inference_receipt` is the
   provider's own `model` field from its response, so the stored receipt and
   the dashboard agree without trusting client configuration.
3. **Pre-request pinning.** The grant file names the exact model, `grant.check()`
   runs before any reservation, and the allowance ledger fingerprints the
   whole grant — a swapped model voids the fingerprint instead of billing.
4. **Key separation.** Only `BASETEN_HACKATHON_API_KEY` is accepted, so spend
   (if any) lands on the event grant, never on a personal key.

## Single live gate (one command, run by the owner)

Prerequisites: event key exported, Docker Desktop running, grant file written.
Grant template (fill values, keep the model byte-exact):

```json
{
  "grant_id": "OWNER-APPROVED-ID",
  "model": "meta-models/muse-spark-open-1026",
  "expires_at": 1794000000,
  "max_requests": 12,
  "max_input_bytes": 100000,
  "max_output_tokens": 8192,
  "approved_usd": "2.50",
  "input_usd_per_million": "1.00",
  "output_usd_per_million": "3.00",
  "credits_verified": true,
  "local_spending_limit_accepted": true
}
```

Verify the dashboard shows remaining event credits first, then from the
project checkout run exactly:

```powershell
$env:BASETEN_HACKATHON_API_KEY = "..."
uv run --locked remote-ai-operator demo --model-mode baseten `
  --grant .runtime/baseten-grant.json --allow-live-inference
```

Expected: exit 0 with the task at `awaiting_approval`, plus one
`inference_receipt` per model call recording the model and endpoint above.
Capture the demo JSON, the task events, and a Baseten dashboard screenshot
showing the model id for the bonus evidence pack.

Note: `scripts/check_submission_eligibility.py` still enforces the Meta-only
rule for the main track and intentionally does not accept `baseten`; the
bonus lane is verified by this document instead.

## Offline evidence (no live calls)

- `tests/test_baseten.py` — 26 tests, all transport stubbed with
  `httpx.MockTransport`: exact model/endpoint/tool payload, receipt fields,
  grant pinning (including refusal of the docs-default id), allowance
  exhaustion without resending, HTTP error mapping, credential redaction and
  echo refusal, unknown-tool refusal, reasoning non-retention, usage
  validation, CLI activation gates, receipt persistence through `Operator.run`,
  and `/api/config` liveness for `baseten` mode.
- `ruff check` and the full sibling `pytest` suite pass from this worktree;
  `node --check` passes for `src/remote_operator/app.js`.

## Spend exposure

$0. This branch made zero live inference calls: construction paths never
touch the network (asserted with a patched `httpx.Client`), and all response
handling is covered by fixtures. The only spend is the owner's single live
gate above, bounded by the finite grant and the local allowance ledger.

## Verified live gate — 2026-11-03

Owner-approved run (`demo --model-mode baseten`, isolated runtime, finite
grant $2.50 / 12 requests, event credits):

- `reported_model` echoed `meta-models/muse-spark-open-1026` on all 4
  receipts; every finish reason `tool_calls`; no retries needed.
- Task reached `awaiting_approval` in 5 calls; Docker validation passed
  (protected acceptance tests, exit 0) on a real model-written implementation.
- Tokens 8,496 in / 10,068 out (~$0.04 at $1/$3 per M; whole run including
  diagnostic probes ~$0.08 of the $5 allowance).
- Live hardening from this run: parallel tool calls are served from a
  history-bound queue (the model ignores `parallel_tool_calls: false`),
  protocol/output-limit failures retry inside one reservation, and
  `read_file` serves `sample-task.md` read-only. Template uses
  `max_output_tokens` 8192 — reasoning shares the output budget and 4096
  stalls the write step.

## Unverified items

- Baseten dashboard confirmation of the model id per request.
- Judge walkthrough of the served UI in `baseten` mode.
- Provider-reported spend/balance: receipts record estimates only; remaining
  event credit is visible on the owner dashboard alone (the event key is
  HTTP 403 on the billing API).
