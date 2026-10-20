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
OWNER_REQ = REPO / "doc/harness/REQ__batch-state-pool-recovery.md"
COMPLETION = REPO / "plugin/skills/run/worktree-completion.md"


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


def test_task_lead_uses_mcp_checkout_without_host_isolation_and_inherits_tools():
    meta = _frontmatter(TASK_LEAD)
    assert "isolation" not in meta, TASK_LEAD
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
            "Never change process-wide cwd",
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
    # The public owner performs the lifecycle; a skill must not reconstruct it
    # with a shell command chain or ask the native host to allocate another W.
    body = _text(BATCH_SKILL)
    _assert_all(body, (
        'wbs(action="dispatch"', 'operation="prepare"', 'operation="bind"',
        'operation="complete"', 'operation="status"', 'operation="resume"',
        'operation="recover"', "one assistant message", "Default lead cap is 3",
        "rebase, fast-forward, evidence harvest, worktree removal and `git branch -d`",
        "install_verified.py", "branches and commits only",
        "Do not run `git worktree add`", "without native worktree isolation",
        "No separate integration child or ledger",
    ), BATCH_SKILL)


def test_batch_skill_covers_conflict_and_blocked_lead_handling():
    _assert_all(_text(BATCH_SKILL), (
        "GIT_EDITOR=true", "--continue", "integration task", "blocked", "failed", "kept",
        "Conflicts, fast-forward refusal or unknown outcomes halt new starts and integration",
        "collect other workers and preserve their results", "finish --resume",
        "Clear the halt only through validated owner recovery",
    ), BATCH_SKILL)
    # Abort/classification details belong to the existing finish owner, not an
    # alternate manual integration implementation in the coordinator prompt.
    req = REPO / "doc/harness/REQ__parallel-tasks-via-worktree-leads.md"
    _assert_all(_text(req), ("rebase --abort", "diff --name-only --diff-filter=U"), req)


def test_batch_skill_integrates_leads_without_merge_commits():
    body = _text(BATCH_SKILL)
    assert "--no-ff" not in body
    assert "git merge --abort" not in body
    _assert_all(body, (
        "No manual force removal, stash, merge-commit fallback or forced branch deletion",
        "Never run integrations concurrently", "recorded lead checkout",
        "without a whole-wave barrier",
    ), BATCH_SKILL)
    # Preserve the loss-prevention and branch identity rules at the shared
    # policy and durable owner rather than pinning obsolete shell steps.
    _assert_all(_text(COMPLETION), (
        "A merge commit in the source range needs resolution",
        "never a lossy rebase that silently drops its changes",
        "git -c rebase.updateRefs=false rebase --no-autostash <destination-tip>",
        "git merge --ff-only refs/heads/<source-branch>",
    ), COMPLETION)


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
    _assert_all(body, (
        'operation="complete", slug=S, worker_id=<bound host ID>, result=<actual JSON object>, worker_stopped=true',
        "for genuinely closed leads", "closes an eligible fully disposed pool",
        "Replayed completion must match the recorded result even when W has already been removed",
        "Partial success retains its checkpoint and exact remaining action",
        "integrated_tip` always takes precedence over a cleanup error",
        "Do not edit records or bypass checkpoints with direct finish",
        "integrated, worktree kept", "integrated, branch kept", "`trailer_present: false`",
    ), BATCH_SKILL)
    norm = _normalized(body)
    assert "python3 ${claude_plugin_root}/scripts/batch_finish.py" not in norm
    assert "python3 plugin/scripts/batch_finish.py" not in norm
    assert "scripts/batch_harvest.py" not in norm
    _assert_all(_text(COMPLETION), (
        "Never unlock a running worker's worktree", "unlock only an existing lock belonging to the stopped worker",
        "report only the branch as retained, not a nonexistent worktree",
    ), COMPLETION)


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
    _assert_all(_text(BATCH_SKILL), (
        "Slugs must be distinct within the batch", "task/archive identity collisions refuse",
        "Never replace the exact pool to bypass a retained scope",
        "Conflicts, fast-forward refusal or unknown outcomes halt new starts and integration",
    ), BATCH_SKILL)
    _assert_all(_text(OWNER_REQ), ("reused task/archive identities refuse intake",), OWNER_REQ)


def test_batch_skill_gates_spawning_on_the_repo_shape_preflight():
    _assert_all(_text(BATCH_SKILL), (
        "preflight classifies every declared scope path",
        "Only a `tracked-area` or validated `selected-submodule` scope may run in a lead",
        "`inside-submodule` or `inside-ignored-nested-repo` is not batched",
        "ordinary harness task in the main checkout", "`outside-root` scope cannot run in a batch lead",
        "control-root shape, clean root/nested repositories", "post-checkout-hook hazards",
        "`.claude/worktrees/` must be ignored", "off-limits",
        "excluded (ordinary task, done or pending)", "queued (dependency or retained scope)",
        "outside the root, not batchable",
    ), BATCH_SKILL)
    # CLI flag syntax and exit classification are backend behavior tests;
    # this prompt requires a successful managed preparation before spawn.
    norm = _normalized(_text(BATCH_SKILL))
    assert norm.index('operation="prepare"') < norm.index("## c) bootstrap and bind before work")
    assert "git check-ignore .claude/worktrees/x" not in norm


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
            "MCP-created linked checkout",
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
    _assert_all(_text(BATCH_SKILL), (
        "owner parks the parent before worktree effects", "records reservations and exact creation intent",
        "bootstrap-only", "No source or task mutation is permitted before bind",
        "Obtain the actual native worker ID from the host", 'operation="bind", slug=S, worker_id=<actual host ID>',
        "owner uses its recorded W/branch; callers do not choose them",
        "continue that same bound agent", "explicit mutation permission",
        "release additionally requires confirmed no external work",
    ), BATCH_SKILL)
    norm = _normalized(_text(BATCH_SKILL))
    assert norm.index('operation="prepare"') < norm.index('operation="bind"') < norm.index("explicit mutation permission")
    _assert_all(_text(TASK_LEAD), (
        "No task MCP call, task artifacts or commit is permitted before bind",
        "coordinator continues this same native agent", 'wbs(action="dispatch", operation="bind")',
        "never infer permission from original prose or edited state",
    ), TASK_LEAD)
    _assert_all(_text(OWNER_REQ), (
        "Reserved and running requests consume slots", "Never expire a reservation solely because time passed",
        "worker, worktree, branch, task and current run, with valid close evidence",
    ), OWNER_REQ)


def test_pool_capacity_refill_and_serial_integration_keep_lifecycle_gates():
    _assert_all(_text(BATCH_SKILL), (
        "Default lead cap is 3", "Reserve at least one slot for independent review/QA",
        "available_slots` (minimum zero)", "No free slot means no spawn",
        "Integrate completed leads in completion order and refill through `prepare` without a whole-wave barrier",
        "Never run integrations concurrently",
        "dependencies release only after their predecessor integrates and cleanup completes",
        "unknown liveness forbids opening a main task",
        "`review-code`, conditional `review-security`, then `qa-cli`",
        "receipt-backed review-before-QA order", 'before `wbs(action="close")`',
        "Operational closure never replaces review/QA or closes the WBS itself",
    ), BATCH_SKILL)
    _assert_all(_text(OWNER_REQ), (
        "explicit `--max-leads`, then optional manifest `batch.max_leads`, then 3",
        "above 8 clamp to 8", "Invalid explicit values fail; invalid manifest values fall back to 3",
    ), OWNER_REQ)


def test_resume_agent_reuses_exact_worktree_and_task_generation_without_isolation():
    path = REPO / "plugin/agents/task-lead-resume.md"
    meta = _frontmatter(path)
    assert meta["name"] == "task-lead-resume"
    assert meta["model"] == "inherit"
    assert "isolation" not in meta
    assert "tools" not in meta
    _assert_all(_text(path), (
        "Resolve W and pass that exact working directory to every command and nested agent before task tools",
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
        "Prefer the original agent",
        "harness:task-lead-resume",
        "non-isolating",
        'operation="resume", slug=S, worker_stopped=true',
        "Confirm through the host that that worker and all nested writers stopped",
    ), BATCH_SKILL)


def test_abandonment_retains_source_and_truthful_unfinished_disposition():
    _assert_all(_text(BATCH_SKILL), (
        "Only an explicit user choice permits abandonment", "retains source, branch, evidence and unresolved scope ownership",
        "never cancels/closes the task or satisfies the WBS", "exceptional owner commands in `batch_state.py`",
        "actual stopped-writer proof", "Label abandoned requests as retained work or never dispatched",
        "Do not claim overall completion while owned cleanup remains unresolved",
    ), BATCH_SKILL)
    _assert_all(_text(OWNER_REQ), (
        "abandon --slug S --worker-stopped --reason TEXT",
        "Refuse unresolved active work or cleanup; distinguish retained abandonment from integrated completion",
        "does not delete unintegrated source, force-delete branches, modify task status",
        "Unknown post-effect outcomes or pending integrated cleanup remain recoverable and cannot be hidden by abandonment",
    ), OWNER_REQ)


def test_native_bootstrap_retention_precedes_bind_and_exact_worker_message():
    assert "SendMessage" in _frontmatter(BATCH_SKILL)["allowed-tools"].split(", ")
    norm = _normalized(_text(BATCH_SKILL))
    assert norm.index("creates the registered worktrees") < norm.index('operation="bind"') < norm.index("native `sendmessage`")
    _assert_all(_text(BATCH_SKILL), (
        "agent verifies registration, branch/HEAD and helper marker", "No source or task mutation is permitted before bind",
        "same bound agent", "explicit mutation permission", "including blocked and zero-source returns",
    ), BATCH_SKILL)
    lead = _normalized(_text(TASK_LEAD))
    assert lead.index("helper-created `.harness-batch-bootstrap`") < lead.index("return bootstrap json") < lead.index('operation="bind"') < lead.index("`sendmessage`")
    _assert_all(_text(TASK_LEAD), (
        "Never create a worktree, write a substitute marker, start a task or edit source",
        "Keep `.harness-batch-bootstrap` through all final returns, including blocked, failed and task-only/zero-source closed results",
        "Never stage, edit or remove it", "git add <intended paths>` excluding `.harness-batch-bootstrap`",
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
        "It is removed only by managed disposal after harvest",
        "Unknown marker bytes, links, tracking or other dirty files refuse",
        "standalone finish has no exemption",
    ), BATCH_SKILL)
    _assert_all(_text(OWNER_REQ), (
        "Managed completion validates the marker and removes it only after durable harvest, immediately before ordinary or guarded S1 removal",
    ), OWNER_REQ)
    _assert_all(_text(TASK_LEAD), (
        "It grants no PASS or task authority",
        "managed finish removes it only after durable evidence harvest, immediately before worktree removal",
    ), TASK_LEAD)


def test_selected_modules_require_opt_in_and_prepared_same_worker_handoff():
    # Permission is split across intake, coordinator binding and the receiving
    # lead. Keeping one surface permissive must not silently widen the others.
    batch = _normalized(_text(BATCH_SKILL))
    lead = _normalized(_text(TASK_LEAD))
    for token in ("submodules: [module paths]", "Public WBS dispatch does not add a separate submodule-selection field",
                  "omission keeps default exclusions", "reserve the whole gitlink"):
        assert _normalized(token) in batch
    bind = batch.index('operation="bind"')
    prepared = batch.index("all selected modules prepared", bind)
    handoff = batch.index("continue that same bound agent", prepared)
    assert bind < prepared < handoff
    assert "same bound agent" in batch[handoff:]
    assert "confirmed prepared-module manifest" in lead
    assert "only coordinator bind prepares those clones" in lead
    assert "only explicitly selected, coordinator-prepared s1 modules within your declared scope" in lead
    assert "missing or mismatched authority means stay stopped" in lead


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
