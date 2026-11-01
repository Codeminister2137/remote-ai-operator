# Owner questions without stalled execution

A model op `ask_owner` parks one task as `waiting_on_owner` with the
question; the worker mirrors the job and keeps draining every other run.
The owner answers in Review & approve (or `POST /api/tasks/{id}/answer`);
the task resumes as `paused` and linked runs requeue automatically.
Unattended supervision mails the question like any run event.

## Splitting guideline

Keep tasks small enough that one question unblocks them: one decision, one
artifact, one validation. A run that needs two answers is two runs — write
the second plan after the first answer lands. Queued runs behind a parked
one are unaffected; the worker never waits on the owner.
