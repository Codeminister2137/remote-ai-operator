"""Cross-process exclusion for one explicitly started local controller."""

import os
import threading


class ProcessLock:
    """Refuse competing CLI/server actions rather than sharing live execution."""

    def __init__(self, path):
        self.path = path
        self.thread = threading.Lock()
        self.stream = None

    def acquire(self, blocking=False):
        if not self.thread.acquire(blocking=blocking):
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.stream = self.path.open("a+b")
            if self.path.stat().st_size == 0:
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if self.stream:
                self.stream.close()
                self.stream = None
            self.thread.release()
            return False

    def release(self):
        if self.stream is not None:
            try:
                self.stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            finally:
                self.stream.close()
                self.stream = None
                self.thread.release()

    def __enter__(self):
        if not self.acquire():
            raise ValueError("Another operator action is active")
        return self

    def __exit__(self, *args):
        self.release()
