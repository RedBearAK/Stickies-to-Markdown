"""
--install-command / --uninstall-command: keep a launcher on PATH.

Writes a tiny stub that execs the interpreter this package is installed
into with `-m stickies_to_markdown`. A stub rather than a symlink, so it
records WHICH interpreter and survives the checkout moving. Re-running it
after rebuilding a venv repairs the recorded path. It never overwrites a
file it did not write itself, and it never edits shell rc files: it prints
the PATH line for the user to paste instead.

Pure stdlib. No rich.
"""

import os
import sys
import stat


COMMAND_NAME = "stickies2md"
MARKER = "# stickies2md launcher stub (managed by --install-command)"


def default_bin_dir():
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
        return os.path.join(base, 'Programs', 'Stickies-to-Markdown', 'bin')
    return os.path.expanduser('~/.local/bin')


def stub_path(bin_dir, name=COMMAND_NAME):
    if os.name == 'nt':
        return os.path.join(bin_dir, name + '.cmd')
    return os.path.join(bin_dir, name)


def package_src_dir():
    """Directory that must be on PYTHONPATH for `import stickies_to_markdown`."""
    import stickies_to_markdown
    return os.path.dirname(os.path.dirname(os.path.abspath(stickies_to_markdown.__file__)))


def stub_text(interpreter, src_dir=None):
    # PYTHONPATH is set as well as the interpreter so the stub works both for
    # a pip-installed package and for a bare checkout on PYTHONPATH (the
    # tech-bin style of launcher). Harmless when the package is installed.
    src_dir = src_dir or package_src_dir()
    if os.name == 'nt':
        return (f"@echo off\r\nrem {MARKER}\r\n"
                f"set \"PYTHONPATH={src_dir};%PYTHONPATH%\"\r\n"
                f"\"{interpreter}\" -m stickies_to_markdown %*\r\n")
    return (f"#!/bin/sh\n{MARKER}\n"
            f"export PYTHONPATH=\"{src_dir}${{PYTHONPATH:+:$PYTHONPATH}}\"\n"
            f"exec \"{interpreter}\" -m stickies_to_markdown \"$@\"\n")


def is_our_stub(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            head = handle.read(512)
    except OSError:
        return False
    return MARKER in head


def recorded_interpreter(path):
    """Interpreter path inside an existing stub, or None."""
    if not is_our_stub(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                if '-m stickies_to_markdown' in line and line.lstrip().startswith(('exec "', '"')):
                    return line.split('"')[1]
    except OSError:
        pass
    return None


def dir_on_path(bin_dir):
    entries = os.environ.get('PATH', '').split(os.pathsep)
    target = os.path.normcase(os.path.abspath(bin_dir))
    return any(os.path.normcase(os.path.abspath(e)) == target for e in entries if e)


def path_hint(bin_dir):
    if os.name == 'nt':
        return f'setx PATH "%PATH%;{bin_dir}"'
    return f'export PATH="{bin_dir}:$PATH"'


def install_command(bin_dir=None, name=COMMAND_NAME, interpreter=None, out=print):
    """
    Write (or refresh) the stub. Returns the stub path, or None when it
    refused to overwrite a foreign file. `out` receives status lines.
    """
    bin_dir = os.path.abspath(bin_dir or default_bin_dir())
    interpreter = interpreter or sys.executable
    path = stub_path(bin_dir, name)

    if os.path.exists(path) and not is_our_stub(path):
        out(f"Refusing to overwrite '{path}': it is not a launcher stub written by this tool.")
        out("Remove it yourself, or pick another location with --dir.")
        return None

    existing = recorded_interpreter(path)
    if existing == interpreter:
        out(f"Launcher already current: '{path}'")
    else:
        os.makedirs(bin_dir, exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='') as handle:
            handle.write(stub_text(interpreter))
        if os.name != 'nt':
            mode = os.stat(path).st_mode
            os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        verb = "Updated" if existing else "Installed"
        out(f"{verb} launcher: '{path}'")
        out(f"  interpreter: '{interpreter}'")

    if dir_on_path(bin_dir):
        out(f"{bin_dir} is on your PATH; run `{name}` from any terminal.")
    else:
        out(f"{bin_dir} is NOT on your PATH. Add this line to your shell startup file:")
        out(f"  {path_hint(bin_dir)}")

    return path


def uninstall_command(bin_dir=None, name=COMMAND_NAME, out=print):
    """Remove the stub if it is ours. Returns True when something was removed."""
    bin_dir = os.path.abspath(bin_dir or default_bin_dir())
    path = stub_path(bin_dir, name)

    if not os.path.exists(path):
        out(f"No launcher at '{path}'")
        return False
    if not is_our_stub(path):
        out(f"Refusing to remove '{path}': it is not a launcher stub written by this tool.")
        return False

    os.remove(path)
    out(f"Removed launcher: '{path}'")
    return True


# End of file #
