"""
Interactive terminal menu: settings, watcher control, live view, logs, and
the install/maintain screen for the terminal command and the .app bundle.

Rendering rule: this module prints only from the main thread, and only
when it chooses to. Nothing arrives from the watcher unasked - the menu
pulls events from the engine queue when it redraws, and the "Watch
activity" screen is the deliberate live view (Ctrl-C leaves it).
"""

import os
import sys
import time
import platform
import threading
import subprocess

from collections import deque

from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.console import Console, Group

from stickies_to_markdown._version import __version__
from stickies_to_markdown.engine import Config, Engine, EngineError
from stickies_to_markdown.engine.config import (
    FILENAME_STYLES, ON_DELETE_CHOICES, CONVERTER_CHOICES, FLAVOR_CHOICES, DEFAULT_SUBFOLDER)
from stickies_to_markdown.engine.stickies import COLOR_NAMES, container_readable
from stickies_to_markdown.engine.convert import pandoc_available
from stickies_to_markdown.engine.emitters import parse_flavors, EmitterError
from stickies_to_markdown.frontends import installer, bundle
from stickies_to_markdown.frontends.render import (
    uptime_str, tail_lines, follow_log, event_markup, status_summary, log_line_markup)


RECENT_EVENTS = 200
LIVE_ROWS = 15
LIVE_REFRESH = 0.5


class StickiesTUI:

    def __init__(self, config=None, console=None):
        self.console = console or Console()
        self.engine = Engine(config or Config())
        self.config = self.engine.config
        self.recent = deque(maxlen=RECENT_EVENTS)

    # --- helpers -----------------------------------------------------------

    def pause(self):
        self.console.input("\n[dim]Press Enter to continue...[/dim]")

    def refresh_state(self):
        """Pull config changes and events; never blocks."""
        self.engine.reload_config_if_changed()
        self.config = self.engine.config
        for event in self.engine.events.drain():
            self.recent.append(event)
        return self.engine.status()

    def last_notable_event(self):
        for event in reversed(self.recent):
            if event.kind != "unchanged":
                return event
        return None

    def ask(self, prompt, **kwargs):
        return Prompt.ask(prompt, console=self.console, **kwargs)

    # --- main menu ---------------------------------------------------------

    def show_menu(self):
        while True:
            status = self.refresh_state()
            self.console.clear()
            self.console.print(f"\n[bold cyan]═══ Stickies to Markdown v{__version__} ═══[/bold cyan]\n")

            first, second = status_summary(status, self.last_notable_event())
            dot = "[green]●[/green]" if status.monitoring and status.healthy else \
                  "[red]●[/red]" if not status.healthy else "[yellow]●[/yellow]"
            self.console.print(f"Status: {dot} {first}")
            if second:
                self.console.print(f"        [dim]{second}[/dim]")
            targets = self.config.targets()
            if not targets:
                self.console.print("Outputs: [red]none configured - Settings > Outputs[/red]")
            else:
                for t in targets:
                    self.console.print(f"Output [bold]{t.name}[/bold]: {t.output_dir() or '[red]no folder[/red]'}"
                                       f"  [dim]{t.get('flavor')}, on delete {t.on_delete()}[/dim]")
            self.console.print("[dim]Settings save on change and apply live to a running watcher[/dim]\n")

            locked_elsewhere = status.lock_holder_pid is not None and not status.monitoring
            self.console.print("1. ⚙️   Settings")
            self.console.print("2. 👁️   View configuration")
            if status.monitoring:
                self.console.print("3. ⏸️   Stop watching")
            elif locked_elsewhere:
                self.console.print(f"3. ▶️   Start watching [dim](running in PID {status.lock_holder_pid})[/dim]")
            else:
                self.console.print("3. ▶️   Start watching")
            self.console.print("4. 📡  Watch activity (live view)")
            self.console.print("5. 🔄  Export all notes now")
            self.console.print("6. 📊  View log")
            self.console.print("7. 📂  Open mirror folder")
            self.console.print("8. 🛠️   Install / maintain terminal command & app\n")
            self.console.print("Q. 🚪  Quit\n")

            choice = self.ask("Select option", default="", show_default=False).strip().upper()
            if not choice:
                continue
            if choice == "1":
                self.settings()
            elif choice == "2":
                self.view_configuration()
            elif choice == "3":
                self.toggle_watching()
            elif choice == "4":
                self.watch_activity()
            elif choice == "5":
                self.export_now()
            elif choice == "6":
                self.view_log()
            elif choice == "7":
                self.open_output_folder()
            elif choice == "8":
                self.install_menu()
            elif choice == "Q":
                self.engine.stop()
                self.console.print("\n[cyan]Goodbye![/cyan]\n")
                return
            else:
                self.console.print(f"[red]Invalid option: {choice}[/red]")
                self.pause()

    # --- watcher -----------------------------------------------------------

    def toggle_watching(self):
        status = self.engine.status()
        if status.monitoring:
            self.engine.stop()
            self.console.print("[green]Watcher stopped.[/green]")
            self.pause()
            return
        if not self.config.has_outputs():
            self.console.print("[red]No output configured. Settings > Outputs > add one first.[/red]")
            self.pause()
            return
        try:
            self.engine.start()
        except EngineError as error:
            self.console.print(f"[red]{error}[/red]")
            self.console.print("[yellow]Settings and logs are still available here; "
                               "that process picks up changes live.[/yellow]")
            self.pause()
            return
        status = self.refresh_state()
        if status.healthy:
            self.console.print(f"[green]Watching {self.config.stickies_dir()}[/green]")
            self.console.print("An initial export of every note runs in the background.")
        else:
            self.console.print(f"[red]Started with a problem: {status.last_error}[/red]")
        self.pause()

    def export_now(self):
        if not self.config.has_outputs():
            self.console.print("[red]No output configured.[/red]")
            self.pause()
            return
        status = self.engine.status()
        if status.monitoring:
            self.engine.request_full_export()
            self.console.print("[green]Full export queued on the watcher; "
                               "see Watch activity.[/green]")
        else:
            self.console.print("Exporting...")
            counters = self.engine.export_once()
            c = counters.as_dict()
            self.console.print(f"[green]Done:[/green] {c['converted']} converted, "
                               f"{c['unchanged']} unchanged, {c['deleted']} deleted, "
                               f"{c['excluded']} excluded, {c['errors']} errors")
            for event in self.engine.events.drain():
                self.recent.append(event)
                if event.kind in ("error", "conflict"):
                    self.console.print(event_markup(event))
        self.pause()

    def watch_activity(self):
        """Live view. Reads the engine queue, or tails the log when another process watches."""
        status = self.refresh_state()
        from_log = not status.monitoring and status.lock_holder_pid is not None
        log_file = self.config.get("log_file")

        self.console.clear()
        source = f"log file (watcher is PID {status.lock_holder_pid})" if from_log else "this process"
        self.console.print(f"[bold]Watch activity[/bold] — source: {source}")
        self.console.print("[dim]Press Ctrl-C to return to the menu[/dim]\n")

        log_lines = deque(maxlen=LIVE_ROWS)
        stop_tail = {"flag": False}
        tail_thread = None
        if from_log and os.path.exists(log_file):
            tail_thread = threading.Thread(
                target=follow_log,
                args=(log_file, log_lines.append, lambda: stop_tail["flag"], LIVE_ROWS),
                daemon=True)
            tail_thread.start()

        def render():
            status = self.refresh_state()
            first, _second = status_summary(status)
            header = f"[bold]{first}[/bold]"
            if status.monitoring:
                header += (f"\nconverted {status.converted_session}, unchanged "
                           f"{status.unchanged_session}, deleted {status.deleted_session}, "
                           f"errors {status.errors_session}, uptime {uptime_str(status.started_at)}")
            table = Table(show_header=False, box=None, padding=(0, 1))
            table.add_column("event", overflow="ellipsis", no_wrap=True)
            if from_log:
                for line in list(log_lines):
                    table.add_row(log_line_markup(line))
            else:
                for event in list(self.recent)[-LIVE_ROWS:]:
                    table.add_row(event_markup(event, width=self.console.width - 6))
            if table.row_count == 0:
                table.add_row("[dim]No activity yet[/dim]")
            return Group(Panel(header, title="Status", border_style="cyan"),
                         Panel(table, title="Recent activity", border_style="dim"))

        try:
            with Live(render(), console=self.console, refresh_per_second=4, screen=False) as live:
                while True:
                    self._live_wait()
                    live.update(render())
        except KeyboardInterrupt:
            pass
        finally:
            stop_tail["flag"] = True
            if tail_thread:
                tail_thread.join(timeout=2)
        self.console.print("\n[dim]Returning to menu[/dim]")

    def _live_wait(self):
        """One tick of the live view. Tests override this to inject Ctrl-C."""
        time.sleep(LIVE_REFRESH)

    # --- settings ----------------------------------------------------------

    def settings(self):
        try:
            self._settings_loop()
        except KeyboardInterrupt:
            raise
        except Exception as error:      # noqa: BLE001 - a screen must never blank silently
            self.console.print(f"\n[red]Settings screen failed: {type(error).__name__}: {error}[/red]")
            self.console.print(f"[dim]{self.config.config_file}[/dim]")
            self.pause()

    def _settings_loop(self):
        while True:
            self.refresh_state()
            self.console.clear()
            self.console.print("\n[bold]Settings[/bold]  [dim](saved on change; a running watcher picks them up)[/dim]\n")
            c = self.config
            if pandoc_available(block=False) is None:
                self.console.print("[dim]Checking for pandoc...[/dim]")
                pandoc_available()          # one-time, cached; a second or two at most
                self.console.clear()
                self.console.print("\n[bold]Settings[/bold]  [dim](saved on change; a running watcher picks them up)[/dim]\n")
            pandoc_note = "available" if pandoc_available() else "not installed"
            rows = [
                ("1", "Stickies folder", c.stickies_dir()),
                ("2", "Converter", f"{c.get('converter')}  [dim](pandoc {pandoc_note})[/dim]"),
                ("3", "Debounce / settle seconds",
                 f"{c.get('debounce_seconds')} / {c.get('settle_seconds')}"),
                ("4", "Code block: min escapes / density",
                 f"{c.get('code_block_min_escapes')} / {c.get('code_block_density')}"),
                ("5", "Dry run", "[cyan]ON[/cyan]" if c.get("dry_run") else "off"),
                ("6", "Log level", c.get("log_level")),
                ("7", "This machine's label / id", f"{c.machine_label()}"
                 + ("  [dim](hostname)[/dim]" if not c.get("machine_label") else "")
                 + f"  [dim]/ {c.machine_id()}[/dim]"),
            ]
            self.console.print("[bold cyan]Global[/bold cyan]")
            table = Table(show_header=False, box=None, padding=(0, 2))
            for number, label, value in rows:
                table.add_row(f"[bold]{number}[/bold]", label, str(value))
            self.console.print(table)

            self.console.print("\n[bold cyan]Outputs[/bold cyan]  [dim](one block per mirror folder)[/dim]")
            targets = c.targets()
            letters = [chr(ord("A") + i) for i in range(len(targets))]
            table = Table(show_header=False, box=None, padding=(0, 2))
            for letter, t in zip(letters, targets):
                table.add_row(f"[bold]{letter}[/bold]", t.name,
                              t.output_dir() or "[red]no folder[/red]",
                              f"[dim]{t.get('flavor')}, {t.get('filename_style')}, "
                              f"on delete {t.on_delete()}[/dim]")
            if not targets:
                table.add_row("", "[red]none - add one below[/red]", "", "")
            table.add_row("[bold]+[/bold]", "Add an output", "", "")
            if targets:
                table.add_row("[bold]-[/bold]", "Remove an output", "", "")
            self.console.print(table)
            self.console.print("\n0. Back\n")

            choices = [r[0] for r in rows] + letters + ["+", "0"] + (["-"] if targets else [])
            choice = self.ask("Change which", default="0").strip().upper()
            if choice not in choices:
                self.console.print(f"[red]Not an option: {choice}[/red]")
                self.pause()
                continue
            if choice == "0":
                return
            if choice in letters:
                self.output_settings(targets[letters.index(choice)].name)
            elif choice == "+":
                self.add_output()
            elif choice == "-":
                self.remove_output()
            else:
                getattr(self, f"_set_g{choice}")()

    # --- global settings ---------------------------------------------------

    def _set_g1(self):
        current = self.config.get("stickies_dir") or ""
        value = self._ask_folder("Stickies folder", current)
        if value is None:
            return
        expanded = os.path.expanduser(value)
        if not os.path.isdir(expanded):
            self.console.print("[red]Not a folder.[/red]")
            self.pause()
            return
        self.config.set("stickies_dir", value)
        readable, reason = container_readable(expanded)
        self.console.print("[green]Saved; readable.[/green]" if readable else f"[red]Saved, but: {reason}[/red]")
        self.pause()

    def _set_g2(self):
        self._set_choice("converter", CONVERTER_CHOICES, "Converter")

    def _set_g3(self):
        for key in ("debounce_seconds", "settle_seconds"):
            raw = self.ask(key, default=str(self.config.get(key)))
            try:
                self.config.set(key, float(raw))
            except ValueError:
                self.console.print("[red]Not a number, unchanged.[/red]")

    def _set_g4(self):
        self.console.print("[dim]A note needing at least MIN escapes and DENSITY per 100 chars "
                           "is emitted as a fenced code block. 0 disables.[/dim]")
        for key, cast in (("code_block_min_escapes", int), ("code_block_density", float)):
            raw = self.ask(key, default=str(self.config.get(key)))
            try:
                self.config.set(key, cast(raw))
            except ValueError:
                self.console.print("[red]Not a number, unchanged.[/red]")

    def _set_g5(self):
        self._set_bool("dry_run", "Dry run (log and report, write nothing)?")

    def _set_g6(self):
        self._set_choice("log_level", ("DEBUG", "INFO", "WARNING", "ERROR"), "Log level")

    def _set_g7(self):
        self.console.print("[dim]The label is for humans (source-machine, {machine}); the id "
                           f"({self.config.machine_id()}, from the hardware UUID) is what keeps "
                           "machines apart and never changes. Empty label = hostname.[/dim]")
        value = self.ask("Machine label", default=self.config.get("machine_label") or "")
        self.config.set("machine_label", value.strip().lower())

    # --- outputs -----------------------------------------------------------

    def _ask_folder(self, prompt, current):
        self.console.print(f"[dim]Current: {current or 'not set'}  (drag a folder here, or paste; "
                           f"empty keeps it)[/dim]")
        value = self.ask(prompt, default=current).strip().strip("'\"").replace("\\ ", " ")
        return value or None

    def _looks_like_someone_elses_folder(self, folder):
        """An existing folder with foreign content (an Obsidian vault root,
        a Documents folder) is almost never what a mirror should write
        into; a dedicated subfolder is. Returns a reason or ""."""
        expanded = os.path.expanduser(folder)
        if not os.path.isdir(expanded):
            return ""
        try:
            entries = os.listdir(expanded)
        except OSError:
            return ""
        if ".obsidian" in entries:
            return "this is an Obsidian vault ROOT"
        from stickies_to_markdown.engine.writer import MARKER_VALUE
        foreign = 0
        for name in entries:
            if name.startswith(".") or name in ("attachments", "_deleted", "_conflicts"):
                continue
            path = os.path.join(expanded, name)
            if os.path.isdir(path):
                foreign += 1
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    if MARKER_VALUE in handle.read(2048):
                        continue
            except OSError:
                pass
            foreign += 1
        return f"it already holds {foreign} item(s) that are not ours" if foreign else ""

    def _confirm_folder(self, folder):
        reason = self._looks_like_someone_elses_folder(folder)
        if not reason:
            return True
        self.console.print(f"[yellow]Careful:[/yellow] {reason}. The mirror writes its files "
                           f"straight into the folder you give it - a dedicated subfolder such as\n"
                           f"    {folder.rstrip('/')}/Synced_from_Stickies\n"
                           f"keeps them together and out of the way.")
        return Confirm.ask("Use this folder anyway?", default=False, console=self.console)

    def add_output(self):
        self.console.print("\n[bold]Add an output[/bold]")
        name = self.ask("Short name (e.g. vault, plain)", default="").strip()
        if not name:
            return
        if "." in name or self.config.target(name) is not None:
            self.console.print("[red]Names must be unique and contain no '.'[/red]")
            self.pause()
            return
        self.console.print("[dim]Point at the vault (or any folder); the mirror is created inside it "
                           f"as '{DEFAULT_SUBFOLDER}/' - change that on the output's screen.[/dim]")
        folder = self._ask_folder("Folder", "")
        if not folder:
            return
        flavor = self._ask_flavors("generic")
        target = self.config.add_target(name, folder, flavor=flavor)
        self.console.print(f"[green]Added output '{name}'.[/green] Files will go to "
                           f"{target.output_dir()} (created on first export).")
        self.pause()

    def _ask_flavors(self, current):
        self.console.print("[dim]Flavors add plugin-specific front matter; combine with commas. "
                           f"Choices: {', '.join(FLAVOR_CHOICES)}[/dim]")
        while True:
            value = self.ask("Flavor(s)", default=str(current)).strip()
            try:
                return ", ".join(parse_flavors(value))
            except EmitterError as error:
                self.console.print(f"[red]{error}[/red]")

    def remove_output(self):
        names = [t.name for t in self.config.targets()]
        name = self.ask("Remove which output", choices=names + ["cancel"], default="cancel")
        if name == "cancel":
            return
        if Confirm.ask(f"Remove '{name}' from the config? (its folder is left untouched)",
                       default=False, console=self.console):
            self.config.remove_target(name)
            self.console.print(f"[green]Removed '{name}'.[/green]")
        self.pause()

    def output_settings(self, name):
        while True:
            self.refresh_state()
            t = self.config.target(name)
            if t is None:
                return
            self.console.clear()
            self.console.print(f"\n[bold]Output '{name}'[/bold]  [dim](saved on change)[/dim]\n")
            rows = [
                ("1", "Folder", t.base_dir() or "[red]not set[/red]"),
                ("13", "Subfolder (blank = write directly into the folder)",
                 t.subfolder() or "[yellow]none[/yellow]"),
                ("14", "-> files go to", t.output_dir() or "[red]-[/red]"),
                ("2", "Front-matter flavor(s)", t.get("flavor")),
                ("3", "Filename style", t.get("filename_style")),
                ("4", "When a note is deleted (on_delete)", t.on_delete()),
                ("5", "Archive folder (deleted_dir)", t.get("deleted_dir")),
                ("6", "Excluded colours", ", ".join(t.get("exclude_colors") or []) or "none"),
                ("7", "Excluded title pattern", t.get("exclude_title_regex") or "none"),
                ("8", "When a note becomes excluded (on_exclude)", t.on_exclude()),
                ("9", "Read-only mirror files", "yes" if t.get("read_only_output") else "no"),
                ("10", "Include attachments", "yes" if t.get("include_attachments") else "no"),
                ("11", "Front matter", "yes" if t.get("front_matter") else "no"),
                ("12", "Rename this output", name),
            ]
            table = Table(show_header=False, box=None, padding=(0, 2))
            for number, label, value in rows:
                table.add_row(f"[bold]{number}[/bold]", label, str(value))
            self.console.print(table)
            self.console.print("\n0. Back\n")
            choice = self.ask("Change which", choices=[r[0] for r in rows if r[0] != "14"] + ["0"],
                              default="0")
            if choice == "0":
                return
            if choice == "13":
                self.console.print("[dim]The mirror is created inside the folder under this name, "
                                   "so nothing spills into a vault root. Enter '-' for none; "
                                   "{machine} expands to this Mac's label.[/dim]")
                value = self.ask("Subfolder", default=t.subfolder() or "-").strip()
                self.config.set_target(name, "subfolder", "" if value in ("-", "") else value)
                continue
            if choice == "12":
                new_name = self.ask("New name", default=name).strip()
                if new_name and new_name != name:
                    try:
                        self.config.rename_target(name, new_name)
                        name = new_name
                    except ValueError as error:
                        self.console.print(f"[red]{error}[/red]")
                        self.pause()
                continue
            getattr(self, f"_set_o{choice}")(name)

    def _set_target_choice(self, name, key, choices, prompt):
        value = self.ask(prompt, choices=list(choices), default=str(self.config.target(name).get(key)))
        self.config.set_target(name, key, value)

    def _set_target_bool(self, name, key, prompt):
        current = bool(self.config.target(name).get(key))
        self.config.set_target(name, key, Confirm.ask(prompt, default=current, console=self.console))

    def _set_o1(self, name):
        target = self.config.target(name)
        value = self._ask_folder("Folder", target.get("output_dir") or "")
        if value and (target.subfolder() or self._confirm_folder(value)):
            self.config.set_target(name, "output_dir", value)
            if not os.path.isdir(os.path.expanduser(value)):
                self.console.print("[dim]It will be created on first export.[/dim]")
                self.pause()

    def _set_o2(self, name):
        self.config.set_target(name, "flavor",
                               self._ask_flavors(self.config.target(name).get("flavor")))

    def _set_o3(self, name):
        self._set_target_choice(name, "filename_style", FILENAME_STYLES, "Filename style")

    def _set_o4(self, name):
        self._set_target_choice(name, "on_delete", ON_DELETE_CHOICES, "When a note is deleted")

    def _set_o5(self, name):
        value = self.ask("Archive folder (relative to mirror, or absolute)",
                         default=str(self.config.target(name).get("deleted_dir")))
        if value.strip():
            self.config.set_target(name, "deleted_dir", value.strip())

    def _set_o6(self, name):
        self.console.print(f"[dim]Colours: {', '.join(COLOR_NAMES)}. Empty clears.[/dim]")
        value = self.ask("Excluded colours (comma-separated)",
                         default=", ".join(self.config.target(name).get("exclude_colors") or []))
        colors = [c.strip().lower() for c in value.split(",") if c.strip()]
        bad = [c for c in colors if c not in COLOR_NAMES]
        if bad:
            self.console.print(f"[red]Unknown colour(s): {', '.join(bad)}[/red]")
            self.pause()
            return
        self.config.set_target(name, "exclude_colors", colors)

    def _set_o7(self, name):
        self.console.print("[dim]A regular expression tested against the first line. Empty "
                           "clears. Tip: exclude by colour instead - it can be chosen before "
                           "the note autosaves.[/dim]")
        value = self.ask("Excluded title pattern",
                         default=self.config.target(name).get("exclude_title_regex") or "")
        self.config.set_target(name, "exclude_title_regex", value.strip())

    def _set_o8(self, name):
        self._set_target_choice(name, "on_exclude", ON_DELETE_CHOICES, "When a note becomes excluded")

    def _set_o9(self, name):
        self._set_target_bool(name, "read_only_output", "Make mirror files read-only (chmod 444)?")

    def _set_o10(self, name):
        self._set_target_bool(name, "include_attachments", "Copy attachments?")

    def _set_o11(self, name):
        self._set_target_bool(name, "front_matter", "Write YAML front matter?")

    # --- shared prompt helpers ---------------------------------------------

    def _set_choice(self, key, choices, prompt=None):
        value = self.ask(prompt or key, choices=list(choices), default=str(self.config.get(key)))
        self.config.set(key, value)

    def _set_bool(self, key, prompt):
        self.config.set(key, Confirm.ask(prompt, default=bool(self.config.get(key)),
                                         console=self.console))

    def view_configuration(self):
        self.console.clear()
        self.console.print("\n[bold]Configuration[/bold]\n")
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("key")
        table.add_column("value")
        for key in sorted(self.config.config):
            table.add_row(key, repr(self.config.config[key]))
        self.console.print(table)
        self.console.print(f"\n[dim]{self.config.config_file}[/dim]")
        self.pause()

    # --- logs & folders ----------------------------------------------------

    def view_log(self):
        path = self.config.get("log_file")
        self.console.clear()
        self.console.print(f"[bold]Log[/bold] [dim]{path}[/dim]\n")
        lines = tail_lines(path, 60)
        if not lines:
            self.console.print("[dim]No log yet.[/dim]")
        for line in lines:
            self.console.print(log_line_markup(line))
        self.console.print("\n[dim]stickies2md --follow-log  for a live tail[/dim]")
        self.pause()

    def open_output_folder(self):
        targets = [t for t in self.config.targets() if t.output_dir()]
        if not targets:
            path = ""
        elif len(targets) == 1:
            path = targets[0].output_dir()
        else:
            name = self.ask("Which output", choices=[t.name for t in targets], default=targets[0].name)
            path = self.config.target(name).output_dir()
        if not path or not os.path.isdir(path):
            self.console.print("[red]Mirror folder not set or not created yet.[/red]")
        elif sys.platform == "darwin":
            subprocess.run(["open", path])
        elif os.name == "nt":
            os.startfile(path)                          # noqa: S606
        else:
            subprocess.run(["xdg-open", path])
        self.pause()

    # --- install / maintain ------------------------------------------------

    def install_menu(self):
        while True:
            self.console.clear()
            self.console.print("\n[bold]Install / maintain[/bold]\n")
            stub = installer.stub_path(installer.default_bin_dir())
            stub_state = ("[green]installed[/green]" if installer.is_our_stub(stub)
                          else "[yellow]not installed[/yellow]")
            recorded = installer.recorded_interpreter(stub)
            if recorded and recorded != sys.executable:
                stub_state += f" [red](points at another interpreter: {recorded})[/red]"
            self.console.print(f"Terminal command  [dim]{stub}[/dim]  {stub_state}")
            if sys.platform == "darwin":
                app = bundle.bundle_path(bundle.default_app_dir())
                app_state = ("[green]installed[/green]" if bundle.is_our_bundle(app)
                             else "[yellow]not installed[/yellow]")
                kind = bundle.launcher_kind(app)
                if kind:
                    app_state += f" [dim]({kind} launcher)[/dim]"
                self.console.print(f"Menu bar app     [dim]{app}[/dim]  {app_state}")
            self.console.print(f"[dim]This interpreter: {sys.executable}[/dim]\n")

            self.console.print("1. Install or refresh the terminal command  [dim](records this interpreter)[/dim]")
            self.console.print("2. Remove the terminal command")
            if sys.platform == "darwin":
                self.console.print("3. Install or refresh the menu bar app bundle")
                self.console.print("4. Remove the app bundle")
                self.console.print("5. Launch the app bundle now")
            self.console.print("\n0. Back\n")
            choices = ["1", "2", "0"] + (["3", "4", "5"] if sys.platform == "darwin" else [])
            choice = self.ask("Select option", choices=choices, default="0")
            if choice == "0":
                return
            self.console.print()
            if choice == "1":
                installer.install_command(out=self.console.print)
            elif choice == "2":
                if Confirm.ask("Remove the terminal command?", default=False, console=self.console):
                    installer.uninstall_command(out=self.console.print)
            elif choice == "3":
                bundle.install_app(out=self.console.print)
            elif choice == "4":
                if Confirm.ask("Remove the app bundle?", default=False, console=self.console):
                    bundle.uninstall_app(out=self.console.print)
            elif choice == "5":
                app = bundle.bundle_path(bundle.default_app_dir())
                if bundle.is_our_bundle(app):
                    subprocess.run(["open", app])
                    self.console.print("Launched. Quit the watcher here if it is running, or the "
                                       "app's Start will report the lock holder.")
                else:
                    self.console.print("[red]Install it first (option 3).[/red]")
            self.pause()


def run_tui(config=None):
    tui = StickiesTUI(config)
    try:
        tui.show_menu()
    except KeyboardInterrupt:
        tui.engine.stop()
        tui.console.print("\n[cyan]Goodbye![/cyan]")
    return 0


# End of file #
