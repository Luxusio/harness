---
freshness: current
owner: harness
---

# Runbook Memory

Harness runbooks are repo-local execution recipes that were discovered during
work and are worth reusing in later sessions. They live outside chat history so
the next agent can see them immediately.

## Files

- `doc/harness/runbooks.yaml` contains approved runbooks.
- `doc/harness/runbook_candidates.yaml` contains unapproved candidates.

Approved runbooks are shown by `prompt_memory.py` in a capped
`[harness-runbooks]` reminder. Candidates are only reminders to run
`harness:maintain`; they are not trusted until reviewed.

## Workflow

Add a candidate when a setup command or environment sequence took real
discovery work:

```bash
python3 plugin/scripts/runbook_memory.py add-candidate \
  --id integration-up \
  --description "Start local full-stack integration environment" \
  --command "./scripts/integration-up.sh" \
  --gotcha "Use --no-daemon when Gradle env changes"
```

Approve or skip candidates through `harness:maintain`:

```bash
python3 plugin/scripts/runbook_memory.py approve integration-up
python3 plugin/scripts/runbook_memory.py skip old-candidate
```

Never persist tokens, passwords, API keys, private keys, or machine-specific
secrets in a runbook. Redact the command first.
