"""
Paper search service — finds open-access water research papers.

Uses the OpenAlex API (free, no API key required) to search across
journals defined in config/journals.yml using keywords from
config/keywords.yml.

OpenAlex docs: https://docs.openalex.org/
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
import yaml

logger = logging.getLogger(__name__)

# OpenAlex API base URL (free, no auth needed)
OPENALEX_API = "https://api.openalex.org"

# Polite pool: add your email to get faster rate limits
OPENALEX_EMAIL = "kahriziehsan490@gmail.com"


def load_keywords(config_path: Path | str = "config/keywords.yml") -> list[str]:
    """Load search keywords from YAML config."""
    path = Path(config_path)
    if not path.exists():
        logger.warning(f"Keywords file not found: {path}, using defaults")
        return ["water footprint", "water management", "irrigation"]

    with open(path) as f:
        data = yaml.safe_load(f)
    keywords = data.get("keywords", [])
    logger.info(f"Loaded {len(keywords)} keywords from {path}")
    return keywords


def load_journals(config_path: Path | str = "config/journals.yml") -> list[dict]:
    """Load journal sources from YAML config."""
    path = Path(config_path)
    if not path.exists():
        logger.warning(f"Journals file not found: {path}, using defaults")
        return []

    with open(path) as f:
        data = yaml.safe_load(f)
    journals = data.get("journals", [])
    # Sort by priority (highest first)
    journals.sort(key=lambda j: j.get("priority", 0), reverse=True)
    logger.info(f"Loaded {len(journals)} journal sources from {path}")
    return journals


def search_papers(
    keywords: list[str],
    journals: list[dict],
    max_results: int = 10,
    days_back: int = 90,
    open_access_only: bool = True,
) -> list[dict]:
    """
    Search for recent papers matching keywords from configured journals.

    Args:
        keywords: Search terms to use
        journals: Journal configs from journals.yml
        max_results: Total papers to find
        days_back: How far back to search (days)
        open_access_only: Only return papers with free full text

    Returns:
        List of paper dicts with: title, url, date, journal, doi
    """
    papers = []
    seen_dois = set()

    # Pick 3-4 random keywords per run to keep results varied
    search_keywords = random.sample(keywords, min(4, len(keywords)))
    logger.info(f"Searching with keywords: {search_keywords}")

    # Calculate date range
    from_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    for keyword in search_keywords:
        if len(papers) >= max_results:
            break

        # Search across journals
        for journal in journals:
            if len(papers) >= max_results:
                break

            try:
                found = _search_openalex(
                    keyword=keyword,
                    journal=journal,
                    from_date=from_date,
                    open_access_only=open_access_only,
                    max_per_query=5,
                )

                for paper in found:
                    doi = paper.get("doi", "")
                    if doi and doi in seen_dois:
                        continue
                    if doi:
                        seen_dois.add(doi)
                    papers.append(paper)

                    if len(papers) >= max_results:
                        break

            except Exception as e:
                logger.warning(f"Search failed for '{keyword}' in {journal['name']}: {e}")

            # Rate limiting: be polite to the API
            time.sleep(0.5)

    logger.info(f"Found {len(papers)} papers total")
    return papers[:max_results]


def _search_openalex(
    keyword: str,
    journal: dict,
    from_date: str,
    open_access_only: bool = True,
    max_per_query: int = 5,
) -> list[dict]:
    """
    Search OpenAlex for papers matching keyword in a specific journal.

    Returns list of paper dicts.
    """
    params = {
        "search": keyword,
        "per_page": max_per_query,
        "sort": "publication_date:desc",
        "mailto": OPENALEX_EMAIL,
    }

    # Build filter
    filters = [f"from_publication_date:{from_date}"]

    if open_access_only:
        filters.append("is_oa:true")

    # Filter by journal ISSN or source ID
    issn = journal.get("issn", "")
    source_id = journal.get("source_id", "")
    if source_id:
        filters.append(f"primary_location.source.id:{source_id}")
    elif issn:
        filters.append(f"primary_location.source.issn:{issn}")

    params["filter"] = ",".join(filters)

    url = f"{OPENALEX_API}/works"
    logger.info(f"Searching OpenAlex: '{keyword}' in {journal['name']}...")

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    results = []
    for work in data.get("results", []):
        paper = _parse_openalex_work(work, journal["name"])
        if paper and paper.get("url"):
            results.append(paper)

    logger.info(f"  Found {len(results)} papers for '{keyword}' in {journal['name']}")
    return results


def _parse_openalex_work(work: dict, journal_name: str) -> Optional[dict]:
    """Parse an OpenAlex work object into our paper format."""
    title = work.get("title", "")
    if not title:
        return None

    # Get the best URL (prefer open access URL)
    url = ""
    doi = work.get("doi", "")

    # Try to get open access URL first
    oa = work.get("open_access", {})
    oa_url = oa.get("oa_url", "")

    # Try primary location
    primary = work.get("primary_location", {})
    landing_url = ""
    pdf_url = ""
    if primary:
        landing_url = primary.get("landing_page_url", "")
        source = primary.get("source", {}) or {}
        if primary.get("is_oa"):
            pdf_url = primary.get("pdf_url", "")

    # Priority: OA URL > PDF URL > DOI > landing page
    if oa_url:
        url = oa_url
    elif pdf_url:
        url = pdf_url
    elif doi:
        url = doi  # DOI URLs work as links (e.g. https://doi.org/10.xxx)
    elif landing_url:
        url = landing_url

    if not url:
        return None

    # Get publication date
    pub_date = work.get("publication_date", "")

    # Get journal/source name from the work itself
    source_name = journal_name
    if primary and primary.get("source"):
        source_name = primary["source"].get("display_name", journal_name)

    return {
        "title": title.strip(),
        "url": url,
        "doi": doi,
        "date": pub_date,
        "journal": source_name,
        "is_open_access": oa.get("is_oa", False),
        "oa_status": oa.get("oa_status", "unknown"),
    }
