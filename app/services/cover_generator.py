"""
SpeakForWater — cover_generator.py

Uses public/cover2.png (or cover.png) as a template. Writes:
  - Episode label "EPISODE N" — centered at (EP_CENTER_X, EP_CENTER_Y)
  - Title — fits inside box (TITLE_PX_X1,TITLE_PX_Y1)→(TITLE_PX_X2,TITLE_PX_Y2)

DEFAULTS (cover2.png):
  Title box     : (219, 191) → (1006, 1842)
  Episode pt    : (613, 190)
  Title font    : Montserrat ExtraBold
  Episode font  : Montserrat SemiBold
  Title color   : #082B5A
  Episode color : #00AEEF

Font sizes are AUTO-FITTED but capped to look reasonable:
  - Title: max 64pt, min 18pt
  - Episode: max 56pt, min 18pt

Override caps via env: TITLE_FONT_MAX, EP_FONT_MAX.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    return (255, 255, 255)


TITLE_COLOR = _hex_to_rgb(os.environ.get("TITLE_COLOR", "#082B5A"))
EP_COLOR = _hex_to_rgb(os.environ.get("EP_COLOR", "#00AEEF"))

TITLE_PX_X1 = int(float(os.environ.get("TITLE_PX_X1", "219")))
TITLE_PX_Y1 = int(float(os.environ.get("TITLE_PX_Y1", "191")))
TITLE_PX_X2 = int(float(os.environ.get("TITLE_PX_X2", "1006")))
TITLE_PX_Y2 = int(float(os.environ.get("TITLE_PX_Y2", "1842")))

EP_CENTER_X = int(float(os.environ.get("EP_CENTER_X", "613")))
EP_CENTER_Y = int(float(os.environ.get("EP_CENTER_Y", "190")))

# Font size caps (these are STRICT upper bounds; auto-shrinks below if needed)
TITLE_FONT_MAX = int(os.environ.get("TITLE_FONT_MAX", "64"))
TITLE_FONT_MIN = int(os.environ.get("TITLE_FONT_MIN", "18"))
EP_FONT_MAX = int(os.environ.get("EP_FONT_MAX", "56"))
EP_FONT_MIN = int(os.environ.get("EP_FONT_MIN", "18"))

MONTSERRAT_EXTRABOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/montserrat/Montserrat-ExtraBold.ttf",
    "/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf",
    "/usr/share/fonts/opentype/montserrat/Montserrat-ExtraBold.otf",
    "assets/fonts/Montserrat-ExtraBold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
MONTSERRAT_SEMIBOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/montserrat/Montserrat-SemiBold.ttf",
    "/usr/share/fonts/truetype/montserrat/Montserrat-Medium.ttf",
    "/usr/share/fonts/opentype/montserrat/Montserrat-SemiBold.otf",
    "assets/fonts/Montserrat-SemiBold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

COVER_CANDIDATES = [
    "public/cover2.png",
    "public/cover.png",
    "./public/cover2.png",
    "./public/cover.png",
]

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _font(size: int, candidates: list[str]) -> ImageFont.FreeTypeFont:
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _strip_html(text: str) -> str:
    if not text:
        return ""
    return _HTML_TAG_RE.sub("", unescape(text)).strip()


def _wrap_lines(draw, text, font, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        trial = " ".join(cur + [w])
        bbox = draw.textbbox((0, 0), trial, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            cur.append(w)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines


def _fit_text(
    draw,
    text: str,
    candidates: list[str],
    max_width: int,
    max_height: int,
    *,
    start_size: int,
    min_size: int,
    line_spacing: int = 8,
):
    """Find largest font size ≤ start_size at which text fits."""
    size = start_size
    while size >= min_size:
        font = _font(size, candidates)
        lines = _wrap_lines(draw, text, font, max_width)
        ascent, descent = font.getmetrics()
        line_h = ascent + descent + line_spacing
        total_h = line_h * len(lines)
        if total_h <= max_height:
            return font, lines, line_h
        size -= 2
    font = _font(min_size, candidates)
    ascent, descent = font.getmetrics()
    return font, _wrap_lines(draw, text, font, max_width), ascent + descent + line_spacing


def _draw_block_centered(draw, lines, font, box_cx: int, box_y1: int, box_y2: int, line_h: int, fill):
    total_h = line_h * len(lines)
    y = box_y1 + max(0, (box_y2 - box_y1 - total_h) // 2)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        draw.text((box_cx - w / 2, y), line, font=font, fill=fill)
        y += line_h


def make_cover(
    output_path: Path,
    title: str,
    episode_number: int,
    background: Optional[Path] = None,
    paper_url: str = "",
    authors: Optional[str] = None,
    year: Optional[str] = None,
    template: Optional[Path] = None,
    cover_title: Optional[str] = None,
) -> Path:
    title = _strip_html(title or "")
    cover_title = _strip_html(cover_title or "") or title

    # DEBUG: explicit logging so we can see what was used
    log.info(f"[cover] paper_title='{title[:120]}'")
    log.info(f"[cover] cover_title='{cover_title[:120]}'")
    if title == cover_title:
        log.warning(
            "[cover] cover_title == paper_title → title_simplifier likely failed "
            "or wasn't called. Check earlier 'title_simplifier' log lines."
        )

    # Resolve template path
    if template and template.exists():
        tpl_path = template
    else:
        tpl_path = None
        for candidate in COVER_CANDIDATES:
            p = Path(candidate)
            if not p.is_absolute():
                p2 = Path(os.environ.get("GITHUB_WORKSPACE", ".")) / candidate
                if p2.exists():
                    tpl_path = p2
                    break
            if p.exists():
                tpl_path = p
                break

    if not tpl_path or not tpl_path.exists():
        log.warning("No cover template found — solid blue fallback.")
        img = Image.new("RGB", (1080, 1920), (10, 37, 64))
    else:
        img = Image.open(tpl_path).convert("RGB")
        log.info(f"[cover] template: {tpl_path}")

    W, H = img.size
    draw = ImageDraw.Draw(img)

    # Clamp title box
    t_x1 = max(0, min(W - 1, TITLE_PX_X1))
    t_y1 = max(0, min(H - 1, TITLE_PX_Y1))
    t_x2 = max(0, min(W, TITLE_PX_X2))
    t_y2 = max(0, min(H, TITLE_PX_Y2))
    t_w = max(50, t_x2 - t_x1)
    t_h = max(50, t_y2 - t_y1)
    t_cx = (t_x1 + t_x2) // 2

    log.info(
        f"[cover] image={W}x{H} title_box=({t_x1},{t_y1})→({t_x2},{t_y2}) "
        f"ep_anchor=({EP_CENTER_X},{EP_CENTER_Y})"
    )

    # ── 1) EPISODE label at the single anchor point ───────────────
    ep_text = f"EPISODE {episode_number}"
    ep_font, _ep_lines, _ep_line_h = _fit_text(
        draw, ep_text, MONTSERRAT_SEMIBOLD_CANDIDATES,
        max_width=int(t_w * 0.55),
        max_height=int(EP_FONT_MAX * 1.4),
        start_size=EP_FONT_MAX, min_size=EP_FONT_MIN,
        line_spacing=0,
    )
    bbox = draw.textbbox((0, 0), ep_text, font=ep_font)
    ep_w = bbox[2] - bbox[0]
    ep_h_px = bbox[3] - bbox[1]
    ep_x = EP_CENTER_X - ep_w // 2
    ep_y = EP_CENTER_Y - ep_h_px // 2 - bbox[1]
    draw.text((ep_x, ep_y), ep_text, font=ep_font, fill=EP_COLOR)

    # ── 2) TITLE box (auto-fitted, capped at TITLE_FONT_MAX) ─────
    title_font, title_lines, title_line_h = _fit_text(
        draw, cover_title, MONTSERRAT_EXTRABOLD_CANDIDATES,
        max_width=int(t_w * 0.92),
        max_height=t_h,
        start_size=TITLE_FONT_MAX, min_size=TITLE_FONT_MIN,
        line_spacing=10,
    )
    _draw_block_centered(draw, title_lines, title_font, t_cx, t_y1, t_y2, title_line_h, TITLE_COLOR)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    log.info(
        f"[cover] saved: {output_path.name} ({output_path.stat().st_size // 1024} KB), "
        f"title_font_size={title_font.size}, ep_font_size={ep_font.size}"
    )
    return output_path
