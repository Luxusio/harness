#!/usr/bin/env bash
set -euo pipefail

# After context compaction, update maintenance queue.
# If compaction changes root indexes or archive state, note it for critic-document follow-up.
QUEUE=".claude/harness/maintenance/QUEUE.md"

if [[ ! -f "$QUEUE" ]]; then
  exit 0
fi

DATE=$(date +%Y-%m-%d)
echo "- [${DATE}] PostCompact: context was compacted. Review open tasks and INF notes for staleness." >> "$QUEUE"

# Check if root indexes or archive state changed during compaction
if git diff --name-only HEAD 2>/dev/null | grep -qE "(doc/.*/CLAUDE\.md|\.claude/harness/archive/)"; then
  echo "- [${DATE}] PostCompact: root indexes or archive state changed — may require critic-document follow-up." >> "$QUEUE"
fi
