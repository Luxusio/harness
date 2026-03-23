#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
EXPECTED_DIR="$REPO_ROOT/harness/memory-index"
TMPDIR_BASE="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_BASE"' EXIT

python3 "$SCRIPT_DIR/build-memory-index.py" --output-dir "$TMPDIR_BASE"

if diff -rq --exclude="README.md" "$TMPDIR_BASE" "$EXPECTED_DIR" > /dev/null 2>&1; then
  echo "OK: memory index is up to date"
  exit 0
else
  echo "STALE: memory index is out of date"
  diff -rq --exclude="README.md" "$TMPDIR_BASE" "$EXPECTED_DIR" || true
  exit 1
fi
