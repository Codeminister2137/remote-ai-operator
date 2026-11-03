"""Server-selected per-principal task stores,independent of login provider."""

import hashlib
import re
import threading
from pathlib import Path

from remote_operator.control import Operator
from remote_operator.credentials import valid_subject

_WORKSPACE = re.compile(r"[0-9a-f]{64}")
# In-memory subject directory names only; evicted entries re-record on the
# subject's next request and drain subject-blind until then.
_MAX_KNOWN_SUBJECTS = 4096


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
        self._subjects = {}
        self._subjects_lock = threading.Lock()

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
        with self._subjects_lock:
            self._subjects.pop(directory.name, None)
            self._subjects[directory.name] = subject
            while len(self._subjects) > _MAX_KNOWN_SUBJECTS:
                self._subjects.pop(next(iter(self._subjects)))
        return Operator(directory, self.seed, self.validator_factory(), durable=self.durable)

    def subject_for_directory(self, directory):
        """Return the in-process subject for a workspace dir, or None.

        The mapping is one-way on disk (sha256 of the subject), so only
        subjects resolved through :meth:`for_subject` in this process are
        known. Unknown directories (e.g. recovered after a restart before the
        subject reconnects) drain without subject-bound grants or keys.
        """
        with self._subjects_lock:
            return self._subjects.get(Path(directory).name)

    def operator_for_directory(self, directory):
        """Return an isolated Operator for a direct child workspace directory.

        Used by the hosted drain sweeper, which enumerates workspace
        directories rather than subjects. Only ``sha256(subject)``-shaped
        child names are admitted; anything else (sidecar files, stray
        directories) is refused, as are symlinked workspaces.
        """
        path = Path(directory)
        if path.parent != self.root or not _WORKSPACE.fullmatch(path.name):
            raise ValueError("Unknown principal workspace")
        for candidate in (self.root, path):
            if candidate.is_symlink() or candidate.is_junction():
                raise ValueError("Linked principal workspace refused")
        return Operator(path, self.seed, self.validator_factory(), durable=self.durable)
