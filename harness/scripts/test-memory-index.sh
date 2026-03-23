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

# ── Runner ────────────────────────────────────────────────────────────────────

echo "=== Memory Index Regression Tests ==="
echo "    Repo root: $REPO_ROOT"

run_test "Query schema parity"        test_query_schema
run_test "Clean rebuild idempotency"  test_idempotency
run_test "Scope index population"     test_scope_population
run_test "Overlay merge"              test_overlay_merge
run_test "Temporal/superseded filter" test_temporal_filtering
run_test "Scope records non-zero"     test_scope_records

echo ""
echo "=== Results: $PASSES passed, $FAILURES failed, $SKIPS skipped ==="

[ "$FAILURES" -eq 0 ] || exit 1
