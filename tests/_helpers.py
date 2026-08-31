"""
Shared scaffolding for the test modules.

tests/_helpers.py

Every test builds a real fake container, real config and real output folder
under a temporary directory: no mocks, no patched filesystem. Importing this
module also makes `stickies_to_markdown` importable straight from the
checkout so `python3 tests/test_x.py` works without an install.

The fixtures in tests/fixtures/ are synthetic .rtfd packages (built by
fixtures/make_fixtures.py). They are structurally valid RTFD but are NOT
byte-real Stickies output; replace/augment them with real packages from a
Mac per checklist §7 step 7 before trusting tier-1/2 conversion.
"""

import sys
import time
import shutil
import tempfile

from pathlib import Path

# src layout: make the package importable from a bare checkout.
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stickies_to_markdown.engine import Config      # noqa: E402
from stickies_to_markdown.engine.config import TARGET_DEFAULTS   # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class Sandbox:
    """
    A temp dir holding: a fake Stickies container populated from the
    fixtures, an output folder, and a config file pointing at both.
    """

    def __init__(self, with_state=True, **config_overrides):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.container = self.root / "Stickies"
        self.output = self.root / "Synced_from_Stickies"
        self.config_dir = self.root / "config"
        self.container.mkdir()
        self.config_dir.mkdir()

        for item in sorted(FIXTURES.iterdir()):
            if item.suffix == ".rtfd" and item.is_dir():
                shutil.copytree(item, self.container / item.name)
        if with_state and (FIXTURES / ".SavedStickiesState").exists():
            shutil.copy2(FIXTURES / ".SavedStickiesState",
                         self.container / ".SavedStickiesState")

        self.config = Config(
            config_file=str(self.config_dir / "stickies_to_markdown.json"))
        # Per-output keys (on_delete, flavor, exclude_colors, ...) go into
        # the single "default" output block; everything else is global.
        block = {"name": "default", "output_dir": str(self.output)}
        settings = {
            "stickies_dir": str(self.container),
            "log_file": str(self.config_dir / "stickies_to_markdown.log"),
        }
        for key, value in config_overrides.items():
            (block if key in TARGET_DEFAULTS else settings)[key] = value
        settings["outputs"] = [block]
        self.config.update(settings)

    @property
    def target(self):
        """The single output block of this sandbox, freshly read."""
        return self.config.targets()[0]

    def set_target(self, key, value):
        self.config.set_target("default", key, value)

    # --- content helpers ---------------------------------------------------

    def note_dirs(self):
        return sorted(p for p in self.container.iterdir() if p.suffix == ".rtfd")

    def mirror_files(self):
        if not self.output.is_dir():
            return []
        return sorted(p for p in self.output.glob("*.md"))

    def tree_signature(self, top):
        """Hashable snapshot of a tree: (relpath, size, sha) per file."""
        import hashlib
        entries = []
        top = Path(top)
        for path in sorted(top.rglob("*")):
            if path.is_file():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                entries.append((str(path.relative_to(top)),
                                path.stat().st_size, digest))
        return tuple(entries)

    def log_text(self):
        try:
            return Path(self.config.get("log_file")).read_text(encoding="utf-8")
        except OSError:
            return ""

    def close(self):
        # Read-only mirror files (chmod 444) still delete fine because the
        # DIRECTORIES stay writable; nothing special needed on POSIX.
        self._tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def wait_for(predicate, timeout=5.0, interval=0.05):
    """Poll until predicate() is truthy or timeout; returns the final value."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def check(condition, ok_text, fail_text):
    """Print a ✓/✗ line and return the condition."""
    if condition:
        print(f"  ✓ {ok_text}")
    else:
        print(f"  ✗ {fail_text}")
    return bool(condition)


def run_suite(title, tests):
    """Run test functions, print a score, return True when all passed."""
    print(f"=== {title} ===")
    passed = 0
    for test_func in tests:
        try:
            if test_func():
                passed += 1
        except Exception as error:
            import traceback
            traceback.print_exc()
            print(f"  ✗ {test_func.__name__} crashed: {type(error).__name__}: {error}")
    print(f"\n=== Results: {passed}/{len(tests)} tests passed ===")
    if passed == len(tests):
        print(f"✅ All {title} passed!")
        return True
    print(f"❌ Some {title} failed!")
    return False


# End of file #
