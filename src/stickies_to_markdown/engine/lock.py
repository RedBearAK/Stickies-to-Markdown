"""
Monitoring lock: only one process may watch a given config's folders.

A pidfile in the config directory, locked with fcntl.flock (POSIX) or
msvcrt.locking (Windows). OS-level locks vanish when the holder dies, so a
crashed monitor never leaves a stale lock behind - the PID in the file is
informational, for the "held by PID 1234" message.
"""

import os


LOCK_FILENAME = "monitor.lock"

try:
    import fcntl

    def _try_lock(handle):
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(handle):
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass

except ImportError:                                    # Windows
    import msvcrt

    def _try_lock(handle):
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(handle):
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


class MonitorLock:
    """
    Usage:
        lock = MonitorLock(config_dir)
        if not lock.acquire():
            report(f"held by PID {lock.holder_pid()}")
        ...
        lock.release()
    """

    def __init__(self, config_dir):
        self.path = os.path.join(config_dir, LOCK_FILENAME)
        self._handle = None

    @property
    def held(self):
        return self._handle is not None

    def acquire(self):
        """True if this process now holds the lock."""
        if self._handle is not None:
            return True
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        handle = open(self.path, 'a+')
        if not _try_lock(handle):
            handle.close()
            return False
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()}\n")
            handle.flush()
        except OSError:
            pass
        self._handle = handle
        return True

    def release(self):
        if self._handle is None:
            return
        _unlock(self._handle)
        try:
            self._handle.close()
        except OSError:
            pass
        self._handle = None

    def holder_pid(self):
        """PID recorded in the lock file, or None if unreadable/absent."""
        try:
            with open(self.path, 'r') as handle:
                text = handle.read().strip()
            return int(text) if text else None
        except (OSError, ValueError):
            return None

    def is_locked_elsewhere(self):
        """Probe without keeping the lock. True when another process holds it."""
        if self._handle is not None:
            return False
        if not os.path.exists(self.path):
            return False
        try:
            handle = open(self.path, 'a+')
        except OSError:
            return True
        try:
            if _try_lock(handle):
                _unlock(handle)
                return False
            return True
        finally:
            handle.close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_exc):
        self.release()


# End of file #
