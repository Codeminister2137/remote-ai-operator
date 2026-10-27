# Remaining release checklist

The local review package is not a submission. Keep private runtime evidence out of
public source. Use this checklist after the current implementation freeze.

- [x] Standalone source and uv lockfile; no private runtime dependency.
- [x] Apache-2.0 decision, LICENSE/NOTICE and wheel metadata.
- [x] Real Meta coding and integrated HTTPS browser evidence, with automated approval labelled.
- [x] Actual phone scripted workflow and current UI controls confirmed by owner.
- [x] Independent source installation, protected Docker checks and source/asset packaging.
- [x] Event-built contribution map, reviewer instructions and three-minute storyboard.
- [x] Fresh physical-phone Meta task, owner review/approval and applied sample; owner confirmed, server receipts verified.
- [x] Bounded live Modal Sandbox runner acceptance on the real runtime; private receipts retained.
- [x] Hosted judge demo deployed and live-verified at `https://kubajaworski999--hosted.us-east.modal.run`
      (scripted mode; access/isolation/CSRF, exact approval, cold resume, idle
      scale-to-zero and a real-browser UI run). Access code and signing secret stay
      outside both repositories.
- [ ] Record and review three-minute video; identify event-built features explicitly.
- [ ] Decide judge access delivery: the hosted app is live and the access code exists;
      only out-of-band delivery to judges is open (submission text or event channel
      assumed; organizers have not answered). Do not publish the owner token or invite
      judges into the private device network.
- [ ] Optional: enable the small finite judge Meta grant and rerun one hosted task with
      a visitor key before judging.
- [x] 2026-10-27 artifact check: wheel/sdist built from the project through the
      `MANIFEST.in` allowlist; the wheel holds the operator package plus HTML/JS/CSS and
      the sdist holds sample, protected checks, scripts, docs, `uv.lock` and
      `.python-version` with no runtime, venv, handoff or secret path; a clean venv
      installed the wheel from fresh resolved dependencies and passed 139 tests plus the
      scripted demo, Docker validation and exact approval; the extracted sdist suite
      passed 139 tests.
- [x] 2026-10-27 locked uv install: from the extracted sdist, `uv sync --locked` and
      `uv run --locked` resolved the shipped `uv.lock`, installed the runtime plus `dev`
      group, the CLI entry point ran and 139 tests passed with the 8 expected Docker skips.
- [x] 2026-10-27 public-tree scan: 67 candidate files, no absolute local paths, owner
      name, email, Modal app id, provider-key patterns, `Bearer` values or private
      `.remote-operator` references; `uv.lock` matches the declared runtime, `modal`
      extra and `dev` group.
- [ ] Review reachable Git history before publishing: the repository has no commits
      yet, so the first public history must be created from the reviewed tree only.
      Exclude private configuration, nonces, accounts, plans, raw receipts and personal
      screenshots.
- [ ] Owner authorizes actual public repository creation/push and submission.
- [ ] Check final event instructions and submit ahead of November 4,21:00 US Eastern
      (November 5,02:00 UTC). Target November 4,18:00 Budapest for margin.

Optional work after the core rehearsal: phone allowance editing with explicit safe
adjustment semantics, receipt formatting, and an additional provider only with an
approved route/credit grant. PC wake, desktop control, private-product integration,
notifications and arbitrary repositories are outside the minimum submission.
