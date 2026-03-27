#!/usr/bin/env bash
set -euo pipefail

# After context compaction, update maintenance queue.
QUEUE=".claude/harness/maintenance/QUEUE.md"

if [[ ! -f "$QUEUE" ]]; then
  exit 0
fi

DATE=$(date +%Y-%m-%d)
echo "- [${DATE}] PostCompact: context was compacted. Review open tasks and INF notes for staleness." >> "$QUEUE"
