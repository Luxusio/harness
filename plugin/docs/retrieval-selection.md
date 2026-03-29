# Retrieval Selection Reference

updated: 2026-03-29

This document describes the full note retrieval pipeline used by `plugin/scripts/memory_selectors.py` and `plugin/scripts/prompt_memory.py`.

---

## Query Tokenization

The `extract_keywords(prompt)` function converts a raw prompt string into a set of retrieval keywords. It handles four types of input:

### 1. File paths

Sequences containing `/` are extracted as both whole paths and individual segments:

```
"api/users.py의 로그인 검증"
→ path token: "api/users.py"
→ segments:   "api", "users", "py"
```

### 2. ASCII identifier decomposition

Plain words, snake\_case, kebab-case, and camelCase identifiers are decomposed into constituents:

```
"getUserProfile"  → "getuserprofile", "get", "user", "profile"
"rate_limiter"    → "rate_limiter", "rate", "limiter"
"auth-middleware" → "auth-middleware", "auth", "middleware"
```

### 3. Unicode / CJK characters

Non-ASCII sequences are extracted as-is. For multi-character sequences, individual characters are also added (useful for Korean/CJK where single characters carry meaning):

```
"로그인 검증" → "로그인", "검증", "로", "그", "인", "검", "증"
```

### 4. N-gram fallback for short prompts

When fewer than 3 keywords are extracted from the above steps, 2-grams and 3-grams are added from filtered tokens to improve coverage for very short prompts:

```
"auth flow"  → ["auth", "flow", "auth flow"]
```

### Stopword filtering

English stopwords (a, the, is, are, have, etc.) and words ≤ 1 character are removed. Stopword filtering applies only to ASCII tokens; Unicode tokens are never filtered.

---

## Multi-Root Candidate Pool Construction

The retrieval system builds a candidate pool from all registered doc roots.

### Root discovery

1. Read `registered_roots` from `.claude/harness/manifest.yaml`
2. If not present, scan all `doc/*` subdirectories
3. Always include `common` (inserted at position 0 if absent)

```yaml
# manifest.yaml example
registered_roots:
  - common
  - frontend
  - api
```

### Candidate collection

For each root `R` in the registered list:
1. List all `*.md` files in `doc/R/`
2. Read up to 1500 characters of each file
3. Parse frontmatter for core fields (freshness, status)
4. Parse optional metadata fields (root, lane, path_scope, topic_tags)
5. Skip notes with `status: superseded`

---

## 5-Signal Scoring Formula

Each candidate note is scored against the query using a linear combination of five signals:

```python
score = (lexical      * 0.40
       + freshness    * 0.25
       + root_match   * 0.15
       + path_overlap * 0.10
       + lane_match   * 0.10)
```

### Signal definitions

| Signal | Range | Formula |
|--------|-------|---------|
| `lexical` | 0.0–1.0 | `matched_keywords / total_keywords` |
| `freshness` | 0.0–1.0 | freshness weight (see table below) |
| `root_match` | 0.5 or 1.0 | 1.0 if note root ∈ active_roots, else 0.5 |
| `path_overlap` | 0.0–1.0 | `|keywords ∩ path_scope_tokens| / |keywords|` |
| `lane_match` | 0.7 or 1.0 | 1.0 if note lane == detected lane, 0.7 otherwise |

### Freshness weights

| State | Weight |
|-------|--------|
| `current` | 1.0 |
| `suspect` | 0.5 |
| `stale` | 0.1 |
| `superseded` | 0.0 (excluded before scoring) |

### Scoring example

Query: "fix login validation in api/users.py"
Keywords extracted: ["fix", "login", "validation", "api", "users", "py", "api/users.py"]

Note A (`doc/api/obs-login-validation.md`):
- lexical: 5/7 = 0.71 (matches "login", "validation", "api", "users", "py")
- freshness: 1.0 (current)
- root_match: 1.0 (root=api, active_roots=[common, api])
- path_overlap: 0.57 (path_scope=[src/api/users.py] → tokens overlap with "api", "users", "py")
- lane_match: 1.0 (lane=debug, detected lane=debug from "fix")
- **total: 0.71×0.4 + 1.0×0.25 + 1.0×0.15 + 0.57×0.1 + 1.0×0.1 = 0.837**

Note B (`doc/common/obs-auth-flow.md`):
- lexical: 2/7 = 0.29 (matches "login", "validation")
- freshness: 0.5 (suspect)
- root_match: 1.0 (root=common, always in active_roots)
- path_overlap: 0.0 (no path_scope set)
- lane_match: 0.7 (no lane set)
- **total: 0.29×0.4 + 0.5×0.25 + 1.0×0.15 + 0.0×0.1 + 0.7×0.1 = 0.432**

Note A ranks higher despite being in a non-common root.

---

## Freshness Integration

Freshness is the second-highest weight (0.25) in the scoring formula. It acts as a multiplier on the entire note's retrieval value:

- A perfect-match but `stale` note scores at most `1.0×0.4 + 0.1×0.25 + ... ≈ 0.45` before other signals
- A moderate-match `current` note with score 0.5 on lexical scores `0.5×0.4 + 1.0×0.25 + ... ≈ 0.45` minimum

Notes with `status: superseded` are excluded from the candidate pool entirely before scoring (not just penalized).

---

## Context Budget

After scoring, candidates are filtered and selected:

1. **Minimum threshold**: notes with `score ≤ 0.1` are dropped
2. **Top-N selection**: at most 2 notes are selected (highest scores first)
3. **Character budget**: total injected context (all slots combined) is capped at 600 characters

The 600-character budget is shared across all context slots:

| Slot | Max count | Source |
|------|-----------|--------|
| Notes | 2 | Multi-root scoring |
| Active task | 1 | Task state relevance |
| Recent verdict | 1 | Most recent critic file |
| Blocker | 1 (if any) | `status: blocked_env` tasks |
| Tooling hints | As-is | Manifest flags |

If the combined context exceeds 600 characters, it is truncated with `...`.

### Root labeling in output

Notes from non-common roots are prefixed in the injected context:

```
Note: [api] Login validation returns 400 on empty username
Note: Authentication requires session cookie (not Authorization header)
```

The second note (common root) has no prefix.

---

## Backward Compatibility Guarantees

1. **Single-root repos** (`doc/common/` only): behavior is identical to pre-v4.1. No manifest changes required.

2. **Notes without new metadata fields**: scored using defaults — `root=common`, `lane=None`, `path_scope=[]`. The `lane_match` and `path_overlap` signals contribute neutral values (0.7 and 0.0 respectively).

3. **Manifest without `registered_roots`**: system falls back to scanning all `doc/*` subdirectories. Existing repos do not need to add this field.

4. **Context budget unchanged**: still 600 characters, top 2 notes.

5. **Threshold unchanged**: 0.1 minimum score for inclusion.

6. **`notes_dir` parameter**: `select_relevant_notes()` still accepts an optional `notes_dir` parameter for single-directory calls (legacy compatibility).
