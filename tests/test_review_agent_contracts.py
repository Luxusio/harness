import importlib.util
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CORE_START = "<!-- harness:role-core:start -->"
CORE_END = "<!-- harness:role-core:end -->"


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _role_core(rel: str) -> str:
    body = _text(rel)
    assert body.count(CORE_START) == 1, rel
    assert body.count(CORE_END) == 1, rel
    start = body.index(CORE_START)
    end = body.index(CORE_END) + len(CORE_END)
    return body[start:end]


def _assert_all(body: str, fragments: tuple[str, ...], path: str) -> None:
    lowered = " ".join(body.lower().split())
    for fragment in fragments:
        normalized = " ".join(fragment.lower().split())
        assert normalized in lowered, f"{path}: missing {fragment!r}"


def test_claude_and_codex_role_cores_are_byte_identical():
    for role in ("developer", "code-reviewer", "security-reviewer"):
        assert _role_core(f"plugin/agents/{role}.md") == _role_core(
            f"plugin-codex/agents/{role}.md"
        )


def test_minimum_sufficient_contract_reaches_every_implementation_role():
    role_paths = (
        "plugin/agents/developer.md",
        "plugin/agents/ac-worker.md",
        "plugin-codex/agents/developer.md",
    )
    for path in role_paths:
        body = _text(path).lower()
        assert "minimum-sufficient" in body or "minimum sufficient" in body, path
        assert "stdlib" in body or "standard library" in body, path
        assert "validation" in body, path
        assert "authorization" in body or "auth" in body, path
        assert "concurren" in body, path
        assert "security" in body, path
    for path in ("plugin/skills/develop/SKILL.md", "plugin-codex/internal-skills/develop/SKILL.md"):
        body = _text(path).lower()
        assert "minimum-sufficient" in body
        assert "agents/developer.md" in body


def test_ac_worker_preserves_ponytail_rules_inside_lane_ownership():
    path = "plugin/agents/ac-worker.md"
    required = (
        "every direct caller",
        "relevant sibling caller",
        "inspection is read-only",
        "writes remain limited to your assigned files",
        "blocker for coordinator review",
        "upstream lane or other assigned prerequisite",
        "needs the behavior now",
        "new package dependency only for a current ac boundary",
        "clearer and safer than a small local implementation",
        "manifest and lockfile are assigned to your lane",
        "needs-coordinator-review",
        "deleting obsolete machinery",
        "boring, clear",
        "data-loss prevention",
        "reproduce the failing behavior before the fix",
        "deliberate known ceiling",
        "concrete condition",
    )
    _assert_all(_text(path), required, path)


def test_developer_core_preserves_ponytail_decision_and_safety_contract():
    paths = ("plugin/agents/developer.md", "plugin-codex/agents/developer.md")
    required = (
        "after you understand",
        "need to exist",
        "already in this codebase",
        "standard library",
        "native platform",
        "already-installed dependency",
        "smallest clear local expression",
        "minimum new code",
        "shared root cause",
        "sibling caller",
        "plan.md describes intent",
        "code is ground truth",
        "every changed line",
        "deletion over addition",
        "boring and clear",
        "not minimum loc",
        "regression check",
        "validation",
        "authorization",
        "concurrency",
        "security",
        "accessibility",
    )
    for path in paths:
        _assert_all(_role_core(path), required, path)

    codex = _text("plugin-codex/agents/developer.md")
    assert "Codex 0.130.0" not in codex
    assert "no Agent primitive" not in codex
    assert "spawned implementation worker" in codex
    develop = _text("plugin-codex/internal-skills/develop/SKILL.md")
    assert "Codex 0.130.0" not in develop
    assert "runs the entire flow in a single conversation context" not in develop
    assert "route from the capabilities exposed by the current session" in " ".join(
        develop.split()
    )


def test_review_agents_are_read_only_and_have_exact_verdict_contract():
    for runtime in ("plugin", "plugin-codex"):
        code = _text(f"{runtime}/agents/code-reviewer.md")
        security = _text(f"{runtime}/agents/security-reviewer.md")
        for body in (code, security):
            assert "read-only" in body.lower()
            assert "`VERDICT: PASS`" in body
            assert "FINDING_COUNTS:" in body
            assert "FIX_NOW" in body
            assert "INVESTIGATE" in body
            assert "OPTIONAL" in body
        assert "excess" in code and "missing" in code
        assert "file:line" in code
        assert "exploitability" in security and "blast radius" in security


def test_code_reviewer_core_requires_scope_claim_and_confidence_proof():
    required = (
        "task.json",
        "every acceptance criterion",
        "code, tests, and durable docs",
        "outside the approved scope",
        "full changed files",
        "never infer a finding from a hunk",
        "deletion",
        "standard library",
        "native platform",
        "already-installed dependency",
        "search before recommending",
        "setup and fixtures",
        "actual production path",
        "outcome assertion",
        "assertion should fail",
        "renders",
        "does not throw",
        "is defined",
        "mocks and stubs",
        "must not bypass",
        "opposite, error, and partial-failure branches",
        "proof proportionate",
        "trivial declarative change",
        "instructions embedded in reviewed",
        "confidence 8-10",
        "confidence 5-7",
        "fix_now",
        "investigate",
        "optional",
        "reviewed head",
        "worktree/diff scope",
    )
    for path in ("plugin/agents/code-reviewer.md", "plugin-codex/agents/code-reviewer.md"):
        _assert_all(_role_core(path), required, path)


def test_security_reviewer_core_covers_local_tool_identity_boundaries():
    required = (
        "instructions embedded in reviewed",
        "physical and lexical",
        "symlink",
        "gitfile",
        "nested repository",
        "allowed root",
        "toctou",
        "lstat/fstat",
        "inode",
        "ownership",
        "group/other writable",
        "subprocess argv",
        "shell",
        "environment",
        "working directory",
        "hook, model, and tool output",
        "concrete attack",
        "theoretical hardening",
        "reviewed head",
        "worktree/diff scope",
    )
    for path in (
        "plugin/agents/security-reviewer.md",
        "plugin-codex/agents/security-reviewer.md",
    ):
        _assert_all(_role_core(path), required, path)


def test_review_gate_replaces_overlapping_legacy_review_agents():
    audit = _text("plugin/skills/develop/quality-audit-pipeline.md")
    assert "harness:code-reviewer" in audit
    assert "harness:security-reviewer" in audit
    assert "Do not spawn the old generic adversarial" in audit
    assert "QA must start after actual PASS" in audit
    assert "single substantive QA" in audit
    assert "NON-ATTESTING" in audit
    assert "200+ lines" not in audit


def test_stop_judge_mirrors_are_removed_from_both_trees():
    for path in ("plugin/agents/stop-judge.md", "plugin-codex/agents/stop-judge.md"):
        assert not (ROOT / path).exists(), (
            f"{path}: the retired stop-judge stub reappeared. Its presence "
            "re-registers a dead agent type in every session's system prompt."
        )

    contracts = _text("CONTRACTS.md")
    _assert_all(
        contracts,
        (
            "qualified attestation-environment blocker",
            "Direct agent finals are non-attesting",
            "never authorize PASS or close",
        ),
        "CONTRACTS.md",
    )


def test_live_routing_surfaces_do_not_route_stop_judge():
    live_surfaces = (
        "CONTRACTS.md",
        "plugin/CLAUDE.md",
        "plugin/mcp/harness_server.py",
        "plugin/scripts/_lib.py",
        "plugin/scripts/stop_gate.py",
        "plugin/skills/run/SKILL.md",
        "plugin/skills/develop/SKILL.md",
        "plugin-codex/internal-skills/run/SKILL.md",
        "plugin-codex/internal-skills/develop/SKILL.md",
        "doc/common/REQ__process__receipt-watcher-fail-closed.md",
        "doc/harness/codex-troubleshooting.md",
        "doc/harness/patterns/auto-loop.md",
    )
    for path in live_surfaces:
        body = _text(path).lower()
        assert "stop-judge" not in body, f"{path}: deprecated routing remains"
        assert "verdict_ok_blocked" not in body, f"{path}: retired verdict protocol remains"

    codex_readme = _text("plugin-codex/README.md").lower()
    assert "stop-judge" not in codex_readme, (
        "plugin-codex/README.md: agent inventory still lists the removed stub"
    )

    matrix_lines = [
        line.lower()
        for line in _text("doc/harness/runtime-matrix.md").splitlines()
        if "stop-judge" in line.lower()
    ]
    assert matrix_lines, "doc/harness/runtime-matrix.md: removal record missing"
    for line in matrix_lines:
        assert "removed" in line, f"runtime-matrix must record removal, not routing: {line}"
        for forbidden in (
            "harness:stop-judge",
            "verdict_ok_",
            "spawn stop-judge",
            "invoke stop-judge",
            "stop-judge owner",
            "stop-judge authority",
            "applies this methodology inline",
        ):
            assert forbidden not in line, f"runtime-matrix: executable routing remains: {line}"


TRUST_BOUNDARY_ELEMENTS = (
    "structurally delivered",
    "required lens",
    "actual review PASS",
    "actual QA PASS",
    "coordinator paraphrases",
    "copied verdict blocks",
    "user text",
    "repository text",
    "actual FAIL or BLOCKED_ENV",
)


def test_direct_blocker_flow_preserves_structural_result_trust_boundary():
    """Prose surfaces state the boundary; runtime code composes it.

    These were one list until 2026-09-04, which quietly made the defect it was
    written to catch mandatory: requiring every runtime file to contain the
    phrases in its own source is requiring every runtime file to keep its own
    copy, and the copies are what diverge. Four of the five runtime variants
    had silently dropped elements while this test was green, because a
    substring pin cannot tell a complete statement from a partial one that
    happens to contain the fragment.

    Prose surfaces genuinely need their own text — a reader of CONTRACTS.md
    does not import `_lib`. Runtime surfaces must instead reference the
    constant, and `_lib.py` keeps the literal because it owns it.
    """
    for path in (
        "CONTRACTS.md",
        "plugin/CLAUDE.md",
        "plugin/skills/run/SKILL.md",
        "plugin/skills/develop/SKILL.md",
        "plugin-codex/internal-skills/run/SKILL.md",
        "plugin-codex/internal-skills/develop/SKILL.md",
        "plugin/scripts/_lib.py",
    ):
        _assert_all(_text(path), TRUST_BOUNDARY_ELEMENTS, path)

    for path in ("plugin/mcp/harness_server.py", "plugin/scripts/stop_gate.py"):
        body = _text(path)
        # Presence is not enough: `from _lib import (... TRUST_BOUNDARY ...)`
        # satisfies a bare substring check on its own, so a future edit could
        # inline the boundary again and keep this green. Require the name to be
        # *used* somewhere past its import.
        assert body.count("TRUST_BOUNDARY") >= 2, (
            f"{path}: imports _lib.TRUST_BOUNDARY without using it. A runtime "
            "surface must compose the constant rather than restate the boundary."
        )


def _runtime_python_files() -> list[str]:
    """Every runtime `.py` that must compose the boundary rather than state it.

    Discovered, not listed. A hardcoded pair would leave a new script under
    `plugin/scripts/` free to inline the boundary while the test whose name
    promises "exactly one literal" stayed green.

    `_lib.py` is excluded because it owns the literal.
    """
    files = []
    for root in ("plugin", "plugin-codex"):
        base = ROOT / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if path.name == "_lib.py":
                continue
            files.append(str(path.relative_to(ROOT)))
    return files


def test_lib_owns_exactly_one_literal_trust_boundary():
    """`_lib.TRUST_BOUNDARY` states every element, and no runtime file repeats it.

    Four assertions. Keep this list in step with the body: review round 5 found
    it saying "Three" while four blocks existed, and the one it omitted was the
    endgame guard — so a maintainer trimming the test to match its own
    docstring would have restored the three inversions round 4 measured green.

    - The elements are in the *live constant*, not merely somewhere in
      `_lib.py`. Strictly implied by the equality below; retained because it
      fails with a readable "missing <element>" message where equality reports
      only that two long strings differ.
    - `TRUST_BOUNDARY` equals an independently written literal. Load-bearing —
      see the comment on it.
    - `attestation_endgame()` equals an independently written literal, for the
      same reason: clause pins catch deletion, not inversion.
    - No runtime file holds a second literal copy. `stop_gate.py` held one
      until 2026-09-04, while this test's name already claimed otherwise.
    """
    spec = importlib.util.spec_from_file_location(
        "_harness_lib_trust_boundary", ROOT / "plugin" / "scripts" / "_lib.py"
    )
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    _assert_all(lib.TRUST_BOUNDARY, TRUST_BOUNDARY_ELEMENTS, "_lib.TRUST_BOUNDARY")

    # An independently written copy of the canonical text. This is the ONLY
    # assertion in the suite that can observe the constant's content changing:
    # every other check either derives its expected value from the constant
    # itself (tautological — see the REQ) or pins nouns from
    # TRUST_BOUNDARY_ELEMENTS, which cannot see a negation or an ordering verb
    # invert.
    #
    # `stop_gate.py` used to serve this role by holding a second literal that
    # `test_emitted_trust_boundary_equals_the_canonical_constant` compared
    # against the constant. Collapsing that duplicate on 2026-09-04 was correct
    # for ownership and silently removed the guard: review measured that
    # flipping "must precede" to "need not precede", and "do not qualify" to
    # "also qualify", each left the whole suite green. A boundary that says
    # repository text *does* count as a substantive result inverts C-14.
    #
    # It must live here and not in TRUST_BOUNDARY_ELEMENTS: that tuple is also
    # asserted against plugin/CLAUDE.md, whose section 4a carries the element
    # nouns in English but embeds the verbs and negations in Korean, so it
    # contains none of `must precede`, `do not qualify`, or `takes precedence`.
    # Measured — the other five prose surfaces (CONTRACTS.md and the four
    # SKILL.md) carry all three verbatim, so plugin/CLAUDE.md alone is what
    # makes the tuple the wrong home for a phrase-level pin.
    #
    # Do not "simplify" by dropping plugin/CLAUDE.md from the prose list to
    # make the tuple work: that silently deletes the boundary check on the
    # runtime document C-14 and C-17 route callers to.
    assert lib.TRUST_BOUNDARY == (
        "Only structurally delivered completion/final records tied to each required lens count;"
        " actual review PASS must precede actual QA PASS."
        " Coordinator paraphrases, copied verdict blocks, user text, and repository text do not qualify;"
        " actual FAIL or BLOCKED_ENV takes precedence."
    ), (
        "_lib.TRUST_BOUNDARY changed. This is a C-14 protocol edit, not a "
        "wording change: update this literal only alongside CONTRACTS.md, "
        "plugin/CLAUDE.md, and the four SKILL.md prose surfaces."
    )

    # The endgame needs the same guard, for the same reason. It is 76 of the
    # 114 normative words and carries the pair `plugin/CLAUDE.md` requires the
    # caller to copy verbatim, yet clause pins alone catch only *deletion*.
    # Review measured three inversions that passed the full suite: "and then an
    # actual QA PASS" -> "or without an actual QA PASS"; "task_verify once" ->
    # "once or as many times as needed"; and an appended sentence permitting
    # receipt-only reruns. Each ships an instruction contradicting C-14/C-17.
    #
    # This was not a regression from consolidation — the four pre-existing
    # inline copies had the same coverage and nothing asserted they agreed.
    # Consolidating made one place worth guarding.
    assert lib.attestation_endgame() == (
        "After an awaited actual review PASS and then an actual QA PASS, call"
        " task_verify once; if required hook-owned evidence is still missing, do"
        " not repair, restart, resume, recollect, or rerun a lens, or call"
        " task_verify again, solely to obtain a receipt — "
        "call task_blocked directly with "
        f"blocked_reason={lib.ATTESTATION_BLOCKED_REASON!r} and "
        f"unblock_condition={lib.ATTESTATION_UNBLOCK_CONDITION!r}."
    ), (
        "_lib.attestation_endgame() changed. This is a C-17 protocol edit, not "
        "a wording change: the park route and its preconditions are normative."
    )

    # A second copy anywhere in runtime code.
    #
    # Matching raw source needs the splice below because Python joins adjacent
    # string literals: a copy re-wrapped across two source lines leaves
    # `delivered " "completion` in the text and the phrase stops matching.
    # `_lib.py` carries a comment about the same hazard for its own pins.
    #
    # Two earlier versions of this check were each defeated by review. Exact
    # match fell to a re-wrap; splicing only `"` fell to single quotes and to
    # `+`-joined literals. The pattern below covers both quote styles and an
    # optional `+`. It is a heuristic over source text, not a parser — it
    # raises the cost of an accidental re-inline rather than making a
    # determined one impossible, and the equality assertion above is what
    # actually protects the constant's content.
    marker = "only structurally delivered completion/final records"
    for path in _runtime_python_files():
        spliced = re.sub(r"""["']\s*\+?\s*["']""", "", _text(path).lower())
        assert marker not in " ".join(spliced.split()), (
            f"{path}: holds a second literal copy of the trust boundary. "
            "Runtime code composes _lib.TRUST_BOUNDARY; only _lib.py owns text."
        )


def test_missing_attestation_pair_has_exactly_one_authoritative_location():
    """The fixed pair lives in `_lib.py` only; prose points at the runtime message.

    A hand-copied literal that drifts by one character silently misroutes
    `task_blocked`, so prose surfaces must reference the runtime-delivered pair
    instead of carrying a second copy.
    """
    fixed = (
        "Required hook-owned review/QA attestation remains missing after substantive "
        "review PASS, QA PASS, and one fresh task_verify.",
        "Run a fresh attested review-then-QA evidence generation when the operator chooses to resume.",
    )

    spec = importlib.util.spec_from_file_location(
        "_harness_lib_attestation", ROOT / "plugin" / "scripts" / "_lib.py"
    )
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    assert (lib.ATTESTATION_BLOCKED_REASON, lib.ATTESTATION_UNBLOCK_CONDITION) == fixed, (
        "plugin/scripts/_lib.py no longer owns the fixed pair"
    )

    # The runtime must still deliver the pair at the decision point, otherwise
    # dropping the prose copies would strand the caller.
    #
    # `attestation_endgame` joined this list on 2026-09-04: `harness_server.py`
    # stopped interpolating the pair directly and now composes the endgame,
    # which carries it. Without that name the check was passing on nothing but
    # a vestigial `from _lib import (... ATTESTATION_BLOCKED_REASON ...)` line
    # — the same "an unused import satisfies a substring check" hole closed for
    # TRUST_BOUNDARY in this file with a count-based assertion.
    delivering = (
        "ATTESTATION_BLOCKED_REASON",
        "attestation_block_instruction",
        "attestation_endgame",
    )
    for path in ("plugin/mcp/harness_server.py", "plugin/scripts/stop_gate.py"):
        # Comments stripped first. A count over the raw file is satisfied by
        # the import line plus any prose mentioning the name — including the
        # comment on `harness_server.py`'s own import explaining that these are
        # a test-facing re-export. Review measured that: deleting both live
        # compositions left this assertion green. Counting code only is what
        # makes "past its import" true rather than aspirational.
        body = "\n".join(
            line for line in _text(path).splitlines()
            if not line.lstrip().startswith("#")
        )
        assert any(body.count(name) >= 2 for name in delivering), (
            f"{path}: no longer emits the fixed pair to the caller. It must "
            f"reference one of {', '.join(delivering)} in code past its import."
        )

    prose_surfaces = (
        "CONTRACTS.md",
        "plugin/CLAUDE.md",
        "plugin/skills/run/SKILL.md",
        "plugin/skills/develop/SKILL.md",
        "plugin-codex/internal-skills/run/SKILL.md",
        "plugin-codex/internal-skills/develop/SKILL.md",
        "doc/common/REQ__process__receipt-watcher-fail-closed.md",
        "doc/harness/codex-troubleshooting.md",
    )
    for path in prose_surfaces:
        body = _text(path)
        normalized = " ".join(body.split())
        for literal in fixed:
            assert " ".join(literal.split()) not in normalized, (
                f"{path}: second copy of the fixed attestation pair. It is owned "
                "by plugin/scripts/_lib.py and delivered in the task_verify "
                "next_action; reference that instead of copying it."
            )
        _assert_all(body, ("_lib.py", "task_verify"), path)


def test_design_maps_agent_behaviors_to_reference_projects():
    design = _text("doc/designs/minimal-implementer-and-code-review-gate.md")
    assert "Agent behavior provenance" in design
    assert "Ponytail `skills/ponytail/SKILL.md`" in design
    assert "gstack `ship/SKILL.md`" in design
    assert "oh-my-claudecode `agents/code-reviewer.md`" in design
