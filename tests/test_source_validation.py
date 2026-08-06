"""
Tests for the ingested-source content guard.

The guard exists because a publisher that blocks Google's fetcher still yields
a source NotebookLM reports as ready — the block page is ingested as if it were
the paper. Verified live on 2026-08-05: a doi.org link for a Springer paper
produced a ready source titled "406 Not Acceptable" holding 44 words.

Run with: pytest tests/test_source_validation.py -v
"""

import json

import pytest

from app.services.notebooklm import (
    MIN_CONTENT_WORDS,
    NotebookLMAutomator,
    SourceContentError,
)

NB = "nb-1234"


def _automator(listing, fulltexts):
    """Automator whose CLI returns canned payloads.

    `source fulltext` prints a human "Matched: ..." line before its JSON, so
    that preamble is reproduced here — parsing it is part of what's under test.
    """
    a = NotebookLMAutomator(auth_json="{}")

    def fake_run_cli(cmd, timeout=120):
        if cmd[1:3] == ["source", "list"]:
            return json.dumps(listing)
        if cmd[1:3] == ["source", "fulltext"]:
            payload = fulltexts[cmd[3]]
            return f"Matched: {cmd[3]}... ({payload['title']})\n{json.dumps(payload)}"
        raise AssertionError(f"unexpected command: {cmd}")

    a._run_cli = fake_run_cli
    return a


def _listing(*sources):
    return {"notebook_id": NB, "sources": list(sources), "count": len(sources)}


def _source(sid="s1", title="A Real Paper", status="ready", url="https://ex.org/p"):
    return {"id": sid, "title": title, "status": status, "url": url, "type": "web_page"}


def _fulltext(title="A Real Paper", words=2000, extra=""):
    content = " ".join(["water"] * words) + extra
    return {"source_id": "s1", "title": title, "content": content, "url": "https://ex.org/p"}


def test_accepts_a_real_paper():
    a = _automator(_listing(_source()), {"s1": _fulltext()})
    result = a._validate_source_content(NB)
    assert result["words"] == 2000
    assert result["title"] == "A Real Paper"


def test_rejects_block_page_by_title():
    """The live Springer Nature failure mode."""
    a = _automator(
        _listing(_source(title="406 Not Acceptable")),
        {"s1": _fulltext(title="406 Not Acceptable", words=44)},
    )
    with pytest.raises(SourceContentError, match="block/error page"):
        a._validate_source_content(NB)


@pytest.mark.parametrize(
    "title",
    ["Access Denied", "Just a moment...", "403 Forbidden", "Attention Required!"],
)
def test_rejects_other_block_titles(title):
    a = _automator(_listing(_source(title=title)), {"s1": _fulltext(title=title)})
    with pytest.raises(SourceContentError):
        a._validate_source_content(NB)


def test_rejects_block_page_with_innocent_title():
    """Long enough and innocently titled — only the body gives it away."""
    a = _automator(
        _listing(_source(title="Environmental Science and Pollution Research")),
        {"s1": _fulltext(words=800, extra=" Your IP has been blocked due to abuse.")},
    )
    with pytest.raises(SourceContentError, match="bot/IP block"):
        a._validate_source_content(NB)


def test_rejects_content_below_word_floor():
    a = _automator(
        _listing(_source()), {"s1": _fulltext(words=MIN_CONTENT_WORDS - 1)}
    )
    with pytest.raises(SourceContentError, match="too little to be the"):
        a._validate_source_content(NB)


def test_thin_content_warns_but_passes(caplog):
    """Abstract-only is usable; it must not fail the episode."""
    a = _automator(_listing(_source()), {"s1": _fulltext(words=300)})
    with caplog.at_level("WARNING"):
        result = a._validate_source_content(NB)
    assert result["words"] == 300
    assert "thin" in caplog.text.lower()


def test_rejects_unready_source():
    a = _automator(_listing(_source(status="error")), {"s1": _fulltext()})
    with pytest.raises(SourceContentError, match="did not finish processing"):
        a._validate_source_content(NB)


def test_rejects_empty_notebook():
    a = _automator(_listing(), {})
    with pytest.raises(SourceContentError, match="no sources"):
        a._validate_source_content(NB)


def test_rejects_when_any_source_is_bad():
    """A good first source must not mask a bad second one."""
    a = _automator(
        _listing(_source(sid="s1"), _source(sid="s2", title="Access Blocked")),
        {"s1": _fulltext(), "s2": _fulltext(title="Access Blocked", words=44)},
    )
    with pytest.raises(SourceContentError):
        a._validate_source_content(NB)


def test_json_parsing_survives_cli_preamble():
    """`source fulltext` prefixes its JSON with a human line."""
    a = _automator(_listing(_source()), {"s1": _fulltext(words=1000)})
    assert a._validate_source_content(NB)["words"] == 1000


class _FakeResponse:
    """Minimal stand-in for a streamed requests response."""

    def __init__(self, body: bytes, status: int = 200):
        self.body, self.status_code = body, status

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_get(monkeypatch, response):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: response)


def test_download_rejects_html_bot_wall(monkeypatch, tmp_path):
    """The Springer/ACS failure mode: HTTP 200, but the body is a block page."""
    a = NotebookLMAutomator(auth_json="{}", storage_dir=tmp_path)
    _patch_get(monkeypatch, _FakeResponse(b"<!DOCTYPE html><html>Access Denied</html>"))
    assert a._download_pdf("https://pubs.acs.org/x.pdf") is None
    assert list(tmp_path.iterdir()) == []  # no partial file left behind


def test_download_accepts_real_pdf(monkeypatch, tmp_path):
    a = NotebookLMAutomator(auth_json="{}", storage_dir=tmp_path)
    _patch_get(monkeypatch, _FakeResponse(b"%PDF-1.4\n" + b"x" * 50_000))
    out = a._download_pdf("https://www.nature.com/x.pdf", filename_stem="my_paper")
    assert out is not None and out.exists()
    assert out.name.startswith("my_paper_") and out.suffix == ".pdf"


def test_download_rejects_truncated_pdf(monkeypatch, tmp_path):
    """A few hundred bytes of PDF header is an error page, not a paper."""
    a = NotebookLMAutomator(auth_json="{}", storage_dir=tmp_path)
    _patch_get(monkeypatch, _FakeResponse(b"%PDF-1.4\n" + b"x" * 100))
    assert a._download_pdf("https://example.org/x.pdf") is None
    assert list(tmp_path.iterdir()) == []


def test_download_rejects_non_200(monkeypatch, tmp_path):
    a = NotebookLMAutomator(auth_json="{}", storage_dir=tmp_path)
    _patch_get(monkeypatch, _FakeResponse(b"%PDF-1.4\n" + b"x" * 50_000, status=403))
    assert a._download_pdf("https://example.org/x.pdf") is None


def test_upload_path_declines_when_download_fails(monkeypatch, tmp_path):
    """A failed download must fall through to the URL path, not raise."""
    a = NotebookLMAutomator(auth_json="{}", storage_dir=tmp_path)
    _patch_get(monkeypatch, _FakeResponse(b"<html>blocked</html>"))
    assert a._add_source_as_file("nb", "https://link.springer.com/x.pdf", "T") is False


def test_upload_removes_temp_file_after_success(monkeypatch, tmp_path):
    """The PDF must not accumulate in the runner's storage dir."""
    a = NotebookLMAutomator(auth_json="{}", storage_dir=tmp_path)
    _patch_get(monkeypatch, _FakeResponse(b"%PDF-1.4\n" + b"x" * 50_000))
    a._run_cli = lambda cmd, timeout=120: "Added source: abc"
    assert a._add_source_as_file("nb", "https://www.nature.com/x.pdf", "Some Paper") is True
    assert list(tmp_path.iterdir()) == []


def test_content_failure_is_per_paper_not_systemic():
    """A blocked publisher must not abort the whole run.

    run_pipeline classifies auth / notebook-cap / rate-limit failures as
    systemic and aborts, re-queueing the paper. A block page is the opposite:
    it says nothing about the other queued papers, so it must fall through to
    the per-paper handler that marks the row failed and moves to the next one.
    """
    from run_pipeline import (
        _is_notebooklm_auth_error,
        _is_notebooklm_limit_error,
        _is_notebooklm_ratelimit_error,
    )

    errors = [
        SourceContentError(
            "The publisher served a block/error page instead of the paper. "
            "NotebookLM ingested it as a valid source titled '406 Not "
            "Acceptable' (matched 'not acceptable'). URL: https://doi.org/x"
        ),
        SourceContentError(
            "Ingested source holds only 44 words (minimum 150) — too little "
            "to be the paper, and almost certainly an error page."
        ),
        SourceContentError(
            "The fetched page is a bot/IP block, not the paper "
            "(matched 'your ip has been blocked' in the body)."
        ),
        SourceContentError("Source did not finish processing (status='error')"),
        SourceContentError("NotebookLM reports no sources in the notebook"),
    ]

    for err in errors:
        assert not _is_notebooklm_auth_error(err), err
        assert not _is_notebooklm_limit_error(err), err
        assert not _is_notebooklm_ratelimit_error(err), err
