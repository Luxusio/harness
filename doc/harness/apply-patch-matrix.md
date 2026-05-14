# `apply_patch` vs `Edit` semantic matrix

AC-002 deliverable. Documents the patterns where Codex's `apply_patch` tool diverges from Claude Code's `Edit` / `MultiEdit` tools, with the test status for each pattern under v1 sync engine.

**Why this matters:** when transforming Claude SKILL.md/agent prompts that call `Edit`/`MultiEdit` into Codex-runnable form, the rewrite must preserve semantics. `apply_patch` is diff-envelope-oriented (one call can rename + add + delete files); `Edit` is operation-oriented (one call edits one file). The naïve text substitution `Edit→apply_patch` produces broken Codex prompts for ~half the patterns below.

Reference: CODEX_REVIEW.md finding 4 (cross-model adversarial review by gpt-5.5 on openai/codex source).

---

## Pattern matrix

Each row: pattern → Claude form → Codex `apply_patch` form → status under v1 sync.

`Status` values:
- **✓ direct** — straight text rewrite works, no semantic change.
- **→ wrap** — needs envelope-level rewrap (e.g. multiple `Edit` calls fold into one `apply_patch` envelope).
- **⚠ caveat** — known parser/semantic difference, needs care.
- **✗ no-port** — pattern has no clean Codex equivalent; skill must be Claude-only OR rewritten.

| # | Pattern | Claude form | Codex `apply_patch` form | Status | v1 sync handling |
|---|---|---|---|---|---|
| 1 | **Single-hunk same-file** | `Edit { file_path, old_string, new_string }` | `*** Update File: <path>` + one `@@` hunk | ✓ direct | Text substitution + envelope wrap |
| 2 | **Multi-hunk same-file** | `MultiEdit { file_path, edits: [...] }` | `*** Update File: <path>` + multiple `@@` hunks | → wrap | Sync engine collapses `MultiEdit` calls into one `apply_patch` envelope per file |
| 3 | **Multi-file edit** | N×`Edit` calls in sequence | One `apply_patch` envelope with N `*** Update File:` blocks | → wrap | Sync engine groups consecutive `Edit` calls across files into one envelope; ordering preserved |
| 4 | **Add file** | `Write { file_path, content }` | `*** Add File: <path>` + content lines | ✓ direct | Text substitution: `Write` → `apply_patch` envelope with `Add File` |
| 5 | **Delete file** | No direct Claude tool (use `Bash rm`) | `*** Delete File: <path>` | ⚠ caveat | Codex-side wins (single envelope). Claude side stays Bash. Sync emits Codex form on transform |
| 6 | **Rename / move** | No direct Claude tool (use `Bash mv` + content rewrite) | `*** Update File: <oldpath>` with `*** Move to: <newpath>` | ⚠ caveat | Codex envelope is cleaner; Claude side stays Bash. Skill prose mentions both. |
| 7 | **Missing final newline** | `Edit` preserves whatever bytes given | `apply_patch` is sensitive to terminal newline in hunk content; can fail or silently strip | ⚠ caveat | Sync engine normalizes: append `\n` to hunk content if source file has one; never strip |
| 8 | **Path with spaces** | `file_path` is a string, no quoting needed | `*** Update File: "<path with spaces>"` — Codex may require quoting or escape | ⚠ caveat | Sync engine quotes paths containing whitespace per Codex convention; unit test in golden corpus |
| 9 | **Repeated identical hunks in same file** | `MultiEdit` deduplicates or accepts repeats per Claude semantics | `apply_patch` may reject ambiguous context if two `@@` hunks have identical headers | ⚠ caveat | Sync engine validates pre-emit: if two hunks are identical, collapse OR add disambiguating context lines |
| 10 | **Ambiguous context** | `Edit { old_string }` requires unique match in file | `apply_patch` hunk header `@@` selects by context lines, can match multiple sites if context is too short | ⚠ caveat | Sync engine adds 3 lines of leading/trailing context minimum on emit; matches `git diff` defaults |
| 11 | **Failed hunk rollback** | If `Edit`'s `old_string` not found, call fails atomically (file unchanged) | If one hunk in a multi-hunk envelope fails, Codex may apply earlier hunks before failing — partial state | ⚠ caveat | Sync engine documents rollback semantics in CODEX_REVIEW + setup wraps each `apply_patch` invocation in pre-commit check |
| 12 | **Same envelope touches protected + unprotected files** | N/A — Claude's `Edit` is per-file, prewrite gate intercepts each | `apply_patch` envelope can mix protected (PLAN.md, CHECKS.yaml) + unprotected files. `prewrite_gate.py` must scan ALL files in envelope, not just the first | ⚠ caveat | AC-007 gate-crash logging extension: prewrite gate parses the full envelope, denies if ANY target is protected. Update gate logic. |
| 13 | **Generated bulk edits that should bypass `apply_patch`** | `Write` with large content, no diff context | Codex prefers `apply_patch` for atomicity, but very large diffs are slow / hit context limits | ✗ no-port at envelope level | Sync engine detects diff size > N lines → emit `Write` semantics in skill prose (`shell` tool with `cat > file <<EOF` heredoc) instead of `apply_patch` |

---

## Implementation notes for AC-005 sync engine

When transforming Claude SKILL.md prose for Codex:

1. **`Read` / `Edit` / `MultiEdit` / `Write` references in code blocks** → rewrite to `read_file` / `apply_patch` / `apply_patch` / `apply_patch` with envelope wrap.
2. **Group consecutive `Edit` / `MultiEdit` / `Write` calls** within one skill phase into one `apply_patch` envelope where possible (pattern 2, 3, 4).
3. **`Bash rm <path>`** in skill prose → optional rewrite to `apply_patch` envelope with `*** Delete File: <path>` (pattern 5). Only do this if the surrounding prose is about modifying the source tree (not git operations or temp files).
4. **`Bash mv <old> <new>`** → optional rewrite to `*** Move to:` form (pattern 6). Same condition as above.
5. **Skill prose paragraphs that explain Claude's tool semantics** ("Edit only modifies the matched section atomically") need separate Codex-side notes ("apply_patch envelope can leave partial state on failure; pair with pre-commit check"). These are addendum lines, not substitutions.
6. **Hunk emission**: 3+ lines context, normalized final newline, quoted paths containing whitespace.

---

## Test plan (AC-002 → AC-003 / AC-005)

Empirical validation against running `codex exec` deferred to AC-003 spike (when the 3 ported skills exercise these patterns) and AC-005 (when the sync engine emits `apply_patch` envelopes and we round-trip them against `codex exec --json` to confirm behavior).

Golden corpus seeds:
- `tests/runtime-sync/corpus/patch-pattern-01-single-hunk/input.md` + `expected-codex.md`
- `tests/runtime-sync/corpus/patch-pattern-02-multi-hunk/...`
- ... (one directory per pattern row above)

Property test: `transform(claude_form) == codex_form` for patterns 1-4 (direct/wrap). Failing test for patterns 5-13 documents the divergence rather than asserting parity (`@pytest.mark.xfail` with reason citing this matrix).

---

## Why 13 patterns, not 3

PLAN.md originally scoped AC-002 to "3-pattern matrix: single-hunk same-file, multi-hunk same-file, multi-file." That covered patterns 1-3 in the table above. CODEX_REVIEW finding 4 enumerated 10 additional patterns (4-13) that the original scope missed:

- Patterns 4-6 (Add/Delete/Move) — `apply_patch` envelope handles these in ONE call; Claude needs separate tools (`Write` for add, `Bash` for delete/move). The "operation-oriented vs envelope-oriented" gap surfaces here.
- Patterns 7-10 (newline, whitespace path, repeated hunks, ambiguous context) — `apply_patch` parser quirks. Real risk in production sync.
- Patterns 11-12 (failed hunk rollback, mixed-protection envelope) — semantic differences that affect the gate scripts (AC-007). Without these documented, the gate has blind spots under Codex.
- Pattern 13 (bulk edits bypass) — performance / context-limit consideration. Affects skill prose choice (`shell` vs `apply_patch`).

The expanded matrix is binding for AC-005 sync engine design.

---

## Sources

- `/project/harness-e14968053086/doc/harness/tasks/TASK__dual-runtime-plugin-claude-codex/CODEX_REVIEW.md` finding 4 — origin of the 10 additional patterns
- `/tmp/openai-codex-src/codex-rs/hooks/src/engine/mod.rs` — Codex apply_patch handler reference (envelope structure)
- OpenAI Codex docs — apply_patch reference: https://developers.openai.com/api/docs/guides/tools-apply-patch (verified URL pattern via CODEX_REVIEW, not fetched)
- Claude Code's `Edit`/`Write`/`MultiEdit` tool definitions — Anthropic docs (model-knowledge)
