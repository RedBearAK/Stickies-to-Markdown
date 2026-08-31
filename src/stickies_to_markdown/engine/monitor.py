"""
The Engine: owns the watchdog observer, the monitoring lock, the event
queue, the session counters and one worker thread. Every front end drives
one of these.

Threading: watchdog delivers events on its observer thread; the handler
only records "uuid X was touched at time T" in a dict. A single worker
thread drains that dict after each note has been quiet for
debounce_seconds, converting one note at a time. status() and
reload_config_if_changed() are cheap and safe from any thread. Nothing
here touches a UI.

Rules derived from observing Stickies (dev_notes/MAC_FINDINGS.md):

1. A note EXISTS when <uuid>.rtfd is a directory containing TXT.rtf. A
   flat <uuid>.rtfd file is a newborn note - wait. A directory briefly
   missing TXT.rtf is mid-save - wait. The directory vanishing is the
   deletion.
2. Any event under <uuid>.rtfd/ (or on the directory itself) is that
   note's change signal; TXT.rtf is replaced, never rewritten in place, so
   on_modified is not relied on. Attribute changes (color, position)
   rewrite the package too, so many events end as "unchanged" - counted,
   not suppressed.
3. settle 1 s / debounce 3 s fit the observed 0.5 s mid-save gap and
   8+ s autosave spacing.
4. .SavedStickiesState is never a trigger; it is re-read while handling a
   package event, and never trusted for existence.
5. Bursts (16 creations in 3 s) drain through the one worker in order.
"""

import os
import re
import time
import threading

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from stickies_to_markdown.engine import stickies
from stickies_to_markdown.engine.lock import MonitorLock
from stickies_to_markdown.engine.config import Config, ConfigError
from stickies_to_markdown.engine.events import Event, Status, EventQueue
from stickies_to_markdown.engine.logsetup import setup_logging, get_logger
from stickies_to_markdown.engine.processor import NoteProcessor, Counters


WORKER_TICK = 0.25          # seconds between pending-set scans
RETRY_LIMIT = 40            # newborn/mid-save waits before giving up (~10 s)
RECENT_ERROR_SECONDS = 300  # a conversion error keeps the icon yellow this long

_PACKAGE_RE = re.compile(
    r"^([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{12})\.rtfd$")


class EngineError(Exception):
    """start() could not proceed (typically: lock held elsewhere)."""


class _Handler(FileSystemEventHandler):
    """Watchdog callback -> pending set. Runs on the observer thread."""

    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def on_any_event(self, event):
        for path in (getattr(event, "src_path", None), getattr(event, "dest_path", None)):
            if path:
                uuid = self.engine.uuid_for(path)
                if uuid:
                    self.engine.touch(uuid)


class Engine:

    def __init__(self, config=None, event_queue_size=500):
        self.config = config or Config()
        self.events = EventQueue(maxsize=event_queue_size)
        self.counters = Counters()
        self.logger = get_logger()
        self.lock = MonitorLock(self.config.config_dir)

        self._observer = None
        self._worker = None
        self._processor = None
        self._state_lock = threading.RLock()
        self._pending_lock = threading.Lock()
        self._pending = {}              # uuid -> last touch time
        self._retries = {}              # uuid -> waits so far
        self._full_export_requested = False
        self._running = False
        self._monitoring = False
        self._last_error = None
        self._last_error_ts = None
        self._container_readable = True
        self._notes_known = 0
        self._last_export_ts = None
        self._started_at = None
        self._watched_dir = None
        self._logging_signature = None

    # --- lifecycle ---------------------------------------------------------

    def start(self, export_first=True):
        """
        Take the lock, configure logging, probe the container, start the
        observer and the worker. Raises EngineError when another process
        already watches this config. An unreadable container is reported
        (unhealthy, with the reason) rather than fatal, so the icon can
        say so while the user grants access.

        Every phase logs, and any failure unwinds completely (lock
        released, state reset) and re-raises as EngineError - a start
        never leaves a half-initialised engine that reports "stopped"
        while holding the lock.
        """
        with self._state_lock:
            if self._monitoring:
                return
            if not self.lock.acquire():
                raise EngineError(
                    f"the watcher is already running in another process "
                    f"(PID {self.lock.holder_pid() or 'unknown'})")
            try:
                self._ensure_logging()
                self.logger.info("=" * 60)
                self.logger.info(f"Stickies-to-Markdown watcher starting - PID {os.getpid()}")
                self.logger.info(f"Config: '{self.config.config_file}'")
                self.logger.info("Outputs: " + (", ".join(f"'{d}'" for d in self.config.output_dirs()) or "NONE"))
                self.logger.info(f"Dry run: {'ON' if self.config.get('dry_run') else 'off'}")

                self._last_error = None
                self._processor = NoteProcessor(self.config, self.events, self.counters, self.logger)
                self.logger.info("Probing the Stickies container (a permission prompt may appear)...")
                self._probe_container()
                self.logger.info("Container: " + ("readable" if self._container_readable
                                                   else f"NOT readable - {self._last_error}"))
                self._start_observer()
                self._running = True
                self._worker = threading.Thread(target=self._work, name="s2m-worker", daemon=True)
                self._worker.start()
                self._monitoring = True
                self._started_at = time.time()
                if export_first:
                    self._full_export_requested = True
                self.logger.info("Watcher running" + (" (initial export queued)" if export_first else ""))
                self.events.put(Event("started", self.config.stickies_dir(),
                                      "watching" if self._container_readable else "container unreadable"))
            except Exception as error:      # noqa: BLE001 - unwind, then report
                self.logger.error(f"Start failed: {type(error).__name__}: {error}")
                self._running = False
                self._stop_observer()
                self._monitoring = False
                self._processor = None
                self.lock.release()
                if isinstance(error, EngineError):
                    raise
                raise EngineError(f"{type(error).__name__}: {error}") from error

    def stop(self):
        with self._state_lock:
            if not self._monitoring and self._observer is None:
                self.lock.release()
                return
            self._running = False
            self._stop_observer()
            worker = self._worker
            if worker is not None and worker.is_alive():
                worker.join(timeout=10)
            self._worker = None
            self._monitoring = False
            self.lock.release()
            self.logger.info("Watcher stopped")
            self.events.put(Event("stopped"))

    def _start_observer(self):
        directory = self.config.stickies_dir()
        self._observer = Observer()
        self._watched_dir = None
        if os.path.isdir(directory) and self._container_readable:
            try:
                self._observer.schedule(_Handler(self), directory, recursive=True)
                self._watched_dir = directory
                self.logger.info(f"Watching: '{directory}'")
            except OSError as error:
                self._set_error(f"cannot watch {directory}: {error}")
        else:
            self.logger.warning(f"Not watching '{directory}': container unreadable or missing")
        self._observer.start()

    def _stop_observer(self):
        if self._observer is None:
            return
        try:
            self._observer.stop()
            self._observer.join(timeout=5)
        except Exception as error:      # noqa: BLE001
            self.logger.error(f"Error stopping observer: {error}")
        self._observer = None
        self._watched_dir = None

    def _ensure_logging(self):
        signature = (self.config.get("log_file"), self.config.get("log_level"),
                     self.config.get("log_max_size"), self.config.get("log_backup_count"))
        if signature != self._logging_signature:
            self.logger = setup_logging(self.config)
            self._logging_signature = signature

    def _probe_container(self):
        readable, reason = stickies.container_readable(self.config.stickies_dir())
        self._container_readable = readable
        if not readable:
            self._set_error(reason)
        elif self._last_error and not self._last_error.startswith("config unreadable"):
            self._last_error = None

    def _set_error(self, message):
        self._last_error = message
        self._last_error_ts = time.time()
        self.logger.error(message)
        self.events.put(Event("error", self.config.stickies_dir(), message))

    # --- event intake (observer thread) ------------------------------------

    def uuid_for(self, path):
        """The note uuid a container path belongs to, or None."""
        base = self._watched_dir or self.config.stickies_dir()
        try:
            relative = os.path.relpath(path, base)
        except ValueError:
            return None
        head = relative.split(os.sep, 1)[0]
        match = _PACKAGE_RE.match(head)
        return match.group(1).upper() if match else None

    def touch(self, uuid):
        with self._pending_lock:
            self._pending[uuid] = time.time()

    # --- worker thread -----------------------------------------------------

    def _work(self):
        while self._running:
            if self._full_export_requested:
                self._full_export_requested = False
                self._run_full_export()
            for uuid in self._due():
                if not self._running:
                    break
                try:
                    self._handle(uuid)
                except Exception as error:      # noqa: BLE001 - keep the worker alive
                    self._set_error(f"{uuid[:8]}: {type(error).__name__}: {error}")
                    self.counters.bump("errors")
            time.sleep(WORKER_TICK)

    def _due(self):
        debounce = float(self.config.get("debounce_seconds", 3.0))
        now = time.time()
        with self._pending_lock:
            due = [u for u, t in self._pending.items() if now - t >= debounce]
            for uuid in due:
                del self._pending[uuid]
        return sorted(due)

    def _handle(self, uuid):
        processor = self._processor
        stickies_dir = self.config.stickies_dir()
        try:
            notes = stickies.enumerate_notes(stickies_dir, self.logger)
        except OSError as error:
            self._probe_container()
            self._set_error(f"cannot list container: {error}")
            return
        self._notes_known = len(notes)
        note = notes.get(uuid)
        package = os.path.join(stickies_dir, f"{uuid}.rtfd")

        if note is None:
            if os.path.exists(package):
                # Newborn flat file, or a directory mid-save without TXT.rtf.
                waits = self._retries.get(uuid, 0) + 1
                if waits <= RETRY_LIMIT:
                    self._retries[uuid] = waits
                    self.touch(uuid)
                else:
                    self._retries.pop(uuid, None)
                    self.logger.warning(f"{uuid[:8]}: never became a package; giving up")
                return
            self._retries.pop(uuid, None)
            for writer in processor.writers:
                removed = writer.handle_deletions(set(notes), ())
                if removed:
                    self.counters.bump("deleted", len(removed))
            self._last_export_ts = time.time()
            return

        self._retries.pop(uuid, None)
        results = processor.process_note(note, settle=True)
        for writer in processor.excluded_writers(results):
            removed = writer.handle_deletions(set(notes) - {uuid}, {uuid})
            self.counters.bump("excluded", len(removed))
        self._last_export_ts = time.time()

    def _run_full_export(self):
        self._processor.export_all()
        self._notes_known = self._processor.notes_known
        self._last_export_ts = time.time()
        with self._pending_lock:
            self._pending.clear()

    # --- one-shot ----------------------------------------------------------

    def export_once(self, config=None):
        """
        Full export without the watcher. `config` lets a front end pass a
        detached copy (dry run, other output folder). Returns Counters.
        Does not need the lock and does not need monitoring to be running.
        """
        run_config = config or self.config
        self._ensure_logging()
        processor = NoteProcessor(run_config, self.events, Counters(), self.logger)
        counters = processor.export_all()
        self._last_export_ts = time.time()
        return counters

    def request_full_export(self):
        """Ask the running worker to do a full export (TUI: 'Export all now')."""
        self._full_export_requested = True

    # --- status ------------------------------------------------------------

    def observer_alive(self):
        observer = self._observer
        return observer is not None and observer.is_alive()

    def status(self):
        with self._state_lock:
            monitoring = self._monitoring
            alive = self.observer_alive()
            last_error = self._last_error
            if monitoring and not alive:
                last_error = last_error or "observer thread is not running"
            recent_error = (self._last_error_ts is not None
                            and time.time() - self._last_error_ts < RECENT_ERROR_SECONDS)
            healthy = (last_error is None or not recent_error) and \
                      self._container_readable and (alive if monitoring else True)
            if not healthy and last_error is None:
                last_error = "container unreadable"
            holder = None
            if not self.lock.held and self.lock.is_locked_elsewhere():
                holder = self.lock.holder_pid()
            return Status(
                monitoring=monitoring,
                healthy=healthy,
                last_error=last_error if not healthy else None,
                container_readable=self._container_readable,
                notes_known=self._notes_known,
                converted_session=self.counters.converted,
                unchanged_session=self.counters.unchanged,
                deleted_session=self.counters.deleted,
                errors_session=self.counters.errors,
                last_export_ts=self._last_export_ts,
                dry_run=bool(self.config.get("dry_run", False)),
                dropped_events=self.events.dropped,
                lock_holder_pid=holder,
                started_at=self._started_at,
            )

    # --- hot reload --------------------------------------------------------

    def reload_config_if_changed(self):
        """
        Re-read the config file if another process changed it. Restarts
        the observer when the watched folder changed; the processor reads
        the rest live. A parse failure keeps the old config and marks the
        engine unhealthy until the file is readable again.
        """
        if not self.config.changed_on_disk():
            return False
        with self._state_lock:
            old_dir = self.config.stickies_dir()
            old_out = self.config.get("outputs")
            try:
                self.config.reload()
            except ConfigError as error:
                self._set_error(f"config unreadable: {error}")
                return False
            if self._last_error and self._last_error.startswith("config unreadable"):
                self._last_error = None
            self._ensure_logging()
            detail = "settings"
            if self._monitoring:
                if self.config.stickies_dir() != old_dir:
                    self._stop_observer()
                    self._last_error = None
                    self._probe_container()
                    self._start_observer()
                    detail = f"watching {self.config.stickies_dir()}"
                if self.config.get("outputs") != old_out:
                    try:
                        self._processor = NoteProcessor(self.config, self.events,
                                                        self.counters, self.logger)
                        self._full_export_requested = True
                        detail = f"outputs -> {', '.join(self.config.output_dirs())}"
                    except ValueError as error:
                        self._set_error(str(error))
                elif self._processor is not None:
                    self._processor.config = self.config
                    for writer in self._processor.writers:
                        writer.config = self.config
            self.logger.info(f"Config reloaded ({detail})")
            self.events.put(Event("config_reloaded", self.config.config_file, detail))
            return True

    # --- test hook ---------------------------------------------------------

    def _kill_observer_for_test(self):
        """Simulate the observer thread dying. Tests only."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)


# End of file #
