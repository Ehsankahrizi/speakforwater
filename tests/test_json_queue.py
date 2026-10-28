"""Tests for the JSON episode queue (data/queue.json) and its merge driver."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.json_queue import (  # noqa: E402
    COLUMNS,
    EpisodeQueue,
    JsonWorksheet,
    a1_to_rowcol,
    dumps_rows,
    load_rows,
    rowcol_to_a1,
    save_rows,
)
from scripts.merge_queue import merge  # noqa: E402


def _row(url, status="queued", ep=""):
    return {"date": "2026-10-28", "paper_url": url, "paper_title": f"T {url}",
            "status": status, "episode_number": ep, "mp3_url": "", "published_at": ""}


@pytest.fixture
def qfile(tmp_path):
    p = tmp_path / "queue.json"
    save_rows([_row("u1", "published", 1), _row("u2", "failed", 2), _row("u3"), _row("u4")], p)
    return p


def test_real_queue_file_is_valid():
    rows = load_rows(ROOT / "data" / "queue.json")
    assert rows, "data/queue.json should not be empty"
    for r in rows:
        assert list(r) == COLUMNS
        assert r["paper_url"]
    urls = [r["paper_url"].strip().lower() for r in rows]
    assert len(urls) == len(set(urls)), "duplicate paper_url in queue"


def test_one_row_per_line(qfile):
    lines = qfile.read_text().splitlines()
    assert lines[0] == "[" and lines[-1] == "]"
    assert len(lines) == 4 + 2
    for ln in lines[1:-1]:
        json.loads(ln.rstrip(","))


def test_a1_roundtrip():
    for r, c in [(2, 1), (10, 4), (400, 7), (5, 27)]:
        assert a1_to_rowcol(rowcol_to_a1(r, c)) == (r, c)


def test_gspread_like_api(qfile):
    ws = JsonWorksheet(qfile)
    vals = ws.get_all_values()
    assert vals[0] == COLUMNS and vals[1][4] == "1"
    recs = ws.get_all_records()
    assert recs[0]["episode_number"] == 1
    assert ws.cell(4, 3).value == "T u3"
    ws.update_cell(4, 4, "processing")
    ws.batch_update([{"range": "D5", "values": [["dropped"]]}])
    ws.append_row(["2026-10-29", "u5", "T u5", "queued", 9, "", ""])
    ws.delete_rows(3)
    again = load_rows(qfile)
    assert [r["paper_url"] for r in again] == ["u1", "u3", "u4", "u5"]
    assert [r["status"] for r in again] == ["published", "processing", "dropped", "queued"]
    with pytest.raises(ValueError):
        ws.update_cell(1, 1, "x")


def test_episode_queue_flow(qfile):
    q = EpisodeQueue(qfile)
    ep = q.get_next_queued()
    assert ep["paper_url"] == "u3" and ep["row_number"] == 4
    q.mark_processing(ep["row_number"])
    assert EpisodeQueue(qfile).get_next_queued()["paper_url"] == "u4"
    q.mark_published(ep["row_number"], "https://x/ep.mp3")
    r = load_rows(qfile)[2]
    assert r["status"] == "published" and r["mp3_url"] == "https://x/ep.mp3" and r["published_at"]


def test_merge_append_vs_status_change():
    base = [_row("a"), _row("b"), _row("c")]
    ours = [_row("a"), _row("b"), _row("c", "published", 7)]   # podcast run
    theirs = base + [_row("d"), _row("e")]                      # search run
    out = merge(base, ours, theirs)
    assert [r["paper_url"] for r in out] == ["a", "b", "c", "d", "e"]
    assert out[2]["status"] == "published" and out[2]["episode_number"] == 7


def test_merge_fieldwise_and_delete():
    base = [_row("a"), _row("b")]
    ours = [dict(_row("a"), status="processing"), _row("b")]
    theirs = [dict(_row("a"), mp3_url="m"), ]                   # b deleted on theirs
    out = merge(base, ours, theirs)
    assert len(out) == 1
    assert out[0]["status"] == "processing" and out[0]["mp3_url"] == "m"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.mark.skipif(subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
                    reason="git not available")
def test_merge_driver_resolves_real_git_rebase(tmp_path):
    """Two clones edit adjacent lines; second push rebases without conflict."""
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    a, b = tmp_path / "a", tmp_path / "b"
    _git(tmp_path, "clone", "-q", str(bare), str(a))
    for repo in (a,):
        _git(repo, "checkout", "-q", "-b", "main")
        (repo / "data").mkdir()
        (repo / "scripts").mkdir()
        (repo / ".gitattributes").write_text("data/queue.json merge=queuejson\n")
        save_rows([_row("a"), _row("b"), _row("c")], repo / "data" / "queue.json")
        _git(repo, "add", ".")
        _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        _git(repo, "push", "-q", "origin", "main")
    _git(tmp_path, "clone", "-q", "-b", "main", str(bare), str(b))
    driver = f"{sys.executable} {ROOT / 'scripts' / 'merge_queue.py'} %O %A %B"
    for repo in (a, b):
        _git(repo, "config", "merge.queuejson.driver", driver)
        _git(repo, "config", "user.name", "t")
        _git(repo, "config", "user.email", "t@t")

    # a: podcast run publishes the LAST row
    rows = load_rows(a / "data" / "queue.json")
    rows[2]["status"] = "published"
    save_rows(rows, a / "data" / "queue.json")
    _git(a, "commit", "-qam", "publish c")
    _git(a, "push", "-q", "origin", "main")

    # b: search run appends two rows (adjacent to the line a changed)
    rows = load_rows(b / "data" / "queue.json") + [_row("d"), _row("e")]
    save_rows(rows, b / "data" / "queue.json")
    _git(b, "commit", "-qam", "add d e")
    _git(b, "pull", "-q", "--rebase", "origin", "main")
    _git(b, "push", "-q", "origin", "main")

    final = load_rows(b / "data" / "queue.json")
    assert [r["paper_url"] for r in final] == ["a", "b", "c", "d", "e"]
    assert final[2]["status"] == "published"
    assert dumps_rows(final) == (b / "data" / "queue.json").read_text()
