---
freshness: current
owner: harness
---

# Runbook Memory

Harness runbooks are repo-local execution recipes that were discovered during
work and are worth reusing in later sessions. They live outside chat history so
the next agent can see them immediately.

Runtime-local memory is only staging. Files such as
`doc/harness/learnings.jsonl`, `doc/harness/tasks/**`, and
`doc/harness/runtime/**` are not committed, so they do not share knowledge with
future contributors by themselves. Reusable discoveries must be promoted to a
committed artifact: a runbook, `doc/harness/patterns/*.md`,
`doc/common/GUIDE__*.md`, a skill, a script, or a regression test.

## Files

- `doc/harness/runbooks.yaml` contains approved runbooks.
- `doc/harness/runbook_candidates.yaml` contains unapproved, gitignored local
  staging. It must be approved into `runbooks.yaml` or another committed
  artifact before it becomes shared project knowledge.

Approved runbooks are shown by `prompt_memory.py` in a capped
`[harness-runbooks]` reminder. Candidates are only reminders for the
active/next harness task; they are not trusted until reviewed and promoted into
a committed artifact.

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

Approve or skip candidates while handling the active/next harness task:

```bash
python3 plugin/scripts/runbook_memory.py approve integration-up
python3 plugin/scripts/runbook_memory.py skip old-candidate
```

Never persist tokens, passwords, API keys, private keys, or machine-specific
secrets in a runbook. Redact the command first.

## Commit-backed learning rule

At task close, classify reusable learning as `none`, `captured`, or `rejected`.
`captured` means the lesson now appears in a committed artifact such as a
runbook, pattern, guide, skill, script, or regression test. `rejected` means the
agent considered the lesson but kept it local because it was task-specific,
noisy, or not reusable. A raw `learnings.jsonl` row alone is never enough for
`captured`.
