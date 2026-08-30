"""
Events and status snapshots: the only channel from the engine to a front end.

The engine PUTS, front ends GET. The queue is bounded and never blocks the
producer: when a front end stops draining, the oldest events are dropped and
the drop count is exposed through Status so nobody mistakes silence for calm.
Exporting must never wait on a UI.

Structure carried over from Duplicate-File-Preventer; kinds and Status
fields are this tool's (handoff §5.4).
"""

import time
import queue
import threading


EVENT_KINDS = (
    "started", "stopped", "converted", "unchanged", "deleted", "excluded",
    "conflict", "scanned", "config_reloaded", "error",
)


class Event:
    """One thing that happened. Immutable by convention."""

    __slots__ = ("kind", "path", "detail", "ts")

    def __init__(self, kind, path="", detail="", ts=None):
        if kind not in EVENT_KINDS:
            raise ValueError(f"Unknown event kind: {kind!r}")
        self.kind = kind
        self.path = path
        self.detail = detail
        self.ts = time.time() if ts is None else ts

    def __repr__(self):
        return f"Event({self.kind!r}, path={self.path!r}, detail={self.detail!r})"

    def time_str(self, fmt="%H:%M:%S"):
        return time.strftime(fmt, time.localtime(self.ts))


class Status:
    """Cheap snapshot of engine state, safe to read from any thread."""

    __slots__ = (
        "monitoring", "healthy", "last_error", "container_readable",
        "notes_known", "converted_session", "unchanged_session",
        "deleted_session", "errors_session", "last_export_ts",
        "dry_run", "dropped_events", "lock_holder_pid", "started_at",
    )

    def __init__(self, monitoring=False, healthy=True, last_error=None,
                 container_readable=True, notes_known=0, converted_session=0,
                 unchanged_session=0, deleted_session=0, errors_session=0,
                 last_export_ts=None, dry_run=False, dropped_events=0,
                 lock_holder_pid=None, started_at=None):
        self.monitoring = monitoring
        self.healthy = healthy
        self.last_error = last_error
        self.container_readable = container_readable
        self.notes_known = notes_known
        self.converted_session = converted_session
        self.unchanged_session = unchanged_session
        self.deleted_session = deleted_session
        self.errors_session = errors_session
        self.last_export_ts = last_export_ts
        self.dry_run = dry_run
        self.dropped_events = dropped_events
        self.lock_holder_pid = lock_holder_pid
        self.started_at = started_at

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__slots__}


class EventQueue:
    """
    Bounded FIFO. put() never blocks and never raises; a full queue drops
    the oldest entry and counts the drop.
    """

    def __init__(self, maxsize=500):
        self._queue = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self.dropped = 0

    def put(self, event):
        with self._lock:
            while True:
                try:
                    self._queue.put_nowait(event)
                    return
                except queue.Full:
                    try:
                        self._queue.get_nowait()
                        self.dropped += 1
                    except queue.Empty:
                        pass

    def get_nowait(self):
        """Raises queue.Empty when nothing is waiting."""
        return self._queue.get_nowait()

    def drain(self):
        """Every waiting event, oldest first. Never blocks."""
        events = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                return events

    def qsize(self):
        return self._queue.qsize()


# End of file #
