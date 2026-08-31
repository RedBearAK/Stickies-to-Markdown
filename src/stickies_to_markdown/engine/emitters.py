"""
Flavor profiles: pure functions that ADD front-matter keys for a specific
consumer of an output folder. They never transform the body and never do
I/O, so composition is a dict merge and a duplicate key is a config error,
never a silent last-write-wins.

Generic keys (marker, uuid, colour, hash, timestamps) come from the writer
and belong to every flavor. Consumer-specific vocabulary lives here. An
output may name several flavors ("obsidian, sticky-notes"); their keys are
merged.

Plugin vocabularies below were read from each plugin's source
(2026-08-31), not guessed:

  floating-sticky-notes  (kasairo)      reads `color:` - our generic key and
                                        vocabulary already; the flavor exists
                                        so the intent is recorded, it adds nothing
  sticky-notes           (abdo-reda;    reads `background_color:` with capitalised
                          simple-sticky-notes is a fork)   Base/Yellow/Green/Blue/Purple/Pink
  colorful-stickynotes   (pandanocturne) reads `colorful-sticky-bg:` with
                                        yellow/mint/blue/lavender/pink/gray/default
  obsidian                              `cssclasses` for a CSS snippet (no plugin)

Desktop Sticky Notes (y-usuzumi) keeps colours in its own settings by file
path, not in front matter, so no flavor can reach it.
"""


class EmitterError(Exception):
    """Unknown flavor, or two emitters claimed the same key."""


def _obsidian_cssclasses(note):
    """Obsidian: style hooks for a CSS snippet (background tint, banner)."""
    classes = ["stickies-mirror"]
    if note.color and note.color != "unknown":
        classes.append(f"sticky-{note.color}")
    return {"cssclasses": classes}


_STICKY_NOTES_COLORS = {          # abdo-reda / rephila: Colors enum, capitalised
    "yellow": "Yellow", "green": "Green", "blue": "Blue",
    "purple": "Purple", "pink": "Pink", "gray": "Base",
}


def _sticky_notes_background(note):
    return {"background_color": _STICKY_NOTES_COLORS.get(note.color, "Base")}


_COLORFUL_COLORS = {              # pandanocturne: bg ids
    "yellow": "yellow", "green": "mint", "blue": "blue",
    "purple": "lavender", "pink": "pink", "gray": "gray",
}


def _colorful_stickynotes_bg(note):
    return {"colorful-sticky-bg": _COLORFUL_COLORS.get(note.color, "default")}


FLAVORS = {
    "generic": [],
    "obsidian": [_obsidian_cssclasses],
    "floating-sticky-notes": [],                     # generic `color` suffices
    "sticky-notes": [_sticky_notes_background],
    "colorful-stickynotes": [_colorful_stickynotes_bg],
}

DELETED_FLAVORS = {
    "generic": {},
    "obsidian": {"cssclasses": ["stickies-deleted"]},
    "floating-sticky-notes": {},
    "sticky-notes": {},
    "colorful-stickynotes": {},
}


def parse_flavors(value):
    """'obsidian, sticky-notes' or ['obsidian', ...] -> ordered unique list."""
    if isinstance(value, (list, tuple)):
        names = [str(v).strip() for v in value]
    else:
        names = [v.strip() for v in str(value or "").split(",")]
    seen = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    for name in seen:
        if name not in FLAVORS:
            raise EmitterError(f"Unknown flavor: {name!r} "
                               f"(choices: {', '.join(sorted(FLAVORS))})")
    return seen or ["generic"]


def deleted_keys(flavor):
    """Extra keys to merge into a mirror file whose note was deleted.
    List values are appended to any existing list under that key."""
    merged = {}
    for name in parse_flavors(flavor):
        for key, value in DELETED_FLAVORS[name].items():
            merged.setdefault(key, [])
            merged[key] = list(merged[key]) + [v for v in value if v not in merged[key]]
    return merged


def flavor_keys(flavor, note):
    """Merged extra keys for one or more flavors, raising on collisions."""
    merged = {}
    for name in parse_flavors(flavor):
        for emitter in FLAVORS[name]:
            for key, value in emitter(note).items():
                if key in merged:
                    raise EmitterError(
                        f"Flavors {flavor!r}: key {key!r} emitted twice "
                        f"(by {emitter.__name__} and an earlier emitter)")
                merged[key] = value
    return merged


# End of file #
