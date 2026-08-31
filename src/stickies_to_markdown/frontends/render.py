"""
Text rendering shared by the CLI and TUI: log-line coloring, event lines,
status summaries, and a pure-Python log follower (tail -f without tail).
Carried over from Duplicate-File-Preventer with this tool's vocabulary.
"""

import os
import time

from datetime import datetime, timezone

from rich.markup import escape


def log_line_style(line):
    """Rich style name for a log line, or None for plain."""
    if "ERROR" in line or "FAILED" in line:
        return "red"
    if "WARNING" in line:
        return "yellow"
    if "Wrote" in line or "converted" in line:
        return "green"
    if "unchanged" in line:
        return "dim"
    if "DRY RUN" in line:
        return "cyan"
    return None


def log_line_markup(line):
    style = log_line_style(line)
    text = escape(line.rstrip("\n"))
    return f"[{style}]{text}[/{style}]" if style else text


EVENT_STYLES = {
    "converted": "green", "unchanged": "dim", "deleted": "yellow",
    "excluded": "yellow", "conflict": "magenta", "error": "red",
    "config_reloaded": "magenta", "started": "green", "stopped": "yellow",
    "scanned": "blue",
}

EVENT_LABELS = {
    "converted": "converted", "unchanged": "unchanged", "deleted": "deleted",
    "excluded": "excluded", "conflict": "CONFLICT", "error": "ERROR",
    "config_reloaded": "config reloaded", "started": "started",
    "stopped": "stopped", "scanned": "export done",
}


def event_line(event, width=None):
    """One-line description of an event, plain text."""
    label = EVENT_LABELS.get(event.kind, event.kind)
    name = f"'{os.path.basename(event.path)}'" if event.path else ""
    parts = [event.time_str(), f"{label:<15}", name, event.detail]
    text = "  ".join(p for p in parts if p)
    if width and len(text) > width:
        text = text[:width - 1] + "…"
    return text


def event_markup(event, width=None):
    style = EVENT_STYLES.get(event.kind)
    text = escape(event_line(event, width))
    return f"[{style}]{text}[/{style}]" if style else text


def status_summary(status, last_event=None):
    """
    Two short plain-text lines for a menu header:
        ● Watching — 12 notes, 3 converted this session
          last: grocery-list--11111111.md converted  14:02:11
    """
    if not status.healthy:
        first = f"Problem: {status.last_error or 'see log'}"
    elif status.monitoring:
        first = (f"Watching — {status.notes_known} notes, "
                 f"{status.converted_session} converted, "
                 f"{status.unchanged_session} unchanged this session")
    elif status.lock_holder_pid:
        first = f"Stopped here — the watcher is running in another process (PID {status.lock_holder_pid})"
    else:
        first = "Stopped"

    if status.dry_run:
        first += " (DRY RUN)"
    if status.dropped_events:
        first += f" [{status.dropped_events} events dropped]"

    second = f"last: {event_line(last_event)}" if last_event else ""
    return first, second


def uptime_str(started_at):
    if not started_at:
        return "-"
    seconds = int(time.time() - started_at)
    clock = datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%H:%M:%S")
    return clock if seconds < 86400 else f"{seconds // 86400}d {clock}"


def tail_lines(path, count):
    """Last `count` lines of a text file (whole-file read; logs are capped)."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            return handle.readlines()[-count:]
    except OSError:
        return []


def follow_log(path, on_line, should_stop, initial_lines=1000, poll=0.5):
    """
    Pure-Python tail -f. Calls on_line(text) for the last `initial_lines`
    and then for every new line until should_stop() is true. Handles
    rotation (file replaced/truncated) by reopening.
    """
    for line in tail_lines(path, initial_lines):
        on_line(line.rstrip("\n"))

    def open_at_end():
        handle = open(path, 'r', encoding='utf-8', errors='replace')
        handle.seek(0, os.SEEK_END)
        return handle, os.fstat(handle.fileno()).st_ino

    try:
        handle, inode = open_at_end()
    except OSError:
        handle, inode = None, None

    try:
        while not should_stop():
            if handle is None:
                try:
                    handle, inode = open_at_end()
                except OSError:
                    time.sleep(poll)
                    continue
            line = handle.readline()
            if line:
                on_line(line.rstrip("\n"))
                continue
            try:
                current = os.stat(path)
                rotated = current.st_ino != inode or current.st_size < handle.tell()
            except OSError:
                rotated = True
            if rotated:
                handle.close()
                handle = None
                continue
            time.sleep(poll)
    finally:
        if handle is not None:
            handle.close()


# End of file #
