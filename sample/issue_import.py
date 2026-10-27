"""CSV import exercise for the demonstration issue tracker."""


def import_issues(text, existing=()):
    """Import CSV without mutating existing issue dictionaries.

    Return {"issues": [...], "errors": [...]}, never a tuple. Accepted issues
    have string id, title, status. Errors have integer row and string message.
    Row is the physical CSV record-ending line number. Empty input or invalid
    headers raise ValueError.
    """
    raise NotImplementedError("Implement the documented CSV import feature")
