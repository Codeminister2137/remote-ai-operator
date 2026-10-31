# Remote supervisor planning threads

Supervisor threads let an owner plan with the model over chat, review a
written plan artifact, and approve it into a queued run job. Threads live in
the same task store as operator tasks, so history, plans and approvals reuse
task storage, ordered events and cancellation. A bounded worker process
(`supervisor-drain`) consumes queued jobs with the existing reconcile/resume
discipline: interrupted runs are inspected, never replayed.

## Routes

All routes exist on both the local owner app (Bearer [REDACTED] and the hosted
visitor app (session cookie), reusing each deployment's principal:

- `POST /api/supervisor/threads` with `{"topic": "..."}` opens a thread.
- `GET /api/supervisor/threads/{id}` returns the thread record and its events.
- `POST /api/supervisor/threads/{id}/messages` with `{"text": "..."}` appends
  a chat message and returns the assistant planning reply.
- `POST /api/supervisor/threads/{id}/plan` renders the plan artifact from the
  transcript and returns it with a single-use approval nonce (10 minutes).
- `POST /api/supervisor/threads/{id}/approve` with `{"nonce": "..."}` approves
  the exact reviewed plan bytes and enqueues a run job.
- `GET /api/supervisor/runs` lists queued runs; `GET /api/supervisor/runs/{id}`
  returns one queued run job.

## Reply routes

Scripted mode returns deterministic planning text labelled
`SCRIPTED OFFLINE REFERENCE`; it never claims model authorship. Live inference
reuses the caller's model actor through the audited operation interface with
the planning transcript as the prompt, renders the returned operation as a
readable planning step, and records the model receipt as a thread event.

## Approval binding

Approval mirrors task approval: the nonce is compared in constant time, expiry
is enforced, cancellation invalidates, and the plan digest is recomputed from
stored bytes before anything is queued. A changed plan invalidates the approval
and reopens the thread; the nonce is consumed on success. No credentials are
kept in the thread store.

## Worker

`uv run --locked remote-ai-operator supervisor-drain` reconciles jobs left
running by an interrupted worker, runs queued jobs oldest-first (default 10,
`--max-jobs 1..100`), then refreshes jobs waiting on owner approval. Each job
becomes one operator task whose prompt is the approved plan (truncated past
4000 characters and flagged), executed with the selected model actor
(`--model-mode scripted` by default; `meta` needs `--allow-live-inference` and
a verified finite grant). One worker at a time: a busy operator lock returns
the job to the queue instead of running it.

Job statuses mirror the linked task: `queued`, `running`, `paused`,
`awaiting_approval`, `completed`, `failed`, `cancelled`. Reconciliation resets
a claimed-but-unstarted job to `queued`, reconciles an interrupted task through
`Operator.reconcile`, and requeues it for a normal resume; refused
reconciliation fails the job with the reason. Cancelling the thread cancels
queued jobs and propagates to the linked task at slice boundaries. The worker
stops at the approval boundary: the owner approves the task through the normal
task routes, and the next drain refreshes the job to `completed`.

## Notifications

With `--notify` the drain writes one owner-only receipt per terminal job
outcome (`completed`, `failed`, `cancelled`, `awaiting_approval`) under
`<runtime>/notify_receipts/` without sending anything. `--notify-live` sends
over stdlib SMTP using `OPERATOR_OWNER_EMAIL` (sole allowed recipient),
`OPERATOR_SMTP_HOST`, optional `OPERATOR_SMTP_PORT` (default 587),
`OPERATOR_SMTP_USER`, `OPERATOR_SMTP_PASS` and `OPERATOR_SMTP_STARTTLS`
(`0` disables; port 465 uses implicit TLS). Off-owner addressing is refused;
transport failures are captured in the receipt and never break the drain.
