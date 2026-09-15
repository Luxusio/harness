import json
import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CLAUDE_DEVELOP = REPO / "plugin" / "skills" / "develop" / "SKILL.md"
PARALLEL_FANOUT = REPO / "plugin" / "skills" / "develop" / "parallel-fanout.md"
CODEX_DEVELOP = REPO / "plugin-codex" / "internal-skills" / "develop" / "SKILL.md"
CONTRACTS = REPO / "CONTRACTS.md"
AC_WORKER = REPO / "plugin" / "agents" / "ac-worker.md"
CODEX_PLUGIN = REPO / "plugin-codex" / ".codex-plugin" / "plugin.json"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(body: str) -> str:
    return " ".join(body.lower().split())


def _policy_text(body: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", body.lower()).split())


REVIEW_DEPTH_TERMS = {
    "precedence": (
        "explicit deep",
        "forced-deep",
        "missing",
        "stale evidence",
        "light proof",
        "standard",
    ),
    "tiers": (
        "light",
        "zero hunters",
        "standard",
        "exactly one",
        "deep",
        "both",
        "hunters",
    ),
    "standard choice": (
        "correctness",
        "contract/test",
        "both material",
        "unresolved",
        "fallback",
    ),
    "attempt and recovery": (
        "increase",
        "attempt",
        "resume",
        "recompute",
        "current evidence",
        "receipts",
    ),
    "formal authority": (
        "fresh formal",
        "full",
        "only",
        "review-code",
        "hunters",
        "receipts",
    ),
    "security and qa": (
        "security",
        "receives no",
        "data",
        "qa",
        "formal",
        "pass",
    ),
}


def _assert_review_depth_contract(body: str, path: Path) -> None:
    normalized = _policy_text(body)
    for label, terms in REVIEW_DEPTH_TERMS.items():
        missing = [term for term in terms if _policy_text(term) not in normalized]
        assert not missing, f"{path}: {label} missing terms {missing!r}"


def _assert_relational_review_policy(body: str, path: Path) -> None:
    """Assert tier inputs map to outputs, not just that both nouns exist."""
    policy = _policy_text(body)
    required_relations = (
        "explicit deep",
        "selects deep" if "selects **deep**" in body.lower() else "risk deep",
        "missing unreadable incomplete or stale evidence",
        "conceal a forced deep predicate",
        "light requires complete positive proof"
        if "requires complete positive proof" in policy
        else "complete positive proof",
        "remaining case is standard"
        if "remaining case is" in policy
        else "remaining work standard",
        "correctness hunter" if "correctness hunter" in policy else "risk selects defect hunter correctness",
        "contract test hunter"
        if "contract test hunter" in policy
        else "risk selects defect hunter contract tests",
        "both material or either domain unresolved"
        if "either domain unresolved" in policy
        else "both material or either unresolved",
        "deterministic standard fallback",
        "light starts zero hunters" if "light starts" in policy else "light zero hunters",
        "standard starts exactly the selected hunter"
        if "standard starts" in policy
        else "standard exactly the selected fresh hunter",
        "deep starts both hunters" if "deep starts" in policy else "deep both fresh hunters",
        "always spawn exactly one fresh"
        if "always spawn" in policy
        else "spawn exactly one fresh formal code reviewer",
        "full sweep formal",
        "attempt depth can only increase"
        if "can only increase" in policy
        else "never decrease the selected depth",
        "resume recovery recompute",
        "never restores depth from"
        if "never restores" in policy
        else "do not read or reconstruct depth from",
    )
    missing = [relation for relation in required_relations if relation not in policy]
    assert not missing, f"{path}: missing relational review rules {missing!r}"

    # Keep precedence relational: the forced predicate list belongs to the
    # DEEP clause, rather than appearing elsewhere near a contradictory tier.
    forced_clause = policy.split("missing unreadable incomplete or stale evidence", 1)[0]
    assert "explicit deep" in forced_clause
    assert "deep" in forced_clause
    assert "standard" not in forced_clause

    evidence_start = policy.index("missing unreadable incomplete or stale evidence")
    light_start = policy.index("complete positive proof", evidence_start)
    evidence_clause = policy[evidence_start:light_start]
    assert "deep" in evidence_clause
    assert "standard" not in evidence_clause

    remaining_start = policy.index("after complete inspection", light_start)
    remaining_clause = policy[remaining_start:policy.index("for standard", remaining_start)]
    assert "standard" in remaining_clause

    # Every rebase element is conjunctive inside the `LIGHT only` proof block.
    rebase = policy.split("a rebase", 1)[1].split("fan out is exact", 1)[0]
    assert "light only" in rebase or "qualifies for light only" in rebase
    for predicate in (
        "old base", "old tip", "new base", "new tip", "conflict free",
        "no manual resolution", "one to one patch equivalence", "no added",
        "dropped", "split", "combined", "reordered", "modified patch",
        "no overlap", "head new tip", "clean", "accounted for",
    ):
        assert predicate in rebase, f"{path}: rebase-LIGHT does not require {predicate!r}"
    assert "missing proof rejects rebase light" in rebase

    fanout = policy.split("fan out is exact", 1)[1]
    light_start = fanout.index("light")
    standard_start = fanout.index("standard", light_start)
    deep_start = fanout.index("deep", standard_start)
    light_route = fanout[light_start:standard_start]
    standard_route = fanout[standard_start:deep_start]
    deep_route = fanout[deep_start:]
    assert "zero hunters" in light_route
    assert "both hunters" not in light_route
    assert "correctness hunter" not in light_route
    assert "contract test hunter" not in light_route
    assert "exactly the selected" in standard_route
    assert "zero hunters" not in standard_route
    assert "both hunters" not in standard_route
    assert "both" in deep_route and "hunters" in deep_route
    assert "zero hunters" not in deep_route

    if "then one fresh full sweep formal code reviewer" in light_route:
        for route in (light_route, standard_route, deep_route):
            assert route.count("one fresh full sweep formal code reviewer") == 1
    else:
        # Codex states the common convergence once, immediately after fan-out.
        assert fanout.count("always spawn exactly one fresh") == 1
        assert fanout.index("always spawn exactly one fresh") > deep_start


def test_claude_develop_requires_lane_table_before_implementation():
    body = _text(CLAUDE_DEVELOP)

    assert "| AC | Files | Depends on | Lane | Route | Reason |" in body
    assert "`Route` must be one of: `Agent(...)`, `sequential-prelude`, `sequential-dependent`" in body
    assert "Fill the table before editing files" in body
    assert "two or more independent `Agent(...)` rows" in body


def test_claude_develop_forbids_collapsing_independent_acs_into_one_executor():
    body = _text(CLAUDE_DEVELOP)

    # The lane label lives in the prompt, never in `name=`. Passing a display
    # name puts it in the `agentType` position, which loses a lens agent's
    # receipt outright and makes every named lane indistinguishable from a lost
    # one — so the example this file pins must not teach it. See
    # doc/harness/REQ__runtime-surfaces-name-the-actual-blocker.md.
    assert 'Lane <task_id>:AC-001' in body
    assert 'subagent_type="harness:ac-worker"' in body
    assert "Never pass `name=` to a spawned agent" in body
    assert 'Agent(name=' not in body
    assert "Use one Agent per independent AC" in body
    assert "Do not assign multiple independent ACs to one" in body
    assert "Do not edit PROGRESS.md" in body
    assert "For sequential batches, work **one AC at a time**" in body
    assert "1. **One AC at a time**, in order." not in body


def test_parallel_fanout_small_task_skip_requires_evidence_and_coordinator_merge():
    body = _text(PARALLEL_FANOUT)

    assert "Parallel is the default posture" in body
    assert "Mandatory parallel delegation is" in body
    assert "User request is not a condition\nfor parallel routing" in body
    assert "do not reduce worker count for independent ACs" in body
    assert "Executors return status, changed paths, and blockers in their final response" in body
    assert "coordinator" in body and "only writer to PROGRESS.md" in body
    assert "Merge cost controls batch size only" in body
    assert "does not justify collapsing two or more" in body
    assert "For N>4, spawn batches of up to 4" in body
    assert "AC ids, estimated lines, estimated runtime" in body
    assert '`reason:"small-task"`' in body
    assert "this opt-out is disabled" in body


def test_claude_develop_parallelizes_full_suite_verification():
    body = _text(CLAUDE_DEVELOP)
    fanout = _text(PARALLEL_FANOUT)

    assert "full-suite verification MUST be delegated to qa-* agents" in body
    assert "Spawn every applicable lens" in body
    assert "Phase 7 multi-lens QA" in fanout
    assert "Phase 7.7 dogfooder" in fanout
    assert "Spawn every applicable lens in one message" in fanout


def test_contracts_do_not_reintroduce_single_agent_default():
    body = _text(CONTRACTS)

    assert "parallel agents = 1 by default" not in body
    assert "fewer parallel agents" not in body
    assert "Develop fanout is" in body
    assert "plugin/skills/develop/parallel-fanout.md" in body


def test_harness_ac_worker_is_scoped_to_one_ac_and_no_shared_artifacts():
    body = _text(AC_WORKER)

    assert "Implement only the assigned AC or lane" in body
    assert "Do not write `PLAN.md`, `TASK.json`, `RECEIPTS.jsonl`" in body
    assert "or `PROGRESS.md`" in body
    assert "Do not collapse multiple independent ACs into your lane" in body


def test_codex_develop_uses_spawn_agent_lane_analysis_not_sequential_default():
    body = _text(CODEX_DEVELOP)

    assert "Phase 3.0: AC Dependency Analysis" in body
    assert "AC Dependency Analysis (sequential on Codex)" not in body
    assert "| AC | Files | Depends on | Lane | Route | Reason |" in body
    assert "`Route` is `spawn_agent(worker)`" in body
    assert "spawn one worker per" in body
    assert "Use one worker per independent AC" in body
    assert "Do not assign multiple independent ACs to one" in body
    assert "state the fallback in task state or final response" in body
    assert "For sequential batches, work **one AC at a time**" in body
    assert "1. **One AC at a time**, in order." not in body


def test_codex_develop_rejects_user_request_based_parallel_skip():
    body = _text(CODEX_DEVELOP)

    assert "capability-gated, not user-request-gated" in body
    assert "The user does not need to ask for delegation" in body
    assert "`user did not ask for delegation` is an invalid" in body
    assert "`delegation was not requested`" in body
    assert "Do not wait for the user to request delegation" in body
    assert "User request is\nnot a condition for parallel routing" in body
    assert "mandatory capability/task-shape routing" in body


def test_codex_develop_sequential_fallback_requires_skip_evidence_payload():
    body = _text(CODEX_DEVELOP)

    assert "Sequential fallback must" in body
    assert "state `ac_count`" in body
    assert "`conflict` (specific" in body
    assert "`estimated_lines`, `estimated_seconds`" in body
    assert "Valid reasons are only `spawn_agent-unavailable`" in body
    assert "`dependency-conflict`, or `small-task`" in body


def test_codex_review_gate_has_risk_proportional_fanout_before_formal_verifier():
    body = _text(CODEX_DEVELOP)
    _assert_review_depth_contract(body, CODEX_DEVELOP)
    normalized = " ".join(body.split())
    assert "exactly nonempty-string `anchor`, `issue`, and `evidence`" in normalized
    assert "at most 20 objects" in body
    assert "65,536 UTF-8 bytes" in body
    assert "without repairing it or inventing `[]`" in body
    assert "escape literal `<`, `>`, `&`" in body
    assert "`\\u003c`, `\\u003e`, `\\u0026`" in body
    assert "only this full sweep formal reviewer" in _policy_text(body)
    assert "hunters never emit verdicts or receipts" in _normalized(body)
    assert "Every retry uses new task names" in body
    assert "security receives no hunter data" in body


def test_claude_and_codex_review_depth_policies_have_semantic_parity():
    claude = _text(REPO / "plugin" / "skills" / "develop" / "quality-audit-pipeline.md")
    codex = _text(CODEX_DEVELOP)
    _assert_review_depth_contract(claude, CLAUDE_DEVELOP)
    _assert_review_depth_contract(codex, CODEX_DEVELOP)
    _assert_relational_review_policy(claude, CLAUDE_DEVELOP)
    _assert_relational_review_policy(codex, CODEX_DEVELOP)

    # These are the safety-sensitive predicates most likely to drift when the
    # two runtimes are maintained separately. Formatting and explanatory prose
    # may differ, but neither runtime may omit a predicate.
    parity_terms = (
        "trust-boundary",
        "sensitive data",
        "concurrency",
        "migration",
        "durable contract",
        "dependency",
        "installer",
        "hook",
        "lifecycle",
        "manual conflict",
        "semantic range-diff",
        "cross-component",
        "dual-domain",
        "old_base",
        "old_tip",
        "new_base",
        "new_tip",
        "one-to-one patch equivalence",
        "no-overlap",
    )
    for term in parity_terms:
        assert _policy_text(term) in _policy_text(claude), (
            f"Claude review policy missing {term!r}"
        )
        assert _policy_text(term) in _policy_text(codex), (
            f"Codex review policy missing {term!r}"
        )


def test_review_depth_status_and_malformed_hunter_contract_are_visible():
    for path in (
        REPO / "plugin" / "skills" / "develop" / "quality-audit-pipeline.md",
        CODEX_DEVELOP,
    ):
        body = _policy_text(_text(path))
        alternatives = (
            ("tier", "depth"),
            "hunter set",
            "concrete reason",
            ("formal review remains mandatory", "full formal review remains mandatory"),
            ("old tier", "old depth"),
            ("new tier", "new depth"),
            "trigger",
            "missing",
            "malformed",
            "unavailable",
            ("inventing `[]`", "convert it to `[]`", "fabricate candidates"),
        )
        for required in alternatives:
            choices = required if isinstance(required, tuple) else (required,)
            assert any(_policy_text(term) in body for term in choices), (
                f"{path}: missing observable/failure contract {choices!r}"
            )


def test_codex_review_depth_route_is_coupled_to_advanced_cachebuster():
    manifest = json.loads(_text(CODEX_PLUGIN))
    version = manifest["version"]
    assert version >= "2.3.0+codex.20260915013000"
    assert "Independent Code Review Gate" in _text(CODEX_DEVELOP)
    assert "LIGHT starts zero hunters" in _text(CODEX_DEVELOP)


def test_coordinator_review_precedes_generic_parallel_failure_retry():
    required = (
        "needs-coordinator-review",
        "before generic rollback",
        "never retry",
        "same ownership",
        "reassign ownership",
        "amend",
        "escalate",
    )
    for path in (CLAUDE_DEVELOP, CODEX_DEVELOP):
        body = " ".join(_text(path).lower().split())
        for fragment in required:
            assert fragment in body, f"{path}: missing {fragment!r}"


def test_codex_worker_prompt_produces_coordinator_review_status():
    body = " ".join(_text(CODEX_DEVELOP).lower().split())

    assert "read `${harness_plugin_root}/agents/developer.md`" in body
    assert "return the exact status `needs-coordinator-review`" in body
    assert "ownership, lane, or approved scope" in body


def test_coordinator_review_keeps_successful_independent_siblings_promoted():
    for path in (CLAUDE_DEVELOP, CODEX_DEVELOP):
        body = " ".join(_text(path).lower().split())
        assert "keep successful independent siblings promoted" in body
