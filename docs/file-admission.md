# File admission

`read_file`/`write_file` reach the registered sample source
(`issue_import.py`) plus single-segment basenames ending in `.js` or `.html`
(such as `app.js`, `index.html`).

Refused before any filesystem access: separators (`/`, `\`), parent
references (`..`), absolute or drive-qualified paths, empty names, and any
other extension. Admitted content shares the `MAX_SOURCE_BYTES` (32768) cap
with the sample source, on both read and write.

`review()` returns one `diff`: the sample-source section first (byte-identical
to the single-file form when no admitted files are present), followed by one
unified diff per admitted file present in the candidate, in sorted name order.

Task approvals stay open for `TASK_APPROVAL_SECONDS` (3600, one hour).

Boundaries kept by this slice: approval binding, apply, validation digests,
and reconcile comparisons for the sample source are unchanged; admitted files
are visible in review and any write (including admitted files) invalidates
stale evidence, but approval/apply still bind the sample source only.
