#!/usr/bin/env python3
"""
Run every test module standalone and print one score.

tests/run_all.py

Each module is run in its own interpreter so a crash in one cannot take
the others down, and so the output is exactly what `python3 tests/test_x.py`
would show. Use `pytest` for the terse version.
"""

import os
import sys
import subprocess

from pathlib import Path


def main():
    here = Path(__file__).resolve().parent
    modules = sorted(p for p in here.glob("test_*.py"))
    results = []

    for module in modules:
        print(f"\n{'#' * 70}\n# {module.name}\n{'#' * 70}")
        proc = subprocess.run([sys.executable, str(module)], cwd=str(here.parent))
        results.append((module.name, proc.returncode == 0))

    print(f"\n{'=' * 70}")
    width = max(len(name) for name, _ in results)
    for name, ok in results:
        print(f"  {'✅' if ok else '❌'}  {name:<{width}}")
    passed = sum(1 for _, ok in results if ok)
    print(f"\n=== {passed}/{len(results)} test modules passed ===")
    return passed == len(results)


if __name__ == '__main__':
    exit(0 if main() else 1)


# End of file #
