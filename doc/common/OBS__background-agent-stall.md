# OBS — background agent stall pattern (sub-f)
tags: [obs, agent-runtime, stall, diagnosis]
task: TASK__plan-workflow-sub-j-stall-diagnosis
created: 2026-04-11

## Symptom summary

During TASK__plan-workflow-sub-f-autoplan-parity, 4 background `harness:developer` agents
were launched in parallel via `Agent({run_in_background: true})`. Three of the four (A1 CEO,
A2 Eng, A4 DX) stalled after an initial burst of edits: their jsonl files froze at
11:08–11:09 UTC, no further assistant turn was serialized, but completion notifications
arrived 20+ minutes later with truncated `result` text mid-sentence. The A3 Design agent
completed cleanly. The same pattern appeared in foreground agents: the B1-B8 foreground
developer (a0febd) stopped at "Now run all the verification greps" without executing them;
critic-runtime first pass (a26e2ac) stopped at "Now write the verdict" after 5 tool uses
without writing it. Re-dispatched critic-runtime with an explicit "write verdict immediately"
brief (a04bef3) completed in 2 tool uses / 61 seconds.

## Per-agent timeline (sub-f background)

| Agent ID | Role | first_ts | last_ts | elapsed_s | last_stop_reason | last_tool | tool_count | bytes | outcome |
|----------|------|----------|---------|-----------|------------------|-----------|------------|-------|---------|
| a6b1de157a3a65f48 | A1 CEO | 11:06:59Z | 11:08:48Z | 108 | tool_use | Read | 21 | 191917 | stalled mid-edit |
| a56853bc8530b0b4c | A2 Eng | 11:07:15Z | 11:09:08Z | 113 | tool_use | Edit | 16 | 155760 | stalled mid-edit |
| aa2b76fa6782c2642 | A3 Design | 11:07:27Z | 11:08:46Z | 79 | end_turn | Bash | 11 | 97775 | **completed cleanly** |
| a9974dc3578c4d9c6 | A4 DX | 11:07:40Z | 11:09:03Z | 82 | tool_use | Edit | 18 | 140243 | stalled mid-edit |

All timestamps are 2026-04-11, UTC. Elapsed = wall time from first to last serialized message.

## Per-agent timeline (sub-f foreground)

| Agent ID | Role | first_ts | last_ts | elapsed_s | last_stop_reason | last_tool | tool_count | bytes | outcome |
|----------|------|----------|---------|-----------|------------------|-----------|------------|-------|---------|
| a0febd522d0ad7263 | B1-B8 developer | 11:17:06Z | 11:19:53Z | 167 | tool_use | Bash | 15 | 109843 | stalled: greps not run, no commit |
| a26e2ac34bf5a43b7 | critic-runtime pass 1 | 11:32:10Z | 11:33:02Z | 52 | tool_use | write_critic_runtime | 5 | 36868 | stalled: verdict not written |
| a04bef3e4b2446e07 | critic-runtime pass 2 (re-dispatch) | 11:23:41Z | 11:24:42Z | 61 | end_turn | write_handoff | 2 | 39568 | **completed cleanly** |
| a5e7198ec78ab593e | writer | 11:34:20Z | 11:36:19Z | 119 | end_turn | write_doc_sync | 8 | 78951 | **completed cleanly** |
| a5227467802fa019c | critic-document | 11:37:00Z | 11:37:58Z | 57 | end_turn | write_critic_document | 15 | 99190 | **completed cleanly** |

## Foreground observations

- B1-B8 developer (a0febd): last assistant text was "Now run all the verification greps."
  stop_reason=tool_use on a Bash call — the Bash result arrived but no subsequent assistant
  turn was serialized. 0 commits from this agent.
- critic-runtime first pass (a26e2ac): last assistant text ended "Now write the verdict."
  stop_reason=tool_use on write_critic_runtime — the MCP tool call was issued but no result
  turn or follow-up assistant turn was serialized.
- critic-runtime re-dispatch with explicit "write verdict immediately" brief (a04bef3):
  completed in 2 tool uses / 61 s, end_turn, verdict written successfully.

## Stop-gate interaction

`stop_gate.py` runs on the **main session** stop hook. It reads `doc/harness/tasks/TASK__*/TASK_STATE.yaml`
and blocks the main-session stop when open tasks exist. It has no mechanism to inject into or
affect subagent sessions — subagents run with their own stop hooks via `subagent_stop_gate.py`
(a separate script), and background agents are not subject to the coordinator's stop hook at
all. The repeated "HARNESS STOP GATE: open tasks remain" messages in the coordinator session
during the stall window were the coordinator's own stop hook firing, not a causal factor in
the background agent stalls.

## Primary hypothesis

**Context exhaustion / turn-budget truncation during open-ended "iterate until done" prompts
on large files, with no explicit terminal instruction.**

Evidence:

1. **Size correlation.** Stalled agents had jsonl sizes 140–192 KB (16–21 tool calls).
   A3 completed at 98 KB (11 tool calls). The stalled agents processed larger files with
   more required edit iterations.

2. **stop_reason=tool_use as the terminal record.** In all 3 background stalls and both
   foreground stalls, the last serialized message is an assistant turn whose stop_reason
   is `tool_use` — meaning the model issued a tool call and the tool result arrived (user
   turn), but no subsequent assistant turn was ever serialized. This is consistent with
   a context/budget ceiling being hit after the tool result was fed back: the model loop
   silently stopped scheduling the next assistant generation.

3. **Foreground stalls match the pattern.** The B1-B8 foreground developer and the
   first critic-runtime pass stalled identically — stop_reason=tool_use, tool result
   arrived, no follow-up assistant turn. This rules out `run_in_background: true` as a
   causal factor. The stall is not a background-mode issue.

4. **Re-dispatch recovery.** critic-runtime pass 2 was given an explicit "write verdict
   immediately" brief and completed in 2 tool calls / 61 seconds. This confirms the
   model CAN finish when the prompt provides a tight terminal instruction; the stall
   correlates with prompts that say "iterate until greps pass" or "after greps pass,
   commit" — leaving the agent in an uncertain loop about whether a terminal condition
   has been met.

5. **Completion notification delay.** Notifications arrived 20+ minutes after the last
   jsonl write. This is consistent with the subagent runtime holding the session open
   (waiting for a next-turn response that never comes) until a server-side timeout fires
   and forces a flush, producing the truncated `result` text.

Secondary: the prompts for A1/A2/A4 and B1-B8 contained open-ended continuation language
("iterate until verification passes", "now update X section") which, combined with large
existing context (many prior Edit results in history), left the model unable to determine
a clean stopping point once the context window approached saturation.

## Coordinator workarounds

1. **Tight terminal instruction.** Every developer/critic dispatch must end with an explicit
   terminal sentence such as "After the commit, stop. Do not continue." or "Write the verdict
   now. Stop after write_critic_runtime returns." Prompts that say "iterate until PASS" or
   "now update section X" without a terminal instruction correlate with stalls.

2. **One-file-one-commit slicing with explicit commit-now gate.** Large multi-file work
   should be dispatched as one agent per file with a "commit immediately after greps pass,
   then stop" requirement. The A3 Design case (smallest file, fewest edits, 11 tool calls,
   clean end_turn) proves this works. Keep the expected tool count to ≤12 tool uses per
   agent dispatch to stay below the apparent saturation threshold.

3. **SendMessage / re-dispatch recovery.** When an agent stalls (notification arrives with
   truncated result and stop_reason=tool_use), re-dispatch a new brief that says exactly
   "run X now and stop" or "write verdict now" rather than an open-ended continuation.
   Do not attempt to continue the stalled session; issue a new agent.

4. **Foreground for quality-critical passes.** critic-runtime and final HANDOFF refreshes
   should run foreground so stalls are caught immediately (visible in the active turn)
   rather than blocking the coordinator for 20 minutes waiting for delayed notifications.

## Follow-up recommendation

No code fix needed at this time. The stall is a **coordinator-prompt discipline issue**:
open-ended "iterate until done" prompts on large-context agents hit a silent context/budget
ceiling and the subagent runtime holds the session open until server-side timeout. The
workarounds above (tight terminal instruction, small-slice dispatch, re-dispatch recovery,
foreground for critic passes) are sufficient for sub-h and sub-i.

Track as `TASK__plan-workflow-sub-k-prompt-templates-for-tight-dispatch` if the team wants
to codify these constraints as a harness REQ note and add prompt template stubs to the
dispatcher skill. Otherwise, treat this OBS as the standing record.

Consider filing with harness plugin maintainers: subagent budget/context exhaustion does
not surface as a clean `stop_reason` (it appears as `tool_use` with a missing follow-up
turn), making it indistinguishable from a normal mid-turn tool call at the jsonl layer.
A dedicated `stop_reason: context_limit` or `stop_reason: budget_exhausted` would allow
the subagent stop hook to detect and report this automatically.

## References

- Raw jsonls (session f456571c):
  - `/home/ccc/.claude/projects/-project-harness-3b4e5969eaf9/f456571c-f1d2-486b-b1af-ea0f93f6092c/subagents/agent-a6b1de157a3a65f48.jsonl` (A1 CEO)
  - `/home/ccc/.claude/projects/-project-harness-3b4e5969eaf9/f456571c-f1d2-486b-b1af-ea0f93f6092c/subagents/agent-a56853bc8530b0b4c.jsonl` (A2 Eng)
  - `/home/ccc/.claude/projects/-project-harness-3b4e5969eaf9/f456571c-f1d2-486b-b1af-ea0f93f6092c/subagents/agent-aa2b76fa6782c2642.jsonl` (A3 Design — baseline, completed cleanly)
  - `/home/ccc/.claude/projects/-project-harness-3b4e5969eaf9/f456571c-f1d2-486b-b1af-ea0f93f6092c/subagents/agent-a9974dc3578c4d9c6.jsonl` (A4 DX)
  - `/home/ccc/.claude/projects/-project-harness-3b4e5969eaf9/f456571c-f1d2-486b-b1af-ea0f93f6092c/subagents/agent-a0febd522d0ad7263.jsonl` (B1-B8 foreground developer)
  - `/home/ccc/.claude/projects/-project-harness-3b4e5969eaf9/f456571c-f1d2-486b-b1af-ea0f93f6092c/subagents/agent-a26e2ac34bf5a43b7.jsonl` (critic-runtime pass 1)
  - `/home/ccc/.claude/projects/-project-harness-3b4e5969eaf9/f456571c-f1d2-486b-b1af-ea0f93f6092c/subagents/agent-a04bef3e4b2446e07.jsonl` (critic-runtime pass 2, completed)
- PLAN.md and CHECKS.yaml: `doc/harness/tasks/TASK__plan-workflow-sub-j-stall-diagnosis/`
- Sub-f task dir: `doc/harness/tasks/TASK__plan-workflow-sub-f-autoplan-parity/`
