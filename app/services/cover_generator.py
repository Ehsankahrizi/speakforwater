"""
SpeakForWater — cover_generator.py

Uses public/cover.png as a template. Writes inside an EXACT pixel region
of the TV screen area:

  Default (measured from the user's template):
    top-left     = (475, 184)
    bottom-right = (1101, 434)

Override via env vars TV_PX_X1, TV_PX_Y1, TV_PX_X2, TV_PX_Y2 if you
update the template.

Inside the TV region we draw, top-to-bottom:
  - "EPISODE"
  - episode number (large)
  - simplified short title (italic, auto-fitted)
  - authors + year (small)

Font size auto-shrinks so nothing escapes the box.
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

WHITE = (245, 248, 252)
SOFT_BLUE = (155, 200, 230)
ACCENT = (255, 220, 110)

SERIF_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
]
SERIF_ITALIC_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Italic.ttf",
]
SANS_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

# ABSOLUTE pixel boundaries of the TV inner area in the cover.png template.
# Set via env or change defaults if your template changes.
TV_PX_X1 = int(os.environ.get("TV_PX_X1", "475"))
TV_PX_Y1 = int(os.environ.get("TV_PX_Y1", "184"))
TV_PX_X2 = int(os.environ.get("TV_PX_X2", "1101"))
TV_PX_Y2 = int(os.environ.get("TV_PX_Y2", "434"))

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
    start_size: int = 64,
    min_size: int = 14,
    line_spacing: int = 6,
):
    size = start_size
    while size >= min_size:
        font = _font(size, candidates)
        lines = _wrap_lines(draw, text, font, max_width)
        ascent, descent = font.getmetrics()
        line_h = ascent + descent + line_spacing
        total_h = line_h * len(lines)
        if total_h <= max_height:
            return font, lines
        size -= 2
    font = _font(min_size, candidates)
    return font, _wrap_lines(draw, text, font, max_width)


def _draw_block(draw, lines, font, x_center: int, y_top: int, fill, line_spacing: int = 6) -> int:
    ascent, descent = font.getmetrics()
    line_h = ascent + descent + line_spacing
    y = y_top
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        draw.text((x_center - w / 2, y), line, font=font, fill=fill)
        y += line_h
    return y


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
    background: Optional[Path] = None,   # ignored when cover.png exists
    paper_url: str = "",
    authors: Optional[str] = None,
    year: Optional[str] = None,
    template: Optional[Path] = None,
    cover_title: Optional[str] = None,   # short, listener-friendly title
) -> Path:
    """
    Render a cover PNG. Writes inside the TV pixel region:
      (TV_PX_X1, TV_PX_Y1) → (TV_PX_X2, TV_PX_Y2)
    Use `cover_title` for a short version of the title (preferred for cover).
    Falls back to `title` if `cover_title` is empty.
    """
    title = _strip_html(title or "")
    cover_title = _strip_html(cover_title or "") or title

    # Resolve template path
    if template and template.exists():
        tpl_path = template
    else:
        for candidate in [
            Path("public/cover.png"),
            Path("./public/cover.png"),
            Path(os.environ.get("GITHUB_WORKSPACE", ".")) / "public" / "cover.png",
        ]:
            if candidate.exists():
                tpl_path = candidate
                break
        else:
            tpl_path = None

    if not tpl_path or not tpl_path.exists():
        log.warning("No cover.png template found — solid blue fallback.")
        img = Image.new("RGB", (1920, 1080), (10, 37, 64))
    else:
        img = Image.open(tpl_path).convert("RGB")

    W, H = img.size
    draw = ImageDraw.Draw(img)

    # Clamp TV pixel coords to image bounds
    tv_x1 = max(0, min(W - 1, TV_PX_X1))
    tv_y1 = max(0, min(H - 1, TV_PX_Y1))
    tv_x2 = max(0, min(W, TV_PX_X2))
    tv_y2 = max(0, min(H, TV_PX_Y2))
    tv_w = tv_x2 - tv_x1
    tv_h = tv_y2 - tv_y1
    tv_cx = (tv_x1 + tv_x2) // 2

    if tv_w < 50 or tv_h < 50:
        log.warning(f"TV region too small ({tv_w}x{tv_h}); check TV_PX_* env vars.")

    # Fetch authors if missing
    if (not authors or not year) and paper_url:
        fetched_a, fetched_y = _fetch_authors_from_openalex(paper_url)
        authors = authors or fetched_a
        year = year or fetched_y

    # ── Vertical layout inside TV (top-padded) ────────────────────
    pad_y = max(8, int(tv_h * 0.05))
    inner_w = int(tv_w * 0.92)
    label_h_max = max(14, int(tv_h * 0.16))
    num_h_max = max(40, int(tv_h * 0.36))
    title_h_max = max(40, int(tv_h * 0.34))
    authors_h_max = max(14, int(tv_h * 0.18))

    y = tv_y1 + pad_y

    # EPISODE label
    label_font, _ = _fit_text(
        draw, "EPISODE", SANS_BOLD_CANDIDATES,
        max_width=inner_w, max_height=label_h_max,
        start_size=42, min_size=12,
    )
    y = _draw_block(draw, ["EPISODE"], label_font, tv_cx, y, SOFT_BLUE, line_spacing=2)

    # Episode number (large, accent)
    num_font, _ = _fit_text(
        draw, str(episode_number), SANS_BOLD_CANDIDATES,
        max_width=inner_w, max_height=num_h_max,
        start_size=140, min_size=30,
    )
    y = _draw_block(draw, [str(episode_number)], num_font, tv_cx, y, ACCENT, line_spacing=2)

    # Short title (italic, auto-fitted)
    quoted = f"“{cover_title}”"
    title_font, title_lines = _fit_text(
        draw, quoted, SERIF_ITALIC_CANDIDATES,
        max_width=inner_w, max_height=title_h_max,
        start_size=48, min_size=12,
    )
    y = _draw_block(draw, title_lines, title_font, tv_cx, y, WHITE, line_spacing=4)

    # Authors + year, anchored to bottom of TV region
    if authors and year:
        authors_line = f"{authors} — {year}"
    elif authors:
        authors_line = authors
    elif year:
        authors_line = f"Published {year}"
    else:
        authors_line = ""

    if authors_line:
        auth_font, auth_lines = _fit_text(
            draw, authors_line, SERIF_BOLD_CANDIDATES,
            max_width=inner_w, max_height=authors_h_max,
            start_size=26, min_size=10,
        )
        ascent, descent = auth_font.getmetrics()
        line_h = ascent + descent + 4
        block_h = line_h * len(auth_lines)
        auth_y = tv_y2 - pad_y - block_h
        # Don't overlap title
        if auth_y < y + 6:
            auth_y = y + 6
        _draw_block(draw, auth_lines, auth_font, tv_cx, auth_y, SOFT_BLUE, line_spacing=4)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    log.info(
        f"Cover saved: {output_path.name} ({output_path.stat().st_size // 1024} KB), "
        f"TV box=({tv_x1},{tv_y1})→({tv_x2},{tv_y2})"
    )
    return output_path
