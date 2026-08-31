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


def _delete_project_note(box):
    import shutil
    shutil.rmtree(box.container / "22222222-BBBB-4BBB-8BBB-222222222222.rtfd")


def test_deleted_note_archives_with_annotation():
    with Sandbox() as box:          # default policy: archive
        _export(box)
        _delete_project_note(box)
        _export(box)
        archived = box.output / "_deleted" / "project-ideas--22222222.md"
        ok = check(archived.is_file(), "vanished note moved to _deleted/",
                   f"missing: {archived}")
        keys, body = split_front_matter(archived.read_text(encoding="utf-8"))
        ok &= check(keys.get("deleted-from-stickies", "").startswith("20"),
                    "archived file annotated with deleted-from-stickies",
                    f"{keys}")
        ok &= check("Important" in body and keys.get("synced-by") == "stickies-to-markdown",
                    "body and other keys byte-preserved", f"{body!r}")
        ok &= check(len(box.mirror_files()) == 6, "mirror root has 6 files",
                    f"{[f.name for f in box.mirror_files()]}")
        # "tombstone" still accepted as an alias
        from stickies_to_markdown.engine.config import OutputTarget
        ok &= check(OutputTarget({"on_delete": "tombstone"}).on_delete() == "archive",
                    "'tombstone' aliases to 'archive'", "alias broken")
        return ok


def test_on_delete_mark_annotates_in_place():
    with Sandbox(on_delete="mark", flavor="obsidian") as box:
        _export(box)
        _delete_project_note(box)
        _export(box)
        marked = box.output / "project-ideas--22222222.md"
        ok = check(marked.is_file() and len(box.mirror_files()) == 7,
                   "mark: file stays in place", f"{[f.name for f in box.mirror_files()]}")
        keys, _ = split_front_matter(marked.read_text(encoding="utf-8"))
        ok &= check(keys.get("deleted-from-stickies", "").startswith("20"),
                    "annotated with deleted-from-stickies", f"{keys}")
        ok &= check("stickies-deleted" in keys.get("cssclasses", "")
                    and "stickies-mirror" in keys.get("cssclasses", ""),
                    "obsidian flavor appends stickies-deleted to cssclasses",
                    f"{keys.get('cssclasses')}")
        before = box.tree_signature(box.output)
        _export(box)
        ok &= check(before == box.tree_signature(box.output),
                    "re-run leaves the marked orphan untouched (idempotent)",
                    "marked file churned")
        return ok


def test_on_delete_delete_and_keep():
    with Sandbox(on_delete="delete") as box:
        _export(box)
        _delete_project_note(box)
        _export(box)
        ok = check(len(box.mirror_files()) == 6
                   and not (box.output / "_deleted").exists(),
                   "delete: file removed, no archive folder", "unexpected files")
    with Sandbox(on_delete="keep") as box:
        _export(box)
        _delete_project_note(box)
        before = box.tree_signature(box.output)
        _export(box)
        ok &= check(before == box.tree_signature(box.output)
                    and len(box.mirror_files()) == 7,
                    "keep: orphan left byte-identical, unannotated",
                    "keep touched the orphan")
    return ok


def test_exclusion_by_color_is_reactive():
    with Sandbox() as box:
        _export(box)                       # gray note (66666666) mirrored
        ok = check(any(f.name.endswith("66666666.md") for f in box.mirror_files()),
                   "before exclusion the gray note is mirrored", "not mirrored")
        box.set_target("exclude_colors", ["gray"])
        counters = _export(box)            # now it becomes excluded
        ok &= check(not any(f.name.endswith("66666666.md") for f in box.mirror_files())
                    and not (box.output / "_deleted").exists(),
                    "exclusion applied on_exclude=delete (gone, not archived)",
                    f"{[f.name for f in box.mirror_files()]}")
        ok &= check(counters.excluded == 1 and counters.deleted == 0
                    and counters.converted == 0,
                    "counted as excluded, not deleted", f"{counters.as_dict()}")
        before = box.tree_signature(box.output)
        _export(box)
        ok &= check(before == box.tree_signature(box.output),
                    "excluded note stays excluded, nothing churns", "churn")
        return ok


def test_exclusion_by_title_regex_with_archive():
    with Sandbox(exclude_title_regex=r"^Packing", on_exclude="archive") as box:
        counters = _export(box)
        names = [f.name for f in box.mirror_files()]
        ok = check(not any("33333333" in n for n in names) and counters.excluded == 0,
                   "title-excluded note never written (nothing to dispose)", f"{names}")
        box.set_target("exclude_title_regex", "")
        _export(box)
        box.set_target("exclude_title_regex", r"^Packing")
        _export(box)
        archived = box.output / "_deleted" / "packing--33333333.md"
        ok &= check(archived.is_file(), "on_exclude=archive archives a newly excluded note",
                    f"missing: {archived}")
        return ok


def test_attachments_follow_the_file():
    with Sandbox() as box:                 # default archive
        _export(box)
        import shutil
        shutil.rmtree(box.container / "77777777-ABAB-4ABA-8ABA-777777777777.rtfd")
        _export(box)
        moved = box.output / "_deleted" / "attachments" / "77777777" / "photo.png"
        ok = check(moved.is_file() and not (box.output / "attachments" / "77777777").exists(),
                   "archive moves the note's attachments alongside it", f"{moved}")
    with Sandbox(on_delete="delete") as box:
        _export(box)
        import shutil
        shutil.rmtree(box.container / "77777777-ABAB-4ABA-8ABA-777777777777.rtfd")
        _export(box)
        ok &= check(not (box.output / "attachments" / "77777777").exists(),
                    "delete removes the note's attachments", "attachments left behind")
    return ok


def test_code_block_note_front_matter_and_slug():
    with Sandbox() as box:
        from test_convert import FORMULA_RTF
        pkg = box.container / "ABCDEF01-0000-4000-8000-000000000000.rtfd"
        pkg.mkdir()
        (pkg / "TXT.rtf").write_bytes(FORMULA_RTF)
        _export(box)
        target = box.output / "excel-formulas--abcdef01.md"
        ok = check(target.is_file(), "slug comes from the first content line, not the fence",
                   f"{[f.name for f in box.mirror_files()]}")
        keys, body = split_front_matter(target.read_text(encoding="utf-8"))
        ok &= check(keys.get("body-format") == "code", "front matter records body-format: code",
                    f"{keys}")
        grocery = next(f for f in box.mirror_files() if f.name.startswith("grocery"))
        keys2, _ = split_front_matter(grocery.read_text(encoding="utf-8"))
        ok &= check(keys2.get("body-format") == "markdown", "prose notes record markdown",
                    f"{keys2}")
        return ok


def test_two_outputs_with_different_settings():
    """One conversion, two mirrors: obsidian/uuid-named/gray-excluded/mark
    vs generic/slug-named/archive. Each has its own index and policies."""
    with Sandbox(flavor="obsidian", filename_style="uuid",
                 exclude_colors=["gray"], on_delete="mark") as box:
        plain = box.root / "plain"
        box.config.add_target("plain", str(plain), flavor="generic", on_delete="archive")
        counters = _export(box)
        vault_files = sorted(f.name for f in box.mirror_files())
        plain_files = sorted(f.name for f in plain.glob("*.md"))
        ok = check(len(vault_files) == 6 and len(plain_files) == 7,
                   "gray note excluded from one output only",
                   f"vault={vault_files} plain={plain_files}")
        ok &= check(all(len(n) == 11 for n in vault_files)
                    and any(n.startswith("grocery-list--") for n in plain_files),
                    "each output uses its own filename style", f"{vault_files[:2]} {plain_files[:2]}")
        keys_v, _ = split_front_matter((box.output / "11111111.md").read_text(encoding="utf-8"))
        keys_p, _ = split_front_matter((plain / "grocery-list--11111111.md").read_text(encoding="utf-8"))
        ok &= check("cssclasses" in keys_v and "cssclasses" not in keys_p,
                    "each output uses its own flavor", f"{keys_v.keys()} {keys_p.keys()}")
        ok &= check(counters.converted == 13 and counters.excluded == 0,
                    "counters count per output export", f"{counters.as_dict()}")
        # delete a note: mark in one, archive in the other
        _delete_project_note(box)
        _export(box)
        marked = box.output / "22222222.md"
        archived = plain / "_deleted" / "project-ideas--22222222.md"
        ok &= check(marked.is_file() and "deleted-from-stickies" in marked.read_text(encoding="utf-8")
                    and archived.is_file(),
                    "deletion policy applied per output (mark vs archive)",
                    f"marked={marked.is_file()} archived={archived.is_file()}")
        # idle re-run: both byte-identical
        before = (box.tree_signature(box.output), box.tree_signature(plain))
        _export(box)
        ok &= check(before == (box.tree_signature(box.output), box.tree_signature(plain)),
                    "idle re-run writes nothing to either output", "churn")
        return ok


def test_legacy_flat_config_migrates():
    import json
    with Sandbox() as box:
        legacy = {"stickies_dir": str(box.container), "output_dir": str(box.output),
                  "flavor": "obsidian", "on_delete": "mark", "converter": "text",
                  "log_file": box.config.get("log_file")}
        with open(box.config.config_file, "w") as handle:
            json.dump(legacy, handle)
        from stickies_to_markdown.engine import Config
        cfg = Config(config_file=box.config.config_file)
        ok = check([t.name for t in cfg.targets()] == ["default"]
                   and cfg.target("default").get("flavor") == "obsidian"
                   and cfg.target("default").on_delete() == "mark"
                   and cfg.get("converter") == "text" and "output_dir" not in cfg.config,
                   "flat single-output config migrated into outputs[default]", f"{cfg.config}")
        with open(box.config.config_file) as handle:
            on_disk = json.load(handle)
        ok &= check("outputs" in on_disk and "output_dir" not in on_disk,
                    "migration written back once", f"{on_disk.keys()}")
        return ok


def test_custom_deleted_dir_and_collision():
    with Sandbox(deleted_dir="Deleted_Stickies") as box:
        _export(box)
        _delete_project_note(box)
        _export(box)
        archived = box.output / "Deleted_Stickies" / "project-ideas--22222222.md"
        ok = check(archived.is_file(), "custom relative deleted_dir honoured",
                   f"missing: {archived}")
        # Same filename archived twice must not overwrite the first copy.
        archived.chmod(0o644)
        archived.write_text(archived.read_text(encoding="utf-8") + "\nEDITED\n",
                            encoding="utf-8")
        again = box.output / "project-ideas--22222222.md"
        again.write_text(
            "---\nsynced-by: stickies-to-markdown\nstickies-uuid: "
            "22222222-BBBB-4BBB-8BBB-222222222222\ncontent-hash: x\n---\n\nnew\n",
            encoding="utf-8")
        from stickies_to_markdown.engine.writer import Writer
        Writer(box.config, box.target, EventQueue()).handle_deletions(set(), set())
        copies = sorted((box.output / "Deleted_Stickies").glob("project-ideas--22222222*.md"))
        ok &= check(len(copies) == 2 and "EDITED" in archived.read_text(encoding="utf-8"),
                    "archive collision gets a timestamp suffix; first copy intact",
                    f"{[c.name for c in copies]}")
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
        ok &= check(not (box.output / "_deleted" / "grocery-list--11111111.md").exists(),
                    "stale name removed outright, not archived (content lives on)",
                    "stale name was archived")
        return ok


if __name__ == "__main__":
    tests = [test_full_export_and_front_matter, test_second_run_writes_nothing,
             test_modified_note_rewrites_exactly_one_file,
             test_deleted_note_archives_with_annotation,
             test_on_delete_mark_annotates_in_place, test_on_delete_delete_and_keep,
             test_code_block_note_front_matter_and_slug,
             test_two_outputs_with_different_settings, test_legacy_flat_config_migrates,
             test_custom_deleted_dir_and_collision, test_exclusion_by_color_is_reactive,
             test_exclusion_by_title_regex_with_archive, test_attachments_follow_the_file,
             test_container_never_touched,
             test_unmarked_file_is_never_touched,
             test_external_edit_quarantined_to_conflicts,
             test_read_only_mode, test_obsidian_flavor_and_uuid_style,
             test_dry_run_writes_nothing, test_rename_retitles_the_file]
    exit(0 if run_suite("exporter tests", tests) else 1)


# End of file #
