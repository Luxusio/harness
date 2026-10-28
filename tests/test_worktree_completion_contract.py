"""Published completion policy and real Codex packaging, not agent obedience.

The existing batch_finish tests exercise Git operations. These guards keep the
four completion entrypoints attached to one policy and preserve its destructive
recovery preconditions without implementing a second recovery engine.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHARED = REPO / "plugin/skills/run/worktree-completion.md"
CALLERS = (
    ("plugin/skills/run/SKILL.md", "Phase 5: Close"),
    ("plugin/skills/develop/SKILL.md", "Phase 8: Close and final response"),
    ("plugin-codex/internal-skills/run/SKILL.md", "Phase 5: Close"),
    ("plugin-codex/internal-skills/develop/SKILL.md", "Phase 9: Final verification, install, close, and response"),
)


def pytest_generate_tests(metafunc):
    if "caller" in metafunc.fixturenames:
        metafunc.parametrize("caller,heading", CALLERS, ids=[p for p, _ in CALLERS])


def _text(path):
    return path.read_text(encoding="utf-8")


def _section(text, heading):
    match = re.search(r"^#{2,3} " + re.escape(heading) + r"\n(.*?)(?=^#{2,3} |\Z)", text, re.M | re.S)
    assert match, f"Missing workflow section: {heading}"
    return " ".join(match[1].split())


def _shared(heading):
    return _section(_text(SHARED), heading)


def test_each_close_phase_loads_shared_policy_before_completion(caller, heading):
    phase = _section(_text(REPO / caller), heading)
    root = "${HARNESS_PLUGIN_ROOT}/internal-skills" if caller.startswith("plugin-codex/") else "${CLAUDE_PLUGIN_ROOT}/skills"
    read = f"Read `{root}/run/worktree-completion.md`"
    assert read in phase
    assert phase.index(read) < phase.index("overall completion")
    assert "coordinator" in phase and "disposal" in phase
    assert "Batch leads return" in phase or "batch-lead rules" in phase


def test_closed_resume_routes_to_disposal_without_repeating_lifecycle():
    for caller, _ in (CALLERS[0], CALLERS[2]):
        resume = _section(_text(REPO / caller), "Phase 0: Resume detection")
        assert re.search(r"[Aa]lready closed.*?Phase 5.*?do not repeat close or (?:review|lenses) solely for cleanup", resume)
    ownership = _shared("Ownership and timing")
    assert "Do not repeat close or rerun lenses solely for cleanup" in ownership
    assert "Source changes or integration conflicts still require" in ownership


def test_batch_routes_managed_completion_and_recovery_does_not_forge_closed():
    phase = _section(_text(REPO / "plugin/skills/batch/SKILL.md"), "d) Complete, recover and refill")
    assert 'operation="complete"' in phase
    assert "Confirm through the host that that worker and all nested writers stopped" in phase
    assert "for genuinely closed leads" in phase
    assert "rebase, fast-forward, evidence harvest, worktree removal and `git branch -d`" in phase
    assert "Review the shared `skills/run/worktree-completion.md`" in phase
    assert "before completion" in phase
    assert "never infers closure from narrative PASS, an archive, or a review result alone" in phase
    assert 'operation="recover"' in phase and 'operation="resume"' in phase
    assert "Blocked/failed leads remain retained with exact blockers" in phase
    # Non-managed already-copied recovery still has the same stricter per-change
    # proof; the new managed entry point does not authorize invented closure.
    recovery = _shared("Interrupted or already-copied recovery")
    assert "do not fabricate a closed lead result" in recovery
    assert "edit original task control" in recovery
    assert "Per-change disposition" in recovery
    assert "Fresh independent review and QA" in recovery
    assert "readable, byte-verified archive" in recovery
    assert "exact path inventory and archived bytes" in recovery


def test_normal_route_is_linear_and_preserves_source_evidence():
    phase = _shared("Normal integration and disposal")
    assert phase.index("git -c rebase.updateRefs=false rebase --no-autostash <destination-tip>") < phase.index("git merge --ff-only refs/heads/<source-branch>") < phase.index("git worktree remove <path>") < phase.index("git branch -d <branch>")
    assert "Never create a merge commit or fall back to one" in phase
    assert "never stash, force-push" in phase
    assert "Never use `--force` on this ordinary route" in phase
    assert "forced branch deletion, `git reset --hard`, or `git clean`" in phase
    assert "source and destination branch identities and tips, clean states" in phase
    assert "--untracked-files=all" in phase
    assert "merge commit in the source range needs resolution" in phase
    assert "Inspect ignored content and retain any unique source, evidence or data outside the tree" in phase
    assert "unknown content or nested repository/submodule data must be retained" in phase


def test_only_owned_stopped_work_is_disposed_from_surviving_control_checkout():
    phase = _shared("Ownership and timing")
    assert "created or explicitly adopted for this work" in phase
    assert "Do not adopt every entry in `git worktree list`" in phase
    assert "Unknown ownership, unknown worker state, unrelated worktrees and still-running work must be retained" in phase
    assert "Never unlock a running worker's worktree" in phase
    assert "never integrates or removes its own worktree" in phase
    assert "Perform integration and cleanup from a surviving coordinator checkout" in phase
    assert "preserve original task evidence there" in phase
    assert "Never delete the active lifecycle's control directory or the current working directory" in phase


def test_copied_recovery_requires_destination_proof_not_just_an_archive():
    phase = _shared("Interrupted or already-copied recovery")
    assert "Prefer resuming and finishing in the original worktree" in phase
    assert "name the runtime blocker and retain the source" in phase
    assert "not permission to silently bypass the original lifecycle by copying into main" in phase
    assert "use a separate recovery task" in phase
    assert "do not fabricate a closed lead result" in phase
    assert "edit original task control, or carry original PASS evidence" in phase
    assert "No unintegrated unique source commits" in phase
    assert "Per-change disposition of tracked, staged, deleted and untracked paths" in phase
    assert "destination commit/path containing each intended change, or its explicit authorized omission" in phase
    assert "Fresh independent review and QA must cover the recovered committed result" in phase
    assert "An archive alone or an ancestor branch alone does not prove dirty changes were integrated" in phase


def test_recovery_archive_and_recheck_precede_exact_scoped_cleanup():
    phase = _shared("Interrupted or already-copied recovery")
    archive = phase.index("readable, byte-verified archive outside every tree being removed")
    recheck = phase.index("Immediately before cleanup, recheck unchanged source/destination tips")
    cleanup = phase.index("Only then restore the exact accounted, archived tracked paths")
    assert archive < recheck < cleanup
    assert "including staged/worktree diffs, binary and untracked contents" in phase
    assert "non-cache ignored evidence" in phase
    assert "Verify names, contents and symlink targets without following links" in phase
    assert "Never overwrite a different archive" in phase
    assert "Keep sensitive recovery data local and gitignored; do not print or stage it" in phase
    assert "exact path inventory and archived bytes" in phase
    assert "A new file or changed byte invalidates the cleanup decision. Retain and reassess" in phase
    assert "remove the exact accounted, archived untracked paths; do not clear anything else" in phase
    assert "Keep the archive after cleanup" in phase
    assert "never report an attempted removal as successful" in phase


def test_refused_cleanup_retains_honest_remaining_work_report():
    normal = _shared("Normal integration and disposal")
    assert "On removal refusal, restore the original lock if the tree remains" in normal
    assert "report lock-restoration failure too" in normal
    assert "report only the branch as retained, not a nonexistent worktree" in normal
    report = _shared("Completion report")
    assert "retained path/branch, exact blocker and next action" in report
    assert "Check the registered worktree list and branch existence to substantiate removal" in report
    assert "Do not claim overall completion while owned cleanup remains unresolved" in report


def test_real_codex_payload_projects_policy_and_resolves_caller_references(tmp_path):
    spec = importlib.util.spec_from_file_location("harness_install_worktree_contract", REPO / "install.py")
    install = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = install
    spec.loader.exec_module(install)
    target = tmp_path / "harness"
    install._build_codex_payload(target, target)

    # The durable wrapper and its integration/harvest dependencies ship in the
    # real payload, with the same implementation exercised by the Git tests.
    for script in ("batch_state.py", "batch_worktrees.py", "batch_pending.py", "batch_finish.py", "batch_harvest.py", "batch_preflight.py", "batch_submodules.py"):
        assert (target / "scripts" / script).read_bytes() == (REPO / "plugin/scripts" / script).read_bytes()

    shared = target / "internal-skills/run/worktree-completion.md"
    expected = _text(SHARED).replace("${CLAUDE_PLUGIN_ROOT}", "${HARNESS_PLUGIN_ROOT}")
    assert _text(shared) == expected
    assert "${CLAUDE_PLUGIN_ROOT}" not in _text(shared)
    helper = re.search(r"`\$\{HARNESS_PLUGIN_ROOT\}/(scripts/batch_state\.py)`", _text(shared))
    assert helper and (target / helper[1]).is_file()
    for caller, heading in CALLERS[2:]:
        phase = _section(_text(target / caller.removeprefix("plugin-codex/")), heading)
        reference = re.search(r"Read `\$\{HARNESS_PLUGIN_ROOT\}/([^`]+)`", phase)
        assert reference, caller
        assert (target / reference[1]).resolve() == shared.resolve()
        assert (target / reference[1]).is_file()


def test_single_force_exception_requires_state_managed_s1_preservation_and_archive():
    exception = _shared("State-managed S1 disposal exception")
    assert "Only `batch_state.py finish`" in exception
    assert "single `git worktree remove --force`" in exception
    assert "sole owner, `batch_harvest.py`" in exception
    for proof in ("exact durable batch/module identities", "integration checkpoint",
                  "byte-verified task archive", "refs/tags/reflogs/history",
                  "complete closure", "unchanged inventory immediately before removal",
                  "tracked, staged, untracked and ignored content"):
        assert proof in exception
    assert "Unknown data" in exception and "retain the tree" in exception
    assert "Restore the original lock and bootstrap marker on refusal" in exception
    assert "branch deletion remains `-d`" in exception
    assert "No standalone helper, lead or copied-recovery route inherits this exception" in exception
    assert "never invoke force manually" in exception
    batch = _section(_text(REPO / "plugin/skills/batch/SKILL.md"),
                     "d) Complete, recover and refill")
    assert "No manual force removal" in batch
    assert "Selected S1 preservation, reconciliation and guarded disposal remain exclusively owned by the existing helpers" in batch
    assert "worktree-completion.md" in batch


def test_copied_recovery_cannot_inherit_selected_module_force_permission():
    recovery = _shared("Interrupted or already-copied recovery")
    assert "ordinary no-force removal" in recovery
    assert "S1 exception does not authorize copied-recovery disposal" in recovery
    assert "No unintegrated unique source commits" in recovery
    assert "Fresh independent review and QA" in recovery
