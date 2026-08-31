"""
The one code path that takes a note from .rtfd to mirror file, for both the
one-shot export (--once, Phase 1) and the live watcher (Phase 2).

Output goes to the log and the event queue. Never to a terminal.
"""

import os
import re
import time
import threading

from stickies_to_markdown.engine import stickies
from stickies_to_markdown.engine.writer import Writer
from stickies_to_markdown.engine.events import Event
from stickies_to_markdown.engine.convert import (
    convert, ConversionError, first_content_line)
from stickies_to_markdown.engine.logsetup import get_logger


class Counters:
    """Session counters, safe to bump from any thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self.converted = 0
        self.unchanged = 0
        self.deleted = 0
        self.excluded = 0
        self.errors = 0

    def bump(self, name, amount=1):
        with self._lock:
            setattr(self, name, getattr(self, name) + amount)

    def as_dict(self):
        return {"converted": self.converted, "unchanged": self.unchanged,
                "deleted": self.deleted, "excluded": self.excluded,
                "errors": self.errors}


# A filesystem event fires when a write begins, not when the writer is done,
# and Stickies may replace the whole package via temp-and-rename. Settle on
# the PACKAGE: newest mtime of the directory and everything in it.
SETTLE_INTERVAL = 0.25
SETTLE_TIMEOUT = 60.0


def _package_signature(rtfd_path):
    newest = 0.0
    total = 0
    try:
        for root, _dirs, files in os.walk(rtfd_path):
            for name in files:
                stat_info = os.stat(os.path.join(root, name))
                newest = max(newest, stat_info.st_mtime)
                total += stat_info.st_size
    except OSError:
        return None
    return (newest, total)


def wait_until_stable(rtfd_path, settle_seconds=1.0,
                      interval=SETTLE_INTERVAL, timeout=SETTLE_TIMEOUT):
    """
    Block until the package signature has held still for settle_seconds,
    or the package vanishes, or timeout. True when stable.
    """
    deadline = time.time() + timeout
    stable_since = None
    last = None
    while time.time() < deadline:
        sample = _package_signature(rtfd_path)
        if sample is None:
            return False
        now = time.time()
        if sample != last:
            last = sample
            stable_since = now
        elif now - stable_since >= settle_seconds:
            return True
        time.sleep(interval)
    return False


class NoteProcessor:

    def __init__(self, config, events, counters=None, logger=None):
        self.config = config
        self.events = events
        self.counters = counters or Counters()
        self.logger = logger or get_logger()
        self.writer = Writer(config, events, self.logger)
        self.notes_known = 0

    def is_excluded(self, note, markdown=None):
        """
        Reactive exclusion: by colour (checked before conversion) or by a
        regex on the first line (needs the converted text). Returns the
        matching reason or "".
        """
        colors = [str(c).lower() for c in (self.config.get("exclude_colors") or [])]
        if note.color in colors:
            return f"colour {note.color}"
        pattern = self.config.get("exclude_title_regex") or ""
        if pattern and markdown is not None:
            first = first_content_line(markdown)
            try:
                if re.search(pattern, first):
                    return f"title matches {pattern!r}"
            except re.error as error:
                self.logger.error(f"exclude_title_regex invalid: {error}")
        return ""

    def process_note(self, note, settle=False):
        """
        Convert one note and bring its mirror file up to date. Returns the
        Event kind, or "excluded" (the caller then applies on_exclude to
        any mirror file the note left behind).
        """
        if self.is_excluded(note):
            return "excluded"
        if settle and not wait_until_stable(
                note.rtfd_path, float(self.config.get("settle_seconds", 1.0))):
            self.events.put(Event("error", note.rtfd_path, "never settled"))
            self.counters.bump("errors")
            return "error"
        try:
            markdown, attachments, body_format = convert(
                note.rtfd_path, self.config.get("converter", "auto"), self.logger,
                code_block_min=int(self.config.get("code_block_min_escapes", 6)),
                code_block_density=float(self.config.get("code_block_density", 4.0)))
        except ConversionError as error:
            self.logger.error(str(error))
            self.events.put(Event("error", note.rtfd_path, str(error)))
            self.counters.bump("errors")
            return "error"
        if self.is_excluded(note, markdown):
            return "excluded"
        kind = self.writer.export_note(note, markdown, attachments, body_format)
        if kind in ("converted", "unchanged"):
            self.counters.bump(kind)
        else:
            self.counters.bump("errors")
        return kind

    def export_all(self):
        """
        Full one-way export: every note, then the deletion pass. Returns
        the counters. The container itself is never written to.
        """
        stickies_dir = self.config.stickies_dir()
        readable, reason = stickies.container_readable(stickies_dir)
        if not readable:
            self.logger.error(f"Container unreadable: {reason}")
            self.events.put(Event("error", stickies_dir, reason))
            self.counters.bump("errors")
            return self.counters

        notes = stickies.enumerate_notes(stickies_dir, self.logger)
        self.notes_known = len(notes)
        self.writer.refresh_index()
        self.logger.info(f"Export start: {len(notes)} notes in {stickies_dir}")
        excluded = set()
        for note in sorted(notes.values(), key=lambda n: n.uuid):
            if self.process_note(note) == "excluded":
                excluded.add(note.uuid)
        live = set(notes.keys()) - excluded
        removed = self.writer.handle_deletions(live, excluded)
        self.counters.bump("deleted", len(removed) - len(
            [n for n in removed if n in self.writer.last_excluded]))
        self.counters.bump("excluded", len(self.writer.last_excluded))
        self.events.put(Event("scanned", stickies_dir,
                              f"{len(notes)} notes; {self.counters.as_dict()}"))
        self.logger.info(f"Export complete: {self.counters.as_dict()}")
        return self.counters


# End of file #
