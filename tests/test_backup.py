#!/usr/bin/env python3
"""
Backup outputs: verbatim replica, tombstones, snapshots, restore - and the
failure modes that matter most: the output location missing (unmounted
volume) at start, vanishing mid-run, and coming back.
"""

import os
import time
import shutil
import zipfile

from pathlib import Path

from _helpers import Sandbox, check, run_suite, wait_for

from stickies_to_markdown.engine import Engine
from stickies_to_markdown.engine.events import EventQueue
from stickies_to_markdown.engine.logsetup import setup_logging
from stickies_to_markdown.engine.processor import NoteProcessor
from stickies_to_markdown.engine.backup import (
    BackupWriter, restore_container, corpus_hash, SNAPSHOT_MARKER, README_NAME)


def _export(box):
    setup_logging(box.config)
    processor = NoteProcessor(box.config, EventQueue())
    return processor.export_all()


def _backup_box(**overrides):
    """A sandbox whose single output is a backup block; `base` is the folder
    the user named, `replica` where packages land."""
    box = Sandbox(type="backup", **overrides)
    box.base = box.named                          # the folder the user named (exists)
    box.replica = Path(box.target.output_dir())
    return box


def _container_signature(box):
    """Signature of the container's packages + state file (what a replica must equal)."""
    return box.tree_signature(box.container)


def _replica_signature(box):
    entries = []
    for path in sorted(box.replica.rglob("*")):
        if path.is_file() and path.name != README_NAME and "_deleted" not in path.parts:
            entries.append((str(path.relative_to(box.replica)), path.stat().st_size,
                            __import__("hashlib").sha256(path.read_bytes()).hexdigest()))
    return tuple(entries)


def test_replica_is_verbatim_and_idempotent():
    with _backup_box() as box:
        counters = _export(box)
        ok = check(box.replica == box.base / "Stickies_backup.noindex" / box.config.machine_id(),
                   "replica in <named folder>/Stickies_backup.noindex/<machine-id> (stable, not hostname)",
                   str(box.replica))
        ok &= check(_replica_signature(box) == _container_signature(box),
                    "replica byte-identical to the container (packages + state file)", "differs")
        ok &= check(counters.converted == 7 and counters.errors == 0, "7 packages replicated",
                    f"{counters.as_dict()}")
        ok &= check((box.replica / README_NAME).is_file() and
                    "restore-from" in (box.replica / README_NAME).read_text(encoding="utf-8"),
                    "readme with restore instructions", "")
        from stickies_to_markdown.engine.writer import ROOT_ABOUT_NAME
        root_note = box.replica.parent / ROOT_ABOUT_NAME
        ok &= check(root_note.is_file() and box.config.machine_id() not in root_note.read_text(encoding="utf-8"),
                    "machine-agnostic root note at the .noindex parent", "")
        src = box.container / "77777777-ABAB-4ABA-8ABA-777777777777.rtfd" / "TXT.rtf"
        dst = box.replica / "77777777-ABAB-4ABA-8ABA-777777777777.rtfd" / "TXT.rtf"
        ok &= check(int(src.stat().st_mtime) == int(dst.stat().st_mtime), "mtimes preserved", "")
        before = box.tree_signature(box.replica)
        counters = _export(box)
        ok &= check(before == box.tree_signature(box.replica) and counters.unchanged == 7,
                    "idle re-run touches nothing", f"{counters.as_dict()}")
        return ok


def test_edit_and_attachment_change_replicate_minimally():
    with _backup_box() as box:
        _export(box)
        pkg = box.container / "33333333-CCCC-4CCC-8CCC-333333333333.rtfd"
        (pkg / "TXT.rtf").write_bytes((pkg / "TXT.rtf").read_bytes().replace(b"socks", b"boots"))
        (box.container / "77777777-ABAB-4ABA-8ABA-777777777777.rtfd" / "photo.png").write_bytes(b"new")
        before = {p: p.stat().st_mtime_ns for p in box.replica.rglob("TXT.rtf")}
        counters = _export(box)
        after = {p: p.stat().st_mtime_ns for p in box.replica.rglob("TXT.rtf")}
        changed = [p.parent.name[:8] for p in before if before[p] != after.get(p)]
        ok = check(counters.converted == 2 and changed == ["33333333"],
                   "only the edited note's TXT.rtf and the changed attachment were rewritten",
                   f"{counters.as_dict()} changed={changed}")
        ok &= check(_replica_signature(box) == _container_signature(box), "still verbatim", "")
        return ok


def test_deletion_tombstones_and_prunes():
    with _backup_box(keep_deleted_days=30) as box:
        _export(box)
        shutil.rmtree(box.container / "22222222-BBBB-4BBB-8BBB-222222222222.rtfd")
        _export(box)
        stamps = list((box.replica / "_deleted").iterdir())
        ok = check(len(stamps) == 1 and
                   (stamps[0] / "22222222-BBBB-4BBB-8BBB-222222222222.rtfd" / "TXT.rtf").is_file(),
                   "vanished package moved to _deleted/<stamp>/ intact", f"{stamps}")
        ok &= check(not (box.replica / "22222222-BBBB-4BBB-8BBB-222222222222.rtfd").exists(),
                    "and gone from the live replica", "")
        # An old tombstone (fake stamp 60 days ago) is pruned; a foreign folder is not.
        old = box.replica / "_deleted" / time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() - 60 * 86400))
        old.mkdir()
        (old / "x").write_text("x")
        foreign = box.replica / "_deleted" / "keep-me"
        foreign.mkdir()
        _export(box)
        ok &= check(not old.exists() and foreign.exists() and stamps[0].exists(),
                    "tombstones older than keep_deleted_days pruned; fresh and foreign kept", "")
        return ok


def test_snapshots_written_pruned_and_marker_checked():
    with _backup_box(snapshots=True, keep_snapshots=2, snapshot_every_days=0,
                     snapshot_quiet_seconds=0) as box:
        _export(box)
        zips = sorted(box.base.glob("Stickies_backup_*.zip"))
        ok = check(len(zips) == 1, "snapshot zip written into the folder the user named", f"{zips}")
        with zipfile.ZipFile(zips[0]) as archive:
            names = archive.namelist()
            comment = archive.comment.decode()
            stored = {i.filename: i.compress_type for i in archive.infolist()}
        ok &= check(comment.startswith(SNAPSHOT_MARKER) and corpus_hash(str(box.replica)) in comment,
                    "zip comment carries marker + corpus hash", comment[:80])
        ok &= check(any(n.endswith("TXT.rtf") for n in names) and ".SavedStickiesState" in names
                    and not any(n.startswith("_deleted") or n == README_NAME for n in names),
                    "zip holds packages + state, not tombstones or the readme", f"{names[:5]}")
        ok &= check(stored.get("77777777-ABAB-4ABA-8ABA-777777777777.rtfd/photo.png") == zipfile.ZIP_STORED
                    and stored.get("11111111-AAAA-4AAA-8AAA-111111111111.rtfd/TXT.rtf") == zipfile.ZIP_DEFLATED,
                    "images stored, text deflated", f"{stored}")
        _export(box)
        ok &= check(len(list(box.base.glob("Stickies_backup_*.zip"))) == 1,
                    "unchanged corpus -> no second snapshot", "")
        # change -> snapshot; three changes -> pruned to newest 2; foreign zip untouched
        foreign = box.base / "Stickies_backup_other_20200101-000000.zip"
        with zipfile.ZipFile(foreign, "w") as archive:
            archive.writestr("x", "not ours")
        for i in range(3):
            pkg = box.container / "11111111-AAAA-4AAA-8AAA-111111111111.rtfd" / "TXT.rtf"
            pkg.write_bytes(pkg.read_bytes() + b" edit%d" % i)
            time.sleep(1.1)                     # distinct timestamps in names
            _export(box)
        ours = [z for z in box.base.glob("Stickies_backup_*.zip") if z != foreign]
        ok &= check(len(ours) == 2 and foreign.is_file(),
                    "pruned to keep_snapshots=2 newest; foreign zip (no marker) left alone",
                    f"{[z.name for z in ours]}")
        return ok


def test_zip_only_mode_and_every_days_gate():
    with _backup_box(replica=False, snapshots=True, snapshot_every_days=30,
                     snapshot_quiet_seconds=0) as box:
        _export(box)
        ok = check(not (box.base / "Stickies_backup.noindex").exists(),
                   "replica off: no replica tree", "")
        zips = list(box.base.glob("Stickies_backup_*.zip"))
        ok &= check(len(zips) == 1, "zip written straight from the container", f"{zips}")
        with zipfile.ZipFile(zips[0]) as archive:
            ok &= check(corpus_hash(str(box.container)) in archive.comment.decode(),
                        "hash is of the container", "")
        pkg = box.container / "11111111-AAAA-4AAA-8AAA-111111111111.rtfd" / "TXT.rtf"
        pkg.write_bytes(pkg.read_bytes() + b" more")
        _export(box)
        ok &= check(len(list(box.base.glob("Stickies_backup_*.zip"))) == 1,
                    "changed corpus but every_days not elapsed -> no new zip", "")
        writer = BackupWriter(box.config, box.target, EventQueue())
        ok &= check(writer.maybe_snapshot(force=True) and
                    len(list(box.base.glob("Stickies_backup_*.zip"))) == 2,
                    "--snapshot-now (force) writes regardless", "")
        return ok


def test_restore_round_trip_from_replica_and_zip():
    with _backup_box(snapshots=True, snapshot_every_days=0, snapshot_quiet_seconds=0) as box:
        _export(box)
        original = _container_signature(box)
        zip_path = next(box.base.glob("Stickies_backup_*.zip"))
        # Wreck the container: delete two notes, corrupt one.
        shutil.rmtree(box.container / "22222222-BBBB-4BBB-8BBB-222222222222.rtfd")
        shutil.rmtree(box.container / "33333333-CCCC-4CCC-8CCC-333333333333.rtfd")
        (box.container / "11111111-AAAA-4AAA-8AAA-111111111111.rtfd" / "TXT.rtf").write_bytes(b"garbage")
        count, safety = restore_container(str(box.replica), str(box.container), str(box.root / "safety"))
        ok = check(count == 7 and _container_signature(box) == original,
                   "restore from replica: container byte-identical to before", "differs")
        ok &= check(safety and zipfile.is_zipfile(safety), "safety zip of the wrecked state written", f"{safety}")
        shutil.rmtree(box.container / "44444444-DDDD-4DDD-8DDD-444444444444.rtfd")
        count, _ = restore_container(str(zip_path), str(box.container), str(box.root / "safety"))
        ok &= check(count == 7 and _container_signature(box) == original,
                    "restore from a snapshot zip: identical again", "differs")
        try:
            restore_container(str(box.root / "nothing-here"), str(box.container), str(box.root / "safety"))
            ok &= check(False, "", "restore accepted a non-backup")
        except RuntimeError as error:
            ok &= check("not a backup" in str(error), "non-backup source refused", str(error))
        return ok


def test_unavailable_at_start_writes_nothing_and_creates_nothing():
    with _backup_box() as box:
        shutil.rmtree(box.base)                     # "volume not mounted"
        counters = _export(box)
        ok = check(not box.base.exists(), "the named folder was NOT created (mount point untouched)", "")
        ok &= check(counters.errors == 7 and counters.converted == 0,
                    "every note reported as an error, nothing written", f"{counters.as_dict()}")
        log = box.log_text()
        ok &= check(log.count("output folder unavailable") == 1,
                    "unavailability logged exactly once, not per note", f"{log.count('output folder unavailable')}")
        box.base.mkdir()                            # "volume mounted"
        counters = _export(box)
        ok &= check(counters.converted == 7 and counters.errors == 0 and
                    _replica_signature(box) == _container_signature(box),
                    "resumes and replicates fully once the folder is back", f"{counters.as_dict()}")
        return ok


def test_markdown_output_has_the_same_guard():
    with Sandbox() as box:
        shutil.rmtree(box.named)
        counters = _export(box)
        ok = check(not box.named.exists() and counters.errors == 7,
                   "markdown output: missing named folder never created, notes reported", f"{counters.as_dict()}")
        box.named.mkdir()
        counters = _export(box)
        ok &= check(counters.converted == 7 and len(box.mirror_files()) == 7,
                    "markdown output resumes", f"{counters.as_dict()}")
        return ok


def test_unavailable_output_does_not_block_other_outputs():
    with Sandbox() as box:
        gone = box.root / "external"
        gone.mkdir()
        box.config.add_target("ext", str(gone), type="backup")
        gone.rmdir()
        counters = _export(box)
        ok = check(len(box.mirror_files()) == 7 and counters.converted == 7 and counters.errors == 7,
                   "markdown output written in full while the backup output is unavailable",
                   f"{counters.as_dict()}")
        return ok


def test_volume_vanishing_during_watch_does_not_crash():
    with _backup_box(debounce_seconds=0.3, settle_seconds=0.2) as box:
        engine = Engine(box.config)
        engine.start()
        try:
            wait_for(lambda: (box.replica / "11111111-AAAA-4AAA-8AAA-111111111111.rtfd").is_dir(), timeout=10)
            shutil.rmtree(box.base)                 # yank the drive mid-watch
            pkg = box.container / "11111111-AAAA-4AAA-8AAA-111111111111.rtfd"
            (pkg / "TXT.rtf").write_bytes((pkg / "TXT.rtf").read_bytes() + b" edit")
            seen = []
            wait_for(lambda: seen.extend(e for e in engine.events.drain() if e.kind == "error")
                     or any("unavailable" in e.detail for e in seen), timeout=8)
            ok = check(any("unavailable" in e.detail for e in seen),
                       "edit while unavailable -> 'unavailable' error event (mid-write OSErrors allowed)",
                       f"{seen}")
            ok &= check(engine.status().monitoring and engine.observer_alive(),
                        "watcher still running", f"{engine.status().as_dict()}")
            ok &= check(not box.base.exists(), "nothing recreated at the mount point", "")
            box.base.mkdir()                        # drive back
            (pkg / "TXT.rtf").write_bytes((pkg / "TXT.rtf").read_bytes() + b" again")
            ok &= check(wait_for(lambda: (box.replica / pkg.name / "TXT.rtf").is_file() and
                                 b"again" in (box.replica / pkg.name / "TXT.rtf").read_bytes(), timeout=10),
                        "replication resumes after the drive returns", "")
            return ok
        finally:
            engine.stop()


def test_dry_run_backup_writes_nothing():
    with _backup_box(dry_run=True, snapshots=True, snapshot_every_days=0, snapshot_quiet_seconds=0) as box:
        counters = _export(box)
        return check(not box.replica.exists() and not list(box.base.glob("*.zip"))
                     and counters.converted == 7,
                     "dry run: reports 7 would-replicate, creates nothing", f"{counters.as_dict()}")


if __name__ == "__main__":
    tests = [test_replica_is_verbatim_and_idempotent, test_edit_and_attachment_change_replicate_minimally,
             test_deletion_tombstones_and_prunes, test_snapshots_written_pruned_and_marker_checked,
             test_zip_only_mode_and_every_days_gate, test_restore_round_trip_from_replica_and_zip,
             test_unavailable_at_start_writes_nothing_and_creates_nothing,
             test_markdown_output_has_the_same_guard, test_unavailable_output_does_not_block_other_outputs,
             test_volume_vanishing_during_watch_does_not_crash, test_dry_run_backup_writes_nothing]
    exit(0 if run_suite("backup tests", tests) else 1)


# End of file #
