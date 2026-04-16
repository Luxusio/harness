# Claude Code Plugin Harness Blueprint (v3)

> ⚠️ Historical design note: this blueprint started as a v3 draft. The current repo uses root `CLAUDE.md` plus `doc/CLAUDE.md` as the documentation registry, and plugin-shipped agents must not rely on `permissionMode`, `mcpServers`, or `hooks` frontmatter. For current executable contracts, prefer `plugin/agents/*`, `plugin/scripts/*`, and `plugin/skills/setup/*`.

이 문서는 다음 목표를 만족하는 Claude Code plugin/프로젝트 구조 설계다.

- main entrypoint는 `harness` plugin — main Claude session이 `Skill(harness:*)`을 직접 호출한다 (별도 orchestrator agent 없음)
- 생산 lane은 `/plan` skill, `developer` subagent, `writer` subagent
- critic은 `plan / runtime / document` 3종 (unified document critic)
- durable memory는 `doc/` 아래 root 확장 방식으로 저장한다
- setup은 repo를 AI가 일할 수 있는 운영체제로 바꾸되 executable QA scaffolding을 포함한다
- root `CLAUDE.md`가 single repo entrypoint이자 durable root registry
- `doc/harness/manifest.yaml`가 initialization marker
- 모든 substantial repo-mutating work는 contract + executable QA + persistence + docs sync를 거친다
- architecture constraints는 optional machine-enforced checks로 분리

---

## 1. 핵심 원칙

1. root `CLAUDE.md`가 single entrypoint이고, 현재 구현에서는 `doc/CLAUDE.md`를 문서 registry로 로드한다.
2. `doc/`는 순수 memory root 공간이다.
3. `REQ__ / OBS__ / INF__` note 파일이 durable knowledge의 기본 단위다.
4. 개발 전에는 반드시 contract `PLAN.md`가 있어야 하고, `plan-critic` PASS가 있어야 한다.
5. `developer`, `writer`는 직접 완료를 선언하지 못한다. 항상 critic 판정 후에만 다음 단계로 간다.
6. 새 root, 새 장기 문서군, note 병합/삭제/압축은 `critic-document` 승인 후에만 한다.
7. `maintain`은 자동 정리 루프를 돌리되, 의미적 구조 변경은 critic-document 승인으로 제한한다.
8. `BLOCKED_ENV`는 task를 열린 상태로 유지하고 blocker를 명시적으로 기록한다. 절대 task를 닫지 않는다.

---

## 2. 디렉터리 구조

```text
repo/
  CLAUDE.md                        # single entrypoint + root registry
  doc/
    common/
      CLAUDE.md                    # always-loaded root index
      REQ__project__primary-goals.md
      OBS__repo__workspace-layout.md
      INF__arch__initial-stack-assumptions.md
    auth/
      CLAUDE.md
      REQ__...
      OBS__...
      INF__...
    billing/
      CLAUDE.md
      ...
  scripts/
    harness/
      verify.py                    # main verification entry point
      smoke.sh                     # smoke test runner
      healthcheck.sh               # health check probe
      reset-db.sh                  # DB reset / seed
  .claude/
    settings.json
    harness/
      manifest.yaml                # initialization marker + runtime config
      critics/
        plan.md
        runtime.md
        document.md
      constraints/                 # optional
        architecture.md
        check-architecture.sh
      tasks/
        TASK__2026-03-27__add-google-oauth/
          REQUEST.md
          PLAN.md
          TASK_STATE.yaml
          HANDOFF.md
          QA__runtime.md
          DOC_SYNC.md
          CRITIC__plan.md
          CRITIC__runtime.md
          CRITIC__document.md
          RESULT.md
      maintenance/
        QUEUE.md
        COMPACTION_LOG.md
      archive/
```

설명:

- root `CLAUDE.md`가 entrypoint이고 `doc/CLAUDE.md`가 문서 registry 역할을 맡는다.
- `doc/harness/manifest.yaml`가 initialization marker.
- `scripts/harness/`는 executable QA scaffolding.
- task 폴더에 `TASK_STATE.yaml`, `HANDOFF.md`, `QA__runtime.md`, `DOC_SYNC.md` 추가.
- `constraints/`는 optional machine-enforced architecture checks.

---

## 3. 부트스트랩 파일

### 3.1 repo root `CLAUDE.md`

```md
# CLAUDE.md
tags: [root, harness, bootstrap]
summary: repo entrypoint and durable root registry
always_load_paths: [doc/common/CLAUDE.md]
registered_roots: [common]
updated: 2026-03-27

@doc/common/CLAUDE.md

# Operating mode
- Repo-mutating work routes through harness skills (`Skill(harness:run)` / `Skill(harness:plan)` / `Skill(harness:develop)` / `Skill(harness:setup)` / `Skill(harness:maintain)`). No separate orchestrator agent — the main Claude session invokes skills directly.
- Every substantial repo-mutating task follows:
  request -> contract plan -> plan critic -> implement -> runtime QA -> persistence -> docs sync -> document critic -> close.
- New durable roots or durable structure changes go through critic-document.
- `doc/harness/manifest.yaml` is the initialization marker.
```

### 3.2 `doc/common/CLAUDE.md`

```md
# common root
tags: [root, common, active]
summary: always-loaded durable context root. note index only.
always_load_notes: [REQ__project__primary-goals.md]
indexed_notes: [OBS__repo__workspace-layout.md, INF__arch__initial-stack-assumptions.md]
updated: 2026-03-27

@REQ__project__primary-goals.md

# Notes
- OBS__repo__workspace-layout.md — repo directory structure observation
- INF__arch__initial-stack-assumptions.md — initial stack assumptions (unverified)
```

### 3.3 `doc/harness/manifest.yaml`

```yaml
version: 3
initialized_at: 2026-03-27
entrypoint: CLAUDE.md
always_load_paths:
  - doc/common/CLAUDE.md
registered_roots:
  - common
runtime:
  verify_script: scripts/harness/verify.py
  smoke_script: scripts/harness/smoke.sh
  reset_script: scripts/harness/reset-db.sh
  healthchecks: []
workflow:
  contract_required: true
  qa_required_for_repo_mutations: true
  persistence_required: true
  docs_sync_required: true
  document_critic: critic-document
```

### 3.4 root-local `doc/<root>/CLAUDE.md`

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

### 5.1 `harness.md`

```md
---
name: harness
description: Default operating agent. Routes work, coordinates critics, updates durable knowledge.
model: sonnet
maxTurns: 12
tools: Read, Edit, Write, MultiEdit, Bash, Glob, Grep, LS, TaskCreate, TaskUpdate
skills:
  - plan
  - maintain
---

Mission:
- Route user work through the mutate-repo loop.
- Always read doc/harness/manifest.yaml when initialized.
- Keep durable context correct and maintainable.

Lane simplification:
- answer/explain → direct response
- everything that mutates the repo → common mutate-repo loop
- maintain → maintenance loop plus critic-document for semantic changes
```

### 5.2 `developer.md`

```md
---
name: developer
description: Implements the approved plan and leaves clear evidence for runtime verification.
model: sonnet
maxTurns: 14
tools: Read, Edit, Write, MultiEdit, Bash, Glob, Grep, LS
---

Before acting:
- Read doc/harness/manifest.yaml and task-local TASK_STATE.yaml
- Read doc/harness/critics/runtime.md
- Read optional doc/harness/constraints/*
- For browser QA prerequisites in plugin form, rely on project/session MCP scope such as `.mcp.json`, not agent frontmatter

On finish:
- Update TASK_STATE.yaml to status: implemented
- Write developer handoff into HANDOFF.md
- Record exact verification breadcrumbs for QA
```

### 5.3 `writer.md`

```md
---
name: writer
description: Updates documentation and durable notes.
model: sonnet
maxTurns: 10
tools: Read, Edit, Write, MultiEdit, Glob, Grep, LS
---

Before acting:
- Read doc/harness/critics/document.md

Output contract:
- Write DOC_SYNC.md summarizing note and index updates
- State that new root creation / archive / compaction require critic-document approval
```

### 5.4 `critic-plan.md`

```md
---
name: critic-plan
description: Verifies PLAN.md as a contract before implementation.
model: sonnet
maxTurns: 8
tools: Read, Glob, Grep, LS
---

Before acting: read doc/harness/critics/plan.md

Output contract:
- verdict: PASS | FAIL
- missing_requirements, missing_verification, missing_persistence, missing_docs_sync, risks, required_doc_updates
```

### 5.5 `critic-runtime.md`

```md
---
name: critic-runtime
description: Mandatory runtime critic with browser-first QA.
model: sonnet
maxTurns: 12
disallowedTools: Edit, Write, MultiEdit, Agent, Skill
---

Before acting: read doc/harness/critics/runtime.md
Optionally run doc/harness/constraints/check-architecture.* if present.
For browser QA in plugin form, rely on project/session MCP scope such as `.mcp.json`, not agent frontmatter.

BLOCKED_ENV is a runtime verdict only — task stays open with status: blocked_env.

Output contract:
- verdict: PASS | FAIL | BLOCKED_ENV
- evidence, repro_steps, unmet_acceptance, blockers, required_OBS_notes
```

### 5.6 `critic-document.md`

```md
---
name: critic-document
description: Unified critic for documentation, note hygiene, and durable structure changes.
model: sonnet
maxTurns: 8
tools: Read, Glob, Grep, LS
---

Before acting: read doc/harness/critics/document.md

Replaces critic-write and critic-structure.

Output contract:
- verdict: PASS | FAIL
- unsupported_claims, classification_errors, missing_registry_updates
- structure_actions, supersede_actions, notes
```

---

## 6. project-custom critic layer

각 critic은 아래 4층을 합쳐서 판단한다.

1. **base critic prompt**
   critic agent 고유의 불변 성격

2. **project playbook**
   `doc/harness/critics/{plan,runtime,document}.md`
   - setup이 repo를 읽고 초안을 자동 생성
   - 프로젝트 전역 규칙 저장

3. **root-local overlay**
   `doc/<root>/CRITIC__runtime.md` 같은 선택적 overlay
   - 특정 root에만 필요한 검증 규칙

4. **task-local contract**
   `doc/harness/tasks/TASK__*/PLAN.md`
   - 이번 작업 acceptance, verification plan, touched roots, expected evidence

### 6.1 project playbook 예시

```md
# runtime critic project playbook
tags: [critic, runtime, project, active]
summary: 이 프로젝트는 web + api + postgres 구조다.
must_verify: browser-flow, api-response, persistence
prefer: pnpm test --filter, curl smoke calls, db query check
block_if: execution-skipped-without-reason, evidence-free-pass
updated: 2026-03-27

# Environment map
- web server: pnpm dev:web
- api server: pnpm dev:api
- health endpoint: http://localhost:3001/health
- db check: psql $DATABASE_URL -c "select ..."

# Browser-first QA map
- preferred_verification_order: [tests, smoke, api, persistence, browser]
- health_checks: [http://localhost:3001/health]
- seed_reset_commands: [pnpm db:reset, pnpm db:seed]
- persistence_checks: [psql $DATABASE_URL -c "select count(*) from users"]
```

---

## 7. task state model

### 7.1 `TASK_STATE.yaml`

```yaml
status: created
mutates_repo: true
qa_required: true
qa_mode: browser-first
plan_verdict: pending
runtime_verdict: pending
document_verdict: pending
needs_env: []
updated: 2026-03-27
```

Recommended states:
- `created` → `planned` → `plan_passed` → `implemented` → `qa_passed` → `persisted` → `docs_synced` → `document_passed` → `closed`
- `blocked_env` (task stays open, blocker is surfaced)

### 7.2 `HANDOFF.md`

Developer writes after implementation:

```text
Result:
  from: developer
  scope: <what changed>
  changes: <files modified>
  verification_inputs: <routes / commands / fixtures / test names>
  blockers: <env / data / secrets issues>
  next_action: runtime QA
```

### 7.3 `DOC_SYNC.md`

Writer writes after durable note work:

```md
# DOC_SYNC
updated: <date>

## Notes created
- <note path> — <description>

## Notes updated
- <note path> — <what changed>

## Notes superseded
- <old note> → <new note>

## Indexes refreshed
- <root CLAUDE.md paths updated>

## Registry changes
- <root CLAUDE.md registry updates, or "none">
```

### 7.4 `QA__runtime.md`

Runtime critic records verification evidence:

```md
# QA Runtime Evidence
date: <date>
qa_mode: browser-first

## Tests run
- <test name>: PASS/FAIL

## Smoke checks
- <command>: <output summary>

## Persistence checks
- <check>: <result>

## Browser checks
- <route/flow>: <result + screenshot if applicable>
```

---

## 8. task 라이프사이클 (v3)

```text
user request
  → harness creates task folder
  → REQUEST.md
  → PLAN.md written as a contract
  → CRITIC__plan.md must PASS
  → implementation (developer, and writer when docs/notes are involved)
  → QA__runtime.md recorded from executable verification
  → CRITIC__runtime.md must PASS
  → TASK_STATE.yaml and HANDOFF.md updated
  → DOC_SYNC.md records durable note/index updates
  → CRITIC__document.md must PASS
  → RESULT.md
  → task close
```

규칙:

- PLAN 없이 개발 금지
- plan critic PASS 없이 개발 금지
- runtime critic PASS 없이 코드 task 종료 금지 (BLOCKED_ENV는 task를 열린 상태로 유지)
- document critic PASS 없이 문서 task 종료 금지
- DOC_SYNC.md 없이 repo-mutating task 종료 금지
- TASK_STATE.yaml의 status: blocked_env인 task는 종료 불가

For answer-only / non-mutating work: no task folder required.

---

## 9. skills

### 9.1 `/plan` skill

PLAN.md를 contract document로 작성한다.

필수 섹션:
- Scope in / Scope out
- User-visible outcomes
- QA mode
- Acceptance criteria
- Verification contract (commands, routes, persistence checks, expected outputs)
- Persistence steps (TASK_STATE, HANDOFF)
- Required doc sync (notes, indexes)
- Hard fail conditions
- Risks / rollback

TASK_STATE.yaml와 HANDOFF.md도 초기화한다.

### 9.2 `/maintain` skill

root `CLAUDE.md` registry + root-specific `CLAUDE.md` 기반으로 검사한다.
semantic durable changes는 `critic-document`에게 보낸다.
optional architecture constraint checks도 실행한다.

---

## 10. hooks 게이트

### 10.0 hook 계약 (CRITICAL)

모든 hook 스크립트는 반드시:
- stdin JSON을 파싱한다 (jq 또는 grep fallback)
- blocking이 필요하면 exit 2를 사용한다 (exit 1은 non-blocking error — 로그만 남기고 진행됨)
- 실행 가능 상태이거나 `bash` 경유로 호출된다
- write-tool `PostToolUse` matcher로 PASS verdict를 invalidation한다 (stale PASS 방지)

### 10.1 hook 역할 (v3)

- 기본 task bootstrap: task 폴더 생성 시 REQUEST.md / TASK_STATE.yaml을 준비한다. 필요하면 `task_created_gate.py`를 수동 bootstrap helper로만 사용한다 (기본 hooks.json에는 연결하지 않음).
- `SubagentStop`:
  - developer: PLAN.md, CRITIC__plan.md, TASK_STATE.yaml, HANDOFF.md 필요
  - writer: TASK_STATE.yaml, DOC_SYNC.md (durable docs 변경 시) 필요
- `TaskCompleted`:
  - TASK_STATE.yaml 필수
  - status: blocked_env이면 종료 거부
  - CRITIC__plan.md PASS 필수
  - repo-mutating: CRITIC__runtime.md PASS + DOC_SYNC.md 필수
  - DOC_SYNC.md 있으면: CRITIC__document.md PASS 필수
  - RESULT.md 필수
- `PostCompact`: maintenance queue 갱신. root index/archive 변경 시 critic-document follow-up 기록.
- `SessionEnd`: TASK_STATE.yaml 기반 스캔. blocked_env task 구분 기록. 미해결 INF, 미완 DOC_SYNC 기록.

---

## 11. executable QA scaffolding

setup이 생성하는 `scripts/harness/`:

| Script | Purpose |
|--------|---------|
| `verify.py` | Main entry point — runs smoke + healthcheck in sequence |
| `smoke.sh` | Project-specific smoke tests (tests, curl, CLI commands) |
| `healthcheck.sh` | Service health probes |
| `reset-db.sh` | Database reset / seed |

Project-shape guidance:
- **web app**: browser-first smoke + route probes + persistence checks
- **api**: curl/http smoke + DB or side-effect checks
- **cli/worker**: example commands + log/output checks
- **library**: tests/examples + minimal reproducible command

---

## 12. optional architecture constraints

`doc/harness/constraints/`에 둔다. repo shape이 machine constraints를 필요로 할 때만 생성.

- `architecture.md` — 인간이 읽는 architecture 규칙
- `check-architecture.sh` — 기계가 실행하는 검증 스크립트

critic-runtime이 optional로 실행할 수 있다.

---

## 13. `/harness:setup`이 해야 하는 일

1. repo census (doc/harness/manifest.yaml 확인)
2. safe observation (비파괴 실행만)
3. minimal questions (최대 5개)
4. bootstrap generation
   - root CLAUDE.md, doc/common/*, doc/harness/manifest.yaml
   - .claude/settings.json
   - doc/harness/critics/{plan,runtime,document}.md
   - scripts/harness/{verify,smoke,healthcheck,reset-db}.sh
   (hook scripts는 plugin 내장 — target project에 복사 불필요)
5. initial notes (REQ, OBS, INF)
6. critic playbook generation
7. executable QA scaffolding (project shape 기반)
8. optional architecture constraints
9. obvious root candidates → critic-document 승인
10. reviewable diff → user 확인 후 반영
11. .gitignore 설정
12. CLAUDE.md에 `## Harness routing` 블록 주입 (마커: `<!-- harness:routing-injected -->`)

### 13.1 setup 산출물 최소 집합

반드시 생성:
- `CLAUDE.md` (root entrypoint + registry)
- `doc/common/CLAUDE.md`
- 최소 1개씩의 `REQ__`, `OBS__`, `INF__`
- `doc/harness/manifest.yaml`
- `doc/harness/critics/{plan,runtime,document}.md`
- `.claude/settings.json`
- `scripts/harness/{verify,smoke,healthcheck,reset-db}.sh`

Hook scripts (task gates, session sync 등)은 plugin에 내장되어 있으므로 target project에 복사 불필요.

선택 생성:
- 추가 root
- root-local critic overlay
- `doc/harness/constraints/{architecture.md,check-architecture.sh}`

---

## 14. 왜 이 구조가 단순한가

- 중앙 orchestrator graph 없음
- root는 registry + note 조합만 강제
- 문서군을 미리 타입으로 강제하지 않음
- critic은 3종으로 고정 (plan, runtime, document)
- project custom은 critic playbook/overlay로 흡수
- 운영 부산물과 durable memory를 물리적으로 분리
- 자동 정리는 기계적 정리와 의미적 정리를 분리
- TASK_STATE.yaml로 task lifecycle을 machine-readable하게 추적
- executable QA scaffolding으로 verification이 실행 가능한 상태를 보장
- BLOCKED_ENV는 task를 열린 상태로 유지해서 blocker가 무시되지 않음

즉, 복잡도는 "상시 런타임"이 아니라 `setup`, `critic`, `maintenance`에만 몰아넣고, 평상시 작업은 `harness -> plan -> worker -> critic`의 짧은 루프로 유지한다.
