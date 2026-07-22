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
        "checks.yaml",
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
    assert "QA must start after the latest review PASS" in audit
    assert "200+ lines" not in audit


def test_design_maps_agent_behaviors_to_reference_projects():
    design = _text("doc/designs/minimal-implementer-and-code-review-gate.md")
    assert "Agent behavior provenance" in design
    assert "Ponytail `skills/ponytail/SKILL.md`" in design
    assert "gstack `ship/SKILL.md`" in design
    assert "oh-my-claudecode `agents/code-reviewer.md`" in design
