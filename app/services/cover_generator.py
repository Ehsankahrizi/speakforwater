"""
SpeakForWater — cover_generator.py

Uses public/cover.png as a template. The cover has THREE separately
configurable regions, all in ABSOLUTE pixel coordinates:

  1. EPISODE region (label + big number) — top of TV
  2. TITLE region (italic, auto-fitted)   — user's box (475,184)→(1101,434)
  3. AUTHORS region (small)               — below the title

Override any region via env vars. Defaults below are tuned for the user's
current cover.png template.

Env vars (all integers, in pixels):
  TITLE_PX_X1, TITLE_PX_Y1, TITLE_PX_X2, TITLE_PX_Y2
  EP_PX_X1,    EP_PX_Y1,    EP_PX_X2,    EP_PX_Y2
  AUTH_PX_X1,  AUTH_PX_Y1,  AUTH_PX_X2,  AUTH_PX_Y2
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

# ── TITLE box (user-defined, image pixels) ──
TITLE_PX_X1 = int(os.environ.get("TITLE_PX_X1", "475"))
TITLE_PX_Y1 = int(os.environ.get("TITLE_PX_Y1", "184"))
TITLE_PX_X2 = int(os.environ.get("TITLE_PX_X2", "1101"))
TITLE_PX_Y2 = int(os.environ.get("TITLE_PX_Y2", "434"))

# ── EPISODE (label + number) box — placed ABOVE the title by default ──
# Sits in the top-strip of the TV between the TV top and the title top.
# Default: same x-range as title, y from ~70 (just below TV top) to TITLE_PX_Y1 - 6
EP_PX_X1 = int(os.environ.get("EP_PX_X1", str(TITLE_PX_X1)))
EP_PX_Y1 = int(os.environ.get("EP_PX_Y1", "70"))
EP_PX_X2 = int(os.environ.get("EP_PX_X2", str(TITLE_PX_X2)))
EP_PX_Y2 = int(os.environ.get("EP_PX_Y2", str(max(80, TITLE_PX_Y1 - 6))))

# ── AUTHORS box — placed BELOW the title ──
# Default: same x-range, y from TITLE_PX_Y2 + 6 to TITLE_PX_Y2 + 160 (clamped at draw time)
AUTH_PX_X1 = int(os.environ.get("AUTH_PX_X1", str(TITLE_PX_X1)))
AUTH_PX_Y1 = int(os.environ.get("AUTH_PX_Y1", str(TITLE_PX_Y2 + 6)))
AUTH_PX_X2 = int(os.environ.get("AUTH_PX_X2", str(TITLE_PX_X2)))
AUTH_PX_Y2 = int(os.environ.get("AUTH_PX_Y2", str(TITLE_PX_Y2 + 160)))

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
            return font, lines, line_h
        size -= 2
    font = _font(min_size, candidates)
    ascent, descent = font.getmetrics()
    return font, _wrap_lines(draw, text, font, max_width), ascent + descent + line_spacing


def _draw_block_centered(draw, lines, font, box_cx: int, box_y1: int, box_y2: int, line_h: int, fill):
    """Vertically center `lines` inside [box_y1, box_y2] and horizontally center on box_cx."""
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
    """Render the cover PNG with three regions: EPISODE, TITLE, AUTHORS."""
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

    def _clamp_box(x1, y1, x2, y2):
        return (
            max(0, min(W - 1, x1)),
            max(0, min(H - 1, y1)),
            max(0, min(W, x2)),
            max(0, min(H, y2)),
        )

    # ── Title box (user-defined) ──────────────────────────────────
    t_x1, t_y1, t_x2, t_y2 = _clamp_box(TITLE_PX_X1, TITLE_PX_Y1, TITLE_PX_X2, TITLE_PX_Y2)
    t_w = max(50, t_x2 - t_x1)
    t_h = max(50, t_y2 - t_y1)
    t_cx = (t_x1 + t_x2) // 2

    # ── Episode box (above title) ─────────────────────────────────
    e_x1, e_y1, e_x2, e_y2 = _clamp_box(EP_PX_X1, EP_PX_Y1, EP_PX_X2, EP_PX_Y2)
    e_w = max(50, e_x2 - e_x1)
    e_h = max(40, e_y2 - e_y1)
    e_cx = (e_x1 + e_x2) // 2

    # ── Authors box (below title) ─────────────────────────────────
    a_x1, a_y1, a_x2, a_y2 = _clamp_box(AUTH_PX_X1, AUTH_PX_Y1, AUTH_PX_X2, AUTH_PX_Y2)
    a_w = max(50, a_x2 - a_x1)
    a_h = max(20, a_y2 - a_y1)
    a_cx = (a_x1 + a_x2) // 2

    log.info(
        f"Cover regions — title=({t_x1},{t_y1})→({t_x2},{t_y2}) "
        f"ep=({e_x1},{e_y1})→({e_x2},{e_y2}) "
        f"authors=({a_x1},{a_y1})→({a_x2},{a_y2}) "
        f"image={W}x{H}"
    )

    # Authors lookup
    if (not authors or not year) and paper_url:
        fetched_a, fetched_y = _fetch_authors_from_openalex(paper_url)
        authors = authors or fetched_a
        year = year or fetched_y

    # ── 1) EPISODE box: small label + big number ──────────────────
    # Use ~30% height for label, ~70% for the number, vertically stacked.
    label_h_max = max(14, int(e_h * 0.32))
    num_h_max = max(20, e_h - label_h_max - 4)

    label_font, _, label_line_h = _fit_text(
        draw, "EPISODE", SANS_BOLD_CANDIDATES,
        max_width=e_w, max_height=label_h_max,
        start_size=40, min_size=12, line_spacing=0,
    )
    num_font, _, num_line_h = _fit_text(
        draw, str(episode_number), SANS_BOLD_CANDIDATES,
        max_width=e_w, max_height=num_h_max,
        start_size=130, min_size=24, line_spacing=0,
    )
    # Stack them: label, then number — start at e_y1
    label_y_top = e_y1
    num_y_top = label_y_top + label_line_h + 2
    # Draw centered horizontally on e_cx
    lb_bbox = draw.textbbox((0, 0), "EPISODE", font=label_font)
    lb_w = lb_bbox[2] - lb_bbox[0]
    draw.text((e_cx - lb_w / 2, label_y_top), "EPISODE", font=label_font, fill=SOFT_BLUE)
    num_str = str(episode_number)
    n_bbox = draw.textbbox((0, 0), num_str, font=num_font)
    n_w = n_bbox[2] - n_bbox[0]
    draw.text((e_cx - n_w / 2, num_y_top), num_str, font=num_font, fill=ACCENT)

    # ── 2) TITLE box (italic, auto-fitted, vertically centered) ───
    title_quoted = f"“{cover_title}”"
    title_font, title_lines, title_line_h = _fit_text(
        draw, title_quoted, SERIF_ITALIC_CANDIDATES,
        max_width=int(t_w * 0.95), max_height=t_h,
        start_size=58, min_size=12, line_spacing=4,
    )
    _draw_block_centered(draw, title_lines, title_font, t_cx, t_y1, t_y2, title_line_h, WHITE)

    # ── 3) AUTHORS box (centered) ─────────────────────────────────
    if authors and year:
        authors_line = f"{authors} — {year}"
    elif authors:
        authors_line = authors
    elif year:
        authors_line = f"Published {year}"
    else:
        authors_line = ""

    if authors_line:
        auth_font, auth_lines, auth_line_h = _fit_text(
            draw, authors_line, SERIF_BOLD_CANDIDATES,
            max_width=int(a_w * 0.95), max_height=a_h,
            start_size=28, min_size=10, line_spacing=4,
        )
        _draw_block_centered(draw, auth_lines, auth_font, a_cx, a_y1, a_y2, auth_line_h, SOFT_BLUE)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    log.info(f"Cover saved: {output_path.name} ({output_path.stat().st_size // 1024} KB)")
    return output_path
