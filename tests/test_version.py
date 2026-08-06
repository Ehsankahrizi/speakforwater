"""
Tests that the project version has exactly one source of truth.

VERSION at the repo root is authoritative. package.json and pyproject.toml
still declare their own versions because npm and Python packaging require it,
and src/lib/version.ts and app/version.py both read VERSION at build/run time.
That leaves two files free to drift silently, which would defeat the point of
a shared version — so pin them here.

To release: edit VERSION, then package.json and pyproject.toml to match.

Run with: pytest tests/test_version.py -v
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"


@pytest.fixture(scope="module")
def version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def test_version_file_exists_and_is_semver(version):
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
        f"VERSION must be a bare semver like 1.2.3, got {version!r}"
    )


def test_version_file_has_no_stray_whitespace():
    """Readers .strip(), but a stray blank line means someone edited it oddly."""
    raw = VERSION_FILE.read_text(encoding="utf-8")
    assert raw.endswith("\n"), "VERSION should end with a single newline"
    assert raw.strip() == raw.rstrip("\n"), "VERSION has leading/inner whitespace"


def test_python_module_reports_the_file_version(version):
    from app.version import VERSION as module_version

    assert module_version == version


def test_package_json_matches(version):
    data = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert data["version"] == version, (
        f"package.json says {data['version']!r}, VERSION says {version!r} — "
        "update package.json to match."
    )


def test_pyproject_matches(version):
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    assert m, "no version field found in pyproject.toml"
    assert m.group(1) == version, (
        f"pyproject.toml says {m.group(1)!r}, VERSION says {version!r} — "
        "update pyproject.toml to match."
    )


def test_site_reads_the_same_file():
    """The Astro helper must resolve to the repo-root VERSION, not a copy."""
    ts = (ROOT / "src" / "lib" / "version.ts").read_text(encoding="utf-8")
    assert '"../../VERSION"' in ts, (
        "src/lib/version.ts should read the repo-root VERSION file"
    )
