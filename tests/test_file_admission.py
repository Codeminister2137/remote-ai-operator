"""Admission checks for controlled .js/.html files and the approval window."""

import difflib
import tempfile
import time
import unittest
from pathlib import Path

from remote_operator.control import (
    MAX_SOURCE_BYTES,
    SOURCE,
    TASK_APPROVAL_SECONDS,
    Operation,
    Operator,
    Validation,
    admitted_file,
    digest,
    read_named,
    read_source,
    write_named,
)
from remote_operator.scripted import ScriptedModel

JS_CONTENT = "console.log('admitted');\n"
HTML_CONTENT = "<!doctype html><title>admitted</title>\n"

REJECTED_NAMES = [
    "",
    "..",
    ".",
    "../outside.js",
    "a/b.js",
    "a\\b.js",
    "/abs.js",
    "\\abs.js",
    "C:\\win.js",
    "C:/win.js",
    "C:drive.js",
    "evil.py",
    "notes.txt",
    "appjs",
    "app.JS",
    "page.HTML",
    "no-extension",
    ".js.bak",
]


class FakeValidator:
    """Controller test double; never used by the application or CLI."""

    def validate(self, workspace, candidate_digest, *, cancelled=None):
        return Validation(True, candidate_digest, 0, "TEST DOUBLE", "test-double")


class FileAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.seed = self.root / "seed.py"
        self.seed.write_text("# initial source\n", encoding="utf-8")
        self.operator = Operator(self.root / "runtime", self.seed, FakeValidator())
        self.task = self.operator.submit("Exercise file admission")
        self.task_id = self.task["id"]

    def tearDown(self):
        self.directory.cleanup()

    def candidate(self):
        return self.operator.folder(self.task_id, "candidate")

    def execute(self, name, arguments):
        task = self.operator.store.get(self.task_id)
        return self.operator._execute(task, Operation(name, arguments))

    def test_read_task_spec_serves_registered_doc_read_only(self):
        project = self.root / "project"
        (project / "sample").mkdir(parents=True)
        (project / "docs").mkdir(parents=True)
        seed = project / "sample" / "issue_import.py"
        seed.write_text("# seed\n", encoding="utf-8")
        (project / "docs" / "sample-task.md").write_text("SPEC\n", encoding="utf-8")
        operator = Operator(project / "runtime", seed, FakeValidator())
        task = operator.submit("spec read")
        for path in ("sample-task.md", "docs/sample-task.md"):
            result = operator._execute(
                operator.store.get(task["id"]), Operation("read_file", {"path": path})
            )
            self.assertEqual(result, {"content": "SPEC\n"})
        with self.assertRaises(ValueError):
            operator._execute(
                operator.store.get(task["id"]),
                Operation("write_file", {"path": "sample-task.md", "content": "x"}),
            )

    def test_read_task_spec_missing_fails_closed(self):
        with self.assertRaises(ValueError):
            self.execute("read_file", {"path": "sample-task.md"})

    def test_admit_js_and_html_roundtrip(self):
        for name, content in (("app.js", JS_CONTENT), ("index.html", HTML_CONTENT)):
            with self.subTest(name=name):
                result = self.execute("write_file", {"path": name, "content": content})
                self.assertEqual(result, {"digest": digest(content)})
                self.assertEqual(self.execute("read_file", {"path": name}), {"content": content})
                self.assertEqual(read_named(self.candidate(), name), content)

    def test_source_roundtrip_unchanged(self):
        self.execute("write_file", {"path": SOURCE, "content": "# changed\n"})
        self.assertEqual(self.execute("read_file", {"path": SOURCE}), {"content": "# changed\n"})
        self.assertEqual(read_source(self.candidate()), "# changed\n")

    def test_admitted_names_accepted(self):
        self.assertEqual(admitted_file(SOURCE), SOURCE)
        for name in ("app.js", "index.html", "a.b.c.js", "x-y_z.html"):
            with self.subTest(name=name):
                self.assertEqual(admitted_file(name), name)

    def test_rejected_names_refused_before_write(self):
        for name in REJECTED_NAMES:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    admitted_file(name)
                with self.assertRaises(ValueError):
                    self.execute("write_file", {"path": name, "content": "bad"})
                with self.assertRaises(ValueError):
                    write_named(self.candidate(), name, "bad")
        self.assertEqual(sorted(path.name for path in self.candidate().iterdir()), [SOURCE])
        self.assertFalse((self.root / "outside.js").exists())

    def test_rejected_names_not_read(self):
        for name in REJECTED_NAMES:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.execute("read_file", {"path": name})

    def test_non_string_names_refused(self):
        for name in (None, 123, b"app.js", ["app.js"]):
            with self.subTest(name=repr(name)):
                with self.assertRaises(ValueError):
                    admitted_file(name)
                with self.assertRaises(ValueError):
                    self.execute("write_file", {"path": name, "content": "bad"})

    def test_oversize_content_rejected(self):
        oversize = "x" * (MAX_SOURCE_BYTES + 1)
        with self.assertRaises(ValueError):
            self.execute("write_file", {"path": "big.js", "content": oversize})
        with self.assertRaises(ValueError):
            write_named(self.candidate(), "big.html", oversize)
        self.assertFalse((self.candidate() / "big.js").exists())
        self.assertFalse((self.candidate() / "big.html").exists())
        (self.candidate() / "planted.js").write_bytes(b"y" * (MAX_SOURCE_BYTES + 1))
        with self.assertRaises(ValueError):
            self.execute("read_file", {"path": "planted.js"})
        with self.assertRaises(ValueError):
            read_named(self.candidate(), "planted.js")

    def test_size_cap_boundary_admitted(self):
        boundary = "z" * MAX_SOURCE_BYTES
        self.execute("write_file", {"path": "edge.js", "content": boundary})
        self.assertEqual(self.execute("read_file", {"path": "edge.js"})["content"], boundary)

    def test_write_invalidates_evidence_for_admitted_files(self):
        task = self.operator.store.get(self.task_id)
        task["evidence"] = {"passed": True}
        self.operator._execute(
            task, Operation("write_file", {"path": "app.js", "content": JS_CONTENT})
        )
        self.assertIsNone(task["evidence"])

    def test_review_covers_every_admitted_file(self):
        before = self.operator.review(self.task_id)["diff"]
        self.execute("write_file", {"path": "app.js", "content": JS_CONTENT})
        self.execute("write_file", {"path": "index.html", "content": HTML_CONTENT})
        diff = self.operator.review(self.task_id)["diff"]
        self.assertTrue(diff.startswith(before))
        for name, content in (("app.js", JS_CONTENT), ("index.html", HTML_CONTENT)):
            expected = "".join(
                difflib.unified_diff(
                    [],
                    content.splitlines(True),
                    fromfile="base/" + name,
                    tofile="candidate/" + name,
                )
            )
            self.assertIn(expected, diff)

    def test_review_without_admitted_files_matches_legacy_diff(self):
        self.execute("write_file", {"path": SOURCE, "content": "# changed\n"})
        task = self.operator.store.get(self.task_id)
        expected = "".join(
            difflib.unified_diff(
                task["base_source"].splitlines(True),
                "# changed\n".splitlines(True),
                fromfile="base/" + SOURCE,
                tofile="candidate/" + SOURCE,
            )
        )
        self.assertEqual(self.operator.review(self.task_id)["diff"], expected)

    def test_approval_window_is_one_hour(self):
        self.assertEqual(TASK_APPROVAL_SECONDS, 3600)
        task = self.operator.run(self.task_id, ScriptedModel())
        remaining = task["approval"]["expires"] - time.time()
        self.assertGreater(remaining, 3500)
        self.assertLessEqual(remaining, 3600)


if __name__ == "__main__":
    unittest.main()
