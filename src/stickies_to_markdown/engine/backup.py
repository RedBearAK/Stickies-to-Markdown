"""
Backup outputs: a verbatim, restorable copy of the Stickies container.

The Markdown mirror is lossy by design; the .rtfd packages plus
.SavedStickiesState are the only lossless form of the notes, and Stickies
keeps no history and does not sync. A backup block keeps:

- a REPLICA tree (default on): every package copied byte-for-byte with
  mtimes preserved, plus the state file; one file changes per edit, so a
  Dropbox/iCloud folder syncs only deltas (and adds its own version
  history). A package that vanishes from Stickies is moved to
  `_deleted/<timestamp>/` rather than dropped, and pruned after
  keep_deleted_days - accidental deletion is the common recovery case.
- SNAPSHOTS (default off): a timestamped zip of the corpus, written at
  most once per snapshot_every_days, only when the corpus changed since the
  last one and has been quiet for snapshot_quiet_seconds. Pruned to the
  newest keep_snapshots. With replica off this is a "zip every N days"
  backup for text-only users.

The replica lives in a `.noindex` folder by default (Spotlight skips the
whole subtree); snapshots go where the user said.

Safety rules shared with the Markdown writer:
- The folder the user NAMED must already exist. If it does not (unmounted
  volume, disconnected share) the output is "unavailable": one error event,
  nothing written, and never a makedirs on it - creating /Volumes/X/... on
  an unmounted drive would plant a local folder at the mount point and
  silently swallow the backup. Only the tool's own subfolder inside an
  existing base is ever created.
- Snapshot pruning touches only zips matching our name pattern AND
  carrying our marker in the zip comment; tombstone pruning only
  timestamp-named folders under our _deleted/.
- Restore is a separate explicit command (restore_container), the one
  deliberate write inside the container. It refuses while Stickies runs
  and zips the live container first.
"""

import os
import re
import json
import time
import shutil
import hashlib
import zipfile
import tempfile
import subprocess

from stickies_to_markdown.engine.events import Event
from stickies_to_markdown.engine.logsetup import get_logger
from stickies_to_markdown.engine.stickies import STATE_FILENAME
from stickies_to_markdown.engine.writer import write_root_about, ROOT_ABOUT_VERSION


DELETED_DIR = "_deleted"
README_NAME = "_About this backup (how to restore).md"
BACKUP_ROOT_ABOUT = """# Stickies backups - one folder per Mac

Each folder here is a **verbatim, restorable backup of Apple Stickies on
one Mac**, maintained by Stickies-to-Markdown. Stickies do not sync between
Macs, so every Mac gets its own folder, named by a stable machine id that
survives renaming the computer; the `_About` note inside each folder says
which Mac it is and how to restore.

- `.noindex` in the parent folder name keeps Spotlight out of all of this.
- Each Mac maintains only its own folder and never touches another's.
- This note is shared and maintained by the tool; it changes only when the
  tool's wording does.
"""
SNAPSHOT_MARKER = "stickies-to-markdown backup"
SNAPSHOT_PREFIX = "Stickies_backup_"
_STAMP_FMT = "%Y%m%d-%H%M%S"
_STAMP_RE = re.compile(r"^\d{8}-\d{6}$")
_SNAPSHOT_RE = re.compile(r"^Stickies_backup_(?P<machine>[^_]+)_(?P<stamp>\d{8}-\d{6})\.zip$")
_UUID_DIR_RE = re.compile(r"^[0-9A-Fa-f-]{36}\.rtfd$")
_STORED_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".heic", ".mov", ".mp4", ".pdf", ".zip", ".m4a"}


def _file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def corpus_hash(root):
    """One hash over every file (relative path + content) under root."""
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            if rel.startswith(DELETED_DIR + os.sep) or name == README_NAME or name == ".DS_Store":
                continue
            digest.update(rel.encode("utf-8") + b"\0")
            try:
                digest.update(_file_hash(path).encode("ascii") + b"\0")
            except OSError:
                digest.update(b"?\0")
    return digest.hexdigest()


def _stamp(ts=None):
    return time.strftime(_STAMP_FMT, time.localtime(ts))


class BackupWriter:
    """Per-output backup writer; same call surface as writer.Writer where
    the processor needs it (export_note, handle_deletions, maintain_extras)."""

    def __init__(self, config, target, events, logger=None):
        self.config = config
        self.target = target
        self.events = events
        self.logger = logger or get_logger()
        if not target.output_dir():
            raise ValueError(f"output '{target.name}' has no output_dir")
        self.last_excluded = []
        self._unavailable_reported = False
        self._last_change = 0.0

    # --- availability --------------------------------------------------------

    @property
    def name(self):
        return self.target.name

    @property
    def output_dir(self):
        return self.target.output_dir()

    @property
    def dry_run(self):
        return bool(self.config.get("dry_run"))

    def available(self):
        """The folder the user named exists (a mount point is no proof of a
        mounted volume, but a MISSING one is proof of an unmounted one)."""
        base = self.target.base_dir()
        if os.path.isdir(base):
            if self._unavailable_reported:
                self.logger.info(f"Output '{self.name}': '{base}' is back")
                self._unavailable_reported = False
            return True
        if not self._unavailable_reported:
            self._unavailable_reported = True
            message = f"output folder unavailable (not mounted?): '{base}'"
            self.logger.error(f"Output '{self.name}': {message}")
            self.events.put(Event("error", base, message))
        return False

    def refresh_index(self):
        pass

    # --- replica --------------------------------------------------------------

    def export_note(self, note, markdown=None, attachments=None, body_format=None):
        """Replicate one package verbatim. Returns 'converted' | 'unchanged' | 'error'."""
        if not self.available():
            return "error"
        if not self.target.get("replica", True):
            self._last_change = max(self._last_change, self._package_mtime(note.rtfd_path))
            return "unchanged"
        try:
            changed = self._sync_tree(note.rtfd_path, os.path.join(self.output_dir, os.path.basename(note.rtfd_path)))
        except OSError as error:
            self.logger.error(f"Output '{self.name}': '{note.uuid8}': {error}")
            self.events.put(Event("error", note.rtfd_path, f"backup: {error}"))
            return "error"
        kind = "converted" if changed else "unchanged"
        if changed:
            self._last_change = time.time()
            self.logger.info(f"Output '{self.name}': {'would replicate' if self.dry_run else 'replicated'} "
                             f"'{os.path.basename(note.rtfd_path)}'")
        self.events.put(Event(kind, os.path.join(self.output_dir, os.path.basename(note.rtfd_path)),
                              "dry run" if self.dry_run else f"backup {self.name}"))
        return kind

    def _sync_tree(self, source, dest):
        """Make dest a byte-identical copy of source (files only, flat or
        nested), mtimes preserved, extra files removed. Returns True if
        anything changed."""
        changed = False
        wanted = set()
        for dirpath, _dirs, files in os.walk(source):
            rel_dir = os.path.relpath(dirpath, source)
            for name in files:
                rel = name if rel_dir == "." else os.path.join(rel_dir, name)
                wanted.add(rel)
                src = os.path.join(dirpath, name)
                dst = os.path.join(dest, rel)
                if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src) \
                        and _file_hash(dst) == _file_hash(src):
                    continue
                changed = True
                if self.dry_run:
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                fd, temp = tempfile.mkstemp(prefix=".s2m.", dir=os.path.dirname(dst))
                os.close(fd)
                shutil.copy2(src, temp)
                os.replace(temp, dst)
        if os.path.isdir(dest):
            for dirpath, _dirs, files in os.walk(dest):
                rel_dir = os.path.relpath(dirpath, dest)
                for name in files:
                    rel = name if rel_dir == "." else os.path.join(rel_dir, name)
                    if rel not in wanted and not name.startswith(".s2m."):
                        changed = True
                        if not self.dry_run:
                            os.remove(os.path.join(dirpath, name))
        return changed

    def _sync_state_file(self):
        src = os.path.join(self.config.stickies_dir(), STATE_FILENAME)
        dst = os.path.join(self.output_dir, STATE_FILENAME)
        if not os.path.isfile(src):
            return False
        if os.path.isfile(dst) and _file_hash(dst) == _file_hash(src):
            return False
        if not self.dry_run:
            os.makedirs(self.output_dir, exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix=".s2m.", dir=self.output_dir)
            os.close(fd)
            shutil.copy2(src, temp)
            os.replace(temp, dst)
        self._last_change = time.time()
        return True

    @staticmethod
    def _package_mtime(path):
        newest = 0.0
        for dirpath, _d, files in os.walk(path):
            for name in files:
                try:
                    newest = max(newest, os.stat(os.path.join(dirpath, name)).st_mtime)
                except OSError:
                    pass
        return newest

    # --- deletions -> tombstones ----------------------------------------------

    def handle_deletions(self, live_uuids, excluded_uuids=()):
        """Packages in the replica whose note is gone move to
        _deleted/<stamp>/; returns the affected package names."""
        self.last_excluded = []
        if not self.available() or not self.target.get("replica", True):
            return []
        affected = []
        try:
            names = sorted(os.listdir(self.output_dir))
        except OSError:
            return []
        stamp_dir = None
        for name in names:
            if not _UUID_DIR_RE.match(name):
                continue
            uuid = name[:-5].upper()
            if uuid in live_uuids:
                continue
            affected.append(name)
            self.logger.info(f"Output '{self.name}': note deleted, tombstoning '{name}'"
                             f"{' (dry run)' if self.dry_run else ''}")
            if self.dry_run:
                continue
            if stamp_dir is None:
                stamp_dir = os.path.join(self.output_dir, DELETED_DIR, _stamp())
                os.makedirs(stamp_dir, exist_ok=True)
            shutil.move(os.path.join(self.output_dir, name), os.path.join(stamp_dir, name))
            self.events.put(Event("deleted", os.path.join(stamp_dir, name), f"backup {self.name}"))
        if affected:
            self._last_change = time.time()
        self._prune_tombstones()
        return affected

    def _prune_tombstones(self):
        days = int(self.target.get("keep_deleted_days", 30) or 0)
        if days <= 0 or self.dry_run:
            return
        root = os.path.join(self.output_dir, DELETED_DIR)
        if not os.path.isdir(root):
            return
        cutoff = time.time() - days * 86400
        for name in os.listdir(root):
            if not _STAMP_RE.match(name):
                continue
            try:
                when = time.mktime(time.strptime(name, _STAMP_FMT))
            except ValueError:
                continue
            if when < cutoff:
                shutil.rmtree(os.path.join(root, name), ignore_errors=True)
                self.logger.info(f"Output '{self.name}': pruned tombstones from '{name}'")

    # --- extras: state file, readme, snapshots ---------------------------------

    def maintain_extras(self, force_snapshot=False, ignore_quiet=False):
        if not self.available():
            return []
        actions = []
        if self.target.get("replica", True):
            if self._sync_state_file():
                actions.append("state file replicated")
            if self.target.get("readme_note", True):
                if self._write_readme():
                    actions.append("wrote readme")
                raw = self.target.data.get("subfolder")
                from stickies_to_markdown.engine.config import DEFAULT_BACKUP_SUBFOLDER
                raw = DEFAULT_BACKUP_SUBFOLDER if raw is None else str(raw)
                if "{machine" in raw and os.path.isdir(os.path.dirname(self.output_dir)):
                    if write_root_about(os.path.dirname(self.output_dir), BACKUP_ROOT_ABOUT,
                                        ROOT_ABOUT_VERSION, None, False, self.dry_run, self.logger):
                        actions.append("wrote root about")
        if self.target.get("snapshots", False):
            actions += self.maybe_snapshot(force=force_snapshot, ignore_quiet=ignore_quiet)
        return actions

    def _write_readme(self):
        path = os.path.join(self.output_dir, README_NAME)
        text = f"""# Stickies backup - how to restore

This folder is a **verbatim backup of Apple Stickies** on the Mac
"{self.config.machine_label()}" (machine id {self.config.machine_id()}, the
folder name - it stays the same if the Mac is renamed), maintained by
Stickies-to-Markdown. Each
`<UUID>.rtfd` is one note exactly as Stickies stores it (text plus
attachments); `.SavedStickiesState` holds colors and window positions.
Notes deleted in Stickies are kept under `_deleted/<date-time>/` for
{int(self.target.get('keep_deleted_days', 30) or 0) or 'ever'} days.

Restore (on the Mac the notes belong to):

    stickies2md --restore-from "<this folder>" --yes

which refuses while Stickies is running, zips the current notes first as a
safety net, then copies these back. Or by hand: quit Stickies, replace the
contents of

    ~/Library/Containers/com.apple.Stickies/Data/Library/Stickies/

with the `.rtfd` packages and `.SavedStickiesState` from here, and relaunch.

The `.noindex` folder name keeps Spotlight out of these files. This note is
maintained by the tool; edits are overwritten.
"""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                if handle.read() == text:
                    return False
        except FileNotFoundError:
            pass
        if not self.dry_run:
            os.makedirs(self.output_dir, exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix=".s2m.", dir=self.output_dir)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(temp, path)
        return True

    def snapshot_source(self):
        return self.output_dir if self.target.get("replica", True) else self.config.stickies_dir()

    def existing_snapshots(self):
        """[(stamp_time, path)] of OUR zips in snapshot_dir, oldest first."""
        folder = self.target.snapshot_dir()
        found = []
        try:
            names = os.listdir(folder)
        except OSError:
            return found
        for name in names:
            match = _SNAPSHOT_RE.match(name)
            if not match or match.group("machine") != self.config.machine_id():
                continue                            # another Mac's series in a shared folder
            path = os.path.join(folder, name)
            try:
                with zipfile.ZipFile(path) as archive:
                    comment = archive.comment.decode("utf-8", errors="replace")
                if not comment.startswith(SNAPSHOT_MARKER):
                    continue
                when = time.mktime(time.strptime(match.group("stamp"), _STAMP_FMT))
            except (OSError, zipfile.BadZipFile, ValueError):
                continue
            found.append((when, path, comment))
        return sorted(found)

    def maybe_snapshot(self, force=False, ignore_quiet=False):
        folder = self.target.snapshot_dir()
        if not os.path.isdir(folder):
            self.logger.error(f"Output '{self.name}': snapshot folder unavailable: '{folder}'")
            self.events.put(Event("error", folder, "snapshot folder unavailable"))
            return []
        source = self.snapshot_source()
        if not os.path.isdir(source):
            return []
        existing = self.existing_snapshots()
        if not force:
            every = float(self.target.get("snapshot_every_days", 30) or 0) * 86400
            if existing and time.time() - existing[-1][0] < every:
                return []
            quiet = float(self.target.get("snapshot_quiet_seconds", 300) or 0)
            if not ignore_quiet and self._last_change and time.time() - self._last_change < quiet:
                return []
        digest = corpus_hash(source)
        if existing and not force:
            last = existing[-1][2]
            if f'"corpus_hash": "{digest}"' in last:
                return []
        if self.dry_run:
            self.logger.info(f"Output '{self.name}': would write snapshot")
            return ["would write snapshot"]
        path = self._write_snapshot(source, folder, digest)
        self.logger.info(f"Output '{self.name}': wrote snapshot '{os.path.basename(path)}'")
        self.events.put(Event("converted", path, f"snapshot {self.name}"))
        return ["wrote snapshot"] + self._prune_snapshots()

    def _write_snapshot(self, source, folder, digest):
        # Stable id in the file name (label in the zip comment): renaming the
        # Mac must not orphan its own snapshot series for pruning purposes.
        name = f"{SNAPSHOT_PREFIX}{self.config.machine_id()}_{_stamp()}.zip"
        path = os.path.join(folder, name)
        while os.path.exists(path):               # never overwrite: wait for the next second
            time.sleep(0.2)
            name = f"{SNAPSHOT_PREFIX}{self.config.machine_id()}_{_stamp()}.zip"
            path = os.path.join(folder, name)
        comment = json.dumps({"marker": SNAPSHOT_MARKER, "machine": self.config.machine_label(),
                              "machine_id": self.config.machine_id(), "corpus_hash": digest,
                              "written": _stamp()})
        fd, temp = tempfile.mkstemp(prefix=".s2m.", suffix=".zip", dir=folder)
        os.close(fd)
        with zipfile.ZipFile(temp, "w") as archive:
            archive.comment = (SNAPSHOT_MARKER + " " + comment).encode("utf-8")
            for dirpath, dirnames, filenames in os.walk(source):
                dirnames.sort()
                rel_dir = os.path.relpath(dirpath, source)
                if rel_dir == DELETED_DIR or rel_dir.startswith(DELETED_DIR + os.sep):
                    continue
                for fname in sorted(filenames):
                    if fname == README_NAME or fname == ".DS_Store" or fname.startswith(".s2m."):
                        continue
                    full = os.path.join(dirpath, fname)
                    rel = fname if rel_dir == "." else os.path.join(rel_dir, fname)
                    method = (zipfile.ZIP_STORED if os.path.splitext(fname)[1].lower() in _STORED_SUFFIXES
                              else zipfile.ZIP_DEFLATED)
                    archive.write(full, rel, compress_type=method)
        os.replace(temp, path)
        return path

    def _prune_snapshots(self):
        keep = int(self.target.get("keep_snapshots", 0) or 0)
        if keep <= 0:
            return []
        existing = self.existing_snapshots()
        actions = []
        for _when, path, _comment in existing[:-keep] if len(existing) > keep else []:
            os.remove(path)
            self.logger.info(f"Output '{self.name}': pruned snapshot '{os.path.basename(path)}'")
            actions.append("pruned snapshot")
        return actions


# --- restore ------------------------------------------------------------------

def stickies_running():
    try:
        return subprocess.run(["pgrep", "-x", "Stickies"], capture_output=True).returncode == 0
    except OSError:
        return False


def restore_container(source, stickies_dir, safety_dir, logger=None, dry_run=False):
    """
    Replace the container's packages and state file with those in `source`
    (a replica folder or a snapshot zip). Refuses if Stickies is running.
    Writes a safety zip of the current container into safety_dir first.
    Returns (restored_package_count, safety_zip_path).
    """
    logger = logger or get_logger()
    if stickies_running():
        raise RuntimeError("Stickies is running - quit it first")
    if not os.path.isdir(stickies_dir):
        raise RuntimeError(f"container not found: '{stickies_dir}'")

    staging = None
    if os.path.isfile(source) and zipfile.is_zipfile(source):
        staging = tempfile.mkdtemp(prefix="s2m-restore-")
        with zipfile.ZipFile(source) as archive:
            archive.extractall(staging)
        source_dir = staging
    elif os.path.isdir(source):
        source_dir = source
    else:
        raise RuntimeError(f"not a backup folder or zip: '{source}'")

    packages = [n for n in os.listdir(source_dir) if _UUID_DIR_RE.match(n)]
    if not packages:
        raise RuntimeError(f"no note packages found in '{source}'")

    safety = None
    if not dry_run:
        os.makedirs(safety_dir, exist_ok=True)
        safety = os.path.join(safety_dir, f"Stickies_pre-restore_{_stamp()}.zip")
        with zipfile.ZipFile(safety, "w", zipfile.ZIP_DEFLATED) as archive:
            for dirpath, _d, files in os.walk(stickies_dir):
                for fname in files:
                    full = os.path.join(dirpath, fname)
                    archive.write(full, os.path.relpath(full, stickies_dir))
        for name in os.listdir(stickies_dir):
            path = os.path.join(stickies_dir, name)
            if _UUID_DIR_RE.match(name):
                shutil.rmtree(path)
            elif name == STATE_FILENAME:
                os.remove(path)
        for name in packages:
            shutil.copytree(os.path.join(source_dir, name), os.path.join(stickies_dir, name))
        state = os.path.join(source_dir, STATE_FILENAME)
        if os.path.isfile(state):
            shutil.copy2(state, os.path.join(stickies_dir, STATE_FILENAME))
    if staging:
        shutil.rmtree(staging, ignore_errors=True)
    logger.info(f"Restored {len(packages)} packages into '{stickies_dir}' from '{source}'"
                + (f"; safety copy '{safety}'" if safety else " (dry run)"))
    return len(packages), safety


# End of file #
