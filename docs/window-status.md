# Window status

The owner UI shows agent activity in the Progress section from a single
`/api/window` poll: the window line (`windowStatus`), the agents line
(`agentsStatus`), and the worker line (`drainStatus`). It answers: is an
agent window active right now, which slice, which agents/workers are busy,
and is the drain worker alive.

## Source file

The agent maintains `window-status.json` at the operator root
(`operator.root/window-status.json`), rewriting it at slice boundaries and
on every heartbeat. The serve process only reads; it never writes.

## Schema

```json
{
  "slice": "2026-11-01 10:00-11:00 UTC",
  "phase": "draining",
  "started_utc": "2026-11-01T10:00:00Z",
  "last_heartbeat_utc": "2026-11-01T10:30:00Z",
  "next_heartbeat_utc": "2026-11-01T10:35:00Z",
  "sleep_state": "awake",
  "note": "slice 3 of 5",
  "agents": [{"name": "worker-a", "task": "digest probe", "status": "running"}]
}
```

All fields are strings except `agents`, a list of `{name, task, status}`
string triples the agent rewrites on every worker spawn/finish.
`sleep_state` is the agent's own sleep label (e.g. `awake`,
`no-sleep override`); it is informational, not a power command.

## Route behaviour

`GET /api/window` (owner auth only; hosted visitors get 403 like the other
owner routes) merges the file with live process state: `serve_pid` and the
drain snapshot (`drain`, null when no worker) plus `running: true`. A
missing file returns `{"running": false}` plus the process keys. Malformed
JSON, a non-object payload, or non-string fields never raise: the loader
falls back to safe defaults (`running: false`, empty strings, no agents).
