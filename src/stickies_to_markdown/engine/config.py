"""
Configuration: load, auto-save, hot-reload support.

Mechanics carried over from Duplicate-File-Preventer unchanged:

- Saves are atomic (temp file + os.replace) because a second process may be
  reading the file while the terminal edits it.
- set() is silent. Telling the user "saved" is a front-end decision.
- changed_on_disk()/reload() support hot reload from a running engine.

Keys are this tool's (handoff §5.5).
"""

import os
import copy
import json
import platform
import tempfile


CONFIG_FILENAME = "stickies_to_markdown.json"
LOG_FILENAME = "stickies_to_markdown.log"

# Post-Catalina per-note storage (handoff §4). Verify on the target Mac
# (first-session checklist step 1) before relying on it.
DEFAULT_STICKIES_DIR = (
    "~/Library/Containers/com.apple.Stickies/Data/Library/Stickies")

FILENAME_STYLES = ("slug-uuid", "uuid")
ON_DELETE_CHOICES = ("tombstone", "delete", "keep")
CONVERTER_CHOICES = ("auto", "textutil", "text")
FLAVOR_CHOICES = ("generic", "obsidian")


class ConfigError(Exception):
    """Config file exists but cannot be parsed."""


def default_config_dir():
    """Platform-standard configuration directory."""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get('APPDATA', os.path.expanduser('~'))
        return os.path.join(base, 'StickiesToMarkdown')
    if system == "Darwin":
        return os.path.expanduser('~/Library/Application Support/StickiesToMarkdown')
    xdg_config = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
    return os.path.join(xdg_config, 'stickies-to-markdown')


class Config:
    """
    Auto-saving configuration.

    Every set() writes the file unless the instance is detached (see
    detached()), which is how one-off exports get temporary overrides
    without touching the user's settings.
    """

    def __init__(self, config_file=None, autosave=True):
        if config_file is None:
            self.config_dir = default_config_dir()
            self.config_file = os.path.join(self.config_dir, CONFIG_FILENAME)
        else:
            self.config_file = os.path.abspath(config_file)
            self.config_dir = os.path.dirname(self.config_file)

        os.makedirs(self.config_dir, exist_ok=True)
        self.autosave = autosave

        self.default_config = {
            "stickies_dir": DEFAULT_STICKIES_DIR,   # auto-detected default
            "output_dir": "",                       # must be set before export
            "filename_style": "slug-uuid",          # or "uuid"
            "on_delete": "tombstone",               # or "delete" / "keep"
            "debounce_seconds": 3.0,                # per-note quiet time (watcher)
            "settle_seconds": 1.0,                  # package stops changing
            "include_attachments": True,
            "front_matter": True,
            "flavor": "generic",                    # or "obsidian"
            "read_only_output": True,               # chmod 444 mirror files
            "converter": "auto",                    # textutil|text
            "log_file": os.path.join(self.config_dir, LOG_FILENAME),
            "log_level": "INFO",
            "log_max_size": 10,                     # MB
            "log_backup_count": 5,
            "dry_run": False,
        }

        self._disk_signature = None
        self.config = self.load_config()

    # --- load / save -------------------------------------------------------

    def _signature(self):
        """(mtime_ns, size) of the file on disk, or None when absent."""
        try:
            stat_info = os.stat(self.config_file)
        except OSError:
            return None
        return (stat_info.st_mtime_ns, stat_info.st_size)

    def load_config(self):
        """Read the file, merging in defaults for any missing keys."""
        if not os.path.exists(self.config_file):
            self._disk_signature = None
            return copy.deepcopy(self.default_config)

        try:
            with open(self.config_file, 'r', encoding='utf-8') as handle:
                loaded = json.load(handle)
        except (OSError, ValueError) as error:
            raise ConfigError(f"{self.config_file}: {error}") from error

        if not isinstance(loaded, dict):
            raise ConfigError(f"{self.config_file}: top level is not an object")

        for key, value in self.default_config.items():
            if key not in loaded:
                loaded[key] = copy.deepcopy(value)

        self._disk_signature = self._signature()
        return loaded

    def save_config(self):
        """Atomic write: temp file in the same directory, then os.replace()."""
        os.makedirs(self.config_dir, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix=".stickies_to_markdown.", suffix=".json.tmp", dir=self.config_dir)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(self.config, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.config_file)
        except BaseException:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
        self._disk_signature = self._signature()

    # --- access ------------------------------------------------------------

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        if self.autosave:
            self.save_config()

    def update(self, values):
        """Set several keys with a single save."""
        self.config.update(values)
        if self.autosave:
            self.save_config()

    # --- resolved paths ----------------------------------------------------

    def stickies_dir(self):
        return os.path.expanduser(self.get("stickies_dir") or DEFAULT_STICKIES_DIR)

    def output_dir(self):
        value = self.get("output_dir") or ""
        return os.path.expanduser(value) if value else ""

    # --- hot reload --------------------------------------------------------

    def changed_on_disk(self):
        """True when another writer has touched the file since we last read it."""
        return self._signature() != self._disk_signature

    def reload(self):
        """
        Re-read the file. On failure the in-memory config is left alone and
        ConfigError propagates; the caller decides how loudly to complain.
        """
        self.config = self.load_config()

    # --- detached copies ---------------------------------------------------

    def detached(self, **overrides):
        """
        A copy that never saves. Used for one-off exports with different
        settings than the user's persistent ones.
        """
        clone = copy.copy(self)
        clone.autosave = False
        clone.config = copy.deepcopy(self.config)
        clone.config.update(overrides)
        return clone


# End of file #
