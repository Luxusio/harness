"""`contract_lint` must run against the real repo, not only fixtures.

`CONTRACTS.md` § 0 states the design invariant: "Prefer machine-enforced gates
over prose. A prose-only rule is commentary." C-11 (managed block not
hand-edited) and C-13 (SKILL.md weight budget) both name `contract_lint.py` as
their enforcement, but on 2026-09-03 nothing ran it automatically:

  * `contract_lint.py` is registered in no `plugin/hooks/hooks.json` event —
    SessionStart runs an inline probe, `verification_gap_check.py`, and
    `drift_warn.py`, and that is all.
  * `tests/test_contract_lint.py` builds a `tempfile` repo; it exercises the
    lint logic, never this repository's `CONTRACTS.md`.
  * `tests/test_skill_weight_contract.py` writes over-budget files under
    `tmp_path`; it proves the scanner reports violations, never that the real
    skill trees are inside budget.

So both contracts described a check that no automated run performed. This file
is that run. It deliberately adds a test rather than a hook: a SessionStart hook
would put the cost on every session start, and C-13's own weight budget argues
against paying runtime latency for a check that a suite already covers.

The hard-drift and weight assertions are each paired with a mutation that must
trip them, because a test that only ever sees a clean tree cannot distinguish
"the repo is compliant" from "the scan reached nothing". The soft-channel
assertion has no real-tree mutation here; its detector is fixture-proven in
`tests/test_contract_lint.py` and that coverage is load-bearing — do not delete
those fixtures as redundant.

The module is loaded under a private name rather than imported, following
`tests/test_skill_weight_contract.py`: the cap-boundary check below lowers
`SKILL_WEIGHT_LIMIT`, and mutating a module object that sibling test files share
is the very defect class this suite has been bitten by.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "CONTRACTS.md"

# The Codex twin of develop/SKILL.md sits at SKILL_WEIGHT_LIMIT. It is the
# boundary case: compliant, but reachable only when a scan covers the
# `plugin-codex` root and the `internal-skills` layout.
CAP_BOUNDARY_SKILL = ROOT / "plugin-codex" / "internal-skills" / "develop" / "SKILL.md"


def _load_contract_lint():
    """Load `contract_lint` as a private module object.

    Registered in `sys.modules` before execution because the module defines
    dataclasses, whose type resolution reads back through `sys.modules`.
    """
    path = ROOT / "plugin" / "scripts" / "contract_lint.py"
    spec = importlib.util.spec_from_file_location("_contract_lint_real", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_contract_lint_real"] = module
    spec.loader.exec_module(module)
    return module


def _plugin_roots() -> list[Path]:
    """Every top-level tree that holds skills, discovered rather than listed.

    `check_skill_weights` takes one plugin root and scans `skills/` and
    `internal-skills/` beneath it. This repository ships two such roots
    (`plugin`, `plugin-codex`), and the CLI's default of `./plugin` therefore
    never reaches the Codex twins on its own. Discovery keeps a third tree from
    landing unguarded.
    """
    roots = []
    for entry in sorted(ROOT.iterdir()):
        if not entry.is_dir():
            continue
        if any((entry / parent).is_dir() for parent in ("skills", "internal-skills")):
            roots.append(entry)
    return roots


class RealContractsFileLints(unittest.TestCase):
    def setUp(self):
        self.lint = _load_contract_lint()

    def test_repo_contracts_file_is_clean(self):
        report = self.lint.lint(str(CONTRACTS), repo_root=str(ROOT))
        self.assertEqual(report.hard, [], f"hard drift in CONTRACTS.md: {report.hard}")
        # Soft issues are also asserted empty: they carry matrix/contract-id
        # mismatches and `Enforced by:` paths that no longer exist, which is
        # precisely how an enforcement claim rots into prose.
        self.assertEqual(report.soft, [], f"soft drift in CONTRACTS.md: {report.soft}")

    def test_the_lint_would_notice_a_tampered_managed_block(self):
        """Mutation: without this, a scan that reached nothing would pass."""
        text = CONTRACTS.read_text(encoding="utf-8")
        self.assertIn("harness:managed-begin", text)
        tampered = text.replace("harness:managed-begin", "harness:managed-BROKEN", 1)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CONTRACTS.md"
            path.write_text(tampered, encoding="utf-8")
            report = self.lint.lint(str(path), repo_root=str(ROOT))
        self.assertTrue(
            report.is_hard(),
            "removing the managed-begin marker produced no hard issue",
        )


class RealSkillTreesAreWithinBudget(unittest.TestCase):
    def setUp(self):
        self.lint = _load_contract_lint()
        self.roots = _plugin_roots()

    def test_more_than_one_root_is_discovered(self):
        """Guards the guard: a single-root scan silently skips the Codex twins."""
        names = [root.name for root in self.roots]
        self.assertIn("plugin", names)
        self.assertIn("plugin-codex", names)

    def test_no_real_skill_exceeds_the_budget(self):
        over = []
        for root in self.roots:
            over.extend(self.lint.check_skill_weights(str(root)))
        detail = ", ".join(
            f"{os.path.relpath(path, ROOT)} is {n} lines "
            f"(>{self.lint.SKILL_WEIGHT_LIMIT})"
            for path, n in over
        )
        self.assertEqual(over, [], f"C-13 weight budget exceeded: {detail}")

    def test_the_scan_reaches_the_file_sitting_at_the_cap(self):
        """Mutation: set the limit just under this file and it must appear.

        Proves `plugin-codex/internal-skills` is actually enumerated — the exact
        path the CLI's `--plugin-root` default of `./plugin` never reaches.

        The threshold is derived from the file's own length rather than from
        `SKILL_WEIGHT_LIMIT`, so trimming this file below the cap — work the
        PLAN lists as a legitimate follow-up — does not turn the suite red on a
        change that improves C-13 compliance.
        """
        self.assertTrue(CAP_BOUNDARY_SKILL.is_file(), CAP_BOUNDARY_SKILL)
        lines = sum(1 for _ in CAP_BOUNDARY_SKILL.open(encoding="utf-8"))
        self.assertLessEqual(
            lines,
            self.lint.SKILL_WEIGHT_LIMIT,
            "the cap-boundary skill is over budget; the weight test covers this",
        )

        # `self.lint` is this test's own module object (see `_load_contract_lint`),
        # so lowering the limit cannot reach any other test.
        self.lint.SKILL_WEIGHT_LIMIT = lines - 1
        over = []
        for root in self.roots:
            over.extend(self.lint.check_skill_weights(str(root)))

        reported = {os.path.realpath(path) for path, _ in over}
        self.assertIn(
            os.path.realpath(CAP_BOUNDARY_SKILL),
            reported,
            "the cap-boundary skill was never enumerated by the scan",
        )


if __name__ == "__main__":
    unittest.main()
