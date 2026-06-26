#!/usr/bin/env python3
"""
Generate the square podcast show cover (3000x3000) for Apple/Spotify.

Apple requires a square RGB JPEG/PNG between 1400x1400 and 3000x3000. This
builds a clean, branded SpeakForWater cover using the repo's Montserrat fonts.
Run: python3 scripts/make_podcast_cover.py  ->  public/podcast-cover.jpg
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "public" / "podcast-cover.jpg"
EXTRABOLD = REPO / "assets" / "fonts" / "Montserrat-ExtraBold.ttf"
SEMIBOLD = REPO / "assets" / "fonts" / "Montserrat-SemiBold.ttf"

S = 3000  # canvas size
TOP = (10, 38, 78)      # deep navy  #0a264e
BOT = (24, 95, 165)     # brand blue #185fa5
CYAN = (6, 182, 212)    # accent     #06b6d4


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _fit_font(path: Path, text: str, max_w: int, start: int) -> ImageFont.FreeTypeFont:
    size = start
    while size > 10:
        f = ImageFont.truetype(str(path), size)
        if f.getbbox(text)[2] <= max_w:
            return f
        size -= 4
    return ImageFont.truetype(str(path), 10)


def main() -> None:
    img = Image.new("RGB", (S, S), TOP)
    px = img.load()
    # vertical gradient
    for y in range(S):
        c = _lerp(TOP, BOT, y / S)
        for x in range(S):
            px[x, y] = c
    d = ImageDraw.Draw(img, "RGBA")

    # soft cyan wave bands across the lower third
    for i, (yc, amp, alpha) in enumerate([(2180, 120, 38), (2360, 160, 52), (2560, 200, 70)]):
        pts = []
        for x in range(0, S + 1, 20):
            pts.append((x, yc + amp * math.sin(x / 520 + i)))
        pts += [(S, S), (0, S)]
        d.polygon(pts, fill=(*CYAN, alpha))

    # water droplet emblem, centered above the title
    cx, dy, r = S // 2, 980, 250
    bulb = [cx - r, dy - r + 70, cx + r, dy + r + 70]
    d.ellipse(bulb, fill=(255, 255, 255, 255))
    d.polygon([(cx, dy - r - 150), (cx - r + 30, dy - 10), (cx + r - 30, dy - 10)],
              fill=(255, 255, 255, 255))
    # inner cyan highlight
    d.ellipse([cx - 95, dy + 35, cx + 25, dy + 155], fill=(*CYAN, 160))

    # title
    title = "SpeakForWater"
    tf = _fit_font(EXTRABOLD, title, int(S * 0.86), 330)
    tb = d.textbbox((0, 0), title, font=tf)
    d.text(((S - (tb[2] - tb[0])) / 2, 1520), title, font=tf, fill=(255, 255, 255))

    # tagline
    tag = "Water research, in plain language"
    sf = _fit_font(SEMIBOLD, tag, int(S * 0.74), 120)
    sb = d.textbbox((0, 0), tag, font=sf)
    d.text(((S - (sb[2] - sb[0])) / 2, 1900), tag, font=sf, fill=(180, 224, 248))

    img.save(OUT, "JPEG", quality=90, optimize=True)
    kb = OUT.stat().st_size / 1024
    print(f"Wrote {OUT}  ({img.size[0]}x{img.size[1]}, {kb:.0f} KB)")


if __name__ == "__main__":
    main()
