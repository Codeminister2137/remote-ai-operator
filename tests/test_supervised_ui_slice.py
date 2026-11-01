"""Genuine admitted-file slice through the real supervised loop.

Thread -> plan -> approve(execute) -> drain -> review, with the worker
writing a real widget.html through the op protocol. The double stands in
for model intelligence only; every controller/tool/store step is genuine.
"""

import json
import tempfile
import unittest
from pathlib import Path

from remote_operator.control import SOURCE, Operation, Operator, Validation
from remote_operator.scripted import REFERENCE, ScriptedModel
from remote_operator.supervisor import Supervisor
from remote_operator.supervisor_runner import SupervisorRunner

WIDGET = """<section class="status-badge">
  <h2>Run status</h2>
  <p data-status="{{status}}">Supervised UI slice: admitted file rendered.</p>
</section>
"""


class Validator:
    def validate(self, workspace, candidate_digest, *, cancelled=None):
        return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")


class UiSliceModel:
    """Write one admitted .html file plus the SOURCE fix, then finish."""

    name = "UI SLICE TEST DOUBLE"

    def next_operation(self, history, *, prompt):
        names = [entry["name"] for entry in history]
        if not names:
            return Operation("read_file", {"path": SOURCE})
        writes = [entry["arguments"]["path"] for entry in history if entry["name"] == "write_file"]
        if "widget.html" not in writes:
            return Operation("write_file", {"path": "widget.html", "content": WIDGET})
        if SOURCE not in writes:
            return Operation("write_file", {"path": SOURCE, "content": REFERENCE})
        if "validate" not in names:
            return Operation("validate", {})
        return Operation("finish", {})


class SupervisedUiSliceTests(unittest.TestCase):
    def test_thread_plan_approve_drain_review_covers_widget(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            seed = root / "seed.py"
            seed.write_text("# seed\n", encoding="utf-8")
            operator = Operator(root / "runtime", seed, Validator())
            supervisor = Supervisor(operator)
            thread = supervisor.open_thread("UI slice")
            supervisor.post_message(ScriptedModel(), thread["id"], "add a status badge")
            plan = supervisor.write_plan(thread["id"])
            run_id = supervisor.approve_plan(thread["id"], plan["nonce"])["run_id"]
            runner = SupervisorRunner(operator)
            runner.drain(UiSliceModel())
            job_path = operator.root / "supervisor_runs" / f"{run_id}.json"
            job = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(job["status"], "awaiting_approval")
            task_id = job["task_id"]
            task = operator.store.get(task_id)
            self.assertEqual(task["status"], "awaiting_approval")
            review = operator.review(task_id)
            self.assertIn("candidate/" + SOURCE, review["diff"])
            self.assertIn("candidate/widget.html", review["diff"])
            self.assertIn("status-badge", review["diff"])
            candidate = operator.folder(task_id, "candidate") / "widget.html"
            self.assertEqual(candidate.read_text(encoding="utf-8"), WIDGET)

    def test_meta_tools_admit_ui_paths(self):
        from remote_operator.meta import INSTRUCTIONS, TOOLS

        self.assertIn(".js", INSTRUCTIONS)
        self.assertIn(".html", INSTRUCTIONS)
        for tool in TOOLS:
            if tool["name"] in {"read_file", "write_file"}:
                path = tool["parameters"]["properties"]["path"]
                self.assertNotIn("enum", path)
                self.assertIn(".html", path["description"])


if __name__ == "__main__":
    unittest.main()
