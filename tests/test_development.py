"""Tests for the foreground development-loop controller.

Synthetic callables only; real temporary file effects; independent validator.
No shell, no network, no new dependencies.
"""

from pathlib import Path

import pytest

from remote_operator.development import (
    ImplementerView,
    SupervisorDecision,
    SupervisorView,
    ValidationResult,
    run_development_loop,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode())


def test_failed_then_repair_passes_no_further_calls(tmp_path):
    calls = {"impl": 0, "sup": 0}
    seen = []

    def validator(files):
        seen.append(dict(files))
        body = files.get("notes.md", b"")
        if body == b"v2":
            return ValidationResult(True, "ok")
        return ValidationResult(False, "not yet")

    def implementer(view: ImplementerView):
        calls["impl"] += 1
        assert isinstance(view.task, str) and view.task
        if calls["impl"] == 1:
            return {"notes.md": "v1"}
        return {"notes.md": "v2"}

    def supervisor(view: SupervisorView):
        calls["sup"] += 1
        if view.last_outcome == "passed":
            assert view.last_passed is True
            return SupervisorDecision("accept", reason="looks good")
        if view.last_outcome == "failed":
            return SupervisorDecision("task", task="repair it", reason="model-repair")
        return SupervisorDecision("task", task="first try", reason="model-first")

    final = run_development_loop(
        workspace=tmp_path,
        objective="write notes",
        allowlist=("notes.md",),
        supervisor=supervisor,
        implementer=implementer,
        validator=validator,
        request_budget=32,
    )
    assert final.status == "accepted"
    assert (tmp_path / "notes.md").read_bytes() == b"v2"
    assert len(seen) == 2
    assert calls == {"impl": 2, "sup": 3}
    assert final.requests_used == 5
    assert final.receipts


def test_budget_exhausted_before_supervisor_send(tmp_path):
    calls = []

    def supervisor(view):
        calls.append("supervisor")
        return SupervisorDecision("task", task="write")

    def implementer(view):
        calls.append("implementer")
        return {"notes.md": "candidate"}

    final = run_development_loop(
        workspace=tmp_path,
        objective="obj",
        allowlist=("notes.md",),
        supervisor=supervisor,
        implementer=implementer,
        validator=lambda f: ValidationResult(True, "protected-pass"),
        request_budget=2,
    )
    assert final.status == "budget-exhausted"
    assert final.requests_used == 2
    assert calls == ["supervisor", "implementer"]
    assert (tmp_path / "notes.md").read_text() == "candidate"


def test_budget_exhausted_before_implementer_send(tmp_path):
    calls = {"impl": 0}

    def supervisor(view: SupervisorView):
        return SupervisorDecision("task", task="do it")

    def implementer(view):
        calls["impl"] += 1
        return {"notes.md": "x"}

    final = run_development_loop(
        workspace=tmp_path,
        objective="obj",
        allowlist=("notes.md",),
        supervisor=supervisor,
        implementer=implementer,
        validator=lambda f: ValidationResult(True, "ok"),
        request_budget=1,
    )
    assert final.status == "budget-exhausted"
    assert calls["impl"] == 0


def test_path_refusals_all_batch_zero_writes(tmp_path):
    _write(tmp_path / "notes.md", "keep")
    bad_batches = [
        {"../evil.md": "x", "notes.md": "changed"},
        {"/abs.md": "x", "notes.md": "changed"},
        {"other.md": "x", "notes.md": "changed"},
    ]
    for bad in bad_batches:
        state = {"n": 0}

        def supervisor(view: SupervisorView, _state=state):
            _state["n"] += 1
            if _state["n"] > 1:
                return SupervisorDecision("stop", reason="done")
            return SupervisorDecision("task", task="go")

        final = run_development_loop(
            workspace=tmp_path,
            objective="obj",
            allowlist=("notes.md",),
            supervisor=supervisor,
            implementer=lambda v, b=bad: dict(b),
            validator=lambda f: (_ for _ in ()).throw(AssertionError("validator must not run")),
            request_budget=8,
            max_no_progress=1,
        )
        assert final.status == "stopped-no-progress"
        assert (tmp_path / "notes.md").read_bytes() == b"keep"
        assert not (tmp_path / "evil.md").exists()
        assert list(tmp_path.iterdir()) == [tmp_path / "notes.md"]


def test_symlink_ancestor_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    _write(real / "notes.md", "keep")
    link = tmp_path / "ws"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    final = run_development_loop(
        workspace=link,
        objective="obj",
        allowlist=("notes.md",),
        supervisor=lambda v: SupervisorDecision("task", task="go"),
        implementer=lambda v: {"notes.md": "changed"},
        validator=lambda f: ValidationResult(True, "ok"),
        request_budget=8,
    )
    assert final.status in ("stopped-uncertain", "stopped-no-progress", "stopped")
    assert (real / "notes.md").read_bytes() == b"keep"


def test_oversize_nonstring_malformed_rejected(tmp_path):
    _write(tmp_path / "notes.md", "keep")
    for bad in ({"notes.md": "x" * 100_000}, {"notes.md": 123}, ["not-a-mapping"]):
        final = run_development_loop(
            workspace=tmp_path,
            objective="obj",
            allowlist=("notes.md",),
            supervisor=lambda v: SupervisorDecision("task", task="go"),
            implementer=lambda v, b=bad: b,
            validator=lambda f: (_ for _ in ()).throw(AssertionError("no validator")),
            request_budget=8,
            max_no_progress=1,
        )
        assert final.status == "stopped-no-progress"
        assert (tmp_path / "notes.md").read_bytes() == b"keep"


def test_stop_decision_no_progress(tmp_path):
    final = run_development_loop(
        workspace=tmp_path,
        objective="obj",
        allowlist=("notes.md",),
        supervisor=lambda v: SupervisorDecision("stop", reason="halt"),
        implementer=lambda v: {"notes.md": "x"},
        validator=lambda f: ValidationResult(True, "ok"),
    )
    assert final.status == "stopped"
    final2 = run_development_loop(
        workspace=tmp_path,
        objective="obj",
        allowlist=("notes.md",),
        supervisor=lambda v: SupervisorDecision("decision_needed", reason="help"),
        implementer=lambda v: {"notes.md": "x"},
        validator=lambda f: ValidationResult(True, "ok"),
    )
    assert final2.status == "decision-needed"
    # repeated premature accept burns no-progress bound, never validates
    calls = {"v": 0}

    def noval(f):
        calls["v"] += 1
        return ValidationResult(True, "ok")

    final3 = run_development_loop(
        workspace=tmp_path,
        objective="obj",
        allowlist=("notes.md",),
        supervisor=lambda v: SupervisorDecision("accept", reason="early"),
        implementer=lambda v: {"notes.md": "x"},
        validator=noval,
        max_no_progress=2,
    )
    assert final3.status == "stopped-no-progress"
    assert calls["v"] == 0


def test_stale_validation_blocks_accept(tmp_path):
    _write(tmp_path / "notes.md", "v1")
    state = {"n": 0}
    holder = {}

    def validator(files):
        holder["bytes"] = dict(files)
        return ValidationResult(True, "ok")

    def supervisor(view: SupervisorView):
        state["n"] += 1
        if state["n"] == 1:
            return SupervisorDecision("task", task="write v2")
        if state["n"] == 2:
            # external mutation after validation, before accept turn
            _write(tmp_path / "notes.md", "tampered")
            return SupervisorDecision("accept", reason="stale accept")
        return SupervisorDecision("stop", reason="end")

    final = run_development_loop(
        workspace=tmp_path,
        objective="obj",
        allowlist=("notes.md",),
        supervisor=supervisor,
        implementer=lambda v: {"notes.md": "v2"},
        validator=validator,
        request_budget=16,
        max_no_progress=6,
    )
    assert final.status != "accepted"
    assert (tmp_path / "notes.md").read_bytes() != holder["bytes"][
        "notes.md"
    ] or final.status != "accepted"


def test_validator_exception_stops_unknown(tmp_path):
    def supervisor(view: SupervisorView):
        if view.last_outcome == "failed":
            return SupervisorDecision("task", task="repair")
        return SupervisorDecision("task", task="go")

    def bad_validator(files):
        raise RuntimeError("isolated runner blew up")

    final = run_development_loop(
        workspace=tmp_path,
        objective="obj",
        allowlist=("notes.md",),
        supervisor=supervisor,
        implementer=lambda v: {"notes.md": "v1"},
        validator=bad_validator,
    )
    assert final.status == "stopped-unknown-validator"
    assert (tmp_path / "notes.md").read_bytes() == b"v1"
    assert any(r.kind == "unknown-validator" and r.provenance == "host" for r in final.receipts)


def test_receipts_provenance(tmp_path):
    receipts: list = []

    def supervisor(view: SupervisorView):
        if view.last_outcome == "passed":
            return SupervisorDecision("accept", reason="model-accept-text")
        return SupervisorDecision("task", task="go", reason="model-task-text")

    final = run_development_loop(
        workspace=tmp_path,
        objective="obj",
        allowlist=("notes.md",),
        supervisor=supervisor,
        implementer=lambda v: {"notes.md": "v1"},
        validator=lambda f: ValidationResult(True, "ok"),
        receipt_sink=receipts.append,
    )
    assert final.status == "accepted"
    kinds = {r.kind for r in receipts}
    assert {"snapshot", "validation", "accepted"} <= kinds
    assert all(r.provenance == "host" for r in receipts)


def test_supervisor_receives_actual_failed_evidence_and_diff(tmp_path):
    seen = []

    def supervisor(view):
        seen.append(view)
        if view.last_outcome == "failed":
            assert view.last_validation_output == "missing required behavior"
            assert "+bad" in view.diff
            return SupervisorDecision("stop")
        return SupervisorDecision("task", task="write")

    result = run_development_loop(
        workspace=tmp_path,
        objective="obj",
        allowlist=("notes.md",),
        supervisor=supervisor,
        implementer=lambda v: {"notes.md": "bad\n"},
        validator=lambda f: ValidationResult(False, "missing required behavior"),
    )
    assert result.status == "stopped"
    assert len(seen) == 2


def test_deleted_validated_empty_file_is_stale(tmp_path):
    def supervisor(view):
        if view.last_passed:
            (tmp_path / "notes.md").unlink()
            return SupervisorDecision("accept")
        return SupervisorDecision("task", task="write empty")

    result = run_development_loop(
        workspace=tmp_path,
        objective="obj",
        allowlist=("notes.md",),
        supervisor=supervisor,
        implementer=lambda v: {"notes.md": ""},
        validator=lambda f: ValidationResult(True, "ok"),
    )
    assert result.status != "accepted"


def test_stop_after_implementer_prevents_write_and_validation(tmp_path):
    stopped = []

    def implementer(view):
        stopped.append(True)
        return {"notes.md": "must not write"}

    def validator(files):
        pytest.fail("validator must not run after stop")

    result = run_development_loop(
        workspace=tmp_path,
        objective="obj",
        allowlist=("notes.md",),
        supervisor=lambda v: SupervisorDecision("task", task="write"),
        implementer=implementer,
        validator=validator,
        should_stop=lambda: bool(stopped),
    )
    assert result.status == "stopped"
    assert not (tmp_path / "notes.md").exists()


@pytest.mark.parametrize("path", ["C:escape", "notes.md:stream", "dir\\notes.md"])
def test_windows_ambiguous_allowlist_paths_refused(tmp_path, path):
    result = run_development_loop(
        workspace=tmp_path,
        objective="obj",
        allowlist=(path,),
        supervisor=lambda v: pytest.fail("invalid scope must stop before model"),
        implementer=lambda v: {},
        validator=lambda f: ValidationResult(True, "ok"),
    )
    assert result.status == "stopped-uncertain"


def test_unchanged_failed_candidate_stops_without_extra_model_calls(tmp_path):
    calls = []

    def supervisor(view):
        calls.append("supervisor")
        return SupervisorDecision("task", task="repair")

    def implementer(view):
        calls.append("implementer")
        return {"notes.md": "same candidate"}

    result = run_development_loop(
        workspace=tmp_path,
        objective="obj",
        allowlist=("notes.md",),
        supervisor=supervisor,
        implementer=implementer,
        validator=lambda f: ValidationResult(False, "protected failure"),
        max_no_progress=1,
    )
    assert result.status == "stopped-no-progress"
    assert calls == ["supervisor", "implementer", "supervisor", "implementer"]
    assert result.requests_used == 4


def test_stop_arriving_during_write_prevents_validator_launch(tmp_path, monkeypatch):
    from pathlib import Path

    stopped = False
    validations = []
    original_write = Path.write_bytes

    def write_then_stop(path, raw):
        nonlocal stopped
        result = original_write(path, raw)
        if path == tmp_path / "notes.md":
            stopped = True
        return result

    monkeypatch.setattr(Path, "write_bytes", write_then_stop)
    result = run_development_loop(
        workspace=tmp_path,
        objective="write notes",
        allowlist=("notes.md",),
        supervisor=lambda view: SupervisorDecision("task", task="write notes"),
        implementer=lambda view: {"notes.md": "preserved candidate"},
        validator=lambda files: validations.append(files) or ValidationResult(True, "ok"),
        should_stop=lambda: stopped,
    )
    assert result.status == "stopped"
    assert (tmp_path / "notes.md").read_bytes() == b"preserved candidate"
    assert validations == []
    assert result.requests_used == 2


def test_docstrings_exist():
    assert run_development_loop.__doc__ and run_development_loop.__doc__.strip()
    assert ValidationResult.__doc__ and ValidationResult.__doc__.strip()
    assert SupervisorDecision.__doc__ and SupervisorDecision.__doc__.strip()


def test_docstrings_are_meaningful():
    # Ensure docstrings describe purpose/contract, not just whitespace
    for obj in (run_development_loop, ValidationResult, SupervisorDecision):
        doc = obj.__doc__ or ""
        assert len(doc.strip()) > 20
        # At least a few words
        assert len(doc.split()) >= 5


def test_receipt_sink_exception_propagates(tmp_path):
    def failing_sink(_receipt):
        raise RuntimeError("sink boom")

    with pytest.raises(RuntimeError, match="sink boom"):
        run_development_loop(
            workspace=tmp_path,
            objective="obj",
            allowlist=("notes.md",),
            supervisor=lambda v: SupervisorDecision("task", task="go"),
            implementer=lambda v: {"notes.md": "v1"},
            validator=lambda f: ValidationResult(True, "ok"),
            receipt_sink=failing_sink,
        )
