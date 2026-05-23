# Self-Improvement & Tiered Learning

Sub-file for run/SKILL.md. After each task close, regardless of outcome, run this pipeline. See `plugin/CLAUDE.md` §11 for the tiered-learning model. This file covers the run-skill-specific mechanics.

---

## Signals to detect during the cycle

1. **Wrong verification strategy** — manifest says "library" but critic needed browser QA; or "web_app" with no dev_command stored.
2. **Missing manifest fields** — test_command wrong, build_command missing, entry_url incorrect.
3. **Repeated critic failures** — same failure class across 2+ tasks (e.g., "missing test coverage" every time → test framework needs bootstrap).
4. **Phase friction** — a phase consistently takes 3+ retry cycles.
5. **New project patterns** — project evolved (added frontend, new framework, new port) but manifest stale.

## Log improvements

```bash
_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown")
mkdir -p doc/harness 2>/dev/null || true
echo '{"ts":"'"$_TS"'","type":"harness-improvement","source":"run","key":"SHORT_KEY","insight":"DESCRIPTION","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

## Auto-fix during close (only when safe)

1. **Stale manifest field** — update with the correct value discovered during the task.
2. **Missing dev_command** — if browser QA was needed and dev server was discovered, store it.
3. **Wrong project type** — if the critic had to switch strategies, update manifest.

Before auto-fixing, report:
```
Harness improvement: <what was wrong> → <what was fixed>
```
If fix is ambiguous or risky, log the signal only. Modify the manifest only with clear evidence.

Signals feed back: plan skill reads `learnings.jsonl` at Phase 0.1.5; setup reads it in repair mode. Improvements compound across tasks. Treat this file as local staging only: it is gitignored and does not share knowledge with future contributors until the lesson is promoted to a committed artifact.

---

## Runbook candidates from discovered commands

When develop or QA turns a failing setup/test/dev-server command into a working
command, capture it as a pending runbook candidate instead of baking the
project-specific command into harness core. This applies to repo-local facts
such as required env vars, container fallback commands, local DB wrappers,
dev-server ports, migration apply recipes, and browser QA bootstrap commands.

Capture only when all of these are true:

- the final command succeeded or reached the next useful blocker;
- the command is repo-specific enough that future contributors would benefit;
- no secret-like value is present; redact tokens, passwords, and private keys;
- the lesson is a repeatable command recipe, not just a one-off error message.

Use the helper so the candidate stays pending until close-time review:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/runbook_memory.py capture \
  --id "<short-id>" \
  --description "<what this starts/tests/verifies>" \
  --failed-command "<representative failed command>" \
  --command "<final successful command or wrapper script>" \
  --failure-class "<missing-env|wrong-host|crlf-env|missing-tool|dev-server-bootstrap|db-bootstrap>" \
  --source-phase "<develop|qa-browser|qa-api|qa-cli>" \
  --source-task "<task_id>" \
  --gotcha "<why the first attempt failed>"
```

Do not auto-approve candidates at capture time. Close-time
`Self-Healing Candidates` owns the decision:

- `applied` — approve the candidate into `doc/harness/runbooks.yaml` or replace
  it with a committed manifest/script/doc update that prevents recurrence.
- `deferred` — ask the user first, then record the proposed artifact/task.
- `rejected` — skip noisy, one-off, stale, or unsafe candidates.

Approved runbooks are repo-local execution memory surfaced by
`prompt_memory.py`; pending candidates are visible but advisory.

---

## Write learnings as docs (primary path)

Most learnings go directly into readable Tier 2 docs under `doc/harness/patterns/`:

```
doc/harness/patterns/
├── testing.md          # test conventions, framework quirks
├── build.md            # build commands, ordering, env requirements
├── verification.md     # verify strategy, dev server, browser QA
└── architecture.md     # module boundaries, dependency patterns, gotchas
```

**Rule: write a doc immediately when you discover something.** Capture useful discoveries while they are fresh.

Each doc starts with a summary table, then concrete details:

```markdown
# <Topic> Patterns

| Pattern | Discovered | Source |
|---------|------------|--------|
| <pattern> | <date> | TASK__<id> |

## <Pattern Name>

<context>

**Why:** <reason this matters>
**How to apply:** <what to do differently>
```

Append to existing docs and preserve their current content.

**When to write:** any discovery that saves 5+ min in a future session (build quirks, env vars, ordering, ports, framework specifics). Check after every task close.

## Commit-backed promotion checkpoint

Before task close, classify every reusable discovery, dogfood finding, user
correction, setup recipe, repeated friction point, and harness-improvement
signal as one of:

- `captured` — promoted in this task to a committed artifact such as
  `plugin/skills/**`, `plugin/scripts/**`, `tests/**`,
  `doc/harness/patterns/*.md`, `doc/common/GUIDE__*.md`, or another durable
  doc. The HANDOFF must list the committed path.
- `rejected` — considered but intentionally not shared because it is
  task-local, noisy, outdated, personal preference, or lacks a trigger/action/
  verification rule. The HANDOFF must give the reason.
- `none` — no reusable learning occurred.

`doc/harness/learnings.jsonl` by itself is never enough for `captured`; it is
only the staging queue. The close gate requires HANDOFF to include
`## Commit-backed Learnings` with `Status: none`, `Status: captured`, or
`Status: rejected` so local-only learnings cannot disappear silently.

## Feedback-derived rules (user correction → readable behavior)

When a user correction points at an agent mistake, extract only a reusable conditional behavior rule:

```markdown
## <Short Rule Name>

When <trigger>, <action>.

Verify by <observable check>.
```

Good Tier 2 entry:

```markdown
## Runtime-Specific Plugin Changes

When changing runtime-specific harness plugin behavior, review both the canonical `plugin/` tree and the runtime-specific tree such as `plugin-codex/`.

Verify by explaining in `HANDOFF.md` which side changed and why any other side was left unchanged.
```

Reject feedback-derived entries when they lack a trigger, action, or verification step; when they only describe blame; or when they are task-local preferences. The close-time requirement is judgment, not forced documentation: HANDOFF records `none`, `captured`, or `rejected`. Reusable feedback that should affect all future agents must be captured in a committed artifact, not only in `learnings.jsonl`.

---

## Mandatory promotion + pruning (after every task close)

Housekeeping, not a gate. If any step fails, log a warning and continue. learnings.jsonl is staging, not permanent storage.

### Steps 1–5: Automated promotion + pruning

All five steps (aggregate by key, promote to Tier 2 patterns, prune promoted,
prune stale >90 days, report Tier 1 candidates) are handled by a single script:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/promote_learnings.py 2>/dev/null || true
```
### Step 5c: Auto-retro trigger

After promote_learnings.py, check if a retro should fire (>=3 tasks closed since last retro):

```bash
_LAST_RETRO=$(ls -t doc/harness/retros/*.md 2>/dev/null | head -1)
_LAST_RETRO_TS=$(stat -c %Y "$_LAST_RETRO" 2>/dev/null || echo 0)
_TASKS_SINCE=$(python3 -c "
import json, sys, os
tl = 'doc/harness/timeline.jsonl'
last_ts = int('$_LAST_RETRO_TS')
count = 0
if os.path.isfile(tl):
    with open(tl) as f:
        for ln in f:
            try:
                e = json.loads(ln)
                if e.get('event') == 'completed' and e.get('skill') == 'run':
                    import datetime
                    ts_str = e.get('ts','')
                    if ts_str:
                        try:
                            from datetime import datetime, timezone
                            t = datetime.strptime(ts_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
                            if int(t.timestamp()) > last_ts:
                                count += 1
                        except Exception:
                            pass
            except Exception:
                pass
print(count)
" 2>/dev/null || echo 0)
if [ "\${_TASKS_SINCE:-0}" -ge 3 ] && [ "\${HARNESS_DISABLE_RETRO:-}" != "1" ]; then
  _RETRO_FIRST=$([ -z "$_LAST_RETRO" ] && echo "true" || echo "false")
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/retro.py --save 2>/dev/null && _RETRO_OUT=$(ls -t doc/harness/retros/*.md 2>/dev/null | head -1) || _RETRO_OUT=""
  if [ "$_RETRO_FIRST" = "true" ] && [ -n "$_RETRO_OUT" ]; then
    echo "Auto-retro enabled. Silence with HARNESS_DISABLE_RETRO=1. Output at $_RETRO_OUT."
  fi
  echo "Auto-ran: retro=$_RETRO_OUT"
fi
```

**HANDOFF Auto-ran section:** Developer Phase 8 must include a section:
```
## Auto-ran
- retro: <path or "(none, threshold not met)">
- hygiene: <N warnings or "(none)">
```
If pipeline output is empty, emit `Auto-ran: (none, threshold not met)`.


The script:
- Promotes keys with ≥2 occurrences to `doc/harness/patterns/<topic>.md`
- Auto-maps keys to topic files (test→testing.md, build→build.md, verify→verification.md, etc.)
- Prunes promoted + stale (>90 day, non-eureka) entries from learnings.jsonl
- Reports Tier 1 candidates (pattern docs with 2+ git commits → promote one-liner to CLAUDE.md)

Use `--dry-run` to preview without modifying files. `--threshold N` to adjust the promotion bar.

### Step 5b: Promote Tier 2 → Tier 1 (CLAUDE.md)

When `promote_learnings.py` reports Tier 1 candidates, manually promote the key fact
as a one-liner into the project `CLAUDE.md` under the appropriate section.
Details stay in the pattern doc.
