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


def _normalized(body: str) -> str:
    return " ".join(body.lower().split())


def _assert_policy_terms(body: str, groups: dict[str, tuple[str, ...]], path: str) -> None:
    """Pin policy concepts while allowing harmless Markdown/prose reflow."""
    normalized = " ".join(re.sub(r"[-_/*`]+", " ", body.lower()).split())
    for label, terms in groups.items():
        missing = [
            term
            for term in terms
            if " ".join(re.sub(r"[-_/*`]+", " ", term.lower()).split()) not in normalized
        ]
        assert not missing, f"{path}: {label} missing terms {missing!r}"


def test_claude_and_codex_role_cores_are_byte_identical():
    for role in ("developer", "defect-hunter", "code-reviewer", "security-reviewer"):
        assert _role_core(f"plugin/agents/{role}.md") == _role_core(
            f"plugin-codex/agents/{role}.md"
        ), (
            f"{role} role core drifted between runtimes; after changing the "
            "developer core, also run tests/evals/developer_behavior/run_live.py"
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


DEVELOP_SKILLS = (
    "plugin/skills/develop/SKILL.md",
    "plugin-codex/internal-skills/develop/SKILL.md",
)
DEVELOPER_CORES = ("plugin/agents/developer.md", "plugin-codex/agents/developer.md")


def test_developer_core_output_contract_and_dependency_admission():
    for path in DEVELOPER_CORES:
        core = _role_core(path)
        _assert_all(
            core,
            (
                "Status: implemented | blocked | needs-coordinator-review",
                "Known ceiling: <ceiling> — upgrade when <trigger>",
                "Assumption: <choice> — because <PLAN/code evidence>",
                "omit the line when there is none",
                "coordinator treats both forms as equivalent",
                "No impossible-state defenses",
                "Admit a new package only when a current AC needs it",
                "package manifest and lockfile are within your ownership",
            ),
            path,
        )
    worker = _text("plugin/agents/ac-worker.md")
    _assert_all(
        worker,
        (
            "AC-003: implemented | blocked | needs-coordinator-review",
            "Known ceiling: <ceiling> — upgrade when <trigger>",
            "Assumption: <choice> — because <PLAN/code evidence>",
        ),
        "plugin/agents/ac-worker.md",
    )


def test_developer_core_splits_blocking_from_defaultable_ambiguity():
    for path in DEVELOPER_CORES:
        core = _role_core(path)
        _assert_all(
            core,
            (
                "PLAN.md describes intent; the code is ground truth",
                "It is blocking when a wrong guess would change the outcome, scope, safety, or external state",
                "Otherwise it is defaultable",
            ),
            path,
        )
        assert "surface the conflict and your assumptions before implementing" not in _normalized(core)
    _assert_all(
        _text("plugin/agents/ac-worker.md"),
        (
            "An ambiguity is blocking when a wrong guess would change the outcome, scope, safety, or external state and neither PLAN.md nor the code settles it",
            "Otherwise it is defaultable",
        ),
        "plugin/agents/ac-worker.md",
    )
    for path in DEVELOP_SKILLS:
        _assert_all(
            _text(path),
            (
                "Ambiguity is blocking when PLAN.md and the code cannot settle it",
                "Decide defaultable ambiguity yourself, report it as `Assumption:`",
            ),
            path,
        )


def test_claude_sequential_coordinator_reads_developer_core_before_editing():
    claude = _text("plugin/skills/develop/SKILL.md")
    _assert_all(
        claude,
        (
            "Before the first sequential edit, the coordinator Reads the role core of `${CLAUDE_PLUGIN_ROOT}/agents/developer.md`",
            "parent context is not propagation",
            "AC workers carry their own ladder copy in `plugin/agents/ac-worker.md`",
        ),
        "plugin/skills/develop/SKILL.md",
    )
    assert "Workers read `plugin/agents/developer.md`" not in claude
    _assert_all(
        _text("plugin-codex/internal-skills/develop/SKILL.md"),
        ("read that file before editing; parent context is not sufficient",),
        "plugin-codex/internal-skills/develop/SKILL.md",
    )
    for path in (*DEVELOP_SKILLS, "plugin/skills/develop/quality-audit-pipeline.md"):
        _assert_all(
            _text(path),
            ("the lane-owning worker, or the coordinator for sequential ACs",),
            path,
        )


def test_known_ceilings_are_promoted_to_a_greppable_doc_ledger_before_close():
    for path in DEVELOP_SKILLS:
        body = _text(path)
        _assert_all(
            body,
            (
                "Copy every reported `Known ceiling:` line under a `Known ceiling` heading",
                "never copy an absent ceiling",
                "Required when a `Known ceiling:` was reported",
            ),
            path,
        )
        phase_86 = body[body.index("### Phase 8.6"):body.index("### Phase 8.7")]
        assert phase_86.index("Known ceiling") < phase_86.index('wbs(action="verify")'), path


def test_develop_tests_are_proportional_to_changed_behavior():
    for path in DEVELOP_SKILLS:
        body = _text(path)
        _assert_all(
            body,
            (
                "modifies existing non-trivial behavior",
                "a trivial declarative or one-line change needs no new test",
                "Phase 6 owns commit order",
                "add tests only for uncovered non-trivial changed behavior",
            ),
            path,
        )
        assert "Commit separately: `test: regression test for" not in body, path


def test_fix_first_is_a_self_check_not_a_code_growth_table():
    develop = ROOT / "plugin/skills/develop"
    fix_first = _text("plugin/skills/develop/fix-first-pattern.md")
    _assert_all(
        fix_first,
        (
            "Pre-review self-check",
            "3-Attempt Escalation Rule",
            "Do not add an `attempts` key to canonical PROGRESS.md",
            "hypothesis-driven-debugging.md",
        ),
        "plugin/skills/develop/fix-first-pattern.md",
    )
    for stale in ("AUTO-FIX", "Add standard pattern", "Extract to named constant"):
        assert stale not in fix_first, stale
    for path in DEVELOP_SKILLS:
        line = next(l for l in _text(path).splitlines() if "**3.6 Fix-first pattern**" in l)
        for stale in ("magic numbers", "missing guards", "AUTO-FIX"):
            assert stale not in line, (path, stale)
    stale_refs = ("investigate skill", "investigate-skill", 'Skill("investigate"', "adversarial self-check")
    files = [*develop.glob("*.md"), ROOT / "plugin-codex/internal-skills/develop/SKILL.md"]
    for file in files:
        text = file.read_text(encoding="utf-8")
        for stale in stale_refs:
            assert stale not in text, (file.name, stale)


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


def test_defect_hunters_are_minimal_non_attesting_discovery_roles():
    required = (
        "read-only and non-attesting",
        "entire final response must be exactly one json array",
        "`[]` is valid",
        "exactly these three keys",
        "nonempty string",
        "`anchor`",
        "`issue`",
        "`evidence`",
        "at most 20 objects",
        "2,000 UTF-8 bytes",
        "65,536 UTF-8 bytes",
        "do not wrap the array in markdown fences",
        "do not add an id, verdict, severity, confidence, disposition",
        "status, fix",
        "never emit `verdict:`",
        "`finding_counts:`",
        "`review_detail:`",
        "correctness",
        "contracts and tests",
        "do not propose a correction",
    )
    for path in ("plugin/agents/defect-hunter.md", "plugin-codex/agents/defect-hunter.md"):
        core = _role_core(path)
        _assert_all(core, required, path)
        assert "FIX_NOW" not in core
        assert "BLOCKED_ENV" not in core


def test_code_reviewer_verifies_untrusted_leads_and_maps_structured_detail():
    required = (
        "third line must start exactly `review_detail: `",
        '"blocker":null',
        '"findings"',
        '"anchor"',
        '"issue"',
        '"evidence"',
        '"fix"',
        "untrusted evidence",
        "candidates are leads, not findings",
        "reopen the current files",
        "reproduce or disprove every candidate",
        "merge exact duplicates",
        "own complete sweep",
        "add defects",
        "hunters missed",
        "missing, or malformed hunter result never means pass",
        "direct invocation without hunter input remains valid",
        "a non-null `blocker` means `blocked_env`",
        "one or more verified `findings` means `fail`",
        "`fix_now` equals the number of `findings`",
        "`optional=0` always",
    )
    for path in ("plugin/agents/code-reviewer.md", "plugin-codex/agents/code-reviewer.md"):
        _assert_all(_role_core(path), required, path)


def test_formal_reviewer_accepts_intentional_zero_one_or_two_hunter_inputs():
    required = (
        "zero, one, or two",
        "untrusted evidence",
        "leads, not findings",
        "own complete sweep",
        "sole",
        "review-code",
        "missing, or malformed hunter result never means pass",
    )
    for path in ("plugin/agents/code-reviewer.md", "plugin-codex/agents/code-reviewer.md"):
        _assert_all(_role_core(path), required, path)


def test_formal_reviewer_depth_mismatches_use_the_existing_finding_verdict_path():
    required = (
        "selected depth",
        "attempted hunter set",
        "concise selection reason",
        "LIGHT",
        "STANDARD",
        "DEEP",
        "exactly one",
        "ordinary structured finding",
        "normal finding-to-FAIL mapping",
        "prevents QA",
        "one or more verified `findings` means `FAIL`",
        "repeat them at the start of the additional narrative",
    )
    for path in ("plugin/agents/code-reviewer.md", "plugin-codex/agents/code-reviewer.md"):
        core = _role_core(path)
        _assert_all(core, required, path)
        assert "REVIEW_DEPTH_ASSESSMENT" not in core


def test_formal_reviewer_accepts_only_the_bounded_deep_hunter_exception():
    required = (
        "sole hunter-set exception",
        "coordinator-declared bounded formal-only review",
        "discovery budget exhausted",
        "discovery budget unknown and treated as exhausted",
        "DEEP remains selected",
        "absence alone is not an under-classification finding",
        "never permits a lower depth",
        "no persisted state",
    )
    for path in ("plugin/agents/code-reviewer.md", "plugin-codex/agents/code-reviewer.md"):
        _assert_all(_role_core(path), required, path)


def test_formal_reviewer_pins_each_depth_mismatch_and_deep_sufficiency_case():
    relations = (
        "LIGHT requires complete positive low-risk proof",
        "STANDARD requires exactly the correct single hunter for one material domain",
        "both or unresolved domains and every forced DEEP trigger require DEEP with both hunters",
        "selected depth is too low or the STANDARD focus is wrong",
        "At already-selected DEEP, an omitted secondary reason is narrative correction, not another escalation",
    )
    for path in ("plugin/agents/code-reviewer.md", "plugin-codex/agents/code-reviewer.md"):
        core = _normalized(_role_core(path))
        for relation in relations:
            assert relation.lower() in core, f"{path}: missing depth relation {relation!r}"


def test_formal_reviewer_role_knows_every_canonical_forced_deep_predicate():
    forced = (
        "security",
        "trust-boundary",
        "sensitive data",
        "concurrency",
        "migration",
        "public contract",
        "durable contract",
        "dependency",
        "build",
        "installer",
        "hook",
        "lifecycle",
        "gate",
        "manual conflict",
        "semantic range-diff",
        "cross-component",
        "dual-domain",
    )
    for path in ("plugin/agents/code-reviewer.md", "plugin-codex/agents/code-reviewer.md"):
        core = _role_core(path)
        _assert_policy_terms(core, {"forced-DEEP role predicates": forced}, path)
        normalized = _normalized(core)
        start = normalized.index("forced-deep")
        end = normalized.index("concretely", start)
        clause = normalized[start:end]
        for predicate in forced:
            normalized_predicate = " ".join(
                re.sub(r"[-_/*`]+", " ", predicate.lower()).split()
            )
            normalized_clause = " ".join(
                re.sub(r"[-_/*`]+", " ", clause.lower()).split()
            )
            assert normalized_predicate in normalized_clause, (
                f"{path}: {predicate!r} is outside the forced-DEEP validation clause"
            )


def test_standalone_reviewer_has_complete_evidence_light_and_rebase_rules():
    evidence_terms = (
        "missing",
        "unreadable",
        "incomplete",
        "stale evidence",
        "conceal a forced DEEP predicate",
        "requires DEEP",
    )
    light_terms = (
        "bounded single-domain scope",
        "mechanically behavior-preserving",
        "non-executable work",
        "no control-flow",
        "state",
        "data",
        "error",
        "contract",
        "dependency",
        "build",
        "install",
        "hook",
        "lifecycle",
        "gate",
        "security",
        "concurrency",
        "migration behavior change",
        "obvious intent",
        "focused verification",
        "current worktree evidence",
    )
    rebase_terms = (
        "rebase is LIGHT only",
        "old_base",
        "old_tip",
        "new_base",
        "new_tip",
        "conflict-free",
        "without manual resolution",
        "one-to-one patch equivalence",
        "without added",
        "dropped",
        "split",
        "combined",
        "reordered",
        "modified patches",
        "semantic no-overlap",
        "symbols",
        "contracts",
        "dependencies",
        "generated outputs",
        "lifecycle behavior",
        "HEAD equal to new_tip",
        "clean, accounted-for index/worktree",
        "Missing proof rejects rebase-LIGHT",
        "requires DEEP",
    )
    for path in ("plugin/agents/code-reviewer.md", "plugin-codex/agents/code-reviewer.md"):
        core = _role_core(path)
        _assert_all(core, evidence_terms, path)
        _assert_all(core, light_terms, path)
        _assert_all(core, rebase_terms, path)


def test_role_forced_deep_independent_predicates_are_mutation_guarded():
    independent = ("public-contract", "durable-contract", "dependency", "build")

    def assert_independent(clause: str, path: str) -> None:
        for predicate in independent:
            assert clause.count(predicate) == 1, (
                f"{path}: forced-DEEP must list {predicate!r} independently"
            )

    for path in ("plugin/agents/code-reviewer.md", "plugin-codex/agents/code-reviewer.md"):
        core = _role_core(path)
        normalized = _normalized(core)
        start = normalized.index("forced-deep predicates")
        end = normalized.index("treat these as", start)
        clause = normalized[start:end]
        assert_independent(clause, path)
        for predicate in independent:
            mutated = clause.replace(predicate, "predicate-removed", 1)
            try:
                assert_independent(mutated, path)
            except AssertionError:
                pass
            else:
                raise AssertionError(f"{path}: removing {predicate!r} escaped the guard")


def test_rebase_failure_predicates_each_independently_force_deep_everywhere():
    surfaces = (
        "plugin/skills/develop/quality-audit-pipeline.md",
        "plugin-codex/internal-skills/develop/SKILL.md",
        "plugin/agents/code-reviewer.md",
        "plugin-codex/agents/code-reviewer.md",
    )
    failures = ("conflict", "semantic difference", "overlap", "evidence loss")

    def assert_failure_clause(clause: str, path: str) -> None:
        normalized = _normalized(clause)
        for failure in failures:
            assert failure in normalized, f"{path}: rebase failure missing {failure!r}"
        assert "deep" in normalized
        assert "standard" not in normalized

    for path in surfaces:
        body = _text(path)
        normalized = _normalized(body)
        start = normalized.index("missing proof rejects rebase-light")
        if "quality-audit-pipeline" in path:
            end = normalized.index("fan-out is exact", start)
        elif "internal-skills/develop" in path:
            end = normalized.index("fan-out is exact", start)
        else:
            end = normalized.index("if the selected depth", start)
        clause = normalized[start:end]
        assert_failure_clause(clause, path)
        for failure in failures:
            mutated = clause.replace(failure, "failure-removed", 1)
            try:
                assert_failure_clause(mutated, path)
            except AssertionError:
                pass
            else:
                raise AssertionError(f"{path}: removing {failure!r} escaped the guard")

        wrong_tier = clause.replace("requires deep", "requires standard", 1)
        if wrong_tier == clause:
            wrong_tier = clause.replace("selects deep", "selects standard", 1)
        if wrong_tier == clause:
            wrong_tier = clause.replace("-> deep", "-> standard", 1)
        assert wrong_tier != clause, f"{path}: test could not mutate rebase DEEP mapping"
        try:
            assert_failure_clause(wrong_tier, path)
        except AssertionError:
            pass
        else:
            raise AssertionError(f"{path}: rebase failures mapped STANDARD without failure")


def test_rebase_light_positive_proof_is_six_clause_all_of_with_operator_guards():
    surfaces = (
        "plugin/skills/develop/quality-audit-pipeline.md",
        "plugin-codex/internal-skills/develop/SKILL.md",
        "plugin/agents/code-reviewer.md",
        "plugin-codex/agents/code-reviewer.md",
    )

    def positive_proof(body: str) -> str:
        normalized = _normalized(body)
        start = normalized.index("a rebase")
        end = normalized.index("missing proof rejects rebase-light", start)
        return normalized[start:end]

    def assert_all_of(proof: str, path: str) -> None:
        assert "light only" in proof or "qualifies for light only" in proof
        clauses = [clause.strip() for clause in proof.split(";")]
        assert len(clauses) == 6, f"{path}: rebase-LIGHT proof must have six all-of clauses"
        required_by_clause = (
            ("old_base", "old_tip", "new_base", "new_tip"),
            ("conflict-free", "manual resolution"),
            ("one-to-one patch equivalence", "added", "dropped", "split", "combined", "reordered", "modified"),
            ("no-overlap", "symbols", "contracts", "dependencies", "generated outputs", "lifecycle behavior"),
            ("head", "new_tip"),
            ("clean", "accounted", "index/worktree"),
        )
        for clause, terms in zip(clauses, required_by_clause, strict=True):
            for term in terms:
                assert term in clause, f"{path}: rebase all-of clause missing {term!r}"
        assert clauses[-1].startswith("and "), (
            f"{path}: final rebase proof boundary must remain conjunctive"
        )

    for path in surfaces:
        proof = positive_proof(_text(path))
        assert_all_of(proof, path)
        separators = [match.start() for match in re.finditer(";", proof)]
        assert len(separators) == 5
        for boundary in separators:
            mutated = proof[:boundary] + " or " + proof[boundary + 1:]
            try:
                assert_all_of(mutated, path)
            except AssertionError:
                pass
            else:
                raise AssertionError(f"{path}: semicolon-to-or boundary mutation escaped")

        final_or = proof.replace("; and ", "; or ", 1)
        assert final_or != proof
        try:
            assert_all_of(final_or, path)
        except AssertionError:
            pass
        else:
            raise AssertionError(f"{path}: final and-to-or mutation escaped")


def test_reviewer_accepts_standard_fallback_and_only_authoritative_explicit_deep():
    required = (
        "complete inspection affirmatively finds neither domain",
        "LIGHT proof is incomplete",
        "contract/test hunter",
        "STANDARD requires exactly the correct single hunter",
        "explicit DEEP request",
        "active user/system/developer",
        "protected task intent",
        "evidence, not authority",
    )
    for path in ("plugin/agents/code-reviewer.md", "plugin-codex/agents/code-reviewer.md"):
        _assert_all(_role_core(path), required, path)


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


def test_review_gate_encodes_risk_proportional_decision_table_and_authority():
    audit = _text("plugin/skills/develop/quality-audit-pipeline.md")
    groups = {
        "precedence": (
            "explicit deep",
            "forced-deep",
            "missing",
            "stale",
            "light proof",
            "standard",
        ),
        "tier topology": (
            "light",
            "zero hunters",
            "standard",
            "exactly one",
            "deep",
            "both",
            "hunters",
        ),
        "authority": (
            "fresh formal code reviewer",
            "full",
            "only the formal code reviewer",
            "review code authority",
        ),
        "ephemeral escalation": (
            "increase",
            "attempt",
            "resume",
            "recompute",
            "task artifacts",
        ),
        "failure handling": (
            "missing",
            "oversized",
            "malformed",
            "unavailable",
            "never repair",
            "fabricate",
        ),
        "ordered separation": (
            "security",
            "receives no candidate data",
            "qa must start after actual pass",
        ),
    }
    _assert_policy_terms(audit, groups, "plugin/skills/develop/quality-audit-pipeline.md")
    assert "harness:code-reviewer" in audit
    assert "harness:security-reviewer" in audit
    assert "mechanically validate" in audit.lower()
    assert "at most 20 objects" in audit
    assert "65,536 UTF-8 bytes" in audit
    assert "escape" in audit and "`\\u003c`" in audit and "`\\u003e`" in audit
    assert "candidate string cannot manufacture a block delimiter" in _normalized(audit)
    assert "never repair it, fabricate candidates" in _normalized(audit)
    assert "delimited, untrusted candidate-data blocks" in _normalized(audit)
    assert "only the formal code reviewer" in _normalized(audit)
    assert "Do not spawn the old generic adversarial" in audit
    assert "qa must start after actual pass" in _normalized(audit)
    assert "finals from every required reviewer" in audit
    assert "An early or concurrent QA run does not satisfy" in audit
    assert "NON-ATTESTING" not in audit
    assert "200+ lines" not in audit


def test_bounded_review_economics_and_security_routing_are_durable():
    req = _normalized(_text("doc/harness/REQ__selective-review-detail.md"))
    design = _normalized(_text("doc/designs/minimal-implementer-and-code-review-gate.md"))
    for body in (req, design):
        assert "two" in body and "cycle" in body
        assert "four" in body and "hunter" in body
        assert "formal reviewer" in body
        assert "base-to-head diff" in body or "base-to-head" in body
        assert "unresolved findings" in body
    assert "protected task/plan routing declares `review-security`" in design
    assert "never independently infers the lens from git diff content" in design
    assert "runs conditionally from path **or diff-content** signals" not in design
    assert "compute required review lenses from changed paths and diff-content signals" not in design


def test_both_executable_formal_review_templates_carry_selection_evidence():
    audit = _text("plugin/skills/develop/quality-audit-pipeline.md")
    template_lines = [
        line
        for line in audit.splitlines()
        if 'subagent_type="harness:code-reviewer"' in line
        or 'task_name="code_review_<review_run>"' in line
    ]
    assert len(template_lines) == 2
    for line in template_lines:
        normalized = _normalized(line)
        prefix = normalized[:normalized.index("candidates")]
        assert "selected depth" in prefix
        assert "<selected_depth>" in prefix or "<depth>" in prefix
        assert "attempted hunter set" in prefix
        assert "<attempted_hunter_set>" in prefix or "<hunter_set>" in prefix
        assert "concise selection reason" in prefix
        assert "<selection_reason>" in prefix


def test_every_forced_deep_predicate_is_named_independently():
    audit = _text("plugin/skills/develop/quality-audit-pipeline.md")
    forced = (
        "security",
        "trust-boundary",
        "sensitive data",
        "concurrency",
        "migration",
        "public",
        "durable contract",
        "dependency",
        "build",
        "installer",
        "hook",
        "lifecycle",
        "gate",
        "manual conflict",
        "semantic range-diff",
        "cross-component",
        "dual-domain",
    )
    _assert_policy_terms(
        audit,
        {"forced-DEEP predicates": forced},
        "plugin/skills/develop/quality-audit-pipeline.md",
    )


def test_light_rebase_requires_complete_positive_proof():
    audit = _text("plugin/skills/develop/quality-audit-pipeline.md")
    required = (
        "old_base",
        "old_tip",
        "new_base",
        "new_tip",
        "conflict-free",
        "no manual resolution",
        "one-to-one patch equivalence",
        "no added",
        "dropped",
        "split",
        "combined",
        "reordered",
        "modified patch",
        "no-overlap",
        "head",
        "new_tip",
        "clean",
        "accounted for",
    )
    _assert_policy_terms(
        audit,
        {"rebase-LIGHT proof": required},
        "plugin/skills/develop/quality-audit-pipeline.md",
    )


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
            "Actual independent review PASS precedes actual QA",
            "WBS verify/close enforce measured outcome checks",
            "without a replacement ledger",
        ),
        "CONTRACTS.md",
    )


def test_live_routing_surfaces_do_not_route_stop_judge():
    live_surfaces = (
        "CONTRACTS.md",
        "plugin/CLAUDE.md",
        "plugin/mcp/harness_server.py",
        "plugin/scripts/_lib.py",
        "plugin/skills/run/SKILL.md",
        "plugin/skills/develop/SKILL.md",
        "plugin-codex/internal-skills/run/SKILL.md",
        "plugin-codex/internal-skills/develop/SKILL.md",
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



def test_design_maps_agent_behaviors_to_reference_projects():
    design = _text("doc/designs/minimal-implementer-and-code-review-gate.md")
    assert "Agent behavior provenance" in design
    assert "Ponytail `skills/ponytail/SKILL.md`" in design
    assert "gstack `ship/SKILL.md`" in design
    assert "oh-my-claudecode `agents/code-reviewer.md`" in design
