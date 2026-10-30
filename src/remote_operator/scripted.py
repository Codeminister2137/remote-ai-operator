"""Explicit offline reference actor; never presented as model-authored coding."""

from remote_operator.control import SOURCE, Operation

REFERENCE = '''"""CSV import for the demonstration issue tracker."""
import csv
import io


def import_issues(text, existing=()):
    """Validate rows, retaining order and rejecting duplicate accepted IDs."""
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != ["id", "title", "status"]:
        raise ValueError("Expected id,title,status headers")
    seen = {issue["id"] for issue in existing}
    issues, errors = [], []
    for row in reader:
        number = reader.line_num
        values = [row.get(name) for name in ("id", "title", "status")]
        if None in row or any(value is None for value in values):
            errors.append({"row": number, "message": "Expected three fields"})
            continue
        identifier, title, status = (value.strip() for value in values)
        status = status.lower()
        if not identifier or not title or status not in {"open", "closed"}:
            errors.append({"row": number, "message": "Invalid issue fields"})
        elif identifier in seen:
            errors.append({"row": number, "message": "Duplicate ID"})
        else:
            seen.add(identifier)
            issues.append({"id": identifier, "title": title, "status": status})
    return {"issues": issues, "errors": errors}
'''

ALLOWED_ACTIONS = {"read_file", "write_file", "validate", "finish"}


def _validate_history_actions(history):
    for entry in history:
        name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", None)
        if name is not None and name not in ALLOWED_ACTIONS:
            raise ValueError(f"unknown action: {name}")


class ScriptedModel:
    """Exercise actual controller/tools using a transparent fixed reference."""

    name = "SCRIPTED OFFLINE REFERENCE (not Meta inference)"

    def next_operation(self, history, *, prompt):
        _validate_history_actions(history)
        names = [entry["name"] for entry in history]
        if not names:
            return Operation("read_file", {"path": SOURCE})
        if "write_file" not in names:
            return Operation("write_file", {"path": SOURCE, "content": REFERENCE})
        if "validate" not in names:
            return Operation("validate", {})
        return Operation("finish", {})
