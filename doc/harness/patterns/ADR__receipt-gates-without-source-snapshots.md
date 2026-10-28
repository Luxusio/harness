> **Historical — superseded 2026-10-28.** [Receipt-free execution and completion](../REQ__receipt-free-execution-and-completion.md) replaces this document’s receipt collection, attestation, naming, storage and completion instructions. The body below is retained history, not active workflow guidance. Actual independent review PASS before actual QA and current required WBS checks remain required.

# ADR: Lifecycle receipts and verification target applicability

Status: amended 2026-10-21. Supersedes the developer-only source-freshness decision of 2026-08-11; lifecycle receipt identity remains unchanged.

## Context
Lifecycle receipts authenticate independent task/run/agent/lens completion and review-before-QA ordering. They alone do not prove source contents. Earlier contracts delegated post-QA edits entirely to the developer, leaving repeated manual manifests and ABA gaps.

## Decision
Keep lifecycle receipt authenticity separate from source applicability. New review/QA uses an owner-issued persistent copied target before the native invocation. Existing authenticated native task_name and matched first-generation runtime identity associate its token; no replay hash, prompt text, direct final or new receipt field supplies source authority. Roles consume the copied read-only source view. This declares a controlled source view, not a sandbox over every arbitrary role tool call.

Explicit verify, close and verified installation compare relevant current inputs/build context with active target records. Context/open/plan do not scan source. Historical lifecycle-only records remain unbound, with their historical lifecycle semantics, and are never source-certified by migration. Target-aware tasks require applicable required-lens evidence. Followup inherited tokens are unsupported for source certification; a newly issued native spawn is the supported route.

## Consequences
Source-only restoration at another path and unrelated documentation edits preserve applicable product results. Input/command/toolchain/dependency changes invalidate affected targets without rewriting authentic receipt history. Diagnostic targets neither replace acceptance nor inflate completion. Immutable manifests live beside copied input storage; WBS has only active references. See [verification target requirements](../REQ__verification-target-applicability.md) for exact supported boundary and limits. Git state is not a substitute for actual declared input bytes.
