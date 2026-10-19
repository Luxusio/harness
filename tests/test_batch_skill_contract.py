"""Contract guards for the harness:task-lead agent and harness:batch skill.

Covers TASK__parallel-tasks-in-one-session AC-004: the batch-lead agent
definition, the batch coordinator skill, and the develop skill's batch-lead
carve-outs.
"""
from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TASK_LEAD = REPO / "plugin" / "agents" / "task-lead.md"
BATCH_SKILL = REPO / "plugin" / "skills" / "batch" / "SKILL.md"
DEVELOP_SKILL = REPO / "plugin" / "skills" / "develop" / "SKILL.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _frontmatter(path: Path) -> dict[str, str]:
    lines = _text(path).splitlines()
    assert lines and lines[0] == "---", f"{path} must start with frontmatter"
    data: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            return data
        if ":" not in line or line.startswith((" ", "\t")):
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    raise AssertionError(f"{path} frontmatter is not closed")


def _normalized(body: str) -> str:
    return " ".join(body.lower().split())


def _assert_all(body: str, fragments: tuple[str, ...], path: Path) -> None:
    lowered = _normalized(body)
    for fragment in fragments:
        assert _normalized(fragment) in lowered, f"{path}: missing {fragment!r}"


def test_task_lead_frontmatter_declares_worktree_isolation_and_inherits_tools():
    meta = _frontmatter(TASK_LEAD)
    assert meta.get("isolation") == "worktree", TASK_LEAD
    assert meta.get("model") == "inherit", TASK_LEAD
    # A lead runs the whole lifecycle: it needs Agent, Skill and the harness MCP
    # task tools under whatever server name the host exposes (plugin-scoped
    # `mcp__plugin_harness_harness__*` or a user-level `mcp__harness__*`). A
    # `tools:` allowlist naming one form strips the tools under the other.
    assert "tools" not in meta, f"{TASK_LEAD}: must inherit the session's tools"


def test_task_lead_keeps_a_one_hour_prompt_cache_per_agent_only():
    # Per-agent flow form, the shape Claude Code's agent schema accepts
    # ("5m" | "1h"). A session-wide subagentPromptCacheTtl was rejected.
    assert _frontmatter(TASK_LEAD).get("experimental") == "{ cacheTtl: 1h }"
    surfaces = [p for p in (REPO / "plugin").rglob("*") if p.is_file()]
    surfaces.append(REPO / ".claude" / "settings.json")
    offenders = [
        str(path.relative_to(REPO)) for path in surfaces
        if "subagentPromptCacheTtl" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []


def test_task_lead_commits_once_with_a_harness_task_trailer():
    norm = _normalized(_text(TASK_LEAD))
    assert _normalized('git commit --trailer "Harness-Task: <task_id>"') in norm
    assert _normalized("in one commit") in norm
    assert "--amend" in norm


def test_task_lead_body_states_batch_carveouts():
    body = _text(TASK_LEAD)
    _assert_all(
        body,
        (
            "workspace",
            "AskUserQuestion",
            "install_verified.py",
            "git rev-parse HEAD",
            "coordinator",
            "never `cd`",
            "plain",
            "installed plugin",
        ),
        TASK_LEAD,
    )
    # Never call AskUserQuestion is a prohibition, not a step it performs.
    assert "never call `askuserquestion`" in _normalized(body)


def test_batch_skill_frontmatter_and_weight():
    meta = _frontmatter(BATCH_SKILL)
    assert meta.get("name") == "batch", BATCH_SKILL
    assert "parallel" in meta.get("description", "").lower(), BATCH_SKILL
    assert "one session" in meta.get("description", "").lower(), BATCH_SKILL

    line_count = len(_text(BATCH_SKILL).splitlines())
    assert line_count <= 500, f"{BATCH_SKILL} has {line_count} lines, budget is 500"


def test_batch_skill_names_every_coordinator_step():
    body = _text(BATCH_SKILL)
    _assert_all(
        body,
        (
            "one assistant message",
            "3 concurrent leads",
            "git merge --ff-only",
            "rebase --no-autostash",
            "batch_finish.py",
            "batch_harvest.py",
            "git worktree remove",
            "never",
            "--force",
            "TASK__batch-integrate",
            "install_verified.py",
            "baseRef",
            "\"head\"",
            "branches and commits only",
        ),
        BATCH_SKILL,
    )


def test_batch_skill_covers_conflict_and_blocked_lead_handling():
    body = _text(BATCH_SKILL)
    _assert_all(
        body,
        (
            "rebase --abort",
            "GIT_EDITOR=true",
            "rebase --continue",
            "carry",
            "integration task",
            "blocked",
            "failed",
            "kept",
        ),
        BATCH_SKILL,
    )


def test_batch_skill_integrates_leads_without_merge_commits():
    body = _text(BATCH_SKILL)
    assert "--no-ff" not in body, BATCH_SKILL
    assert "git merge --abort" not in body, BATCH_SKILL
    assert "never by a merge commit" in body, BATCH_SKILL
    assert "fall back to a merge commit" in body, BATCH_SKILL
    _assert_all(
        body,
        (
            # The branch is checked out in the lead worktree: rebase runs there.
            'git -C <W> rebase --no-autostash "$(git rev-parse HEAD)"',
            # A rebase drops a merge commit's own change; refuse such a lead.
            'git rev-list --merges "$(git rev-parse HEAD)..<branch>"',
            # A conflict and a dirty refusal share exit 1; paths tell them apart.
            "git -C <W> diff --name-only --diff-filter=U",
            "git -C <W> rebase --abort",
            "go on to the next closed lead",
        ),
        BATCH_SKILL,
    )


def test_develop_skill_points_to_task_lead_carveouts():
    body = _text(DEVELOP_SKILL)
    assert "plugin/agents/task-lead.md" in body, DEVELOP_SKILL
    # Referenced once near the write-focus pre-flight and once near auto-install.
    assert body.count("plugin/agents/task-lead.md") >= 2, DEVELOP_SKILL
    _assert_all(
        body,
        (
            "never calls `AskUserQuestion`",
            "skips this step",
        ),
        DEVELOP_SKILL,
    )


def test_root_claude_md_states_batch_focus_and_install_clauses():
    body = _normalized(_text(REPO / "CLAUDE.md"))
    assert _normalized(
        "In `harness:batch` mode, batch leads skip this verified install; it runs "
        "once from the post-merge integration task in the main checkout."
    ) in body
    assert _normalized(
        "Each linked git worktree is its own checkout with its own write focus"
    ) in body


def test_batch_skill_routes_managed_finish_through_durable_state_and_integrates_closed_leads():
    body = _text(BATCH_SKILL)
    norm = _normalized(body)
    # User projects have no plugin/scripts/; the installed payload does.
    assert "${claude_plugin_root}/scripts/batch_state.py" in norm
    assert "python3 ${claude_plugin_root}/scripts/batch_finish.py" not in norm
    assert "python3 plugin/scripts/batch_finish.py" not in norm
    # batch_finish.py owns harvest; the skill no longer calls it directly.
    assert "scripts/batch_harvest.py" not in norm
    assert _normalized(
        "--repo <main checkout> --batch-id <id> finish --slug <slug>"
    ) in norm
    # Claude Code keeps its agent lock after the lead returns; the helper
    # releases it only when the worktree is locked.
    assert "git worktree unlock" in norm
    assert _normalized("only when `git worktree list --porcelain` shows one") in norm
    assert _normalized("never unlock a worktree whose lead is still running") in norm
    # Every status the helper prints has a coordinator action.
    for status, code in (
        ("integrated", 0), ("kept", 3), ("conflict", 4), ("ff-refused", 5),
    ):
        assert f"`{status}` (exit {code})" in norm, status
    assert _normalized("any other exit (1: unexpected error, 2: usage): stop integrating") in norm
    # Step e.1 resumes a resolved lead; a conflict never strands later leads.
    assert _normalized("rerun the step d.1 command with `--resume`") in norm
    assert _normalized("continue step d in order for every remaining closed lead") in norm
    # Step g reports both tips and the traceability trailer.
    assert _normalized("integrated, worktree kept") in norm
    # After a `git branch -d` refusal the worktree is gone: no rerun helps.
    assert _normalized("integrated, branch kept") in norm
    assert _normalized("`git branch -d <branch>` once the cause is fixed") in norm
    assert "`trailer_present: false`" in norm


def test_c09_batch_clause_is_in_root_and_template_contracts():
    clause = _normalized(
        "Each linked git worktree is a separate checkout with its own write focus; "
        "a coordinator may run one task per worktree via `harness:batch`"
    )
    for path in (REPO / "CONTRACTS.md", REPO / "plugin/skills/setup/templates/CONTRACTS.md"):
        body = _text(path)
        c09 = body[body.index("### C-09"):body.index("### C-10")]
        assert clause in _normalized(c09), path


def test_this_repo_branches_leads_from_head_and_ignores_worktrees():
    import json
    import subprocess

    settings = json.loads((REPO / ".claude/settings.json").read_text(encoding="utf-8"))
    assert settings["worktree"]["baseRef"] == "head"
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".claude/worktrees/probe"], cwd=REPO,
    )
    assert ignored.returncode == 0


def test_batch_skill_rejects_reused_slugs_before_integrating():
    norm = _normalized(_text(BATCH_SKILL))
    assert _normalized("Slugs must be distinct within the batch") in norm
    assert "doc/harness/archive/batch/task__<slug>" in norm
    assert _normalized("integration stopped at a conflict carried from step d.2") in norm


def test_batch_skill_gates_spawning_on_the_repo_shape_preflight():
    body = _text(BATCH_SKILL)
    norm = _normalized(body)
    # Installed payload path, like batch_harvest; a user project has no plugin/.
    assert "${claude_plugin_root}/scripts/batch_preflight.py" in norm
    assert "python3 plugin/scripts/batch_preflight.py" not in norm
    assert _normalized("Spawn only when it exits 0") in norm
    for shape in ("`submodule-checkout`", "`linked-worktree`", "`non-git`", "`separate-git-dir`"):
        assert shape in norm, shape
    _assert_all(
        body,
        (
            "in every populated submodule, and in every ignored nested repo",
            "`inside-submodule` or `inside-ignored-nested-repo` is not batched",
            "ordinary harness task in the main checkout",
            "`outside-root` scope cannot run in a batch lead",
            "excluded_requests",
            "overlaps",
            "off-limits: <preflight off_limits",
            "keep it and report it like a blocked lead",
            # One path per flag; `ok` is necessary, not sufficient.
            "--request <slug>=<path> --request <slug>=<path2>",
            "a comma list is refused",
            "exits 0 (`verdict: \"ok\"`) and steps b.2–b.5 hold",
            # Step g accounts for every request kept out of the wave.
            "excluded (ordinary task, done or pending)",
            "queued (dependency or retained scope)",
            "outside the root, not batchable",
        ),
        BATCH_SKILL,
    )
    # The plain root-only status check is gone; the script owns cleanliness.
    assert _normalized("`git status --porcelain` in the main checkout must be empty") not in norm
    # Step b.3 moved into the script: git sees a symlinked `.claude` elsewhere.
    assert "git check-ignore .claude/worktrees/x" not in norm
    assert "`worktrees_ignore`" in norm
    assert _normalized("at the path git actually sees") in norm


def test_task_lead_forbids_module_management_and_unselected_or_nested_repo_edits():
    body = _text(TASK_LEAD)
    norm = _normalized(body)
    for subcommand in ("update", "init", "deinit", "sync", "set-url", "absorbgitdirs"):
        assert f"`{subcommand}`" in norm or f"git submodule {subcommand}" in norm, subcommand
    _assert_all(
        body,
        (
            "Never run `git submodule update`",
            "any other `git submodule` subcommand except `status`",
            "`--recurse-submodules`",
            "`-c submodule.recurse=true`",
            "Unselected submodules, ignored nested repos and `off-limits` paths remain forbidden",
            "Never initialize, clone, fetch remotely or push a module",
            "`off-limits`",
            "return `blocked`",
        ),
        TASK_LEAD,
    )


def test_req_documents_worktree_location_and_multi_repo_shapes():
    path = REPO / "doc/harness/REQ__parallel-tasks-via-worktree-leads.md"
    body = _text(path)
    frontmatter = body.split("\n---", 1)[0]
    assert "plugin/scripts/batch_preflight.py" in frontmatter
    assert "plugin/scripts/batch_finish.py" in frontmatter
    assert "\n## Worktree location\n" in body
    assert "\n## Multi-repo and submodules\n" in body
    _assert_all(
        body,
        (
            "validates registration, not path",
            "$CODEX_HOME/worktrees",
            "WorktreeCreate hook",
            "read-only",
            "same 9p mount",
            "It is NOT recommended to make multiple checkouts of a superproject.",
            "refuses that worktree forever",
            "REQ__batch-submodule-support.md",
            "Recursive graphs, unpopulated modules",
        ),
        path,
    )


def test_task_lead_operates_only_its_worktree_wbs_and_defines_verdicts():
    norm = _normalized(_text(TASK_LEAD))
    assert _normalized("Operate only the lead WBS in your worktree. The coordinator owns its") in norm
    for verdict in ("`closed`", "`blocked`", "`failed`"):
        assert verdict in norm
    assert _normalized("`failed` when the lifecycle could not reach PASS") in norm


def test_pool_reserves_and_binds_before_authorizing_exact_worker():
    body = _text(BATCH_SKILL)
    _assert_all(body, (
        "init --requests-file <JSON> [--max-leads <N>]",
        "claim` atomically reserves capacity before spawning",
        "Reserved and running requests both consume slots",
        "current destination `spawn_head`",
        "bootstrap-only; do not start a task or edit source",
        "bind --slug <slug> --worker-id <native id> --worktree <W> --branch <branch>",
        "Only after successful binding",
        "resume that same agent",
        "Use the actual bound worker id",
        "Identity/task/run/close mismatch refuses",
        "--worker-stopped --no-external-work",
        "Never discard a reservation just due to elapsed time",
    ), BATCH_SKILL)
    norm = _normalized(body)
    assert norm.index("init --requests-file") < norm.index("claim` atomically")
    assert norm.index("bootstrap-only; do not") < norm.index("bind --slug") < norm.index("Only after successful binding".lower())
    _assert_all(_text(TASK_LEAD), (
        "Do not call `wbs(action=\"open\")`, create task artifacts, edit source or commit",
        "this same native agent with confirmed `batch_state.py bind` success",
        "Never infer permission from the original request",
    ), TASK_LEAD)


def test_pool_capacity_refill_and_serial_integration_keep_lifecycle_gates():
    _assert_all(_text(BATCH_SKILL), (
        "default is **3 concurrent leads**",
        "`--max-leads`, then manifest `batch.max_leads`, then 3",
        "above 8 clamp to 8",
        "Invalid explicit values refuse; invalid manifest values fall back to 3",
        "serially in completion order",
        "claim/refill free slots from the current main HEAD while other leads continue",
        "No full-wave barrier",
        "Never run two finish commands concurrently",
        "only releases a dependency once its predecessor is integrated and cleaned",
        "Do not open a main-checkout task while any lead remains live or unknown",
        "`review-code`, conditional `review-security`, then `qa-cli`",
        "receipt-backed review-before-QA order",
        "before `wbs(action=\"close\")`",
        "never substitutes for task review/QA/close",
        "never bypass durable checkpoints by invoking direct finish here",
    ), BATCH_SKILL)


def test_resume_agent_reuses_exact_worktree_and_task_generation_without_isolation():
    path = REPO / "plugin/agents/task-lead-resume.md"
    meta = _frontmatter(path)
    assert meta["name"] == "task-lead-resume"
    assert meta["model"] == "inherit"
    assert "isolation" not in meta
    assert "tools" not in meta
    _assert_all(_text(path), (
        "Resolve W and enter that exact directory before task tools or nested agents",
        "source and task mutations are not",
        "binds your actual host worker identity",
        "explicit mutation permission",
        "wbs(action=\"open\", workspace=W, wbs_id=existing_id)` without `fresh_run`",
        "Require the same run id",
        "Preserve receipts and evidence",
        "never restart solely to manufacture a PASS",
        "Never clean up your own worktree",
    ), path)
    _assert_all(_text(BATCH_SKILL), (
        "Prefer the original native agent",
        "harness:task-lead-resume",
        "Never spawn regular `task-lead` for resume",
        "resume --slug <slug> --worker-stopped",
        "Confirm the original worker and all nested writers through the host",
    ), BATCH_SKILL)


def test_abandonment_retains_source_and_truthful_unfinished_disposition():
    _assert_all(_text(BATCH_SKILL), (
        "Only after an explicit user choice to abandon",
        "abandon --slug <slug> --worker-stopped --reason <decision>",
        "Abandonment retains source, branch and evidence",
        "retains unresolved scope ownership",
        "It does not cancel/close the task or satisfy the Goal",
        "Report the unfinished retained disposition and next action",
        "never `-D` for unmerged unique commits",
        "Operational close refuses unfinished work/cleanup",
        "reports abandoned retention separately",
    ), BATCH_SKILL)


def test_native_bootstrap_retention_precedes_bind_and_exact_worker_message():
    assert "SendMessage" in _frontmatter(BATCH_SKILL)["allowed-tools"].split(", ")
    norm = _normalized(_text(BATCH_SKILL))
    bootstrap = "bootstrap --slug <slug> --worktree <w> --branch <branch>"
    assert norm.index(bootstrap) < norm.index("bind --slug") < norm.index("native `sendmessage`")
    _assert_all(_text(BATCH_SKILL), (
        'Require `status: "bootstrapped"`',
        "exact `{batch_id,slug,spawn_head}` reservation bytes",
        "Only after successful binding, use native `SendMessage` addressed to that exact bound agent ID",
        "Retain the marker through every unfinished or zero-source final",
        "never stage or remove it in a lead",
    ), BATCH_SKILL)
    lead = _normalized(_text(TASK_LEAD))
    assert lead.index(bootstrap) < lead.index("return bootstrap json") < lead.index("`batch_state.py bind`") < lead.index("`sendmessage`")
    _assert_all(_text(TASK_LEAD), (
        "sole permitted pre-bind write",
        "Keep `.harness-batch-bootstrap` through all final returns, including blocked, failed and task-only/zero-source closed results",
        "Never stage, edit or remove it",
        "git add <intended paths>` excluding `.harness-batch-bootstrap`",
        "without a dummy or empty commit",
    ), TASK_LEAD)


def test_resume_preserves_marker_and_managed_finish_removes_only_after_harvest():
    path = REPO / "plugin/agents/task-lead-resume.md"
    _assert_all(_text(path), (
        "Preserve any existing `.harness-batch-bootstrap` marker exactly as supplied",
        "never stage, replace or remove it",
        "do not rerun new-worktree `bootstrap`",
        "Keep it even for unfinished or zero-source results",
        "Continue only on the coordinator's `SendMessage` to this exact bound agent ID",
        "with successful binding and explicit mutation permission",
    ), path)
    _assert_all(_text(BATCH_SKILL), (
        "Managed finish preserves the marker through rebase and integration failures",
        "removes only that marker after the durable archive checkpoint, immediately before ordinary or guarded S1 worktree removal",
        "Unknown marker bytes, links, tracking or other dirty files refuse",
        "standalone finish has no exemption",
    ), BATCH_SKILL)
    _assert_all(_text(TASK_LEAD), (
        "It grants no PASS or task authority",
        "managed finish removes it only after durable evidence harvest, immediately before worktree removal",
    ), TASK_LEAD)


def test_selected_modules_require_opt_in_and_prepared_same_worker_handoff():
    # Permission is split across intake, coordinator binding and the receiving
    # lead. Keeping one surface permissive must not silently widen the others.
    batch = _normalized(_text(BATCH_SKILL))
    lead = _normalized(_text(TASK_LEAD))
    for token in ("submodules: [module paths]", "--submodule <slug>=<module path>",
                  "omission keeps default exclusions", "reserve the whole gitlink"):
        assert token in batch
    bind = batch.index("bind --slug")
    prepared = batch.index("all selected modules prepared", bind)
    handoff = batch.index("only after successful binding", prepared)
    assert bind < prepared < handoff
    assert "exact bound agent id" in batch[handoff:]
    assert "confirmed prepared-module manifest" in lead
    assert "only coordinator bind prepares these clones" in lead
    assert "only explicitly selected, coordinator-prepared s1 modules within your declared scope" in lead
    assert "missing or incomplete preparation is a blocker" in lead


def test_selected_module_commits_stay_under_outer_task_lifecycle():
    lead = _normalized(_text(TASK_LEAD))
    assert "commit module changes before the outer gitlink commit" in lead
    assert "`git -c <w/module>`" in lead
    assert "do not start a separate task inside a module" in lead
    assert "lifecycle calls and nested review/qa cwd at w" in lead
    outer = lead[lead.index("## after `wbs(action=\"close\")` reports pass"):]
    assert "outer implementation diff" in outer
    assert 'git commit --trailer "harness-task: <task_id>"' in outer
    assert "no push, no merge" in outer


def test_selected_resume_preserves_module_identity_and_unfinished_edits():
    path = REPO / "plugin/agents/task-lead-resume.md"
    resume = _normalized(_text(path))
    assert "isolation" not in _frontmatter(path)
    for token in ("original selected-module manifest", "private-store identities",
                  "named branches", "preserve those exact stores", "unfinished edits",
                  "never clone, initialize or allocate a replacement module checkout",
                  "confirmed prepared-module handoff"):
        assert token in resume
    assert "dirty module edits" in resume and "rather than resetting" in resume
    assert "wbs(action=\"open\", workspace=w, wbs_id=existing_id)` without `fresh_run`" in resume


def test_published_s1_contract_has_one_owner_and_keeps_unsupported_topologies_out():
    owner = REPO / "doc/harness/REQ__batch-submodule-support.md"
    for path in (BATCH_SKILL, REPO / "doc/harness/REQ__parallel-tasks-via-worktree-leads.md",
                 REPO / "doc/harness/REQ__batch-state-pool-recovery.md",
                 REPO / "doc/harness/GUIDE__how-harness-works.md"):
        assert owner.name in _text(path), path
    contract = _normalized(_text(owner))
    for token in ("schema 2", "schema 1", "older readers refuse schema 2",
                  "annotated tags", "reflog", "intermediate commits",
                  "survive garbage collection", "before ordinary destination cleanliness checks",
                  "unrelated dirt", "ordinary sequential route"):
        assert token in contract
    assert "recursive modules" in contract and "not supported" in contract
