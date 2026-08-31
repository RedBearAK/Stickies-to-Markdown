#!/usr/bin/env python3
"""
Generate every icon this app ships, with Pillow only:

    AppIcon.icns              Finder/Spotlight/Dock icon for the .app bundle
    watching.png (+@2x)       menu bar: green sticky        = watching
    stopped.png  (+@2x)       menu bar: gray sticky         = stopped
    problem.png  (+@2x)       menu bar: amber sticky + "!"  = problem

The menu bar icons are full-color PNGs, not macOS template images, so the
status color survives. They are this app's own artwork - a sticky-note
silhouette - and deliberately unlike Duplicate-File-Preventer's dot /
square / triangle set. Re-run after editing; commit the results:

    python3 src/stickies_to_markdown/frontends/icons/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
YELLOW = (254, 244, 156)          # the real Stickies yellow, #fef49c
SHADE = (222, 206, 110)
INK = (60, 60, 60)

STATUS = {
    "watching": ((110, 200, 120), (70, 150, 85)),     # green
    "stopped": ((170, 170, 170), (120, 120, 120)),    # gray
    "problem": ((245, 180, 60), (200, 135, 30)),      # amber
}


def sticky(size, fill, shade, mark=None):
    """A sticky-note silhouette with a folded corner, optional '!' mark."""
    scale = 8
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = big * 0.06
    fold = big * 0.30
    body = [(m, m), (big - m, m), (big - m, big - m - fold),
            (big - m - fold, big - m), (m, big - m)]
    d.polygon(body, fill=fill)
    d.polygon([(big - m - fold, big - m), (big - m, big - m - fold),
               (big - m - fold, big - m - fold)], fill=shade)
    if mark == "!":
        w = big * 0.13
        cx = big * 0.46
        d.rounded_rectangle([cx - w / 2, big * 0.20, cx + w / 2, big * 0.56],
                            radius=w / 2, fill=INK)
        d.ellipse([cx - w / 2, big * 0.64, cx + w / 2, big * 0.64 + w], fill=INK)
    return img.resize((size, size), Image.LANCZOS)


def app_icon(size):
    scale = 4
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = big * 0.08
    fold = big * 0.22
    body = [(m, m), (big - m, m), (big - m, big - m - fold),
            (big - m - fold, big - m), (m, big - m)]
    d.polygon(body, fill=YELLOW)
    d.polygon([(big - m - fold, big - m), (big - m, big - m - fold),
               (big - m - fold, big - m - fold)], fill=SHADE)
    w = big * 0.052
    x0, x1 = big * 0.24, big * 0.66
    top, bot, mid = big * 0.30, big * 0.66, big * 0.52
    d.line([(x0, bot), (x0, top), ((x0 + x1) / 2, mid), (x1, top), (x1, bot)],
           fill=INK, width=int(w), joint="curve")
    ax = big * 0.76
    d.line([(ax, big * 0.30), (ax, big * 0.60)], fill=INK, width=int(w))
    d.polygon([(ax - big * 0.09, big * 0.55), (ax + big * 0.09, big * 0.55),
               (ax, big * 0.68)], fill=INK)
    return img.resize((size, size), Image.LANCZOS)


def main():
    # menu bar: 20 pt, @2x for Retina (AppKit picks the @2x file itself)
    for name, (fill, shade) in STATUS.items():
        mark = "!" if name == "problem" else None
        sticky(20, fill, shade, mark).save(HERE / f"{name}.png")
        sticky(40, fill, shade, mark).save(HERE / f"{name}@2x.png")
    # app icon
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    images = [app_icon(s) for s in sizes]
    images[-1].save(HERE / "AppIcon.icns", format="ICNS",
                    append_images=images[:-1], sizes=[(s, s) for s in sizes])
    print(f"wrote status icons + AppIcon.icns in {HERE}")


if __name__ == "__main__":
    main()


# End of file #
