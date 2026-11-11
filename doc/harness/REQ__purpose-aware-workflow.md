# Purpose-aware Harness execution

Status: accepted user outcome; implementation in progress

## Requirement provenance

The user explicitly observed that ordinary documentation and planning still
force the complete development workflow, and asked for flexibility across
different users and projects. The user then approved the proposed split with
“ㅇㅇ ㄱㄱ 그렇게 바꿔줘”. The approved proposal separates read-only discussion,
document/planning deliverables and development; keeps one WBS; promotes actual
executable changes within that WBS; configures project policy in existing YAML;
and installs only when this work changes an installed payload.

The requirements below are user-confirmed outcomes. The representation and
implementation choices are derived necessities, not independently requested
features. The private inbox's API-token reuse suggestion is not adopted here.

## Required behavior

- Read-only questions, research discussions and reviews do not open a WBS.
- A requested document or plan is an independent deliverable. Its completion
  requires relevant content, source and link verification, without automatically
  entering product development, product QA or deployment.
- WBS remains the shared outcome/progress owner. A completed planning phase of
  an implementation request does not complete its unimplemented product outcome.
- Development retains independent code review, applicable security review,
  actual relevant QA and current outcome checks. A current substantive failure
  cannot be cleared by a green machine assertion.
- Actual effect governs verification. Runtime instructions, authority/config,
  builds and executable changes cannot become ordinary prose merely through a
  Markdown extension or placement under a documentation directory. Unknown
  impact requires development scrutiny.
- When development becomes necessary and is authorized, update the same WBS
  and run, preserving history and invalidating incompatible evidence. A change
  of declaration cannot erase already admitted development obligations.
- Existing project YAML controls document review, development verification and
  automatic delivery preferences. Hooks, MCP and skills use the same policy.
- Automatic installation is conditional on this work's actual payload changes,
  including committed changes. Document work does not repair unrelated runtime
  drift. Explicit manual installation remains available and fully validated.
- User changes, protected artifact writers, private authentication boundaries,
  source ownership, required reviews and stopped-worker cleanup remain intact.

## Derived representation

One optional `wbs-work` JSON fence in the owner-published PLAN declares `kind`:
`document`, `plan` or `development`. Omission retains development semantics.
This purpose is distinct from planning depth and review depth. It does not add
a control file or public skill. Project preferences live under `workflow` in
`doc/harness/manifest.yaml`; malformed or unsupported settings refuse rather
than silently weakening execution.

```yaml
workflow:
  document_review: self       # self | independent (existing document critic)
  verification: affected      # affected | full (development test selection)
  install: changed            # changed | manual
  document_paths: [doc/common, docs, README.md, README.codex.md, CHANGELOG.md]
  executable_paths: []        # additive project-specific instruction boundaries
```

Omitting `workflow` uses the shown defaults. These are project settings, not
environment overrides. A document/plan workpack declares its purpose as:

```wbs-work
{"kind":"document"}
```

Use `plan` only for a requested planning deliverable. Development is the default;
the same declaration can promote authorized executable work to `development`.
`review-document` selects the existing `critic-document` method. No independent
role target is needed for self-checked prose; any declared independent role still
uses its applicable source view. WBS checks remain measured and owner-published.
Choose explicit verification inputs for the actual deliverable and necessary
references when they become known. Document/planning verification does not fall
back to hashing the whole checkout. A pure document repository can explicitly
select `document_paths: [.]`; known instructions, configuration and executable
files still require development scrutiny.

Configured document paths identify narrative roles, with additive executable
paths and immutable known instruction/config/runtime exclusions. This mechanism
does not claim to understand arbitrary document semantics. The workflow must
escalate behavioral or uncertain changes even within a narrative role.

Verification and admission bind only relevant policy effects. Delivery-only
preferences do not invalidate unchanged review/test evidence. Preserve the
minimal owner-captured Git base needed to detect committed delivery changes;
do not recapture it after work has begun or add a separate state ledger.

## Verification and recovery

Exercise native document/plan and development publication/check/close flows,
real prewrite paths, same-run promotion, incompatible proof/target invalidation,
invalid configuration, absent/foreign ownership, no-source-hash control calls,
and committed-payload/manual/no-payload delivery selection. Verify both runtime
prompts and setup contracts agree with these paths.

The four control keys remain unchanged, but older runtimes cannot interpret
empty or document-only role declarations. Before returning to an older runtime,
finish or park active lightweight work under the new runtime, or use its WBS
owner to promote to development with fresh required evidence. Do not install
permissive legacy readers. New runtime sessions load the updated behavior.
