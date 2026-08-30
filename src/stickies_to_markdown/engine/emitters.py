"""
Flavor profiles: pure functions that ADD front-matter keys for a specific
consumer of the output folder. They never transform the body and never do
I/O, so composition is a dict merge and a duplicate key is a config error,
never a silent last-write-wins.

Generic keys (marker, uuid, colour, hash, timestamps) come from the writer
and belong to every flavor. Consumer-specific vocabulary lives here:
"cssclasses" means something to Obsidian and nothing to anything else, so
it is only emitted when the obsidian flavor is configured.
"""


class EmitterError(Exception):
    """Unknown flavor, or two emitters claimed the same key."""


def _obsidian_cssclasses(note):
    """Obsidian: style hooks for a CSS snippet (background tint, banner)."""
    classes = ["stickies-mirror"]
    if note.color and note.color != "unknown":
        classes.append(f"sticky-{note.color}")
    return {"cssclasses": classes}


FLAVORS = {
    "generic": [],
    "obsidian": [_obsidian_cssclasses],
}


DELETED_FLAVORS = {
    "generic": {},
    # Obsidian: a class to style orphaned notes differently (greyed banner).
    "obsidian": {"cssclasses": ["stickies-deleted"]},
}


def deleted_keys(flavor):
    """Extra keys to merge into a mirror file whose note was deleted.
    List values are appended to any existing list under that key."""
    try:
        return dict(DELETED_FLAVORS[flavor])
    except KeyError:
        raise EmitterError(f"Unknown flavor: {flavor!r}") from None


def flavor_keys(flavor, note):
    """Merged extra keys for `flavor`, raising on collisions."""
    try:
        emitters = FLAVORS[flavor]
    except KeyError:
        raise EmitterError(f"Unknown flavor: {flavor!r} "
                           f"(choices: {', '.join(sorted(FLAVORS))})") from None
    merged = {}
    for emitter in emitters:
        for key, value in emitter(note).items():
            if key in merged:
                raise EmitterError(
                    f"Flavor {flavor!r}: key {key!r} emitted twice "
                    f"(by {emitter.__name__} and an earlier emitter)")
            merged[key] = value
    return merged


# End of file #
