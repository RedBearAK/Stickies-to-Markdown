"""
Idempotent output into the mirror folder (handoff §5.2).

    <output_dir>/
        <slug-of-first-line>--<uuid8>.md    (or <uuid8>.md, per filename_style)
        attachments/<uuid8>/<original name>
        _deleted/<same name>.md             on_delete = "archive" (dir configurable)
        _conflicts/<name>.<stamp>.md        mirror file was edited externally

Rules enforced here, all from the handoff and the DFP quarantine ethos:

- Write only when the rendered output differs from what is on disk
  (byte compare, ignoring only the synced-at line). Sync clients and
  editors otherwise churn on every autosave.
- Same-directory temp file + os.replace(). With read_only_output the
  mirror files stay chmod 444 permanently: replacing a directory entry
  needs write permission on the DIRECTORY, not the file.
- Never touch a file without this tool's front-matter marker.
- An externally edited mirror file (body hash no longer matches its
  recorded content-hash) is quarantined to _conflicts/, never clobbered.
- A note deleted in Stickies is handled per on_delete: "archive" (annotate
  + move to deleted_dir), "mark" (annotate in place), "delete", or "keep"
  (untouched orphan). The annotation is a `deleted-from-stickies` timestamp
  so a consumer can tell an orphan from a live note. A retitled note's old
  filename is always removed - the content lives on under the new name.
"""

import os
import re
import time
import shutil
import hashlib
import tempfile

from stickies_to_markdown.engine.events import Event
from stickies_to_markdown.engine.emitters import flavor_keys, deleted_keys
from stickies_to_markdown.engine.convert import first_content_line
from stickies_to_markdown.engine.logsetup import get_logger


MARKER_KEY = "synced-by"
MARKER_VALUE = "stickies-to-markdown"
DELETED_KEY = "deleted-from-stickies"
ATTACHMENTS_DIR = "attachments"
CONFLICTS_DIR = "_conflicts"

_ATTACH_MARK = re.compile(r"@@ATTACHMENT:([^@]+)@@")
_SLUG_MAX = 40


# --- front matter (hand-rolled subset: flat scalars + inline lists) --------

def _yaml_scalar(value):
    # Quote only what YAML actually needs quoted: ": " and " #" sequences,
    # flow/quote/newline characters, leading indicator chars, edge spaces.
    # A bare "sha256:abc" or ISO timestamp stays unquoted (and Obsidian
    # then types date-like properties as dates).
    text = str(value)
    if (text == "" or re.search(r':\s|\s#|[\[\]{}"\n]|^\s|\s$', text)
            or text[0] in "'&*?|>%@`!,-"):
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return text


def _unquote(value):
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace('\\\\', '\\')
    return value

def render_front_matter(keys):
    lines = ["---"]
    for key, value in keys.items():
        if isinstance(value, (list, tuple)):
            rendered = "[" + ", ".join(_yaml_scalar(v) for v in value) + "]"
        else:
            rendered = _yaml_scalar(value)
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    return "\n".join(lines) + "\n"

def split_front_matter(text):
    """(keys_dict, body). keys are raw value strings; {} when no block."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    keys = {}
    for line in text[4:end].split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            keys[key.strip()] = _unquote(value.strip())
    return keys, text[end + 5:]


def _merge_key(lines, key, value):
    """Set `key` in a list of front-matter lines; list values append to an
    existing inline list under the same key."""
    rendered = (("[" + ", ".join(_yaml_scalar(v) for v in value) + "]")
                if isinstance(value, (list, tuple)) else _yaml_scalar(value))
    for index, line in enumerate(lines):
        if not line.startswith(key + ":"):
            continue
        existing = line[len(key) + 1:].strip()
        if isinstance(value, (list, tuple)) and existing.startswith("["):
            items = [i.strip() for i in existing[1:-1].split(",") if i.strip()]
            items += [_yaml_scalar(v) for v in value if _yaml_scalar(v) not in items]
            lines[index] = f"{key}: [{', '.join(items)}]"
        else:
            lines[index] = f"{key}: {rendered}"
        return lines
    lines.append(f"{key}: {rendered}")
    return lines


# --- naming ----------------------------------------------------------------

def slugify(first_line):
    text = first_line.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text).strip("-")
    return text[:_SLUG_MAX].rstrip("-") or "note"

def first_line_of(markdown):
    line = first_content_line(markdown)
    return line.lstrip("#").strip() or line

def filename_for(note, markdown, style):
    if style == "uuid":
        return f"{note.uuid8}.md"
    return f"{slugify(first_line_of(markdown) or 'note')}--{note.uuid8}.md"


# --- hashing ---------------------------------------------------------------

def body_hash(body_text):
    return "sha256:" + hashlib.sha256(body_text.encode("utf-8")).hexdigest()

def _file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _strip_volatile(text):
    """Drop the keys that must not, by themselves, cause a rewrite:
    synced-at (always now) and modified (TXT.rtf's mtime - Stickies replaces
    that file on every save, including a colour change or a window move,
    so it would churn every mirror file on every attribute fiddle)."""
    return re.sub(r"^(synced-at|modified):.*$", "", text, flags=re.MULTILINE)


class Writer:
    """One writer per output block. `target` carries every per-folder
    setting (an OutputTarget); `config` supplies the globals (dry run)."""

    def __init__(self, config, target, events, logger=None):
        self.config = config
        self.target = target
        self.events = events
        self.logger = logger or get_logger()
        if not target.output_dir():
            raise ValueError(f"output '{target.name}' has no output_dir")
        self._mirror_index = None       # uuid -> filename, lazy
        self.last_excluded = []

    @property
    def name(self):
        return self.target.name

    @property
    def output_dir(self):
        return self.target.output_dir()

    @property
    def dry_run(self):
        return bool(self.config.get("dry_run"))

    @property
    def read_only(self):
        return bool(self.target.get("read_only_output", True))

    def refresh_index(self):
        """Forget the cached uuid->filename map (another process may have
        written files); it is rebuilt on next use."""
        self._mirror_index = None

    # --- public ------------------------------------------------------------

    def export_note(self, note, markdown, attachments, body_format="markdown"):
        """
        Bring the mirror file (and attachments) for one note up to date.
        Returns the Event kind that describes what happened.
        """
        self._body_format = body_format
        os.makedirs(self.output_dir, exist_ok=True)
        markdown = self._resolve_attachment_links(note, markdown, attachments)
        target_name = filename_for(note, markdown, self.target.get("filename_style"))
        target_path = os.path.join(self.output_dir, target_name)

        previous_name = self._index().get(note.uuid)
        if previous_name and previous_name != target_name:
            self._remove_renamed(previous_name)

        existing_text = self._read_marked(target_path, note)
        if existing_text is None and os.path.exists(target_path):
            return "error"      # unmarked file in the way; event already sent

        conflicted = self._quarantine_if_edited(target_path, existing_text, target_name)
        if conflicted:
            existing_text = None

        rendered = self._render(note, markdown, existing_text)
        if existing_text is not None and (
                _strip_volatile(rendered) == _strip_volatile(existing_text)):
            kind = "unchanged"
        else:
            self._write_atomic(target_path, rendered)
            self._index()[note.uuid] = target_name
            kind = "converted"
            self.logger.info(f"{'DRY RUN: would write' if self.dry_run else 'Wrote'}"
                             f" {target_name}")

        if self.target.get("include_attachments", True):
            self._copy_attachments(note, attachments)

        detail = "dry run" if self.dry_run else note.color
        self.events.put(Event("conflict" if conflicted else kind,
                              target_path, detail))
        return "converted" if conflicted else kind

    def handle_deletions(self, live_uuids, excluded_uuids=()):
        """
        Mirror files whose note vanished from the container (on_delete) or
        became excluded (on_exclude). Returns the affected filenames;
        self.last_excluded holds the subset that were exclusions.
        """
        affected = []
        self.last_excluded = []
        for uuid, name in list(self._index().items()):
            if uuid in live_uuids:
                continue
            excluded = uuid in excluded_uuids
            policy = self.target.on_exclude() if excluded else self.target.on_delete()
            self._dispose(name, uuid, policy,
                          reason="excluded" if excluded else "note deleted")
            del self._index()[uuid]
            affected.append(name)
            if excluded:
                self.last_excluded.append(name)
            self.events.put(Event("excluded" if excluded else "deleted",
                                  os.path.join(self.output_dir, name),
                                  "dry run" if self.dry_run else policy))
        return affected

    # --- internals ---------------------------------------------------------

    def _render(self, note, markdown, existing_text):
        keys = {}
        if self.target.get("front_matter", True):
            rtf_stat = self._stat(note.rtf_path)
            # created: sticky once recorded. TXT.rtf is replaced on every
            # save (new birth time), so the package DIRECTORY's birth time
            # is the honest source the first time, and the existing mirror
            # file's value thereafter.
            existing_keys = split_front_matter(existing_text)[0] if existing_text else {}
            dir_stat = self._stat(note.rtfd_path)
            created = existing_keys.get("created") or self._iso(
                getattr(dir_stat, "st_birthtime", None) or (dir_stat and dir_stat.st_mtime))
            keys = {
                MARKER_KEY: MARKER_VALUE,
                "stickies-uuid": note.uuid,
                "color": note.color,
                "color-hex": note.color_hex or "",
                "created": created,
                "modified": self._iso(rtf_stat and rtf_stat.st_mtime),
                "source": note.rtfd_path,
                "body-format": getattr(self, "_body_format", "markdown"),
                "content-hash": body_hash(markdown),
                "synced-at": self._iso(time.time()),
            }
            keys.update(flavor_keys(self.target.get("flavor", "generic"), note))
            return render_front_matter(keys) + "\n" + markdown
        return markdown

    def _read_marked(self, path, note):
        """File text when it is ours; None when absent or not ours."""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
        except FileNotFoundError:
            return None
        except OSError as error:
            self.events.put(Event("error", path, f"unreadable: {error}"))
            return None
        keys, _body = split_front_matter(text)
        if keys.get(MARKER_KEY) != MARKER_VALUE:
            message = "exists but was not written by this tool; skipped"
            self.logger.error(f"{path}: {message}")
            self.events.put(Event("error", path, message))
            return None
        return text

    def _quarantine_if_edited(self, path, existing_text, name):
        """True when the file was externally edited and has been moved aside."""
        if existing_text is None:
            return False
        keys, body = split_front_matter(existing_text)
        recorded = keys.get("content-hash", "")
        if not recorded or recorded == body_hash(body.lstrip("\n")):
            return False
        stamp = time.strftime("%Y%m%d-%H%M%S")
        conflict_dir = os.path.join(self.output_dir, CONFLICTS_DIR)
        conflict_path = os.path.join(conflict_dir, f"{name[:-3]}.{stamp}.md")
        self.logger.warning(f"Externally edited mirror file quarantined: "
                            f"{name} -> {CONFLICTS_DIR}/")
        if not self.dry_run:
            os.makedirs(conflict_dir, exist_ok=True)
            shutil.move(path, conflict_path)
        return True

    def _remove_renamed(self, name):
        """A retitled note: the stale filename goes; content lives on."""
        path = os.path.join(self.output_dir, name)
        self.logger.info(f"renamed: removing stale {name}"
                         f"{' (dry run)' if self.dry_run else ''}")
        if not self.dry_run and os.path.exists(path):
            os.remove(path)

    def _dispose(self, name, uuid, policy, reason):
        """Apply a deletion policy to a mirror file and its attachments."""
        path = os.path.join(self.output_dir, name)
        uuid8 = uuid.replace("-", "")[:8].lower()
        attachments = os.path.join(self.output_dir, ATTACHMENTS_DIR, uuid8)
        self.logger.info(f"{reason}: {name} -> {policy}"
                         f"{' (dry run)' if self.dry_run else ''}")
        if self.dry_run or not os.path.exists(path) or policy == "keep":
            return
        if policy == "delete":
            os.remove(path)
            shutil.rmtree(attachments, ignore_errors=True)
            return
        # "mark" and "archive" both annotate first.
        self._annotate_deleted(path)
        if policy == "archive":
            deleted_dir = self.target.deleted_dir()
            os.makedirs(deleted_dir, exist_ok=True)
            target = os.path.join(deleted_dir, name)
            if os.path.exists(target):       # earlier archive of the same name
                stamp = time.strftime("%Y%m%d-%H%M%S")
                target = os.path.join(deleted_dir, f"{name[:-3]}.{stamp}.md")
            shutil.move(path, target)
            if os.path.isdir(attachments):
                # Keep the file's relative links valid inside deleted_dir.
                archived = os.path.join(deleted_dir, ATTACHMENTS_DIR, uuid8)
                if os.path.exists(archived):
                    shutil.rmtree(archived)
                os.makedirs(os.path.dirname(archived), exist_ok=True)
                shutil.move(attachments, archived)

    def _annotate_deleted(self, path):
        """
        Add `deleted-from-stickies: <now>` (plus any flavor keys) to the
        front matter, editing lines in place so everything else is byte-
        preserved. Idempotent: an already-annotated file is left alone.
        Returns True when the file was rewritten.
        """
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        if not text.startswith("---\n"):
            self.logger.warning(f"{os.path.basename(path)}: no front matter, "
                                f"cannot annotate deletion")
            return False
        end = text.find("\n---\n", 4)
        if end < 0:
            return False
        lines = text[4:end].split("\n")
        if any(line.startswith(DELETED_KEY + ":") for line in lines):
            return False
        lines.append(f"{DELETED_KEY}: {self._iso(time.time())}")
        for key, value in deleted_keys(self.target.get("flavor", "generic")).items():
            lines = _merge_key(lines, key, value)
        self._write_atomic(path, "---\n" + "\n".join(lines) + text[end:])
        return True

    def _write_atomic(self, path, text):
        if self.dry_run:
            return
        directory = os.path.dirname(path)
        fd, temp_path = tempfile.mkstemp(prefix=".s2m.", suffix=".md.tmp",
                                         dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            if self.read_only:
                os.chmod(temp_path, 0o444)
            os.replace(temp_path, path)
        except BaseException:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    def _resolve_attachment_links(self, note, markdown, attachments):
        """Inline @@ATTACHMENT@@ marks become links; unmentioned files append."""
        base = f"{ATTACHMENTS_DIR}/{note.uuid8}"
        mentioned = set()

        def replace(match):
            name = match.group(1)
            mentioned.add(name)
            return f"![{name}]({base}/{name})"

        markdown = _ATTACH_MARK.sub(replace, markdown)
        extras = [a for a in attachments if a not in mentioned]
        if extras and self.target.get("include_attachments", True):
            links = "\n".join(f"![{name}]({base}/{name})" for name in extras)
            markdown = markdown.rstrip("\n") + "\n\n" + links + "\n"
        return markdown

    def _copy_attachments(self, note, attachments):
        if not attachments:
            return
        dest_dir = os.path.join(self.output_dir, ATTACHMENTS_DIR, note.uuid8)
        for name in attachments:
            source = os.path.join(note.rtfd_path, name)
            dest = os.path.join(dest_dir, name)
            try:
                if os.path.exists(dest) and _file_hash(dest) == _file_hash(source):
                    continue
                if not self.dry_run:
                    os.makedirs(dest_dir, exist_ok=True)
                    fd, temp_path = tempfile.mkstemp(prefix=".s2m.", dir=dest_dir)
                    os.close(fd)
                    shutil.copyfile(source, temp_path)
                    os.replace(temp_path, dest)
                self.logger.info(f"Attachment {'would copy' if self.dry_run else 'copied'}:"
                                 f" {note.uuid8}/{name}")
            except OSError as error:
                self.events.put(Event("error", source, f"attachment: {error}"))

    def _index(self):
        """uuid -> mirror filename, built by scanning our own files once."""
        if self._mirror_index is not None:
            return self._mirror_index
        index = {}
        try:
            names = sorted(os.listdir(self.output_dir))
        except OSError:
            names = []
        for name in names:
            if not name.endswith(".md"):
                continue
            path = os.path.join(self.output_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    head = handle.read(4096)
            except OSError:
                continue
            keys, _ = split_front_matter(head)
            if keys.get(MARKER_KEY) == MARKER_VALUE and keys.get("stickies-uuid"):
                index[keys["stickies-uuid"].strip('"')] = name
        self._mirror_index = index
        return index

    @staticmethod
    def _stat(path):
        try:
            return os.stat(path)
        except OSError:
            return None

    @staticmethod
    def _iso(timestamp):
        if not timestamp:
            return ""
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(timestamp))


def purge_mirror(folder, dry_run=True):
    """
    Remove everything this tool wrote into `folder` and nothing else:
    .md files carrying the marker, their attachments/<uuid8>/ folders,
    and the tool's own _deleted/_conflicts subfolders (only files with
    the marker inside those, then the folder if it is empty). Returns
    (removed_paths, kept_count). Files are chmod 444 - the directory
    permission is what removal needs.
    """
    folder = os.path.expanduser(folder)
    removed = []
    kept = 0
    uuids = set()

    def marked(path):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                keys, _ = split_front_matter(handle.read(4096))
            return keys.get(MARKER_KEY) == MARKER_VALUE, keys.get("stickies-uuid", "")
        except OSError:
            return False, ""

    def sweep(directory):
        nonlocal kept
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            return
        for name in names:
            path = os.path.join(directory, name)
            if os.path.isfile(path) and name.endswith(".md"):
                ours, uuid = marked(path)
                if ours:
                    uuids.add(uuid.replace("-", "")[:8].lower())
                    removed.append(path)
                    if not dry_run:
                        os.remove(path)
                    continue
            if os.path.isfile(path):
                kept += 1

    sweep(folder)
    for sub in (DELETED_DIR_NAMES):
        sweep(os.path.join(folder, sub))
    for base in [folder] + [os.path.join(folder, sub) for sub in DELETED_DIR_NAMES]:
        attachments = os.path.join(base, ATTACHMENTS_DIR)
        if os.path.isdir(attachments):
            for uuid8 in sorted(os.listdir(attachments)):
                path = os.path.join(attachments, uuid8)
                if uuid8 in uuids and os.path.isdir(path):
                    removed.append(path)
                    if not dry_run:
                        shutil.rmtree(path)
            if not dry_run:
                try:
                    os.rmdir(attachments)
                except OSError:
                    pass
    if not dry_run:
        for sub in DELETED_DIR_NAMES:
            try:
                os.rmdir(os.path.join(folder, sub))
            except OSError:
                pass
    return removed, kept


DELETED_DIR_NAMES = ("_deleted", "_conflicts")


# End of file #
