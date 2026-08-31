#!/usr/bin/env python3
"""The flag-driven front end, run in-process through run_cli()."""

import io
import contextlib

from _helpers import Sandbox, check, run_suite

from stickies_to_markdown.frontends.cli import run_cli


def _run(box, *argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run_cli(["--config", str(box.config.config_file), *argv])
    return code, out.getvalue(), err.getvalue()


def test_once_exports_and_reports():
    with Sandbox() as box:
        code, out, err = _run(box, "--once")
        ok = check(code == 0, "--once exits 0", f"rc={code} err={err!r}")
        ok &= check("7 converted" in out, "summary line printed", f"out={out!r}")
        ok &= check(len(box.mirror_files()) == 7, "files written",
                    f"{len(box.mirror_files())}")
        code2, out2, _ = _run(box, "--once")
        ok &= check(code2 == 0 and "7 unchanged" in out2,
                    "second --once reports unchanged", f"out={out2!r}")
        return ok


def test_missing_output_dir_exits_2_with_hint():
    with Sandbox() as box:
        box.config.set("outputs", [])
        code, _out, err = _run(box, "--once")
        return check(code == 2 and "--add-output" in err,
                     "no outputs: exit 2 and a setup hint",
                     f"rc={code} err={err!r}")


def test_per_run_override_not_saved():
    with Sandbox() as box:
        other = box.root / "elsewhere"
        code, _out, _err = _run(box, "--once", "--output-dir", str(other),
                                "--filename-style", "uuid")
        ok = check(code == 0 and len(list(other.glob("*.md"))) == 7,
                   "--output-dir override honoured", f"rc={code}")
        box.config.reload()
        ok &= check(box.config.output_dirs() == [str(box.output)],
                    "override did not touch the saved config",
                    f"saved: {box.config.output_dirs()!r}")
        return ok


def test_set_roundtrip_and_validation():
    with Sandbox() as box:
        code, out, _ = _run(box, "--set", "default.on_delete=delete",
                            "--set", "debounce_seconds=5.5")
        ok = check(code == 0 and "default.on_delete = 'delete'" in out,
                   "--set persists and echoes (NAME.KEY for an output)", f"rc={code} out={out!r}")
        box.config.reload()
        ok &= check(box.config.get("debounce_seconds") == 5.5 and box.target.on_delete() == "delete",
                    "float value coerced; output key landed in its block",
                    f"{box.config.get('debounce_seconds')!r} {box.target.on_delete()}")
        _run(box, "--set", "default.exclude_colors=gray, pink")
        box.config.reload()
        ok &= check(box.target.get("exclude_colors") == ["gray", "pink"],
                    "list value coerced from comma-separated",
                    f"{box.target.get('exclude_colors')!r}")
        code3, out3, _ = _run(box, "--set", "output_dir=/tmp/legacy")
        box.config.reload()
        ok &= check(code3 == 0 and box.target.output_dir() == "/tmp/legacy",
                    "legacy --set output_dir= targets the first output", f"{out3!r}")
        code4, out4, _ = _run(box, "--add-output", "plain=/tmp/plain")
        box.config.reload()
        ok &= check(code4 == 0 and [t.name for t in box.config.targets()] == ["default", "plain"],
                    "--add-output NAME=PATH appends a block", f"{out4!r}")
        code5, _o, _e = _run(box, "--remove-output", "plain")
        box.config.reload()
        ok &= check(code5 == 0 and [t.name for t in box.config.targets()] == ["default"],
                    "--remove-output NAME removes it", "")
        code2, _out2, err2 = _run(box, "--set", "no_such_key=1")
        ok &= check(code2 == 2 and "Unknown setting" in err2,
                    "unknown key rejected with exit 2", f"rc={code2}")
        return ok


def test_show_config_lists_everything():
    with Sandbox() as box:
        code, out, _ = _run(box, "--show-config")
        return check(code == 0 and "output_dir" in out and "converter" in out,
                     "--show-config prints the effective settings",
                     f"rc={code}")


def test_dry_run_flag():
    with Sandbox() as box:
        code, out, _ = _run(box, "--once", "--dry-run")
        return check(code == 0 and "(dry run)" in out
                     and not box.mirror_files(),
                     "--dry-run reports but writes nothing",
                     f"rc={code} out={out!r} files={box.mirror_files()}")


def test_error_exit_code():
    with Sandbox() as box:
        box.config.set("stickies_dir", str(box.root / "missing"))
        code, _out, err = _run(box, "--once")
        return check(code == 1 and "error" in err.lower(),
                     "unreadable container: exit 1, error on stderr",
                     f"rc={code} err={err!r}")


def test_start_watches_and_stops_on_sigint():
    import signal, subprocess, sys, time
    from _helpers import wait_for
    with Sandbox(debounce_seconds=0.3, settle_seconds=0.2) as box:
        proc = subprocess.Popen(
            [sys.executable, "-m", "stickies_to_markdown", "--config", str(box.config.config_file), "--start"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env={**__import__("os").environ, "PYTHONPATH": str(__import__("_helpers")._SRC)})
        ok = check(wait_for(lambda: len(box.mirror_files()) == 7, timeout=15),
                   "--start performs the initial export", f"{[f.name for f in box.mirror_files()]}")
        proc.send_signal(signal.SIGINT)
        try:
            out, _ = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            out = "(killed)"
        ok &= check(proc.returncode == 0 and "Watching:" in out and "Stopped." in out,
                    "--start announces the folders and stops cleanly on Ctrl-C",
                    f"rc={proc.returncode} out={out[-400:]!r}")
        return ok


def test_install_flags_reach_the_writers():
    import tempfile, os
    with Sandbox() as box, tempfile.TemporaryDirectory() as tmp:
        code, out, _ = _run(box, "--install-command", "--dir", tmp)
        ok = check(code == 0 and os.path.isfile(os.path.join(tmp, "stickies2md")),
                   "--install-command writes the stub", f"rc={code} out={out!r}")
        code, out, _ = _run(box, "--uninstall-command", "--dir", tmp)
        ok &= check(code == 0 and not os.path.exists(os.path.join(tmp, "stickies2md")),
                    "--uninstall-command removes it", f"rc={code}")
        code, out, _ = _run(box, "--install-app", "--app-dir", tmp)
        ok &= check(code == 0 and os.path.isdir(os.path.join(tmp, "Stickies to Markdown.app")),
                    "--install-app writes the bundle", f"rc={code} out={out[-200:]!r}")
        code, out, _ = _run(box, "--uninstall-app", "--app-dir", tmp)
        ok &= check(code == 0 and not os.path.exists(os.path.join(tmp, "Stickies to Markdown.app")),
                    "--uninstall-app removes it", f"rc={code}")
        return ok


def test_purge_mirror_removes_only_ours():
    with Sandbox() as box:
        _run(box, "--once")
        foreign = box.output / "my-own-note.md"
        foreign.write_text("mine\n", encoding="utf-8")
        code, out, _ = _run(box, "--purge-mirror", str(box.output))
        ok = check(code == 0 and "Would remove 10" in out or "Would remove 8" in out,
                   "dry run lists what it would remove", out[-200:])
        ok &= check(len(box.mirror_files()) == 8, "dry run removed nothing", "")
        code, out, _ = _run(box, "--purge-mirror", str(box.output), "--yes")
        ok &= check(code == 0 and foreign.is_file()
                    and not any(f.name != "my-own-note.md" for f in box.mirror_files())
                    and not (box.output / "attachments").exists(),
                    "--yes removed our files and attachments, kept the foreign file",
                    f"{[f.name for f in box.mirror_files()]}")
        return ok


if __name__ == "__main__":
    tests = [test_once_exports_and_reports, test_missing_output_dir_exits_2_with_hint,
             test_per_run_override_not_saved, test_set_roundtrip_and_validation,
             test_show_config_lists_everything, test_dry_run_flag,
             test_error_exit_code, test_start_watches_and_stops_on_sigint,
             test_install_flags_reach_the_writers, test_purge_mirror_removes_only_ours]
    exit(0 if run_suite("cli tests", tests) else 1)


# End of file #
