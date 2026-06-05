#!/usr/bin/env python3
"""Render a macOS-correct app icon: rounded-rect (squircle) with the cat, proper padding.
1024 master with transparent corners → sliced into the .iconset by build_app.sh."""
from PIL import Image, ImageDraw, ImageChops
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CAT = os.path.join(HERE, "..", "branding", "cat sticking tongue out.png")
OUT = os.path.join(HERE, "..", "branding", "AppIcon_1024.png")

S = 1024
MARGIN = 100            # macOS icon grid: art sits inside a 824×824 rounded rect
BOX = S - 2 * MARGIN    # 824
RADIUS = 185            # ≈ macOS continuous-corner radius for an 824 square

# vertical gradient: near-white → soft tongue-pink (defines the squircle on white bgs)
top, bot = (255, 250, 252), (249, 196, 216)
col = Image.new("RGB", (1, BOX))
for y in range(BOX):
    t = y / (BOX - 1)
    col.putpixel((0, y), tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3)))
grad = col.resize((BOX, BOX))

# composite the cat with a multiply blend so its white background melts into the gradient
cat = Image.open(CAT).convert("RGBA")
tw = int(BOX * 0.90)
th = int(cat.height * tw / cat.width)
cat = cat.resize((tw, th))
catOnWhite = Image.new("RGB", (BOX, BOX), (255, 255, 255))
catOnWhite.paste(cat.convert("RGB"), ((BOX - tw) // 2, (BOX - th) // 2 + int(BOX * 0.03)), cat.split()[3])
blended = ImageChops.multiply(grad, catOnWhite)

# rounded-rect alpha mask → transparent corners
mask = Image.new("L", (BOX, BOX), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, BOX - 1, BOX - 1], radius=RADIUS, fill=255)
rect = blended.convert("RGBA")
rect.putalpha(mask)

canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
canvas.alpha_composite(rect, (MARGIN, MARGIN))
canvas.save(OUT)
print("wrote", OUT)
