---
name: migrate-docs
description: Migrate explicitly selected repository business documents to the configured Confluence knowledge source while preserving source meaning, references and recoverability.
---

Inspect the selected source documents and existing destination topics first.
Move only business documentation in the user's authorized scope. Keep AGENTS,
CLAUDE, SKILL, source assets/config and WBS evidence in their owning repository.

Preserve original requirements, conditions, exceptions, tables, code, links and
image/attachment references. Apply the knowledge skill's rules for
[selecting the authoritative home](../knowledge/SKILL.md#designate-a-source-of-truth)
and [preserving evidence and authority](../knowledge/SKILL.md#distinguish-evidence-and-authority)
to each migrated topic. Retain original versions and historical decisions.
Maintain source-to-destination references and use native editing operations.

Verify stored content and link/reference coverage, integrate derived knowledge
and obtain independent document review. Upload success alone does not establish
a successful migration. Leave failures and unverified pieces explicit.

Deleting repository originals, changing official source documents or widening
the destination audience requires authorization for that specific action.
Before authorized deletion, establish committed recoverability. Do not silently
remove originals, bulk rewrite company spaces, or treat this skill as publication
authorization. No generic migration ledger or retry queue is required.
