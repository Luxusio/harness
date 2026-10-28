"""Within-task parallel width: plan decomposition, batch cap, test-author lane,
and the ac-worker sub-split.

See doc/harness/patterns/ADR__within-task-parallel-width.md. Every rule here is
prose a coordinator or worker follows, so these tests pin the text that carries
each rule, plus the two executable facts the rules lean on: the receipt-lens
inference for the new agent names, and setup's preservation of the manifest
key.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
WRITE_ARTIFACTS = REPO / "plugin" / "skills" / "plan" / "write-artifacts.md"
REVIEW_PHASES = REPO / "plugin" / "skills" / "plan" / "review-phases.md"
FANOUT = REPO / "plugin" / "skills" / "develop" / "parallel-fanout.md"
DEVELOP = REPO / "plugin" / "skills" / "develop" / "SKILL.md"
AC_WORKER = REPO / "plugin" / "agents" / "ac-worker.md"
TEST_AUTHOR = REPO / "plugin" / "agents" / "test-author.md"
CODEX_TEST_AUTHOR = REPO / "plugin-codex" / "agents" / "test-author.md"
CODEX_DEVELOP = REPO / "plugin-codex" / "internal-skills" / "develop" / "SKILL.md"
ADR = REPO / "doc" / "harness" / "patterns" / "ADR__within-task-parallel-width.md"

LENS_AGENTS = (
    "`code-reviewer`",
    "`security-reviewer`",
    "`defect-hunter`",
    "`qa-*`",
    "`ux-*`",
    "`dogfooder`",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(body: str) -> str:
    return " ".join(body.split())


def _section(body: str, start: str, end: str) -> str:
    begin = body.index(start)
    return body[begin:body.index(end, begin + len(start))]


def _frontmatter(body: str) -> dict[str, str]:
    lines = body.splitlines()
    assert lines[0] == "---"
    fields = {}
    for line in lines[1:lines.index("---", 1)]:
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def _tools(body: str) -> list[str]:
    return [tool.strip() for tool in _frontmatter(body)["tools"].split(",")]


def _load(module_name: str, relative: str):
    spec = importlib.util.spec_from_file_location(module_name, REPO / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _assert_phrases(body: str, phrases, label: str) -> None:
    missing = [phrase for phrase in phrases if phrase not in body]
    assert not missing, f"{label}: missing {missing!r}"


# --- 1. Plan decomposition (write-artifacts.md, review-phases.md) -----------


def _ac_shape_section() -> str:
    return _section(_text(WRITE_ARTIFACTS), "## 6.6 Acceptance criteria", "\n## 6.8")


def _width_bullet() -> str:
    deep = _section(
        _text(REVIEW_PHASES),
        "### Deep understanding (every brief)",
        "### 2. Build findings table",
    )
    bullets = [line for line in deep.splitlines() if line.startswith("- **Width check.**")]
    assert len(bullets) == 1, bullets
    return bullets[0]


def test_plan_ac_shape_declares_files_tests_and_dependencies():
    section = _ac_shape_section()
    shape = _section(section, "```text", "\n```\n")
    _assert_phrases(
        shape,
        (
            "**AC-00N: <outcome>.**",
            "**Files:** `<source path>`, ... | none",
            "**Tests:** `<test path>`, ... | none",
            "**Depends on:** none | AC-00M, ...",
            "- Verify: `<command>`",
        ),
        "AC shape block",
    )
    _assert_phrases(
        _flat(section),
        (
            "No path appears in both `**Files:**` and `**Tests:**`.",
            "Cut ACs along disjoint file seams",
            "develop runs them as parallel lanes",
            "split it into one AC per seam",
            "it is a helper-extract AC that each consumer lists in `**Depends on:**`",
            "develop runs it before those consumers",
            "test-only AC (`**Files:** none`) that depends on the implementing AC",
            "`**Tests:**` holds test paths only",
        ),
        "write-artifacts.md 6.6",
    )


def test_plan_reviewer_brief_checks_parallel_width():
    bullet = _width_bullet()
    _assert_phrases(
        bullet,
        (
            "`**Files:**`",
            "`**Tests:**`",
            "without a helper-extract AC both depend on",
            "bundles independent file seams",
            "separate parallel lanes",
        ),
        "review-phases.md width check",
    )


def test_codex_copied_plan_files_stay_runtime_neutral():
    """Both plan sub-files are copied into the Codex payload by install.py.

    The new text must not name a Claude agent type or spawn call, and neither
    file may cite a backticked Claude-only path (the payload test checks the
    installed copy; this pins the source). `${CLAUDE_PLUGIN_ROOT}` is rewritten
    by the copy and stays allowed.
    """
    new_text = _ac_shape_section() + "\n" + _width_bullet()
    for token in ("harness:ac-worker", "harness:test-author", "Agent(", "spawn_agent"):
        assert token not in new_text, token
    for path in (WRITE_ARTIFACTS, REVIEW_PHASES):
        body = _text(path)
        for token in ("`plugin/agents/", "`plugin/skills/develop/", "`plugin/skills/plan-"):
            assert token not in body, (path.name, token)


# --- 2. Configurable batch cap (parallel-fanout.md) --------------------------


def test_fanout_cap_is_optional_bounded_and_defaults_to_four():
    body = _text(FANOUT)
    cap = _section(body, "## Batch cap", "## Parallelization Triggers")
    _assert_phrases(
        _flat(cap),
        (
            "Read `develop.fanout_cap` from `doc/harness/manifest.yaml` once at Phase 3.0",
            "develop: fanout_cap: 6",
            "An integer from 1 to 8 is the cap.",
            "An integer above 8 caps at 8.",
            "A missing key, a missing `develop` section, or any other value (zero, "
            "negative, fractional, boolean, string, null, list) leaves the cap at 4.",
            "Setup preserves the key and never writes it.",
            "`effective fanout cap: <N> (<source>)` on a line above the lane table",
            "`default`, `develop.fanout_cap`, `clamped from <M>`, `invalid value → default`",
            "The cap governs Phase 3.0 AC lanes only",
            "The cap counts AC lanes, not agents",
            "`harness:test-author` rides in its AC's lane",
            "Cap parallel fanout at N=4 in a single batch by default. Past the effective cap,",
            "With a configured cap C, for N>C spawn batches of up to C.",
            "5 x cap: 20 agents at the default cap 4, 40 agents at the maximum cap 8",
        ),
        "Batch cap",
    )
    # The default wording stays literal and on its original lines.
    assert "For N>4, spawn batches of up to 4 in successive assistant turns; do not\n" in cap
    assert (
        "Merge cost controls batch size only. It does not justify collapsing two or more\n"
        in cap
    )
    # The lane table carries the effective cap on the line above it.
    lane_rule = _section(body, "### Lane table requirement", "| AC | Files | Depends on |")
    assert "`effective fanout cap:` line" in lane_rule


def test_setup_migration_preserves_develop_fanout_cap():
    """The cap is only configurable if setup keeps the key it never writes."""
    setup = _load("within_task_width_setup_finalize", "plugin/scripts/setup_finalize.py")
    block = "develop:\n  fanout_cap: 6\n"
    current = (
        f"version: {setup.MANIFEST_VERSION}\nname: demo\ntype: library\n\n"
        "qa:\n  default_mode: cli\n  browser_qa_supported: false\n"
        "  desktop_qa_supported: false\n  ux_review_supported: false\n\n" + block
    )
    legacy = "name: demo\ntype: library\n" + block
    for original in (current, legacy):
        migrated, errors = setup.migrate_manifest_text(original)
        assert errors == [], errors
        assert block in migrated, migrated


def test_setup_does_not_write_fanout_cap():
    paths = [
        REPO / "plugin" / "scripts" / "setup_finalize.py",
        *(REPO / "plugin" / "skills" / "setup").rglob("*"),
        *(REPO / "plugin-codex" / "skills" / "setup").rglob("*"),
    ]
    writers = [
        path.relative_to(REPO).as_posix()
        for path in paths
        if path.is_file()
        and "fanout_cap" in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert writers == []


# --- 3. Test-author lane ------------------------------------------------------


def test_test_author_frontmatter_is_sonnet_without_spawn_tool():
    body = _text(TEST_AUTHOR)
    fields = _frontmatter(body)
    assert fields["name"] == "test-author"
    assert fields["model"] == "sonnet"
    assert _tools(body) == ["Read", "Write", "Bash", "Glob", "Grep", "LS"]
    assert "spawned only by the harness develop coordinator" in fields["description"]


def test_test_author_writes_only_tests_from_plan_intent():
    for path, spawn_rule in (
        (TEST_AUTHOR, "Do not spawn agents."),
        (CODEX_TEST_AUTHOR, "Do not spawn workers."),
    ):
        _assert_phrases(
            _text(path),
            (
                "You run in the same batch as the ac-worker that implements your AC.",
                "only inside the `**Tests:**` paths your prompt assigns.",
                "Never edit a `**Files:**` path",
                "return `needs-coordinator-review` instead of touching the implementation",
                "Derive every test from PLAN.md intent and existing public interfaces, "
                "never from the in-progress implementation",
                "including an import of a new symbol PLAN.md names, fail until the AC lands",
                "never make a red test green by loosening its assertion",
                "once the whole batch returns",
                "Do not write `PLAN.md`, `TASK.json`, or `PROGRESS.md`.",
                "Do not call harness MCP artifact writers.",
                spawn_rule,
                "Do not run full-suite QA.",
                "AC-003 tests: written | blocked | needs-coordinator-review",
                "Expected red: <test ids that fail until the AC lands, or none>",
                "Expected green: <test ids that pin preserved behavior, or none>",
                "Interface: <public names the tests rely on, each with its PLAN.md or code source>",
                "Assumption: <choice> — because <PLAN/code evidence>",
            ),
            path.relative_to(REPO).as_posix(),
        )
    assert "Write only inside the `**Tests:**` paths your prompt assigns." in _text(TEST_AUTHOR)




def test_fanout_pairs_test_author_and_reconciles_after_the_batch():
    body = _text(FANOUT)
    trigger = next(
        line for line in body.splitlines() if line.startswith("| Test-author pairing |")
    )
    _assert_phrases(
        trigger,
        (
            "declares both `**Files:**` and `**Tests:**`",
            "Spawn one `harness:test-author` in the same assistant message as that ac-worker",
        ),
        "Test-author pairing trigger",
    )
    independence = _flat(
        _section(body, "### Component-independent definition", "### Small-task edge case")
    )
    _assert_phrases(
        independence,
        (
            "the union of `**Files:**` and `**Tests:**` for one AC shares no path "
            "with the union for the other",
            "current execution ownership and WBS dependencies",
        ),
        "Component-independent definition",
    )
    lane = _flat(_section(body, "### Test-author lane", "### ac-worker sub-split (Claude only)"))
    _assert_phrases(
        lane,
        (
            "gets one `harness:test-author`, spawned in the same assistant message "
            "as that AC's ac-worker",
            "`**Tests:** none` means no test-author for that AC",
            "`**Files:** none` marks a test-only AC, which runs as one ordinary "
            "`harness:ac-worker` lane",
            "A sequential AC the coordinator implements itself under `developer.md` has no "
            "pair either; the coordinator writes its tests.",
            "The paired ac-worker owns only `**Files:**`",
            "does not run or wait on the AC's `Verify:`",
            'the `Lane` cell notes "+test-author"; the Route vocabulary is unchanged',
            "Reconciliation starts when that AC's implementation and test author return",
            "without waiting for unrelated siblings",
            "a full-tree `Verify:` command waits for all relevant writers",
            "| Red, test matches PLAN.md | fix in the `**Files:**` lane |",
            "| Red, test asserts beyond or against PLAN.md | fix in the `**Tests:**` lane |",
            "an import of a new symbol PLAN.md names is expected red",
            "never retry with the same ownership",
            "No fix crosses between `**Files:**` and `**Tests:**`.",
            "The 3-Attempt Escalation Rule in `fix-first-pattern.md` bounds the loop.",
            "Count attempts per failing test across both lanes: moving a red test to the "
            "other lane does not reset its count.",
            "when `harness:test-author` is unavailable",
            "run the ac-worker unpaired, owning `**Files:**` and `**Tests:**`, "
            "and write the reason in the lane table",
        ),
        "Test-author lane",
    )
    routing = next(line for line in body.splitlines() if line.startswith("| 3 (per-AC tests) |"))
    assert "`harness:test-author`" in routing and "no lens" in routing


def test_codex_test_author_mirror_and_phase_30_line():
    mirror = _text(CODEX_TEST_AUTHOR)
    fields = _frontmatter(mirror)
    assert fields["name"] == "test-author"
    assert set(fields) == {"name", "description"}
    _assert_phrases(
        mirror,
        (
            "> **Codex runtime overlay:**",
            '`spawn_agent(task_name="test_author_ac_<NNN>")`',
            "Use `apply_patch` only inside the `**Tests:**` paths your prompt assigns.",
        ),
        "Codex test-author mirror",
    )
    for claude_only in ("`Agent`", "Sub-split", "sub-worker", "${CLAUDE_PLUGIN_ROOT}", "tools:"):
        assert claude_only not in mirror, claude_only

    phase_30 = _section(
        _text(CODEX_DEVELOP), "### Phase 3.0: AC Dependency Analysis", "### Phase 3.1"
    )
    lines = [line for line in phase_30.splitlines() if "test_author_ac_" in line]
    assert len(lines) == 1, lines
    _assert_phrases(
        lines[0],
        (
            "When an AC declares both `**Files:**` and `**Tests:**` paths (neither `none`)",
            "reserve its worker/test-author pair via `ready-lanes.md`",
            "explicit unpaired fallback only when host capacity requires it",
            '`spawn_agent(task_name="test_author_ac_<NNN>")`',
            "so the watcher infers no lens",
            "reads `${HARNESS_PLUGIN_ROOT}/agents/test-author.md`",
            "owns only `**Tests:**`",
            "the worker owns only `**Files:**` and leaves the AC's `Verify:` to the coordinator",
            "After both writers return, run scoped tests",
            "full-tree `Verify:` waits for all relevant writers",
            "send failures to the owning lane",
        ),
        "Codex Phase 3.0 test-author line",
    )


# --- 5. ac-worker sub-split (Claude only) -------------------------------------


def _assert_lens_ban(body: str) -> None:
    ban = next(line for line in body.splitlines() if "Never spawn a lens agent:" in line)
    clause = ban.split("Never spawn a lens agent:", 1)[1].split(".", 1)[0]
    missing = [agent for agent in LENS_AGENTS if agent not in clause]
    assert not missing, f"lens ban does not name {missing!r}"


def test_ac_worker_sub_split_is_bounded_one_level_and_sole_reporter():
    body = _text(AC_WORKER)
    assert _tools(body) == ["Read", "Write", "Bash", "Glob", "Grep", "LS", "Agent"]
    split = _flat(_section(body, "## Sub-split (Claude only)", "## Always Do"))
    _assert_phrases(
        split,
        (
            "If your prompt says you are a sub-worker (`Sub-worker of AC-NNN`), this section "
            "does not apply to you: implement your paths and never spawn.",
            "Run scoped tests for your own paths only; do not run or wait on the AC's "
            "`Verify:` command",
            "and in Always Do step 3 for a sub-worker.",
            "each owns a pairwise-disjoint subset of your `**Files:**`, you keep any remainder",
            "you split only when no piece changes an interface another piece consumes",
            "Spawn at most 3 sub-workers, in one message",
            "only within spare host slots explicitly assigned by the coordinator",
            "Splits go one level deep only.",
            '"Sub-worker of AC-NNN: do not split further"',
            '"A sub-worker never spawns."',
            "\"Run scoped tests for your own paths only; you do not run the AC's `Verify:`.\"",
            # Paired + split in one lane: the parent's combined checks never include Verify:.
            "When your AC is also paired with a test author, those checks are the existing tests "
            "for the changed paths: the AC's `Verify:` still belongs to the coordinator",
            "Never pass `name=` to a sub-worker.",
            "You remain the sole reporter",
            "return the one `AC-NNN:` block",
            "a sub-worker's `blocked` or `needs-coordinator-review` becomes yours",
            "you keep successful sub-worker edits",
            "If the `Agent` tool is unavailable",
            "implement the AC without splitting",
        ),
        "ac-worker Sub-split",
    )
    # The worked spawn example carries every marker a sub-worker prompt must carry.
    example = next(line for line in body.splitlines() if 'prompt="Sub-worker of AC-' in line)
    for marker in (
        "Sub-worker of AC-003: do not split further.",
        "A sub-worker never spawns.",
        "Run scoped tests for your own paths only; you do not run the AC's `Verify:`.",
    ):
        assert marker in example, marker
    fanout = _flat(
        _section(_text(FANOUT), "### ac-worker sub-split (Claude only)", "## Stage Agent Routing")
    )
    _assert_phrases(
        fanout,
        ("at most 3 unnamed sub-workers", "one level deep", "sole reporter"),
        "parallel-fanout sub-split note",
    )


def test_ac_worker_never_spawns_lens_agents():
    """Nested lens spawns bind receipts to the task outside the review-before-QA order."""
    body = _text(AC_WORKER)
    _assert_lens_ban(body)
    _assert_phrases(
        _flat(body),
        (
            'Always pass `subagent_type="harness:ac-worker"`. Never omit it',
            "a general-purpose agent that inherits every tool, including harness MCP writers",
            "Never `harness:developer`, `harness:task-lead`, or `harness:test-author`.",
            "- Do not spawn any agent type other than `harness:ac-worker`, "
            "and only under Sub-split.",
        ),
        "ac-worker spawn allowlist",
    )
    spawned = re.findall(r'subagent_type="([^"]+)"', body)
    assert spawned and set(spawned) == {"harness:ac-worker"}, spawned

    # The guard is only worth having if dropping any one lens from the ban fails it.
    ban_line = next(line for line in body.splitlines() if "Never spawn a lens agent:" in line)
    for agent in LENS_AGENTS:
        mutated = body.replace(ban_line, ban_line.replace(agent, "`removed`", 1), 1)
        try:
            _assert_lens_ban(mutated)
        except AssertionError:
            continue
        raise AssertionError(f"removing {agent} from the ban escaped the guard")


def test_paired_ac_worker_owns_only_files_and_defers_verify():
    body = _text(AC_WORKER)
    paired = _flat(_section(body, "## Paired with a test author", "## Sub-split (Claude only)"))
    _assert_phrases(
        paired,
        (
            "When your prompt says a `harness:test-author` owns your AC's `**Tests:**` paths, "
            "you own only `**Files:**`.",
            "Never write in its `**Tests:**` paths",
            "Do not run or wait on the AC's `Verify:` command",
            "reproduce a bug ad hoc without committing a test",
            "Run existing tests for your changed paths: report `implemented` on that evidence",
            "This section replaces the per-AC verify command in \"Treat the AC plus its per-AC "
            "verify command as your success criterion\" and in Always Do step 3",
            "for a paired AC only",
        ),
        "ac-worker paired mode",
    )
    # Every rule the override names must still exist, or the override points at nothing
    # and the surviving rule silently contradicts paired mode.
    always_do = _flat(_section(body, "## Always Do", "## Never Do"))
    assert "3. Run scoped tests for the changed paths, plus any per-AC verification command" in always_do
    flat = _flat(body)
    assert "Treat the AC plus its per-AC verify command as your success criterion" in flat
    assert "Leave the smallest meaningful regression check" in flat


def test_develop_phase_30_loads_fanout_before_any_agent_batch():
    phase_30 = _flat(
        _section(_text(DEVELOP), "### Phase 3.0: AC Dependency Analysis", "### Phase 3.1")
    )
    assert (
        "Load `parallel-fanout.md` before any `Agent(...)` batch: it owns the batch cap, "
        "`harness:test-author` pairing, and ac-worker sub-splits." in phase_30
    )
    assert "only for uncommon routing cases" not in phase_30


# --- ADR ------------------------------------------------------------------------


def test_adr_records_decisions_bounds_codex_scope_and_rejections():
    _assert_phrases(
        _flat(_text(ADR)),
        (
            "Status: accepted (2026-09-28)",
            "The user approved four changes on 2026-09-28 (proposal items 1, 2, 3 and 5)",
            "## Rejected alternatives",
            "**Item 4, worktree-isolated ac-workers.**",
            "The user rejected it on 2026-09-28.",
            "**A separate `harness:ac-subworker` without `Agent`.**",
            "`develop.fanout_cap`",
            "| integer 1 to 8 | that integer |",
            "| integer above 8 | 8 |",
            "The cap counts AC lanes, not agents",
            "at most 3 unnamed sub-workers",
            "Splits go one level deep only.",
            "Never a lens agent",
            "## Codex scope",
            '`spawn_agent(task_name="test_author_ac_<NNN>")`',
            "| `develop.fanout_cap` | yes: shared ready-lane dispatcher, constrained by actual host slots |",
            "| ac-worker sub-split | no: Codex has no ac-worker agent |",
            "5 x cap concurrent agents",
            "No hook or MCP tool enforces them.",
            "only the prompt marker stops it from splitting again",
            "A sub-worker runs scoped tests for its own paths only",
            "by the parent, or by the coordinator when the AC is also paired with a test author",
            "Both residual risks are accepted.",
        ),
        "ADR",
    )
