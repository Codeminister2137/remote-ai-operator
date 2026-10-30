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
        """Initialize scoped operator resolver.

        Args:
            root: Base directory under which per-subject workspaces are created.
            seed: Path to seed material used to initialize new operator stores.
            validator_factory: Callable returning a validator for operator actions.
            durable: Optional durable storage handle forwarded to Operator.
        """
        self.root = root.absolute()
        self.seed = seed
        self.validator_factory = validator_factory
        self.durable = durable

    def for_subject(self, subject):
        """Return an isolated Operator for the given authenticated subject.

        Validates the subject identifier, derives a deterministic workspace
        directory from its hash, rejects symlinked workspaces, and returns
        an Operator bound to that directory.

        Args:
            subject: Opaque authenticated subject identifier (non-empty string).

        Returns:
            An Operator instance scoped to the subject's isolated directory.

        Raises:
            ValueError: If subject is invalid or workspace path is a symlink/junction.
        """
        valid_subject(subject)
        directory = self.root / hashlib.sha256(subject.encode()).hexdigest()
        # The deployment may mount a volume through a symlink (Modal does), so only
        # the caller-selected root and the subject directory must be link-free.
        for path in (self.root, directory):
            if path.is_symlink() or path.is_junction():
                raise ValueError("Linked principal workspace refused")
        return Operator(directory, self.seed, self.validator_factory(), durable=self.durable)
