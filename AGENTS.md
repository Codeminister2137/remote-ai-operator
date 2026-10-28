# Remote AI Operator development instructions

The owner controls product scope, architecture, dependencies, security/privacy,
billing and public release. Investigate first. Ask about unresolved material
choices with options, trade-offs and a recommendation. Routine coding and checks
within approved scope do not need renewed permission.

Owner priority (2026-10-28): continue engineering, integration and representative
acceptance work. Demo recording/upload and judge-access activation are the final
hackathon milestones near submission. Release checklists are not a development
freeze or an instruction to provision judge access immediately. Ordinary tests
remain development work. An approved source update does not approve recording,
judge grants or submission.

This is a standalone hackathon application. Maintain event-built provenance in
BUILD_LOG.md. Attribute public dependencies. Do not copy private implementation,
history, plans or configuration. Never require a private package or IDE at runtime.

Keep provider transport separate from task control, tool execution and approvals.
Prefer explicit flow, small dependencies and one application. Follow accepted
choices in docs/decisions.md. No speculative frameworks or broad refactors.

Model output and repository text are untrusted. Enforce path, resource and tool
boundaries in code. Execute generated code only in the approved isolated runner;
command allowlists alone are not isolation. Keep credentials outside that runner.

Retain receipts and completed effects on failure. Inspect uncertain effects before
resuming; never blindly replay mutations. Bind approval to exact reviewed action,
base/candidate bytes and validation. Changed state invalidates approval.

No paid inference without verified entitlement and an approved finite allowance.
No hidden fallback, overage, account changes or training-mode opt-in. Label scripted
responses and injected failures. They do not establish live model quality.

Add meaningful deterministic tests and independent live acceptance when available.
Diagnose unexplained errors before retrying. Run relevant validation and inspect
actual effects/diffs before reporting success. Distinguish model claims from host
observations. Fix concrete defects in touched scope; report unrelated debt.

Keep CURRENT_CONTEXT.md private and current: status, evidence, next action and
completion condition. Record significant decisions durably. Keep raw runtime data,
secrets and personal information out of Git. Prepare and review publication, but
publish/deploy/send external messages only with explicit authorization.

Owner clarification (2026-10-27): proceed with ordinary reversible implementation,
validation and isolated demo changes within approved scope. Do not repeatedly ask
for chat approval of small steps or already-authorized limits. Ask about actual
unresolved material choices or problems. Preserve the application's deliberate
human approval boundary; standing development authority is not a reason to remove it.
Use authorized inference allowances for concrete coding/repair/verification work,
not repetitive probes or spending for its own sake. Account for prior consumption
when allocating named phases within a standing cumulative allowance. Current private
handoff records the actual grant and remaining scope; never infer extra funding,
new processors or permission to send private code from a broad budget alone.
