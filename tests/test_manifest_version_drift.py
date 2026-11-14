"""The manifest version is coordinated across setup docs and code.

setup_finalize.MANIFEST_VERSION is the single source of truth. The setup
skill docs (Claude + Codex) and the bootstrap manifest template each hardcode
the same integer for their own reasons (a shell upgrade check and a rendered
template respectively), and the setup docs, verify-report.md, and
plugin/scripts/README.md repeat it in prose; this test asserts none of them
drift from the runtime constant.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugin/scripts/setup_finalize.py"


def load_setup_finalize():
    spec = importlib.util.spec_from_file_location("setup_finalize_drift_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_setup_skill_md_files_match_manifest_version():
    setup_finalize = load_setup_finalize()
    expected = setup_finalize.MANIFEST_VERSION

    for rel in ("plugin/skills/setup/SKILL.md", "plugin-codex/skills/setup/SKILL.md"):
        text = (REPO / rel).read_text(encoding="utf-8")
        match = re.search(r"_HARNESS_MANIFEST_VERSION=(\d+)", text)
        assert match, f"{rel} is missing _HARNESS_MANIFEST_VERSION=<int>"
        assert int(match.group(1)) == expected, (
            f"{rel} declares _HARNESS_MANIFEST_VERSION={match.group(1)}, "
            f"setup_finalize.MANIFEST_VERSION is {expected}"
        )


def test_bootstrap_manifest_template_matches_manifest_version():
    setup_finalize = load_setup_finalize()
    expected = setup_finalize.MANIFEST_VERSION

    bootstrap = (REPO / "plugin/skills/setup/bootstrap.md").read_text(encoding="utf-8")
    match = re.search(r"(?m)^version: (\d+)$", bootstrap)
    assert match, "bootstrap.md manifest template is missing a top-level `version:` line"
    assert int(match.group(1)) == expected, (
        f"bootstrap.md template declares version: {match.group(1)}, "
        f"setup_finalize.MANIFEST_VERSION is {expected}"
    )


# Prose mirrors of the supported version: `version: N` in backticks and the
# verify-report "manifest schema: vN" completion line.
_PROSE_MIRRORS = (
    "plugin/skills/setup/SKILL.md",
    "plugin-codex/skills/setup/SKILL.md",
    "plugin/skills/setup/bootstrap.md",
    "plugin/skills/setup/verify-report.md",
    "plugin/scripts/README.md",
)


def test_every_prose_version_mirror_matches_manifest_version():
    expected = load_setup_finalize().MANIFEST_VERSION
    seen = 0
    for rel in _PROSE_MIRRORS:
        text = (REPO / rel).read_text(encoding="utf-8")
        for match in re.finditer(r"`version: (\d+)`|manifest schema: v(\d+)", text):
            seen += 1
            value = int(match.group(1) or match.group(2))
            assert value == expected, (
                f"{rel} mentions {match.group(0)!r}; "
                f"setup_finalize.MANIFEST_VERSION is {expected}"
            )
    # SKILL.md x2, bootstrap.md, verify-report.md x4, scripts README.
    assert seen >= 8, f"expected at least 8 prose version mirrors, found {seen}"



def test_setup_skill_version_check_block_reads_the_manifest_robustly(tmp_path):
    """QA finding: CRLF, quoted, and keyless manifests were reported as current."""
    import subprocess
    import shutil
    import pytest

    bash = shutil.which("bash")
    if not bash:
        pytest.skip("the documented Bash version-check snippet requires Bash")

    current = load_setup_finalize().MANIFEST_VERSION
    previous = current - 1
    cases = {
        b"version: 5\r\nname: x\r\n": f"UPGRADE_AVAILABLE: 5 -> {current}",
        b'version: "5"\n': f"UPGRADE_AVAILABLE: 5 -> {current}",
        b"name: x\n": f"UPGRADE_AVAILABLE: 0 -> {current}",
        f"version: {previous}\n".encode(): f"UPGRADE_AVAILABLE: {previous} -> {current}",
        f"version: {current}\n".encode(): "UPGRADE_AVAILABLE: no",
        b"version: 5  \n": f"UPGRADE_AVAILABLE: 5 -> {current}",
        b"version: 5  \r\n": f"UPGRADE_AVAILABLE: 5 -> {current}",
        b"version: 99999999999999999999\n": "UPGRADE_AVAILABLE: no",  # newer; no bash overflow
        b"version: 05\n": f"UPGRADE_AVAILABLE: 05 -> {current}",
        b"version: 5 # c\n": (
            "UPGRADE_AVAILABLE: unknown (manifest version '5 # c' is not an integer; "
            "run setup_finalize.py --check)"
        ),
        None: "UPGRADE_AVAILABLE: no",  # no manifest at all
    }
    for rel in ("plugin/skills/setup/SKILL.md", "plugin-codex/skills/setup/SKILL.md"):
        text = (REPO / rel).read_text(encoding="utf-8")
        block = text.split("# Version check\n", 1)[1].split("```", 1)[0]
        for index, (manifest, expected) in enumerate(cases.items()):
            root = tmp_path / f"{rel.replace('/', '_')}_{index}"
            if manifest is not None:
                (root / "doc/harness").mkdir(parents=True)
                (root / "doc/harness/manifest.yaml").write_bytes(manifest)
            result = subprocess.run(
                [bash, "-c", f'_ROOT="{root}"\n{block}'],
                capture_output=True, text=True, timeout=10,
            )
            assert result.stdout.strip() == expected, (rel, manifest, result.stdout, result.stderr)
            assert result.stderr == "", (rel, manifest, result.stderr)
