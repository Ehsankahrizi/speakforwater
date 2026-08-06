"""
Tests that the project version has exactly one source of truth.

VERSION at the repo root is authoritative. package.json and pyproject.toml
still declare their own versions because npm and Python packaging require it,
and src/lib/version.ts and app/version.py both read VERSION at build/run time.
That leaves two files free to drift silently, which would defeat the point of
a shared version — so pin them here.

scripts/sync_version.py propagates VERSION into the manifests and
.githooks/pre-commit runs it whenever VERSION is staged, so in practice you
edit VERSION and commit. These tests are the backstop for a commit made with
the hook disabled or from a fresh clone.

Run with: pytest tests/test_version.py -v
"""

import json
import re
import subprocess
import sys
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


# ── The sync script and hook that keep the manifests in step ───────────

def _run_sync(cwd: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(cwd / "scripts" / "sync_version.py"), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def sandbox(tmp_path) -> Path:
    """A miniature repo, so the tests never rewrite the real manifests."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "sync_version.py").write_text(
        (ROOT / "scripts" / "sync_version.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "VERSION").write_text("2.3.4\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{\n  "name": "x",\n  "version": "1.0.0",\n'
        '  "description": "dash — kept",\n  "type": "module"\n}\n',
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    return tmp_path


def test_sync_rewrites_both_manifests(sandbox):
    assert _run_sync(sandbox).returncode == 0
    assert '"version": "2.3.4"' in (sandbox / "package.json").read_text()
    assert 'version = "2.3.4"' in (sandbox / "pyproject.toml").read_text()


def test_sync_preserves_formatting_and_non_ascii(sandbox):
    """A json round-trip would reflow the file and escape the em dash."""
    _run_sync(sandbox)
    text = (sandbox / "package.json").read_text(encoding="utf-8")
    assert "dash — kept" in text, "em dash was escaped or mangled"
    assert text.startswith('{\n  "name": "x",'), "file was reflowed"
    assert text.endswith("}\n")


def test_sync_is_idempotent(sandbox):
    _run_sync(sandbox)
    first = (sandbox / "package.json").read_text(encoding="utf-8")
    _run_sync(sandbox)
    assert (sandbox / "package.json").read_text(encoding="utf-8") == first


def test_check_mode_detects_drift_and_writes_nothing(sandbox):
    before = (sandbox / "package.json").read_text(encoding="utf-8")
    result = _run_sync(sandbox, "--check")
    assert result.returncode == 1, "drift must exit non-zero so CI can gate on it"
    assert (sandbox / "package.json").read_text(encoding="utf-8") == before


def test_check_mode_passes_when_in_sync(sandbox):
    _run_sync(sandbox)
    assert _run_sync(sandbox, "--check").returncode == 0


def test_sync_rejects_a_malformed_version(sandbox):
    (sandbox / "VERSION").write_text("not-a-version\n", encoding="utf-8")
    result = _run_sync(sandbox)
    assert result.returncode != 0
    assert "semver" in (result.stdout + result.stderr).lower()


def test_precommit_hook_is_executable_and_targets_version():
    hook = ROOT / ".githooks" / "pre-commit"
    assert hook.exists(), "the hook that automates the sync is missing"
    assert hook.stat().st_mode & 0o111, "hook is not executable; git will skip it"
    body = hook.read_text(encoding="utf-8")
    assert "sync_version.py" in body
    assert "VERSION" in body


def test_site_reads_the_same_file():
    """The Astro helper must resolve to the repo-root VERSION, not a copy."""
    ts = (ROOT / "src" / "lib" / "version.ts").read_text(encoding="utf-8")
    assert '"../../VERSION"' in ts, (
        "src/lib/version.ts should read the repo-root VERSION file"
    )
