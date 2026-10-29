"""Validate that a Remote Operator demo configuration satisfies the Meta-only rule.

Usage:
    python scripts/check_submission_eligibility.py \
        --env-file .env \
        --grant-file private/grant.json \
        --model-mode meta

Exit codes:
    0 - configuration satisfies the Meta-only submission rule
    1 - one or more violations were found
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from remote_operator.meta import InferenceGrant


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith("export") and len(line) > 6 and line[6] in (" ", "\t"):
            line = line[6:].lstrip(" \t")
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _load_grant(path: Path) -> InferenceGrant:
    data = json.loads(path.read_text(encoding="utf-8"))
    return InferenceGrant(**data)


def _check_model_name(model: str) -> list[str]:
    violations: list[str] = []
    if not re.fullmatch(r"muse-[a-z0-9.-]+", model):
        violations.append(
            "Grant model does not match the required Meta model pattern 'muse-<identifier>'."
        )
    if "contributor" in model:
        violations.append("Grant model is a contributor model and is refused.")
    return violations


def _check_grant_eligibility(grant: InferenceGrant) -> list[str]:
    try:
        grant.check()
    except ValueError as exc:
        if isinstance(grant.model, str):
            model_violations = _check_model_name(grant.model)
            if model_violations:
                return model_violations
        else:
            return [
                "Grant model does not match the required Meta model pattern 'muse-<identifier>'."
            ]
        return [f"Grant fails runtime validation: {exc}"]
    except (TypeError, ArithmeticError):
        return ["Grant fails runtime validation: malformed field types."]
    return []


def _check_env(env: dict[str, str]) -> list[str]:
    violations: list[str] = []
    non_meta_keys = {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "COHERE_API_KEY",
        "AZURE_OPENAI_API_KEY",
    }
    for key in sorted(non_meta_keys):
        if env.get(key):
            violations.append(f"Non-Meta provider credential {key} is set.")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--grant-file", type=Path)
    parser.add_argument("--model-mode", choices=("scripted", "meta"), required=True)
    args = parser.parse_args(argv)

    report_lines: list[str] = [
        "# Submission eligibility check",
        "",
        f"- model-mode: `{args.model_mode}`",
        f"- env-file: `{args.env_file}`",
    ]
    if args.grant_file:
        report_lines.append(f"- grant-file: `{args.grant_file}`")
    report_lines.append("")

    violations: list[str] = []

    try:
        env = _parse_env(args.env_file)
    except (OSError, UnicodeError):
        _print_report(report_lines, ["Could not read env file."])
        return 1
    violations.extend(_check_env(env))

    if args.model_mode == "scripted":
        report_lines.append(
            "Scripted mode calls no external model, so the Meta-only rule is satisfied."
        )
    else:
        if not args.grant_file:
            parser.error("--grant-file is required when --model-mode is meta")
        try:
            grant = _load_grant(args.grant_file)
        except (OSError, UnicodeError, TypeError, ValueError):
            _print_report(report_lines, ["Grant loading failed: unreadable or malformed file."])
            return 1

        grant_violations = _check_grant_eligibility(grant)
        violations.extend(grant_violations)
        if not grant_violations:
            report_lines.append(f"- grant model: `{grant.model}`")

    _print_report(report_lines, violations)
    return 1 if violations else 0


def _print_report(lines: list[str], violations: list[str]) -> None:
    if violations:
        lines.append("## Violations")
        for violation in violations:
            lines.append(f"- [FAIL] {violation}")
        lines.append("")
        lines.append("Result: **NOT ELIGIBLE**")
    else:
        lines.append("Result: **ELIGIBLE**")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
