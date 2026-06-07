"""
SpeakForWater — multi_source_search.py

Multi-source academic paper search, adapted from the Hydra Research Hub "Iris"
literature agent. Searches several scholarly APIs IN PARALLEL, de-duplicates by
title, and returns a normalized list of paper dicts.

Unlike the older `paper_search.py` (which locked results to a hardcoded whitelist
of technical hydrology journals via OpenAlex ISSN filters), this casts a wide net
across all of academia, so applied / stakeholder-relevant water research can
surface — then the Groq ranker (paper_ranker.py) is the gate that keeps only the
audience-friendly ones.

By default we query only OpenAlex + Semantic Scholar (the two broadest, most
metadata-rich free sources). arXiv / Scopus / Google Scholar are implemented but
disabled by default — arXiv skews toward technical ML/physics preprints, and the
others need API keys. Toggle via the `sources` arg or SEARCH_SOURCES env.

Each search returns a list of dicts with this shape:
    {
      "title", "summary" (abstract), "authors" (list), "year" (str),
      "source", "link", "doi", "oa_pdf_url"
    }

ENVIRONMENT VARIABLES (all optional)
    OPENALEX_MAILTO    email -> OpenAlex "polite pool" (faster, more reliable)
    UNPAYWALL_EMAIL    email for the Unpaywall OA-PDF lookup
    SCOPUS_API_KEY     enable Scopus search (Elsevier API key)
    ENABLE_SCHOLAR     "1" to enable Google Scholar via the `scholarly` package
    SEARCH_SOURCES     comma list, e.g. "openalex,semantic_scholar" (default)
    AGGREGATE_TIMEOUT  per-aggregate wall-clock cap in seconds (default 90)
"""
from __future__ import annotations

import concurrent.futures
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import requests

# Default contact email (OpenAlex polite pool + Unpaywall). Override via env.
_DEFAULT_EMAIL = "kahriziehsan490@gmail.com"
_MAILTO = os.getenv("OPENALEX_MAILTO") or os.getenv("UNPAYWALL_EMAIL") or _DEFAULT_EMAIL
_UA = f"SpeakForWater-PaperSearch/1.0 (mailto:{_MAILTO})"

# Canonical source registry. aggregate_research() picks from these by key.
DEFAULT_SOURCES = ["openalex", "semantic_scholar"]


# ========================== Unpaywall (OA PDF finder) ==========================
def find_oa_pdf(doi: str) -> Optional[str]:
    """Use Unpaywall to find a legal open-access PDF URL for a DOI."""
    if not doi:
        return None
    email = os.getenv("UNPAYWALL_EMAIL") or os.getenv("OPENALEX_MAILTO") or _DEFAULT_EMAIL
    clean_doi = re.sub(r"^https?://doi\.org/", "", doi)
    try:
        resp = requests.get(
            f"https://api.unpaywall.org/v2/{clean_doi}",
            params={"email": email}, timeout=15, headers={"User-Agent": _UA},
        )
        if resp.status_code != 200:
            return None
        best = resp.json().get("best_oa_location") or {}
        return best.get("url_for_pdf") or best.get("url")
    except Exception as e:
        print(f"[Unpaywall] {type(e).__name__}: {e}")
        return None


# ========================== Semantic Scholar ==========================
def search_semantic_scholar(query: str, max_results: int = 6) -> List[Dict[str, Any]]:
    """Semantic Scholar Graph API (free, no key, excellent metadata)."""
    print(f"[search] Semantic Scholar: '{query}'")
    try:
        resp = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": query, "limit": max_results,
                "fields": "title,abstract,authors,year,externalIds,url,isOpenAccess,openAccessPdf",
            },
            timeout=20, headers={"User-Agent": _UA},
        )
        if resp.status_code != 200:
            print(f"[Semantic Scholar] HTTP {resp.status_code}")
            return []
        papers = []
        for p in resp.json().get("data", []):
            doi = (p.get("externalIds") or {}).get("DOI", "")
            oa_pdf = (p.get("openAccessPdf") or {}).get("url", "")
            authors = [a.get("name", "") for a in (p.get("authors") or [])]
            papers.append({
                "title": p.get("title", "Untitled"),
                "summary": p.get("abstract") or "No abstract available.",
                "authors": [a for a in authors if a],
                "year": str(p.get("year") or "n.d."),
                "source": "Semantic Scholar",
                "link": f"https://doi.org/{doi}" if doi else p.get("url", ""),
                "doi": doi,
                "oa_pdf_url": oa_pdf,
            })
        return papers
    except Exception as e:
        print(f"[Semantic Scholar Error] {e}")
        return []


# ========================== OpenAlex ==========================
def _reconstruct_abstract(inv_index) -> str:
    if not inv_index:
        return ""
    pos = {}
    for word, idxs in inv_index.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def search_openalex(query: str, max_results: int = 6) -> List[Dict[str, Any]]:
    print(f"[search] OpenAlex: '{query}'")
    params = {"search": query, "per_page": max_results, "sort": "relevance_score:desc"}
    if _MAILTO:
        params["mailto"] = _MAILTO
    try:
        resp = requests.get("https://api.openalex.org/works", params=params, timeout=25,
                            headers={"User-Agent": _UA})
        if resp.status_code != 200:
            print(f"[OpenAlex] HTTP {resp.status_code}")
            return []
        papers = []
        for w in resp.json().get("results", []):
            authors = [a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])]
            doi = w.get("doi") or ""
            link = doi or (w.get("primary_location") or {}).get("landing_page_url") or w.get("id", "")
            oa = w.get("open_access", {})
            oa_url = oa.get("oa_url", "") if oa.get("is_oa") else ""
            papers.append({
                "title": w.get("title") or w.get("display_name") or "Untitled",
                "summary": _reconstruct_abstract(w.get("abstract_inverted_index")) or "No abstract available.",
                "authors": [a for a in authors if a],
                "year": str(w.get("publication_year", "n.d.")),
                "source": "OpenAlex",
                "link": link,
                "doi": re.sub(r"^https?://doi\.org/", "", doi) if doi else "",
                "oa_pdf_url": oa_url,
            })
        return papers
    except Exception as e:
        print(f"[OpenAlex Error] {e}")
        return []


# ========================== arXiv (off by default) ==========================
def search_arxiv(query: str, max_results: int = 3, retries: int = 2) -> List[Dict[str, Any]]:
    print(f"[search] arXiv: '{query}'")
    q = urllib.parse.quote(query)
    url = (f"http://export.arxiv.org/api/query?search_query=all:{q}"
           f"&start=0&max_results={max_results}&sortBy=relevance")
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=25) as resp:
                xml_data = resp.read()
            root = ET.fromstring(xml_data)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            papers = []
            for entry in root.findall("atom:entry", ns):
                authors = [a.find("atom:name", ns).text.strip()
                           for a in entry.findall("atom:author", ns)
                           if a.find("atom:name", ns) is not None]
                published = entry.find("atom:published", ns)
                year = published.text[:4] if (published is not None and published.text) else "n.d."
                entry_id = entry.find("atom:id", ns).text.strip()
                pdf_link = entry_id.replace("/abs/", "/pdf/") + ".pdf" if "/abs/" in entry_id else ""
                papers.append({
                    "title": entry.find("atom:title", ns).text.strip().replace("\n", " "),
                    "summary": entry.find("atom:summary", ns).text.strip().replace("\n", " "),
                    "authors": authors,
                    "year": year,
                    "source": "arXiv",
                    "link": entry_id,
                    "doi": "",
                    "oa_pdf_url": pdf_link,
                })
            return papers
        except Exception as e:
            last_err = e
            print(f"[arXiv] attempt {attempt}/{retries}: {type(e).__name__}: {e}")
            if attempt < retries:
                time.sleep(3)
    print(f"[arXiv] unavailable ({last_err}); relying on other sources.")
    return []


# ========================== Scopus (off by default, needs key) ==========================
def search_scopus(query: str, max_results: int = 3) -> List[Dict[str, Any]]:
    key = os.getenv("SCOPUS_API_KEY")
    if not key:
        print("[search] Scopus skipped (no SCOPUS_API_KEY).")
        return []
    print(f"[search] Scopus: '{query}'")
    try:
        resp = requests.get(
            "https://api.elsevier.com/content/search/scopus",
            headers={"X-Elsevier-APIKey": key, "Accept": "application/json"},
            params={"query": f"TITLE-ABS-KEY({query})", "count": max_results}, timeout=20)
        if resp.status_code != 200:
            print(f"[Scopus] HTTP {resp.status_code}")
            return []
        entries = resp.json().get("search-results", {}).get("entry", [])
        papers = []
        for e in entries:
            doi = e.get("prism:doi", "")
            papers.append({
                "title": e.get("dc:title", "No Title"),
                "summary": e.get("dc:description", "Abstract requires institutional access."),
                "authors": [e.get("dc:creator", "")] if e.get("dc:creator") else [],
                "year": (e.get("prism:coverDate", "n.d.") or "n.d.")[:4],
                "source": "Scopus",
                "link": f"https://doi.org/{doi}" if doi else ((e.get("link", [{}]) or [{}])[0].get("@href", "")),
                "doi": doi,
                "oa_pdf_url": "",
            })
        return papers
    except Exception as e:
        print(f"[Scopus Error] {e}")
        return []


# ========================== Google Scholar (off by default, needs scholarly) ==========================
def search_scholar(query: str, max_results: int = 2) -> List[Dict[str, Any]]:
    if os.getenv("ENABLE_SCHOLAR", "0") != "1":
        print("[search] Scholar skipped (set ENABLE_SCHOLAR=1 to enable).")
        return []
    try:
        from scholarly import scholarly
    except Exception:
        print("[search] Scholar unavailable (pip install scholarly).")
        return []
    print(f"[search] Scholar: '{query}'")
    papers = []
    try:
        sq = scholarly.search_pubs(query)
        for _ in range(max_results):
            try:
                p = next(sq)
                bib = p.get("bib", {})
                author = bib.get("author")
                papers.append({
                    "title": bib.get("title", "Untitled"),
                    "summary": bib.get("abstract", "No abstract available."),
                    "authors": author if isinstance(author, list) else [author] if author else [],
                    "year": str(bib.get("pub_year", "n.d.")),
                    "source": "Google Scholar",
                    "link": p.get("pub_url", ""),
                    "doi": "", "oa_pdf_url": "",
                })
            except StopIteration:
                break
        return papers
    except Exception as e:
        print(f"[Scholar Error] {e}")
        return []


# Registry mapping source keys -> (display name, callable factory).
_SOURCE_REGISTRY = {
    "openalex": ("OpenAlex", lambda q, n: search_openalex(q, max_results=n)),
    "semantic_scholar": ("Semantic Scholar", lambda q, n: search_semantic_scholar(q, max_results=n)),
    "arxiv": ("arXiv", lambda q, n: search_arxiv(q, max_results=max(1, n - 3))),
    "scopus": ("Scopus", lambda q, n: search_scopus(q, max_results=max(1, n - 3))),
    "scholar": ("Google Scholar", lambda q, n: search_scholar(q, max_results=max(1, n - 4))),
}


def _resolve_sources(sources: Optional[List[str]]) -> List[str]:
    if sources is None:
        env = os.getenv("SEARCH_SOURCES", "")
        sources = [s.strip().lower() for s in env.split(",") if s.strip()] or list(DEFAULT_SOURCES)
    valid = [s for s in sources if s in _SOURCE_REGISTRY]
    return valid or list(DEFAULT_SOURCES)


# ========================== Aggregate (the main entry point) ==========================
def aggregate_research(
    query: str,
    sources: Optional[List[str]] = None,
    per_source: int = 6,
    breadth: float = 1.0,
) -> List[Dict[str, Any]]:
    """Query the selected sources IN PARALLEL and de-dup by title.

    sources    : list of source keys (default OpenAlex + Semantic Scholar, or
                 the SEARCH_SOURCES env). Unknown keys are ignored.
    per_source : base number of results requested per source.
    breadth    : scales per-source result counts. <1 narrows, >1 widens.
    Results are reassembled in the requested source order so de-dup tie-breaking
    is deterministic (earlier sources win).
    """
    keys = _resolve_sources(sources)
    n = max(1, round(per_source * (breadth if breadth and breadth > 0 else 1.0)))

    searches = [(_SOURCE_REGISTRY[k][0], (lambda fn=_SOURCE_REGISTRY[k][1]: fn(query, n))) for k in keys]
    agg_timeout = int(os.getenv("AGGREGATE_TIMEOUT", "90"))
    by_source: Dict[str, List[Dict[str, Any]]] = {name: [] for name, _ in searches}
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(searches))) as ex:
        future_to_name = {ex.submit(fn): name for name, fn in searches}
        done, not_done = concurrent.futures.wait(future_to_name, timeout=agg_timeout)
        for fut in done:
            name = future_to_name[fut]
            try:
                by_source[name] = fut.result() or []
            except Exception as e:
                print(f"[search] source '{name}' errored: {type(e).__name__}: {e}")
        for fut in not_done:
            print(f"[search] source '{future_to_name[fut]}' exceeded {agg_timeout}s; skipping.")
            fut.cancel()

    results = []
    for name, _ in searches:
        results.extend(by_source.get(name, []))
    seen, unique = set(), []
    for p in results:
        key = p.get("title", "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(p)
    print(f"[search] '{query[:50]}' -> {len(unique)} unique papers from {len(results)} results "
          f"({', '.join(keys)} in {time.time() - t0:.1f}s).")
    return unique


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "access to safe drinking water in rural communities"
    for i, p in enumerate(aggregate_research(q), 1):
        authors = ", ".join(p["authors"][:3]) or "Unknown"
        oa = "OA" if p["oa_pdf_url"] else "—"
        print(f"\n[{i}] ({p['source']}, {p['year']}, {oa}) {p['title']}\n    {authors}\n    {p['link']}")
