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
import requests
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
PER_SOURCE = int(os.environ.get("PER_SOURCE", "15"))
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
_DEFAULT_EXCLUDED = (
    "10.3390|mdpi.com,"          # MDPI (low signal-to-noise for a general audience)
    "10.3389|frontiersin.org,"   # Frontiers (editorial-quality concerns)
    "10.1155|hindawi.com,"       # Hindawi (editorial-quality concerns)
    "10.22214,"                  # IJRASET (predatory)
    "10.34218,"                  # IJPP (predatory)
    "ssrn.com,"                  # working papers, not peer-reviewed journals
    "eprints,"                   # university repository deposits / theses
    "orbi.uliege.be"             # institutional thesis repository
)


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


# ── Ingestibility verification ─────────────────────────────────────────
#
# NotebookLM fetches URL sources server-side, from Google's own IPs, and the
# major academic publishers block that fetcher. Worse, the block is silent: the
# fetch "succeeds", and NotebookLM ingests the block page as a healthy source.
# Verified live 2026-08-05 — a Springer DOI produced a ready source titled
# "406 Not Acceptable", and a PMC link produced "Checking your browser -
# reCAPTCHA". app/services/notebooklm.py now refuses those at generation time.
#
# So the only route that reliably works is: we download the PDF ourselves and
# upload the bytes. That means a paper is only worth queueing if WE can
# actually fetch its PDF. Measured the same day:
#
#   fetchable: arxiv.org, journals.plos.org, nature.com, frontiersin.org
#   blocked:   link.springer.com, pubs.acs.org, sciencedirect.com,
#              pmc.ncbi.nlm.nih.gov
#
# That list is deliberately NOT hard-coded as a filter — it would go stale, and
# blanket-excluding Springer or Elsevier would throw away good papers whose
# other hosted copies are fine. Instead every candidate is verified by actually
# fetching its first bytes and checking for the PDF magic number, and several
# locations per paper are tried before giving up.

PDF_VERIFY_BYTES = 2048
PDF_VERIFY_TIMEOUT = 20
# Unpaywall requires a contact address. search-papers.yml already sets
# UNPAYWALL_EMAIL for the aggregator; reuse it rather than add another var.
UNPAYWALL_EMAIL = (
    os.environ.get("UNPAYWALL_EMAIL")
    or os.environ.get("OPENALEX_MAILTO")
    or os.environ.get("PODCAST_OWNER_EMAIL")
    or ""
).strip()

# Publisher CDNs commonly serve a bot wall to non-browser user agents.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


def _verify_pdf_url(url: str) -> bool:
    """True if this URL actually serves a PDF to us right now.

    Checks the magic number rather than trusting Content-Type: the blocked
    hosts return an HTML bot wall, sometimes still labelled as a PDF.
    """
    if not url or not url.lower().startswith(("http://", "https://")):
        return False
    try:
        with requests.get(
            url,
            headers={"User-Agent": BROWSER_UA, "Accept": "application/pdf,*/*"},
            timeout=PDF_VERIFY_TIMEOUT,
            stream=True,
            allow_redirects=True,
        ) as r:
            if r.status_code != 200:
                return False
            chunk = next(r.iter_content(PDF_VERIFY_BYTES), b"") or b""
            return chunk.lstrip()[:5].startswith(b"%PDF")
    except Exception as e:
        logger.debug(f"    verify failed for {url[:70]}: {type(e).__name__}")
        return False


def _unpaywall_pdf_locations(doi: str) -> list[str]:
    """Every OA PDF location Unpaywall knows for this DOI, publisher first.

    A paper blocked at its publisher is often readable from a repository
    mirror, so one failed URL should not condemn the paper.
    """
    doi = re.sub(r"^https?://doi\.org/", "", (doi or "").strip(), flags=re.I)
    if not doi or not UNPAYWALL_EMAIL:
        return []
    try:
        r = requests.get(
            f"https://api.unpaywall.org/v2/{doi}",
            params={"email": UNPAYWALL_EMAIL},
            timeout=PDF_VERIFY_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception as e:
        logger.debug(f"    unpaywall lookup failed for {doi}: {type(e).__name__}")
        return []

    urls: list[str] = []
    for loc in data.get("oa_locations") or []:
        for key in ("url_for_pdf", "url"):
            u = (loc.get(key) or "").strip()
            if u and u not in urls:
                urls.append(u)
    return urls


# Hosts measured refusing NotebookLM's own fetcher at ADD_SOURCE (rpc_code=9),
# or feeding it a block page. Being un-downloadable is NOT on its own a reason
# to drop a paper: ScienceDirect blocks our downloader but serves NotebookLM
# perfectly — episode 175 ingested 13,157 words that way. A paper is only
# hopeless when we cannot download it AND NotebookLM cannot fetch it either.
#
# This list is measured, narrow, and will go stale. It only ever costs us a
# paper we might have kept; the content guard in app/services/notebooklm.py is
# the actual safety net against a bad source becoming an episode.
INGEST_HOSTILE_HOSTS = (
    "link.springer.com",
    "nature.com",
    "pubs.acs.org",
    "pmc.ncbi.nlm.nih.gov",
)


def _ingest_hostile(url: str) -> bool:
    return any(h in url.lower() for h in INGEST_HOSTILE_HOSTS)


def resolve_source_url(paper: dict) -> tuple[str | None, str]:
    """Pick the URL to queue and say how the content will actually be obtained.

    Returns ``(url, route)`` where route is:
      "download" — we can fetch the PDF ourselves and upload the bytes
                   (safest; works even where NotebookLM's fetcher is blocked)
      "fetch"    — we cannot download it, but NotebookLM should manage the URL
      ""         — neither route is available; the paper is unusable
    """
    tried: list[str] = []
    for key in ("oa_pdf_url", "url", "link"):
        u = (paper.get(key) or "").strip()
        if u and u not in tried:
            tried.append(u)

    for u in _unpaywall_pdf_locations(paper.get("doi", "")):
        if u not in tried:
            tried.append(u)

    for u in tried:
        if _verify_pdf_url(u):
            return u, "download"

    for u in tried:
        if not _ingest_hostile(u):
            return u, "fetch"

    return None, ""


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
            # Pass the real journal/venue name (not the search engine) so the
            # ranker can judge journal quality and spot predatory venues.
            p["journal"] = p.get("venue") or p.get("source", "")
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
    # Rank a wider pool than we need: some top-ranked papers will turn out to
    # be un-fetchable, and we backfill from further down rather than shipping
    # a short queue. Ranking order is preserved, so journal quality still wins.
    ranked = rank_papers(candidates, max_keep=MAX_PAPERS * 3)

    if not ranked:
        logger.info("No papers passed the AI ranking threshold. Exiting.")
        return

    logger.info(
        f"\n{len(ranked)} papers passed AI ranking. Checking each has a working "
        f"route into NotebookLM (its own fetcher is blocked by some publishers, "
        f"so where we can, we download the PDF and upload the bytes)..."
    )

    papers: list[dict] = []
    n_blocked = 0
    for p in ranked:
        if len(papers) >= MAX_PAPERS:
            break
        title = (p.get("title") or "")[:60]
        url, route = resolve_source_url(p)
        if not url:
            n_blocked += 1
            logger.info(f"  ✗ no route in: {title}")
            continue
        # Queue the URL we just qualified, not whichever one search returned.
        p["url"] = url
        papers.append(p)
        logger.info(f"  ✓ [{route}] {title}")

    logger.info(
        f"\n{len(papers)} papers queueable ({n_blocked} dropped — neither "
        f"downloadable by us nor fetchable by NotebookLM). Checking duplicates..."
    )

    if not papers:
        logger.info(
            "No ranked paper had a working route in. That usually means the "
            "run drew entirely from publishers that block both us and "
            "NotebookLM (Springer, ACS). Widen config/keywords.yml toward "
            "open-access venues, or raise NUM_QUERIES."
        )
        return

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
