# Submission draft - review before entering the event form

## Project name

Remote AI Operator

## Problem statement

Build for Your World: personal tooling the author actually uses. This entry
started as the author's own need — supervising real coding work from a phone
while away from the PC — and the hosted demo, phone UI and agentic loop below
are the working result, not a mock-up.

## Short description

Supervise real Meta-powered coding from a phone, with controlled tools, retained
progress through interruptions, independent tests and approval of an exact change.

## Problem and impact

Useful coding tasks often need attention while the developer is away from a PC.
A remote agent needs more than chat: visible effects, controlled execution, recovery
that avoids repeating mutations, and a clear human decision before application.
This standalone prototype demonstrates that complete supervised loop.

## The agentic loop (entry narrative)

Four stages, each visible in the served UI and each human-gated:

1. **Supervisor plans** — the owner chats with the model in a planning
   thread (Supervise view); the model drafts, the owner refines, then
   **Write plan** renders a reviewable plan artifact.
2. **Owner approves** — the exact plan bytes (or the exact code diff at
   task level) are reviewed with host-observed evidence and approved with
   a single-use, expiring nonce. Changed bytes invalidate approval.
3. **Drain executes** — the bounded drain worker (`supervisor-drain`,
   also running inside `serve` and as the hosted multi-subject sweeper)
   reconciles interrupted jobs without replay and runs queued ones
   oldest-first, stopping again at the human approval boundary.
4. **Inbox notifies** — the owner is pulled back only when needed: the
   🔔 inbox bell jumps to parked owner questions and run outcomes;
   unattended/attended mail receipts cover terminal job events.

The loop composes: a plan approval enqueues a run, the run stops at code
approval, the approved code runs as the visible feature outcome.

## What we built during the event

A new independent Python application: Meta Responses tool adapter, scoped coding
controller, durable task/event and allowance ledger, isolated Docker validation,
interrupted-effect reconciliation, exact-source approval, a mobile browser UI and a
single-writer durable hosted deployment on Modal. A small issue-tracker CSV import
provides a visible feature outcome. Existing
private-product implementation is not included or required. Dependencies retain
their own licenses; the original repository is Apache-2.0. See BUILD_LOG.md and DEMO.md.

This entry was built with Muse Code: the agentic coding assistant authored,
tested and iterated the implementation under the author's direction, with every
change reviewed by the author before landing.

## Provenance: event-built vs prior

Every major feature, labelled. Only event-built work is part of the judged
runtime; prior work is excluded infrastructure the demo neither needs nor shows
to judges.

| Feature | Provenance | Notes |
| --- | --- | --- |
| Meta Responses tool adapter, scoped controller | Event-built (Nov 2026) | `meta.py`, `control.py`; BUILD_LOG-dated |
| Task/event store, allowance ledger, reconciliation | Event-built (Nov 2026) | Durable receipts, exact-source approval |
| Isolated validation (Docker + Modal Sandbox) | Event-built (Nov 2026) | Live-accepted on both runners |
| Served UI: wizard, chat, review, split diff, evidence | Event-built (Nov 2026) | `app.js` / `index.html`, incl. loop3 branches |
| Inbox bell + owner-question parking | Event-built (Nov 2026) | `ask_owner` op, bell jump-to-unread |
| Supervisor threads, plans, drain worker, hosted sweeper | Event-built (Nov 2026) | `supervisor.py`, `supervisor_runner.py` |
| Dictation (Meta STT + Web Speech fallback) | Event-built (Nov 2026) | `stt.py`; audio never persisted |
| PL→EN server-side normalization | Event-built (Nov 2026) | `translate.py`; source preserved |
| Open-weights bonus lane (Baseten Muse Spark Open) | Event-built (Nov 2026) | `--model-mode baseten`; exact-model grant; see `docs/openweights-verification.md` |
| Judge accounts, self-grants, hosted sessions, Modal deploy | Event-built (Nov 2026) | ADR-0004/0005, single-writer volume |
| AI Workbench predecessor product | Prior — excluded | Private product; not in this tree, not required |
| Pending-actions bridge (`external_actions.py`) | Prior surface — hidden from judges | Neutral contract for Workbench-side callers; judge sessions hide the section |
| Window/agent activity file (`window-status.json`) | Prior producer — hidden from judges | Reader is event-built; the producing agent is external; judge sessions hide the section |

## Meaningful use of Meta tooling

The submission's runtime must call only Meta models. Remote Operator offers
scripted mode with no model call, or Meta mode with a validated `muse-*` grant
and a fixed Meta Responses endpoint. See [runtime eligibility](submission-eligibility.md)
for offline checks and the separate deployment/video review required before submission.

muse-spark-1.3 inspects the registered source, authors the implementation and calls
protected validation tools. The adapter uses structured Responses function tools
and transient encrypted continuation. A cold continuation uses recorded visible
history honestly; private reasoning is not persisted. The host enforces tool scope,
spending admission, actual-effect receipts and approval independently of the model.
Meta also authored narrow application development changes, identified in BUILD_LOG.

## Working demonstration

A user submits the importer task from a phone. Meta writes the importer; the host
injects a clearly labelled interruption after a saved edit. Recovery keeps the
completed edit. Seven independent protected tests verify behaviour. The user
reviews the exact diff and approves application to a separate local demo target.
Executing that applied code accepts two valid issues and reports duplicate/missing
field errors. A fresh physical-phone run is owner-confirmed and its saved effects
are independently checked. The required three-minute video remains to be recorded.

## Technical depth

Persisted pending effects and explicit reconciliation prevent blind mutation
replay. Approval binds candidate/base bytes, validation evidence, runner policy and
expiry. Generated code runs in a non-root, network-disabled, read-only container
with resource bounds and both host and container-owned deadlines. Durable local
reservations precede paid requests and retain failed-call costs. The phone can
cancel, review, select saved tasks and inspect interrupted state without automatic
approval. This is a prototype containment boundary, not a general security proof.

## Reproducibility and judge access

Complete runnable source, lockfile, sample project, protected tests and configuration
examples are prepared. An offline scripted mode exercises control flow without
provider credits and is visibly labelled; it is not evidence of Meta authorship.
Live use requires an authorized Meta key, verified credits and a finite private grant.
Follow docs/reviewer-guide.md, or the 5-minute path in docs/judge-card.md
(code → wizard → sample → approve → try-it).

A hosted scripted demo is deployed at
`https://kubajaworski999--hosted.us-east.modal.run` and is live-verified (access
code, per-visitor isolation, scripted run, exact approval, cold-start recovery and a
real-browser UI pass). Judges receive the shared access code out-of-band; it is never
committed. The hosted app starts on the labelled scripted reference at zero inference
cost and has no access to the owner's PC or private source. A visitor may attach their
own Meta key only when a finite judge grant is enabled; until then the key endpoint
refuses. Existing published repository:
`https://github.com/Codeminister2137/remote-ai-operator/tree/main`.
The current local review candidate has not been published. Video URL remains
pending explicit recording/upload approval and an actual reviewed recording.
The owner's private phone network is not a shared
judge endpoint.

## Scope and honest limits

One registered source file; no arbitrary private-repository execution. Same-route
recovery is demonstrated; cross-provider fallback is not implemented. No PC wake,
full desktop control, private-product integration or provider-enforced billing cap
is claimed. Spending is judge-funded through a self-grant (Max requests +
Approved USD, defaults 10 / $2.50); there is no provider-enforced billing
cap beyond the grant itself.

## Bonus category

Best Agentic Use-Case: the model chooses controlled coding actions, observes tool
results and produces a tested feature under human supervision. Request consideration;
do not claim eligibility or an award before organizer/judge confirmation.

## Recording preparation

Use DEMO.md's 180-second storyboard. Keep Meta mode and injected-failure labels
visible; show actual diff, passed summary, physical review/approval and applied result.
Use a fresh task for a new live recording; label any replay of the saved acceptance.
Crop bearer credentials, private hostnames, personal notifications and live nonces.
Keep unedited evidence privately. Visibly shorten waits without rearranging effects.
Do not present automated developer acceptance as a physical human button press.
