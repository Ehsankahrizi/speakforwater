"""Project version, read from the repo-root VERSION file.

VERSION is the single source of truth for both the Python pipeline and the
Astro site (src/lib/version.ts reads the same file). package.json and
pyproject.toml still carry their own declarations because their tooling
requires it; scripts/sync_version.py propagates VERSION into them, and
tests/test_version.py fails if any of them drift.

To release: edit VERSION and commit. The .githooks/pre-commit hook syncs the
manifests and stages them (enable once with `git config core.hooksPath
.githooks`); run scripts/sync_version.py by hand if the hook is not enabled.
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
