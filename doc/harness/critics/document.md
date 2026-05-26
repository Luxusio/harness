# document critic project playbook
summary: harness plugin — Python scripts, Markdown agents/skills/docs
updated: 2026-03-30

# Hard FAIL conditions

- Facts in documentation contradict observable reality (code, tests, runtime)
- Two active documents directly contradict each other
- Documentation changes make things harder to find (broken links, removed indexes without replacement)
- DOC_SYNC.md claims notes were created but the files don't exist
- DOC_SYNC.md omits changes that actually happened (drift between claim and reality)
- DOC_SYNC.md claims "none" across all sections but doc files actually changed on disk
- Supersede chain is broken: a superseded note is still marked `status: active`
- Root index was not updated after a note was created or removed
- A changed `REQ__*.md` is too vague for implementation or QA to verify
- A changed `REQ__*.md` omits observable UI/API behavior introduced by the diff
- A changed `REQ__*.md` contradicts PLAN.md, HANDOFF.md, tests, or implementation

# Documentation impact judgment

Before failing a task for a missing REQ, inspect the task's PLAN/HANDOFF/DOC_SYNC
durable-doc judgment:

- `REQ needed` should name a concrete `doc/<area>/REQ__*.md` path or explain why
  an existing REQ already covers the behavior.
- `Pattern/skill doc enough` is coherent when the diff changes harness process,
  agent instructions, testing guidance, coding conventions, or implementation
  patterns without changing a product/runtime contract.
- `No durable doc needed` is coherent only for internal-only, test-only,
  mechanical, or one-off maintenance changes where the reason names the
  unchanged durable knowledge surface.

For clear UI/API/backoffice/admin screens, routes, controllers, endpoints,
native navigation/back-stack behavior, externally consumed contracts, or
observable bugfixes, missing REQ remains a FAIL. For ambiguous changes, judge
whether the recorded durable-doc decision is coherent before escalating.

# Durable REQ quality bar

For each changed `doc/<area>/REQ__*.md`, verify that the note answers:

- What user-visible behavior or externally consumed contract must hold
- Which screen states, filters/search/sorting, loading/empty/error states,
  labels/visibility, and click/input interactions apply for UI behavior
- Which request/response shape, status codes, validation, auth/session behavior,
  compatibility, and side effects apply for API behavior
- What verification cues QA should use to prove the requirement
- What is explicitly out of scope when the boundary is easy to misread

PASS only when the REQ is specific enough that a future implementer or QA agent
can detect a mismatch without reading the old task transcript.

# Checks (warnings, not automatic FAIL)

- Missing index updates after note creation
- Notes without evidence fields (OBS) or verify_by fields (INF)
- Stale freshness metadata
- Notes marked INF that have never been verified

# Verification procedure

1. Compare DOC_SYNC.md claims against `git diff --name-only` — every changed doc file must appear in DOC_SYNC.md
2. For each note listed as created: confirm the file exists on disk
3. For each note listed as updated: confirm the file was actually modified
4. For each supersede entry: confirm old note is marked `status: superseded` and new note is `status: active`
5. For each index refresh listed: confirm root CLAUDE.md entry exists and is accurate
6. Check that no doc file changed silently (changed on disk but absent from DOC_SYNC.md)
7. For each changed `REQ__*.md`, compare the note to PLAN.md, HANDOFF.md,
   REQUEST.md if present, changed source files, and tests. FAIL if required
   observable behavior exists only in task artifacts and not in the REQ.
