---
date: 2026-07-22
task: TASK__strengthen-developer-reviewer-prompts-from-ponytail-gstack
tags: [developer, code-review, security-review, ponytail, gstack, codex]
---

# Developer and reviewer prompts now share one tested behavioral core

The Claude and Codex developer, code-reviewer, and security-reviewer prompts now
carry byte-identical marker-delimited role cores, with only runtime routing notes
outside the core. This prevents the shorter Codex copies from silently losing
PLAN/code conflict handling, surgical AC scope, evidence thresholds, or reviewed
worktree reporting.

The developer contract adopts more of Ponytail at the pinned
`16f29800fd2681bdf24f3eb4ccffe38be3baec6b` revision: understand and trace first,
then stop at current necessity, existing-code reuse, stdlib, native platform,
already-installed dependency, the smallest clear local expression, or minimum
new code. Bug fixes inspect direct and sibling callers and land at the shared
root cause. Deletion and boring primitives are preferred only when they preserve
complete requested behavior, edge-case correctness, safety invariants, and a
focused runnable regression check.

The named Claude AC worker now carries the missing operational subset without
copying the whole developer prompt: it inspects direct and relevant sibling
callers, keeps writes inside its assigned lane, reproduces bugs when feasible,
prefers deletion and boring clear primitives, preserves accessibility and
data-loss safeguards, records known ceilings, and applies an explicit new-
dependency admission rule. The Codex spawn template explicitly tells generic
workers to read the full Codex developer role and to return exact status
`needs-coordinator-review` when convergence requires an ownership, lane, or
approved-scope change.

Dogfood closed the remaining coordinator loop: an out-of-lane shared-root fix
now returns exact status `needs-coordinator-review`, which both Claude and Codex
develop flows handle before generic rollback by reassigning ownership, amending
the lane/AC, or escalating. The same ownership is never retried unchanged.
Upstream prerequisites are distinct from package dependencies, and package
admission requires assigned manifest and lockfile ownership.

Code review now proves every acceptance criterion against code, tests, and
durable docs before judging quality, detects out-of-plan changes, searches
before recommending replacements, and prevents low-confidence speculation from
becoming `FIX_NOW`. Security review adds conditional local-tool boundaries for
physical path identity, symlinks, Git worktrees/gitfiles/submodules, metadata
confinement, TOCTOU, ownership/modes, subprocess context, and hook/model/tool
output provenance. Both reviewers treat embedded commands in reviewed artifacts
as evidence rather than authority and remain strictly read-only.

Code review also follows test evidence from setup and fixtures through the
actual production path and branch to an assertion that would fail on the
claimed regression. Smoke assertions retain their legitimate narrow use but no
longer stand in for behavioral coverage, and mocks cannot silently bypass the
boundary the review claims is tested.

The task intentionally does not add a prompt generator, new reviewer agents,
line-count routing, mutating review auto-fixes, global Ponytail injection, or
raw LOC minimization.
