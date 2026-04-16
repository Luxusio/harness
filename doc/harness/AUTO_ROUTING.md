# harness Auto-Routing

tags: [harness, routing, intent]
status: draft
created: 2026-04-09
task_ref: TASK__harness-architecture

Auto-routing is harness's zero-learning-curve interface. When a user expresses intent in natural language, harness reads the pattern and routes to the appropriate specialist without requiring slash commands.

---

## Routing Principles

1. **Route first, respond second.** When intent matches a specialist, invoke the specialist rather than answering ad-hoc. The specialist produces better results.
2. **Prefer specificity.** If multiple patterns match, the most specific pattern wins. "bug in the auth service" → investigate, not health.
3. **Ambiguous intent → clarify briefly, then route.** Do not silently default to ad-hoc.
4. **Non-routable requests** (e.g., "what does this function do?") are answered directly. Routing is for actions, not explanations.

---

## Intent Pattern → Specialist Mapping

### investigate

**Activates when:**
- User reports errors, exceptions, stack traces
- "왜 안돼", "에러", "버그", "오류", "왜 실패해", "깨졌어"
- "bug", "broken", "error", "exception", "failing", "not working", "why is this"
- "it was working yesterday", "something changed", "regression"
- "500 error", "crash", "panic", "segfault"
- "root cause", "debug this", "investigate", "diagnose"
- User pastes a stack trace or error log
- Unexpected output without an obvious cause

**Routing action:** Invoke `investigate` specialist. Do not attempt to fix inline.

**Example triggers:**
```
"TypeError: cannot read property 'id' of undefined"
"왜 빌드가 실패하지?"
"이 API가 갑자기 500을 뱉어"
"it was working an hour ago, now it crashes on startup"
```

---

### health

**Activates when:**
- "코드 품질", "헬스체크", "health check", "quality score"
- "how healthy is the codebase", "run all checks"
- "lint errors", "type errors", "how many test failures"
- "code quality dashboard", "quality report"
- "before I merge, what's the state of the codebase"
- User asks for a comprehensive quality scan before a release or PR

**Routing action:** Invoke `health` specialist. Reports weighted composite score.

**Example triggers:**
```
"코드베이스 상태 어때?"
"run a health check"
"quality score before I merge this"
"how many type errors do we have?"
```

---

### review

**Activates when:**
- "리뷰", "PR 확인", "코드 리뷰", "diff 봐줘"
- "review this PR", "code review", "pre-landing review", "check my diff"
- "is this safe to merge?", "look at my changes", "review before I push"
- User is about to merge or land code changes
- "any issues with this diff?"

**Routing action:** Invoke `review` specialist. Analyzes diff against base branch.

**Example triggers:**
```
"이 PR 리뷰해줘"
"review my diff before I merge"
"check my changes for issues"
"is this PR safe to land?"
```

---

### checkpoint

**Activates when:**
- "저장", "체크포인트", "checkpoint", "save progress"
- "어디까지 했지", "where was I", "resume", "pick up where I left off"
- "다시 이어서 하고 싶어", "what was I working on"
- Session appears to be ending or user is switching context
- User mentions a break or returning after time away
- "save my work", "I'll come back to this"

**Routing action:** Invoke `checkpoint` specialist (save or restore mode based on context).

**Example triggers:**
```
"checkpoint"
"지금 상태 저장해줘"
"어디까지 했더라?"
"I'm stepping away, save where we are"
"picking up where we left off"
```

---

### learn

**Activates when:**
- "배운 것", "learnings", "what have we learned"
- "show learnings", "past patterns", "we fixed this before"
- "이전에 비슷한 문제 있었나?", "didn't we solve this already?"
- "prune stale learnings", "export learnings"
- "what do we know about X from past sessions"

**Routing action:** Invoke `learn` specialist. Searches and manages session learnings.

**Example triggers:**
```
"이전에 이런 에러 본 적 있어?"
"what have we learned about auth in this project?"
"show me the learnings"
"prune old learnings"
```

---

### retro

**Activates when:**
- "회고", "레트로", "retro", "retrospective"
- "weekly retro", "what did we ship", "engineering retrospective"
- "이번 주 어땠어?", "this sprint review"
- "what patterns did we see this week"
- User is at the end of a sprint or work week

**Routing action:** Invoke `retro` specialist. Analyzes git history and code quality metrics.

**Example triggers:**
```
"weekly retro"
"이번 주 뭐 했지?"
"engineering retrospective for this sprint"
"what did we ship this week?"
```

---

### document (DOC_SYNC)

**Activates when:**
- "문서 업데이트", "docs update", "sync documentation"
- "update the docs", "post-ship docs", "documentation sync"
- After a task closes with pending doc changes
- "CLAUDE.md 업데이트", "README 업데이트"
- "keep docs in sync"

**Routing action:** Invoke `writer` agent → produce DOC_SYNC.md → critic-document pass.

**Example triggers:**
```
"update documentation after this change"
"sync the docs"
"docs are out of date"
```

---

## Routing Decision Matrix

| Signal type | Routed to | Notes |
|-------------|-----------|-------|
| Error / stack trace / crash | investigate | Highest priority — always route |
| "bug", "broken", "not working" | investigate | Even vague reports route to investigate |
| Code quality / health scan | health | |
| PR / diff / merge review | review | |
| Save state / resume session | checkpoint | |
| Past patterns / learnings | learn | |
| Retrospective / sprint review | retro | |
| Doc sync / update docs | writer (DOC_SYNC) | |
| New task / feature request | plan-skill (canonical loop start) | |
| Question / explanation | direct answer (no routing) | Not all requests need routing |

---

## Non-Routable Requests

These are answered directly without invoking a specialist:

- Explanations ("what does this function do?")
- Definitions ("what is X?")
- Simple lookups ("what's the current branch?")
- Status checks ("show me open tasks")
- Confirmations ("yes/no" responses)

---

## Routing Confidence Levels

| Confidence | Behavior |
|-----------|----------|
| High (clear pattern match) | Invoke specialist immediately, announce routing |
| Medium (ambiguous) | State intent interpretation, invoke unless user corrects |
| Low (multiple patterns match) | Ask one clarifying question, then route |
| None (no match) | Answer directly |

**Announcement format:**
```
Routing to investigate — looks like a debugging task.
[investigate output follows]
```

This keeps routing transparent without requiring user confirmation for high-confidence cases.
