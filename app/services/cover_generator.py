"""
SpeakForWater — cover_generator.py

Uses public/cover2.png (or cover.png as fallback) as a template.

LAYOUT (configurable via env vars):
  - Episode label "EPISODE N" — centered at single point (EP_CENTER_X, EP_CENTER_Y)
  - Title — bounding box (TITLE_PX_X1, TITLE_PX_Y1)→(TITLE_PX_X2, TITLE_PX_Y2)

DEFAULTS (user's cover2.png):
  Title box   : (219, 191) → (1006, 1842)
  Episode pt  : (613, 190)
  Title font  : Montserrat ExtraBold   (fallback: DejaVu Serif BoldItalic)
  Episode font: Montserrat SemiBold    (fallback: DejaVu Sans Bold)
  Title color : #082B5A
  Episode col : #00AEEF
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


# ── Colors ───────────────────────────────────────────────────────
TITLE_COLOR = _hex_to_rgb(os.environ.get("TITLE_COLOR", "#082B5A"))
EP_COLOR = _hex_to_rgb(os.environ.get("EP_COLOR", "#00AEEF"))

# ── Title bounding box (absolute pixels) ─────────────────────────
TITLE_PX_X1 = int(float(os.environ.get("TITLE_PX_X1", "219")))
TITLE_PX_Y1 = int(float(os.environ.get("TITLE_PX_Y1", "191")))
TITLE_PX_X2 = int(float(os.environ.get("TITLE_PX_X2", "1006")))
TITLE_PX_Y2 = int(float(os.environ.get("TITLE_PX_Y2", "1842")))

# ── Episode center anchor point (X, Y) ───────────────────────────
EP_CENTER_X = int(float(os.environ.get("EP_CENTER_X", "613")))
EP_CENTER_Y = int(float(os.environ.get("EP_CENTER_Y", "190")))

# ── Font candidates (Montserrat first, fallback to DejaVu) ───────
MONTSERRAT_EXTRABOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/montserrat/Montserrat-ExtraBold.ttf",
    "/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf",
    "/usr/share/fonts/opentype/montserrat/Montserrat-ExtraBold.otf",
    "assets/fonts/Montserrat-ExtraBold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
]
MONTSERRAT_SEMIBOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/montserrat/Montserrat-SemiBold.ttf",
    "/usr/share/fonts/truetype/montserrat/Montserrat-Medium.ttf",
    "/usr/share/fonts/opentype/montserrat/Montserrat-SemiBold.otf",
    "assets/fonts/Montserrat-SemiBold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

# ── Template path candidates ─────────────────────────────────────
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
    """Find largest font size at which `text` fits within (max_width, max_height)."""
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


def _fetch_authors_from_openalex(paper_url: str) -> tuple[Optional[str], Optional[str]]:
    if not paper_url:
        return None, None
    try:
        api = "https://api.openalex.org/works/" + urllib.parse.quote(paper_url, safe="")
        req = urllib.request.Request(api, headers={"User-Agent": "SpeakForWater/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        names = []
        for a in data.get("authorships", []) or []:
            au = a.get("author") or {}
            n = au.get("display_name")
            if n:
                names.append(n)
        year = data.get("publication_year")
        if names:
            authors_str = ", ".join(names[:2]) + (", et al." if len(names) > 2 else "")
            return authors_str, (str(year) if year else None)
    except Exception as e:
        log.info(f"Could not fetch authors from OpenAlex ({e}); skipping.")
    return None, None


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
    """Render the cover PNG with episode at single anchor point + title in big bounding box."""
    title = _strip_html(title or "")
    cover_title = _strip_html(cover_title or "") or title

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
        log.info(f"Using template: {tpl_path}")

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
        f"Cover: image={W}x{H}, title_box=({t_x1},{t_y1})→({t_x2},{t_y2}), "
        f"ep_anchor=({EP_CENTER_X},{EP_CENTER_Y})"
    )

    # ── 1) EPISODE label at the single anchor point ──────────────
    # We size the episode font so the "EPISODE N" string fits
    # within ~60% of the title box width.
    ep_text = f"EPISODE {episode_number}"
    ep_max_width = int(t_w * 0.65)
    ep_max_height = 130  # ample vertical room around the anchor
    ep_font, ep_lines, ep_line_h = _fit_text(
        draw, ep_text, MONTSERRAT_SEMIBOLD_CANDIDATES,
        max_width=ep_max_width, max_height=ep_max_height,
        start_size=72, min_size=18, line_spacing=0,
    )
    # Draw centered on EP_CENTER_X, EP_CENTER_Y (anchor = middle-middle)
    bbox = draw.textbbox((0, 0), ep_text, font=ep_font)
    ep_w = bbox[2] - bbox[0]
    ep_h = bbox[3] - bbox[1]
    ep_x = EP_CENTER_X - ep_w // 2
    ep_y = EP_CENTER_Y - ep_h // 2 - bbox[1]  # account for ascender offset
    draw.text((ep_x, ep_y), ep_text, font=ep_font, fill=EP_COLOR)

    # ── 2) TITLE box (auto-fitted, vertically centered) ─────────
    title_font, title_lines, title_line_h = _fit_text(
        draw, cover_title, MONTSERRAT_EXTRABOLD_CANDIDATES,
        max_width=int(t_w * 0.94), max_height=t_h,
        start_size=120, min_size=18, line_spacing=12,
    )
    _draw_block_centered(draw, title_lines, title_font, t_cx, t_y1, t_y2, title_line_h, TITLE_COLOR)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    log.info(
        f"Cover saved: {output_path.name} ({output_path.stat().st_size // 1024} KB), "
        f"cover_title='{cover_title[:80]}'"
    )
    return output_path
