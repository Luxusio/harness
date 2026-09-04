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
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "CONTRACTS.md"
TEMPLATE_CONTRACTS = ROOT / "plugin" / "skills" / "setup" / "templates" / "CONTRACTS.md"

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


class SetupTemplateShipsTheSameContracts(unittest.TestCase):
    """What setup installs must be the rule set the runtime enforces.

    `plugin/skills/setup/bootstrap.md` copies the template to a new project's
    `CONTRACTS.md`. On 2026-09-04 that template was missing C-14a and C-17
    entirely, so a fresh project received a contract document that never stated
    the turn-end rule while the Stop gate enforced it from the first session —
    the user is blocked by a rule their own contracts file does not contain.

    Nothing checked *this*. `contract_lint` did run against the template —
    `plugin/scripts/golden_replay.py::test_contract_lint_template` has done so
    since before this task — but per-file linting cannot see divergence by
    construction: the matrix check compares a file against itself, so both
    files lint clean while declaring different rule sets. That run also only
    inspects the exit code, so soft issues pass, and it is not collected by
    pytest. `tests/test_setup_finalize.py` copies the template without reading
    it. The drift therefore accumulated silently across releases.

    Contract *identity* is asserted, not prose. The root legitimately carries
    this repository's own evidence — commit hashes, session dates, line
    references — while the template must stay general. What may never differ is
    which rules a project is held to.
    """

    def setUp(self):
        self.lint = _load_contract_lint()

    def _ids(self, path: Path) -> list[str]:
        text = path.read_text(encoding="utf-8")
        return sorted(set(self.lint.CONTRACT_HEADING.findall(text)))

    def test_root_and_template_declare_the_same_contract_ids(self):
        root = self._ids(CONTRACTS)
        template = self._ids(TEMPLATE_CONTRACTS)
        self.assertEqual(
            root, template,
            "setup would install a different rule set than the runtime enforces; "
            f"only in root={sorted(set(root) - set(template))} "
            f"only in template={sorted(set(template) - set(root))}",
        )

    def test_the_id_scan_sees_suffixed_contracts(self):
        """C-14a is the reason this file exists — prove the scan reaches it.

        `CONTRACT_HEADING` required the id to end in a digit until 2026-09-04,
        so C-14a matched nothing: no four-field check, no matrix cross-check,
        and no matrix row in the root. The lint reported "17 contracts, 17
        matrix refs OK" because it could not see the contract on either side.
        Without this assertion the equality above passes vacuously for exactly
        the ids that motivated it.
        """
        for path in (CONTRACTS, TEMPLATE_CONTRACTS):
            with self.subTest(path=path.name):
                self.assertIn("C-14a", self._ids(path))

    def _titles(self, path: Path) -> dict[str, str]:
        """Map contract id → **Title:** line, for both files.

        Title is the one field that is a normative claim rather than repo
        evidence, so it is the one field that may be compared without coupling
        the two documents' prose. `When`/`Enforced by`/`On violation`/`Why`
        legitimately diverge — C-13, C-14, C-14a, C-17 and C-18 all carry
        repo-specific detail in the root that must not ship to a user project.
        """
        text = path.read_text(encoding="utf-8")
        titles = {}
        sections = re.split(r"^###\s+(C-\d+[a-z]*)\s*$", text, flags=re.MULTILINE)
        for cid, body in zip(sections[1::2], sections[2::2]):
            match = re.search(r"^\*\*Title:\*\*\s*(.+)$", body, flags=re.MULTILINE)
            if match:
                titles[cid] = " ".join(match.group(1).split())
        return titles

    def test_root_and_template_agree_on_every_contract_title(self):
        """Two of the eight 2026-09-04 drifts were Title-only.

        C-13 shipped to users as "skills and agent spawns bounded" while the
        root read "skills bounded, agent fanout batched", and C-14 as "review
        and QA receipts" against "subagent receipts". Id equality cannot see
        that: the rule set matched while the rules said different things.

        All 18 titles are byte-identical as of this change, so this assertion
        costs nothing today and goes red on exactly the drift class that had to
        be repaired by hand.
        """
        self.assertEqual(self._titles(CONTRACTS), self._titles(TEMPLATE_CONTRACTS))

    def test_the_template_lints_clean_against_this_tree(self):
        """Clean here, where the referenced runtime paths exist.

        Deliberately not a fresh-project claim. A user project has no
        `plugin/` directory, so C-01/C-02/C-05/C-11/C-12's path hints do not
        resolve there and the template raises five soft warnings on install —
        a pre-existing gap this change neither widens nor closes, recorded in
        REQ__setup-template-installs-the-current-contract.md.
        """
        report = self.lint.lint(str(TEMPLATE_CONTRACTS), repo_root=str(ROOT))
        self.assertEqual(report.hard, [], f"template hard issues: {report.hard}")
        self.assertEqual(report.soft, [], f"template soft issues: {report.soft}")


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
