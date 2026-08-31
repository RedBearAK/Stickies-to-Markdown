#!/usr/bin/env python3
"""
Generate AppIcon.icns (and a preview PNG) for the .app bundle: a yellow
sticky with a folded corner, carrying a Markdown "M" with a down arrow.
Pillow only. Re-run after editing; commit the result.

    python3 src/stickies_to_markdown/frontends/icons/make_app_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
YELLOW = (254, 244, 156)          # the real Stickies yellow, #fef49c
SHADE = (222, 206, 110)
INK = (60, 60, 60)


def draw(size):
    scale = 4
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = big * 0.08                              # margin
    fold = big * 0.22
    # sticky body with the bottom-right corner folded
    body = [(m, m), (big - m, m), (big - m, big - m - fold),
            (big - m - fold, big - m), (m, big - m)]
    d.polygon(body, fill=YELLOW)
    d.polygon([(big - m - fold, big - m), (big - m, big - m - fold),
               (big - m - fold, big - m - fold)], fill=SHADE)
    # the "M"
    w = big * 0.052
    x0, x1 = big * 0.24, big * 0.66
    top, bot = big * 0.30, big * 0.66
    mid = big * 0.52
    d.line([(x0, bot), (x0, top), ((x0 + x1) / 2, mid), (x1, top), (x1, bot)],
           fill=INK, width=int(w), joint="curve")
    # the down arrow
    ax = big * 0.76
    d.line([(ax, big * 0.30), (ax, big * 0.60)], fill=INK, width=int(w))
    d.polygon([(ax - big * 0.09, big * 0.55), (ax + big * 0.09, big * 0.55),
               (ax, big * 0.68)], fill=INK)
    return img.resize((size, size), Image.LANCZOS)


def main():
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    images = [draw(s) for s in sizes]
    images[-1].save(HERE / "AppIcon.icns", format="ICNS",
                    append_images=images[:-1], sizes=[(s, s) for s in sizes])
    images[-1].resize((256, 256), Image.LANCZOS).save(HERE / "app_icon_preview.png")
    print(f"wrote {HERE / 'AppIcon.icns'}")


if __name__ == "__main__":
    main()


# End of file #
