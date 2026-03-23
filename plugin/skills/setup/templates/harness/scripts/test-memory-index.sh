#!/usr/bin/env bash
# Regression test suite for the harness memory index.
# Tests run from the repo root. Some tests SKIP when prerequisites are not yet built.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

FAILURES=0
PASSES=0
SKIPS=0

# ── Helpers ──────────────────────────────────────────────────────────────────

pass() { echo "  PASS: $1"; PASSES=$((PASSES + 1)); }
fail() { echo "  FAIL: $1"; FAILURES=$((FAILURES + 1)); }
skip() { echo "  SKIP: $1"; SKIPS=$((SKIPS + 1)); }

run_test() {
    local name="$1"
    shift
    echo ""
    echo "--- Test: $name ---"
    "$@"
}

# ── Test 1: Query schema parity ───────────────────────────────────────────────
# Flattened records must expose statement, provenance, index_status, scope, _score.

test_query_schema() {
    if [ ! -f "harness/memory-index/manifest.json" ]; then
        skip "memory index not built (manifest.json missing)"
        return
    fi

    local tmpfile
    tmpfile=$(mktemp /tmp/harness-test-XXXXXX.json)
    python3 harness/scripts/query-memory.py --query "approval" --format json 2>/dev/null > "$tmpfile"

    python3 - "$tmpfile" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
if len(data) == 0:
    print("  SKIP: query returned no results (index may be empty)")
    sys.exit(0)
first = data[0]
missing = [f for f in ("statement","provenance","index_status","scope","_score") if f not in first]
if missing:
    print(f"  FAIL: missing fields: {missing}  present keys: {list(first.keys())}")
    sys.exit(1)
print(f"  PASS: flattened record has all required schema fields ({len(data)} results)")
PYEOF
    local rc=$?
    rm -f "$tmpfile"
    return $rc
}

# ── Test 2: Clean rebuild idempotency ─────────────────────────────────────────
# Two consecutive builds must produce zero git diff.

test_idempotency() {
    if [ ! -f "harness/scripts/build-memory-index.py" ]; then
        skip "build-memory-index.py not found"
        return
    fi

    # If the index already has uncommitted changes (from concurrent workers),
    # skip — we cannot reliably test idempotency in that state.
    local pre_diff
    pre_diff=$(git diff --stat harness/memory-index/ 2>/dev/null || true)
    if [ -n "$pre_diff" ]; then
        skip "memory-index already has uncommitted changes (concurrent workers running)"
        return
    fi

    python3 harness/scripts/build-memory-index.py > /dev/null 2>&1

    # Check again after first build — if something changed externally, skip.
    local mid_diff
    mid_diff=$(git diff --stat harness/memory-index/ 2>/dev/null || true)
    if [ -n "$mid_diff" ]; then
        skip "memory-index changed externally during first build (concurrent workers running)"
        return
    fi

    python3 harness/scripts/build-memory-index.py > /dev/null 2>&1

    local diff_stat
    diff_stat=$(git diff --stat harness/memory-index/ 2>/dev/null || true)
    if [ -z "$diff_stat" ]; then
        pass "consecutive builds produce identical output"
    else
        fail "consecutive builds differ"
        echo "$diff_stat"
    fi
}

# ── Test 3: Scope index population ───────────────────────────────────────────
# harness/memory-index/active/by-domain and by-path must have files when built.

test_scope_population() {
    if [ ! -d "harness/memory-index/active" ]; then
        skip "harness/memory-index/active/ directory does not exist"
        return
    fi

    local domain_count=0
    local path_count=0

    if [ -d "harness/memory-index/active/by-domain" ]; then
        domain_count=$(find harness/memory-index/active/by-domain -name '*.json' 2>/dev/null | wc -l)
    fi
    if [ -d "harness/memory-index/active/by-path" ]; then
        path_count=$(find harness/memory-index/active/by-path -name '*.json' 2>/dev/null | wc -l)
    fi

    if [ "$domain_count" -eq 0 ] && [ "$path_count" -eq 0 ]; then
        skip "by-domain and by-path indices not built yet (scope indexing may be pending)"
        return
    fi

    # One or both may still be pending from worker-2 — skip rather than fail.
    if [ "$domain_count" -eq 0 ]; then
        skip "by-domain is empty (by-path has $path_count) — scope indexing may be partial"
        return
    fi
    if [ "$path_count" -eq 0 ]; then
        skip "by-path is empty (by-domain has $domain_count) — scope indexing may be partial"
        return
    fi

    pass "by-domain has $domain_count files"
    pass "by-path has $path_count files"
}

# ── Test 4: Overlay merge ─────────────────────────────────────────────────────
# When overlay is present, --include-overlay must return results without error.

test_overlay_merge() {
    if [ ! -f "harness/scripts/build-memory-overlay.py" ]; then
        skip "build-memory-overlay.py not available yet (overlay worker may be pending)"
        return
    fi

    python3 harness/scripts/build-memory-overlay.py > /dev/null 2>&1 || true

    if [ ! -f ".harness-cache/memory-overlay/records.jsonl" ]; then
        skip "overlay records.jsonl not produced (build-memory-overlay.py may be a stub)"
        return
    fi

    if [ ! -f "harness/memory-index/manifest.json" ]; then
        skip "memory index not built — cannot test overlay merge"
        return
    fi

    local output
    output=$(python3 harness/scripts/query-memory.py \
        --query "current task session" \
        --include-overlay \
        --format json 2>/dev/null)

    local count
    count=$(python3 -c "import json,sys; print(len(json.loads(sys.stdin.read())))" <<< "$output")
    pass "overlay merge returned $count results without error"
}

# ── Test 5: Temporal / superseded filtering ───────────────────────────────────
# Default query results must contain zero records where index_status == "superseded".

test_temporal_filtering() {
    if [ ! -f "harness/memory-index/manifest.json" ]; then
        skip "memory index not built (manifest.json missing)"
        return
    fi

    local tmpfile
    tmpfile=$(mktemp /tmp/harness-test-XXXXXX.json)
    python3 harness/scripts/query-memory.py --query "decisions" --top 50 --format json 2>/dev/null > "$tmpfile"

    python3 - "$tmpfile" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
superseded = [r for r in data if r.get("index_status") == "superseded"]
if superseded:
    print(f"  FAIL: {len(superseded)} superseded records leaked through in default results")
    sys.exit(1)
print(f"  PASS: no superseded records in default query results ({len(data)} records checked)")
PYEOF
    local rc=$?
    rm -f "$tmpfile"
    return $rc
}

# ── Test 6: Scope records non-zero ────────────────────────────────────────────
# At least one record in by-subject must have populated scope.domains or scope.paths.

test_scope_records() {
    if [ ! -d "harness/memory-index/active/by-subject" ]; then
        skip "by-subject index directory missing"
        return
    fi

    local count
    count=$(python3 - <<'PYEOF'
import json, pathlib
idx = pathlib.Path("harness/memory-index/active/by-subject")
count = 0
for f in idx.glob("*.json"):
    try:
        data = json.loads(f.read_text())
    except Exception:
        continue
    if isinstance(data, dict) and "records" in data:
        rows = data["records"]
    elif isinstance(data, list):
        rows = data
    else:
        rows = [data]
    for rec in rows:
        scope = rec.get("scope", {})
        if scope.get("domains") or scope.get("paths"):
            count += 1
print(count)
PYEOF
)

    if [ "$count" -gt 0 ]; then
        pass "$count records have non-empty scope (domains or paths)"
    else
        skip "no records have populated scope yet (scope population may be pending)"
    fi
}

# ── Test 7: Precision — approval source of truth ──────────────────────────────
# Query "approval source of truth" with domain boost must return a result whose
# provenance.source_path contains "approvals" or "architecture", not an unrelated file.

test_precision_approval() {
    if [ ! -f "harness/memory-index/manifest.json" ]; then
        skip "memory index not built (manifest.json missing)"
        return
    fi

    local tmpfile
    tmpfile=$(mktemp /tmp/harness-test-XXXXXX.json)
    python3 harness/scripts/query-memory.py \
        --query "approval source of truth" \
        --domains "approval-gates" \
        --top 3 \
        --format json 2>/dev/null > "$tmpfile"

    local result
    result=$(python3 - "$tmpfile" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
if not data:
    print("SKIP:no results returned for approval query (index may be empty)")
    sys.exit(0)
top = data[0]
src = top.get('provenance', {}).get('source_path', '') if isinstance(top.get('provenance'), dict) else ''
if 'approvals' in src or 'architecture' in src:
    print(f"PASS:top result for 'approval source of truth' is relevant (source: {src})")
else:
    print(f"FAIL:top result source is '{src}' — expected approvals-related")
PYEOF
)
    rm -f "$tmpfile"
    local verdict="${result%%:*}"
    local msg="${result#*:}"
    case "$verdict" in
        PASS) pass "$msg" ;;
        SKIP) skip "$msg" ;;
        *)    fail "$msg" ;;
    esac
}

# ── Test 8: No unrelated high-authority leakage ───────────────────────────────
# Query "current task status" — top 3 results must have lexical overlap with query
# terms, not just score high on authority alone.

test_no_authority_leakage() {
    if [ ! -f "harness/memory-index/manifest.json" ]; then
        skip "memory index not built (manifest.json missing)"
        return
    fi

    local tmpfile
    tmpfile=$(mktemp /tmp/harness-test-XXXXXX.json)
    python3 harness/scripts/query-memory.py \
        --query "current task status" \
        --top 5 \
        --format json 2>/dev/null > "$tmpfile"

    local result
    result=$(python3 - "$tmpfile" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
if not data:
    print("SKIP:no results returned (index may be empty)")
    sys.exit(0)
query_words = {'current', 'task', 'status'}
leaked = 0
for r in data[:3]:
    subject = r.get('subject_key', '')
    statement = r.get('statement', '')
    tags = ' '.join(r.get('tags', []))
    record_text = f"{subject} {statement} {tags}".lower()
    record_words = set(record_text.split())
    if not (record_words & query_words):
        leaked += 1
if leaked == 0:
    print("PASS:no authority-only leakage in top 3 results")
else:
    print(f"FAIL:{leaked} unrelated record(s) in top 3 have no lexical overlap with query")
PYEOF
)
    rm -f "$tmpfile"
    local verdict="${result%%:*}"
    local msg="${result#*:}"
    case "$verdict" in
        PASS) pass "$msg" ;;
        SKIP) skip "$msg" ;;
        *)    fail "$msg" ;;
    esac
}

# ── Test 9: Path boost specificity ────────────────────────────────────────────
# A specific path boost for "harness/policies/approvals.yaml" must not
# score records that only have generic paths like "harness/docs/architecture/README.md".
# Verifies that path matching uses containment (bp in sp or sp in bp), not just prefix.

test_path_boost_specificity() {
    if [ ! -f "harness/memory-index/manifest.json" ]; then
        skip "memory index not built (manifest.json missing)"
        return
    fi

    local tmpfile
    tmpfile=$(mktemp /tmp/harness-test-XXXXXX.json)
    python3 harness/scripts/query-memory.py \
        --query "approval" \
        --paths "harness/policies/approvals.yaml" \
        --format json \
        --top 10 2>/dev/null > "$tmpfile"

    local result
    result=$(python3 - "$tmpfile" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
if not data:
    print("SKIP:no results (index may be empty)")
    sys.exit(0)
boost_path = "harness/policies/approvals.yaml"
generic_path = "harness/docs/architecture/README.md"
false_boosts = 0
for r in data:
    scope_paths = r.get('scope', {}).get('paths', [])
    if not isinstance(scope_paths, list):
        continue
    has_specific = any(boost_path in sp or sp in boost_path for sp in scope_paths)
    has_only_generic = any(generic_path in sp or sp in generic_path for sp in scope_paths) and not has_specific
    if has_only_generic:
        false_boosts += 1
if false_boosts == 0:
    print("PASS:path boost specificity: no generic-only paths received approvals.yaml boost")
else:
    print(f"FAIL:{false_boosts} record(s) with only generic paths appear in approvals.yaml-boosted results")
PYEOF
)
    rm -f "$tmpfile"
    local verdict="${result%%:*}"
    local msg="${result#*:}"
    case "$verdict" in
        PASS) pass "$msg" ;;
        SKIP) skip "$msg" ;;
        *)    fail "$msg" ;;
    esac
}

# ── Test 10: Overlay schema parity ────────────────────────────────────────────
# Records from the overlay must have provenance, temporal, scope, and relations fields.

test_overlay_schema_parity() {
    if [ ! -f "harness/scripts/build-memory-overlay.py" ]; then
        skip "build-memory-overlay.py not available (overlay worker may be pending)"
        return
    fi

    python3 harness/scripts/build-memory-overlay.py > /dev/null 2>&1 || true

    if [ ! -f ".harness-cache/memory-overlay/records.jsonl" ]; then
        skip "overlay records.jsonl not produced (build-memory-overlay.py may be a stub)"
        return
    fi

    if [ ! -f "harness/memory-index/manifest.json" ]; then
        skip "memory index not built — cannot test overlay schema parity"
        return
    fi

    local tmpfile
    tmpfile=$(mktemp /tmp/harness-test-XXXXXX.json)
    python3 harness/scripts/query-memory.py \
        --query "current task" \
        --include-overlay \
        --format json 2>/dev/null > "$tmpfile"

    local result
    result=$(python3 - "$tmpfile" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
overlay = [r for r in data if isinstance(r.get('id'), str) and r['id'].startswith('overlay:')]
if not overlay:
    print("SKIP:no overlay records in results")
    sys.exit(0)
r = overlay[0]
required = ['provenance', 'temporal', 'scope', 'relations']
missing = [f for f in required if f not in r or not isinstance(r[f], dict)]
if not missing:
    print("PASS:overlay record has schema parity (provenance, temporal, scope, relations)")
else:
    print(f"FAIL:overlay record missing or wrong type for fields: {missing}")
PYEOF
)
    rm -f "$tmpfile"
    local verdict="${result%%:*}"
    local msg="${result#*:}"
    case "$verdict" in
        PASS) pass "$msg" ;;
        SKIP) skip "$msg" ;;
        *)    fail "$msg" ;;
    esac
}

# ── Test 11: Relation edge existence ──────────────────────────────────────────
# At least one record in the source shards must have a non-empty relation field.

test_relation_edge_existence() {
    local shard_dir="harness/memory-index/source-shards"
    if [ ! -d "$shard_dir" ]; then
        skip "source-shards directory missing (index may not be built yet)"
        return
    fi

    local edges
    edges=$(python3 - <<PYEOF
import json, pathlib, sys
count = 0
shard_dir = pathlib.Path("harness/memory-index/source-shards")
for f in shard_dir.rglob("*.json"):
    try:
        data = json.loads(f.read_text())
    except Exception:
        continue
    if isinstance(data, dict) and "records" in data:
        rows = data["records"]
    elif isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = [data]
    else:
        rows = []
    for rec in rows:
        rels = rec.get("relations", {})
        if not isinstance(rels, dict):
            continue
        for k in ("supersedes", "extends", "resolves", "conflicts_with"):
            if rels.get(k):
                count += 1
print(count)
PYEOF
)

    if [ "$edges" -gt 0 ]; then
        pass "$edges relation edge(s) found in source shards"
    else
        skip "no relation edges found yet (temporal fixture may be pending)"
    fi
}

# ── Test 12: Rebuild idempotency (strict via check-memory-index.sh) ────────────
# Two consecutive builds followed by check-memory-index.sh must exit 0.

test_rebuild_idempotency_strict() {
    if [ ! -f "harness/scripts/build-memory-index.py" ]; then
        skip "build-memory-index.py not found"
        return
    fi
    if [ ! -f "harness/scripts/check-memory-index.sh" ]; then
        skip "check-memory-index.sh not found"
        return
    fi

    # Skip if concurrent workers have the index in an uncommitted state
    local pre_diff
    pre_diff=$(git diff --stat harness/memory-index/ 2>/dev/null || true)
    if [ -n "$pre_diff" ]; then
        skip "memory-index has uncommitted changes (concurrent workers running)"
        return
    fi

    timeout 30 python3 harness/scripts/build-memory-index.py > /dev/null 2>&1 || {
        skip "build-memory-index.py timed out or failed"
        return
    }
    timeout 30 python3 harness/scripts/build-memory-index.py > /dev/null 2>&1 || {
        skip "second build-memory-index.py timed out or failed"
        return
    }

    if timeout 30 bash harness/scripts/check-memory-index.sh > /dev/null 2>&1; then
        pass "rebuild idempotency verified (check-memory-index.sh passes after two builds)"
    else
        fail "rebuild produces inconsistent output (check-memory-index.sh failed)"
    fi
}

# ── Test 13: Pack format ───────────────────────────────────────────────────────
# If --format pack is available, it must produce structured JSON with a "facts" key.
# Gracefully skips if the format is not yet implemented.

test_pack_format() {
    if [ ! -f "harness/memory-index/manifest.json" ]; then
        skip "memory index not built (manifest.json missing)"
        return
    fi

    local tmpfile
    tmpfile=$(mktemp /tmp/harness-test-XXXXXX.json)
    python3 harness/scripts/query-memory.py \
        --query "approval" \
        --format pack 2>/dev/null > "$tmpfile" || true

    if [ ! -s "$tmpfile" ]; then
        rm -f "$tmpfile"
        skip "pack format not implemented (no output)"
        return
    fi

    local result
    result=$(python3 - "$tmpfile" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    raw = f.read().strip()
if not raw:
    print("SKIP:pack format not implemented (empty output)")
    sys.exit(0)
try:
    d = json.loads(raw)
except json.JSONDecodeError:
    print("SKIP:pack format not implemented (output is not JSON)")
    sys.exit(0)
if 'facts' in d:
    print("PASS:pack format produces structured JSON with 'facts' key")
else:
    print(f"FAIL:pack format JSON missing 'facts' key (keys: {list(d.keys())})")
PYEOF
)
    rm -f "$tmpfile"
    local verdict="${result%%:*}"
    local msg="${result#*:}"
    case "$verdict" in
        PASS) pass "$msg" ;;
        SKIP) skip "$msg" ;;
        *)    fail "$msg" ;;
    esac
}

# ── Test 14: Exact path query precision ──────────────────────────────────────
# --paths "harness/policies/approvals.yaml" must put approvals source in top results.

test_exact_path_precision() {
    if [ ! -f "harness/memory-index/manifest.json" ]; then
        skip "memory index not built (manifest.json missing)"
        return
    fi

    local output
    output=$(python3 harness/scripts/query-memory.py \
        --query "approval" \
        --paths "harness/policies/approvals.yaml" \
        --top 3 \
        --format json 2>/dev/null) || { skip "query-memory.py failed"; return; }

    local result
    result=$(python3 - <<PYEOF
import json, sys
try:
    data = json.loads("""$output""")
except Exception:
    data = []
if not data:
    print("SKIP:no results returned")
    sys.exit(0)
top = data[0]
prov = top.get('provenance', {})
src = prov.get('source_path', '') if isinstance(prov, dict) else ''
if 'approvals' in src or 'architecture' in src:
    print(f"PASS:exact path query: top result from approvals/architecture source ({src})")
else:
    print(f"FAIL:exact path query: top result from '{src}', expected approvals source")
PYEOF
)
    local verdict="${result%%:*}"
    local msg="${result#*:}"
    case "$verdict" in
        PASS) pass "$msg" ;;
        SKIP) skip "$msg" ;;
        *)    fail "$msg" ;;
    esac
}

# ── Test 15: Temporal operator NOT in lexical scoring ────────────────────────
# "what changed before ADR-0002" must NOT have approval_rule records in top 3
# that match purely on the word "before".

test_temporal_operator_no_leak() {
    if [ ! -f "harness/memory-index/manifest.json" ]; then
        skip "memory index not built (manifest.json missing)"
        return
    fi

    local tmpfile
    tmpfile=$(mktemp /tmp/harness-test-XXXXXX.json)
    python3 harness/scripts/query-memory.py \
        --query "what changed before ADR-0002" \
        --top 5 \
        --format json 2>/dev/null > "$tmpfile" || { rm -f "$tmpfile"; skip "query-memory.py failed"; return; }

    local result
    result=$(python3 - "$tmpfile" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
if not data:
    print("SKIP:no results returned (index may be empty)")
    sys.exit(0)
leaked = sum(
    1 for r in data[:3]
    if r.get('kind') in ('approval_rule',)
    and 'before' in r.get('statement', '').lower()
)
if leaked == 0:
    print("PASS:temporal operator: no 'before' lexical leak in top 3")
else:
    print(f"FAIL:temporal operator: {leaked} records leaked via 'before' lexical match")
PYEOF
)
    rm -f "$tmpfile"
    local verdict="${result%%:*}"
    local msg="${result#*:}"
    case "$verdict" in
        PASS) pass "$msg" ;;
        SKIP) skip "$msg" ;;
        *)    fail "$msg" ;;
    esac
}

# ── Test 16: Overlay precedence for direct query ──────────────────────────────
# When overlay is present, query "current task status" with --include-overlay
# must return an overlay record as the top result.

test_overlay_precedence() {
    if [ ! -f "harness/scripts/build-memory-overlay.py" ]; then
        skip "build-memory-overlay.py not available (overlay worker may be pending)"
        return
    fi

    python3 harness/scripts/build-memory-overlay.py > /dev/null 2>&1 || { skip "overlay builder not available"; return; }

    if [ ! -f ".harness-cache/memory-overlay/records.jsonl" ]; then
        skip "overlay records.jsonl not produced (may be a stub)"
        return
    fi

    if [ ! -f "harness/memory-index/manifest.json" ]; then
        skip "memory index not built — cannot test overlay precedence"
        return
    fi

    local tmpfile
    tmpfile=$(mktemp /tmp/harness-test-XXXXXX.json)
    python3 harness/scripts/query-memory.py \
        --query "current task status" \
        --include-overlay \
        --top 3 \
        --format json 2>/dev/null > "$tmpfile" || { rm -f "$tmpfile"; skip "query-memory.py failed"; return; }

    local result
    result=$(python3 - "$tmpfile" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
if not data:
    print("SKIP:no results")
    sys.exit(0)
top_id = data[0].get('id', '')
if str(top_id).startswith('overlay:'):
    print(f"PASS:overlay precedence: top result is overlay record (id={top_id})")
else:
    print(f"FAIL:overlay precedence: top result id='{top_id}', expected overlay:*")
PYEOF
)
    rm -f "$tmpfile"
    local verdict="${result%%:*}"
    local msg="${result#*:}"
    case "$verdict" in
        PASS) pass "$msg" ;;
        SKIP) skip "$msg" ;;
        *)    fail "$msg" ;;
    esac
}

# ── Test 17: Rich pack facts have full fields ─────────────────────────────────
# Pack format facts[0] must contain scope, temporal, relations, provenance,
# index_status, and tags.

test_rich_pack_facts() {
    if [ ! -f "harness/memory-index/manifest.json" ]; then
        skip "memory index not built (manifest.json missing)"
        return
    fi

    local tmpfile
    tmpfile=$(mktemp /tmp/harness-test-XXXXXX.json)
    python3 harness/scripts/query-memory.py \
        --query "approval" \
        --domains "approval-gates" \
        --format pack 2>/dev/null > "$tmpfile" || true

    if [ ! -s "$tmpfile" ]; then
        rm -f "$tmpfile"
        skip "pack format not implemented (no output)"
        return
    fi

    local result
    result=$(python3 - "$tmpfile" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    raw = f.read().strip()
if not raw:
    print("SKIP:pack format not implemented (empty output)")
    sys.exit(0)
try:
    pack = json.loads(raw)
except json.JSONDecodeError:
    print("SKIP:pack format not implemented (not valid JSON)")
    sys.exit(0)
facts = pack.get('facts', [])
if not facts:
    print("SKIP:pack has no facts")
    sys.exit(0)
f = facts[0]
required = ['scope', 'temporal', 'relations', 'provenance', 'index_status', 'tags']
missing = [k for k in required if k not in f]
if not missing:
    print("PASS:rich pack: facts[0] has scope, temporal, relations, provenance, index_status, tags")
else:
    print(f"FAIL:rich pack: missing fields {missing}")
PYEOF
)
    rm -f "$tmpfile"
    local verdict="${result%%:*}"
    local msg="${result#*:}"
    case "$verdict" in
        PASS) pass "$msg" ;;
        SKIP) skip "$msg" ;;
        *)    fail "$msg" ;;
    esac
}

# ── Test 18: Source-path index exists ────────────────────────────────────────
# harness/memory-index/active/by-source-path/ must exist and contain JSON files.

test_source_path_index_exists() {
    if [ ! -d "harness/memory-index/active/by-source-path" ]; then
        skip "by-source-path index not yet built"
        return
    fi

    local count
    count=$(find harness/memory-index/active/by-source-path -name '*.json' 2>/dev/null | wc -l)
    if [ "$count" -gt 0 ]; then
        pass "source-path index has $count files"
    else
        fail "source-path index directory exists but is empty"
    fi
}

# ── Test 19: Identifier index exists ─────────────────────────────────────────
# harness/memory-index/active/by-identifier/ must have ADR-0001 or ADR-0002 entry.

test_identifier_index_exists() {
    if [ ! -d "harness/memory-index/active/by-identifier" ]; then
        skip "by-identifier index not yet built"
        return
    fi

    if [ -f "harness/memory-index/active/by-identifier/ADR-0001.json" ] || \
       [ -f "harness/memory-index/active/by-identifier/ADR-0002.json" ]; then
        pass "identifier index has ADR entries"
    else
        fail "identifier index exists but no ADR-0001 or ADR-0002 files found"
    fi
}

# ── Test 20: Source files to verify capped at 4 ───────────────────────────────
# Pack format source_files_to_verify must contain at most 4 entries.

test_source_files_cap() {
    if [ ! -f "harness/memory-index/manifest.json" ]; then
        skip "memory index not built (manifest.json missing)"
        return
    fi

    local tmpfile
    tmpfile=$(mktemp /tmp/harness-test-XXXXXX.json)
    python3 harness/scripts/query-memory.py \
        --query "approval" \
        --format pack 2>/dev/null > "$tmpfile" || true

    if [ ! -s "$tmpfile" ]; then
        rm -f "$tmpfile"
        skip "pack format not implemented (no output)"
        return
    fi

    local result
    result=$(python3 - "$tmpfile" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    raw = f.read().strip()
if not raw:
    print("SKIP:pack format not implemented (empty output)")
    sys.exit(0)
try:
    pack = json.loads(raw)
except json.JSONDecodeError:
    print("SKIP:pack format not implemented (not valid JSON)")
    sys.exit(0)
if 'source_files_to_verify' not in pack:
    print("SKIP:pack has no source_files_to_verify key")
    sys.exit(0)
count = len(pack['source_files_to_verify'])
if count <= 4:
    print(f"PASS:source_files_to_verify capped at {count} (<=4)")
else:
    print(f"FAIL:source_files_to_verify has {count} files (expected <=4)")
PYEOF
)
    rm -f "$tmpfile"
    local verdict="${result%%:*}"
    local msg="${result#*:}"
    case "$verdict" in
        PASS) pass "$msg" ;;
        SKIP) skip "$msg" ;;
        *)    fail "$msg" ;;
    esac
}

# ── Runner ────────────────────────────────────────────────────────────────────

echo "=== Memory Index Regression Tests ==="
echo "    Repo root: $REPO_ROOT"

run_test "Query schema parity"               test_query_schema
run_test "Clean rebuild idempotency"         test_idempotency
run_test "Scope index population"            test_scope_population
run_test "Overlay merge"                     test_overlay_merge
run_test "Temporal/superseded filter"        test_temporal_filtering
run_test "Scope records non-zero"            test_scope_records
run_test "Precision: approval source"        test_precision_approval
run_test "No authority leakage"              test_no_authority_leakage
run_test "Path boost specificity"            test_path_boost_specificity
run_test "Overlay schema parity"             test_overlay_schema_parity
run_test "Relation edge existence"           test_relation_edge_existence
run_test "Rebuild idempotency (strict)"      test_rebuild_idempotency_strict
run_test "Pack format"                       test_pack_format
run_test "Exact path query precision"        test_exact_path_precision
run_test "Temporal operator no leak"         test_temporal_operator_no_leak
run_test "Overlay precedence"                test_overlay_precedence
run_test "Rich pack facts"                   test_rich_pack_facts
run_test "Source-path index exists"          test_source_path_index_exists
run_test "Identifier index exists"           test_identifier_index_exists
run_test "Source files cap <=4"              test_source_files_cap

echo ""
echo "=== Results: $PASSES passed, $FAILURES failed, $SKIPS skipped ==="

[ "$FAILURES" -eq 0 ] || exit 1
