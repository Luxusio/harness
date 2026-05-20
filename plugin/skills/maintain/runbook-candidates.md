# Runbook Candidate Review

Runbook candidates are repo-specific execution recipes discovered during
development. They are not trusted until reviewed.

Detect candidates:

```bash
python3 plugin/scripts/runbook_memory.py list
```

For each pending candidate in `doc/harness/runbook_candidates.yaml`:

```
AskUserQuestion:
  Question: "Runbook candidate detected: <id>. Approve this execution recipe?"
  Options:
    - A) Approve — move it into doc/harness/runbooks.yaml
    - B) Defer — keep it pending for later
    - C) Skip — remove the candidate
```

On A: `python3 plugin/scripts/runbook_memory.py approve <id>`, then re-run
`python3 plugin/scripts/runbook_memory.py render` to confirm the prompt block is
concise and secret-free.

On B: no-op. The candidate remains pending.

On C: `python3 plugin/scripts/runbook_memory.py skip <id>`.

Never approve a candidate containing tokens, passwords, API keys, private keys,
or user-specific absolute paths. Ask for a redacted replacement instead.
