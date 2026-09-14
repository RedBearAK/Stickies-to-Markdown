#!/usr/bin/env python3
"""
The interactive menu, driven by scripted input through a rich Console with
a StringIO. Covers the paths that don't need a terminal: menu render,
settings edits persisting, export-now, view configuration/log, the
install screen's state line, and quitting stops the engine.
"""

import io
import os
import tempfile

from rich.console import Console

from _helpers import Sandbox, check, run_suite

from stickies_to_markdown.frontends import tui as tui_module
from stickies_to_markdown.frontends.tui import StickiesTUI


def _tui(box, keys):
    """A TUI whose prompts read from `keys`, printing to a captured buffer."""
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, width=100)
    console.input = lambda *_a, **_k: ""            # pause(): Enter
    tui = StickiesTUI(box.config, console=console)
    feed = iter(keys)

    def ask(prompt, **kwargs):
        value = next(feed)
        return value if value != "" else kwargs.get("default", "")

    tui.ask = ask
    tui_module.Confirm.ask = staticmethod(lambda *a, **k: next(feed) == "y")
    return tui, output


def test_menu_renders_and_quits_cleanly():
    with Sandbox() as box:
        tui, out = _tui(box, ["Q"])
        tui.show_menu()
        text = out.getvalue()
        ok = check("Stickies to Markdown" in text and "Install / maintain" in text,
                   "menu rendered with the install entry", text[:300])
        ok &= check(not tui.engine.status().monitoring, "engine stopped on quit", "")
        return ok


def test_settings_change_persists():
    with Sandbox() as box:
        # 1 settings -> A (output "default") -> 4 on_delete -> mark -> 0 back
        #   -> 5 dry run -> yes -> + add output "plain" -> folder -> generic -> 0 -> Q
        other = str(box.root / "plain")
        os.makedirs(other)
        tui, _ = _tui(box, ["1", "A", "4", "mark", "0", "5", "y", "+", "plain", "markdown", other,
                            "generic", "0", "Q"])
        tui.show_menu()
        from stickies_to_markdown.engine import Config
        fresh = Config(config_file=box.config.config_file)
        ok = check(fresh.target("default").on_delete() == "mark" and fresh.get("dry_run") is True,
                   "per-output and global settings edited in the menu are saved",
                   f"{fresh.target('default').on_delete()} {fresh.get('dry_run')}")
        ok &= check([t.name for t in fresh.targets()] == ["default", "plain"]
                    and fresh.target("plain").output_dir() == os.path.join(other, "Synced_from_Stickies"),
                    "a second output added from the menu, with the default subfolder", f"{fresh.targets()}")
        return ok


def test_export_now_and_views():
    with Sandbox() as box:
        tui, out = _tui(box, ["5", "2", "6", "Q"])
        tui.show_menu()
        text = out.getvalue()
        ok = check(len(box.mirror_files()) == 7 and "7 converted" in text,
                   "Export all notes now exports and reports", f"{[f.name for f in box.mirror_files()]}")
        ok &= check("output_dir" in text and "Configuration" in text, "view configuration", "")
        ok &= check("Export complete" in text or "Log" in text, "view log", "")
        return ok


def test_start_stop_from_menu():
    from _helpers import wait_for
    with Sandbox(debounce_seconds=0.3, settle_seconds=0.2) as box:
        tui, out = _tui(box, ["3", "3", "Q"])
        # "Press Enter" after Start: give the worker time to finish the export.
        tui.pause = lambda: wait_for(lambda: len(box.mirror_files()) == 7, timeout=10)
        tui.show_menu()
        text = out.getvalue()
        ok = check("Watching" in text and "Watcher stopped" in text,
                   "start then stop from the menu", text[-500:])
        ok &= check(wait_for(lambda: len(box.mirror_files()) == 7, timeout=10),
                    "starting the watcher ran the initial export", "")
        return ok


def test_install_screen_reports_state_and_installs():
    with Sandbox() as box, tempfile.TemporaryDirectory() as tmp:
        from stickies_to_markdown.frontends import installer
        saved = installer.default_bin_dir
        installer.default_bin_dir = lambda: tmp
        try:
            tui, out = _tui(box, ["8", "1", "0", "Q"])
            tui.show_menu()
            text = out.getvalue()
            ok = check("not installed" in text, "install screen shows 'not installed' first", "")
            ok &= check(os.path.isfile(os.path.join(tmp, "stickies2md")) and "Installed launcher" in text,
                        "option 1 installs the terminal command", text[-400:])
            return ok
        finally:
            installer.default_bin_dir = saved


def test_backup_output_from_menu():
    with Sandbox() as box:
        folder = str(box.root / "backups")
        os.makedirs(folder)
        # + add -> name -> type backup -> folder -> (per-output) B -> 6 snapshots yes -> 0 -> 0 -> Q
        tui, out = _tui(box, ["1", "+", "bk", "backup", folder, "B", "6", "y", "0", "0", "Q"])
        tui.show_menu()
        from stickies_to_markdown.engine import Config
        fresh = Config(config_file=box.config.config_file)
        t = fresh.target("bk")
        ok = check(t is not None and t.type == "backup" and t.get("snapshots") is True,
                   "backup output added and its snapshot toggle set from its own screen", f"{t}")
        ok &= check(t.output_dir().endswith(os.path.join("Stickies_backup.noindex", fresh.machine_id())),
                    "backup subfolder default applied", t.output_dir())
        ok &= check("Backup output 'bk'" in out.getvalue(), "backup screen rendered", "")
        return ok


if __name__ == "__main__":
    tests = [test_menu_renders_and_quits_cleanly, test_settings_change_persists,
             test_export_now_and_views, test_start_stop_from_menu,
             test_install_screen_reports_state_and_installs, test_backup_output_from_menu]
    exit(0 if run_suite("tui tests", tests) else 1)


# End of file #
