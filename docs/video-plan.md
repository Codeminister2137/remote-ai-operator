# Video plan — 3-minute submission recording (owner step-by-step)

Due with the project by **Wed Nov 4, 9:00 PM ET** (Thu 03:00 CET).
The demo including its video is **25% of the score** (impact 30%, creative
Meta-tool use 25%, technical depth 20%). Source: event rules coverage;
see `docs/x-sharing-analysis.md` for dates and prize split.

Ground rules (from `docs/submission-draft.md`, binding):

- Fresh task for a new live recording; label any replay of saved acceptance.
- Keep Meta mode, injected-failure labels, actual diff, passed summary,
  physical review/approval and applied result visible.
- Crop Bearer tokens, API keys, live approval nonces, private hostnames,
  personal notifications and private runtime logs.
- Make time-cut edits visible; never rearrange effects.
- Do not present automated developer acceptance as a physical button press.

## UI vocabulary (current — use these words on camera)

- **Build view** (not "demo"): create and run tasks.
- **Review view**: approve/reject the exact candidate change.
- **Supervise view**: chat threads, run settings, keys, judge grant.
- **Activity view**: worker pill detail, window, agents, drain.
- Approval applies to a **separate local practice file**; it never
  pushes or deploys.
- The tour's "practice file / real action" split: the Build view is the
  real action surface; samples and the offline reference are practice.

## Setup checklist (before recording)

1. PC: serves running (`day_cycle open` state: :8765 + :8766), Docker up,
   phone on the approved private HTTPS transport.
2. Phone: charged, Do-Not-Disturb on, Chrome open on the operator URL,
   desktop notifications off or cropped.
3. Server: Meta mode confirmed, finite grant visible with remaining
   allowance and expiry; allowance panel on screen at task creation.
4. Privacy sweep: no Bearer/key/nonce visible; no personal tabs in frame.
5. Project scene: serve started with `--work-root <repo>\demo-repo`
   (or pick it in the installer); reset `demo-repo/notes.txt` to one line,
   pre-run the project task to awaiting-approval off camera — the shot
   shows review → approve → changed file (a real approval, no fake press).
6. Rehearse once in scripted mode (no credit); keep its banner visible
   during rehearsal, then switch to Meta for the take.

## 180-second shot list (adapted from DEMO.md to current UI)

| Time | Show | Say / establish |
| --- | --- | --- |
| 0–20 | App title; Build view; allowance panel | "Remote AI Operator lets me supervise a coding task from my phone. This controller, Meta adapter, recovery, evidence and approval UI were built during the event." |
| 20–40 | New task, request text, Meta mode, inject-failure checked, Create and run | "Add CSV import: accept valid rows, report missing fields and duplicates. Meta implements; the controller limits tools and spending. The injected failure is deliberate." |
| 40–80 | Tool events arriving; worker pill Working; candidate diff in Review | "Meta inspects the source and spec, edits the importer, requests tests. Generated code runs in a network-disabled container without model credentials." |
| 80–105 | Injected-failure event, retained edit, recovery, resume to review | "The interruption lands after a saved edit. Recovery keeps that edit and continues without repeating the mutation." |
| 105–135 | Passed summary, exact diff, receipt details briefly | "Host-observed checks of the candidate. Approval binds these source bytes, the base and the validation policy; changed state needs fresh review." |
| 135–165 | Physical approve press, applied importer run, 2 accepted / 2 errors | "I approve this exact local change. The applied code imports valid rows and reports the duplicate and missing title." |
| 165–175 | Work-on picker → demo-repo project task; staged diff; approve; changed file on disk | "Same approvals, real repository: the agent stages changes under my folder, I review the diff, approve, and the file updates. No automated checks here — I am the reviewer." |
| 175–180 | Repo + limitations slate | "Public source runs independently. Sample task plus real-repository development with staged approvals; no PC wake." |

## Landmines (from the Nov 3 UI review — avoid on camera)

- Do not call the Build view a demo; judges confuse it with this video.
- The Workbench section is hidden for the event; do not narrate it.
- Supervision/run-mode scope lives in tooltips; do not linger on settings.
- If the pill shows Working with no visible activity, show its hover
  reason instead of guessing.
- Skip the tour on camera unless time allows; its practice-file wording
  needs context the video lacks.

## After recording

1. Review the cut against the ground rules above (fresh task? labels?
   crops? no fake presses?).
2. Keep the unedited capture + receipts privately (never in the repo).
3. Upload only after explicit owner approval; paste the URL into
   `docs/submission-draft.md` (Video URL line) and the event form.
