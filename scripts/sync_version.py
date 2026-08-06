#!/usr/bin/env python3
"""Propagate the repo-root VERSION into package.json and pyproject.toml.

VERSION is the single source of truth (app/version.py and src/lib/version.ts
read it directly), but npm and Python packaging insist on a literal version in
their own manifests. This keeps those two in step so VERSION is the only file
anyone edits by hand.

Rewrites are done with a targeted regex on the raw text rather than by parsing
and re-serialising: a json round-trip reflows the file and escapes non-ASCII
(package.json's description contains an em dash), producing a diff far larger
than the one line that actually changed.

Usage:
    python scripts/sync_version.py            # rewrite the manifests
    python scripts/sync_version.py --check    # report drift, change nothing

--check exits 1 on drift, so CI can use it as a gate. The pre-commit hook in
.githooks/pre-commit runs the rewriting form whenever VERSION is staged.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"

# (path, human name, pattern with one capture group around the version literal)
TARGETS = (
    (ROOT / "package.json", "package.json", r'("version"\s*:\s*")([^"]+)(")'),
    (ROOT / "pyproject.toml", "pyproject.toml", r'(?m)^(version\s*=\s*")([^"]+)(")'),
)


def read_version() -> str:
    if not VERSION_FILE.exists():
        sys.exit(f"error: {VERSION_FILE} does not exist")
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        sys.exit(f"error: VERSION must be a bare semver like 1.2.3, got {version!r}")
    return version


def sync(version: str, *, check: bool) -> int:
    drifted = 0
    for path, name, pattern in TARGETS:
        if not path.exists():
            print(f"  ! {name} not found, skipping")
            continue

        text = path.read_text(encoding="utf-8")
        match = re.search(pattern, text)
        if not match:
            print(f"  ! no version field found in {name}")
            drifted += 1
            continue

        current = match.group(2)
        if current == version:
            print(f"  = {name} already {version}")
            continue

        drifted += 1
        if check:
            print(f"  ✗ {name} is {current}, VERSION is {version}")
            continue

        # Replace only the captured version literal; count=1 so a coincidental
        # later match (a dependency pin, say) is never touched.
        path.write_text(
            text[: match.start()]
            + match.group(1) + version + match.group(3)
            + text[match.end():],
            encoding="utf-8",
        )
        print(f"  → {name} {current} → {version}")

    return drifted


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--check",
        action="store_true",
        help="report drift without writing; exit 1 if anything is out of sync",
    )
    args = ap.parse_args()

    version = read_version()
    print(f"VERSION = {version}")
    drifted = sync(version, check=args.check)

    if args.check and drifted:
        print(f"\n{drifted} file(s) out of sync. Run: python scripts/sync_version.py")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
