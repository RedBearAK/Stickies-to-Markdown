"""
Read-only view of Apple Stickies' storage (handoff §4, §5.1).

Post-Catalina layout, to be verified on the target Mac (checklist §7):

    <stickies_dir>/
        <UUID>.rtfd/TXT.rtf [+ attachments]     one package per note
        .SavedStickiesState                     plist: colour/order/geometry

Nothing in this module ever writes inside the container. The state file is
read defensively: reports exist of Stickies truncating it mid-write, so a
missing or unparseable state file must never block exporting the .rtfd
contents - colour just falls back to "unknown".

The real key names inside .SavedStickiesState are UNVERIFIED (checklist §7
step 3). _color_from_entry() therefore probes several plausible spellings
and value shapes; record the real ones in dev_notes once observed, then
tighten this.
"""

import os
import re
import plistlib

from stickies_to_markdown.engine.logsetup import get_logger


STATE_FILENAME = ".SavedStickiesState"
RTF_NAME = "TXT.rtf"

_UUID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")

# Classic Stickies palette. Integer mappings are a guess pending checklist
# §7 step 3; string values pass through lowercased.
_COLOR_NAMES = ("yellow", "blue", "green", "pink", "purple", "gray")
_COLOR_KEY_CANDIDATES = ("color", "Color", "colour", "noteColor", "StickyColor")
_UUID_KEY_CANDIDATES = ("uuid", "UUID", "noteUUID", "identifier", "ID")


class Note:
    """One sticky: identity, package path, and state-file metadata."""

    __slots__ = ("uuid", "rtfd_path", "color", "order")

    def __init__(self, uuid, rtfd_path, color="unknown", order=None):
        self.uuid = uuid
        self.rtfd_path = rtfd_path
        self.color = color
        self.order = order

    @property
    def uuid8(self):
        return self.uuid.replace("-", "")[:8].lower()

    @property
    def rtf_path(self):
        return os.path.join(self.rtfd_path, RTF_NAME)

    def __repr__(self):
        return f"Note({self.uuid8}, color={self.color!r})"


def container_readable(stickies_dir):
    """
    (readable: bool, reason: str). The §3.4 health probe: a denied TCC
    grant fails here as PermissionError with no other symptom, ever.
    """
    try:
        os.listdir(stickies_dir)
        return True, ""
    except PermissionError:
        return False, ("permission denied - grant Full Disk Access to the "
                       "app or terminal running this tool")
    except FileNotFoundError:
        return False, f"not found: {stickies_dir}"
    except OSError as error:
        return False, str(error)


def enumerate_notes(stickies_dir, logger=None):
    """
    uuid -> Note for every <UUID>.rtfd in the container, enriched with
    colour/order from the state file when available. Raises OSError only
    for the top-level listing; per-note problems are logged and skipped.
    """
    logger = logger or get_logger()
    notes = {}
    for name in sorted(os.listdir(stickies_dir)):
        stem, ext = os.path.splitext(name)
        if ext.lower() != ".rtfd" or not _UUID_RE.match(stem):
            continue
        rtfd_path = os.path.join(stickies_dir, name)
        if not os.path.isdir(rtfd_path):
            continue
        if not os.path.isfile(os.path.join(rtfd_path, RTF_NAME)):
            logger.warning(f"Package without {RTF_NAME}, skipped: {name}")
            continue
        uuid = stem.upper()
        notes[uuid] = Note(uuid, rtfd_path)

    state = read_state(stickies_dir, logger=logger)
    for uuid, note in notes.items():
        meta = state.get(uuid)
        if meta:
            note.color = meta.get("color", "unknown")
            note.order = meta.get("order")
    return notes


# --- .SavedStickiesState ---------------------------------------------------

def read_state(stickies_dir, logger=None):
    """
    uuid -> {"color": str, "order": int|None}, parsed defensively from
    .SavedStickiesState. Any failure returns {} (colour falls back to
    "unknown"); it must never block an export.
    """
    logger = logger or get_logger()
    path = os.path.join(stickies_dir, STATE_FILENAME)
    try:
        with open(path, 'rb') as handle:
            data = plistlib.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, plistlib.InvalidFileException, ValueError) as error:
        logger.warning(f"State file unreadable ({error}); colours unknown")
        return {}

    entries = _find_note_entries(data)
    state = {}
    for order, entry in enumerate(entries):
        uuid = _uuid_from_entry(entry)
        if not uuid:
            continue
        state[uuid.upper()] = {
            "color": _color_from_entry(entry),
            "order": order,
        }
    return state


def _find_note_entries(data):
    """
    The list of per-note dicts, wherever the plist hides it: the top level
    may be that list, or a dict containing it under an unknown key.
    """
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict):
        best = []
        for value in data.values():
            if isinstance(value, list):
                dicts = [e for e in value if isinstance(e, dict)]
                if len(dicts) > len(best):
                    best = dicts
        return best
    return []


def _uuid_from_entry(entry):
    for key in _UUID_KEY_CANDIDATES:
        value = entry.get(key)
        if isinstance(value, str) and _UUID_RE.match(value):
            return value
    # Fall back to any UUID-shaped string value under any key.
    for value in entry.values():
        if isinstance(value, str) and _UUID_RE.match(value):
            return value
    return None


def _color_from_entry(entry):
    for key in _COLOR_KEY_CANDIDATES:
        if key not in entry:
            continue
        value = entry[key]
        if isinstance(value, str) and value.strip():
            name = value.strip().lower()
            return name if name in _COLOR_NAMES else name
        if isinstance(value, int) and 0 <= value < len(_COLOR_NAMES):
            return _COLOR_NAMES[value]
    return "unknown"


# End of file #
