#!/usr/bin/env python3
"""
Bump the date-based version in src/stickies_to_markdown/_version.py.

    python3 dev_notes/bump_version.py          # -> YYYYMMDD.0 today, or .N+1 if already today
    python3 dev_notes/bump_version.py --show   # print without changing

Convention: YYYYMMDD.N, N counting releases within the day from 0. Part of
every delta: bump, then package. The string also feeds CFBundleVersion via
--install-app and `stickies2md --version`.
"""

import re
import sys
import datetime

from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent.parent / "src" / "stickies_to_markdown" / "_version.py"
PATTERN = re.compile(r'__version__ = "(\d{8})\.(\d+)"')


def main():
    text = VERSION_FILE.read_text(encoding="utf-8")
    match = PATTERN.search(text)
    if not match:
        sys.exit(f"no YYYYMMDD.N version found in {VERSION_FILE}")
    today = datetime.date.today().strftime("%Y%m%d")
    current_date, current_n = match.group(1), int(match.group(2))
    new = f"{today}.{current_n + 1 if current_date == today else 0}"
    if "--show" in sys.argv:
        print(f"{current_date}.{current_n} -> {new}")
        return
    VERSION_FILE.write_text(text.replace(match.group(0), f'__version__ = "{new}"'), encoding="utf-8")
    print(f"{current_date}.{current_n} -> {new}")


if __name__ == "__main__":
    main()


# End of File #
