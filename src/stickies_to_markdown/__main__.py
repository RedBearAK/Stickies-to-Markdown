"""
Entry point and dispatch.

    stickies2md                   interactive menu (settings, logs, install)
    stickies2md --start           watch in the foreground
    stickies2md --once            export every note and exit
    stickies2md --follow-log      live log
    stickies2md --menubar         macOS menu bar app (optional extra: rumps)
    stickies2md --install-command / --install-app
"""

import sys
import platform


CLI_FLAGS = ("--start", "-s", "--once", "-o", "--show-log", "-l", "--follow-log", "-f",
             "--show-config", "--set",
             "--install-command", "--uninstall-command", "--install-app", "--uninstall-app",
             "--dry-run", "-d", "--config", "-c", "--version", "-V", "--help", "-h")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--menubar" in argv:
        if platform.system() != "Darwin":
            sys.exit("--menubar is only available on macOS")
        try:
            from stickies_to_markdown.frontends.menubar import run_menubar
        except ImportError as error:
            sys.exit("Menu bar mode needs the optional extra: "
                     f"pip install '.[menubar]'  ({error})")
        from stickies_to_markdown.engine import Config
        config_file = None
        if "--config" in argv:
            config_file = argv[argv.index("--config") + 1]
        return run_menubar(Config(config_file=config_file))

    if any(a in CLI_FLAGS or a.startswith(("--config=", "--set=")) for a in argv):
        from stickies_to_markdown.frontends.cli import run_cli
        return run_cli(argv)

    from stickies_to_markdown.frontends.tui import run_tui
    return run_tui()


if __name__ == "__main__":
    sys.exit(main())


# End of file #
