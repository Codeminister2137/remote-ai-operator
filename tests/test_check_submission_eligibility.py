"""Tests for scripts/check_submission_eligibility.py."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_submission_eligibility.py"
ROOT = SCRIPT.parent.parent
PYTHON = sys.executable


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )


def _valid_grant() -> dict:
    return {
        "grant_id": "fixture-123",
        "model": "muse-spark-fixture",
        "expires_at": int(time.time()) + 3600,
        "max_requests": 10,
        "max_input_bytes": 100000,
        "max_output_tokens": 1000,
        "approved_usd": "1.00",
        "input_usd_per_million": "0.50",
        "output_usd_per_million": "0.50",
        "credits_verified": True,
        "local_spending_limit_accepted": True,
    }


def _write(path: Path, data: dict | str) -> None:
    if isinstance(data, dict):
        path.write_text(json.dumps(data), encoding="utf-8")
    else:
        path.write_text(data, encoding="utf-8")


def test_scripted_mode_is_eligible(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    _write(env_file, "OPERATOR_MODE=scripted\n")
    result = _run("--env-file", str(env_file), "--model-mode", "scripted")
    assert result.returncode == 0
    assert "ELIGIBLE" in result.stdout


def test_meta_mode_with_valid_grant_is_eligible(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    grant_file = tmp_path / "grant.json"
    _write(env_file, "OPERATOR_MODE=meta\nMETA_API_KEY=secret\n")
    _write(grant_file, _valid_grant())
    result = _run(
        "--env-file", str(env_file), "--grant-file", str(grant_file), "--model-mode", "meta"
    )
    assert result.returncode == 0
    assert "ELIGIBLE" in result.stdout


def test_meta_mode_with_invalid_model_is_not_eligible(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    grant_file = tmp_path / "grant.json"
    grant = _valid_grant()
    grant["model"] = "gpt-4"
    _write(env_file, "OPERATOR_MODE=meta\n")
    _write(grant_file, grant)
    result = _run(
        "--env-file", str(env_file), "--grant-file", str(grant_file), "--model-mode", "meta"
    )
    assert result.returncode == 1
    assert "NOT ELIGIBLE" in result.stdout
    assert "muse-<identifier>" in result.stdout


def test_meta_mode_with_contributor_model_is_not_eligible(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    grant_file = tmp_path / "grant.json"
    grant = _valid_grant()
    grant["model"] = "muse-spark-contributor"
    _write(env_file, "OPERATOR_MODE=meta\n")
    _write(grant_file, grant)
    result = _run(
        "--env-file", str(env_file), "--grant-file", str(grant_file), "--model-mode", "meta"
    )
    assert result.returncode == 1
    assert "contributor model" in result.stdout


def test_meta_mode_with_expired_grant_is_not_eligible(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    grant_file = tmp_path / "grant.json"
    grant = _valid_grant()
    grant["expires_at"] = 1
    _write(env_file, "OPERATOR_MODE=meta\n")
    _write(grant_file, grant)
    result = _run(
        "--env-file", str(env_file), "--grant-file", str(grant_file), "--model-mode", "meta"
    )
    assert result.returncode == 1
    assert "expired" in result.stdout


def test_env_with_non_meta_provider_key_is_not_eligible(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    grant_file = tmp_path / "grant.json"
    _write(env_file, "OPERATOR_MODE=meta\nOPENAI_API_KEY=secret\n")
    _write(grant_file, _valid_grant())
    result = _run(
        "--env-file", str(env_file), "--grant-file", str(grant_file), "--model-mode", "meta"
    )
    assert result.returncode == 1
    assert "OPENAI_API_KEY" in result.stdout


def test_meta_mode_requires_grant_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    _write(env_file, "OPERATOR_MODE=meta\n")
    result = _run("--env-file", str(env_file), "--model-mode", "meta")
    assert result.returncode != 0
    assert "grant-file" in result.stderr or "required" in result.stderr.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
