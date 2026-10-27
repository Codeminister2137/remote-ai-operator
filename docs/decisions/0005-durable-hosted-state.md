# 0005 - Single-writer SQLite state on a Modal Volume

Status: accepted 2026-10-27. Supersedes the "persistence undecided" status in 0003 and
0004; it does not change the session, credential or budget decisions in those ADRs.

## Context

The hosted judge demo records tasks, events, approvals and allowance consumption in
the existing SQLite store. Without durable storage a Modal scale-to-zero/cold start
silently stranded committed task state, and a resumable browser session would have
resolved to an empty workspace. The owner had to choose between a single-writer
volume design, an ephemeral hosted store, or a distributed record store.

Official Modal Volumes documentation (checked 2026-10-27) states that concurrent
modification of the same file is last-write-wins, distributed file locking is not
supported, a Volume appears empty during its own reload, and commits run in the
background every few seconds plus on shutdown.

## Owner decision and rationale

The owner selected SQLite on a Modal Volume v2 with a single container. Stated
rationale:"seems the most straightforward". The choice preserves the existing
`Store` schema, transactions, ordering and recovery checks instead of adding a new
database service, schema migration and credentials for a small invitation-like demo.

## Alternatives considered

- Ephemeral store (no Volume). Simplest and cheapest, but every cold start loses
  tasks, approvals and consumed allowance. It would also make resumable sessions
  misleading. Rejected because admitted work must stay inspectable.
- Distributed record store (Postgres/Redis/object store with locking). Supports
  multiple writers and independent scaling, but adds a new paid service, connection
  secrets, schema/contracts and operational failure modes for a demo that does not
  need horizontal scale. Rejected for this scope.
- Single-writer SQLite on a Modal Volume v2 (chosen). Reuses the existing store and
  keeps admission-before-send semantics, at the cost of exactly one container and a
  non-atomic volume commit after each write.

## Accepted implementation

- Runtime root is `/data/runtime` on the `remote-operator-state` Volume v2.
- `max_containers=1`; deploys use `modal deploy --strategy recreate`, so two
  containers never write the same files. `min_containers=0` allows scale-to-zero.
- Startup calls `volume.reload()`, creates the runtime root and runs
  `PRAGMA quick_check` on every existing `*/tasks.sqlite3`. A corrupt or unreadable
  store refuses startup rather than discarding recorded effects.
- `Store(after_commit=volume.commit)` flushes after each committed write transaction
  (schema creation, task save, allowance reservation, cancellation request). Reads
  never commit. Final shutdown also commits pending writes.
- Operationally, `scaledown_window=300` keeps one warm container for five idle
  minutes; a cold start reloads the committed state before serving requests.
- The hosted session signing secret(`OPERATOR_SESSION_SECRET`) is stable and private,
  so a browser resumes the same subject and therefore the same task store after a
  restart. Rotating it logs visitors out but does not delete recorded tasks.

## Consequences and remaining limits

- This is explicitly **not** a multi-writer design. Do not raise `max_containers`
  above one, use a rolling deploy that overlaps containers, or mount the volume in a
  second app while the hosted app can write.
- SQLite commit and volume commit are separate. If `volume.commit()` fails after the
  SQLite transaction, the request reports failure but local state may already have
  advanced; a crash before a retry can lose that write. This is acceptable for a
  demo, not a billing-grade ledger. There is no retry/queue layer.
- Last-write-wins applies even to one writer only if another actor mounts the volume;
  keep the volume exclusive to this app.
- There is no point-in-time restore or backup policy. A corrupted volume is refused
  at startup and must be investigated; the app does not auto-delete state.
- Schema changes rely on `CREATE TABLE IF NOT EXISTS`; there is no general migration
  framework. A future incompatible schema change needs an explicit migration plan.
- Logout is process-local (see amended 0004): a signed token remains valid until it
  expires after a restart. Revocation requires rotating the signing secret.
