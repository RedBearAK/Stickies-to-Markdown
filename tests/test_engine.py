#!/usr/bin/env python3
"""
The live watcher, against a fake container that behaves the way the real
one was observed to (dev_notes/MAC_FINDINGS.md): packages are born as flat
files, TXT.rtf is replaced not rewritten, deletion is the directory
vanishing, and creations come in bursts.

Uses wait_for(predicate), never fixed sleeps, so it holds on inotify here
and FSEvents on a Mac. Debounce/settle are shortened to keep the suite
quick; the defaults are exercised by the config tests.
"""

import os
import shutil
import tempfile

from pathlib import Path

from _helpers import Sandbox, FIXTURES, check, run_suite, wait_for

from stickies_to_markdown.engine import Engine, EngineError, Config

FAST = {"debounce_seconds": 0.3, "settle_seconds": 0.2}
NEW_RTF = (rb"{\rtf1\ansi\ansicpg1252{\fonttbl\f0\fswiss\fcharset0 Helvetica;}"
           rb"\f0\fs24 Brand new note\par with a line\par}")


def _engine(box, export_first=True):
    engine = Engine(box.config)
    engine.start(export_first=export_first)
    return engine


def _drain(engine, kinds=None):
    events = engine.events.drain()
    return [e for e in events if kinds is None or e.kind in kinds]


def _mirror_names(box):
    return [f.name for f in box.mirror_files()]


def _replace_rtf(pkg, data):
    """Stickies' write pattern: new inode via temp + rename."""
    temp = pkg / ".TXT.rtf.tmp"
    temp.write_bytes(data)
    os.replace(temp, pkg / "TXT.rtf")


def test_start_exports_then_watches():
    with Sandbox(**FAST) as box:
        engine = _engine(box)
        try:
            ok = check(wait_for(lambda: len(box.mirror_files()) == 7),
                       "start performs the initial full export", f"{_mirror_names(box)}")
            st = engine.status()
            ok &= check(st.monitoring and st.healthy and st.container_readable,
                        "status: monitoring, healthy, container readable", f"{st.as_dict()}")
        finally:
            engine.stop()
        return ok


def test_edit_via_replace_converts_once():
    with Sandbox(**FAST) as box:
        engine = _engine(box)
        try:
            wait_for(lambda: len(box.mirror_files()) == 7)
            _drain(engine)
            pkg = box.container / "11111111-AAAA-4AAA-8AAA-111111111111.rtfd"
            _replace_rtf(pkg, (pkg / "TXT.rtf").read_bytes().replace(b"Milk", b"Cream"))
            got = wait_for(lambda: [e for e in engine.events.drain()
                                    if e.kind == "converted"] or None, timeout=8)
            ok = check(got and len(got) == 1 and got[0].path.endswith("grocery-list--11111111.md"),
                       "one converted event for the replaced TXT.rtf",
                       f"{got}")
            mirror = box.output / "grocery-list--11111111.md"
            ok &= check("Cream" in mirror.read_text(encoding="utf-8"),
                        "mirror file carries the new content", "stale")
        finally:
            engine.stop()
        return ok


def test_attribute_rewrite_is_unchanged_not_suppressed():
    with Sandbox(**FAST) as box:
        engine = _engine(box)
        try:
            wait_for(lambda: len(box.mirror_files()) == 7)
            _drain(engine)
            pkg = box.container / "22222222-BBBB-4BBB-8BBB-222222222222.rtfd"
            _replace_rtf(pkg, (pkg / "TXT.rtf").read_bytes())   # same bytes, new inode
            got = wait_for(lambda: [e for e in engine.events.drain()
                                    if e.kind in ("unchanged", "converted")] or None, timeout=8)
            return check(got and got[0].kind == "unchanged",
                         "identical rewrite (colour/move) counted as unchanged",
                         f"{got}")
        finally:
            engine.stop()


def test_newborn_flat_file_then_package():
    with Sandbox(**FAST) as box:
        engine = _engine(box)
        try:
            wait_for(lambda: len(box.mirror_files()) == 7)
            _drain(engine)
            flat = box.container / "99999999-9999-4999-8999-999999999999.rtfd"
            flat.write_bytes(b"rtfd\x00flat-wrapper")           # step 1: flat file
            wait_for(lambda: engine._retries.get("99999999-9999-4999-8999-999999999999"), timeout=5)
            ok = check(len(box.mirror_files()) == 7 and not (box.output / "_deleted").exists(),
                       "flat file: nothing written, nothing deleted (waiting)",
                       f"{_mirror_names(box)}")
            flat.unlink()                                       # step 2: becomes a package
            flat.mkdir()
            (flat / "TXT.rtf").write_bytes(NEW_RTF)
            ok &= check(wait_for(lambda: (box.output / "brand-new-note--99999999.md").is_file(),
                                 timeout=8),
                        "package appears -> converted", f"{_mirror_names(box)}")
            return ok
        finally:
            engine.stop()


def test_mid_save_gap_is_not_a_deletion():
    with Sandbox(**FAST) as box:
        engine = _engine(box)
        try:
            wait_for(lambda: len(box.mirror_files()) == 7)
            _drain(engine)
            pkg = box.container / "33333333-CCCC-4CCC-8CCC-333333333333.rtfd"
            data = (pkg / "TXT.rtf").read_bytes()
            (pkg / "TXT.rtf").unlink()                          # observed: gone for 0.5 s
            wait_for(lambda: False, timeout=0.3)
            (pkg / "TXT.rtf").write_bytes(data.replace(b"socks", b"boots"))
            got = wait_for(lambda: [e for e in engine.events.drain()
                                    if e.kind in ("converted", "deleted")] or None, timeout=8)
            ok = check(got and all(e.kind == "converted" for e in got),
                       "TXT.rtf delete+create handled as an edit, never a deletion",
                       f"{got}")
            ok &= check(not (box.output / "_deleted").exists(), "no tombstone", "tombstoned")
            return ok
        finally:
            engine.stop()


def test_directory_vanishing_is_the_deletion():
    with Sandbox(**FAST) as box:
        engine = _engine(box)
        try:
            wait_for(lambda: len(box.mirror_files()) == 7)
            _drain(engine)
            shutil.rmtree(box.container / "22222222-BBBB-4BBB-8BBB-222222222222.rtfd")
            ok = check(wait_for(lambda: (box.output / "_deleted" / "project-ideas--22222222.md").is_file(),
                                timeout=8),
                       "removed package -> on_delete applied (archived)", f"{_mirror_names(box)}")
            got = _drain(engine, {"deleted"})
            ok &= check(len(got) == 1, "one deleted event", f"{got}")
            return ok
        finally:
            engine.stop()


def test_burst_of_creations_all_land():
    with Sandbox(**FAST) as box:
        engine = _engine(box)
        try:
            wait_for(lambda: len(box.mirror_files()) == 7)
            _drain(engine)
            for i in range(16):
                pkg = box.container / f"AAAAAAAA-0000-4000-8000-{i:012d}.rtfd"
                pkg.mkdir()
                (pkg / "TXT.rtf").write_bytes(NEW_RTF.replace(b"Brand new note", b"Burst %d" % i))
            ok = check(wait_for(lambda: len(box.mirror_files()) == 23, timeout=20),
                       "16 packages created in a burst all mirrored", f"{len(box.mirror_files())}")
            return ok
        finally:
            engine.stop()


def test_recolour_to_excluded_removes_file():
    with Sandbox(exclude_colors=["gray"], on_exclude="delete", **FAST) as box:
        engine = _engine(box)
        try:
            wait_for(lambda: len(box.mirror_files()) == 6)      # gray note excluded at start
            _drain(engine)
            # Recolour yellow note 11111111 to gray: state file rewritten (temp+rename)
            # and, as observed, the package rewritten too.
            import plistlib
            state_path = box.container / ".SavedStickiesState"
            with open(state_path, "rb") as handle:
                state = plistlib.load(handle)
            for entry in state:
                if entry["UUID"].startswith("11111111"):
                    entry["StickyColor"] = {"Red": 0.933, "Green": 0.933, "Blue": 0.933, "Alpha": 1.0}
            temp = box.container / ".SavedStickiesState.tmp"
            with open(temp, "wb") as handle:
                plistlib.dump(state, handle, fmt=plistlib.FMT_BINARY)
            os.replace(temp, state_path)
            pkg = box.container / "11111111-AAAA-4AAA-8AAA-111111111111.rtfd"
            _replace_rtf(pkg, (pkg / "TXT.rtf").read_bytes())
            ok = check(wait_for(lambda: not (box.output / "grocery-list--11111111.md").exists(),
                                timeout=8),
                       "note recoloured to an excluded colour -> mirror file removed",
                       f"{_mirror_names(box)}")
            return ok
        finally:
            engine.stop()


def test_unreadable_container_is_yellow_not_silent():
    with Sandbox(**FAST) as box:
        box.config.set("stickies_dir", str(box.root / "nowhere"))
        engine = _engine(box, export_first=False)
        try:
            st = engine.status()
            ok = check(st.monitoring and not st.healthy and st.last_error
                       and "not found" in st.last_error,
                       "missing container: monitoring but unhealthy with the reason",
                       f"{st.as_dict()}")
            ok &= check(not st.container_readable, "container_readable=False", "")
            return ok
        finally:
            engine.stop()


def test_observer_death_shows_in_status():
    with Sandbox(**FAST) as box:
        engine = _engine(box, export_first=False)
        try:
            ok = check(engine.status().healthy, "healthy before", "")
            engine._kill_observer_for_test()
            st = engine.status()
            ok &= check(not st.healthy and "observer" in (st.last_error or ""),
                        "dead observer -> unhealthy with reason", f"{st.as_dict()}")
            return ok
        finally:
            engine.stop()


def test_second_engine_refuses_and_reports_holder():
    with Sandbox(**FAST) as box:
        first = _engine(box, export_first=False)
        try:
            second = Engine(Config(config_file=box.config.config_file))
            try:
                second.start(export_first=False)
                ok = check(False, "", "second engine started despite the lock")
            except EngineError as error:
                ok = check(str(os.getpid()) in str(error), "second engine refused, names the PID",
                           f"{error}")
            ok &= check(second.status().lock_holder_pid == os.getpid(),
                        "status of the refused engine shows the holder", "")
            return ok
        finally:
            first.stop()


def test_hot_reload_output_dir_triggers_export():
    with Sandbox(**FAST) as box:
        engine = _engine(box)
        try:
            wait_for(lambda: len(box.mirror_files()) == 7)
            other = box.root / "elsewhere"
            # Another process edits the config file.
            Config(config_file=box.config.config_file).set_target("default", "output_dir", str(other))
            ok = check(wait_for(engine.reload_config_if_changed, timeout=3),
                       "config change on disk detected", "")
            ok &= check(wait_for(lambda: len(list(other.glob("*.md"))) == 7, timeout=8),
                        "new output folder populated by a full export", f"{list(other.glob('*'))}")
            return ok
        finally:
            engine.stop()


if __name__ == "__main__":
    tests = [test_start_exports_then_watches, test_edit_via_replace_converts_once,
             test_attribute_rewrite_is_unchanged_not_suppressed,
             test_newborn_flat_file_then_package, test_mid_save_gap_is_not_a_deletion,
             test_directory_vanishing_is_the_deletion, test_burst_of_creations_all_land,
             test_recolour_to_excluded_removes_file,
             test_unreadable_container_is_yellow_not_silent, test_observer_death_shows_in_status,
             test_second_engine_refuses_and_reports_holder,
             test_hot_reload_output_dir_triggers_export]
    exit(0 if run_suite("watcher engine tests", tests) else 1)


# End of file #
