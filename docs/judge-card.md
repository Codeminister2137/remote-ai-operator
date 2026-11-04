# Judge quick-start card — Remote AI Operator

5 minutes, hosted demo, no install. Everything below runs at the deployed
judge URL; the demo access code is public in the README
([Hosted visitor mode](../README.md#hosted-visitor-mode)) — entry is open to
everyone and each visit stays private.

> Live URL: `https://remote-operator--hosted.us-east.modal.run`
> (the offline reference runs without anything from you; real AI mode
> needs your key — see minute 2 below).

## The 5-minute path

### 0:00–1:00 · Code

Clone or browse the standalone tree (Apache-2.0, no private packages):

- Entry points: `src/remote_operator/cli.py` (`serve`, `serve --hosted`,
  `demo`, `supervisor-drain`), `modal_app.py` (hosted deploy).
- The loop in four files: `supervisor.py` (plans), `control.py` + `web.py`
  (human approval), `supervisor_runner.py` (the worker that runs approved
  work), `hosted.py` + `app.js` inbox bell (notifies).
- Provenance: `BUILD_LOG.md` (dated event work),
  [submission draft](submission-draft.md) (event-built vs prior labels).

### 1:00–2:30 · Wizard

Open the hosted URL. The **Setup wizard — 4 steps** tracks:

1. **Code** — enter the demo access code, open your private visit.
2. **Account** — register your judge account, then log in
   (username 3–32 chars, password 8+; inline hints validate as you type).
3. **Limits** — save spending limits (defaults: 10 requests, $2.50).
4. **Key or offline** — attach your Meta key for real AI, or
   **Continue without a key** for the free offline reference.

Each step has a **Go to …** button that opens the Access panel and focuses
the right field. The Access panel collapses once connected.

No-key path stays fully usable: offline reference, sample review,
approval, sample import run — zero cost, zero AI calls.

Scope (both paths): the hosted demo runs the fixed sample task only —
one source file plus its acceptance checks. A custom request still
funnels into that sample; the demo never builds arbitrary projects.
No-key path: the offline reference answers from a fixed script — try
how the system works, zero AI. Key path: real Meta AI works within the
sample. Real-repository development is a self-hosted feature and is
shown in the submission video.

### 2:30–3:30 · Sample

In the Build view, step 2, press **Load sample review**. It reuses one of
your validated saved tasks when available; on a first visit it creates and
runs the sample task for you (first run checks everything, takes a minute
or two). You land in the Review view with a real proposed change and real
check results.

### 3:30–4:30 · Approve

In the Review view:

- **Two views of the exact change** (side-by-side / one-list toggle).
- **Evidence card** — how many of the 7 safety checks passed, where they
  ran, how long they took; the full technical receipt stays one click
  away under “Show validation receipt”.
- **Approval checklist** — rows confirming the exact change, what it was
  checked against, and when the approval expires.
- **Countdown chip** (footer) — time left to approve.
- Press **Approve this verified change**, or **Run fresh validation** if
  the time ran out, **Reject change** to refuse.

Nothing is pushed or deployed: approval applies one reviewed source file
to the separate local demo target only.

### 4:30–5:00 · Try it

Press **Try it in Step 3** (or scroll to Build view step 3 yourself).
**Run approved import** runs the applied importer on the sample rows:
2 accepted issues, 2 row errors (duplicate ID + missing title). The
**sample inputs** offer one-click data — All valid (3 accepted,
0 errors), Tricky rows (1 accepted, 4 errors), All broken (0 accepted,
4 errors) — each labelled with expected counts.

## What to look at (reviewer checklist)

- Exact-change approval: approving locks in the exact change, what it was
  checked against, and a 60-minute deadline. Change anything and the
  approval is refused — try editing, then approving.
- Independent checks: 7 safety checks run in an isolated test box
  (Docker on a PC, Modal Sandbox — the hosted run's isolated test box —
  when hosted; see modal-hosting.md); the AI never grades its own work.
- Safe retries: switch on “Inject one transport failure”, run, and watch
  the retry keep the saved work instead of doing it twice.
- Privacy: a second visitor (private window + same access code)
  sees an empty task list and none of your work.
- The loop, end to end: Supervise view — thread, chat, write plan, run
  settings, approve plan — then the worker runs it and the 🔔 **Inbox bell**
  takes you to waiting questions and run results.
- Judge visits hide owner-only areas (Worker & machine, Workbench
  actions). The footer names the exact served version, for bug reports.

## Where keys and grants live

| Secret / limit | Where | Notes |
| --- | --- | --- |
| Demo access code | Public in the README ([Hosted visitor mode](../README.md#hosted-visitor-mode)); entered once | Open entry; opens your private signed-in visit |
| Judge password | Stored on the server as a salted hash, never in plain text | Your judge name keeps your tasks separate |
| Meta API key | “Your model key”, this visit only | Never saved, never your identity, remove anytime |
| Spending limits | Judge grant (Max requests + Approved USD) | You set them; you pay; owner pays nothing |
| Model | Fixed by the server | Judges cannot change the model |
| Owner grant | Private server setting (judges never see it) | — |

The offline reference needs none of this: no key, no grant, no spend.

## Screenshots

> Screenshots pending capture: they were not taken yet.
> Suggested captures from the hosted URL at phone width (390px):
> (1) setup wizard at step 3 with inline limit hints;
> (2) Review view with side-by-side change + evidence card + approval checklist;
> (3) Build view step 3 after “Run approved import” (2 accepted / 2 errors);
> (4) Supervise chat with bubbles + 🔔 inbox badge;
> (5) dictation button naming which transcription it uses (Meta or browser).

