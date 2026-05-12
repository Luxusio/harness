#!/usr/bin/env bash
# Install or update the harness Claude Code plugin from this checkout.
# Idempotent: same invocation works for first install and subsequent updates.
#
# Source:  the directory above this script (the repo root).
# Dest:    $HARNESS_DEST (default $HOME/.claude/harness-dev).

set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_DIR="${HARNESS_DEST:-$HOME/.claude/harness-dev}"

if ! command -v claude >/dev/null 2>&1; then
  echo "error: 'claude' CLI not on PATH. Install Claude Code first." >&2
  exit 1
fi

# 1. Sync source into the marketplace path.
#    rm -rf  -> propagate upstream deletions (orphan files don't linger).
#    tar pipe -> batched syscalls, much faster than cp -r on a tree with many small files.
#    --exclude='./.git' -> the destination is a runtime mirror, not a working repo.
rm -rf "$DEST_DIR"
mkdir -p "$DEST_DIR"
(cd "$SRC_DIR" && tar -cf - --exclude='./.git' .) | (cd "$DEST_DIR" && tar -xf -)
echo "synced $SRC_DIR -> $DEST_DIR (.git excluded)"

# 2. Register marketplace + install plugin (first install) or refresh (update).
if claude plugin marketplace list 2>/dev/null | grep -qE "(^|[^a-zA-Z])harness$"; then
  echo "harness marketplace already registered -- refreshing from $DEST_DIR..."
  claude plugin marketplace update harness
else
  echo "first install -- registering marketplace and installing plugin..."
  claude plugin marketplace add "$DEST_DIR"
  claude plugin install harness@harness
fi

echo ""
echo "harness installed/updated from $SRC_DIR"
