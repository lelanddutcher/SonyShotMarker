#!/usr/bin/env python3
"""Render the plugin's icon set from branding/, so the panel matches the app.

Not shipped inside the plugin package — package_plugin.sh excludes it. Run it only when
the branding art changes:

    python3 plugin/premiere-uxp/make_icons.py
"""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
CAT = os.path.join(REPO, "branding", "cat sticking tongue out.png")
OUT = os.path.join(HERE, "icons")

TOP, BOTTOM = (255, 250, 252), (249, 196, 216)   # same near-white → tongue-pink as the app


def squircle(size: int, radius_ratio: float = 0.225) -> Image.Image:
    """The app's rounded-square treatment: pink gradient plate, cat multiplied on top."""
    cat = Image.open(CAT).convert("RGB")
    side = min(cat.size)
    cat = cat.crop(((cat.width - side) // 2, (cat.height - side) // 2,
                    (cat.width + side) // 2, (cat.height + side) // 2))
    cat = cat.resize((size, size), Image.LANCZOS)

    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        grad.putpixel((0, y), tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)))
    plate = grad.resize((size, size))

    art = Image.blend(plate, cat, 0.88)

    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size * 4 - 1, size * 4 - 1],
        radius=int(size * 4 * radius_ratio), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)

    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(art, (0, 0), mask)
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    written = []
    # Panel footer mark (rendered at 26 CSS px, so 52 covers a 2x display).
    for name, size in [("cat.png", 52),
                       ("plugin-icon.png", 48), ("plugin-icon@2x.png", 96),
                       ("panel.png", 23), ("panel@2x.png", 46)]:
        path = os.path.join(OUT, name)
        squircle(size).save(path)
        written.append(f"{name} ({size}px)")
    print("wrote " + ", ".join(written) + f"\n  -> {OUT}")


if __name__ == "__main__":
    main()
