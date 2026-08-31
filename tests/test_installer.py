#!/usr/bin/env python3
"""--install-command / --uninstall-command: the launcher stub on PATH."""

import os
import sys
import stat
import tempfile
import subprocess

from pathlib import Path

from _helpers import check, run_suite

from stickies_to_markdown.frontends.installer import (
    MARKER, COMMAND_NAME, stub_path, is_our_stub, install_command,
    uninstall_command, recorded_interpreter, dir_on_path, package_src_dir)


def collect():
    lines = []
    return lines, lines.append


def test_install_writes_executable_stub_with_interpreter():
    with tempfile.TemporaryDirectory() as tmp:
        lines, out = collect()
        path = install_command(tmp, out=out)
        ok = check(path == stub_path(tmp) and os.path.isfile(path), "stub written", f"{lines}")
        text = Path(path).read_text()
        ok &= check(MARKER in text and sys.executable in text and package_src_dir() in text,
                    "stub records marker, interpreter and package dir", text)
        if os.name != "nt":
            ok &= check(os.stat(path).st_mode & stat.S_IXUSR, "stub is executable", "")
        ok &= check(recorded_interpreter(path) == sys.executable,
                    "recorded interpreter reads back", f"{recorded_interpreter(path)}")
        return ok


def test_stub_actually_runs_the_tool():
    if os.name == "nt":
        return True
    with tempfile.TemporaryDirectory() as tmp:
        path = install_command(tmp, out=lambda _: None)
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        result = subprocess.run([path, "--version"], capture_output=True, text=True, env=env)
        return check(result.returncode == 0 and "stickies2md" in result.stdout,
                     f"`{COMMAND_NAME} --version` via the stub: {result.stdout.strip()}",
                     f"rc={result.returncode} out={result.stdout!r} err={result.stderr[-200:]!r}")


def test_reinstall_is_idempotent_and_refreshes_interpreter():
    with tempfile.TemporaryDirectory() as tmp:
        install_command(tmp, out=lambda _: None)
        lines, out = collect()
        install_command(tmp, out=out)
        ok = check(any("already current" in l for l in lines), "second install: already current", f"{lines}")
        lines, out = collect()
        path = install_command(tmp, interpreter="/opt/other/python3", out=out)
        ok &= check(recorded_interpreter(path) == "/opt/other/python3"
                    and any("Updated" in l for l in lines),
                    "changed interpreter -> stub rewritten (venv rebuild case)", f"{lines}")
        return ok


def test_never_touches_foreign_files():
    with tempfile.TemporaryDirectory() as tmp:
        foreign = stub_path(tmp)
        Path(foreign).write_text("#!/bin/sh\necho mine\n")
        lines, out = collect()
        ok = check(install_command(tmp, out=out) is None and "mine" in Path(foreign).read_text(),
                   "refuses to overwrite a foreign file", f"{lines}")
        ok &= check(uninstall_command(tmp, out=out) is False and os.path.exists(foreign),
                    "refuses to remove a foreign file", f"{lines}")
        return ok


def test_uninstall_removes_own_stub():
    with tempfile.TemporaryDirectory() as tmp:
        path = install_command(tmp, out=lambda _: None)
        ok = check(uninstall_command(tmp, out=lambda _: None) and not os.path.exists(path),
                   "own stub removed", "")
        ok &= check(not is_our_stub(path), "no longer detected", "")
        return ok


def test_dir_on_path_detection():
    with tempfile.TemporaryDirectory() as tmp:
        saved = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = tmp + os.pathsep + saved
            ok = check(dir_on_path(tmp), "dir on PATH detected", "")
            os.environ["PATH"] = saved
            ok &= check(not dir_on_path(tmp), "dir off PATH detected", "")
        finally:
            os.environ["PATH"] = saved
        return ok


if __name__ == "__main__":
    tests = [test_install_writes_executable_stub_with_interpreter, test_stub_actually_runs_the_tool,
             test_reinstall_is_idempotent_and_refreshes_interpreter, test_never_touches_foreign_files,
             test_uninstall_removes_own_stub, test_dir_on_path_detection]
    exit(0 if run_suite("installer tests", tests) else 1)


# End of file #
