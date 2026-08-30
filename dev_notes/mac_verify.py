#!/usr/bin/env python3
"""
Mac-side verification for Stickies-to-Markdown (dev_notes/mac_verify.py).

Automates dev_notes/FIRST_SESSION_CHECKLIST.md: probes the real Stickies
container, records findings to a log file, and never writes inside the
container. Re-run freely; each run gets its own timestamped log. Steps are
independent - a failure logs and moves on.

    python3 dev_notes/mac_verify.py                  # all applicable steps
    python3 dev_notes/mac_verify.py --steps 3,4      # just state keys + watch
    python3 dev_notes/mac_verify.py --steps 4 --watch-seconds 0   # annotated session
    python3 dev_notes/mac_verify.py --capture ~/s2m_fixtures --capture-count 8

Steps:
    1  container path, layout, FDA probe
    2  package anatomy (TXT.rtf, header, attachments)
    3  real .SavedStickiesState structure and key names
    4  live-write behavior: an annotated observation session. Lines you
       type are stamped into the log between filesystem events; inode
       tracking distinguishes in-place rewrite from temp-and-rename
    5  textutil tier-2 output on a real package
    6  colour calibration: every note's StickyColor as hue/sat + the name
       the engine assigns, for checking the hue bands in stickies.py
    7  converter check: both tiers on EVERY package; lists which notes
       carry bold/italic through textutil and shows those lines
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
    log.line("    (if a 'would like to access data from other apps' prompt "
             "appeared, it was")
    log.line("    attributed to the app hosting this terminal - Terminal.app, "
             "VS Code, etc.)")
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
    """path -> (inode, size, mtime_ns, is_dir) for everything in the
    container, directories included: a new note is born as a FLAT .rtfd
    file and becomes a directory a few seconds later (verified), so the
    file/dir distinction is part of the finding."""
    state = {}
    for root, dirs, files in os.walk(container):
        for name in dirs:
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
                state[path] = (st.st_ino, 0, st.st_mtime_ns, True)
            except OSError:
                pass
        for name in files:
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
                state[path] = (st.st_ino, st.st_size, st.st_mtime_ns, False)
            except OSError:
                pass
    return state


def step_4_watch(log, args):
    seconds = args.watch_seconds
    title = "until you stop it" if seconds <= 0 else f"{seconds}s watch"
    log.section(f"4. Live-write behavior ({title})")
    log.line("  Anything you type here + Enter is stamped into the log as a NOTE,")
    log.line("  interleaved with the filesystem events - narrate what you do.")
    log.line("  Type q (or Ctrl-C) to finish." if seconds <= 0 else
             "  (--watch-seconds 0 for an open-ended session)")
    log.line("")
    log.line("  Suggested session (annotate BEFORE each action):")
    log.line("    typing: type a sentence, then HANDS OFF everything for 5 min")
    log.line("            (no clicks, no focus change - this measures the timer)")
    log.line("    focus: click another note / another app")
    log.line("    move / recolor / collapse: each is a separate save (known)")
    log.line("    delete: Cmd-W closes = deletes (confirmation only if text)")
    log.line("    quit: Cmd-Q Stickies, wait, relaunch")
    log.line("  Watching...")
    container = args.stickies_dir
    before = _tree_state(container)
    deadline = None if seconds <= 0 else time.time() + seconds
    last_mark = time.time()
    seen = 0
    stdin_open = True

    def stamp():
        return f"{time.strftime('%H:%M:%S')} +{time.time() - last_mark:5.1f}s"

    try:
        while deadline is None or time.time() < deadline:
            if stdin_open and _stdin_ready(0.2):
                line = sys.stdin.readline()
                if not line:
                    stdin_open = False
                elif line.strip().lower() == "q":
                    break
                elif line.strip():
                    last_mark = time.time()
                    log.line(f"  {time.strftime('%H:%M:%S')}  NOTE: {line.strip()}")
                continue
            if not stdin_open:
                time.sleep(0.2)
            after = _tree_state(container)
            for path in after.keys() - before.keys():
                kind = "DIR " if after[path][3] else "FILE"
                log.line(f"  {stamp()}  CREATED {kind} {os.path.relpath(path, container)}")
                seen += 1
            for path in before.keys() - after.keys():
                kind = "DIR " if before[path][3] else "FILE"
                log.line(f"  {stamp()}  DELETED {kind} {os.path.relpath(path, container)}")
                seen += 1
            for path in after.keys() & before.keys():
                if before[path][:3] != after[path][:3]:
                    rel = os.path.relpath(path, container)
                    if before[path][3] != after[path][3]:
                        log.line(f"  {stamp()}  BECAME {'DIR' if after[path][3] else 'FILE'} {rel}")
                    elif after[path][3]:
                        log.line(f"  {stamp()}  DIR-TOUCH {rel}")
                    else:
                        same = before[path][0] == after[path][0]
                        how = ("IN-PLACE (same inode)" if same
                               else "REPLACED (new inode = temp-and-rename)")
                        log.line(f"  {stamp()}  CHANGED  {rel}  {how}")
                    seen += 1
            before = after
    except KeyboardInterrupt:
        log.line("  (stopped)")
    log.finding(f"{seen} filesystem events; the +Ns column is time since your "
                "last NOTE, so a CHANGED line's offset from 'edit' is the save "
                "delay (or its absence is the answer)")


def _stdin_ready(timeout):
    """True when a line is waiting on stdin (POSIX select; never blocks)."""
    try:
        import select
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        return bool(ready)
    except (OSError, ValueError, ImportError):
        time.sleep(timeout)
        return False


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

def step_6_colors(log, args):
    log.section("6. Colour calibration (StickyColor -> palette name)")
    src = Path(__file__).resolve().parent.parent / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        from stickies_to_markdown.engine.stickies import classify_color
    except ImportError as error:
        log.finding(f"repo engine not importable ({error})")
        return
    import colorsys
    path = Path(args.stickies_dir) / ".SavedStickiesState"
    try:
        with open(path, "rb") as handle:
            entries = plistlib.load(handle)
    except Exception as error:
        log.finding(f"state file unreadable: {error!r}")
        return
    if isinstance(entries, dict):
        entries = next((v for v in entries.values() if isinstance(v, list)), [])
    log.line("  For each note: open it in Stickies, check the Color menu, and")
    log.line("  confirm the engine's name. Adjust _HUE_BANDS in stickies.py if not.")
    log.line("")
    log.line(f"  {'uuid8':<9} {'hex':<8} {'hue':>5} {'sat':>5} {'val':>5}  engine says")
    seen = {}
    for entry in entries:
        color = entry.get("StickyColor") if isinstance(entry, dict) else None
        if not isinstance(color, dict):
            continue
        r, g, b = (float(color.get(k, 0)) for k in ("Red", "Green", "Blue"))
        h, sat, val = colorsys.rgb_to_hsv(r, g, b)
        name, hex_code = classify_color(r, g, b)
        uuid8 = str(entry.get("UUID", "?")).replace("-", "")[:8].lower()
        log.line(f"  {uuid8:<9} {hex_code:<8} {h * 360:5.0f} {sat:5.2f} {val:5.2f}  {name}")
        seen.setdefault(name, set()).add(hex_code)
    log.finding(f"palette names in use: {sorted(seen)} "
                f"(need all six for full calibration)")
    ambiguous = {k: v for k, v in seen.items() if len(v) > 1}
    if ambiguous:
        log.finding(f"one name covers several hex values - fine if they are "
                    f"the same colour, suspicious otherwise: {ambiguous}")


# --- step 7 ----------------------------------------------------------------

def step_7_converters(log, args):
    log.section("7. Converter check: every package, both tiers")
    src = Path(__file__).resolve().parent.parent / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        from stickies_to_markdown.engine.convert import convert
    except ImportError as error:
        log.finding(f"repo engine not importable ({error}); run from the "
                    "repo checkout or pip install first")
        return
    packages = sorted(p for p in Path(args.stickies_dir).iterdir()
                      if p.suffix == ".rtfd" and p.is_dir())
    if not packages:
        log.finding("no packages to convert")
        return

    import re
    emphasis = re.compile(r"(?<!\\)\*\*[^*\n]+\*\*|(?<![\\*\w])\*[^*\\\n]+\*(?![*\w])")
    styled = []
    failures = []
    log.line(f"  {'uuid8':<9} {'textutil':>9} {'text':>7}  emphasis  format    first line")
    for pkg in packages:
        uuid8 = pkg.name[:8].lower()
        results = {}
        for tier in ("textutil", "text"):
            try:
                markdown, _attachments, fmt = convert(str(pkg), tier)
                results[tier] = markdown
                results["format"] = fmt
            except Exception as error:
                results[tier] = None
                failures.append(f"{uuid8} {tier}: {type(error).__name__}: "
                                f"{str(error)[:120]}")
        tu, tx = results.get("textutil"), results.get("text")
        runs = emphasis.findall(tu) if tu else []
        first = next((l for l in (tu or tx or "").splitlines()
                      if l.strip() and not l.startswith("```")), "")
        log.line(f"  {uuid8:<9} {'-' if tu is None else len(tu):>9} {'-' if tx is None else len(tx):>7}  "
                 f"{len(runs):>8}  {results.get('format', '-'):<8}  {first[:40]}")
        if runs:
            styled.append((uuid8, runs, tu))

    for line in failures:
        log.finding(f"conversion failed: {line}")
    if styled:
        for uuid8, runs, markdown in styled:
            log.line(f"\n  --- {uuid8}: {len(runs)} emphasised run(s) via textutil ---")
            for line in markdown.splitlines():
                if emphasis.search(line):
                    log.line(f"    {line[:110]}")
        log.finding(f"bold/italic survive textutil in {len(styled)} note(s): "
                    f"{', '.join(u for u, _, _ in styled)} - check the marked "
                    "lines above match what you see in Stickies")
    else:
        log.finding("no note produced any **bold** or *italic* through textutil. "
                    "If one of your notes HAS bold/italic text, textutil is "
                    "dropping it - report which note")
    if not failures:
        log.finding(f"both tiers converted all {len(packages)} packages")


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
         4: step_4_watch, 5: step_5_textutil, 6: step_6_colors,
         7: step_7_converters, 8: step_8_capture}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--stickies-dir", default=str(DEFAULT_CONTAINER),
                        help="container to probe (default: the real one)")
    parser.add_argument("--steps", default="",
                        help="comma-separated step numbers (default: all)")
    parser.add_argument("--watch-seconds", type=int, default=25,
                        help="step 4 window; 0 = open-ended until q/Ctrl-C")
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
