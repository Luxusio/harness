---
name: maintain
description: |
  Audit and repair the host project's contracts system. Reads CONTRACTS.md,
  runs contract_lint, diffs against the current harness template, and
  proposes Edit-based repairs. Edits only the managed block; never touches
  CONTRACTS.local.md or user-owned sections of CLAUDE.md.

  Trigger keywords: "maintain", "contract drift", "CLAUDE.md 정리",
  "규약 정비", "contracts 꼬임", "harness upgrade cleanup".

  Also auto-suggested by the harness agent when the SessionStart hook
  reports hard drift in CONTRACTS.md.
user-invocable: true
allowed-tools: Read, Bash, Edit, AskUserQuestion
---

## Voice

Direct, terse. Show diffs, ask once, apply. Never bulk-rewrite.

## When to run

- User says "maintain", "regulations drifted", "claude.md 정비", etc.
- SessionStart hook reported HARD drift in CONTRACTS.md.
- After a harness release (template changed, host project needs catch-up).
- User is confused by rule-enforcement behavior and suspects contract drift.

## Flow

### Phase 0: Locate files

```bash
_CONTRACTS="CONTRACTS.md"
_LOCAL="CONTRACTS.local.md"
_CLAUDE="CLAUDE.md"
_TEMPLATE="${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/CONTRACTS.md"

for f in "$_CONTRACTS" "$_TEMPLATE"; do
  [ -f "$f" ] || { echo "MISSING: $f"; }
done
```

If `CONTRACTS.md` is missing: suggest running setup skill, exit.
If template is missing: the plugin install is broken — exit with error.

### Phase 1: Lint report

Run the full (non-quick) lint and capture the report:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/contract_lint.py" \
  --path "$_CONTRACTS" --repo-root . 2>&1 | tee /tmp/maintain_lint.txt
_EXIT=${PIPESTATUS[0]}
```

Classify findings:
- `[HARD]` lines: must be fixed before close.
- `[SOFT]` lines: proposed fixes surfaced to user.
- `[INFO]` lines: status only.

### Phase 2: Extract managed blocks (host vs template)

Extract the managed block from both files for comparison:

```bash
python3 - <<'PY'
import re, sys
def block(p):
    t = open(p).read()
    m = re.search(r"<!--\s*harness:managed-begin.*?-->(.*?)<!--\s*harness:managed-end\s*-->",
                  t, re.DOTALL)
    return m.group(1) if m else None

host = block("CONTRACTS.md")
tmpl = block("${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/CONTRACTS.md")
open("/tmp/maintain_host.txt","w").write(host or "")
open("/tmp/maintain_tmpl.txt","w").write(tmpl or "")
print("host block:", len(host or ""), "chars")
print("tmpl block:", len(tmpl or ""), "chars")
PY

diff -u /tmp/maintain_host.txt /tmp/maintain_tmpl.txt > /tmp/maintain_diff.txt || true
```

If either block is unextractable (marker damage): go to Phase 3 marker repair
first. Otherwise proceed to Phase 4.

### Phase 3: Marker repair (only if extraction failed)

Do NOT bulk-rewrite CONTRACTS.md. Instead:

```
AskUserQuestion:
  Question: "CONTRACTS.md managed-block markers are damaged. Options:"
  Options:
    - A) Rename existing file to CONTRACTS.broken.md and install fresh template (preserves your file)
    - B) Show me the file so I can repair markers manually
    - C) Skip — I will repair later
```

On A: move the old file aside with `mv`, then `cp` the template in place.
Do not merge content automatically — user can port their content from
`.broken.md` by hand.

### Phase 4: Drift categorization

From the diff, group changes:

1. **New contracts** — in template but not host. List ids (C-##) + titles.
2. **Removed contracts** — in host but not template. Likely harness dropped
   them in a release.
3. **Modified contracts** — same id, body differs. List ids + a 2-line summary
   of the change.
4. **Matrix drift** — § 1 table rows differ.

### Phase 5: Propose repair (one AskUserQuestion per category)

For each non-empty category, show the user a bounded diff and ask:

```
AskUserQuestion:
  Question: "<N> new contracts in harness template: <C-16, C-17, ...>. Apply?"
  Context: <first ~20 lines of the proposed additions>
  Options:
    - A) Apply all
    - B) Review each one individually (iterate)
    - C) Skip this batch
```

Identical question shape for **removed** (user may want to keep them as
project-specific C-100+ → move to CONTRACTS.local.md) and **modified**
(show unified diff of the changed bodies).

### Phase 6: Apply with Edit tool

Never use Write on CONTRACTS.md — always Edit to preserve surrounding
content and reduce blast radius. For each approved change:

- **Add a new contract:** Edit — insert the new `### C-##` block just
  before the `<!-- harness:managed-end -->` marker.
- **Remove a contract:** Edit — delete the exact block. If the user chose
  "keep as C-100+", also Edit `CONTRACTS.local.md` to append the block
  with a new id.
- **Modify a contract:** Edit — replace the old block's body only. Keep
  the heading (C-##) stable so matrix links don't break.
- **Matrix drift:** Edit — update the § 1 table rows.

After every Edit, re-run `contract_lint.py` to confirm the change did not
introduce new drift:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/contract_lint.py" --path CONTRACTS.md
```

If lint now reports a NEW hard issue caused by our edit, roll back that
Edit (reverse the change) and report the failure — do not chain broken
states.

### Phase 7: Verify CLAUDE.md import

Check that `@CONTRACTS.md` is still imported:

```bash
grep -qF "@CONTRACTS.md" CLAUDE.md || echo "WARN: @CONTRACTS.md import missing"
```

If missing, re-propose the injection (same prompt as setup Phase 3.7.3).
Never touch CLAUDE.md outside that one-line insertion.

### Phase 8: Re-interview (when project character drift suspected)

Trigger conditions (any):
- User explicitly asked to re-open the interview ("re-interview", "re-ask setup questions").
- `doc/common/CLAUDE.md summary:` no longer matches recent commit subjects (scan `git log --format=%s -50`; if 0 overlap with summary tokens, flag drift).
- `doc/harness/manifest.yaml` `execution_mode_default` contradicts last 10 tasks' actual modes.

Flow:

```bash
_ANS="doc/harness/.interview-answers.json"
if [ ! -f "$_ANS" ]; then
  echo "No prior interview on file — direct user to run setup skill."
  exit 0
fi

# Schema version gate
_SV=$(python3 -c "import json; print(json.load(open('$_ANS')).get('schema_version','?'))" 2>/dev/null)
if [ "$_SV" != "1" ]; then
  echo "WARN: .interview-answers.json schema_version=$_SV (expected 1). Skip re-interview."
  exit 0
fi
```

Show the prior answers summary (Q1-Q6) and emit one AskUserQuestion per
question:

```
AskUserQuestion:
  Question: "Prior answer Q<N> (<short>): <value>. Update?"
  Options:
    - A) Keep as-is
    - B) Update — (please describe new answer)
    - C) Mark as drifted but keep (log for next session)
```

For each Q where the user chose B:
1. Replay that question's Apply-step from `project-interview.md` (Step 2)
   — update only the target file for that specific Q.
2. Rewrite the `answers.<qN>` block in `.interview-answers.json` atomically
   (tmp + rename). Bump `interviewed_at`. Do NOT touch other answer blocks.
3. Append a re-interview row to AUDIT_TRAIL.md:
   ```
   | <#> | re-interview | Q<N> updated | log | - | <short summary> | - |
   ```

Never bulk-rewrite `.interview-answers.json`. Only the Q-level blocks that
changed get touched.

### Phase 8.5: Schema migration guard

If `.interview-answers.json schema_version` > 1 is detected (future
harness release): refuse to re-interview, prompt user to update the
harness first:

```
AskUserQuestion:
  Question: "Interview answers file is schema_version=<N> but this maintain skill understands v1. How to proceed?"
  Options:
    - A) Skip re-interview — update harness first
    - B) Back up file and run fresh interview (writes v1)
    - C) Abort maintain
```

### Phase 9: Report

Summarize what changed in a 5-line report:

```
Maintain report
  Drift detected: <hard: N, soft: M>
  Applied: +<new> -<removed> ~<modified> contracts
  Matrix rows updated: <K>
  CONTRACTS.local.md: untouched (<bytes> bytes preserved)
  Lint after: <OK | issues>
```

Log to `doc/harness/learnings.jsonl`:
```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"operational","source":"maintain","key":"contract-repair","insight":"<summary>","task":"maintain"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

## Safety invariants (enforced by this skill)

- **C-15 (setup must not overwrite user files)** applies verbatim here.
- Never write `CONTRACTS.local.md`. Read-only.
- Never bulk-rewrite `CONTRACTS.md` — only Edit the managed block.
- Never touch `CLAUDE.md` outside the `@CONTRACTS.md` import line.
- If any step would require more than ~20 Edit operations to complete,
  STOP and AskUserQuestion — the damage is too large for safe automated
  repair, user must decide scope.

## Failure modes

- **Template unreadable:** plugin install broken. Direct user to reinstall.
- **Host CONTRACTS.md too divergent:** propose "fresh install + port"
  instead of thousand-line diff.
- **User rejected every proposed change:** exit cleanly. Drift remains
  logged for next session.
