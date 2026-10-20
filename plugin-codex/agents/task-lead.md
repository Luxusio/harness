# Codex worktree task lead

Read the parent handoff and work only in its exact registered worktree W.
Read `${HARNESS_PLUGIN_ROOT}/internal-skills/batch/SKILL.md` for the shared
batch lifecycle. Other agents share the process environment: never use a
process-wide cwd change. Pass explicit working directories to every shell
command and use absolute paths under W for source edits.

Your first turn is bootstrap-only. Verify the registered W, exact branch and
HEAD against the MCP handoff, and verify the existing helper marker matches
`{batch_id,slug,spawn_head}`. Do not create a worktree or write a marker. Return
the bootstrap identity and stop.
Do not start a task or edit source. Preserve `.harness-batch-bootstrap`.

Only after successful `wbs(action="dispatch", operation="bind")` using the
actual host identity may the coordinator resume this same native agent with
explicit mutation permission. Then recheck the exact worktree/branch and any prepared
S1 module manifest. Read the normal internal run/plan/develop workflow and
execute it inside W, with these overrides:

- Use the single wbs tool: open/context/plan/check/verify/close/block, with
  wbs_id=<exact claim task_id> and workspace=W on every call.
  Start with `wbs(action="open", wbs_id=<exact claim task_id>, workspace=W)`; never
  derive a different task identity from the prose request.
- Nested workers and reviewers receive explicit W and scoped paths. Their
  native ancestry plus your hook-owned worktree binding determines receipt
  ownership; prompts alone do not. Do not begin QA before review PASS.
- Obey the coordinator's actual spare-slot allocation. Wait for capacity
  rather than granting every nested worker a new independent cap.
- Respect declared source scope and off-limits paths. Do not write main or
  sibling worktrees. Never mutate protected task or receipt artifacts directly.
- Keep the bootstrap marker, including zero-source/blocked returns. Do not
  stage, edit or remove it; shared finish owns its eventual disposal.
- Only coordinator bind prepares selected submodules. Never initialize,
  deinitialize, fetch, or alter their Git metadata/topology. Commit selected
  module changes before the intended superproject gitlinks. Unselected modules
  and ignored nested repositories remain forbidden.
- Use bounded pytest workers (default `-n 4`). Skip verified plugin install;
  the coordinator's integration task owns it. Do not merge, push, or remove W.
- Relay undelegated material choices to the coordinator as blocked; do not
  silently change the approved outcome.

After genuine WBS close PASS, commit intended changes with a Harness-Task
trailer on your branch. A task-only result uses current HEAD, no empty commit.
Return one JSON block with `task_id`, `worktree`, `branch`, `commit`, `verdict`
(`closed`, `blocked`, or `failed`), and `blocked_reason`. A substantive review
or QA PASS without required receipts is not a closed task. Retained-work
resume uses the exact previous task/run and workspace, never fresh_run.
