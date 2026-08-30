#!/usr/bin/env python3
"""Container enumeration and defensive .SavedStickiesState parsing."""

import os

from _helpers import Sandbox, check, run_suite

from stickies_to_markdown.engine import stickies


def test_enumerates_all_packages():
    with Sandbox() as box:
        notes = stickies.enumerate_notes(str(box.container))
        ok = check(len(notes) == 7, "all 7 fixture packages found",
                   f"found {len(notes)}: {sorted(notes)}")
        note = notes.get("11111111-AAAA-4AAA-8AAA-111111111111")
        ok &= check(note is not None and note.uuid8 == "11111111",
                    "uuid8 short id derived", f"note: {note!r}")
        return ok


def test_colors_from_state_file():
    with Sandbox() as box:
        notes = stickies.enumerate_notes(str(box.container))
        colors = {n.uuid8: n.color for n in notes.values()}
        ok = check(colors.get("11111111") == "yellow" and
                   colors.get("22222222") == "blue" and
                   colors.get("66666666") == "gray",
                   "integer colours mapped through the palette",
                   f"colours: {colors}")
        return ok


def test_missing_state_file_is_fine():
    with Sandbox(with_state=False) as box:
        notes = stickies.enumerate_notes(str(box.container))
        ok = check(len(notes) == 7, "notes enumerated without a state file",
                   f"found {len(notes)}")
        ok &= check(all(n.color == "unknown" for n in notes.values()),
                    "colour falls back to 'unknown'",
                    f"{ {n.uuid8: n.color for n in notes.values()} }")
        return ok


def test_truncated_state_file_is_fine():
    with Sandbox() as box:
        state_path = box.container / ".SavedStickiesState"
        state_path.write_bytes(state_path.read_bytes()[:20])   # simulate 2019 bug
        notes = stickies.enumerate_notes(str(box.container))
        return check(len(notes) == 7 and
                     all(n.color == "unknown" for n in notes.values()),
                     "truncated state file does not block enumeration",
                     f"{len(notes)} notes")


def test_non_note_entries_ignored():
    with Sandbox() as box:
        (box.container / "not-a-uuid.rtfd").mkdir()
        (box.container / "stray.txt").write_text("x")
        empty_pkg = box.container / "99999999-9999-4999-8999-999999999999.rtfd"
        empty_pkg.mkdir()      # no TXT.rtf inside
        notes = stickies.enumerate_notes(str(box.container))
        return check(len(notes) == 7,
                     "non-UUID names, files and RTF-less packages skipped",
                     f"found {len(notes)}: {sorted(notes)}")


def test_container_probe():
    with Sandbox() as box:
        readable, _ = stickies.container_readable(str(box.container))
        ok = check(readable, "readable container probes True", "probe failed")
        missing, reason = stickies.container_readable(str(box.root / "nope"))
        ok &= check(not missing and "not found" in reason,
                    "missing container probes False with a reason",
                    f"got {missing}, {reason!r}")
        if os.geteuid() != 0:      # root ignores modes; skip there
            locked = box.root / "locked"
            locked.mkdir(mode=0o000)
            try:
                denied, reason = stickies.container_readable(str(locked))
                ok &= check(not denied and "Full Disk Access" in reason,
                            "PermissionError probes False naming the FDA fix",
                            f"got {denied}, {reason!r}")
            finally:
                locked.chmod(0o755)
        return ok


if __name__ == "__main__":
    tests = [test_enumerates_all_packages, test_colors_from_state_file,
             test_missing_state_file_is_fine, test_truncated_state_file_is_fine,
             test_non_note_entries_ignored, test_container_probe]
    exit(0 if run_suite("stickies container tests", tests) else 1)


# End of file #
