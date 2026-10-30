from __future__ import annotations

import dataclasses
import sys
import types
from typing import Any

import pytest

from remote_operator.development import (
    ImplementerView,
    SupervisorDecision,
    SupervisorView,
    ValidationResult,
    run_development_loop,
)
from remote_operator.development_json import JsonRoles


def _sup_view(**kw: Any) -> SupervisorView:
    base: dict[str, Any] = {
        "objective": "o",
        "snapshot": {"a.txt": None},
        "diff": "",
        "last_outcome": "none",
        "last_passed": False,
        "last_validation_output": "",
        "budget_left": 5,
        "tasks_used": 0,
        "repairs_used": 0,
        "allowed": ("a.txt",),
    }
    base.update(kw)
    return SupervisorView(**base)  # type: ignore[arg-type]


def _impl_view(**kw: Any) -> ImplementerView:
    base: dict[str, Any] = {
        "objective": "o",
        "task": "t",
        "targets": ("a.txt",),
        "snapshot": {},
    }
    base.update(kw)
    return ImplementerView(**base)  # type: ignore[arg-type]


def _must_not_send(role: str, payload: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError("send must not be called")


def test_exact_view_types_reject_fakes_and_subclasses():
    @dataclasses.dataclass(frozen=True)
    class FakeSup:
        objective: str = "o"
        snapshot: dict = dataclasses.field(default_factory=dict)
        diff: str = ""
        last_outcome: str = "none"
        last_passed: bool = False
        last_validation_output: str = ""
        budget_left: int = 5
        tasks_used: int = 0
        repairs_used: int = 0
        allowed: tuple = ("a.txt",)

    class SubSup(SupervisorView):
        pass

    class SubImpl(ImplementerView):
        pass

    with pytest.raises(TypeError):
        JsonRoles(_must_not_send).supervisor(FakeSup())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        JsonRoles(_must_not_send).implementer(FakeSup())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        JsonRoles(_must_not_send).supervisor(_impl_view())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        JsonRoles(_must_not_send).implementer(_sup_view())  # type: ignore[arg-type]
    # Subclasses are not the exact controller type under the strict contract.
    with pytest.raises(TypeError):
        JsonRoles(_must_not_send).supervisor(SubSup(**dataclasses.asdict(_sup_view())))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        JsonRoles(_must_not_send).implementer(SubImpl(**dataclasses.asdict(_impl_view())))  # type: ignore[arg-type]


def test_missing_vs_empty_snapshot():
    def send(role: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert payload["snapshot"] == {"a.txt": None}
        return {"action": "stop", "reason": "done"}

    result = JsonRoles(send).supervisor(_sup_view(snapshot={"a.txt": None}))
    assert type(result) is SupervisorDecision

    def send2(role: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert payload["snapshot"] == {"a.txt": ""}
        return {"changes": {"a.txt": ""}}

    out = JsonRoles(send2).implementer(_impl_view(snapshot={"a.txt": b""}))
    assert out == {"a.txt": ""}


def test_unicode_strict_and_invalid_utf8():
    def send(role: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert payload["snapshot"]["a.txt"] == "h\u00e9llo \U0001f600"
        return {"action": "stop"}

    JsonRoles(send).supervisor(_sup_view(snapshot={"a.txt": "h\u00e9llo \U0001f600".encode()}))
    with pytest.raises((ValueError, TypeError)):
        JsonRoles(_must_not_send).supervisor(_sup_view(snapshot={"a.txt": b"\xff\xfe"}))


def test_no_mutation():
    snap = {"a.txt": b"hi"}
    view = _sup_view(snapshot=snap)
    before = dict(snap)

    def send(role: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload["snapshot"]["a.txt"] = "MUT"
        return {"action": "stop"}

    JsonRoles(send).supervisor(view)
    assert snap == before
    assert view.snapshot == before


def test_malformed_refused_no_send():
    for bad in [
        {},
        {"action": "task", "extra": 1},
        {"action": "bogus"},
        {"action": 1},
        {"action": True},
        {"action": "task", "task": 5},
        {"action": "task", "task": None},
        {"action": "task", "task": True},
        {"action": "stop", "reason": 3},
        {"action": "task", "targets": "a.txt"},
        {"action": "task", "targets": ("a.txt",)},
        {"action": "task", "targets": None},
        {"action": "task", "targets": ["a.txt", 1]},
        {"action": "task", "targets": ["a.txt", True]},
        {"action": None},
    ]:

        def send(role: str, payload: dict[str, Any], _bad: Any = bad) -> dict[str, Any]:
            return _bad

        with pytest.raises((ValueError, TypeError)):
            JsonRoles(send).supervisor(_sup_view())
    for bad in [
        {},
        {"changes": "x"},
        {"changes": [1]},
        {"wrong": {}},
        {"changes": {"a": 1}},
        {"changes": {"a": None}},
        {"changes": {1: "x"}},
        {"changes": {}, "extra": 1},
    ]:
        with pytest.raises((ValueError, TypeError)):
            JsonRoles(lambda r, p, _bad=bad: _bad).implementer(_impl_view())
    with pytest.raises((ValueError, TypeError)):
        JsonRoles(lambda r, p: [1, 2]).supervisor(_sup_view())  # type: ignore[return-value]
    with pytest.raises(TypeError):
        JsonRoles(123)  # type: ignore[arg-type]


def test_empty_targets_rejected():
    """Regression: empty string in targets must be rejected, not silently accepted."""
    for bad_targets in [
        [""],
        ["a.txt", ""],
        ["", "b.txt"],
        ["a.txt", "", "c.txt"],
    ]:
        def send(role: str, payload: dict[str, Any], _bad_targets: Any = bad_targets) -> dict[str, Any]:
            return {"action": "task", "task": "do", "targets": _bad_targets}

        with pytest.raises(ValueError):
            JsonRoles(send).supervisor(_sup_view())

    # Empty list is allowed (no targets), but list with empty string is not
    def send_ok(role: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"action": "task", "task": "do", "targets": []}

    decision = JsonRoles(send_ok).supervisor(_sup_view())
    assert decision.targets == ()

    def send_valid(role: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"action": "task", "task": "do", "targets": ["a.txt"]}

    decision2 = JsonRoles(send_valid).supervisor(_sup_view())
    assert decision2.targets == ("a.txt",)


def test_send_called_once_and_propagates():
    calls: list[str] = []

    def send(role: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(role)
        return {"action": "stop"}

    JsonRoles(send).supervisor(_sup_view())
    assert calls == ["supervisor"]

    def boom(role: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("net-down")

    with pytest.raises(RuntimeError):
        JsonRoles(boom).supervisor(_sup_view())
    with pytest.raises(RuntimeError):
        JsonRoles(boom).implementer(_impl_view())


def test_targets_tuple_type():
    def send(role: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"action": "task", "task": "do", "targets": ["a.txt"]}

    decision = JsonRoles(send).supervisor(_sup_view())
    assert type(decision.targets) is tuple and decision.targets == ("a.txt",)
    assert type(decision) is SupervisorDecision


def test_foreign_supervisor_decision_not_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    @dataclasses.dataclass(frozen=True)
    class Counterfeit:
        action: str
        task: str = ""
        targets: tuple = ()
        reason: str = ""

    foreign = types.ModuleType("foreign_collision_mod")
    foreign.SupervisorDecision = Counterfeit  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "foreign_collision_mod", foreign)

    def send(role: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"action": "stop", "reason": "ok"}

    decision = JsonRoles(send).supervisor(_sup_view())
    assert type(decision) is SupervisorDecision
    assert not isinstance(decision, Counterfeit)


def test_non_string_dict_keys_rejected_without_send():
    with pytest.raises(TypeError):
        JsonRoles(_must_not_send).supervisor(_sup_view(snapshot={1: b"x"}))
    with pytest.raises(TypeError):
        JsonRoles(_must_not_send).supervisor(_sup_view(snapshot={(1, 2): b"x"}))
    with pytest.raises(TypeError):
        JsonRoles(_must_not_send).implementer(_impl_view(snapshot={1: b"x"}))


def test_unsupported_values_rejected_without_send():
    with pytest.raises(TypeError):
        JsonRoles(_must_not_send).supervisor(_sup_view(snapshot={"a.txt": {1, 2}}))
    with pytest.raises(TypeError):
        JsonRoles(_must_not_send).supervisor(_sup_view(snapshot={"a.txt": object()}))
    with pytest.raises(TypeError):
        JsonRoles(_must_not_send).supervisor(_sup_view(snapshot={"a.txt": float("nan")}))
    with pytest.raises(TypeError):
        JsonRoles(_must_not_send).supervisor(_sup_view(snapshot={"a.txt": float("inf")}))
    with pytest.raises(TypeError):
        JsonRoles(_must_not_send).implementer(_impl_view(snapshot={"a.txt": {"k"}}))


def test_integration_failed_then_repair_accept(tmp_path: Any) -> None:
    allow = ("a.txt", "b.txt")
    observed: list[tuple] = []

    def host_validator(files: Any) -> ValidationResult:
        if files.get("a.txt") == b"fixed":
            return ValidationResult(True, "ok", "host")
        return ValidationResult(False, "need-fix", "host")

    def send(role: str, payload: dict[str, Any]) -> dict[str, Any]:
        if role == "supervisor":
            assert set(payload.keys()) >= {"objective", "snapshot", "diff"}
            observed.append(
                (
                    payload.get("last_outcome"),
                    payload.get("last_passed"),
                    dict(payload.get("snapshot", {})),
                )
            )
            if payload.get("last_outcome") == "none":
                return {"action": "task", "task": "initial", "targets": ["a.txt"]}
            if payload.get("last_outcome") == "failed" and not payload.get("last_passed"):
                assert "need-fix" in payload.get("last_validation_output", "")
                return {"action": "task", "task": "repair", "targets": ["a.txt"]}
            if payload.get("last_passed"):
                assert payload["snapshot"].get("a.txt") == "fixed"
                return {"action": "accept", "reason": "good"}
            raise AssertionError(f"unexpected supervisor payload {payload.get('last_outcome')}")
        assert set(payload.keys()) >= {"objective", "task", "snapshot"}
        if len(observed) == 1:
            return {"changes": {"a.txt": "broken"}}
        return {"changes": {"a.txt": "fixed"}}

    roles = JsonRoles(send)
    final = run_development_loop(
        workspace=tmp_path,
        objective="fix a.txt",
        allowlist=allow,
        supervisor=roles.supervisor,
        implementer=roles.implementer,
        validator=host_validator,
        request_budget=16,
        max_tasks=4,
        max_repairs=3,
    )
    assert final.status == "accepted"
    assert (tmp_path / "a.txt").read_bytes() == b"fixed"
    assert "failed" in [item[0] for item in observed]
    assert observed[-1][1] is True
