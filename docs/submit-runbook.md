# Submit-morning runbook (2026-11-04)

Ordered gates for submission day. Stop at any red gate; nothing here
self-authorizes. Recording/upload/submission each need the owner's
explicit OK at execution time.

## 0. Preconditions (from the 20:00 review)

- Owner Meta grant renewed (values from inbox decision 1) or skipped
  (video records scripted + hosted-judge paths only).
- Repo-public timing + judge-delivery channel decided (inbox 3, 4).
- `fix/frontend-copy-20261103` merge decision made.

## 1. Lift the freeze, land final merges

```powershell
git -C C:\Users\Jakub\PycharmProjects\remote-ai-operator switch main
git -C C:\Users\Jakub\PycharmProjects\remote-ai-operator merge --no-ff fix/frontend-copy-20261103
git -C C:\Users\Jakub\PycharmProjects\remote-ai-operator push origin main
```

Gate: merge is static + tests only (no serve behavior change beyond
copy/labels). Skip if the 20:00 review held the branch.

## 2. Restart serves, confirm health

Owner's 2 restart commands, then:

```powershell
(Test-NetConnection 127.0.0.1 -Port 8765).TcpTestSucceeded
(Test-NetConnection 127.0.0.1 -Port 8766).TcpTestSucceeded
```

Gate: both True. If the restart breaks anything, the previous serve
state is the fallback (report, do not improvise).

## 3. Final-tree gates (exact publish candidate)

From `C:\Users\Jakub\PycharmProjects\remote-ai-operator`:

```powershell
.venv/Scripts/python.exe -m ruff check src tests scripts
.venv/Scripts/python.exe -m pytest -q -m "not docker" -p no:cacheprovider
node tests/js_actions.cjs
node --check src/remote_operator/app.js
```

Gate: ruff clean; pytest 576 passed / 9 skipped / 265 subtests (or
better — any NEW failure stops the line); ACTIONS-OK.

## 4. Eligibility on the real files

```powershell
.venv/Scripts/python.exe scripts/check_submission_eligibility.py --env-file C:\Users\Jakub\PycharmProjects\ai-workbench\.env --grant-file .runtime/grant.json --model-mode meta
```

Gate: ELIGIBLE. If the grant was renewed, this is its first live
proof. Known ambient state: empty non-Meta key names in `.env` are
ignored (only set values flag); the demo reads only
META/Baseten/OPERATOR_* variables (code-proven 2026-11-03).

## 5. Secret sweep on the publish candidate

```powershell
git grep -iE "sk-[A-Za-z0-9]{8}|ghp_[A-Za-z0-9]{8}|xox[bap]-|AIza[A-Za-z0-9_-]{8}" origin/main -- .
git ls-files origin/main | Select-String -Pattern "\.env$|grant\.json|smtp|secret|_token|private/|phone/"
```

Gate: no hits except known false positives (e.g. "sk-" inside prose).
Also confirm no private plans or raw trial evidence in the tree.

## 6. Hosted spot check (no inference spend)

- Deployed URL returns 200 with the scripted banner visible.
- Only if the image changed since `4d8671e`: full redeploy +
  phase-one re-verify (see `docs/modal-hosting.md`). Docs-only main
  commits do NOT need a redeploy.

## 7. Repo + judge access (per 20:00 decisions)

- Make the repo public (now/at-submit/keep-private per decision).
- Deliver URL + access code via the decided channel (code already public in README by owner decision 2026-11-03 open entry; no separate delivery needed).

## 8. Video, upload, submit

Follow `docs/video-plan.md` for takes (scripted rehearsal first,
labelled live takes, injected-failure take labelled). Then upload +
submit per `docs/submission-draft.md`, each step on explicit OK.

## 9. Post-submit notes

- X-sharing window opens ~Nov 10 (decision 5 from 20:00).
- Branch consolidation: run
  `docs/plans/branch-consolidation-checklist.md` (workbench-local)
  at pre-polish, not before the video is recorded.
