"""
Configuration: load, auto-save, hot-reload support.

Mechanics carried over from Duplicate-File-Preventer unchanged:

- Saves are atomic (temp file + os.replace) because a second process may be
  reading the file while the terminal edits it.
- set() is silent. Telling the user "saved" is a front-end decision.
- changed_on_disk()/reload() support hot reload from a running engine.

Shape (JSON):

    {
      "stickies_dir": ..., "converter": ..., ...      global keys
      "outputs": [                                    one block per mirror folder
        {"name": "vault", "output_dir": "~/Obsidian/Vault/Synced_from_Stickies",
         "flavor": "obsidian", "on_delete": "archive", ...},
        {"name": "plain", "output_dir": "~/Dropbox/Notes", "flavor": "generic"}
      ]
    }

Global keys govern reading, converting, watching and logging. Every
per-folder decision (flavor, naming, deletion policy, exclusions,
attachments) lives in its output block. A pre-multi-output file with
output_dir at the top level is migrated into a single "default" block.
"""

import os
import copy
import json
import platform
import tempfile


CONFIG_FILENAME = "stickies_to_markdown.json"
LOG_FILENAME = "stickies_to_markdown.log"

# Post-Catalina per-note storage (verified, dev_notes/MAC_FINDINGS.md).
DEFAULT_STICKIES_DIR = (
    "~/Library/Containers/com.apple.Stickies/Data/Library/Stickies")

FILENAME_STYLES = ("slug-uuid", "uuid")
# What happens to a mirror file when its note is deleted in Stickies:
#   archive  move it to deleted_dir, annotated with deleted-from-stickies
#   mark     leave it in place, annotated with deleted-from-stickies
#   delete   remove it
#   keep     leave it exactly as it is (an unannotated orphan)
# "tombstone" is accepted as an alias of "archive" (earlier name).
ON_DELETE_CHOICES = ("archive", "mark", "delete", "keep")
ON_DELETE_ALIASES = {"tombstone": "archive"}
CONVERTER_CHOICES = ("auto", "textutil", "pandoc", "text")
FLAVOR_CHOICES = ("generic", "obsidian", "floating-sticky-notes", "sticky-notes",
                  "colorful-stickynotes")
DEFAULT_SUBFOLDER = "Synced_from_Stickies"

# One block per mirror folder. Every key is optional in the file; missing
# ones take these defaults. `name` is the handle used by --set NAME.KEY and
# the menu; `output_dir` is the only one that must be set.
TARGET_DEFAULTS = {
    "name": "",
    "output_dir": "",
    # The mirror lives in output_dir/<subfolder>, created on first export,
    # so pointing an output at a vault or Documents never spills files into
    # it. "" writes directly into output_dir.
    "subfolder": DEFAULT_SUBFOLDER,
    "flavor": "generic",                    # one or more of FLAVOR_CHOICES, comma-separated
    "filename_style": "slug-uuid",          # or "uuid"
    "on_delete": "archive",                 # mark | delete | keep
    "deleted_dir": "_deleted",              # relative to output_dir, or absolute
    "on_exclude": "delete",                 # archive | mark | delete | keep
    "exclude_colors": [],                   # e.g. ["gray"]
    "exclude_title_regex": "",              # e.g. "^\\s*#private\\b"
    "read_only_output": True,               # chmod 444 mirror files
    "include_attachments": True,
    "front_matter": True,
}

# Keys that used to live at the top level of a single-output config.
_LEGACY_TARGET_KEYS = [k for k in TARGET_DEFAULTS if k != "name"]


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


class OutputTarget:
    """A read-only view of one output block with the resolved accessors the
    writer needs. Edits go through Config.set_target()."""

    def __init__(self, data):
        self.data = dict(TARGET_DEFAULTS)
        self.data.update(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    @property
    def name(self):
        return self.data.get("name") or "default"

    def base_dir(self):
        """The folder the user named, before the subfolder."""
        value = self.data.get("output_dir") or ""
        return os.path.expanduser(value) if value else ""

    def subfolder(self):
        value = self.data.get("subfolder")
        return DEFAULT_SUBFOLDER if value is None else str(value).strip().strip("/")

    def output_dir(self):
        """Where files actually go: base/subfolder - unless the subfolder is
        blank, or the base already IS that subfolder (no double nesting)."""
        base = self.base_dir()
        sub = self.subfolder()
        if not base or not sub or os.path.basename(base.rstrip("/")) == sub:
            return base
        return os.path.join(base, sub)

    def on_delete(self):
        value = str(self.data.get("on_delete") or "archive")
        return ON_DELETE_ALIASES.get(value, value)

    def on_exclude(self):
        value = str(self.data.get("on_exclude") or "delete")
        return ON_DELETE_ALIASES.get(value, value)

    def deleted_dir(self):
        value = os.path.expanduser(str(self.data.get("deleted_dir") or "_deleted"))
        return value if os.path.isabs(value) else os.path.join(self.output_dir(), value)

    def __repr__(self):
        return f"OutputTarget({self.name!r}, {self.output_dir()!r})"


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
            # --- global: reading Stickies, converting, watching, logging ---
            "stickies_dir": DEFAULT_STICKIES_DIR,   # auto-detected default
            "converter": "auto",                    # textutil|pandoc|text
            "debounce_seconds": 3.0,                # per-note quiet time; Stickies
                                                    # autosaves 8+ s apart (verified)
            "settle_seconds": 1.0,                  # package stops changing; the
                                                    # mid-save gap seen was 0.5 s
            # A note needing this many escapes AND this density (per 100
            # non-space chars) is emitted verbatim in a fenced code block
            # instead of escaped. 0 in either disables.
            "code_block_min_escapes": 6,
            "code_block_density": 4.0,
            "log_file": os.path.join(self.config_dir, LOG_FILENAME),
            "log_level": "INFO",
            "log_max_size": 10,                     # MB
            "log_backup_count": 5,
            "dry_run": False,
            # --- outputs: one block per mirror folder (TARGET_DEFAULTS) ---
            "outputs": [],
        }

        self._disk_signature = None
        self.config = self.load_config()

    # --- load / save -------------------------------------------------------

    def _signature(self):
        try:
            stat_info = os.stat(self.config_file)
        except OSError:
            return None
        return (stat_info.st_mtime_ns, stat_info.st_size)

    def load_config(self):
        """Read the file, merging in defaults; migrate a legacy flat file."""
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

        migrated = self._migrate_legacy(loaded)
        for key, value in self.default_config.items():
            if key not in loaded:
                loaded[key] = copy.deepcopy(value)
        if not isinstance(loaded.get("outputs"), list):
            loaded["outputs"] = []

        self._disk_signature = self._signature()
        if migrated and self.autosave:
            self.config = loaded
            self.save_config()
        return loaded

    @staticmethod
    def _migrate_legacy(loaded):
        """output_dir (and friends) at the top level -> one "default" block."""
        legacy = {k: loaded.pop(k) for k in _LEGACY_TARGET_KEYS if k in loaded}
        if not legacy:
            return False
        if legacy.get("output_dir") and not loaded.get("outputs"):
            legacy["name"] = "default"
            loaded["outputs"] = [legacy]
        return True

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

    # --- global access -----------------------------------------------------

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        if self.autosave:
            self.save_config()

    def update(self, values):
        self.config.update(values)
        if self.autosave:
            self.save_config()

    def stickies_dir(self):
        return os.path.expanduser(self.get("stickies_dir") or DEFAULT_STICKIES_DIR)

    # --- outputs -----------------------------------------------------------

    def targets(self):
        """Every configured output block, as OutputTarget objects."""
        return [OutputTarget(block) for block in self.get("outputs") or []
                if isinstance(block, dict)]

    def target(self, name):
        for target in self.targets():
            if target.name == name:
                return target
        return None

    def output_dirs(self):
        return [t.output_dir() for t in self.targets() if t.output_dir()]

    def has_outputs(self):
        return bool(self.output_dirs())

    def add_target(self, name, output_dir, **keys):
        if not name or "." in name:
            raise ValueError("output name must be non-empty and contain no '.'")
        if self.target(name) is not None:
            raise ValueError(f"an output named {name!r} already exists")
        block = {"name": name, "output_dir": output_dir}
        for key, value in keys.items():
            if key not in TARGET_DEFAULTS:
                raise ValueError(f"unknown output setting: {key}")
            block[key] = value
        outputs = list(self.get("outputs") or [])
        outputs.append(block)
        self.set("outputs", outputs)
        return OutputTarget(block)

    def remove_target(self, name):
        outputs = [b for b in (self.get("outputs") or [])
                   if (b.get("name") or "default") != name]
        if len(outputs) == len(self.get("outputs") or []):
            raise ValueError(f"no output named {name!r}")
        self.set("outputs", outputs)

    def set_target(self, name, key, value):
        if key not in TARGET_DEFAULTS or key == "name":
            raise ValueError(f"unknown output setting: {key}")
        outputs = copy.deepcopy(self.get("outputs") or [])
        for block in outputs:
            if (block.get("name") or "default") == name:
                block[key] = value
                self.set("outputs", outputs)
                return
        raise ValueError(f"no output named {name!r}")

    def rename_target(self, name, new_name):
        if not new_name or "." in new_name:
            raise ValueError("output name must be non-empty and contain no '.'")
        if self.target(new_name) is not None:
            raise ValueError(f"an output named {new_name!r} already exists")
        outputs = copy.deepcopy(self.get("outputs") or [])
        for block in outputs:
            if (block.get("name") or "default") == name:
                block["name"] = new_name
                self.set("outputs", outputs)
                return
        raise ValueError(f"no output named {name!r}")

    def single_target(self, output_dir, **keys):
        """A detached config with exactly one output (per-run overrides),
        inheriting the first configured block's settings when there is one."""
        block = dict(TARGET_DEFAULTS)
        if self.targets():
            block.update(self.targets()[0].data)
        block.update({"name": "override", "output_dir": output_dir})
        block.update(keys)
        return self.detached(outputs=[block])

    # --- hot reload --------------------------------------------------------

    def changed_on_disk(self):
        return self._signature() != self._disk_signature

    def reload(self):
        self.config = self.load_config()

    # --- detached copies ---------------------------------------------------

    def detached(self, **overrides):
        """A copy that never saves; overrides may include a whole `outputs` list."""
        clone = copy.copy(self)
        clone.autosave = False
        clone.config = copy.deepcopy(self.config)
        clone.config.update(overrides)
        return clone


# End of file #
