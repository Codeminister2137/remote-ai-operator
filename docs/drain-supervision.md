# Drain supervision

`serve` runs queued supervisor jobs in a daemon worker thread. Each pass
is guarded, counted and timestamped in a shared `DrainWorkerStatus`;
a monitor thread restarts the worker when its thread dies and counts
the restart. `GET /api/drain` (owner auth) reports
`running/alive/last_pass/passes/errors/restarts`; the Progress section
shows one line from it. Without a worker the endpoint answers
`{"running": false}`.
