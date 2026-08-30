#!/usr/bin/env python3
"""
Mac-side verification for Stickies-to-Markdown (dev_notes/mac_verify.py).

Automates dev_notes/FIRST_SESSION_CHECKLIST.md: probes the real Stickies
container, records findings to a log file, and never writes inside the
container. Re-run freely; each run gets its own timestamped log. Steps are
independent - a failure logs and moves on.

    python3 dev_notes/mac_verify.py                  # all applicable steps
    python3 dev_notes/mac_verify.py --steps 3,4      # just state keys + watch
    python3 dev_notes/mac_verify.py --watch-seconds 45
    python3 dev_notes/mac_verify.py --capture ~/s2m_fixtures --capture-count 8

Steps:
    1  container path, layout, FDA probe
    2  package anatomy (TXT.rtf, header, attachments)
    3  real .SavedStickiesState structure and key names
    4  live-write behavior (edit notes while it watches; inode tracking
       distinguishes in-place rewrite from temp-and-rename)
    5  textutil tier-2 output on a real package
    6  Foundation tier-1 load probe (subprocess; a crash can't kill the run)
    7  converter comparison: tiers on one package via the repo's engine
    8  capture sanitised fixture candidates (only with --capture)

Stdlib only. Safe on Linux with --stickies-dir pointing at test fixtures
(darwin-only steps skip themselves).
"""

import os
import sys
import time
import shutil
import argparse
import plistlib
import subprocess
import traceback

from pathlib import Path
from datetime import datetime

DEFAULT_CONTAINER = Path(
    "~/Library/Containers/com.apple.Stickies/Data/Library/Stickies").expanduser()
TEXTUTIL = "/usr/bin/textutil"
IS_MAC = sys.platform == "darwin"


class Log:
    """Everything to the terminal AND the log file. FINDING: lines are
    the greppable answers to the checklist blanks."""

    def __init__(self, path):
        self.path = Path(path)
        self.handle = open(self.path, "w", encoding="utf-8")
        self.findings = []

    def line(self, text=""):
        print(text)
        self.handle.write(text + "\n")
        self.handle.flush()

    def finding(self, text):
        self.findings.append(text)
        self.line(f"  FINDING: {text}")

    def section(self, title):
        self.line(f"\n{'=' * 66}\n{title}\n{'=' * 66}")

    def close(self):
        self.handle.close()


# --- step 1 ----------------------------------------------------------------

def step_1_container(log, args):
    log.section("1. Container path, layout, FDA probe")
    container = args.stickies_dir
    log.line(f"Probing: {container}")
    try:
        names = sorted(os.listdir(container))
    except PermissionError:
        log.finding("PermissionError listing container -> Full Disk Access "
                    "IS required (grant it to this terminal and re-run)")
        return
    except FileNotFoundError:
        log.finding(f"Container NOT FOUND at {container} - layout assumption "
                    "wrong for this macOS; locate it before anything else")
        return

    log.finding(f"container readable WITHOUT error from this process "
                f"({len(names)} entries)")
    rtfds = [n for n in names if n.endswith(".rtfd")]
    state = [n for n in names if n == ".SavedStickiesState"]
    others = [n for n in names if n not in rtfds and n not in state]
    log.finding(f"{len(rtfds)} .rtfd packages; state file "
                f"{'present' if state else 'MISSING'}")
    if others:
        log.finding(f"other entries (record these): {others}")
    for name in names[:30]:
        log.line(f"    {name}")
    if len(names) > 30:
        log.line(f"    ... and {len(names) - 30} more")


# --- step 2 ----------------------------------------------------------------

def _pick_package(container, prefer_attachment=True):
    packages = sorted(p for p in Path(container).iterdir()
                      if p.suffix == ".rtfd" and p.is_dir())
    if prefer_attachment:
        for pkg in packages:
            extras = [f for f in pkg.iterdir()
                      if f.name != "TXT.rtf" and not f.name.startswith(".")]
            if extras:
                return pkg
    return packages[0] if packages else None


def step_2_anatomy(log, args):
    log.section("2. Package anatomy")
    pkg = _pick_package(args.stickies_dir)
    if pkg is None:
        log.finding("no .rtfd packages found; create a few notes first")
        return
    log.line(f"Examining: {pkg.name}")
    files = sorted(pkg.iterdir())
    for f in files:
        log.line(f"    {f.name}  ({f.stat().st_size} bytes)")
    has_rtf = any(f.name == "TXT.rtf" for f in files)
    log.finding(f"TXT.rtf present: {has_rtf}")
    extras = [f.name for f in files
              if f.name != "TXT.rtf" and not f.name.startswith(".")]
    log.finding(f"attachment files in package: {extras or 'none'}")
    if has_rtf:
        header = (pkg / "TXT.rtf").read_bytes()[:200]
        log.finding(f"RTF header: {header!r}")
    # Every package or just this one?
    missing = [p.name for p in Path(args.stickies_dir).iterdir()
               if p.suffix == ".rtfd" and p.is_dir()
               and not (p / "TXT.rtf").is_file()]
    log.finding(f"packages WITHOUT TXT.rtf: {missing or 'none'}")


# --- step 3 ----------------------------------------------------------------

def _describe(value, depth=0, max_depth=3):
    pad = "    " * depth
    if isinstance(value, dict):
        lines = [f"{pad}dict with {len(value)} keys:"]
        for key, sub in list(value.items())[:20]:
            lines.append(f"{pad}    {key!r}: {type(sub).__name__}"
                         + (f" = {sub!r}"[:90] if not isinstance(sub, (dict, list))
                            else ""))
            if isinstance(sub, (dict, list)) and depth < max_depth:
                lines.extend(_describe(sub, depth + 2, max_depth))
        return lines
    if isinstance(value, list):
        lines = [f"{pad}list of {len(value)}"]
        if value and depth < max_depth:
            lines.append(f"{pad}  [0]:")
            lines.extend(_describe(value[0], depth + 2, max_depth))
        return lines
    return [f"{pad}{type(value).__name__} = {value!r}"[:100]]


def step_3_state(log, args):
    log.section("3. .SavedStickiesState structure  <- the parser guesses these")
    path = Path(args.stickies_dir) / ".SavedStickiesState"
    if not path.is_file():
        log.finding("state file absent")
        return
    try:
        with open(path, "rb") as handle:
            data = plistlib.load(handle)
    except Exception as error:
        log.finding(f"state file did not parse as a plist: {error!r} - "
                    "dump raw header for inspection")
        log.line(f"    raw[:120]: {path.read_bytes()[:120]!r}")
        return
    for line in _describe(data):
        log.line(line)
    log.finding("record: top-level shape, per-note UUID key name, colour key "
                "name/type - then tighten engine/stickies.py candidates")
    log.finding("colour mapping: make 6 notes, one per colour, in a known "
                "order; re-run this step; match values to colours")


# --- step 4 ----------------------------------------------------------------

def _tree_state(container):
    """path -> (inode, size, mtime_ns) for everything in the container."""
    state = {}
    for root, _dirs, files in os.walk(container):
        for name in files:
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
                state[path] = (st.st_ino, st.st_size, st.st_mtime_ns)
            except OSError:
                pass
    return state


def step_4_watch(log, args):
    log.section(f"4. Live-write behavior ({args.watch_seconds}s watch)")
    log.line(">>> Type into an EXISTING note now, pause a few seconds,")
    log.line(">>> type again, then create a NEW note and delete another.")
    log.line(">>> Watching for changes...")
    container = args.stickies_dir
    before = _tree_state(container)
    deadline = time.time() + args.watch_seconds
    seen = 0
    while time.time() < deadline:
        time.sleep(0.2)
        after = _tree_state(container)
        stamp = time.strftime("%H:%M:%S")
        for path in after.keys() - before.keys():
            log.line(f"  {stamp}  CREATED  {os.path.relpath(path, container)}")
            seen += 1
        for path in before.keys() - after.keys():
            log.line(f"  {stamp}  DELETED  {os.path.relpath(path, container)}")
            seen += 1
        for path in after.keys() & before.keys():
            if before[path] != after[path]:
                rel = os.path.relpath(path, container)
                same_inode = before[path][0] == after[path][0]
                how = "IN-PLACE (same inode)" if same_inode \
                    else "REPLACED (new inode = temp-and-rename)"
                log.line(f"  {stamp}  CHANGED  {rel}  {how}")
                seen += 1
        before = after
    if seen == 0:
        log.finding("no changes observed - Stickies may not have autosaved "
                    "in the window; re-run with --watch-seconds 60 and try "
                    "quitting Stickies mid-watch")
    else:
        log.finding(f"{seen} change events; read the CHANGED lines above: "
                    "in-place vs replaced decides the Phase 2 event mapping, "
                    "and .SavedStickiesState churn shows the state-write habit")


# --- step 5 ----------------------------------------------------------------

def step_5_textutil(log, args):
    log.section("5. textutil tier-2 on a real package")
    if not IS_MAC or not os.path.exists(TEXTUTIL):
        log.finding("skipped: textutil unavailable (not macOS)")
        return
    pkg = _pick_package(args.stickies_dir)
    if pkg is None:
        log.finding("no packages to convert")
        return
    proc = subprocess.run([TEXTUTIL, "-convert", "html", "-stdout", str(pkg)],
                          capture_output=True, timeout=30)
    log.finding(f"textutil rc={proc.returncode} on {pkg.name}")
    if proc.returncode == 0:
        text = proc.stdout.decode("utf-8", errors="replace")
        for line in text.splitlines()[:50]:
            log.line(f"    {line[:110]}")
        log.finding("check above: how lists, bold, and <img src=...> appear; "
                    "compare against the _HtmlWalker assumptions")
    else:
        log.line(f"    stderr: {proc.stderr.decode(errors='replace')[:300]}")


# --- step 6 ----------------------------------------------------------------

_FOUNDATION_PROBE = """
import Foundation
url = Foundation.NSURL.fileURLWithPath_({pkg!r})
s, a, e = (Foundation.NSAttributedString.alloc()
           .initWithURL_options_documentAttributes_error_(
               url, {{'DocumentType': 'NSRTFD'}}, None, None))
print('loaded:', s is not None, '| length:', s and s.length(), '| err:', e)
"""


def step_6_foundation(log, args):
    log.section("6. Foundation tier-1 load probe")
    if not IS_MAC:
        log.finding("skipped: not macOS")
        return
    pkg = _pick_package(args.stickies_dir)
    if pkg is None:
        log.finding("no packages to load")
        return
    code = _FOUNDATION_PROBE.format(pkg=str(pkg))
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, timeout=30)
    out = proc.stdout.decode(errors="replace").strip()
    err = proc.stderr.decode(errors="replace").strip()
    if proc.returncode == 0 and "loaded: True" in out:
        log.finding(f"Foundation loads RTFD: {out}")
    elif "ModuleNotFoundError" in err or "ImportError" in err:
        log.finding(f"PyObjC Foundation NOT importable with {sys.executable} "
                    "- run this script with the venv python that has rumps")
    else:
        log.finding(f"Foundation probe FAILED rc={proc.returncode}: "
                    f"{out or err[:300]}")
    log.line("    (trait constants get verified by step 7 on a bold note)")


# --- step 7 ----------------------------------------------------------------

def step_7_converters(log, args):
    log.section("7. Converter comparison (repo engine, all tiers)")
    src = Path(__file__).resolve().parent.parent / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        from stickies_to_markdown.engine.convert import convert
    except ImportError as error:
        log.finding(f"repo engine not importable ({error}); run from the "
                    "repo checkout or pip install first")
        return
    pkg = _pick_package(args.stickies_dir)
    if pkg is None:
        log.finding("no packages to convert")
        return
    log.line(f"Package: {pkg.name}  (pick a BOLD+italic note for the real "
             "trait test; use --stickies-dir + a copy to control which)")
    for tier in ("foundation", "textutil", "text"):
        try:
            markdown, attachments = convert(str(pkg), tier)
            log.line(f"\n  --- {tier} ---")
            for line in markdown.splitlines()[:15]:
                log.line(f"    {line[:110]}")
            log.finding(f"tier '{tier}' produced {len(markdown)} chars, "
                        f"attachments={attachments}")
        except Exception as error:
            log.finding(f"tier '{tier}' failed: {type(error).__name__}: "
                        f"{str(error)[:200]}")
    log.finding("compare emphasis between tiers: if 'foundation' shows ** on "
                "the wrong runs, fix _TRAIT_BOLD/_TRAIT_ITALIC in convert.py")


# --- step 8 ----------------------------------------------------------------

def step_8_capture(log, args):
    log.section("8. Capture fixture candidates")
    if not args.capture:
        log.finding("skipped (pass --capture DIR to stage fixture copies)")
        return
    dest = Path(args.capture).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    packages = sorted(p for p in Path(args.stickies_dir).iterdir()
                      if p.suffix == ".rtfd" and p.is_dir())[:args.capture_count]
    for pkg in packages:
        target = dest / pkg.name
        if target.exists():
            log.line(f"    exists, skipped: {pkg.name}")
            continue
        shutil.copytree(pkg, target)
        log.line(f"    copied: {pkg.name}")
    state = Path(args.stickies_dir) / ".SavedStickiesState"
    if state.is_file() and not (dest / state.name).exists():
        shutil.copy2(state, dest / state.name)
        log.line(f"    copied: {state.name}")
    log.finding(f"{len(packages)} packages staged in {dest}")
    log.finding("REVIEW FOR PRIVATE CONTENT before moving into "
                "tests/fixtures/ - these are verbatim copies of your notes")


STEPS = {1: step_1_container, 2: step_2_anatomy, 3: step_3_state,
         4: step_4_watch, 5: step_5_textutil, 6: step_6_foundation,
         7: step_7_converters, 8: step_8_capture}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--stickies-dir", default=str(DEFAULT_CONTAINER),
                        help="container to probe (default: the real one)")
    parser.add_argument("--steps", default="",
                        help="comma-separated step numbers (default: all)")
    parser.add_argument("--watch-seconds", type=int, default=25,
                        help="step 4 observation window")
    parser.add_argument("--capture", metavar="DIR", default="",
                        help="step 8: stage fixture copies here")
    parser.add_argument("--capture-count", type=int, default=8)
    parser.add_argument("--log", default="",
                        help="log path (default: ./stickies_verify_<ts>.log)")
    args = parser.parse_args()
    args.stickies_dir = os.path.expanduser(args.stickies_dir)

    log_path = args.log or f"stickies_verify_{datetime.now():%Y%m%d-%H%M%S}.log"
    log = Log(log_path)
    log.line(f"Stickies-to-Markdown Mac verification - {datetime.now():%Y-%m-%d %H:%M:%S}")
    log.line(f"platform={sys.platform}  python={sys.version.split()[0]}  "
             f"container={args.stickies_dir}")

    wanted = ([int(s) for s in args.steps.split(",") if s.strip()]
              if args.steps else sorted(STEPS))
    for number in wanted:
        try:
            STEPS[number](log, args)
        except Exception:
            log.line(f"  step {number} CRASHED:")
            for line in traceback.format_exc().splitlines():
                log.line(f"    {line}")

    log.section("Findings summary")
    for finding in log.findings:
        log.line(f"  - {finding}")
    log.line(f"\nFull log: {log.path.resolve()}")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())


# End of file #
