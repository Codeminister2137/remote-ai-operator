# Development loop

Foreground controller `run_development_loop` in `src/remote_operator/development.py` plus JSON adapters `JsonRoles` in `src/remote_operator/development_json.py`. Ordinary Python and dataclasses only. No shell, no network, no services.

## Roles and injected boundaries

Host injects every effectful boundary:

- `supervisor(SupervisorView) -> SupervisorDecision`: plans one step. Actions are `task`, `accept`, `stop`, `decision_needed`. Sees objective, snapshot dict, unified diff from baseline, last outcome, last validation output, budget counters, allowlist.
- `implementer(ImplementerView) -> Mapping[str,str]`: returns full UTF-8 text per file. Cannot expand the allowlist. Output is staged, checked, then written as bytes.
- `validator(Mapping[str,bytes]) -> ValidationResult`: trusted host check over exact candidate bytes. Provenance must be `host`. Malformed result stops as `stopped-unknown-validator` with effects preserved.
- `receipt_sink(Receipt)`: trusted observer called for each receipt. Returned FinalState values retain accumulated receipts in memory; the caller supplies durable recording. Exceptions from the sink propagate.
- `should_stop() -> bool`: cooperative stop. Polled before supervisor, after supervisor, before implementer, after implementer, before validator launch after writes, and before accept. Any true value stops with effects preserved. Exceptions from the callback count as stop.

`JsonRoles(send)` adapts both model roles to JSON dicts over one injected `send(role, payload)`. Supervisor responses must contain only `action`, `task`, `targets`, `reason` with strict string types and `targets` as `list[str]`; implementer responses must contain exactly `changes` as `dict[str,str]`. Payload conversion allows None, bool, int, strict str, finite float, UTF-8 bytes, dict with string keys, list and tuple; NaN, infinities, non-UTF-8 bytes, and non-string keys are rejected. Views must be the exact dataclass types.

Model output and repository text are untrusted data. Generated code is never executed by this loop.

## Fixed workspace and allowlist

`workspace` plus `allowlist` of 1 to 8 fixed relative POSIX paths define the whole writable scope. Entries reject empty paths, absolute paths, drive prefixes, backslashes, colons, control characters, and `.`, `..`, or empty segments. Workspace ancestors and every candidate prefix must be link-free; symlinks and junctions refuse or stop uncertain. Snapshots read at most 32768 bytes per file and 131072 bytes total. Batches are limited to 8 files, each at most 32768 UTF-8 bytes, each key inside the allowlist and inside narrowed task targets. Writes create parents and preserve partial effects on failure.

## Finite shared bounds

- `request_budget`: shared counter for supervisor calls plus implementer calls. Exhaustion returns `budget-exhausted`.
- `max_tasks`: maximum implementer invocations. Hit returns `stopped-task-bound`.
- `max_repairs`: maximum tasks after a non-passing outcome. First task is not a repair. Hit returns `stopped-repair-bound`.
- `max_no_progress`: consecutive refused/stale steps or unchanged failing candidates. Changed failing candidates restart the no-progress count. Hit returns `stopped-no-progress`. A passing validation resets it to zero.

There is no implicit hosted inference, arbitrary shell, new dependency, service, daemon, or sleep in this path.

## Stops preserve effects and receipts

Returned `FinalState` values contain status, workspace hash, task/repair/request counters and accumulated receipts. Invalid configuration can raise before a state exists. Uncertain paths never roll back: `stopped-uncertain` on external mutation during supervisor, implementer, validation, or accept; `uncertain-partial-write` when writes fail midway; `stopped-unknown-validator` when the validator raises or returns a malformed outcome. Host receipts record snapshot hash, calls, diffs, validation results, refusals, and stop reasons.

## Exact passing-current-byte acceptance

`accept` succeeds only when the last validation passed and the stored validated hash still equals both the current workspace hash and the hash at accept time. Changed bytes invalidate approval. `accept-refused` and `accept-stale` are refusal outcomes/receipts, not final status names; the loop can request another task or stop on its bounds. No-progress increments so stale approval cannot loop forever.

## Live use and human boundary

In live use, actual generated-code execution must use a protected isolated runner; command allowlists alone are not isolation, and credentials stay outside that runner. Architecture, privacy, dependency, billing, and release choices remain human decisions. This loop never provisions access or publishes on its own. The public controller and adapters have no private Workbench runtime dependency. If Meta or Modal infrastructure is mentioned elsewhere, it is only one bounded development trial, not broad parity or production reliability.

## Scripted offline example

The following example is scripted and offline. It is not proof of live model quality. It validates inert text bytes only and never executes generated code.

```python
import tempfile
from pathlib import Path
from remote_operator.development import ValidationResult, run_development_loop
from remote_operator.development_json import JsonRoles

GOOD = b"v2 ok\n"

calls = {"implementer": 0}


def send(role, payload):
    if role == "supervisor":
        if payload["last_passed"]:
            return {"action": "accept", "reason": "passing bytes reviewed"}
        if payload["tasks_used"] == 0:
            return {
                "action": "task",
                "task": "write v2",
                "targets": ["notes.txt"],
                "reason": "first try",
            }
        assert "want exact" in payload["last_validation_output"]
        return {
            "action": "task",
            "task": "fix per validator feedback",
            "targets": ["notes.txt"],
            "reason": "repair",
        }
    assert role == "implementer"
    calls["implementer"] += 1
    if calls["implementer"] == 1:
        return {"changes": {"notes.txt": "bad draft\n"}}
    return {"changes": {"notes.txt": "v2 ok\n"}}


roles = JsonRoles(send)


def validator(snapshot):
    if snapshot.get("notes.txt") == GOOD:
        return ValidationResult(passed=True, output="ok")
    return ValidationResult(passed=False, output="want exact 'v2 ok' line")


with tempfile.TemporaryDirectory() as tmp:
    final = run_development_loop(
        workspace=Path(tmp),
        objective="Write notes.txt with one line: v2 ok",
        allowlist=("notes.txt",),
        supervisor=roles.supervisor,
        implementer=roles.implementer,
        validator=validator,
        request_budget=8,
        max_tasks=4,
        max_repairs=2,
        max_no_progress=4,
    )
    assert final.status == "accepted", final
    assert (Path(tmp) / "notes.txt").read_bytes() == GOOD
    print("accepted", final.tasks_used, final.requests_used)
```
