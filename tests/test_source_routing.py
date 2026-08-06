"""
Tests for how search_papers picks a paper's route into NotebookLM.

Two independent blocks exist, and they do not overlap:
  - NotebookLM's server-side fetcher is refused by Springer Nature and ACS.
  - Publisher CDNs refuse *our* downloader (Elsevier, Springer, ACS).

So a failed download alone must not disqualify a paper — ScienceDirect blocks
our downloader but serves NotebookLM fine, which is how episode 175 ingested
13,157 words. A paper is only hopeless when both routes are shut.

Run with: pytest tests/test_source_routing.py -v
"""

import pytest

# search_papers pulls in the Sheets and Groq stacks at module scope; skip
# where they are absent (local dev) rather than fail. CI installs
# requirements.txt, so these tests always run there.
sp = pytest.importorskip("search_papers")


@pytest.fixture
def no_network(monkeypatch):
    """Nothing here should touch the network; Unpaywall returns nothing."""
    monkeypatch.setattr(sp, "_unpaywall_pdf_locations", lambda doi: [])


def _downloadable(monkeypatch, *urls):
    ok = set(urls)
    monkeypatch.setattr(sp, "_verify_pdf_url", lambda u: u in ok)


def test_downloadable_paper_uses_the_download_route(monkeypatch, no_network):
    """Nature: NotebookLM refuses the domain, but we can fetch the PDF."""
    url = "https://www.nature.com/articles/s41598-026-55822-0_reference.pdf"
    _downloadable(monkeypatch, url)
    assert sp.resolve_source_url({"oa_pdf_url": url}) == (url, "download")


def test_undownloadable_but_fetchable_paper_is_kept(monkeypatch, no_network):
    """Episode 175's real case — this must not be dropped."""
    url = "https://doi.org/10.1016/j.envpol.2023.121751"
    _downloadable(monkeypatch)  # nothing downloadable
    assert sp.resolve_source_url({"oa_pdf_url": url}) == (url, "fetch")


def test_paper_blocked_both_ways_is_dropped(monkeypatch, no_network):
    """Springer: refuses NotebookLM's fetcher and our downloader."""
    url = "https://link.springer.com/content/pdf/10.1007/s11356-026-38041-y.pdf"
    _downloadable(monkeypatch)
    assert sp.resolve_source_url({"oa_pdf_url": url}) == (None, "")


def test_download_is_preferred_over_fetch(monkeypatch, no_network):
    """When both routes work, take the one that cannot be silently blocked."""
    fetchable = "https://example.org/article"
    downloadable = "https://example.org/article.pdf"
    _downloadable(monkeypatch, downloadable)
    url, route = sp.resolve_source_url(
        {"url": fetchable, "oa_pdf_url": downloadable}
    )
    assert (url, route) == (downloadable, "download")


def test_unpaywall_mirror_rescues_a_blocked_publisher_url(monkeypatch):
    """One blocked publisher link must not condemn the paper."""
    mirror = "https://arxiv.org/pdf/1234.5678"
    monkeypatch.setattr(sp, "_unpaywall_pdf_locations", lambda doi: [mirror])
    _downloadable(monkeypatch, mirror)
    url, route = sp.resolve_source_url(
        {"oa_pdf_url": "https://link.springer.com/content/pdf/x.pdf", "doi": "10.1/x"}
    )
    assert (url, route) == (mirror, "download")


@pytest.mark.parametrize(
    "url,hostile",
    [
        ("https://link.springer.com/content/pdf/x.pdf", True),
        ("https://www.nature.com/articles/x.pdf", True),
        ("https://pubs.acs.org/doi/pdf/x", True),
        ("https://pmc.ncbi.nlm.nih.gov/articles/PMC1/", True),
        ("https://doi.org/10.1016/j.envpol.2023.121751", False),
        ("https://arxiv.org/pdf/1706.03762", False),
        ("https://journals.plos.org/plosone/article/file?id=x", False),
    ],
)
def test_ingest_hostile_host_detection(url, hostile):
    assert sp._ingest_hostile(url) is hostile


def test_no_urls_at_all_is_dropped(no_network):
    assert sp.resolve_source_url({}) == (None, "")
