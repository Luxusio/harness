"""Setup's pytest-availability probe must test the declared runner.

`plugin/skills/setup/verify-report.md` used to say: for a pytest-based
`test_command`, run `python3 -m pytest --version` and report a missing runner
as blocking. On this repository that probe fails — `python3` here has no pytest
— while the declared `test_command`, `uv run pytest ...`, works in the
coordinator shell and in subagent shells alike. So setup would have reported a
healthy repo as blocked, on the strength of a command the manifest never named.

Launcher-mediated suites (`uv`, `poetry`, `hatch`, `tox`, a venv interpreter)
are the normal case, so the fixed probe is wrong by default rather than in an
edge case. The skill is prose; these are the structural and behavioural halves
of the guard. See doc/harness/REQ__recorded-claims-must-stay-falsifiable.md.
"""

from __future__ import annotations

import shlex
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY_REPORT = ROOT / "plugin" / "skills" / "setup" / "verify-report.md"
MANIFEST = ROOT / "doc" / "harness" / "manifest.yaml"


def declared_test_command() -> str:
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.startswith("test_command:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def runner_probe(test_command: str) -> list[str]:
    """The skill's rule, executable: truncate at `pytest`, append `--version`.

    Truncation is the point — the declared command runs the whole suite.
    """
    parts = shlex.split(test_command)
    if "pytest" not in parts:
        return []
    return parts[: parts.index("pytest") + 1] + ["--version"]


class VerifyReportProbeTests(unittest.TestCase):
    def test_the_skill_derives_the_probe_from_the_manifest(self):
        """Matched on the instruction, not on the mention.

        The replacement text still names `python3 -m pytest --version` — as the
        thing not to do — so a bare "this string is absent" check would fail on
        correct content and pass on a rewrite that dropped the reasoning.
        """
        text = VERIFY_REPORT.read_text(encoding="utf-8")
        self.assertNotIn("For pytest-based `test_command`, also run", text)
        self.assertIn("truncated at its `pytest` token", text)

    def test_the_declared_command_truncates_to_a_runnable_probe(self):
        command = declared_test_command()
        self.assertTrue(command, "manifest declares no test_command")
        probe = runner_probe(command)
        self.assertTrue(probe, f"no pytest token in {command!r}")
        self.assertEqual(probe[-1], "--version")
        self.assertEqual(probe[-2], "pytest")
        # The load-bearing one: truncation must drop the suite arguments. A
        # length comparison would not say this — it holds trivially whatever
        # truncation does, and would go red on a `test_command` that simply
        # ends at `pytest`, which is correct input.
        self.assertNotIn("tests/", probe, "the probe must not run the suite")

        proc = subprocess.run(
            probe, cwd=str(ROOT), capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("pytest", proc.stdout.lower())

    # Deliberately no test asserting `python3 -m pytest --version` fails here.
    # It does fail from a plain shell — measured 2026-09-18, in the coordinator
    # shell and a subagent shell — which is the hazard this change addresses.
    # But as a subprocess of pytest it inherits the venv and exits 0, so that
    # assertion would encode the runner's environment rather than the host's.
    # The evidence lives in the REQ; a premise-guard that reports the opposite
    # of the truth depending on who runs it is worse than no guard.


if __name__ == "__main__":
    unittest.main()
