"""Prompt-contract tests for the batch integration `review-code` carry rule.

The post-wave `harness:batch` integration task carries a lead range that its
own lead already reviewed and QA'd when rebase plus fast-forward is proven
patch-equivalent, and selects review depth over the residual only. Selection
is orchestration instruction, not lifecycle code, so these tests pin the
wording relations in the pipeline, the Claude reviewer, and the REQ.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PIPELINE = "plugin/skills/develop/quality-audit-pipeline.md"
REVIEWER = "plugin/agents/code-reviewer.md"
CODEX_REVIEWER = "plugin-codex/agents/code-reviewer.md"
REQ = "doc/harness/REQ__selective-review-detail.md"
HEADING = "### Batch integration review scope"
NEXT_HEADING = "### Bounded discovery retries"
CORE_END = "<!-- harness:role-core:end -->"
CARRY_OPEN = "A lead range is carried, not re-reviewed, only when all of these hold:"
CARRY_CLOSE = "A range that fails any condition is residual."


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _norm(body: str) -> str:
    return " ".join(body.lower().split())


def _policy(body: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", body.lower()).split())


def _section() -> str:
    body = _text(PIPELINE)
    assert body.count(HEADING) == 1, "batch integration section must appear once"
    start = body.index(HEADING)
    return body[start:body.index(NEXT_HEADING, start)]


def _items(block: str, marker: str) -> list[str]:
    """Return normalized list items (with continuation lines) that start with marker."""
    items: list[list[str]] = []
    current: list[str] | None = None
    for line in block.splitlines():
        if re.match(marker, line):
            current = [line]
            items.append(current)
        elif current is not None and line.startswith("  "):
            current.append(line)
        else:
            current = None
    return [_norm(" ".join(item)) for item in items]


def _bullet(section: str, prefix: str) -> str:
    for item in _items(section, r"- "):
        if item.startswith(_norm(prefix)):
            return item
    raise AssertionError(f"missing bullet starting {prefix!r}")


def _assert_carry(section: str) -> None:
    assert CARRY_OPEN in section, "carry must be an explicit all-of list"
    assert CARRY_CLOSE in section
    block = section[section.index(CARRY_OPEN) + len(CARRY_OPEN):section.index(CARRY_CLOSE)]
    conditions = _items(block, r"- ")
    assert len(conditions) == 3, f"carry must have exactly three conditions: {conditions!r}"
    proof, step, archive = conditions
    for term in (
        "rebase-light proof above holds at that lead's fast-forward point",
        "one-to-one patch equivalence",
        "identical ordered `git patch-id --stable` output for both ranges",
        "patch-id column",
        "<old_base>..<old_tip>",
        "<new_base>..<new_tip>",
        "same commit count",
        "only the patch ids are compared",
        "whitespace-sensitive file changed on both sides counts as overlap",
    ):
        assert term in proof, f"proof condition missing {term!r}"
    assert "step d procedure" in step and "rebase never stopped" in step
    assert "not through the step e conflict path" in step
    for term in ("harvested archive", "closed with pass", "closed: true", "no `blocked.md`"):
        assert term in archive, f"archive condition missing {term!r}"
    for condition in (proof, step):
        assert condition.endswith(";"), "non-final carry conditions must stay conjunctive"
        assert not condition.endswith(("; or", " or;", "or")), "carry condition turned disjunctive"
    assert archive.endswith(".")


def _assert_residual(section: str) -> None:
    normalized = _norm(section)
    assert "select depth by the normal precedence above over the residual only" in normalized
    parts = _items(section, r"[0-9]\. ")
    assert len(parts) == 4, f"residual must list four parts: {parts!r}"
    expected = (
        ("1. conflict resolutions", ("step e", "manual-conflict, deep")),
        ("2. cross-lead interactions", ("overlaps what earlier leads changed", "(deep)")),
        ("3. non-carried ranges", ("outside every lead range",)),
        ("4. the integration task's own edits", ()),
    )
    for part, (head, terms) in zip(parts, expected, strict=True):
        assert part.startswith(head), f"residual order broken at {head!r}: {part!r}"
        for term in terms:
            assert term in part, f"{head}: missing {term!r}"
    for part in parts[:2]:
        assert "standard" not in part and "light" not in part
    assert "an empty residual is light" in normalized


def test_section_sits_between_fanout_bullets_and_discovery_retries():
    body = _text(PIPELINE)
    fanout = body.index("Fan-out is exact:")
    deep_bullet = body.index("- **DEEP**: both fresh hunters together", fanout)
    heading = body.index(HEADING)
    retries = body.index(NEXT_HEADING)
    assert fanout < deep_bullet < heading < retries
    between = body[deep_bullet:heading]
    assert "after both attempts finish." in between
    assert between.count("\n- ") == 0, "section must follow the DEEP bullet directly"
    assert "\n#" not in between


def test_section_is_claude_only_and_scopes_the_integration_review_code_lens():
    normalized = _norm(_section())
    assert normalized.startswith(_norm(HEADING) + " claude-only.")
    for term in (
        "`review-code` lens",
        "`harness:batch` integration task",
        "`task__batch-integrate-<slug>`",
        "a carry inherits the trust of that lead's close.",
    ):
        assert term in normalized, term


def test_each_endpoint_names_its_source():
    section = _section()
    relations = {
        "- `old_base`": ("coordinator head", "step b.5"),
        "- `old_tip`": (
            "the `commit` the lead returned",
            "leads commit after `wbs(action=\"close\")`",
            "tree their review and qa passed",
        ),
        "- `new_base`": (
            "main head before that lead's rebase",
            "previous lead's `new_tip`",
            "`old_base` for the first lead",
        ),
        "- `new_tip`": ("integrated tip", "after that lead's fast-forward"),
    }
    for prefix, terms in relations.items():
        bullet = _bullet(section, prefix)
        for term in terms:
            assert term in bullet, f"{prefix}: missing {term!r}"


def test_carry_requires_all_three_conditions():
    section = _section()
    _assert_carry(section)
    mutations = (
        section.replace("only when all of these hold", "when any of these hold", 1),
        section.replace("counts as overlap;", "counts as overlap; or", 1),
        section.replace("conflict path;", "conflict path, or", 1),
        section.replace("same commit\n  count", "commit\n  count", 1),
    )
    for mutated in mutations:
        assert mutated != section, "mutation did not apply"
        try:
            _assert_carry(mutated)
        except AssertionError:
            continue
        raise AssertionError("a weakened carry condition escaped the guard")


def test_failed_carry_is_residual_and_failures_select_deep():
    normalized = _norm(_section())
    assert "a range that fails any condition is residual" in normalized
    assert "missing proof alone only rejects the carry" in normalized
    failure = normalized[normalized.index("missing proof alone only rejects the carry"):]
    failure = failure[:failure.index("the residual is everything")]
    for term in (
        "conflict",
        "semantic difference",
        "overlap",
        "evidence loss",
        "endpoint that no longer resolves",
        "selects deep",
    ):
        assert term in failure, f"failure mapping missing {term!r}"
    assert "standard" not in failure


def test_depth_predicates_come_from_the_residual_only():
    normalized = _norm(_section())
    assert (
        "the residual is everything from `old_base` to the current worktree outside the "
        "carried ranges"
    ) in normalized
    assert "carried ranges do not count toward cross-component or dual-domain" in normalized
    assert "contribute no other depth predicate" in normalized
    assert "a deep request from the active instructions still selects deep" in normalized


def test_residual_precedence_is_ordered_with_forced_deep_items():
    section = _section()
    _assert_residual(section)
    one = "1. conflict resolutions from the step e path (manual-conflict, DEEP);"
    two_start = section.index("2. cross-lead interactions")
    two_end = section.index("(DEEP);", two_start) + len("(DEEP);")
    two = section[two_start:two_end]
    swapped = section.replace(one, "\0", 1).replace(two, one.replace("1.", "2.", 1), 1)
    swapped = swapped.replace("\0", two.replace("2.", "1.", 1), 1)
    mutations = (
        section.replace("(manual-conflict, DEEP)", "(manual-conflict, STANDARD)", 1),
        section.replace("changed\n   (DEEP)", "changed\n   (STANDARD)", 1),
        swapped,
        section.replace("An empty residual is LIGHT.", "An empty residual is STANDARD.", 1),
    )
    for mutated in mutations:
        assert mutated != section, "mutation did not apply"
        try:
            _assert_residual(mutated)
        except AssertionError:
            continue
        raise AssertionError("a remapped or reordered residual escaped the guard")


def test_invocation_lists_evidence_and_qa_still_runs():
    normalized = _norm(_section())
    assert (
        "the formal reviewer's approved sweep scope is the residual; carried ranges are "
        "in scope only to verify their carry proofs"
    ) in normalized
    assert (
        "formal reviewer invocation's selection reason lists, for each lead, its four "
        "endpoints, the patch-id comparison result, and its carried or residual status"
    ) in normalized
    assert "the full suite and `qa-cli` still run on the combined result" in normalized
    assert "a carry narrows only `review-code`" in normalized


def test_section_avoids_anchors_used_by_existing_contract_tests():
    section = _section()
    normalized = _norm(section)
    policy = _policy(section)
    for anchor in (
        "a rebase",
        "stored narrative",
        "missing proof rejects rebase-light",
        "fan-out is exact",
        "if the selected depth",
        "explicit deep",
        "resume/recovery",
        "review depth assessment",
    ):
        assert anchor not in normalized, anchor
    for anchor in (
        "a rebase",
        "zero hunters",
        "one fresh full sweep formal code reviewer",
        "stored narrative",
        "review depth assessment",
        "resume recovery",
    ):
        assert anchor not in policy, anchor
    assert 'subagent_type="harness:code-reviewer"' not in section
    assert 'task_name="code_review_' not in section
    # install.py copies this file into the Codex payload unchanged in meaning.
    assert "${CLAUDE_PLUGIN_ROOT}" not in section
    assert "`plugin/" not in section


def test_existing_rebase_light_proof_still_owns_the_first_anchors():
    body = _text(PIPELINE)
    normalized = _norm(body)
    heading = normalized.index(_norm(HEADING))
    assert normalized.index("a rebase") < normalized.index("missing proof rejects rebase-light")
    assert normalized.index("missing proof rejects rebase-light") < normalized.index("fan-out is exact")
    assert normalized.index("fan-out is exact") < heading
    assert normalized.index("resume/recovery") < heading
    assert normalized.rindex("stored narrative") > heading
    templates = [
        line
        for line in body.splitlines()
        if 'subagent_type="harness:code-reviewer"' in line
        or 'task_name="code_review_<review_run>"' in line
    ]
    assert len(templates) == 2


def test_claude_reviewer_verifies_carries_outside_the_shared_core():
    body = _text(REVIEWER)
    assert body.count(CORE_END) == 1
    core, after = body.split(CORE_END, 1)
    assert "harness:batch" not in core
    tail = _norm(after)
    assert tail.startswith("claude-only, for the `harness:batch` integration task")
    for term in (
        "`task__batch-integrate-<slug>`",
        "approved sweep scope is the residual",
        "each carried range is in scope only to verify its carry",
        "verify each carried proof yourself",
        "contiguous on the current history",
        "`git patch-id --stable` comparison and commit counts",
        "`old_base`, `old_tip`, `new_base`, and `new_tip`",
        "rebase never stopped",
        "step e conflict path",
        "harvested archive for a pass close",
        "rebase-light proof at that lead's fast-forward point",
        "sweep the complete residual",
        "conflict resolutions, cross-lead interactions, non-carried ranges, and the "
        "integration task's own edits",
        "a carry you cannot reproduce is a wrong carry and under-classification",
        "existing single ordinary fix_now finding",
        "run the missing discovery, and a fresh formal review",
        "never yields pass or blocked_env",
    ):
        assert term in tail, f"reviewer paragraph missing {term!r}"
    assert tail.index("verify each carried proof") < tail.index(
        "run verification commands in the foreground"
    )
    codex = _text(CODEX_REVIEWER)
    assert "harness:batch" not in codex
    assert "TASK__batch-integrate" not in codex


def test_req_records_the_carry_and_its_verification():
    req = _norm(_text(REQ))
    start = req.index("batch integration carry (claude-only)")
    requirement = req[start:req.index("every invoked hunter", start)]
    for term in (
        "`old_base` = the coordinator head from batch step b.5",
        "`old_tip` = the lead's returned `commit`",
        "`new_base` = the main head before that lead's rebase",
        "`new_tip` = the integrated tip",
        "identical ordered `git patch-id --stable` patch ids",
        "step d procedure",
        "step e conflict path",
        "harvested archive shows the lead closed with pass",
        "missing proof alone only rejects the carry",
        "carried ranges do not count toward cross-component or dual-domain",
        "conflict resolutions (manual-conflict, deep)",
        "cross-lead interactions (overlap with earlier leads, deep)",
        "an empty residual is light",
        "under-classification `fix_now`",
        "the full suite and `qa-cli` still run on the combined result",
    ):
        assert term in requirement, f"REQ requirement missing {term!r}"
    verification = req[req.index("## verification"):]
    assert "`tests/test_batch_integration_review_scope.py` pins the batch integration carry" in verification
    frontmatter = _text(REQ).split("---", 2)[1]
    assert "  - plugin/skills/batch/SKILL.md" in frontmatter
