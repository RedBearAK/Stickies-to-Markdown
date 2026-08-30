"""
Entry point and dispatch.

    stickies2md --once            export every note and exit
    stickies2md --show-log        print the log
    stickies2md --follow-log      live log
    stickies2md --set KEY=VALUE   change a setting

Phase 2 adds: (no flags) interactive menu, --start, --menubar,
--install-command, --install-app - from the DFP patterns.
"""

import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    from stickies_to_markdown.frontends.cli import run_cli
    return run_cli(argv)


if __name__ == "__main__":
    sys.exit(main())


# End of file #
