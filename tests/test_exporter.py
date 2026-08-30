#!/usr/bin/env python3
"""
Phase 1 acceptance (handoff §8): full export produces the mirror; a second
run writes nothing; a modified fixture rewrites exactly one file; a removed
fixture produces a tombstone; the container tree is unchanged afterwards.
Plus: conflict quarantine, unmarked-file safety, read-only mode, flavors.
"""

import os
import stat

from _helpers import Sandbox, check, run_suite

from stickies_to_markdown.engine.events import EventQueue
from stickies_to_markdown.engine.logsetup import setup_logging
from stickies_to_markdown.engine.processor import NoteProcessor
from stickies_to_markdown.engine.writer import split_front_matter


def _export(box):
    setup_logging(box.config)
    processor = NoteProcessor(box.config, EventQueue())
    return processor.export_all()


def test_full_export_and_front_matter():
    with Sandbox() as box:
        counters = _export(box)
        files = box.mirror_files()
        ok = check(counters.converted == 7 and counters.errors == 0,
                   "7 notes converted, 0 errors", f"{counters.as_dict()}")
        ok &= check(len(files) == 7, "7 mirror files written",
                    f"{[f.name for f in files]}")
        grocery = [f for f in files if f.name.startswith("grocery-list--11111111")]
        ok &= check(bool(grocery), "slug--uuid8 filename",
                    f"{[f.name for f in files]}")
        if grocery:
            keys, body = split_front_matter(grocery[0].read_text(encoding="utf-8"))
            ok &= check(keys.get("synced-by") == "stickies-to-markdown" and
                        keys.get("color") == "yellow" and
                        keys.get("stickies-uuid", "").startswith("11111111") and
                        keys.get("content-hash", "").startswith("sha256:"),
                        "marker, colour, uuid and hash in front matter", f"{keys}")
            ok &= check("Milk and eggs" in body, "body converted", f"{body!r}")
        punct = [f for f in files if f.name == "note--66666666.md"]
        ok &= check(bool(punct), "punctuation-only first line slugs to 'note'",
                    f"{[f.name for f in files]}")
        attach = box.output / "attachments" / "77777777" / "photo.png"
        ok &= check(attach.is_file(), "attachment copied",
                    f"missing: {attach}")
        return ok


def test_second_run_writes_nothing():
    with Sandbox() as box:
        _export(box)
        before = box.tree_signature(box.output)
        counters = _export(box)
        after = box.tree_signature(box.output)
        ok = check(counters.converted == 0 and counters.unchanged == 7,
                   "second run: all notes unchanged", f"{counters.as_dict()}")
        ok &= check(before == after, "output tree byte-identical",
                    "output changed on an idle re-run")
        return ok


def test_modified_note_rewrites_exactly_one_file():
    with Sandbox() as box:
        _export(box)
        rtf = (box.container / "33333333-CCCC-4CCC-8CCC-333333333333.rtfd"
               / "TXT.rtf")
        rtf.write_bytes(rtf.read_bytes().replace(b"passport", b"tickets"))
        counters = _export(box)
        ok = check(counters.converted == 1 and counters.unchanged == 6,
                   "exactly one file rewritten", f"{counters.as_dict()}")
        packing = next(f for f in box.mirror_files()
                       if f.name.startswith("packing--33333333"))
        ok &= check("tickets" in packing.read_text(encoding="utf-8"),
                    "rewrite carries the new content", "stale content")
        return ok


def test_deleted_note_tombstones():
    with Sandbox() as box:
        _export(box)
        import shutil
        shutil.rmtree(box.container / "22222222-BBBB-4BBB-8BBB-222222222222.rtfd")
        _export(box)
        tombstone = box.output / "_deleted" / "project-ideas--22222222.md"
        ok = check(tombstone.is_file(), "vanished note moved to _deleted/",
                   f"missing: {tombstone}")
        ok &= check(len(box.mirror_files()) == 6, "mirror root has 6 files",
                    f"{[f.name for f in box.mirror_files()]}")
        return ok


def test_container_never_touched():
    with Sandbox() as box:
        before = box.tree_signature(box.container)
        _export(box)
        _export(box)
        after = box.tree_signature(box.container)
        return check(before == after,
                     "container tree byte-identical after two exports",
                     "the tool wrote inside the container")


def test_unmarked_file_is_never_touched():
    with Sandbox() as box:
        box.output.mkdir()
        foreign = box.output / "grocery-list--11111111.md"
        foreign.write_text("my own file, hands off\n", encoding="utf-8")
        counters = _export(box)
        ok = check(foreign.read_text(encoding="utf-8")
                   == "my own file, hands off\n",
                   "file without our marker left alone", "foreign file changed")
        ok &= check(counters.errors >= 1, "collision reported as an error",
                    f"{counters.as_dict()}")
        return ok


def test_external_edit_quarantined_to_conflicts():
    with Sandbox(read_only_output=False) as box:
        _export(box)
        mirror = next(f for f in box.mirror_files()
                      if f.name.startswith("grocery-list--11111111"))
        text = mirror.read_text(encoding="utf-8")
        mirror.write_text(text.replace("Milk", "Oat milk"), encoding="utf-8")
        _export(box)
        conflicts = list((box.output / "_conflicts").glob("grocery-list--*.md"))
        ok = check(len(conflicts) == 1, "edited mirror file quarantined",
                   f"conflicts: {[c.name for c in conflicts]}")
        ok &= check("Oat milk" in conflicts[0].read_text(encoding="utf-8"),
                    "the user's edit is preserved in _conflicts/",
                    "edit lost")
        ok &= check("Oat milk" not in mirror.read_text(encoding="utf-8"),
                    "mirror file rewritten from the source of truth",
                    "mirror kept the external edit")
        return ok


def test_read_only_mode():
    with Sandbox() as box:
        _export(box)
        mirror = box.mirror_files()[0]
        mode = stat.S_IMODE(os.stat(mirror).st_mode)
        ok = check(mode == 0o444, "mirror files are chmod 444", f"mode {oct(mode)}")
        # Updating a 444 file must still work (rename needs dir perms only).
        rtf = (box.container / "11111111-AAAA-4AAA-8AAA-111111111111.rtfd"
               / "TXT.rtf")
        rtf.write_bytes(rtf.read_bytes().replace(b"Milk", b"Cream"))
        counters = _export(box)
        ok &= check(counters.converted == 1 and counters.errors == 0,
                    "read-only mirror file still updated via rename",
                    f"{counters.as_dict()}")
        return ok


def test_obsidian_flavor_and_uuid_style():
    with Sandbox(flavor="obsidian", filename_style="uuid") as box:
        _export(box)
        files = box.mirror_files()
        ok = check(all(len(f.name) == 11 for f in files),   # uuid8 + ".md"
                   "uuid filename style", f"{[f.name for f in files]}")
        keys, _ = split_front_matter(files[0].read_text(encoding="utf-8"))
        ok &= check("stickies-mirror" in keys.get("cssclasses", ""),
                    "obsidian flavor adds cssclasses", f"{keys}")
        return ok


def test_dry_run_writes_nothing():
    with Sandbox(dry_run=True) as box:
        counters = _export(box)
        ok = check(not box.output.exists() or not box.mirror_files(),
                   "dry run wrote no mirror files",
                   f"{[f.name for f in box.mirror_files()]}")
        ok &= check(counters.converted == 7,
                    "dry run still reports what it would do",
                    f"{counters.as_dict()}")
        return ok


def test_rename_retitles_the_file():
    with Sandbox() as box:
        _export(box)
        rtf = (box.container / "11111111-AAAA-4AAA-8AAA-111111111111.rtfd"
               / "TXT.rtf")
        rtf.write_bytes(rtf.read_bytes().replace(b"Grocery list", b"Shopping"))
        _export(box)
        names = [f.name for f in box.mirror_files()]
        ok = check("shopping--11111111.md" in names,
                   "retitled note gets a new filename", f"{names}")
        ok &= check("grocery-list--11111111.md" not in names,
                    "old filename handled as a rename, not left behind",
                    f"{names}")
        ok &= check((box.output / "_deleted" / "grocery-list--11111111.md").is_file(),
                    "old name tombstoned per on_delete", "no tombstone")
        return ok


if __name__ == "__main__":
    tests = [test_full_export_and_front_matter, test_second_run_writes_nothing,
             test_modified_note_rewrites_exactly_one_file,
             test_deleted_note_tombstones, test_container_never_touched,
             test_unmarked_file_is_never_touched,
             test_external_edit_quarantined_to_conflicts,
             test_read_only_mode, test_obsidian_flavor_and_uuid_style,
             test_dry_run_writes_nothing, test_rename_retitles_the_file]
    exit(0 if run_suite("exporter tests", tests) else 1)


# End of file #
