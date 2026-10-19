# REQ - Autonomous WBS execution

## Intent and provenance

Harness decomposes and sequences a clear multi-step request autonomously.
Historical source: user feedback recorded 2026-05-29 said task splitting and
sequence questions should be handled by Harness. That historical attribution
is retained as recorded, not independently re-verified here.

Current user_explicit source: “그냥 다 wbs안에 묶어버리면 되지 않나?” and
“호환성용 코드는 최대한 버리고싶은데... 기존꺼는 마이그레이션 하는 방향만
남겨두고.” The conversion below is a derived implementation of those current
requests while preserving the prior autonomy behavior.

## Observable behavior

- Open one WBS for the request or native objective. Keep outcomes coarse and
  update dependencies and pending outcomes as user requirements or necessary
  development discoveries arrive. Do not require a predeclared child queue.
- Choose ready work from the same tree using roadmap order, dependencies and
  risk/value. Continue implementation and outcome checks without asking the
  user which obvious stage to do next. Status reports name the next action and
  reason rather than asking the user to sequence internal work.
- Passing a leaf check is a measured checkpoint, not execution completion.
  `wbs(action="next")` selects ready outcomes within the open root;
  `wbs(action="close")` closes that execution only after all required outcomes,
  integrated work packs and independent review/QA proof pass.
- After execution close, run self-improvement, learning promotion and hygiene
  before any separately authorized follow-up execution. Do not close and reopen
  the root between every leaf.
- Ask only for an unresolved user-owned product, architecture, billing, auth,
  data, privacy, destructive/external action, credential or agreed go/no-go
  decision, or a contradiction that would implement the wrong intent.
- Preserve explicit user stops and configured budgets. Never invent product
  intent or promote an assumption into a confirmed user requirement.

## Verification cues

WBS tests cover dependency-ready outcomes, rolling refinement, truthful checks,
unknown baselines, isolated-pool integration and receipt-backed close. Runtime
instructions retain autonomous continuation and post-close learning. Retired
Goal queues and callable aliases remain absent; existing data is imported only
through explicit migration.
