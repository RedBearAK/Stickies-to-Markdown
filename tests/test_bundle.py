#!/usr/bin/env python3
"""
--install-app: the .app bundle writer. Generated on any OS; only codesign
and Login Items need a Mac. The compiled launcher is built and RUN here.
"""

import os
import sys
import stat
import time
import signal
import plistlib
import tempfile
import subprocess

from pathlib import Path

from _helpers import check, run_suite

from stickies_to_markdown._version import __version__
from stickies_to_markdown.frontends import bundle as bundle_module
from stickies_to_markdown.frontends.bundle import (
    MARKER, APP_NAME, BUNDLE_ID, c_compiler, install_app, launcher_kind,
    is_our_bundle, uninstall_app, package_src_dir, recorded_interpreter)


class no_compiler:
    def __enter__(self):
        self._saved = bundle_module.c_compiler
        bundle_module.c_compiler = lambda: None

    def __exit__(self, *_exc):
        bundle_module.c_compiler = self._saved


def test_bundle_structure():
    with tempfile.TemporaryDirectory() as tmp:
        path = install_app(tmp, out=lambda _: None)
        contents = Path(path) / "Contents"
        ok = check(path.endswith(f"{APP_NAME}.app") and contents.is_dir(), "bundle written", path)
        with open(contents / "Info.plist", "rb") as handle:
            plist = plistlib.load(handle)
        ok &= check(plist["CFBundleIdentifier"] == "com.redbearak.stickies-to-markdown"
                    and plist["LSUIElement"] is True and plist["CFBundleExecutable"] == "launcher"
                    and plist["CFBundleVersion"] == __version__,
                    "Info.plist: stable identifier, LSUIElement, launcher, version", f"{plist}")
        ok &= check((contents / "PkgInfo").read_text() == "APPL????", "PkgInfo", "")
        ok &= check((contents / "Resources" / "AppIcon.icns").is_file(), "app icon present", "")
        launcher = contents / "MacOS" / "launcher"
        ok &= check(launcher.is_file() and os.stat(launcher).st_mode & stat.S_IXUSR,
                    "launcher executable", "")
        ok &= check(is_our_bundle(path) and recorded_interpreter(path) == sys.executable,
                    "bundle recognised as ours with this interpreter", f"{recorded_interpreter(path)}")
        return ok


def test_compiled_and_script_launchers():
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        if c_compiler():
            path = install_app(tmp, out=lambda _: None)
            ok &= check(launcher_kind(path) == "compiled", "C launcher compiled", launcher_kind(path))
            source = Path(path) / "Contents" / "MacOS" / "launcher.c"
            ok &= check(source.is_file() and "-m" in source.read_text()
                        and "stickies_to_markdown" in source.read_text(),
                        "C source kept beside the binary, targets this package", "")
        else:
            print("  - no C compiler here; compiled launcher skipped")
    with tempfile.TemporaryDirectory() as tmp, no_compiler():
        lines = []
        path = install_app(tmp, out=lines.append)
        ok &= check(launcher_kind(path) == "script" and any("no C compiler" in l for l in lines),
                    "shell fallback used and explained when no compiler", f"{lines}")
    return ok


def _run_launcher(path, home):
    launcher = Path(path) / "Contents" / "MacOS" / "launcher"
    env = dict(os.environ)
    env["HOME"] = home
    env.pop("PYTHONPATH", None)
    result = subprocess.run([str(launcher)], capture_output=True, text=True, env=env, timeout=30)
    log = Path(home) / bundle_module.LOG_RELATIVE
    return result, (log.read_text() if log.is_file() else "")


def test_launchers_run_and_reach_the_dispatcher():
    """Off macOS, --menubar is refused by the dispatcher with a clear message
    into the launcher log; reaching it proves PYTHONPATH, spawn and the log."""
    if os.name == "nt" or sys.platform == "darwin":
        print("  - skipped here")
        return True
    ok = True
    for label, ctx in (("compiled", None), ("script", no_compiler())):
        with tempfile.TemporaryDirectory() as tmp:
            if ctx is not None:
                ctx.__enter__()
            try:
                if label == "compiled" and not c_compiler():
                    continue
                path = install_app(tmp, out=lambda _: None)
                result, log_text = _run_launcher(path, tmp)
            finally:
                if ctx is not None:
                    ctx.__exit__(None, None, None)
            ok &= check("only available on macOS" in log_text,
                        f"{label}: reached the dispatcher's platform gate via the log",
                        f"{label}: rc={result.returncode} log={log_text!r} err={result.stderr[-300:]}")
            ok &= check(result.returncode == 1, f"{label}: child exit code propagated", f"rc={result.returncode}")
    return ok


def test_compiled_launcher_forwards_sigterm():
    if os.name == "nt" or sys.platform == "darwin" or not c_compiler():
        print("  - skipped here")
        return True
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "fake_python"
        fake.write_text("#!/bin/sh\ntrap 'exit 143' TERM\nwhile :; do sleep 0.1; done\n")
        fake.chmod(0o755)
        path = install_app(tmp, interpreter=str(fake), out=lambda _: None)
        launcher = Path(path) / "Contents" / "MacOS" / "launcher"
        env = dict(os.environ)
        env["HOME"] = tmp
        proc = subprocess.Popen([str(launcher)], env=env)
        time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        try:
            rc = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            return check(False, "", "launcher did not exit after SIGTERM")
        return check(rc == 143, f"SIGTERM forwarded; launcher exited with the child's code ({rc})", f"rc={rc}")


def test_reinstall_and_foreign_bundle():
    with tempfile.TemporaryDirectory() as tmp:
        path = install_app(tmp, out=lambda _: None)
        lines = []
        install_app(tmp, interpreter="/opt/other/python3", out=lines.append)
        ok = check(recorded_interpreter(path) == "/opt/other/python3" and any("Updated" in l for l in lines),
                   "reinstall refreshes the recorded interpreter", f"{lines}")
    with tempfile.TemporaryDirectory() as tmp:
        foreign = Path(bundle_module.bundle_path(tmp))
        (foreign / "Contents" / "MacOS").mkdir(parents=True)
        (foreign / "Contents" / "MacOS" / "launcher").write_text("#!/bin/sh\necho mine\n")
        lines = []
        ok &= check(install_app(tmp, out=lines.append) is None, "foreign bundle never overwritten", f"{lines}")
        ok &= check(uninstall_app(tmp, out=lines.append) is False and foreign.exists(),
                    "foreign bundle never removed", "")
    with tempfile.TemporaryDirectory() as tmp:
        path = install_app(tmp, out=lambda _: None)
        ok &= check(uninstall_app(tmp, out=lambda _: None) and not os.path.exists(path),
                    "own bundle removed", "")
    return ok


if __name__ == "__main__":
    tests = [test_bundle_structure, test_compiled_and_script_launchers,
             test_launchers_run_and_reach_the_dispatcher, test_compiled_launcher_forwards_sigterm,
             test_reinstall_and_foreign_bundle]
    exit(0 if run_suite("bundle tests", tests) else 1)


# End of file #
