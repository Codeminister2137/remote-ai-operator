# Window status

The owner UI shows one agent-activity line in the Progress section
(`windowStatus`). It answers: is an agent window active right now, which
slice, and when was the last heartbeat.

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
  "note": "slice 3 of 5"
}
```

All fields are strings. `sleep_state` is the agent's own sleep label
(e.g. `awake`, `sleep_scheduled`); it is informational, not a power command.

## Route behaviour

`GET /api/window` (owner auth only; hosted visitors get 403 like the other
owner routes) returns the file fields plus `running: true`. A missing file
returns `{"running": false}`. Malformed JSON, a non-object payload, or
non-string fields never raise: the loader falls back to safe defaults
(`running: false`, empty strings).
