# Supervised UI slice (D3 proof)

`tests/test_supervised_ui_slice.py` drives the genuine loop path —
thread, plan, approve with execute, drain worker, review — with a worker
that writes a real `widget.html` through the `write_file` op. The run
reaches `awaiting_approval` (completion needs the owner), the review diff
covers both `issue_import.py` and the admitted file, and the candidate
bytes round-trip exactly.

Model prompts (`meta.py`) now describe the admitted paths; the host
(`control.admitted_file`) remains the enforcing gate. A live Meta run of
the same slice needs a finite grant file plus meta mode; the scripted
double here proves the machinery, not model judgment.
