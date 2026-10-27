"""Server-selected per-principal task stores,independent of login provider."""

import hashlib
from pathlib import Path

from remote_operator.control import Operator
from remote_operator.credentials import valid_subject


class ScopedOperators:
    """Resolve an already-authenticated opaque subject to an isolated local store.

    Callers must authenticate before invoking this service. This is not a public
    signup API,a distributed lock or a Modal persistence implementation.
    """

    def __init__(self, root: Path, seed: Path, validator_factory, *, durable=None):
        self.root = root.absolute()
        self.seed = seed
        self.validator_factory = validator_factory
        self.durable = durable

    def for_subject(self, subject):
        valid_subject(subject)
        directory = self.root / hashlib.sha256(subject.encode()).hexdigest()
        # The deployment may mount a volume through a symlink (Modal does), so only
        # the caller-selected root and the subject directory must be link-free.
        for path in (self.root, directory):
            if path.is_symlink() or path.is_junction():
                raise ValueError("Linked principal workspace refused")
        return Operator(directory, self.seed, self.validator_factory(), durable=self.durable)
