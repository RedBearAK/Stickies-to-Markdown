"""
Read-only view of Apple Stickies' storage (handoff §4, §5.1).

Verified on macOS 2026-08-30 (dev_notes/MAC_FINDINGS.md):

    <stickies_dir>/
        <UUID>.rtfd/TXT.rtf [+ attachments]     one package per note
        .SavedStickiesState                     binary plist, top level is a
                                                LIST of per-note dicts

Per-note dict keys seen: UUID, StickyColor / ControlColor / HighlightColor /
SpineColor (each {Red, Green, Blue, Alpha} floats 0..1), Frame, ExpandedSize,
ExpandFrameY, Floating, Translucent, ZOrder, SpellCheckingTypes.

The colour is a float RGB, not an enum, so it is classified by hue into the
classic palette names. The bands were calibrated on one real note (yellow);
dev_notes/mac_verify.py step 6 prints every note's hue/saturation and the
guessed name so the other five can be confirmed and the bands adjusted.

Nothing in this module ever writes inside the container. The state file is
read defensively: a missing or unparseable one must never block exporting
the .rtfd contents - colour just falls back to "unknown".
"""

import os
import re
import colorsys
import plistlib

from stickies_to_markdown.engine.logsetup import get_logger


STATE_FILENAME = ".SavedStickiesState"
RTF_NAME = "TXT.rtf"

_UUID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")

COLOR_NAMES = ("yellow", "blue", "green", "pink", "purple", "gray")

# Hue bands in degrees (colorsys hue * 360). Below GRAY_SATURATION the
# colour is grey regardless of hue. Verified: yellow at ~54 degrees. The
# others are hue-theory placements pending step-6 calibration.
GRAY_SATURATION = 0.12
_HUE_BANDS = (
    (20, 75, "yellow"),
    (75, 170, "green"),
    (170, 260, "blue"),
    (260, 305, "purple"),
    (305, 360, "pink"),
    (0, 20, "pink"),
)


class Note:
    """One sticky: identity, package path, and state-file metadata."""

    __slots__ = ("uuid", "rtfd_path", "color", "color_hex", "order",
                 "floating", "translucent")

    def __init__(self, uuid, rtfd_path, color="unknown", color_hex="",
                 order=None, floating=False, translucent=False):
        self.uuid = uuid
        self.rtfd_path = rtfd_path
        self.color = color
        self.color_hex = color_hex
        self.order = order
        self.floating = floating
        self.translucent = translucent

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
        return False, ("permission denied - the app or terminal running this "
                       "tool needs access to Stickies' data (System Settings "
                       "> Privacy & Security); a dismissed prompt is a "
                       "permanent deny until 'tccutil reset'")
    except FileNotFoundError:
        return False, f"not found: {stickies_dir}"
    except OSError as error:
        return False, str(error)


def enumerate_notes(stickies_dir, logger=None):
    """
    uuid -> Note for every <UUID>.rtfd in the container, enriched from the
    state file when available. Raises OSError only for the top-level
    listing; per-note problems are logged and skipped.
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
            note.color = meta["color"]
            note.color_hex = meta["color_hex"]
            note.order = meta["order"]
            note.floating = meta["floating"]
            note.translucent = meta["translucent"]
    return notes


# --- colour ----------------------------------------------------------------

def classify_color(red, green, blue):
    """(name, hex) for float RGB 0..1, by saturation then hue band."""
    hue, saturation, _value = colorsys.rgb_to_hsv(red, green, blue)
    hex_code = "#{:02x}{:02x}{:02x}".format(
        round(red * 255), round(green * 255), round(blue * 255))
    if saturation < GRAY_SATURATION:
        return "gray", hex_code
    degrees = hue * 360.0
    for low, high, name in _HUE_BANDS:
        if low <= degrees < high:
            return name, hex_code
    return "unknown", hex_code


def color_from_entry(entry):
    """(name, hex) from a per-note state dict; ('unknown', '') if absent."""
    value = entry.get("StickyColor")
    if isinstance(value, dict):
        try:
            return classify_color(float(value.get("Red", 0)),
                                  float(value.get("Green", 0)),
                                  float(value.get("Blue", 0)))
        except (TypeError, ValueError):
            pass
    return "unknown", ""


# --- .SavedStickiesState ---------------------------------------------------

def read_state(stickies_dir, logger=None):
    """
    uuid -> {color, color_hex, order, floating, translucent}, parsed
    defensively. Any failure returns {} - it must never block an export.
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

    entries = _note_entries(data)
    state = {}
    for index, entry in enumerate(entries):
        uuid = entry.get("UUID")
        if not (isinstance(uuid, str) and _UUID_RE.match(uuid)):
            continue
        name, hex_code = color_from_entry(entry)
        order = entry.get("ZOrder")
        state[uuid.upper()] = {
            "color": name,
            "color_hex": hex_code,
            "order": order if isinstance(order, int) else index,
            "floating": bool(entry.get("Floating", False)),
            "translucent": bool(entry.get("Translucent", False)),
        }
    return state


def _note_entries(data):
    """The per-note dicts: the top level IS the list (verified); tolerate
    a dict wrapper in case a future macOS adds one."""
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


# End of file #
