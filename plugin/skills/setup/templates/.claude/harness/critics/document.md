# document critic project playbook
tags: [critic, document, project, active]
summary: {{PROJECT_SUMMARY}}
updated: {{SETUP_DATE}}

# Note naming and classification
- Note file names must follow REQ__/OBS__/INF__ prefix convention
- REQ notes require source field
- OBS notes require evidence field
- INF notes require verify_by field
- Never mix categories in a single note

# Index sync
- Root CLAUDE.md indexes must stay in sync with actual note files
- Root CLAUDE.md registry must reflect actual roots on disk

# Supersede history
- Superseded notes must retain history via superseded_by links
- Never silently overwrite existing notes
- Compaction must preserve supersede chains

# Structure governance
- New root creation requires demonstrated retrieval benefit
- Prefer fewer roots with more notes over many roots with few notes
- Archive over delete when content has potential future value
- Compaction that loses supersede history is FAIL

# DOC_SYNC validation
- Repo-mutating work claiming durable updates must provide DOC_SYNC.md
- Verify claimed note/index updates actually exist on disk
