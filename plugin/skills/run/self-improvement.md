# Self-Improvement & Tiered Learning

Sub-file for run/SKILL.md. After each task close, regardless of outcome, run this pipeline. See `plugin/CLAUDE.md` §12 for the tiered-learning model. This file covers the run-skill-specific mechanics.

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
If fix is ambiguous or risky, log the signal only. Do NOT modify manifest without clear evidence.

Signals feed back: plan skill reads `learnings.jsonl` at Phase 0.1.5; setup reads it in repair mode. Improvements compound across tasks.

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

**Rule: write a doc immediately when you discover something.** Don't wait for repetition.

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

Append if doc exists — never overwrite.

**When to write:** any discovery that saves 5+ min in a future session (build quirks, env vars, ordering, ports, framework specifics). Check after every task close.

---

## Mandatory promotion + pruning (after every task close)

Housekeeping, not a gate. If any step fails, log a warning and continue. learnings.jsonl is staging, not permanent storage.

### Step 1: Aggregate learnings by key

```bash
python3 -c "
import json, collections
counts = collections.Counter()
entries = []
with open('doc/harness/learnings.jsonl') as f:
    for line in f:
        try:
            e = json.loads(line.strip())
            k = e.get('key', '')
            if k: counts[k] += 1
            entries.append(e)
        except: pass
for k, c in counts.most_common(20):
    print(f'{c}\t{k}')
" 2>/dev/null
```

### Step 2: Promote keys with count ≥ 2 to Tier 2

Append (or update) in `doc/harness/patterns/<topic>.md`:
```markdown
| <key> | <date> | run-auto-promote |
## <key>
<latest insight>
**Promoted from learnings:** N occurrences across M tasks
```
Update count + latest insight if entry exists.

### Step 3: Prune promoted entries from learnings.jsonl

```bash
python3 -c "
import json
from collections import Counter
entries = []
with open('doc/harness/learnings.jsonl') as f:
    for line in f:
        try: entries.append(json.loads(line.strip()))
        except: pass
counts = Counter(e.get('key','') for e in entries if e.get('key'))
promoted = {k for k, c in counts.items() if c >= 2}
with open('doc/harness/learnings.jsonl', 'w') as f:
    for e in entries:
        if e.get('key','') not in promoted:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
" 2>/dev/null
```

### Step 4: Prune stale entries (>90 days, keep eureka/calibration forever)

```bash
python3 -c "
import json, datetime
cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=90)).strftime('%Y-%m-%dT%H:%M:%SZ')
entries = []
with open('doc/harness/learnings.jsonl') as f:
    for line in f:
        try:
            e = json.loads(line.strip())
            ts = e.get('ts', '')
            tp = e.get('type', '')
            if tp in ('eureka', 'confidence-calibration') or ts >= cutoff:
                entries.append(e)
        except: pass
with open('doc/harness/learnings.jsonl', 'w') as f:
    for e in entries:
        f.write(json.dumps(e, ensure_ascii=False) + '\n')
" 2>/dev/null
```

### Step 5: Promote Tier 2 → Tier 1 (CLAUDE.md)

If any pattern doc is referenced in 2+ tasks (check `git log` on the pattern doc path), promote the one-liner into the project `CLAUDE.md` under the appropriate section. One line per entry — details stay in the pattern doc.
