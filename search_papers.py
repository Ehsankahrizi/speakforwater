#!/usr/bin/env python3
"""
SpeakForWater — Paper Search Pipeline (multi-source, stakeholder-oriented)

Runs daily (separate from podcast generation) to find new open-access water
research papers that matter to real water users — farmers, households,
villages, local agencies, industry, the general public — and add them to the
Google Sheet queue.

Steps:
  1. Load stakeholder-oriented queries from config/keywords.yml
  2. Search MULTIPLE sources in parallel (OpenAlex + Semantic Scholar) via
     app/services/multi_source_search.py — NOT locked to a technical-journal
     whitelist, so applied / everyday-relevant research can surface
  3. Keep only open-access papers (so NotebookLM can read the full text)
  4. AI-rank for plain-language, real-world relevance (paper_ranker.py)
  5. Check the Google Sheet for duplicates
  6. Add new papers with status "queued"

Usage:
  python search_papers.py

Environment variables:
  GOOGLE_CREDENTIALS_JSON  — Service account JSON for Google Sheets
  SPREADSHEET_ID           — Google Sheet ID
  GROQ_API_KEY             — Groq key for the AI ranker
  SEARCH_SOURCES           — comma list (default "openalex,semantic_scholar")
  NUM_QUERIES              — how many queries to run per pass (default 6)
  PER_SOURCE               — results requested per source per query (default 6)
  YEARS_BACK               — keep papers no older than this many years (default 5)
  MAX_PAPERS               — max papers to queue per run (default 10)
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path

import gspread
import yaml
from google.oauth2.service_account import Credentials

from app.services.paper_ranker import rank_papers
from app.services.multi_source_search import aggregate_research


def load_keywords(config_path: Path | str = "config/keywords.yml") -> list[str]:
    """Load stakeholder-oriented search queries from YAML config."""
    path = Path(config_path)
    if not path.exists():
        logger.warning(f"Keywords file not found: {path}, using defaults")
        return ["access to safe drinking water", "irrigation water management", "drought impact on communities"]
    with open(path) as f:
        data = yaml.safe_load(f)
    keywords = data.get("keywords", [])
    logger.info(f"Loaded {len(keywords)} queries from {path}")
    return keywords

# ── Logging ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("paper-search")

# ── Config ─────────────────────────────────────────────────────────────

GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SHEET_NAME = os.environ.get("SHEET_NAME", "Sheet1")

MAX_PAPERS = int(os.environ.get("MAX_PAPERS", "10"))
NUM_QUERIES = int(os.environ.get("NUM_QUERIES", "6"))
PER_SOURCE = int(os.environ.get("PER_SOURCE", "6"))
YEARS_BACK = int(os.environ.get("YEARS_BACK", "5"))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


def validate_env():
    """Check required environment variables."""
    missing = []
    if not GOOGLE_CREDENTIALS_JSON:
        missing.append("GOOGLE_CREDENTIALS_JSON")
    if not SPREADSHEET_ID:
        missing.append("SPREADSHEET_ID")
    if missing:
        logger.error(f"Missing env vars: {', '.join(missing)}")
        sys.exit(1)


# ── Search (multi-source) ──────────────────────────────────────────────

def _recent_enough(year_str: str, cutoff_year: int) -> bool:
    """Keep papers from the last YEARS_BACK years. Keep unknown years (don't
    discard a good paper just because a source omitted the date)."""
    try:
        return int(year_str) >= cutoff_year
    except (ValueError, TypeError):
        return True  # "n.d." / missing — keep, let the ranker judge


# Publishers to exclude (low signal-to-noise for a general audience). Each entry
# is matched against the DOI prefix and any URL/link on the paper. MDPI's DOI
# prefix is 10.3390 and its papers live on mdpi.com. Override via env
# EXCLUDE_PUBLISHERS as a comma list of "doi_prefix|domain" pairs.
_DEFAULT_EXCLUDED = "10.3390|mdpi.com"


def _excluded_publisher(paper: dict) -> str | None:
    """Return the matched publisher tag if the paper is from an excluded
    publisher, else None. Matches on DOI prefix or any URL/link/domain."""
    rules = os.environ.get("EXCLUDE_PUBLISHERS", _DEFAULT_EXCLUDED).strip()
    if not rules:
        return None
    doi = (paper.get("doi") or "").strip().lower()
    haystack = " ".join(
        str(paper.get(k) or "").lower()
        for k in ("doi", "link", "oa_pdf_url", "url")
    )
    for rule in rules.split(","):
        rule = rule.strip()
        if not rule:
            continue
        for token in rule.split("|"):
            token = token.strip().lower()
            if not token:
                continue
            if token.startswith("10.") and doi:
                # DOI prefix match (e.g. "10.3390/..." for MDPI).
                bare = re.sub(r"^https?://doi\.org/", "", doi)
                if bare.startswith(token):
                    return rule
            if token in haystack:
                return rule
    return None


def gather_candidates() -> list[dict]:
    """Run several stakeholder-oriented queries across sources, merge, filter
    to open-access + recent, and return ranker-ready paper dicts."""
    keywords = load_keywords()
    if not keywords:
        logger.error("No queries configured. Edit config/keywords.yml")
        return []

    queries = random.sample(keywords, min(NUM_QUERIES, len(keywords)))
    logger.info(f"Running {len(queries)} queries:")
    for q in queries:
        logger.info(f"  • {q}")

    cutoff_year = datetime.now().year - YEARS_BACK
    seen_titles: set[str] = set()
    seen_dois: set[str] = set()
    candidates: list[dict] = []

    n_raw = n_oa = n_recent = n_kept = 0
    for query in queries:
        for p in aggregate_research(query, per_source=PER_SOURCE):
            n_raw += 1
            title = (p.get("title") or "").strip()
            if not title:
                continue

            # Open-access gate: NotebookLM needs a readable full text.
            oa_url = (p.get("oa_pdf_url") or "").strip()
            if not oa_url:
                continue
            n_oa += 1

            # Recency gate.
            if not _recent_enough(p.get("year", ""), cutoff_year):
                continue
            n_recent += 1

            # Publisher exclusion (e.g. MDPI — low signal for a general audience).
            excluded = _excluded_publisher(p)
            if excluded:
                logger.info(f"  Skipping ({excluded}): {title[:60]}")
                continue
            n_kept += 1

            # De-dup across all queries.
            tkey = title.lower()
            dkey = (p.get("doi") or "").strip().lower()
            if tkey in seen_titles or (dkey and dkey in seen_dois):
                continue
            seen_titles.add(tkey)
            if dkey:
                seen_dois.add(dkey)

            # Normalize for the ranker (it reads abstract / journal / year) and
            # for the Sheet (it needs a single best URL).
            p["abstract"] = p.get("summary", "")
            p["journal"] = p.get("source", "")
            p["url"] = oa_url or p.get("link", "")
            p["date"] = p.get("year", "")
            candidates.append(p)

    logger.info(
        f"Search funnel: {n_raw} raw → {n_oa} open-access → {n_recent} recent "
        f"→ {n_kept} after publisher filter → {len(candidates)} unique candidates."
    )
    return candidates


# ── Google Sheets ──────────────────────────────────────────────────────

def get_sheet():
    """Connect to Google Sheet."""
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    sheet = spreadsheet.worksheet(SHEET_NAME)
    logger.info(f"Connected to sheet: {spreadsheet.title} / {SHEET_NAME}")
    return sheet


def get_existing_urls(sheet) -> tuple[set[str], set[str]]:
    """Get all paper URLs + titles already in the Sheet to avoid duplicates."""
    all_rows = sheet.get_all_records()
    urls: set[str] = set()
    titles: set[str] = set()
    for row in all_rows:
        url = str(row.get("paper_url", "")).strip().lower()
        title = str(row.get("paper_title", "")).strip().lower()
        if url:
            urls.add(url)
        if title:
            titles.add(title)
    logger.info(f"Found {len(urls)} existing URLs in Sheet")
    return urls, titles


def get_next_episode_number(sheet) -> int:
    """Find the highest episode number in the Sheet and return next one."""
    all_rows = sheet.get_all_records()
    max_ep = 0
    for row in all_rows:
        try:
            ep = int(row.get("episode_number", 0))
            if ep > max_ep:
                max_ep = ep
        except (ValueError, TypeError):
            pass
    return max_ep + 1


def add_papers_to_sheet(sheet, papers: list[dict], existing_urls: set, existing_titles: set, start_episode: int) -> int:
    """Add new papers to the Google Sheet. Returns number of papers added."""
    added = 0
    episode_num = start_episode

    for paper in papers:
        url_lower = paper["url"].strip().lower()
        if url_lower in existing_urls:
            logger.info(f"  Skipping (duplicate URL): {paper['title'][:60]}")
            continue

        title_lower = paper["title"].strip().lower()
        if title_lower in existing_titles:
            logger.info(f"  Skipping (duplicate title): {paper['title'][:60]}")
            continue

        row = [
            paper.get("date", ""),          # A: date
            paper["url"],                    # B: paper_url
            paper["title"],                  # C: paper_title
            "queued",                        # D: status
            episode_num,                     # E: episode_number
            "",                              # F: mp3_url
            "",                              # G: published_at
        ]

        try:
            sheet.append_row(row, value_input_option="USER_ENTERED")
            existing_urls.add(url_lower)
            existing_titles.add(title_lower)
            logger.info(f"  Added ep#{episode_num}: {paper['title'][:60]}...")
            logger.info(f"    URL: {paper['url'][:80]}")
            logger.info(
                f"    Source: {paper.get('source', '?')} | score: {paper.get('score', '?')} "
                f"| {paper.get('reason', '')}"
            )
            added += 1
            episode_num += 1
        except Exception as e:
            logger.error(f"  Failed to add paper: {e}")

    return added


def main():
    logger.info("=" * 60)
    logger.info("  SpeakForWater — Paper Search Pipeline (multi-source)")
    logger.info("=" * 60)

    validate_env()

    logger.info(f"\nGathering candidates (target {MAX_PAPERS}, last {YEARS_BACK} years)...")
    candidates = gather_candidates()

    if not candidates:
        logger.info("No open-access candidates found. Try adjusting queries or YEARS_BACK.")
        return

    logger.info(f"\nFound {len(candidates)} open-access candidates. Running AI ranking via Groq...")
    papers = rank_papers(candidates, max_keep=MAX_PAPERS)

    if not papers:
        logger.info("No papers passed the AI ranking threshold. Exiting.")
        return

    logger.info(f"\n{len(papers)} papers passed AI ranking. Checking for duplicates...")

    sheet = get_sheet()
    existing_urls, existing_titles = get_existing_urls(sheet)
    next_episode = get_next_episode_number(sheet)

    logger.info(f"\nAdding new papers (starting at episode #{next_episode})...")
    added = add_papers_to_sheet(sheet, papers, existing_urls, existing_titles, next_episode)

    logger.info("\n" + "=" * 60)
    logger.info(f"  Done! Added {added} new papers to the queue.")
    logger.info(f"  Skipped {len(papers) - added} duplicates.")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
