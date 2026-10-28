"""
JSON-file episode queue — replaces the Google Sheet.

The queue lives in the repo at ``data/queue.json`` (override with the
``QUEUE_FILE`` env var). Each row is one paper:

    date, paper_url, paper_title, status, episode_number, mp3_url, published_at

The file is written with ONE ROW PER LINE so git diffs stay readable and a
custom merge driver (scripts/merge_queue.py) can reconcile concurrent edits
row-by-row.

``JsonWorksheet`` implements the small subset of the gspread ``Worksheet``
API this repo used (get_all_records, get_all_values, append_row, update_cell,
cell, batch_update, delete_rows), with the same 1-based row/column numbering
where row 1 is the header. That keeps the pipeline and maintenance scripts'
logic unchanged — only "where does the sheet come from" changed.

Every write is saved to disk immediately (atomically). Committing the file
back to the repo is done by ``commit_queue()`` / scripts/commit_queue.py.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

logger = logging.getLogger(__name__)

COLUMNS = [
    "date",
    "paper_url",
    "paper_title",
    "status",
    "episode_number",
    "mp3_url",
    "published_at",
]

_REPO_DIR = Path(os.environ.get("GITHUB_WORKSPACE") or Path(__file__).resolve().parents[2])
DEFAULT_QUEUE_FILE = _REPO_DIR / "data" / "queue.json"


def queue_path() -> Path:
    p = os.environ.get("QUEUE_FILE")
    return Path(p) if p else DEFAULT_QUEUE_FILE


# ── Serialisation ──────────────────────────────────────────────────────

def _normalise_row(row: dict) -> dict:
    """Coerce a row to exactly COLUMNS, in order (tolerates stray header spaces)."""
    norm = {str(k).strip().lower().replace(" ", "_"): v for k, v in row.items()}
    out = {}
    for c in COLUMNS:
        v = norm.get(c, "")
        if v is None:
            v = ""
        if c == "episode_number":
            s = str(v).strip()
            v = int(s) if re.fullmatch(r"-?\d+", s) else s
        else:
            v = str(v)
        out[c] = v
    return out


def load_rows(path: Path | None = None) -> list[dict]:
    path = path or queue_path()
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    data = json.loads(text)
    if isinstance(data, dict):  # tolerate {"rows": [...]}
        data = data.get("rows", [])
    return [_normalise_row(r) for r in data]


def dumps_rows(rows: list[dict]) -> str:
    """One JSON object per line — diff- and merge-friendly."""
    if not rows:
        return "[]\n"
    lines = [json.dumps(_normalise_row(r), ensure_ascii=False) for r in rows]
    return "[\n" + ",\n".join(lines) + "\n]\n"


def save_rows(rows: list[dict], path: Path | None = None) -> None:
    path = path or queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".queue-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dumps_rows(rows))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── A1 helpers (replacement for gspread.utils) ─────────────────────────

def rowcol_to_a1(row: int, col: int) -> str:
    letters = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row}"


def a1_to_rowcol(a1: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*([A-Za-z]+)(\d+)\s*", a1.split(":")[0].split("!")[-1])
    if not m:
        raise ValueError(f"Bad A1 reference: {a1!r}")
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)), col


def _numericise(v):
    """Mimic gspread.get_all_records(): numeric strings come back as numbers."""
    if isinstance(v, (int, float)):
        return v
    s = str(v)
    if re.fullmatch(r"-?\d+", s.strip() or "x"):
        return int(s.strip())
    try:
        if re.fullmatch(r"-?\d+\.\d+", s.strip()):
            return float(s.strip())
    except ValueError:
        pass
    return s


# ── gspread-compatible worksheet ───────────────────────────────────────

class JsonWorksheet:
    """Drop-in stand-in for a gspread Worksheet, backed by data/queue.json."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else queue_path()
        self.title = "queue"
        self.spreadsheet = SimpleNamespace(title=self.path.name)
        self._rows = load_rows(self.path)
        logger.info(f"Loaded queue: {self.path} ({len(self._rows)} rows)")

    # reads
    def reload(self):
        self._rows = load_rows(self.path)

    def get_all_records(self, **_kw) -> list[dict]:
        return [{c: _numericise(r[c]) for c in COLUMNS} for r in self._rows]

    def get_all_values(self, **_kw) -> list[list[str]]:
        out = [list(COLUMNS)]
        for r in self._rows:
            out.append(["" if r[c] is None else str(r[c]) for c in COLUMNS])
        return out

    def cell(self, row: int, col: int):
        vals = self.get_all_values()
        try:
            v = vals[row - 1][col - 1]
        except IndexError:
            v = ""
        return SimpleNamespace(value=v, row=row, col=col)

    # writes
    def _save(self):
        save_rows(self._rows, self.path)

    def _set(self, row: int, col: int, value):
        if row < 2:
            raise ValueError("Row 1 is the header and cannot be edited.")
        if not 1 <= col <= len(COLUMNS):
            raise ValueError(f"Column {col} out of range 1..{len(COLUMNS)}")
        idx = row - 2
        if idx >= len(self._rows):
            raise IndexError(f"Row {row} does not exist (queue has {len(self._rows)} rows).")
        r = dict(self._rows[idx])
        r[COLUMNS[col - 1]] = value
        self._rows[idx] = _normalise_row(r)

    # Every write re-reads the file first, so a stale in-memory copy can never
    # clobber a change saved by another JsonWorksheet in the same run.
    def update_cell(self, row: int, col: int, value):
        self.reload()
        self._set(row, col, value)
        self._save()

    def batch_update(self, data: list[dict], **_kw):
        self.reload()
        for item in data:
            r0, c0 = a1_to_rowcol(item["range"])
            for dr, vals in enumerate(item["values"]):
                for dc, v in enumerate(vals):
                    self._set(r0 + dr, c0 + dc, v)
        self._save()

    def append_row(self, values: list, **_kw):
        vals = list(values) + [""] * (len(COLUMNS) - len(values))
        self.reload()
        self._rows.append(_normalise_row(dict(zip(COLUMNS, vals))))
        self._save()

    def delete_rows(self, start: int, end: int | None = None):
        end = end or start
        if start < 2:
            raise ValueError("Cannot delete the header row.")
        self.reload()
        del self._rows[start - 2:end - 1]
        self._save()


def open_worksheet(path: Path | str | None = None) -> JsonWorksheet:
    return JsonWorksheet(path)


# ── Episode queue (same interface the pipeline used before) ───────────

class EpisodeQueue:
    """
    Reads and updates the episode queue.

    ``row_number`` keeps the old spreadsheet meaning: 1-based, row 1 = header,
    so the first paper is row 2.
    """

    def __init__(self, path: Path | str | None = None):
        self.sheet = JsonWorksheet(path)

    def get_next_queued(self) -> dict | None:
        self.sheet.reload()
        for i, row in enumerate(self.sheet.get_all_records()):
            if str(row.get("status", "")).strip().lower() == "queued":
                try:
                    ep_num = int(row.get("episode_number") or 0)
                except (ValueError, TypeError):
                    ep_num = 0
                row_number = i + 2
                logger.info(f"Queued row {row_number}: episode_number={ep_num}")
                return {
                    "row_number": row_number,
                    "date": str(row.get("date", "")),
                    "paper_url": str(row.get("paper_url", "")),
                    "paper_title": str(row.get("paper_title", "")),
                    "status": str(row.get("status", "")),
                    "episode_number": ep_num,
                }
        logger.info("No queued episodes found")
        return None

    def update_status(self, row_number: int, status: str, mp3_url: str = "", published_at: str = ""):
        self.sheet.reload()
        updates = [{"range": rowcol_to_a1(row_number, 4), "values": [[status]]}]
        if mp3_url:
            updates.append({"range": rowcol_to_a1(row_number, 6), "values": [[mp3_url]]})
        if published_at:
            updates.append({"range": rowcol_to_a1(row_number, 7), "values": [[published_at]]})
        self.sheet.batch_update(updates)
        logger.info(f"Row {row_number} updated: status={status}")

    def mark_processing(self, row_number: int):
        self.update_status(row_number, "processing")

    def mark_published(self, row_number: int, mp3_url: str):
        now = datetime.now(timezone.utc).isoformat()
        self.update_status(row_number, "published", mp3_url=mp3_url, published_at=now)

    def mark_failed(self, row_number: int, error: str = ""):
        self.update_status(row_number, f"failed: {error}" if error else "failed")


# ── Commit the queue back to the repo ─────────────────────────────────

def commit_queue(message: str, path: Path | None = None, retries: int = 4) -> bool:
    """
    git add + commit + pull --rebase + push the queue file.
    Returns True if something was pushed. Safe to call when nothing changed.
    Concurrent edits from another workflow are reconciled by the
    'queuejson' merge driver (see .gitattributes / scripts/merge_queue.py).
    """
    path = path or queue_path()
    repo = _REPO_DIR

    def git(*args, check=True):
        res = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
        if check and res.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
        return res

    _ensure_git_setup(repo)
    git("add", str(path))
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        logger.info("Queue unchanged — nothing to commit.")
        return False
    git("commit", "-m", message)
    for attempt in range(1, retries + 1):
        git("pull", "--rebase", "--autostash", "origin", _default_branch(repo), check=False)
        if git("push", check=False).returncode == 0:
            logger.info("Queue pushed.")
            return True
        logger.warning(f"Push rejected (attempt {attempt}/{retries}); retrying...")
    raise RuntimeError("Could not push queue changes after retries.")


def _default_branch(repo: Path) -> str:
    return os.environ.get("QUEUE_BRANCH", "main")


def _ensure_git_setup(repo: Path) -> None:
    """Identity + merge driver registration (idempotent; needed on CI runners)."""
    def cfg(key, val):
        cur = subprocess.run(["git", "config", "--get", key], cwd=str(repo), capture_output=True, text=True)
        if cur.returncode != 0 or not cur.stdout.strip():
            subprocess.run(["git", "config", key, val], cwd=str(repo), check=False)

    cfg("user.name", "SpeakForWater Bot")
    cfg("user.email", "bot@speakforwater.com")
    subprocess.run(
        ["git", "config", "merge.queuejson.driver", "python3 scripts/merge_queue.py %O %A %B"],
        cwd=str(repo), check=False,
    )
    subprocess.run(["git", "config", "merge.queuejson.name", "SpeakForWater queue.json row merge"],
                   cwd=str(repo), check=False)
