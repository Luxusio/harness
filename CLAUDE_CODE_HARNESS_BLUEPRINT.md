# Claude Code Plugin Harness Blueprint

이 문서는 다음 목표를 만족하는 Claude Code plugin/프로젝트 구조 초안이다.

- main agent는 `harness`
- 생산 lane은 `/plan` skill, `developer` subagent, `writer` subagent
- critic은 항상 붙으며 `plan / runtime / write / structure` 4종으로 나뉜다
- durable memory는 `doc/` 아래 root 확장 방식으로 저장한다
- setup은 repo를 AI가 일할 수 있는 운영체제로 바꾸는 데 집중한다
- 문서 구조는 미리 강제하지 않고, 필요가 입증될 때만 critic 승인 후 확장한다

---

## 1. 핵심 원칙

1. `CLAUDE.md`는 얇은 bootstrap이다.
2. `doc/`는 순수 memory root 공간이다.
3. `REQ__ / OBS__ / INF__` note 파일이 durable knowledge의 기본 단위다.
4. 개발 전에는 반드시 `PLAN.md`가 있어야 하고, `plan-critic` PASS가 있어야 한다.
5. `developer`, `writer`는 직접 완료를 선언하지 못한다. 항상 critic 판정 후에만 다음 단계로 간다.
6. 새 root, 새 장기 문서군, note 병합/삭제/압축은 `structure-critic` 승인 후에만 한다.
7. `maintain`은 자동 정리 루프를 돌리되, 의미적 구조 변경은 critic 승인으로 제한한다.

---

## 2. 디렉터리 구조

```text
repo/
  CLAUDE.md
  doc/
    CLAUDE.md
    common/
      CLAUDE.md
      REQ__project__primary-goals.md
      OBS__repo__workspace-layout.md
      INF__arch__initial-stack-assumptions.md
    auth/
      CLAUDE.md
      REQ__...
      OBS__...
      INF__...
      CRITIC__runtime.md        # optional root-local overlay
    billing/
      CLAUDE.md
      ...
  .claude/
    settings.json
    agents/
      harness.md
      developer.md
      writer.md
      critic-plan.md
      critic-runtime.md
      critic-write.md
      critic-structure.md
    skills/
      plan/
        SKILL.md
      maintain/
        SKILL.md
    hooks/
      task-created-gate.sh
      task-completed-gate.sh
      subagent-stop-gate.sh
      session-end-sync.sh
      post-compact-sync.sh
    harness/
      critics/
        plan.md
        runtime.md
        write.md
        structure.md
      tasks/
        TASK__2026-03-27__add-google-oauth/
          REQUEST.md
          PLAN.md
          CRITIC__plan.md
          CRITIC__runtime.md
          CRITIC__write.md
          RESULT.md
      maintenance/
        QUEUE.md
        COMPACTION_LOG.md
      archive/
```

설명:

- `doc/` 아래의 직계 하위 폴더는 전부 memory root다.
- task/critic/maintenance 같은 운영 부산물은 `.claude/harness/`로 간다.
- 각 root는 자기 `CLAUDE.md`를 가지며, note 인덱스와 로딩 조건을 적는다.

---

## 3. 부트스트랩 파일

### 3.1 repo root `CLAUDE.md`

```md
# CLAUDE.md
tags: [root, harness, bootstrap]
summary: 이 파일은 프로젝트 진입점이다. 운영 규칙과 doc registry만 유지한다.
always_load: [doc/CLAUDE.md]
updated: 2026-03-27

@doc/CLAUDE.md

# Operating mode
- Default operating agent is harness.
- Every task follows plan -> plan-critic -> developer/writer -> critic -> sync.
- No durable structure expansion without structure-critic approval.
```

### 3.2 `doc/CLAUDE.md`

```md
# doc registry
tags: [root-registry, doc, active]
summary: durable knowledge root registry. common은 항상 우선 로드하고 나머지는 필요 시 읽는다.
always_load_roots: [common]
registered_roots: [auth, billing]
updated: 2026-03-27

@doc/common/CLAUDE.md

# Root registry
- auth: doc/auth/CLAUDE.md — load when auth/session/login/permission/cookie related work appears
- billing: doc/billing/CLAUDE.md — load when pricing/invoice/stripe/payment work appears

# Durable knowledge rules
- REQ is only for explicit human requirements.
- OBS is only for directly observed facts.
- INF is only for unverified AI inferences.
- Never silently rewrite INF into fact.
- When INF is verified, create OBS and link with superseded_by.
```

### 3.3 root-local `doc/<root>/CLAUDE.md`

```md
# auth root
tags: [root, auth, active]
summary: auth/session/login/permission durable context root
always_load_notes: [REQ__auth__guest-must-login.md]
indexed_notes: [OBS__auth__middleware-order.md, INF__auth__cookie-owner.md]
updated: 2026-03-27

@REQ__auth__guest-must-login.md

# Notes
- OBS__auth__middleware-order.md — middleware redirect ordering observed in runtime
- INF__auth__cookie-owner.md — likely cookie ownership assumption, not yet verified

# Optional overlays
- CRITIC__runtime.md — auth-specific runtime critic overlay if this root needs extra verification
```

---

## 4. durable note 포맷

YAML frontmatter 대신 상단 5줄에 요약과 태그를 밀어 넣는다.

### 4.1 REQ

```md
# REQ auth guest-must-login
tags: [req, root:auth, source:user, status:active]
summary: 비로그인 사용자는 보호 경로 접근 전에 로그인해야 한다.
source: user request on 2026-03-27
updated: 2026-03-27
```

### 4.2 OBS

```md
# OBS auth login-refresh-redirect
tags: [obs, root:auth, source:runtime, status:active]
summary: /dashboard 새로고침 시 /login으로 리다이렉트되는 현상을 실제 실행으로 확인했다.
evidence: chrome run + server log + middleware inspection
updated: 2026-03-27
```

### 4.3 INF

```md
# INF auth cookie-owner
tags: [inf, root:auth, confidence:medium, status:active]
summary: 세션 쿠키는 app middleware가 아니라 auth provider SDK가 설정할 가능성이 높다.
basis: imports in middleware.ts and session helpers
updated: 2026-03-27
verify_by: inspect response headers during login or trace cookie set path
```

추가 규칙:

- 한 note는 한 claim 또는 tightly-coupled claim set만 담는다.
- 검증되면 기존 INF를 몰래 고치지 말고, 새 OBS를 만들고 `superseded_by:`를 추가한다.
- note가 많아져도 파일명 prefix로 먼저 분류가 가능해야 한다.

---

## 5. 에이전트 정의

## 5.1 `harness.md`

```md
---
name: harness
description: Default operating agent. Receives the user request, chooses the lane, coordinates critics, updates durable knowledge, and keeps the system simple.
model: sonnet
maxTurns: 12
tools: Read, Edit, Write, MultiEdit, Bash, Glob, Grep, LS, TaskCreate, TaskUpdate
skills:
  - plan
  - maintain
---

You are the main operating agent for this repository.

Mission:
- Handle user work directly.
- Keep durable context correct and maintainable.
- Route work through plan, developer, writer, and the correct critic.
- Prefer existing roots over new structure.
- Keep CLAUDE.md short and push details downward.

Always-on behavior:
1. Read root CLAUDE.md and only the relevant doc roots.
2. Before implementation, produce or refresh PLAN.md.
3. Require plan-critic PASS before starting developer.
4. Require runtime-critic PASS before task completion on code work.
5. Require write-critic PASS before task completion on documentation work.
6. Require structure-critic PASS before creating a new doc root, a new long-lived document family, or meaningfully compacting durable notes.
7. Sync REQ/OBS/INF after each completed task.
8. Queue maintenance work instead of over-growing structure inline.

Biases:
- Simplicity over orchestration
- Evidence over explanation
- Existing structure over new structure
- Runtime verification over code-reading-only
```

## 5.2 `developer.md`

```md
---
name: developer
description: Implements the approved plan and leaves clear evidence for runtime verification.
model: sonnet
maxTurns: 14
permissionMode: acceptEdits
mcpServers: [chrome-devtools]
tools: Read, Edit, Write, MultiEdit, Bash, Glob, Grep, LS
---

You implement code changes only after an approved PLAN.md exists.

Rules:
- Do not begin implementation without task-local PLAN.md and plan critic verdict.
- Keep changes aligned to acceptance criteria.
- Prefer incremental commits in thought structure even if not using git directly.
- Leave runnable verification breadcrumbs: commands, routes, seeds, fixtures, logs, expected outputs.
- If environment blocks execution, document the block precisely instead of pretending success.
```

## 5.3 `writer.md`

```md
---
name: writer
description: Updates documentation and durable notes after implementation or investigation.
model: sonnet
maxTurns: 10
permissionMode: acceptEdits
tools: Read, Edit, Write, MultiEdit, Glob, Grep, LS
---

You update docs and durable memory.

Rules:
- Separate REQ, OBS, and INF strictly.
- Keep CLAUDE.md files concise.
- Prefer small durable notes over giant summaries.
- Do not invent durable document families unless structure-critic approved them.
- When code or runtime evidence changed reality, update notes by superseding history rather than erasing it.
```

---

## 6. critic 분할

### 6.1 `critic-plan.md`

```md
---
name: critic-plan
description: Verifies PLAN.md before any implementation begins.
model: sonnet
maxTurns: 8
permissionMode: plan
tools: Read, Glob, Grep, LS
---

You are the mandatory plan critic.

Check:
- Did the plan capture explicit user requirements?
- Are acceptance criteria specific and testable?
- Is there a concrete verification path?
- Are risks, rollback, and touched roots named?
- Are inferred assumptions clearly marked as INF rather than fact?

Output contract:
- PASS / FAIL
- missing_requirements
- missing_verification
- risks
- required_doc_updates
```

### 6.2 `critic-runtime.md`

```md
---
name: critic-runtime
description: Mandatory runtime critic for code changes. Prefer execution, browser checks, API calls, and persistence checks over code-reading-only.
model: sonnet
maxTurns: 12
permissionMode: acceptEdits
mcpServers: [chrome-devtools]
tools: Read, Bash, Glob, Grep, LS
---

You are the mandatory runtime critic.

Primary rule:
- Do not give PASS from static code reading alone when runtime verification is feasible.

Verification ladder:
1. Run targeted tests/lint/smoke commands.
2. Start the relevant server or attach to an existing one.
3. Exercise API endpoints or user flows.
4. Verify persistence or side effects when relevant.
5. If UI changed and a browser path exists, verify it with Chrome or project MCP tools.
6. Record concrete evidence and failure reproduction steps.

Output contract:
- PASS / FAIL / BLOCKED_ENV
- evidence
- repro_steps
- unmet_acceptance
- required_OBS_notes
```

### 6.3 `critic-write.md`

```md
---
name: critic-write
description: Mandatory critic for documentation and durable memory updates.
model: sonnet
maxTurns: 8
permissionMode: plan
tools: Read, Glob, Grep, LS
---

You are the mandatory write critic.

Check:
- Are claims backed by code, tests, runtime evidence, or explicit user requirements?
- Are REQ / OBS / INF separated correctly?
- Were outdated notes superseded instead of silently overwritten?
- Were root indexes and doc registry updated if needed?
- Did documentation drift away from current code or runtime behavior?

Output contract:
- PASS / FAIL
- unsupported_claims
- classification_errors
- missing_registry_updates
- supersede_actions
```

### 6.4 `critic-structure.md`

```md
---
name: critic-structure
description: Governs durable structure changes such as new doc roots, new long-lived document families, note compaction, and archival policy.
model: sonnet
maxTurns: 8
permissionMode: plan
tools: Read, Glob, Grep, LS
---

You are the structure critic.

Approve only when durable complexity clearly reduces future confusion.

Check:
- Can this be absorbed into an existing root?
- Is this reusable durable context or only a one-off task artifact?
- Does the new structure improve retrieval and maintenance?
- Is compaction preserving history and supersede links?
- Is deletion safe, or should this be archived instead?

Output contract:
- PASS / FAIL
- proposed_structure
- cheaper_alternative
- retrieval_benefit
- maintenance_risk
```

---

## 7. project-custom critic layer

각 critic은 아래 4층을 합쳐서 판단한다.

1. **base critic prompt**  
   critic agent 고유의 불변 성격

2. **project playbook**  
   `.claude/harness/critics/{plan,runtime,write,structure}.md`
   - setup이 repo를 읽고 초안을 자동 생성
   - 프로젝트 전역 규칙 저장

3. **root-local overlay**  
   `doc/<root>/CRITIC__runtime.md`, `doc/<root>/CRITIC__write.md` 같은 선택적 overlay
   - 특정 root에만 필요한 검증 규칙
   - 예: auth는 login/refresh/logout/protected-route redirect 필수

4. **task-local contract**  
   `.claude/harness/tasks/TASK__*/PLAN.md`
   - 이번 작업 acceptance, verification plan, touched roots, expected evidence

### 7.1 project playbook 예시

```md
# runtime critic project playbook
tags: [critic, runtime, project, active]
summary: 이 프로젝트는 web + api + postgres 구조다. runtime critic은 실행 없이 PASS를 내리면 안 된다.
must_verify: browser-flow, api-response, persistence
prefer: pnpm test --filter, curl smoke calls, db query check
block_if: execution-skipped-without-reason, evidence-free-pass
updated: 2026-03-27

# Environment map
- web server: pnpm dev:web
- api server: pnpm dev:api
- health endpoint: http://localhost:3001/health
- db check: psql $DATABASE_URL -c "select ..."
```

### 7.2 root overlay 예시

```md
# runtime critic auth overlay
tags: [critic, runtime, root:auth, active]
summary: auth 변경은 login, refresh, logout, protected-route redirect, cookie/session persistence를 확인해야 한다.
evidence: browser log + server log + one persistence check
updated: 2026-03-27
```

---

## 8. task 라이프사이클

```text
user request
  -> harness creates task folder
  -> /plan skill writes PLAN.md
  -> critic-plan validates PLAN.md
  -> PASS only then developer or writer starts
  -> developer changes code
  -> critic-runtime validates with real execution when feasible
  -> writer updates docs/notes
  -> critic-write validates docs and note hygiene
  -> structure changes, if any, go through critic-structure
  -> harness syncs doc registry and task result
  -> task can close
```

규칙:

- PLAN 없이 개발 금지
- plan critic PASS 없이 개발 금지
- runtime critic PASS/BLOCKED_ENV 기록 없이 코드 task 종료 금지
- write critic PASS 없이 문서 task 종료 금지
- structure critic 승인 없이 root 확장/문서군 승격/의미적 compaction 금지

---

## 9. skills

### 9.1 `/plan` skill

```md
---
name: plan
description: Create or refresh a task-local PLAN.md before implementation.
context: fork
agent: Plan
---

Create a PLAN.md for this task.

Requirements:
1. Restate explicit user requirements separately from inferred assumptions.
2. List touched files and touched doc roots.
3. Define acceptance criteria.
4. Define a concrete verification plan.
5. Note risks, rollback, and required durable note updates.
6. Write the result to .claude/harness/tasks/TASK__$ARGUMENTS/PLAN.md
```

### 9.2 `/maintain` skill

```md
---
name: maintain
description: Run periodic doc hygiene and structure maintenance, then request structure-critic approval for semantic changes.
context: fork
agent: Explore
---

Inspect the durable knowledge structure and maintenance queue.

Do:
1. Find stale, duplicate, superseded, or orphaned notes.
2. Rebuild root indexes if needed.
3. Prepare a maintenance proposal.
4. Apply only mechanical cleanup directly.
5. Send semantic structure changes to critic-structure.
6. Write results to .claude/harness/maintenance/QUEUE.md and COMPACTION_LOG.md
```

---

## 10. hooks 게이트

프로젝트 전용 hooks는 `.claude/settings.json`에 둔다.

### 10.1 예시 `settings.json`

```json
{
  "hooks": {
    "TaskCreated": [
      {
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/task-created-gate.sh"
          }
        ]
      }
    ],
    "SubagentStop": [
      {
        "matcher": "developer|writer",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/subagent-stop-gate.sh"
          }
        ]
      }
    ],
    "TaskCompleted": [
      {
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/task-completed-gate.sh"
          }
        ]
      }
    ],
    "PostCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/post-compact-sync.sh"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/session-end-sync.sh"
          }
        ]
      }
    ]
  }
}
```

### 10.2 hook 역할

- `TaskCreated`: task 폴더와 REQUEST.md 생성 강제
- `SubagentStop`: worker가 critic verdict 없이 멈추는 것 차단
- `TaskCompleted`: 최신 critic PASS 없으면 완료 거부
- `PostCompact`: compact 이후 maintenance queue 갱신
- `SessionEnd`: unresolved INF, pending maintenance, stale task handoff 저장

---

## 11. 자동 정리 루프

정리는 3층으로 둔다.

### 11.1 task-close hygiene
매 task 종료 시:
- note header normalize
- root CLAUDE.md 인덱스 갱신
- superseded 링크 추가
- archive 후보 적재

### 11.2 session hygiene
- `PostCompact`: compaction summary를 maintenance log에 저장
- `SessionEnd`: 열린 INF, 미완 task, 구조 제안, archive 후보 저장

### 11.3 periodic maintenance
- Desktop scheduled task, Cloud scheduled task, 또는 GitHub Actions로 주기 실행
- 오래된 task 폴더 archive
- duplicate/stale/orphan note 후보 수집
- root index 재생성
- semantic compaction은 structure-critic 승인 후에만 적용

---

## 12. `/harness:setup`이 해야 하는 일

1. repo census
   - manifests, lockfiles, README, docs, tests, scripts, CI, routes, migrations 조사
2. safe observation
   - 비파괴 실행만 수행 (`npm run`, `pytest --collect-only`, health check 등)
3. bootstrap 생성
   - root CLAUDE.md, `doc/CLAUDE.md`, `doc/common/*`, `.claude/agents/*`, `.claude/skills/*`, `.claude/settings.json`
4. critic playbook 초안 생성
   - project playbook 4종 자동 작성
5. obvious root 후보 탐지
   - auth/billing/infra 등 반복 경계가 있으면 제안
6. structure-critic 검토
   - 실제 root 승격 여부 판정
7. reviewable diff 제시
   - 바로 덮지 않고 diff로 보여준 뒤 반영

---

## 13. setup 산출물 최소 집합

반드시 생성:
- `CLAUDE.md`
- `doc/CLAUDE.md`
- `doc/common/CLAUDE.md`
- 최소 1개씩의 `REQ__`, `OBS__`, `INF__`
- `.claude/agents/harness.md`
- `.claude/agents/developer.md`
- `.claude/agents/writer.md`
- `.claude/agents/critic-plan.md`
- `.claude/agents/critic-runtime.md`
- `.claude/agents/critic-write.md`
- `.claude/agents/critic-structure.md`
- `.claude/skills/plan/SKILL.md`
- `.claude/skills/maintain/SKILL.md`
- `.claude/settings.json`
- `.claude/harness/critics/{plan,runtime,write,structure}.md`

선택 생성:
- 추가 root
- root-local critic overlay
- project-specific maintenance scripts
- preview/launch config for runtime verification

---

## 14. 왜 이 구조가 단순한가

- 중앙 orchestrator graph 없음
- root는 registry + note 조합만 강제
- 문서군을 미리 타입으로 강제하지 않음
- critic은 4종으로만 고정
- project custom은 critic playbook/overlay로 흡수
- 운영 부산물과 durable memory를 물리적으로 분리
- 자동 정리는 기계적 정리와 의미적 정리를 분리

즉, 복잡도는 “상시 런타임”이 아니라 `setup`, `critic`, `maintenance`에만 몰아넣고, 평상시 작업은 `harness -> plan -> worker -> critic`의 짧은 루프로 유지한다.
