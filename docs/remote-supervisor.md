# Remote supervisor planning threads

Supervisor threads let an owner plan with the model over chat, review a
written plan artifact, and approve it into a queued run job. Threads live in
the same task store as operator tasks, so history, plans and approvals reuse
task storage, ordered events and cancellation. A worker process (separate
change) consumes queued jobs with the existing reconcile/resume discipline.

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
