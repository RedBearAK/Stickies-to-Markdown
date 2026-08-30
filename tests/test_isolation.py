#!/usr/bin/env python3
"""
The rule that made DFP work: engine/ never writes to stdout/stderr and
never imports a UI module. `Foundation` (PyObjC, non-GUI) is allow-listed
inside convert.py only; AppKit and rumps are forbidden everywhere.

Two layers: a source grep, and a full export run in a fresh interpreter
with both streams captured.
"""

import re
import sys
import subprocess

from pathlib import Path

from _helpers import Sandbox, check, run_suite

ENGINE = Path(__file__).resolve().parent.parent / "src" / "stickies_to_markdown" / "engine"

FORBIDDEN_EVERYWHERE = ("AppKit", "rumps", "rich", "Cocoa", "objc")
FORBIDDEN_OUTSIDE_CONVERT = ("Foundation",)
PRINTY = re.compile(r"^\s*print\(|sys\.stdout|sys\.stderr", re.MULTILINE)


def test_no_ui_imports_in_engine():
    ok = True
    for path in sorted(ENGINE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        imports = re.findall(r"^\s*(?:import|from)\s+([\w.]+)", source, re.MULTILINE)
        roots = {name.split(".")[0] for name in imports}
        for module in FORBIDDEN_EVERYWHERE:
            ok &= check(module not in roots,
                        f"{path.name}: no {module}",
                        f"{path.name} imports {module}")
        if path.name != "convert.py":
            for module in FORBIDDEN_OUTSIDE_CONVERT:
                ok &= check(module not in roots,
                            f"{path.name}: no {module}",
                            f"{path.name} imports {module} (convert.py only)")
    return ok


def test_no_prints_in_engine():
    ok = True
    for path in sorted(ENGINE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        ok &= check(not PRINTY.search(source),
                    f"{path.name}: no print/stdout/stderr",
                    f"{path.name} writes to a terminal stream")
    return ok


def test_export_is_silent():
    with Sandbox() as box:
        code = (
            "import sys; sys.path.insert(0, {src!r})\n"
            "from stickies_to_markdown.engine.config import Config\n"
            "from stickies_to_markdown.engine.events import EventQueue\n"
            "from stickies_to_markdown.engine.logsetup import setup_logging\n"
            "from stickies_to_markdown.engine.processor import NoteProcessor\n"
            "config = Config(config_file={cfg!r})\n"
            "setup_logging(config)\n"
            "NoteProcessor(config, EventQueue()).export_all()\n"
        ).format(src=str(ENGINE.parent.parent), cfg=str(box.config.config_file))
        proc = subprocess.run([sys.executable, "-c", code],
                              capture_output=True, timeout=60)
        ok = check(proc.returncode == 0, "engine-only export ran",
                   f"rc={proc.returncode}: {proc.stderr.decode()[:400]}")
        ok &= check(proc.stdout == b"" and proc.stderr == b"",
                    "engine wrote nothing to stdout/stderr",
                    f"stdout={proc.stdout[:200]!r} stderr={proc.stderr[:200]!r}")
        ok &= check(len(box.mirror_files()) == 7, "and still exported",
                    f"{len(box.mirror_files())} files")
        return ok


def test_export_silent_even_when_unhealthy():
    with Sandbox() as box:
        box.config.set("stickies_dir", str(box.root / "missing"))
        code = (
            "import sys; sys.path.insert(0, {src!r})\n"
            "from stickies_to_markdown.engine.config import Config\n"
            "from stickies_to_markdown.engine.events import EventQueue\n"
            "from stickies_to_markdown.engine.processor import NoteProcessor\n"
            "NoteProcessor(Config(config_file={cfg!r}), EventQueue()).export_all()\n"
        ).format(src=str(ENGINE.parent.parent), cfg=str(box.config.config_file))
        proc = subprocess.run([sys.executable, "-c", code],
                              capture_output=True, timeout=60)
        return check(proc.returncode == 0 and proc.stdout == b""
                     and proc.stderr == b"",
                     "errors go to log/queue, never a terminal "
                     "(even with logging unconfigured)",
                     f"stdout={proc.stdout[:200]!r} stderr={proc.stderr[:200]!r}")


if __name__ == "__main__":
    tests = [test_no_ui_imports_in_engine, test_no_prints_in_engine,
             test_export_is_silent, test_export_silent_even_when_unhealthy]
    exit(0 if run_suite("engine isolation tests", tests) else 1)


# End of file #
