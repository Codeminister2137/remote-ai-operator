# Judge quick-start card — Remote AI Operator

5 minutes, hosted demo, no install. Everything below runs at the deployed
judge URL; the access code is supplied separately and never committed.

> Live URL: `https://kubajaworski999--hosted.us-east.modal.run`
> (scripted reference verified live; judge-funded Meta mode needs your key —
> see minute 2 below).

## The 5-minute path

### 0:00–1:00 · Code

Clone or browse the standalone tree (Apache-2.0, no private packages):

- Entry points: `src/remote_operator/cli.py` (`serve`, `serve --hosted`,
  `demo`, `supervisor-drain`), `modal_app.py` (hosted deploy).
- The loop in four files: `supervisor.py` (plans), `control.py` + `web.py`
  (human approval), `supervisor_runner.py` (drain executes),
  `hosted.py` + `app.js` inbox bell (notifies).
- Provenance: `BUILD_LOG.md` (dated event work),
  [submission draft](submission-draft.md) (event-built vs prior labels).

### 1:00–2:30 · Wizard

Open the hosted URL. The **Setup wizard — 4 steps** tracks:

1. **Code** — enter the demo access code, open your private session.
2. **Account** — register your judge account, then log in
   (username 3–32 chars, password 8+; inline hints validate as you type).
3. **Limits** — save spending limits (defaults: 10 requests, $2.50).
4. **Key or scripted** — attach your Meta key for real AI, or
   **Continue without a key** for the free offline reference.

Each step has a **Go to …** button that opens the Access panel and focuses
the right field. The Access panel collapses once connected.

No-key path stays fully usable: scripted reference, sample review,
approval, CSV try-it — zero cost, zero model calls.

### 2:30–3:30 · Sample

In the Demo view, step 2, press **Load sample review**. It reuses one of
your validated saved tasks when available; on a cold start it creates and
runs the sample task for you (first run validates, takes a minute or two).
You land in the Review view with a real diff and real validation evidence.

### 3:30–4:30 · Approve

In the Review view:

- **Split diff** (Split: old vs new / Unified toggle) — the exact candidate
  change.
- **Evidence card** — `PASS n/7 checks · runner · duration · exit code`;
  raw receipt stays one click away under “Show validation receipt”.
- **Approval checklist** — PASS/FAIL rows binding candidate bytes, base,
  validation evidence, runner policy and expiry.
- **Review window chip** (footer) — live countdown to approval expiry.
- Press **Approve this verified change**, or **Run fresh validation** if
  the window expired, **Reject change** to refuse.

Nothing is pushed or deployed: approval applies one reviewed source file
to the separate local demo target only.

### 4:30–5:00 · Try it

Press **Try it in Demo step 3** (or scroll to Demo step 3 yourself).
**Run approved import** executes the applied importer on the sample CSV:
2 accepted issues, 2 row errors (duplicate ID + missing title). The
**sample gallery** offers one-click inputs — Happy path (3/0), Edge cases
(1/4), All invalid (0/4) — each labelled with expected counts.

## What to look at (reviewer checklist)

- Exact-change approval: approve binds candidate digest, base digest,
  validation evidence, runner policy and a 10-minute expiry. Change any
  input and the approval is refused — try editing state, then approving.
- Host-observed evidence: 7 protected checks run in an isolated runner
  (Docker locally, Modal Sandbox hosted); the model never touches the
  verdict.
- Recovery without replay: enable “Inject one transport failure”, run,
  and watch recovery retain the saved edit instead of repeating it.
- Isolation: a second visitor session (private window + same access code)
  sees an empty task list and gets 404 for your task IDs.
- The loop, end to end: Supervise view — thread, chat, write plan, run
  settings, approve plan — then the drain runs it and the 🔔 **Inbox bell**
  jumps you to parked questions and run outcomes.
- Judge sessions hide owner-only surfaces (Worker & machine, Workbench
  actions). The footer build chip names the serving commit.

## Where keys and grants live

| Secret / limit | Where | Notes |
| --- | --- | --- |
| Demo access code | Supplied out-of-band; entered once | Never in the repo; opens a signed session cookie |
| Judge password | `visitors/judge_accounts.sqlite3` (salted hash) | Stable `judge-<name>` subject keeps your tasks |
| Meta API key | “Your model key”, session-only | Never persisted, never identity, detach anytime |
| Spending limits | Judge grant (Max requests + Approved USD) | You set them; you pay; owner pays nothing |
| Model | Server-pinned `OPERATOR_JUDGE_MODEL` | Judges cannot change the model |
| Owner grant | Private server config (not deployed for judges) | Would take precedence if ever enabled |

Scripted mode needs none of this: no key, no grant, no spend.

## Screenshots

> PLACEHOLDER (parent): no headless browser in this environment
> (`chromium` absent), so screenshots were not captured here.
> Suggested captures from the hosted URL at phone width (390px):
> (1) setup wizard at step 3 with inline limit hints;
> (2) Review view with split diff + evidence card + approval checklist;
> (3) Demo step 3 after “Run approved import” (2 accepted / 2 errors);
> (4) Supervise chat with bubbles + 🔔 inbox badge;
> (5) dictation button naming the Meta backend in Meta mode.

## Loop3 branch map (merge status for the parent)

UI described above spans unmerged `loop3/*` branches on top of `main`
(`44244e3`): `onboard` (wizard, sample review, CSV gallery),
`bellchat` (inbox bell, chat bubbles, typing indicator),
`reviewloop` (evidence card, window/build chips, humanized statuses,
judge visibility, try-it shortcut), `h1a` (hosted multi-subject drain
sweeper, `waiting_on_key`), `stt` (Meta transcription backend with
auto-slice dictation, Web Speech fallback), `translate` (server-side
PL→EN normalization at ingestion), `openweights` (Baseten model-mode on
Muse Spark Open for the open-weights bonus, exact-model grant, key
separation — see docs/openweights-verification.md).
Merge all before pointing judges at this card.
