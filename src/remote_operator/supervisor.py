"""Supervisor planning threads over the existing task store.

Threads are task records carrying a ``supervisor_thread`` marker, so chat
history, plan artifacts and approvals reuse task storage, ordered events and
cancellation. Scripted replies are deterministic labelled reference text.
Live inference reuses the caller's model actor through the audited operation
interface and records its receipt. Approving a plan binds the exact reviewed
plan bytes and enqueues a run job; changed plans invalidate approval. No
credentials are kept in the thread store.
"""

import json
import secrets
import time
import uuid

from remote_operator.control import digest

PLAN_APPROVAL_SECONDS = 600
RUNS_DIRNAME = "supervisor_runs"


class Supervisor:
    """Planning threads bound to one operator's store, lock and root."""

    def __init__(self, operator):
        self.operator = operator

    @property
    def _runs(self):
        folder = self.operator.root / RUNS_DIRNAME
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _thread(self, thread_id: str) -> dict:
        if not isinstance(thread_id, str) or not thread_id.isalnum() or len(thread_id) != 32:
            raise ValueError("Invalid thread identifier")
        task = self.operator.store.get(thread_id)
        if task.get("id") != thread_id or not task.get("supervisor_thread"):
            raise ValueError("Unknown supervisor thread")
        return task

    def open_thread(self, topic: str) -> dict:
        """Open a planning thread for ``topic``; returns its summary."""
        if not isinstance(topic, str) or not topic.strip() or len(topic) > 4000:
            raise ValueError("Thread requires a 1-4000 character topic")
        with self.operator.lock:
            thread_id = uuid.uuid4().hex
            thread = {
                "id": thread_id,
                "prompt": topic,
                "status": "open",
                "supervisor_thread": True,
                "history": [],
                "plan": None,
                "approval": None,
            }
            self.operator.store.save(thread, "thread_opened", {})
            return {"id": thread_id, "topic": topic, "status": "open"}

    def get_thread(self, thread_id: str) -> dict:
        """Return the thread record with its ordered events."""
        thread = self._thread(thread_id)
        return {"thread": thread, "events": self.operator.store.events(thread_id)}

    def list_threads(self, limit: int = 20) -> list[dict]:
        """Return id/topic/status summaries for planning threads, newest first."""
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError(f"limit must be an integer between 1 and 50: got {limit!r}")
        summaries = []
        for item in self.operator.store.recent_tasks(50):
            if len(summaries) >= limit:
                break
            try:
                task = self.operator.store.get(item["id"])
            except KeyError:
                continue
            if task.get("supervisor_thread"):
                summaries.append(
                    {"id": task["id"], "topic": task["prompt"][:160], "status": task["status"]}
                )
        return summaries

    def post_message(self, model, thread_id: str, text: str) -> dict:
        """Append a user message and generate the assistant planning reply."""
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError("Message requires 1-4000 characters")
        with self.operator.lock:
            thread = self._thread(thread_id)
            if thread["status"] != "open":
                raise ValueError("Thread is closed to new messages")
            thread["history"].append({"role": "user", "text": text})
            if type(model).__name__ == "ScriptedModel":
                reply = self._scripted_reply(thread)
                route = "scripted"
            else:
                reply, route = self._live_reply(model, thread)
            thread["history"].append({"role": "assistant", "text": reply, "route": route})
            self.operator.store.save(thread, "thread_message", {"route": route})
            return {"reply": reply, "route": route}

    def _scripted_reply(self, thread: dict) -> str:
        count = sum(1 for turn in thread["history"] if turn["role"] == "user")
        return (
            "[SCRIPTED OFFLINE REFERENCE] Recorded message "
            f"{count} for '{thread['prompt'][:80]}'. "
            "Ask for a plan artifact when requirements are complete."
        )

    def _live_reply(self, model, thread: dict) -> tuple[str, str]:
        transcript = "\n".join(
            f"{turn['role']}: {turn['text']}" for turn in thread["history"][-10:]
        )
        prompt = (
            f"Planning thread '{thread['prompt']}'. Transcript:\n{transcript}\n"
            "Propose the next planning step."
        )
        operation = model.next_operation([], prompt=prompt)
        receipt = getattr(model, "last_receipt", None)
        if receipt is not None:
            self.operator.store.save(thread, "inference_receipt", receipt)
        name = getattr(operation, "name", "reply")
        args = getattr(operation, "arguments", {})
        return f"[{model.name}] planning step {name}: {json.dumps(args)[:500]}", "meta"

    def write_plan(self, thread_id: str) -> dict:
        """Render the plan artifact from the transcript; returns it with a nonce."""
        with self.operator.lock:
            thread = self._thread(thread_id)
            if thread["status"] != "open":
                raise ValueError("Thread is closed to new plans")
            user_turns = [turn["text"] for turn in thread["history"] if turn["role"] == "user"]
            if not user_turns:
                raise ValueError("Thread has no requirements yet")
            lines = [f"# Plan: {thread['prompt']}", ""]
            for index, text in enumerate(user_turns, 1):
                lines.append(f"{index}. {text}")
            lines += ["", "Assistant turns recorded: ", ""]
            for turn in thread["history"]:
                if turn["role"] == "assistant":
                    lines.append(f"- ({turn.get('route', 'unknown')}) {turn['text'][:200]}")
            markdown = "\n".join(lines)
            plan_digest = digest(markdown)
            approval = {
                "nonce": secrets.token_hex(16),
                "plan_digest": plan_digest,
                "expires": time.time() + PLAN_APPROVAL_SECONDS,
            }
            thread["plan"] = {"markdown": markdown, "digest": plan_digest}
            thread["approval"] = approval
            thread["status"] = "planned"
            self.operator.store.save(thread, "plan_written", {"digest": plan_digest})
            return {"digest": plan_digest, "markdown": markdown, "nonce": approval["nonce"]}

    def approve_plan(self, thread_id: str, nonce: str, *, now: float | None = None) -> dict:
        """Approve the exact reviewed plan bytes and enqueue a run job."""
        with self.operator.lock:
            thread = self._thread(thread_id)
            approval = thread.get("approval")
            if thread["status"] != "planned" or not approval:
                raise ValueError("No pending plan approval")
            if not isinstance(nonce, str):
                raise ValueError("Invalid or expired approval")  # noqa: TRY004
            try:
                nonce.encode("ascii")
            except UnicodeEncodeError:
                raise ValueError("Invalid or expired approval")
            current = time.time() if now is None else now
            if (
                not secrets.compare_digest(approval["nonce"], nonce)
                or current >= approval["expires"]
            ):
                raise ValueError("Invalid or expired approval")
            if self.operator.store.cancelled(thread_id):
                raise ValueError("Cancellation invalidated this approval")
            plan = thread.get("plan") or {}
            if digest(plan.get("markdown", "")) != approval["plan_digest"]:
                thread["status"] = "open"
                thread["approval"] = None
                self.operator.store.save(thread, "approval_invalidated")
                raise ValueError("Reviewed plan changed")
            run_id = uuid.uuid4().hex
            job = {
                "run_id": run_id,
                "thread_id": thread_id,
                "plan_digest": approval["plan_digest"],
                "plan": plan["markdown"],
                "status": "queued",
                "created": current,
            }
            target = self._runs / f"{run_id}.json"
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(job), encoding="utf-8")
            tmp.replace(target)
            thread["status"] = "approved"
            thread["approval"] = None
            self.operator.store.save(thread, "run_queued", {"run_id": run_id})
            return {"run_id": run_id, "status": "queued"}

    def list_runs(self) -> list[dict]:
        """Return queued run summaries, newest first."""
        runs = []
        for path in sorted(self._runs.glob("*.json"), reverse=True):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            runs.append(
                {
                    "run_id": job.get("run_id", path.stem),
                    "thread_id": job.get("thread_id", ""),
                    "status": job.get("status", "unknown"),
                    "created": job.get("created", 0),
                }
            )
        return runs

    def run_status(self, run_id: str) -> dict:
        """Return one queued run job; raises KeyError when unknown."""
        if not isinstance(run_id, str) or not run_id.isalnum() or len(run_id) != 32:
            raise ValueError("Invalid run identifier")
        path = self._runs / f"{run_id}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise KeyError(f"Unknown run: {run_id}") from None
