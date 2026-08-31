#!/usr/bin/env python3
"""
Phase 1 acceptance (handoff §8): full export produces the mirror; a second
run writes nothing; a modified fixture rewrites exactly one file; a removed
fixture produces a tombstone; the container tree is unchanged afterwards.
Plus: conflict quarantine, unmarked-file safety, read-only mode, flavors.
"""

import os
import stat

from pathlib import Path

from _helpers import Sandbox, check, run_suite, notes_in

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
        plain_base = box.root / "plain"
        box.config.add_target("plain", str(plain_base), flavor="generic", on_delete="archive")
        plain = Path(box.config.target("plain").output_dir())
        ok0 = check(plain == plain_base / "Synced_from_Stickies",
                    "an output gets the default subfolder inside the folder given", str(plain))
        counters = _export(box)
        vault_files = sorted(f.name for f in box.mirror_files())
        plain_files = sorted(f.name for f in notes_in(plain))
        ok = ok0 & check(len(vault_files) == 6 and len(plain_files) == 7,
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


def test_plugin_flavors_from_source():
    """Keys/values as read from each plugin's code (emitters.py docstring)."""
    with Sandbox(flavor="obsidian, sticky-notes, colorful-stickynotes") as box:
        _export(box)
        keys, _ = split_front_matter((box.output / "packing--33333333.md").read_text(encoding="utf-8"))
        ok = check(keys.get("background_color") == "Green", "sticky-notes: background_color Green", f"{keys}")
        ok &= check(keys.get("colorful-sticky-bg") == "mint", "colorful-stickynotes: green -> mint", f"{keys}")
        ok &= check("sticky-green" in keys.get("cssclasses", ""), "obsidian cssclasses alongside", f"{keys}")
        ok &= check(keys.get("color") == "green", "generic color (Floating Sticky Notes reads this)", f"{keys}")
        gray, _ = split_front_matter((box.output / "note--66666666.md").read_text(encoding="utf-8"))
        ok &= check(gray.get("background_color") == "Base" and gray.get("colorful-sticky-bg") == "gray",
                    "gray maps to Base / gray per plugin vocabulary", f"{gray}")
        return ok


def test_subfolder_blank_writes_directly():
    with Sandbox(subfolder="") as box:
        _export(box)
        return check(len(box.mirror_files()) == 7 and not (box.output / "Synced_from_Stickies").exists(),
                     "subfolder '' writes straight into the folder", "")


def test_slug_style_collision_and_rename():
    with Sandbox(filename_style="slug") as box:
        _export(box)
        names = sorted(f.name for f in box.mirror_files())
        suffixed = [n for n in names if "--" in n]
        ok = check("grocery-list.md" in names and "note.md" in names
                   and suffixed == ["note--66666666.md"],
                   "slug style: plain names; the one real collision (two 'note' slugs) suffixed",
                   f"{names}")
        # a second note with the same first line -> only the newcomer gets the suffix
        pkg = box.container / "ABCDEF02-0000-4000-8000-000000000000.rtfd"
        pkg.mkdir()
        (pkg / "TXT.rtf").write_bytes(
            (box.container / "11111111-AAAA-4AAA-8AAA-111111111111.rtfd" / "TXT.rtf").read_bytes())
        _export(box)
        names = sorted(f.name for f in box.mirror_files())
        ok &= check("grocery-list.md" in names and "grocery-list--abcdef02.md" in names,
                    "collision: existing keeps the plain name, newcomer gets uuid8", f"{names}")
        before = box.tree_signature(box.output)
        _export(box)
        ok &= check(before == box.tree_signature(box.output), "stable across re-runs", "churn")
        return ok


def test_machine_identity_isolates_shared_folders():
    """Two Macs mirroring into one folder: neither touches the other's files."""
    with Sandbox(machine_label="mac-a", machine_id="aaaaaaaa") as box:
        _export(box)
        keys, _ = split_front_matter((box.output / "grocery-list--11111111.md").read_text(encoding="utf-8"))
        ok = check(keys.get("source-machine") == "mac-a" and keys.get("source-machine-id") == "aaaaaaaa",
                   "source-machine and source-machine-id written", f"{keys}")
        # Renaming the machine (label changes, id does not) must not orphan its own files.
        box.config.set("machine_label", "mac-a-renamed")
        counters = _export(box)
        ok &= check(counters.deleted == 0 and counters.converted == 7,
                    "hostname change: files still recognised as ours (rewritten with the new label)",
                    f"{counters.as_dict()}")
        # Now "mac-b" runs against a container that lacks all of mac-a's notes.
        import shutil
        for pkg in box.note_dirs():
            shutil.rmtree(pkg)
        pkg = box.container / "BBBBBBBB-0000-4000-8000-000000000000.rtfd"
        pkg.mkdir()
        (pkg / "TXT.rtf").write_bytes(
            rb"{\rtf1\ansi{\fonttbl\f0\fswiss Helvetica;}\f0 Mac B note\par}")
        box.config.update({"machine_label": "mac-b", "machine_id": "bbbbbbbb"})
        counters = _export(box)
        names = sorted(f.name for f in box.mirror_files())
        ok &= check(len(names) == 8 and "mac-b-note--bbbbbbbb.md" in names
                    and counters.deleted == 0 and not (box.output / "_deleted").exists(),
                    "mac-b left mac-a's 7 files alone and added its own", f"{names} {counters.as_dict()}")
        return ok


def test_machine_placeholder_in_subfolder():
    with Sandbox(machine_label="studio", machine_id="c0ffee00", subfolder="Stickies/{machine}") as box:
        ok = check(box.target.output_dir() == str(box.output / "Stickies" / "studio"),
                   "{machine} expands to the label", box.target.output_dir())
        box.set_target("subfolder", "Stickies/{machine_id}")
        ok &= check(box.target.output_dir() == str(box.output / "Stickies" / "c0ffee00"),
                    "{machine_id} expands to the stable id", box.target.output_dir())
        from stickies_to_markdown.engine.config import machine_id
        ok &= check(len(machine_id()) == 8 and machine_id() == machine_id(),
                    f"detected machine id is 8 hex and stable ({machine_id()})", "")
        return ok


def test_readme_note_maintained_and_sorted_first():
    with Sandbox(on_delete="mark") as box:
        _export(box)
        readme = box.output / "_About these notes (read-only mirror).md"
        ok = check(readme.is_file(), "readme note written into the mirror folder", "")
        ok &= check(readme.name < min(f.name for f in box.mirror_files()),
                    "sorts before every note (leading underscore)", readme.name)
        keys, body = split_front_matter(readme.read_text(encoding="utf-8"))
        ok &= check(keys.get("synced-by") == "stickies-to-markdown" and keys.get("mirror-readme") == "True"
                    and "stickies-uuid" not in keys and "read-only" in body
                    and "marked `deleted-from-stickies`" in body,
                    "marked as ours, no uuid (never indexed as a note), states the policy", f"{keys}")
        ok &= check(stat.S_IMODE(os.stat(readme).st_mode) == 0o444, "readme is read-only too", "")
        before = box.tree_signature(box.output)
        _export(box)
        ok &= check(before == box.tree_signature(box.output), "idempotent across exports", "churn")
        box.set_target("on_delete", "archive")
        _export(box)
        ok &= check("moved to `_deleted/`" in readme.read_text(encoding="utf-8"),
                    "rewritten when the policy it describes changes", "stale")
        readme.chmod(0o644)
        readme.write_text("---\ntitle: mine\n---\nmy own note\n", encoding="utf-8")
        _export(box)
        ok &= check("my own note" in readme.read_text(encoding="utf-8"),
                    "a foreign file with that name is left alone", "clobbered")
        return ok


def test_obsidian_snippet_installed_into_vault():
    with Sandbox(flavor="obsidian") as box:
        # Make the sandbox root look like a vault; the mirror is a subfolder of it.
        (box.root / ".obsidian").mkdir()
        (box.root / ".obsidian" / "appearance.json").write_text('{"baseFontSize": 16}', encoding="utf-8")
        _export(box)
        css = box.root / ".obsidian" / "snippets" / "stickies-mirror.css"
        import json
        appearance = json.loads((box.root / ".obsidian" / "appearance.json").read_text(encoding="utf-8"))
        ok = check(css.is_file() and ".sticky-yellow" in css.read_text(encoding="utf-8"),
                   "snippet written into <vault>/.obsidian/snippets/", "")
        ok &= check(appearance.get("enabledCssSnippets") == ["stickies-mirror"]
                    and appearance.get("baseFontSize") == 16,
                    "enabled in appearance.json without disturbing other keys", f"{appearance}")
        before = (css.stat().st_mtime_ns, (box.root / ".obsidian" / "appearance.json").stat().st_mtime_ns)
        _export(box)
        after = (css.stat().st_mtime_ns, (box.root / ".obsidian" / "appearance.json").stat().st_mtime_ns)
        ok &= check(before == after, "second export touches neither file", "churn")
        css.write_text("/* someone else's */ body {}", encoding="utf-8")
        _export(box)
        ok &= check(css.read_text(encoding="utf-8").startswith("/* someone else's"),
                    "a foreign snippet of the same name is never overwritten", "clobbered")
        return ok


def test_no_vault_no_snippet_and_generic_flavor_no_snippet():
    with Sandbox(flavor="obsidian") as box:          # no .obsidian anywhere above
        _export(box)
        ok = check(not (box.root / ".obsidian").exists(), "no vault found: nothing created", "")
    with Sandbox(flavor="generic") as box:
        (box.root / ".obsidian").mkdir()
        _export(box)
        ok &= check(not (box.root / ".obsidian" / "snippets").exists(),
                    "generic flavor: vault present but no snippet installed", "")
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
             test_plugin_flavors_from_source, test_subfolder_blank_writes_directly,
             test_slug_style_collision_and_rename, test_machine_identity_isolates_shared_folders,
             test_machine_placeholder_in_subfolder, test_readme_note_maintained_and_sorted_first,
             test_obsidian_snippet_installed_into_vault, test_no_vault_no_snippet_and_generic_flavor_no_snippet,
             test_custom_deleted_dir_and_collision, test_exclusion_by_color_is_reactive,
             test_exclusion_by_title_regex_with_archive, test_attachments_follow_the_file,
             test_container_never_touched,
             test_unmarked_file_is_never_touched,
             test_external_edit_quarantined_to_conflicts,
             test_read_only_mode, test_obsidian_flavor_and_uuid_style,
             test_dry_run_writes_nothing, test_rename_retitles_the_file]
    exit(0 if run_suite("exporter tests", tests) else 1)


# End of file #
