"""
--install-app: write a macOS .app bundle that launches the menu bar mode.

Verified for this tool (dev_notes/MAC_FINDINGS.md): the Stickies container
is behind a PROMPTING TCC category ("access data from other apps"), so the
compiled launcher's stable identity is what makes that prompt appear in the
app's own name on first launch instead of blaming python3.

Not py2app. The bundle is an Info.plist with LSUIElement (no Dock tile),
the icon, and a launcher under Contents/MacOS that runs the recorded
interpreter with `-m stickies_to_markdown --menubar`. Because it holds no
Python of its own, it never goes stale with a Python or macOS upgrade;
re-run --install-app after rebuilding a venv and it repairs the recorded
paths, the same way --install-command does for the terminal stub.

The launcher is a tiny compiled C program (launcher_template.c) when a C
compiler is available, and a shell script otherwise. That matters for
permissions: macOS attributes folder-access prompts and grants to the
"responsible" process, and it only credits a real signed Mach-O inside the
bundle. With a shell-script launcher tccd skips /bin/sh and pins the grant
to the interpreter binary: the prompt says "python3.12", "Show in Finder"
opens Homebrew, and every script that interpreter runs shares the grant.
With the compiled launcher (spawn + wait, never exec) the prompt names the
app and the grant follows the bundle.

Pure stdlib. The bundle is generated, and the C launcher compiled and run,
on any POSIX OS by the tests; only codesigning and Login Items need a Mac.
"""

import os
import sys
import stat
import shutil
import plistlib
import subprocess

from stickies_to_markdown._version import __version__


APP_NAME = "Stickies to Markdown"
BUNDLE_ID = "com.redbearak.stickies-to-markdown"   # NEVER change: TCC grants are keyed to it
MARKER = "# stickies2md app launcher (managed by --install-app)"
LOG_RELATIVE = "Library/Logs/StickiesToMarkdown/launcher.log"

ICON_SOURCE = os.path.join(os.path.dirname(__file__), "icons", "AppIcon.icns")
C_TEMPLATE = os.path.join(os.path.dirname(__file__), "launcher_template.c")


def default_app_dir():
    return os.path.expanduser("~/Applications")


def bundle_path(app_dir, name=APP_NAME):
    return os.path.join(app_dir, name + ".app")


def package_src_dir():
    """Directory that must be on PYTHONPATH for `import stickies_to_markdown`."""
    import stickies_to_markdown
    return os.path.dirname(os.path.dirname(os.path.abspath(stickies_to_markdown.__file__)))


def launcher_text(interpreter, src_dir):
    """Shell fallback: same behavior as the C launcher, weaker TCC attribution."""
    return (
        "#!/bin/sh\n"
        f"{MARKER}\n"
        "# Regenerate with:  stickies2md --install-app\n"
        "cd /\n"
        f"export PYTHONPATH=\"{src_dir}${{PYTHONPATH:+:$PYTHONPATH}}\"\n"
        f"LOG=\"$HOME/{LOG_RELATIVE}\"\n"
        "mkdir -p \"$(dirname \"$LOG\")\"\n"
        f"\"{interpreter}\" -m stickies_to_markdown --menubar \"$@\" >> \"$LOG\" 2>&1 &\n"
        "child=$!\n"
        "trap 'kill -TERM \"$child\" 2>/dev/null' TERM INT HUP\n"
        "wait \"$child\"\n"
        "exit $?\n"
    )


def c_escape(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def launcher_c_source(interpreter, src_dir):
    with open(C_TEMPLATE, "r", encoding="utf-8") as handle:
        template = handle.read()
    return (template.replace("@@MARKER@@", MARKER)
                    .replace("@@INTERPRETER@@", c_escape(interpreter))
                    .replace("@@SRC_DIR@@", c_escape(src_dir))
                    .replace("@@LOG_RELATIVE@@", LOG_RELATIVE))


def c_compiler():
    """Path to a usable C compiler, or None."""
    if sys.platform == "darwin":
        probe = subprocess.run(["xcode-select", "-p"], capture_output=True)
        if probe.returncode != 0:
            return None          # the cc shim would only pop the CLT installer
    return shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")


def compile_launcher(source_path, output_path, out):
    cc = c_compiler()
    if not cc:
        return False
    cmd = [cc, "-O2", "-Wall", "-o", output_path, source_path]
    if sys.platform == "darwin":
        cmd[1:1] = ["-mmacosx-version-min=11.0"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        out(f"C launcher build failed, using shell launcher instead:\n{result.stderr.strip()}")
        return False
    return True


def info_plist(name=APP_NAME):
    return {
        "CFBundleName": name,
        "CFBundleDisplayName": name,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleVersion": __version__,
        "CFBundleShortVersionString": __version__,
        "CFBundlePackageType": "APPL",
        "CFBundleExecutable": "launcher",
        "CFBundleIconFile": "AppIcon",
        "LSUIElement": True,            # menu bar only: no Dock tile, no app menu
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
    }


def _launcher_sources(path):
    """Text files that identify the bundle as ours: the C source if kept, else the script."""
    macos = os.path.join(path, "Contents", "MacOS")
    return [os.path.join(macos, "launcher.c"), os.path.join(macos, "launcher")]


def is_our_bundle(path):
    for candidate in _launcher_sources(path):
        try:
            with open(candidate, "r", encoding="utf-8", errors="replace") as handle:
                if MARKER in handle.read(512):
                    return True
        except OSError:
            continue
    return False


def launcher_kind(path):
    """'compiled', 'script', or None."""
    if not is_our_bundle(path):
        return None
    return "compiled" if os.path.isfile(_launcher_sources(path)[0]) else "script"


def recorded_interpreter(path):
    if not is_our_bundle(path):
        return None
    for candidate in _launcher_sources(path):
        try:
            with open(candidate, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    stripped = line.strip(" *")
                    if stripped.startswith('interpreter="'):
                        return stripped.split('"')[1]
                    if line.startswith('"') and '-m stickies_to_markdown' in line:
                        return line.split('"')[1]
        except OSError:
            continue
    return None


SIGN_IDENTITY_KEY = "S2MCodesignIdentity"     # remembered in Info.plist


def recorded_sign_identity(path):
    """The identity a previous --install-app signed with, or None."""
    try:
        with open(os.path.join(path, "Contents", "Info.plist"), "rb") as handle:
            return plistlib.load(handle).get(SIGN_IDENTITY_KEY)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None


def codesign(path, out, identity="-"):
    """
    Sign the bundle so it has a stable identity for TCC. Mac only.

    "-" is ad-hoc: enough for the folder grants (Documents etc.), but the
    newer "access data from other apps" grant has been seen to re-prompt on
    every launch with an ad-hoc signature. A self-signed Code Signing
    certificate (Keychain Access > Certificate Assistant > Create a
    Certificate, type "Code Signing") gives TCC a real designated
    requirement to persist against:  --install-app --sign-identity "Name".
    """
    if sys.platform != "darwin" or not shutil.which("codesign"):
        return False
    command = ["codesign", "--force", "--deep", "--sign", identity, "--identifier", BUNDLE_ID]
    if identity == "-":
        # An ad-hoc signature's designated requirement is a bare cdhash, and
        # tccd stores grants against that as SESSION-SCOPED ("Session scoped
        # auth is invalid for client" in the TCC log, then a fresh prompt on
        # every launch - observed 2026-08-30). An explicit identifier-based
        # requirement gives TCC something stable to persist against.
        command += ["--requirements", f'=designated => identifier "{BUNDLE_ID}"']
    result = subprocess.run(command + [path], capture_output=True, text=True)
    if result.returncode != 0:
        out(f"codesign with identity {identity!r} failed (bundle still works, TCC "
            f"grants may not stick): {result.stderr.strip()}")
        return False
    return True


def install_app(app_dir=None, interpreter=None, src_dir=None, name=APP_NAME, out=print,
                sign_identity=None):
    """
    Write (or refresh) the bundle. Returns its path, or None when it
    refused to overwrite something it did not create. The signing identity
    is remembered in Info.plist so re-runs keep using it; pass one to
    change it ("-" for ad-hoc).
    """
    app_dir = os.path.abspath(app_dir or default_app_dir())
    interpreter = interpreter or sys.executable
    src_dir = src_dir or package_src_dir()
    path = bundle_path(app_dir, name)

    if os.path.exists(path) and not is_our_bundle(path):
        out(f"Refusing to overwrite '{path}': it is not a bundle written by this tool.")
        return None

    existing = recorded_interpreter(path)
    sign_identity = sign_identity or recorded_sign_identity(path) or "-"
    contents = os.path.join(path, "Contents")
    macos = os.path.join(contents, "MacOS")
    resources = os.path.join(contents, "Resources")
    os.makedirs(macos, exist_ok=True)
    os.makedirs(resources, exist_ok=True)

    launcher = os.path.join(macos, "launcher")
    source = os.path.join(macos, "launcher.c")
    for stale in (launcher, source):
        if os.path.exists(stale):
            os.remove(stale)

    with open(source, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(launcher_c_source(interpreter, src_dir))
    kind = "compiled" if compile_launcher(source, launcher, out) else "script"
    if kind == "script":
        os.remove(source)
        with open(launcher, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(launcher_text(interpreter, src_dir))
    os.chmod(launcher, os.stat(launcher).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    plist = info_plist(name)
    plist[SIGN_IDENTITY_KEY] = sign_identity
    with open(os.path.join(contents, "Info.plist"), "wb") as handle:
        plistlib.dump(plist, handle)

    with open(os.path.join(contents, "PkgInfo"), "w", encoding="ascii") as handle:
        handle.write("APPL????")

    if os.path.isfile(ICON_SOURCE):
        shutil.copyfile(ICON_SOURCE, os.path.join(resources, "AppIcon.icns"))

    verb = "Updated" if existing else "Installed"
    out(f"{verb} app bundle: '{path}'")
    out(f"  interpreter: '{interpreter}'")
    out(f"  package dir: '{src_dir}'")
    out(f"  launcher:    {kind}")
    if kind == "script":
        out("  (no C compiler found: macOS will attribute folder permissions to the")
        out("   interpreter instead of the app; install the Xcode Command Line Tools")
        out("   with `xcode-select --install` and re-run --install-app to fix that)")
    if codesign(path, out, sign_identity):
        out(f"  signed:      {'ad-hoc' if sign_identity == '-' else sign_identity}")
        if sign_identity == "-":
            out("  requirement: identifier-based (so TCC can persist grants)")
            out("  verify:      codesign -dr - \"<the .app>\"   ->   designated => identifier ...")

    out("")
    out("Next steps:")
    out(f"  open \"{path}\"                        # launch it now")
    out("  System Settings > General > Login Items > '+' and pick it   # launch at login")
    out(f"  launcher output: ~/{LOG_RELATIVE}")
    out("On first launch macOS asks whether the app may 'access data from other")
    out("apps' (the Stickies container). Allow it; Don't Allow is a silent,")
    out("permanent deny until `tccutil reset` - the menu icon turns yellow.")
    return path


def uninstall_app(app_dir=None, name=APP_NAME, out=print):
    app_dir = os.path.abspath(app_dir or default_app_dir())
    path = bundle_path(app_dir, name)
    if not os.path.exists(path):
        out(f"No app bundle at '{path}'")
        return False
    if not is_our_bundle(path):
        out(f"Refusing to remove '{path}': it is not a bundle written by this tool.")
        return False
    shutil.rmtree(path)
    out(f"Removed app bundle: '{path}'")
    out("If it was a Login Item, remove it in System Settings > General > Login Items.")
    return True


# End of file #
