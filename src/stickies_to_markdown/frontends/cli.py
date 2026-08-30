"""
Flag-driven front end (Phase 1). Printing happens HERE, never in the engine.

    stickies2md --once                 full export, then exit
    stickies2md --once --dry-run      show what would change, write nothing
    stickies2md --show-log            print the log
    stickies2md --follow-log          live log (handles rotation)
    stickies2md --config PATH         alternate config file
    stickies2md --output-dir PATH     override for this run (not saved)
    stickies2md --set KEY=VALUE       change a setting persistently
"""

import os
import sys
import time
import argparse

from stickies_to_markdown._version import __version__
from stickies_to_markdown.engine.config import (
    Config, ConfigError, FILENAME_STYLES, ON_DELETE_CHOICES,
    CONVERTER_CHOICES, FLAVOR_CHOICES,
)
from stickies_to_markdown.engine.logsetup import setup_logging
from stickies_to_markdown.engine.processor import NoteProcessor
from stickies_to_markdown.engine.events import EventQueue


def build_parser():
    parser = argparse.ArgumentParser(
        prog="stickies2md",
        description="One-way mirror of Apple Stickies into annotated Markdown.")
    parser.add_argument("--version", "-V", action="version",
                        version=f"stickies2md {__version__}")
    parser.add_argument("--config", "-c", metavar="PATH",
                        help="alternate config file")
    action = parser.add_argument_group("actions")
    action.add_argument("--once", "-o", action="store_true",
                        help="export every note once, then exit")
    action.add_argument("--show-log", "-l", action="store_true",
                        help="print the log file")
    action.add_argument("--follow-log", "-f", action="store_true",
                        help="follow the log file (Ctrl-C to stop)")
    action.add_argument("--show-config", action="store_true",
                        help="print the effective configuration")
    action.add_argument("--set", metavar="KEY=VALUE", action="append",
                        default=[], help="persistently change a setting")
    over = parser.add_argument_group("per-run overrides (not saved)")
    over.add_argument("--dry-run", "-d", action="store_true",
                      help="log and report, write nothing")
    over.add_argument("--stickies-dir", metavar="PATH")
    over.add_argument("--output-dir", metavar="PATH")
    over.add_argument("--converter", choices=CONVERTER_CHOICES)
    over.add_argument("--flavor", choices=FLAVOR_CHOICES)
    over.add_argument("--filename-style", choices=FILENAME_STYLES)
    over.add_argument("--on-delete", choices=ON_DELETE_CHOICES)
    return parser


def run_cli(argv):
    args = build_parser().parse_args(argv)
    try:
        config = Config(config_file=args.config)
    except ConfigError as error:
        print(f"Config error: {error}", file=sys.stderr)
        return 2

    if args.set:
        return _apply_sets(config, args.set)
    if args.show_config:
        for key in sorted(config.config):
            print(f"{key} = {config.config[key]!r}")
        return 0
    if args.show_log:
        return _show_log(config)
    if args.follow_log:
        return _follow_log(config)
    if args.once:
        return _run_once(config, args)

    build_parser().print_help()
    return 0


# --- actions ---------------------------------------------------------------

def _run_once(config, args):
    overrides = {}
    for key, value in (("dry_run", args.dry_run or None),
                       ("stickies_dir", args.stickies_dir),
                       ("output_dir", args.output_dir),
                       ("converter", args.converter),
                       ("flavor", args.flavor),
                       ("filename_style", args.filename_style),
                       ("on_delete", args.on_delete)):
        if value:
            overrides[key] = value
    run_config = config.detached(**overrides) if overrides else config

    if not run_config.output_dir():
        print("No output folder configured. Set one with:\n"
              "    stickies2md --set output_dir=~/path/to/Synced_from_Stickies\n"
              "or for this run only:  stickies2md --once --output-dir PATH",
              file=sys.stderr)
        return 2

    setup_logging(run_config)
    events = EventQueue()
    processor = NoteProcessor(run_config, events)
    counters = processor.export_all()

    for event in events.drain():
        if event.kind == "error":
            print(f"  error: {event.path}: {event.detail}", file=sys.stderr)

    mode = " (dry run)" if run_config.get("dry_run") else ""
    print(f"Export{mode}: {counters.converted} converted, "
          f"{counters.unchanged} unchanged, {counters.deleted} deleted, "
          f"{counters.errors} errors")
    print(f"Output: {run_config.output_dir()}")
    return 1 if counters.errors else 0


def _apply_sets(config, assignments):
    for assignment in assignments:
        key, sep, raw = assignment.partition("=")
        if not sep:
            print(f"--set needs KEY=VALUE, got: {assignment}", file=sys.stderr)
            return 2
        key = key.strip()
        if key not in config.default_config:
            print(f"Unknown setting: {key}\nKnown: "
                  f"{', '.join(sorted(config.default_config))}", file=sys.stderr)
            return 2
        config.set(key, _coerce(raw.strip(), config.default_config[key]))
        print(f"{key} = {config.get(key)!r}")
    return 0


def _coerce(raw, default):
    if isinstance(default, list):
        return [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(default, bool):
        return raw.lower() in ("1", "true", "yes", "on")
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, int):
        return int(raw)
    return raw


def _show_log(config):
    path = config.get("log_file")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            sys.stdout.write(handle.read())
    except FileNotFoundError:
        print(f"No log yet at {path}")
    return 0


def _follow_log(config, poll=0.5):
    """Pure-python tail -f that survives rotation."""
    path = config.get("log_file")
    print(f"Following {path}  (Ctrl-C to stop)")
    handle = None
    inode = None
    try:
        while True:
            try:
                stat_info = os.stat(path)
            except OSError:
                stat_info = None
            if stat_info and (handle is None or stat_info.st_ino != inode):
                first_open = handle is None
                if handle:
                    sys.stdout.write(handle.read())     # drain the rotated file
                    handle.close()
                handle = open(path, "r", encoding="utf-8", errors="replace")
                inode = stat_info.st_ino
                if first_open and stat_info.st_size > 8192:
                    handle.seek(stat_info.st_size - 8192)
                    handle.readline()   # drop a partial line
            if handle:
                chunk = handle.read()
                if chunk:
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
            time.sleep(poll)
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        if handle:
            handle.close()


# End of file #
