#!/usr/bin/env python3
"""
(Re)generate the synthetic fixtures. Run from anywhere:

    python3 tests/fixtures/make_fixtures.py

These are structurally valid RTFD packages with Apple-flavoured RTF, but
they are NOT byte-real Stickies output. Checklist §7 step 7: copy 6-8 real
packages (stripped of anything private) and the real .SavedStickiesState
over these before trusting tier-1/2 conversion, and record the real state
file key names in dev_notes/.
"""

import plistlib

from pathlib import Path

HERE = Path(__file__).resolve().parent

HEADER = (r"{\rtf1\ansi\ansicpg1252\cocoartf2761" "\n"
          r"{\fonttbl\f0\fswiss\fcharset0 Helvetica;}" "\n"
          r"{\colortbl;\red255\green255\blue255;}" "\n"
          r"\pard\tx560\pardirnatural\partightenfactor0" "\n")

# StickyColor float RGB per note. yellow/blue/green/pink are the real
# values observed on a Mac (2026-08-30); purple and gray are plausible
# members of their hue bands pending calibration (mac_verify.py step 6).
COLORS = {
    "yellow": (0.996, 0.957, 0.612),    # #fef49c  verified
    "blue":   (0.678, 0.957, 1.000),    # #adf4ff  verified
    "green":  (0.698, 1.000, 0.631),    # #b2ffa1  verified
    "pink":   (1.000, 0.780, 0.780),    # #ffc7c7  verified
    "purple": (0.859, 0.749, 0.996),
    "gray":   (0.882, 0.882, 0.882),
}

NOTES = {
    # plain, three paragraphs
    "11111111-AAAA-4AAA-8AAA-111111111111": {
        "color": "yellow",
        "rtf": HEADER + r"\f0\fs24 \cf0 Grocery list\par Milk and eggs\par "
               r"Coffee \'96 dark roast\par}",
    },
    # formatting: bold + italic (tier 2 flattens; tier 1 styles it)
    "22222222-BBBB-4BBB-8BBB-222222222222": {
        "color": "blue",
        "rtf": HEADER + r"\f0\fs24 \cf0 Project ideas\par "
               r"{\b Important:} ship the \i first\i0  version\par}",
    },
    # list-ish lines with bullets
    "33333333-CCCC-4CCC-8CCC-333333333333": {
        "color": "green",
        "rtf": HEADER + r"\f0\fs24 \cf0 Packing\par \bullet  socks\par "
               r"\bullet  charger\par \bullet  passport\par}",
    },
    # empty note
    "44444444-DDDD-4DDD-8DDD-444444444444": {
        "color": "pink",
        "rtf": HEADER + r"\f0\fs24 \cf0 }",
    },
    # unicode: accents via \'xx, unicode via \uN (incl. an emoji pair)
    "55555555-EEEE-4EEE-8EEE-555555555555": {
        "color": "purple",
        "rtf": HEADER + r"\f0\fs24 \cf0 Caf\'e9 notes \u8212 ?\par "
               r"Snowman: \u9731 ?  Rocket: \u-10179 ?\u-8556 ?\par}",
    },
    # first line only punctuation -> slug falls back to "note"
    "66666666-FFFF-4FFF-8FFF-666666666666": {
        "color": "gray",
        "rtf": HEADER + r"\f0\fs24 \cf0 !!! ???\par body text\par}",
    },
    # attachment alongside the RTF
    "77777777-ABAB-4ABA-8ABA-777777777777": {
        "color": "yellow",
        "rtf": HEADER + r"\f0\fs24 \cf0 Whiteboard photo\par See attached\par}",
        "attachments": {"photo.png": b"\x89PNG\r\n\x1a\nfakepngdata"},
    },
}


def main():
    for uuid, spec in NOTES.items():
        package = HERE / f"{uuid}.rtfd"
        package.mkdir(exist_ok=True)
        (package / "TXT.rtf").write_bytes(spec["rtf"].encode("ascii"))
        for name, data in spec.get("attachments", {}).items():
            (package / name).write_bytes(data)

    # Real state-file shape (verified on macOS 2026-08-30): the top level
    # is a LIST of per-note dicts; colour is a float RGBA dict.
    def rgba(name):
        r, g, b = COLORS[name]
        return {"Red": r, "Green": g, "Blue": b, "Alpha": 1.0}

    state = [
        {"UUID": uuid,
         "StickyColor": rgba(spec["color"]),
         "ControlColor": rgba(spec["color"]),
         "HighlightColor": rgba(spec["color"]),
         "SpineColor": rgba(spec["color"]),
         "Frame": "{{100, 100}, {250, 200}}",
         "ExpandedSize": "{250, 200}",
         "ExpandFrameY": 1.0,
         "Floating": False,
         "Translucent": False,
         "SpellCheckingTypes": 9159,
         "ZOrder": index + 1}
        for index, (uuid, spec) in enumerate(NOTES.items())
    ]
    with open(HERE / ".SavedStickiesState", "wb") as handle:
        plistlib.dump(state, handle, fmt=plistlib.FMT_BINARY)

    print(f"Wrote {len(NOTES)} packages + .SavedStickiesState in {HERE}")


if __name__ == "__main__":
    main()


# End of file #
