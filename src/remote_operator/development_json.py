"""Reusable JSON role adapters for the foreground development loop."""

from __future__ import annotations

import dataclasses
import logging
import math
from collections.abc import Callable
from typing import Any

from remote_operator.development import ImplementerView, SupervisorDecision, SupervisorView

SendFn = Callable[[str, dict[str, Any]], dict[str, Any]]

_ALLOWED_ACTIONS = ("task", "accept", "stop", "decision_needed")
_SUPERVISOR_KEYS = frozenset(("action", "task", "targets", "reason"))

logger = logging.getLogger(__name__)


def _is_strict_str(value: Any) -> bool:
    return type(value) is str


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is str:
        return value
    if type(value) is float:
        if math.isnan(value) or math.isinf(value):
            raise TypeError("unsupported float value")
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"invalid utf-8: {exc}") from exc
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"non-string dict key: {type(key).__name__}")
            out[key] = _to_jsonable(item)
        return out
    if type(value) is list or type(value) is tuple:
        return [_to_jsonable(item) for item in value]
    raise TypeError(f"unsupported value type: {type(value).__name__}")


def _payload_for_view(view: Any, expected: type) -> dict[str, Any]:
    if type(view) is not expected:
        raise TypeError(f"view must be exactly {expected.__name__}")
    raw = dataclasses.asdict(view)
    converted = _to_jsonable(raw)
    assert isinstance(converted, dict)
    return converted


def _reject(exc: Exception) -> None:
    logger.debug("JsonRoles rejected malformed response: %s", exc)
    raise exc


class JsonRoles:
    """Adapt controller views to JSON dicts via injected send(role, payload)."""

    def __init__(self, send: SendFn) -> None:
        if not callable(send):
            raise TypeError("send must be callable")
        self._send = send

    def supervisor(self, view: SupervisorView) -> SupervisorDecision:
        payload = _payload_for_view(view, SupervisorView)
        result = self._send("supervisor", payload)
        try:
            if not isinstance(result, dict):
                raise TypeError("supervisor response must be a dict")
            unknown = set(result.keys()) - set(_SUPERVISOR_KEYS)
            if unknown:
                raise ValueError(f"unknown supervisor fields: {sorted(unknown)}")
            if "action" not in result:
                raise ValueError("supervisor response missing action")
            action = result["action"]
            if not _is_strict_str(action):
                raise TypeError("action must be a string")
            if action not in _ALLOWED_ACTIONS:
                raise ValueError(f"unsupported action: {action!r}")
            task: Any = ""
            reason: Any = ""
            if "task" in result:
                task = result["task"]
                if not _is_strict_str(task):
                    raise TypeError("task must be a string")
            if "reason" in result:
                reason = result["reason"]
                if not _is_strict_str(reason):
                    raise TypeError("reason must be a string")
            targets_tuple: tuple[str, ...] = ()
            if "targets" in result:
                targets = result["targets"]
                if type(targets) is not list:
                    raise TypeError("targets must be list[str]")
                for item in targets:
                    if not _is_strict_str(item):
                        raise TypeError("targets must be list[str]")
                    if item == "":
                        raise ValueError("targets must be non-empty strings")
                targets_tuple = tuple(targets)
            return SupervisorDecision(action=action, task=task, targets=targets_tuple, reason=reason)
        except (ValueError, TypeError) as exc:
            _reject(exc)
            raise  # unreachable, _reject always raises

    def implementer(self, view: ImplementerView) -> dict[str, str]:
        payload = _payload_for_view(view, ImplementerView)
        result = self._send("implementer", payload)
        try:
            if not isinstance(result, dict):
                raise TypeError("implementer response must be a dict")
            if set(result.keys()) != {"changes"}:
                raise ValueError("implementer response must contain exactly 'changes'")
            changes = result["changes"]
            if not isinstance(changes, dict):
                raise TypeError("changes must be dict[str,str]")
            for key, val in changes.items():
                if not _is_strict_str(key) or not _is_strict_str(val):
                    raise TypeError("changes must be dict[str,str]")
            return dict(changes)
        except (ValueError, TypeError) as exc:
            _reject(exc)
            raise
