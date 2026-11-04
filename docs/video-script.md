# Video script — 180-second submission take (narration + shots)

Companion to `docs/video-plan.md` (setup, ground rules, landmines —
all binding). Read the plan first; this file is only the spoken
lines, timed to what must be visible. Speak at a normal pace; each
block runs ~10–15 seconds. Total: ~180 seconds.

Privacy during the whole take: no Bearer token, API key, approval
nonce, hostname, notification or private log in frame. Crop in OBS
before recording, not after.

## 0:00–0:20 — Title and premise

SHOW: app title, Build view, allowance panel (Meta mode, finite
grant with remaining allowance and expiry visible).

SAY: "Remote AI Operator lets me supervise a coding task from my
phone. The controller, the Meta adapter, recovery, evidence and
this approval UI were all built during the event."

## 0:20–0:45 — New task

SHOW: new task form; type the request; Meta mode; inject-failure
checked; press Create and run.

SAY: "Add a CSV import: accept valid rows, report missing fields
and duplicates. Meta implements it while the controller limits
tools and spending. The injected failure is deliberate — watch
recovery handle it."

## 0:45–1:20 — Run

SHOW: tool events arriving; worker pill Working; then the
candidate diff in the Review view.

SAY: "Meta inspects the source and the spec, edits the importer,
then requests tests. The generated code runs in a
network-disabled container with no model credentials."

## 1:20–1:45 — Interruption and recovery

SHOW: injected-failure event; retained edit; recovery; resume
back to review.

SAY: "The interruption lands after a saved edit. Recovery keeps
that edit and continues without repeating the change."

## 1:45–2:15 — Review

SHOW: passed summary, exact diff, one receipt briefly.

SAY: "Host-observed checks of the candidate. Approval binds
these exact source bytes, the base and the validation policy —
changed state needs fresh review."

## 2:15–2:45 — Approve and apply

SHOW: physical approve press (confirmation dialog); applied
importer run; two accepted rows, two reported errors.

SAY: "I approve this exact local change. The applied code
imports the valid rows and reports the duplicate and the
missing title. Approval touches this practice file only —
it never pushes or deploys."

## 2:45–2:55 — Real repository

SHOW: Work-on picker set to demo-repo; project task already at
awaiting-approval (pre-run off camera); staged diff; approve;
changed notes.txt on disk.

SAY: "Same approvals, real repository: the agent stages changes
under my folder, I review the diff and approve, and the file
updates. No automated checks here — I am the reviewer."

## 2:55–3:00 — Close

SHOW: repository + limitations slate (or README scope block).

SAY: "Public source runs independently: the sample task plus
real-repository development with staged approvals. Thank you."

## Contingencies (say only if true, never fake)

- Run stalls: "The run is still working; I will cut the wait
  here." Make the time cut visible; never rearrange effects.
- Fresh-task rule: this take uses one fresh task per scene. If
  a shot replays saved acceptance, say so on camera: "This
  replays saved acceptance from earlier today."
- If anything fails live: keep the failure in the cut or
  re-record the scene; do not narrate over a different take's
  footage.
