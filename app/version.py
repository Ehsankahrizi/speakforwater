"""Project version, read from the repo-root VERSION file.

VERSION is the single source of truth for both the Python pipeline and the
Astro site (src/lib/version.ts reads the same file). package.json and
pyproject.toml still carry their own declarations because their tooling
requires it — tests/test_version.py fails if any of them drift from VERSION.

To release: edit VERSION, run the tests, commit.
"""

from __future__ import annotations

from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def _read_version() -> str:
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        # Never let a missing file take down a pipeline run — the version is
        # for display, not control flow.
        return "0.0.0+unknown"


__version__ = _read_version()
VERSION = __version__
