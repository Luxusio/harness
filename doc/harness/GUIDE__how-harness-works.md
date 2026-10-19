---
tags: [harness, guide, architecture, lifecycle, onboarding]
summary: 하네스 전체 동작 안내 — 과제 수명주기, MCP 제어면, 훅과 prewrite gate, 영수증과 마감 게이트, 스킬 단계, 에이전트와 lens, Goal, batch 병렬 모드, 설치·setup·manifest, 계약, 메모리·학습, Claude/Codex 차이.
updated: 2026-10-19
freshness: current
invalidated_by_paths:
  - .claude-plugin/marketplace.json
  - .claude/settings.json
  - .codex-plugin/marketplace.json
  - .github/workflows/
  - AGENTS.md
  - CLAUDE.md
  - CONTRACTS.md
  - CONTRIBUTING.md
  - doc/common/GUIDE__mcp-tool-naming.md
  - doc/common/GUIDE__runbook-memory.md
  - doc/common/REQ__process__receipt-watcher-fail-closed.md
  - doc/common/REQ__process__subagent-lifecycle-cleanup.md
  - doc/common/REQ__process__subagent-receipt-binding.md
  - doc/designs/minimal-implementer-and-code-review-gate.md
  - doc/harness/ADR__remove-hygiene-subsystem.md
  - doc/harness/AUTO_ROUTING.md
  - doc/harness/IMPORT_LIST.md
  - doc/harness/REQ__bytecode-cache-cannot-disable-receipts.md
  - doc/harness/REQ__contract-enforcement-claims-are-executable.md
  - doc/harness/REQ__gate-does-not-demand-impossible-evidence.md
  - doc/harness/REQ__guards-are-verified-where-they-run.md
  - doc/harness/REQ__harness-announces-lost-receipt-capability.md
  - doc/harness/REQ__installed-tree-modes-are-installer-owned.md
  - doc/harness/REQ__lens-verdict-contract-ownership.md
  - doc/harness/REQ__lens-verdicts-bind-when-the-lens-complied.md
  - doc/harness/REQ__mutation-scope-follows-the-diff.md
  - doc/harness/REQ__parallel-tasks-via-worktree-leads.md
  - doc/harness/REQ__qa-notes-carry-their-own-invalidation.md
  - doc/harness/REQ__receipt-capability-diagnosis.md
  - doc/harness/REQ__receipt-subsystem-failures-are-observable.md
  - doc/harness/REQ__req-capture-with-or-without-task.md
  - doc/harness/REQ__runtime-normative-text-has-one-source.md
  - doc/harness/REQ__runtime-surfaces-name-the-actual-blocker.md
  - doc/harness/REQ__selective-review-detail.md
  - doc/harness/REQ__session-start-hooks-no-op-outside-harness.md
  - doc/harness/REQ__subagent-completion-receipt-transcript-shape.md
  - doc/harness/REQ__subagent-lifecycle-receipt-boundaries.md
  - doc/harness/REQ__subagent-receipt-session-binding.md
  - doc/harness/REQ__task-blocked-is-the-park-record.md
  - doc/harness/REQ__test-suite-determinism-under-xdist.md
  - doc/harness/REQ__unbound-verdict-names-the-spawn-shape.md
  - doc/harness/REQ__unreadable-worker-state-is-not-recordable.md
  - doc/harness/REQ__verdict-binding-survives-output-framing.md
  - doc/harness/REQ__versioned-project-file-migrations.md
  - doc/harness/SPEC.md
  - doc/harness/apply-patch-matrix.md
  - doc/harness/codex-payload-deltas.md
  - doc/harness/codex-troubleshooting.md
  - doc/harness/manifest.yaml
  - doc/harness/patterns/
  - doc/harness/qa/QA_KNOWLEDGE.yaml
  - doc/harness/runbooks.yaml
  - doc/harness/runtime-matrix.md
  - doc/harness/runtime-services.md
  - install.py
  - plugin-codex/
  - plugin/
  - pyproject.toml
  - tests/
  - README.md
  - doc/CLAUDE.md
  - doc/harness/critics/
  - doc/harness/review-overlays/
freshness_updated: 2026-10-19T06:06:13Z
---

# GUIDE — 하네스는 어떻게 동작하는가

이 문서는 harness 플러그인이 실제로 어떻게 동작하는지 한곳에 모아 설명한다. 대상은 저장소 소유자와 기여자다.

이 문서는 해설서이고 규범 문서가 아니다. 반드시 지켜야 할 것은 `CONTRACTS.md`, `plugin/CLAUDE.md`, 각 REQ/ADR 문서가 정한다. 이 문서는 그 규범이 코드에서 어떻게 구현되는지 따라간다. 규범 문서의 문구는 길게 옮기지 않고 링크로 대신한다(§17).

읽는 법:

- 문장 안이나 소절 끝의 `path:line`이 근거 위치다. 기존 줄 번호는 2026-09-28 기준의 탐색 힌트다. WBS 제어면 설명은 2026-10-19의 정규 소스를 다시 확인했다. frontmatter의 `invalidated_by_paths`에 있는 경로가 바뀌면 이 문서를 `suspect`로 취급한다(§13.7). 디렉터리 접두사로도 매칭된다.
- 코드와 산문 문서가 서로 다르면 **실제 동작은 코드를 따른다.** 작성 중 발견한 불일치는 해당 절에 적고 §7.9와 §17.2에 모았다.
- "코드에서 도출, 테스트 없음"이라고 표시한 항목은 코드 경로를 읽고 얻은 결론이고, 끝까지 실행해 보지는 않았다.

---

## 1. 한눈에 보기

### 1.1 한 페이지 요약

하네스는 저장소를 바꾸는 작업에 세 가지를 강제한다.

1. **과제로 묶는다.** repo-mutating 작업은 모두 `doc/harness/tasks/TASK__<id>/` 디렉터리를 가진 과제(task)가 된다. 과제의 제어 상태(`TASK.json`, `PLAN.md`, WBS.json, 활성 마커, 명시적 마이그레이션 자료)는 Write/Edit 계열 도구로 쓸 수 없다(prewrite gate). `_lib`의 writer 함수는 바인딩된 MCP 핸들러만 호출할 수 있다. 마커는 예외적으로 Codex 등록 훅도 쓸 수 있다. 셸 쓰기는 가로채지 않는다(C-05, `CONTRACTS.md:102-114`).
2. **계획이 먼저다.** 활성 과제에 `PLAN.md`가 없으면 PreToolUse 훅 `prewrite_gate.py`가 소스 파일 Write/Edit를 거부한다(C-02).
3. **독립 증거가 있어야 닫힌다.** 과제를 닫으려면 리뷰 lens와 QA lens 서브에이전트가 실제로 시작하고 끝났다는 기록이 `RECEIPTS.jsonl`에 있어야 한다. 이 파일은 Write/Edit로 쓸 수 없다. `record()`도 allowlist에 있는 호출자(Claude 라이프사이클 훅, Codex watcher)만 부를 수 있다. 다만 셸로 append하는 것은 막지 않는다. 리뷰가 PASS한 **뒤에** 시작한 QA가 PASS해야 `runtime_verdict: PASS`가 되고, 그때만 `wbs(action="close")`가 과제를 닫는다(C-04, C-14).

**왜 이렇게까지 하는가.** 코디네이터가 스스로 적은 PASS는 환각과 구별할 수 없다(`CONTRACTS.md` C-14 Why). 그래서 PASS의 근거를 "훅이 관찰한 서브에이전트의 시작과 종료"에 묶는다. 반면 영수증은 Git이나 소스 상태에 묶이지 않는다. 리뷰·QA 뒤에 코드를 다시 고쳤다면 해당 lens를 다시 돌리는 것은 개발자 책임이다(`CONTRACTS.md` C-04/C-14, `plugin/skills/develop/quality-audit-pipeline.md:297-299`).

구성 요소는 세 층이다.

| 층 | 역할 | 대표 파일 | 강제 방식 |
|---|---|---|---|
| 스킬 | 단계별 절차(run, plan, develop, batch, setup) | `plugin/skills/*/SKILL.md` | 프롬프트. 모델이 따르며 기계 강제는 없다 |
| MCP 제어면 | WBS 실행 상태의 정규 writer이자 verdict 계산기 | `plugin/mcp/harness_server.py`, `plugin/scripts/_lib.py` | 조건이 안 맞으면 호출 거부 |
| 훅 | 쓰기 차단, 영수증 기록, 컨텍스트 주입 | `plugin/hooks/hooks.json`, `prewrite_gate.py`, `background_hook.py`, `prompt_memory.py` | PreToolUse deny, 영수증 append |

### 1.2 요청이 하네스에 들어오는 경로

```
 Claude                                        Codex
 ──────                                        ─────
 사용자 프롬프트                                  사용자 프롬프트
   │ (run/plan/develop은 user-invocable:false)      │ UserPromptSubmit: 매번 [harness-route] 주입
   ▼                                              ▼
 프롬프트 문구만으로 유도:                          $harness:run (openai.yaml
   - root CLAUDE.md routing 블록                   allow_implicit_invocation: true)
   - SessionStart 배너 "Auto-routing on."              │
   - prompt_memory [harness-goal] 힌트                  ▼
     (활성 과제가 없을 때만. 이 저장소에서는            internal-skills/run
      400자 상한에 잘림)
   │
   ▼
 모델이 스스로 Skill(harness:run) 호출
   │ 호출하지 않으면 ─▶ 유일한 기계 장치: prewrite no-active-task (§5.4)
```

- Claude 플러그인에서 run, plan, develop, plan-*는 모두 `user-invocable: false`다. 사용자가 slash 명령으로 부를 수 있는 것은 setup과 batch뿐이다(`plugin/skills/run/SKILL.md:5`, `plugin/skills/setup/SKILL.md:9`, `plugin/skills/batch/SKILL.md:5`, `tests/test_skill_visibility.py:9-60`). `CONTRIBUTING.md`도 일반 요청에서 Harness로 자동 라우팅되는 절차를 안내한다.
- 요청을 run으로 보내는 훅은 없다. 모델이 볼 수 있는 것은 프롬프트 문구뿐이다. root CLAUDE.md의 routing 블록, SessionStart 배너의 "Auto-routing on. Just describe what you want."(`plugin/hooks/hooks.json:8`), prompt_memory가 붙이는 `[harness-goal] … plain mutating request? wbs(open)` 힌트가 그것이다. 마지막 힌트는 활성 과제가 없을 때만 붙는다(`prompt_memory.py:73-108`).
- 이 저장소에서는 그 goal 힌트가 사실상 전달되지 않는다. DOC_GATE(135자)와 승인된 runbook 블록(396자, 이 저장소에서 측정) 뒤에 붙기 때문에, 힌트는 약 533자 지점에서 시작하고 400자 상한에 잘린다(`prompt_memory.py:48-53, 238-268`, §4.3).
- 모델이 run을 호출하지 않았을 때 작동하는 기계 장치는 prewrite `no-active-task` 하나다. 이 장치는 `SOURCE_EXTENSIONS`에만 적용되고, 최상위 strict 키가 있거나 다른 open/invalid 과제가 있을 때만 발동한다(§5.4).
- Codex는 매 프롬프트에 `[harness-route] Repository mutation: invoke $harness:run before edits`를 주입한다. `plugin-codex/skills/run/agents/openai.yaml:5-6`은 암시적 호출을 허용한다.

### 1.3 정규 루프

공개 수명주기는 `WBS open → plan → develop → QA → close`이다. 독립 리뷰와 `wbs(action="verify")`는 공개 단계가 아니라 close 직전의 필수 내부 게이트다(`plugin/CLAUDE.md:12-19`, C-01).

```
 사용자 요청 (repo-mutating)
   │
   ▼
 [run skill] Phase 0: 활성 과제 확인 ──(있음)──▶ 재개 지점으로 이동
   │ (없음)
   ▼
 WBS open ──▶ TASK.json(4필드) + .active_sessions/<sid>.json + .active
   │
   ▼
 [plan skill] compact 또는 full ──▶ WBS plan ──▶ PLAN.md + required_lenses
   │
   ▼
 [develop skill] 구현 ◀── prewrite gate가 Write/Edit마다 검사
   │  (lane 표, ac-worker, PROGRESS.md, 테스트, 커밋)
   ▼
 Phase 6.6 리뷰: [defect-hunter 0~2 (+security-reviewer, 독립)] → code-reviewer
   │    SubagentStart/Stop 훅 ──▶ RECEIPTS.jsonl  started / completed
   │    리뷰 원문 ──▶ REVIEWS.jsonl
   ▼ (모든 review lens PASS 이후)
 Phase 7 QA: 선언된 qa-* 전부를 한 메시지로 스폰 ──▶ RECEIPTS.jsonl
   │
   ▼
 WBS verify ──▶ runtime_verdict  PASS / FAIL / BLOCKED_ENV / PENDING
   │ PASS
   ▼
 (harness 소스 저장소만) install_verified.py  — Phase 7.8
   │
   ▼
 WBS close ──▶ TASK.json.close_receipt_fingerprint 기록, 마커 삭제
   │
   ▼
 self-improvement: learnings.jsonl, promote_learnings.py(보고만), retro, 커밋
```

실제 환경 blocker가 있거나, review/QA가 실제로 `BLOCKED_ENV`를 냈거나, 실제 review PASS와 QA PASS 뒤 `wbs(action="verify")`를 1회 불렀는데도 필수 영수증이 없어 PASS가 되지 않으면 `wbs(action="block")`로 과제를 **주차(park)** 한다. 실제 FAIL은 주차하지 않고 수정한다. 난이도, 시간 압박, retry 소진은 주차 사유가 아니다. 주차는 완료가 아니다. 과제는 `blocked` 상태로 남고, 나중에 기본 `wbs(action="open")`로 재개한다(C-17 Parking clause).

### 1.4 용어집

| 용어 | 뜻 | 위치/근거 |
|---|---|---|
| task(과제) | repo-mutating 작업 한 단위. ID는 `TASK__[A-Za-z0-9_.-]{1,180}` | `doc/harness/tasks/TASK__<id>/`, `_lib.py:2015-2096` |
| task dir | 과제 디렉터리. `doc/harness/tasks`의 바로 아래 자식이어야 하고 symlink이면 안 된다 | `_lib.py:2015-2096` |
| TASK.json | 정확히 네 필드(`run_id`, `execution_mode`, `required_lenses`, `close_receipt_fingerprint`)만 가진 제어 파일 | `_lib.py:43-45, 1366-1391` |
| run / `run_id` | 과제의 "증거 세대". UUIDv7이고, 여기에 들어 있는 ms 타임스탬프가 run 시작 cutoff다. `fresh_run=true`일 때만 바뀐다 | `_lib.py:1325-1363, 1709-1724` |
| lens | 영수증을 남기는 검증 관점. `review-code`, `review-security`, `qa-api`, `qa-browser`, `qa-cli`, `qa-desktop` 여섯 개뿐이다 | `_lib.py:46-52` |
| required_lenses | 이 과제를 닫는 데 필요한 lens 목록. `review-code`와 `qa-*` 하나 이상이 반드시 들어간다 | `_lib.py:1311-1322` |
| receipt(영수증) | `RECEIPTS.jsonl`의 한 줄. `started` 또는 `completed` | `_lib.py:3067-3159` |
| REVIEWS.jsonl | review lens 완료 원문을 sha256으로 주소화해 저장하는 파일. verdict 계산에는 쓰이지 않는다 | `_lib.py:2774-3064` |
| review_verdict / runtime_verdict | 영수증에서 매번 계산하는 판정. 저장되지 않는다 | `_lib.py:4521-4633` |
| missing_for_close | close를 막는 항목 목록(PLAN.md, review PASS, QA PASS) | `_lib.py:4843-4968` |
| status | `open` / `blocked` / `closed` / `invalid`. 저장값이 아니라 파일 상태에서 파생된다 | `_lib.py:3878-3908` |
| fingerprint | 파일 이름과 원시 바이트(파일이 없으면 `<missing>` 표지)를 도메인 분리해 해시한 `sha256:` 값. `sha256sum RECEIPTS.jsonl`과 다르다. 계산하면서 모든 행을 검증한다. close 시점 값이 TASK.json에 기록된다 | `_lib.py:3369-3412, 3911-3920` |
| focus(write focus) | 세션 하나가 쓰기 권한을 가진 열린 과제 하나(C-09) | `harness_server.py:1612-1646` |
| session marker | `doc/harness/tasks/.active_sessions/<sid>.json`. 영수증을 묶는 유일한 권위 | `_lib.py:2102-2114, 2389-2411` |
| legacy `.active` | `doc/harness/tasks/.active`. task_dir 문자열 하나만 담은 공유 마커(MCP가 쓰면 절대경로) | `_lib.py:2105-2106, 2267-2268, 2343-2350` |
| session hint | `.active_sessions/.session-hint`. UserPromptSubmit 훅(Claude는 `prompt_memory.py`, Codex는 `hook_user_prompt_submit.py` → `prompt_memory.py`)이 매 프롬프트마다 쓰는 세션 ID. 읽는 쪽은 Claude(및 generic) 런타임 MCP의 세션 식별(`_current_session_identity`), 실제 세션 ID가 `default`일 때 대체값으로 쓰는 `install_verified.py`, 그리고 `CODEX_THREAD_ID`가 없을 때 대체값으로 쓰는 `hook_tree_health.py`의 Codex 등록 확인이다 | `_lib.py:2117-2162`, `prompt_memory.py:222-235`, `hook_user_prompt_submit.py:63-74`, `harness_server.py:203-215` |
| Native Goal | objective와 지속 실행 문맥; 일반 요청과 같은 WBS를 사용 | §9 |
| coordinator / lead | batch에서 main checkout에 있는 세션이 coordinator, worktree 하나를 맡는 `harness:task-lead` 서브에이전트가 lead | `plugin/skills/batch/SKILL.md`, `plugin/agents/task-lead.md` |
| workspace | task 도구에 넘기는 linked git worktree 절대 경로. 생략하면 main checkout | `harness_server.py:1057-1087` |
| control root / task root | MCP 프로세스가 속한 checkout / 과제가 실제로 있는 checkout | 같은 곳 |
| MAINTENANCE | 과제 디렉터리 안의 마커 파일. plan-first 면제, workflow-control 파일 쓰기 허용, `maintenance_task` 라우팅을 켠다. REQ 규칙(§5.5)은 면제하지 않는다 | `prewrite_gate.py:733-749, 783-803`, `_lib.py:2518-2550` |
| standard / micro | 기존 제어 기록의 `execution_mode`; 공개 open은 standard로 생성한다. 기존 micro는 **PLAN.md 요구만** 없앤다. 리뷰·QA 영수증은 그대로 필요하다 | `_lib.py:4857-4864, 1311-1322` |
| fresh_run | 기존 과제의 run을 교체하고 영수증을 버리는 `wbs(action="open")` 옵션 | `harness_server.py:1396-1423` |
| park / BLOCKED.md | `wbs(action="block")`로 남기는 미완료 기록. 이 상태에서 runtime_verdict는 `BLOCKED_ENV` | `harness_server.py:1856-1918` |
| fixed pair | 필수 lens가 실제로 돌아 결과를 낸 뒤(실제 QA PASS 후 `wbs(action="verify")` 1회)에도 필수 영수증이 없을 때, 그 `wbs(action="verify")` 응답의 `next_action`에서 그대로 복사해 `wbs(action="block")`에 넣는 고정 문구 두 쌍. 이 run에 영수증이 하나도 없으면 empty-stream 쌍, 일부 있으면 missing-attestation 쌍이다. `doc/harness/.receipt-capability-broken`이 있으면 어느 쪽도 쓰지 않는다. `_lib.py`에만 있다 | `_lib.py:53-59, 152-202` |
| non-attesting | 영수증을 만들지 않는 역할이나 결과(developer, defect-hunter, ux-*, dogfooder 등) | `subagent_lifecycle.py:147-172` |
| LIGHT / STANDARD / DEEP | Phase 6.6 review 깊이. defect-hunter 수가 각각 0/1/2다 | `quality-audit-pipeline.md:40-100` |
| manifest | `doc/harness/manifest.yaml`. 저장소가 harness를 쓰는지 판단하는 표지(`version: 7`) | `_lib.py:2004-2012`, `setup_finalize.py:29-40` |

---

## 2. 저장소와 런타임 배치

### 2.1 소스 트리

```
/ (저장소 루트)
├── CLAUDE.md, AGENTS.md      프로젝트 문서 (Claude / Codex). 둘 다 @CONTRACTS.md import
├── CONTRACTS.md              계약 C-01..C-18 (managed block)
├── install.py                설치기 (Claude·Codex 런타임 트리 동기화)
├── pyproject.toml            Python ≥3.12, dev 의존성, pytest addopts (§2.4)
├── .claude-plugin/marketplace.json   Claude 마켓플레이스 정의(source ./plugin). 설치 시 mirror로 복사
├── .codex-plugin/marketplace.json    root Codex 마켓플레이스 정의 (version 2.3.0-codex)
├── .claude/settings.json     이 저장소의 Claude 설정 (아래 설명)
├── .github/workflows/tests.yml   CI (§2.4)
├── plugin/                   Claude 런타임 payload (= Codex 공용 스크립트 원본)
│   ├── .claude-plugin/plugin.json   version 2.3.0 (설치 시 +h<sha8> 부여)
│   ├── .mcp.json             플러그인 MCP 서버 정의 (${CLAUDE_PLUGIN_ROOT}/mcp/harness_server.py)
│   ├── CLAUDE.md             런타임 규칙 (스킬이 인용하는 권위 문서)
│   ├── agents/               17개 에이전트 정의
│   ├── hooks/hooks.json      Claude 훅 등록 (6개 이벤트)
│   ├── mcp/harness_server.py MCP 제어면 (도구 11개)
│   ├── scripts/              _lib.py, prewrite_gate.py, subagent_lifecycle.py, …
│   └── skills/               run, plan, develop, batch, setup, plan-*-review
│                             (goal-queue/는 SKILL.md 없는 빈 디렉터리. Git이 추적하지 않는 로컬 잔재)
├── plugin-codex/             Codex payload의 일부 (전체 조립은 §2.2)
│   ├── .codex-plugin/plugin.json    수동 관리 version (2.3.0+codex.20260929030000)
│   ├── .codex-version        요구 codex CLI 최소 버전 (0.130.0)
│   ├── skills/{run,setup}    공개 진입 스킬 (run은 internal-skills/run을 여는 20줄 래퍼)
│   ├── internal-skills/      batch, develop, plan, plan-*-review, run 의 SKILL.md (goal-queue/는 빈 잔재)
│   └── agents/               16개 (task-lead 포함, ac-worker 없음) — 방법론 참고용
├── doc/                      영속 지식 (doc/CLAUDE.md 가 registry)
│   ├── common/               공용 REQ/GUIDE
│   └── harness/              manifest.yaml, REQ__*, ADR__*, patterns/, tasks/, goals/,
│                             learnings.jsonl, runbooks.yaml, checkpoints/, retros/, archive/batch/
└── tests/                    pytest. hooks.json 형태, 계약 lint, 에이전트 계약, batch 등을 고정 (§2.4)
```

근거: `plugin-codex/skills/run/SKILL.md:6-20`, `install.py:990-1045, 1093-1094`, `plugin/.claude-plugin/plugin.json:3`, `plugin-codex/.codex-plugin/plugin.json:3`, `plugin-codex/.codex-version:20`, `.claude-plugin/marketplace.json:10-12`, `tests/test_codex_public_run_skill.py:105-106`.

- **`plugin-codex/`만으로는 Codex payload가 완성되지 않는다.** 설치기는 여기에 `plugin/scripts`, `plugin/mcp`, `plugin/skills`의 공용 보조 문서(경로 치환), setup 보조 파일, 생성한 hooks.json/.mcp.json을 합친다. 따라서 `plugin/skills/develop/*.md` 같은 공용 보조 문서를 고치면 Codex 동작도 바뀐다(§2.2).
- **`.claude/settings.json`의 현재 상태**: main-thread agent로 `harness:harness`를 지정한다. 그러나 이 에이전트는 삭제됐다(`plugin/CHANGELOG.md:100`, `plugin/CLAUDE.md:9-10`). 허용 목록에는 존재하지 않는 `Skill(harness:maintain)`이 있다. agent teams 실험 플래그가 켜져 있고, worktree base ref는 `head`다(`.claude/settings.json:3, 8-9, 13-18`). 이 상태가 C-07/C-08에 주는 영향은 §12.2에 있다.

`doc/harness/tasks/`, `learnings.jsonl`, `archive/` 등 운영 파일은 gitignore 대상이다(§11.6). 과제 증거는 커밋되지 않는다. 오래 남겨야 할 교훈은 REQ/GUIDE/ADR/pattern 문서나 테스트로 승격해야 한다(§13).

**version 문자열 다섯 가지.** 서로 다르고, 각각 쓰이는 곳이 다르다.

| 위치 | 값 | 비고 |
|---|---|---|
| `plugin/.claude-plugin/plugin.json` | `2.3.0` | 설치된 사본은 `2.3.0+h<sha8>`(§2.3) |
| `plugin-codex/.codex-plugin/plugin.json` | `2.3.0+codex.20260929030000` | 손으로 관리. Codex cache 디렉터리 이름 |
| root `.codex-plugin/marketplace.json` | `2.3.0-codex` | `:13` |
| 설치기가 생성하는 Codex marketplace | `2.3.0` | `install.py:1047-1062` |
| MCP `serverInfo.version` | `2.0.0` | `harness_server.py:26-27` |

### 2.2 설치된 트리

`install.py`는 네 트리 중 세 개(1, 3, 4)를 직접 쓴다. Claude plugin cache(2)는 install.py가 호출하는 `claude plugin install` / `claude plugin update harness@harness`가 mirror의 marketplace에서 채운다. install.py는 이 밖에 `~/.codex/config.toml`과 Codex hook trust 상태도 쓴다(`install.py:54-64, 1093-1149, 1407-1514, 1936-1941`).

| # | 트리 | 기본 위치 | 누가 쓰나 | 내용 |
|---|---|---|---|---|
| 1 | Claude mirror | `~/.claude/harness-dev` (`HARNESS_DEST`로 변경 가능) | install.py | `.claude-plugin/` + `plugin/`. 사용자 수준 MCP 서버가 여기서 뜬다 |
| 2 | Claude plugin cache | `<config>/plugins/cache/harness/harness/<version>` | Claude Code CLI | Claude Code가 **실제로 훅을 실행하는** 사본 |
| 3 | Codex mirror | `~/.codex/harness/plugins/harness` + `~/.codex/harness/.agents/plugins/marketplace.json` | install.py | Codex 마켓플레이스 원본. **Codex MCP 서버가 여기서 뜬다**(config.toml `[mcp_servers.harness]`) |
| 4 | Codex plugin cache | `<codex_home>/plugins/cache/harness/harness/<version>` | install.py | Codex 훅이 실행되는 사본 |

**Codex payload 조립.** `_build_codex_payload`는 다음 순서로 payload를 만든다(`install.py:990-1045`).

1. `plugin-codex/`를 복사한다.
2. `plugin/scripts`와 `plugin/mcp`를 복사한다.
3. `plugin/skills`의 공용 보조 문서를 `internal-skills/`로 복사한다. 복사하면서 `${CLAUDE_PLUGIN_ROOT}/skills/` → `${HARNESS_PLUGIN_ROOT}/internal-skills/`, `${CLAUDE_PLUGIN_ROOT}` → `${HARNESS_PLUGIN_ROOT}`, `plugin-codex/agents/` → `${HARNESS_PLUGIN_ROOT}/agents/`로 치환한다. 대상 파일은 다음과 같다.
   - develop 6개: fix-first-pattern, runtime-smoke, quality-audit-pipeline, verification-gate, test-failure-triage, hypothesis-driven-debugging
   - plan 4개: decision-principles, intake, review-phases, write-artifacts
   - plan-devex-review의 dx-hall-of-fame, plan-eng-review의 rubrics-threat-rollback, run의 self-improvement
4. setup의 repo-census, project-interview, bootstrap, verify-report와 `templates/`를 `skills/setup/`로 복사한다.
5. `hooks.json`과 `.mcp.json`을 생성한다.
6. 필수 파일 8개 중 하나라도 없으면 `incomplete Codex payload: <rel>`로 실패한다.

유지보수할 때 알아 둘 점:

- 공용 보조 문서는 `plugin/skills`가 원본이다. 고치면 Codex에도 반영된다.
- SKILL.md(develop/plan/run 등)는 Codex 쪽이 손으로 관리하는 별도 포트다. 두 쪽을 따로 고쳐야 하고, 따로 어긋날 수 있다. 예를 들어 Codex develop에는 `## Model Routing`, `Phase 9`, qa_codifier 단계가 있지만 Claude 쪽에는 없다.
- `parallel-fanout.md`와 `browser-verification.md`는 Codex로 복사되지 않는다.

### 2.3 실제로 실행되는 사본

- **Claude 훅**은 plugin cache(2)에서 실행된다. `claude plugin update`는 version 문자열이 바뀔 때만 cache를 갱신한다. 그래서 설치기는 복사한 `plugin.json`의 version만 `<base>+h<sha8>`(payload 내용 해시)로 바꾸고, 소스의 `plugin.json`은 `2.3.0` 그대로 둔다. 2026-09-25에 같은 version으로 설치했더니 옛 사본이 계속 실행되는 사고가 있었고, 이 방식은 그 뒤에 도입됐다(`install.py:1105-1130`).
- **Claude MCP 서버**는 두 경로로 뜰 수 있다.
  - 플러그인 경로: `plugin/.mcp.json`이 cache 안의 `${CLAUDE_PLUGIN_ROOT}/mcp/harness_server.py`를 띄운다. 도구 이름은 `mcp__plugin_harness_harness__<tool>`.
  - 사용자 수준 경로: 설치기가 `claude mcp add harness`로 mirror의 `plugin/mcp/harness_server.py`를 등록한다. 이때 `HARNESS_PLUGIN_ROOT`, `CLAUDE_PLUGIN_ROOT`, `HARNESS_RUNTIME=claude`, `PYTHONDONTWRITEBYTECODE=1`을 넘긴다(`install.py:1811, 1953-1973`). 도구 이름은 `mcp__harness__<tool>`.

  이름 차이 때문에 생기는 "No such tool available" 문제는 `doc/common/GUIDE__mcp-tool-naming.md`에 정리되어 있다.
- **Codex 훅**은 plugin cache(4)에서 실행된다. cache를 설치할 때 hooks.json을 cache 경로 기준으로 다시 생성한다(`install.py:1083-1091, 1151-1158`). cache 디렉터리 이름은 내용 해시가 아니라 손으로 관리하는 version이다. 이전 version 디렉터리는 일부러 남겨 둔다. 실행 중인 Codex 세션이 절대 경로로 훅을 참조하기 때문이다(`install.py:1474-1514`).
- **Codex MCP 서버**(task 도구와 receipt watcher 포함)는 config.toml `[mcp_servers.harness]`가 가리키는 Codex mirror(3)의 `mcp/harness_server.py`에서 뜬다. env는 `HARNESS_RUNTIME=codex`, `PYTHONDONTWRITEBYTECODE=1`이다(`install.py:567-584, 1704-1705, 1752-1764`, `harness_server.py:2322-2326`). 그러므로 Codex에서도 Claude처럼 **훅 트리와 MCP 트리가 서로 다르다.**

결론적으로 **저장소의 `plugin/`을 고쳐도 설치하기 전까지는 실행 중인 하네스가 바뀌지 않는다.** 이미 떠 있는 MCP 서버와 훅은 새 세션을 열기 전까지 옛 코드일 수 있다. 이를 알려 주는 장치는 두 가지다.

- `drift_warn.py`: SessionStart에서 소스 `plugin/scripts/*.py`와 지금 실행 중인 scripts 디렉터리의 sha256을 비교한다. 경고는 소스 저장소에서만 뜬다(`drift_warn.py:69-113`).
- `hook_tree_health.py`: `wbs(action="open")`의 `RECEIPT_HOOKS_UNAVAILABLE` 경고와 `wbs(action="context")`/`wbs(action="verify")`의 `watcher_status.receipt_capability_warning`으로 나타난다(§4.9).

batch lead도 worktree의 `plugin/`이 아니라 설치된 플러그인으로 돈다(`plugin/agents/task-lead.md:63-66`).

### 2.4 테스트와 CI

- **요구 사항**: `pyproject.toml`은 Python ≥3.12와 dev 의존성(pytest, pytest-xdist, pyyaml)을 요구한다. addopts는 `-n auto --dist worksteal`이다(`pyproject.toml:1-14`).
- **실행**: manifest `test_command`는 `uv run pytest tests/ -x --tb=short`(`doc/harness/manifest.yaml:12`)다. runbook의 대안은 `mise exec -- uv run pytest`(`doc/harness/runbooks.yaml:1-13`)다.
- **conftest**: `PYTHONDONTWRITEBYTECODE`를 강제하고 실제 설치 트리 파일 제거를 감시한다. `learnings_ledger_gains_no_suite_rows` 세션 가드는 이 체크아웃의 `doc/harness/learnings.jsonl`에 테스트 행이 추가되면 실패한다. 축소·교체·재작성 뒤에도 새 행을 비교하며, writer 이름으로 라이브 훅을 판별하며 `background_hook`, `subagent_lifecycle`, `receipts`, `prompt_memory`, `tool_routing`의 행만 제외한다. 같은 writer 이름으로 테스트가 쓴 행도 제외되는 한계가 있고, 다른 writer의 실제 세션 행은 실패로 드러난다. 중첩 probe는 바깥 세션 가드에 맡긴다. ledger를 쓰는 테스트는 tmp harness root를 쓴다(`tests/conftest.py`의 `learnings_ledger_gains_no_suite_rows`, `_ledger_rows_appended`, `make_tmp_harness_root`; [결정성 REQ](REQ__test-suite-determinism-under-xdist.md)).
- **보조 스크립트**:
  - `golden_replay.py`: 스크립트 7종의 스모크 테스트(`plugin/scripts/golden_replay.py:1-24`).
  - `mutation_probe.py`: diff 범위 안에서 mutation을 탐색하고 보고만 한다. 어떤 스킬도 이것을 호출하지 않는다(`plugin/scripts/mutation_probe.py:1-31`, `doc/harness/REQ__mutation-scope-follows-the-diff.md`).
- **CI**: Python 3.12에 `python -m pip install --group dev`로 pytest·xdist·pyyaml을 설치하고 전체 suite와 packaged golden replay를 실행한다(`.github/workflows/tests.yml`).

---

## 3. 과제 상태와 MCP 도구

### 3.1 과제 디렉터리의 파일

| 파일 | 쓰는 주체 | 내용 | 보호(C-05) |
|---|---|---|---|
| `TASK.json` | MCP (`wbs(action="open")`, `wbs(action="plan")`, `wbs(action="close")`) | 네 필드 제어 정보 | 예 (owner `task-control-mcp`) |
| `REQUEST.md` | 비공개 scaffold helper 또는 명시적인 요청문 기록 | 기존 원 요청문. 공개 WBS open은 request_file을 받지 않음 | 아니오 |
| `PLAN.md` | `wbs(action="plan")` | 계획, WBS 성과, 검증 계약 | 예 (owner `plan-skill`) |
| `WBS.json` | WBS plan/check/dispatch owner | 성과 기준선, 검증 결과, work-pack 연결 | 예 (owner `task-control-mcp`) |
| `PROGRESS.md` | develop 코디네이터 | 정확히 7키: phase, current_ac, partial_ac, completed_acs, allowed_paths, test_paths, forbidden_paths | 아니오 (scope lock 입력) |
| `RECEIPTS.jsonl` | Claude 훅 / Codex watcher | 영수증 스트림 | 예 (owner `receipt-lifecycle-hook`) |
| `REVIEWS.jsonl` | 영수증 writer, `review-log` | review 원문, 파일 모드 0600 | 예 (owner `review-detail-writer`) |
| `BLOCKED.md` | `wbs(action="block")` | Blocked Reason / Unblock Condition / Resume / Blocked At | 아니오 (단, 소유자·모드 검사를 받음) |
| `MAINTENANCE` | 누구나 | 마커 파일 | 아니오 |
| `deferred-scope.md` | full plan (heredoc) | 미룬 범위 | 아니오 |
| `restore-points/pre-plan-<ts>.md` | plan Phase 0.5 | 재계획 전 PLAN.md 사본 | 아니오 |
| `audit/scope-lock-bypass.flag` | prewrite gate | 마지막으로 scope lock을 우회한 경로 하나. 영속 기록이 아니다(§5.6) | 아니오 |

과제 디렉터리 밖의 관련 파일:

- `doc/harness/tasks/.active_sessions/<sid>.json`: 세션 마커.
- `doc/harness/tasks/.active_sessions/.session-hint`: 세션 hint.
- `doc/harness/tasks/.active`: legacy 마커.
- `doc/harness/checkpoints/<TASK_ID>.md`: `write_checkpoint.py`가 쓴다. gitignore 대상.
- `doc/harness/goals/*.json`: 폐기된 입력 자료. 일반 실행에서는 해석하지 않으며 명시적 WBS migrate로만 보관·변환한다.
- `doc/harness/archive/goal-migration/**`: 원본 바이트와 재개 가능한 마이그레이션 manifest.
- `doc/harness/runtime/batches/**`: batch pool 상태와 immutable owner_task_id. 직접 수정하지 않는다.

근거: `prewrite_gate.py:74-87, 147-204`, `_lib.py:2484-2512`, `plugin/skills/develop/SKILL.md:169-188`, `plugin/skills/plan/intake.md:117-141`, `plugin/scripts/write_checkpoint.py:1-16`.

`ensure_task_scaffold`는 `TASK.json`과 (요청문이 있으면) `REQUEST.md`만 만든다. PLAN.md는 만들지 않는다(`_lib.py:2484-2512`).

### 3.2 TASK.json의 네 필드

```json
{
  "run_id": "<canonical lowercase UUIDv7>",
  "execution_mode": "standard",
  "required_lenses": ["review-code", "qa-cli"],
  "close_receipt_fingerprint": null
}
```

- 키는 정확히 이 네 개다. 다른 키가 하나라도 있으면 `_validate_task_control`이 거부한다(`_lib.py:1366-1391`).
- `run_id`: 소문자 정규형 RFC 9562 UUIDv7. `new_uuid7()`이 48비트 ms 타임스탬프와 74비트 난수로 만든다. 그 타임스탬프가 run 시작 cutoff(`task_run_started_at`)다(`_lib.py:1325-1363`).
- `execution_mode`: `standard` 또는 `micro`.
- `required_lenses`: 비어 있으면 안 되고 중복도 없어야 한다. 순서는 `LENS_ORDER`를 따르고, `review-code`와 `qa-*` 하나 이상을 반드시 포함한다. 새 과제의 기본값은 `["review-code","qa-cli"]`이다(`_lib.py:1311-1322, 1700-1706`).
- `close_receipt_fingerprint`: `null`이거나 `sha256:<64 hex>`.

`read_task_control`은 다음 조건 중 하나라도 어긋나면 **`{}`를 돌려준다(fail closed)**: 일반 파일, 현재 uid 소유, nlink 1, group/world 쓰기 불가, 16 KiB 이하, 읽는 동안 변하지 않음, JSON 키 중복 없음(`_lib.py:1394-1450`).

**권한 문제가 생기는 곳.** harness가 쓰는 TASK.json은 항상 0600이다(receipt 트랜잭션 안에서는 `os.open(…, 0o600)`, 그 밖에서는 `mkstemp`). umask는 권한 비트를 빼기만 하므로 umask 때문에 TASK.json이 invalid가 되지는 않는다. invalid를 만드는 것은 외부 chmod/chown, 하드 링크, 다른 도구나 다른 uid의 복사·편집이다. umask가 실제로 문제를 일으키는 곳은 과제 디렉터리다. `wbs(action="open")`는 과제 디렉터리를 `os.makedirs`로 만드는데, umask가 느슨하면(예: 002) 디렉터리가 group-writable이 된다. 그러면 receipt 트랜잭션 진입 단계에서 `receipt storage integrity unavailable`로 실패하고 빈 디렉터리가 남는다(`_lib.py:600-613, 1678-1697, 2578-2592`, `harness_server.py:1206-1208`).

**writer 권한은 바인딩된 프레임으로 제한된다.** 누가 무엇을 쓸 수 있는지는 대상마다 다르다(`_lib.py:1453-1463, 1597-1610`).

- **TASK.json 쓰기·교체·복원과 close**: 정규 MCP 비공개 실행 owner만 허용한다. Goal 장부 writer는 제거했으며 명시적 마이그레이션 owner가 기존 binding 변환만 수행한다.
- **활성 마커**(`write_active_marker`, binding-conflict fence, 마커 복원·삭제): 위 프레임들과, Codex `codex_hook_registration`의 `restore_watcher_registration`, `register_task_result`가 쓸 수 있다(`marker=True`, `_lib.py:2252, 2273, 2317, 2443`, `codex_hook_registration.py:443-444`).

바인딩 검사는 모듈의 정규 경로와 코드 identity까지 확인한다. 다만 이것은 `_lib` writer 함수 호출에 대한 **프로세스 내 프레임 검사**다. 셸이나 다른 프로세스가 파일을 직접 쓰는 것은 막지 않는다. `wbs(action="verify")`는 writer가 아니다(`harness_server.py:2061-2068`).

### 3.3 status는 저장되지 않고 파생된다

```
TASK.json 유효?
  └ 아니오 ─────────────────────────────────────────────▶ invalid
  └ 예
     BLOCKED.md 존재?
       └ 예: 안전한 일반 파일이고 close fingerprint 없음? ─ 예 ─▶ blocked
       │                                                  └ 아니오 ▶ invalid
       └ 아니오
          close_receipt_fingerprint 설정됨?
            └ 예: 현재 fingerprint 계산
            │      ├ 실패 (무결성 오류, 또는 이 reader가 거부한 행) ─▶ invalid
            │      └ 성공: 기록값과 같음? ─ 예 ─▶ closed
            │                             └ 아니오 ▶ invalid
            └ 아니오 ────────────────────────────────────────▶ open
```

근거: `_lib.py:3878-3908`.

알아 둘 점:

- **closed 상태는 `RECEIPTS.jsonl`의 정확한 바이트에 묶여 있다.** close 뒤에 이 파일이 한 바이트라도 바뀌면 상태가 `invalid`로 바뀐다. 그러면 기존 완료 증거가 무효이며, `wbs(action="open")`는 plain이든 fresh_run이든 거부한다(`_lib.py:3902-3907`, `harness_server.py:1295-1307`).
- **낡은 reader도 closed를 invalid로 본다.** fingerprint를 계산하는 동안 모든 행을 검증한다. 그래서 더 새로운 빌드가 쓴 행을 읽지 못하는 낡은 MCP나 훅 런타임은 바이트가 하나도 바뀌지 않았는데도 제대로 닫힌 과제를 `invalid`로 보고하고. 오류 문구는 reader가 낡았을 가능성을 언급한다(`_lib.py:3340-3366`).
- CONTRACTS C-17은 `planning`/`implementing`/`verifying` 같은 상태 이름을 쓰지만, 코드에는 `open`/`blocked`/`closed`/`invalid` 네 개뿐이다(`CONTRACTS.md:259`).

### 3.4 내부 task ID 규칙

비공개 `canonical_task_dir` helper의 규칙(`_lib.py:2015-2096`):

- `TASK__` 접두사가 없으면 붙인다.
- 형식은 `TASK__[A-Za-z0-9_.-]{1,180}`이다.
- `task_id`, `slug`, `task_dir`를 함께 주면 서로 일치해야 한다.
- `task_dir`은 정규 상대경로나 절대경로와 정확히 같아야 한다.
- 검증된 `doc/harness/tasks`의 바로 아래 자식이어야 하고 symlink이면 안 된다. 새 WBS를 열기 전 경로를 검증하므로 디렉터리가 실제로 있을 필요는 없다. 미래 Goal child를 등록하는 API는 없다.

### 3.5 MCP 도구 하나

stdio JSON-RPC 2.0은 Content-Length와 NDJSON 전송을 지원한다.
`TOOL_DEFS`, `TOOLS`, tools/list 모두 `wbs` 하나만 제공한다. 구형 별칭은
Unknown tool로 거부된다. 아래의 task handler 이름은 비공개 구현 이름이다.

| wbs action | 동작 |
|---|---|
| open / context | WBS 실행 생성·재개 / 현재 문맥 |
| plan / check / next | 성과 정의 / 선택 assertion 실행 / 의존 준비 성과 |
| dispatch | 정확한 work pack 바인딩과 부모 주차 |
| verify / close / block | 독립 영수증 검증 / 완료 / 차단 기록 |
| migrate | 구형 데이터 preview; apply=true일 때만 변환 |

공개 입력은 `action`과 같은 실행을 고르는 `wbs_id`를 기본으로 한다.
`wbs_id`는 `WBS__<safe-id>`, 내부 실행 ID인 `TASK__<safe-id>`, 또는 bare safe ID를
받아 같은 실행 디렉터리로 정규화한다. open에서 ID를 생략할 때만 objective로
안정적인 루트를 유도한다. 알려진 ID가 있으면 모든 후속 호출에서 그대로 쓴다.

| action | 공통 필드 외 허용 입력 |
|---|---|
| open | objective, fresh_run(boolean, 기본 false) |
| context / next / verify / close | 없음 |
| plan | plan(필수), required_lenses |
| check | node_id(필수), timeout(정수 1..60초, 기본 60) |
| dispatch | ownership(미완료 성과 ID별 현재 writer scope) |
| block | blocked_reason, unblock_condition(둘 다 필수) |
| migrate | apply(boolean, 기본 false); action과 apply만 허용 |

일반 action은 `workspace`로 등록된 worktree를 선택할 수 있다. migrate는 control
root에서 실행하며 wbs_id/workspace를 받지 않는다. 각 action에 허용하지 않은
필드는 거부한다. task_id/task_dir/slug, execution_mode/request_file,
run_commands/parallel/max_workers는 공개 WBS 입력이 아니다. 이들 일부가 비공개
실행 helper에 남아 있어도 직접 호출하거나 공개 호출에 추가하지 않는다.

WBS plan은 open 상태에서만 성공한다. blocked 상태이면 먼저 같은 ID로 open한다.
PLAN에는 비어 있지 않은 계획과 하나의 유효한 wbs JSON fence가 필요하다.
성과·부모·의존 관계·가중치·기존 검증의 전이 규칙을 검사하고 PLAN/TASK/WBS를
원자적으로 게시한다. 계획의 모든 산문 절을 기계가 검사하는 것은 아니다.
required_lenses를 생략하면 기존 선언을 유지하며, 명시한 집합은 검증·정렬한다.
계획을 고쳐도 run_id는 바뀌지 않는다. 이미 연결된 pack이 있으면 필요한 통합
증거 없이 그 성과 기준선을 변경할 수 없다. 진행률 변경 규칙은 §9를 따른다.

**오류 형태.** 결과가 나오는 경로에 따라 모양이 다르다(`harness_server.py:862-867, 897-920, 2168-2225`).

| 원인 | `isError` 결과에 들어가는 것 |
|---|---|
| `_ToolArgumentError` | 자체 data: `error_code`(`INVALID_ARGUMENT` / `ARGUMENT_TOO_LARGE`), `field`, `reason`, `rejected_value`, `expected`, `next_action`, details |
| 일반 `ValueError` | `field`, `rejected_value`, `expected`, `next_action`. 저장소 root 오류는 `task_storage_root`를 field로 두고 자체 next_action을 가진다 |
| `GitBindingError`(또는 code/path/invariant/next_action 속성을 가진 예외) | `error_code`, `path`, `invariant`, `next_action`. field·rejected_value·expected는 없다 |
| 그 밖의 예외 | `"<tool> failed: <e>"`만 |
| 핸들러의 거부(예: task_start의 focus 거부, closed 과제 거부) | 예외가 아니라 `_err`가 돌려주는 data: `task_dir`, `status`, `next_action` 등 |

`call_tool`은 `_invoke`를 통해 비공개 실행 owner와 같은 구조화 오류를 반환한다. 원인과 복구 안내를 함께 읽는다.

**런타임 판별.** `HARNESS_RUNTIME` 환경 변수를 먼저 본다. 없으면 `clientInfo.name`에 `codex`나 `claude`가 들어 있는지 본다. 둘 다 아니면 `generic`이다. Codex로 initialize되면 MCP 프로세스 안에서 Codex `WatcherManager`를 시작한다. 시작에 실패하면 `last_watcher_error`에 기록하고 서버는 그대로 뜬다(`harness_server.py:30-40, 2243-2258, 2322-2334`).

### 3.6 WBS open과 증거 세대

공개 open은 `wbs_id` 또는 objective를 사용한다. `fresh_run`은 boolean이어야
한다. 비공개 start helper의 이름은 `handle_task_start`지만 별도 MCP 도구는 없다.

| 기존 상태 | open 호출 | 결과 | 증거 |
|---|---|---|---|
| 실행 없음 | 기본 | standard 실행 생성, TASK.json과 마커 게시 | 새 run |
| open | 기본 | 같은 실행 재개, run_action=preserved | 유지 |
| blocked | 기본 | 같은 실행 재개, BLOCKED.md 해제 | 유지 |
| closed | 기본 | 거부; 명시적 fresh_run 선택 안내 | 유지 |
| 유효한 open/blocked/closed | fresh_run=true | 새 run_id와 비어 있는 close fingerprint | 기존 영수증 폐기 |
| invalid 또는 안전하지 않은 TASK/receipt | 어느 쪽이든 | 거부; fresh_run은 복구 권한이 아님 | 임의 재작성 없음 |
| 다른 열린 실행이 세션 focus 소유 | 어느 쪽이든 | 거부 또는 trusted binding 단계에서 fence | C-09 유지 |

open은 연결된 work pack의 통합·pool close 증거도 확인한다. 부모가 pack을
기다리는 동안 fresh_run으로 이 장벽을 우회할 수 없다. 구형 Goal 파일이 남아
있으면 일반 작업 전에 migrate preview/apply를 요구한다.

새 공개 실행은 standard다. 기존 TASK.json의 micro 값은 내부 기록 형식이며,
공개 execution_mode 옵션이나 PLAN 생략을 선택하는 새 경로가 아니다. 기존
micro 실행의 PLAN 면제는 review/QA 영수증을 면제하지 않는다. request_file도
비공개 helper 입력일 뿐 공개 intake가 아니다.

fresh_run은 의도적으로 기존 run 영수증을 버린다. 이전 run에서 시작한 agent의
completion으로 새 run을 검증할 수 없다. 영수증이 없다는 이유만으로 선택하지
않는다. 새 run의 기존 WBS assertion은 run identity가 달라져 measured credit을
주지 않는다. 과제 디렉터리나 기존 증거를 임의로 지워 복구하지 않는다.

반환에는 wbs_id, task_dir, task_id, run_id, run_action, evidence_preserved,
warnings, watcher_status, task_context와 tree/progress가 포함된다. 일부 진단은
비공개 owner의 task_* 이름을 오류 prefix로 유지할 수 있다. 호출명은 항상 wbs다.
컨텍스트 생성이 실패했다면 같은 ID로 context를 재시도한다. open/fresh_run을
반복해 세대를 바꾸거나 receipt-only lens 재실행을 하지 않는다.

### 3.7 세션, focus, 마커(C-09)

**세션 ID.** `current_session_id()`는 다음 순서로 값을 찾고, 결과를 `[A-Za-z0-9_.-]`로 정리한다(`_lib.py:921-936`).

1. hook 입력의 `session_id`/`sessionId`
2. `HARNESS_SESSION_ID`
3. `CODEX_SESSION_ID`
4. `CODEX_THREAD_ID`
5. `CLAUDE_SESSION_ID`
6. 모두 없으면 `default`

**MCP 서버가 쓰는 세션 ID.** Claude Code는 MCP 서버 환경에 세션 ID를 넘기지 않는다. 그래서 UserPromptSubmit 훅(`prompt_memory.py`)이 매 프롬프트마다 실제 세션 ID를 `.session-hint`에 쓰고, Claude(및 generic) MCP 서버는 이 hint를 읽는다. Codex MCP 서버의 세션 식별(`_current_session_identity`)은 hint를 읽지 않고 `CODEX_THREAD_ID`만 쓴다. 다만 Codex 등록 확인(`hook_tree_health._codex_registration_present`)은 `CODEX_THREAD_ID`가 없을 때 hint로 대신한다. 그런데도 hint는 Codex에서도 기록된다. Codex UserPromptSubmit 래퍼가 `session_id`를 담은 payload를 `prompt_memory.py`로 넘기고, 그 스크립트가 무조건 `write_session_hint`를 부르기 때문이다(`harness_server.py:193-215`, `_lib.py:2117-2162`, `hook_user_prompt_submit.py:63-78`, `prompt_memory.py:222-235`). hint에 `default`나 정리되지 않은 값이 들어오면 거부한다.

**마커 두 종류.**

- 세션 마커 `.active_sessions/<sid>.json`: `{session_id, task_dir, task_id, run_id, updated}`. `write_active_marker`가 원자적으로 쓴다.
- legacy `.active`: task_dir 문자열. `publish_legacy=True`(기본)면 세션 마커와 함께 쓴다(`_lib.py:2245-2268`).

**활성 과제 해석.** `resolve_active_task_dir`는 이 세션의 마커를 먼저 본다(열린 과제이고 task_id가 일치해야 한다). 없으면 legacy `.active`로 넘어간다. legacy 쪽은 `require_live_state=False`라서 열려 있지 않은 대상도 돌려준다. run_id도 비교하지 않는다(`_lib.py:2352-2386`).

**영수증 바인딩.** `resolve_session_task_binding`은 `default`와 정리되지 않은 세션 ID를 거부한다. `marker.run_id == TASK.json.run_id`이고 status가 `open`이어야 한다. **영수증 권위는 세션 마커에서만 나온다**(`_lib.py:2389-2411`).

**write focus(C-09).** `_session_resumes`는 focus가 비어 있거나, 열려 있지 않거나, 지금 시작하려는 과제와 같을 때만 True다. 다른 열린 과제가 focus를 쥐고 있으면 `wbs(action="open")`는 `task_start refused: another open task owns the resolvable session focus`로 거부하고, next_action은 "Finish or park the currently focused task"다(`harness_server.py:1196-1204, 1338-1347, 1612-1646`).

> C-09는 세션 focus 충돌을 거부하며 진행은 WBS 의존 관계에 따른다. Codex MCP는 명시적 workspace 호출 또는 exact `CODEX_THREAD_ID` 없는 main 호출에서 exact-session 검사를 PostToolUse로 미룬다. main exact identity가 있으면 공유 잠금 안에서 다른 worktree의 열린 바인딩도 검사하고 충돌 시 거절한다. PostToolUse가 발견한 충돌은 관련 과제를 fence하여 thread binding을 지운다(`CONTRACTS.md` C-09).

주의할 점:

- **hint는 저장소 전역이고 마지막에 쓴 쪽이 이긴다.** 한 checkout에서 여러 세션을 동시에 돌리면, 가장 최근에 프롬프트를 보낸 세션이 다음 `wbs(action="open")`/`wbs(action="context")`의 바인딩을 가져간다. 여기에는 Claude와 Codex를 섞어 쓰는 경우도 포함된다. 같은 checkout에서 Codex 프롬프트가 들어오면, 동시에 돌고 있는 Claude 세션의 다음 바인딩이 Codex 세션 ID로 바뀐다. legacy `.active`도 공유되므로 다른 세션의 새 과제를 `_session_resumes`가 거부할 수 있다(`harness_server.py:203-209`).
- **마커가 없는 세션의 쓰기는 다른 세션의 과제 기준으로 gate된다.** prewrite gate는 훅 payload의 session_id로 `resolve_active_task_dir`를 부른다. 이 세션에 살아 있는 마커가 없으면 legacy `.active`로 넘어가는데, 이때 liveness를 검사하지 않는다. 결과적으로 PLAN.md 유무, forbidden_paths, WFCS용 MAINTENANCE가 모두 legacy가 가리키는 (다른 세션의) 과제 기준으로 판정된다(`_lib.py:2353-2386`, `prewrite_gate.py:733-737, 755, 783-803`).
- **프롬프트를 한 번도 보내지 않은 Claude 세션**(hint 없음)에서 `wbs(action="open")`를 부르면 `default.json`(과 `.active`)만 생긴다. 이 상태에서 끝난 서브에이전트의 영수증은 기록되지 않는다. hint가 생긴 뒤 `wbs(action="context")`(또는 기본 `wbs(action="open")`)를 부르면 복구된다. hint 세션에는 마커가 없으므로 `_session_resumes`가 이 과제를 가리키는 legacy `.active`로 넘어가 True가 되고, 그 세션의 마커가 현재 run_id로 쓰인다. 재바인딩은 그 **뒤에** 스폰하는 lens에만 효과가 있다. 이미 끝난 lens의 결과는 NON-ATTESTING으로 취급하고, 영수증을 얻으려고 다시 돌리지 않는다. 실제 결과에 따라 진행한다. 실제 FAIL은 수정하고, 실제 BLOCKED_ENV는 바로 `wbs(action="block")`로 게시하며, 실제 review PASS일 때만 QA로 넘어간다. 실제 QA PASS 뒤에 `wbs(action="verify")`를 1회 부른다. 순서 있는 영수증 PASS면 닫는다. 아니면 그 응답의 `next_action`에 있는 고정 쌍을 그대로 복사해 `wbs(action="block")`를 부른다. 이 run에 영수증이 하나도 없으면 empty-stream 쌍이고, 영수증은 있는데 필수 completion이 없으면 missing-attestation 쌍이다. 단 `doc/harness/.receipt-capability-broken`이 있으면 어느 쌍도 복사하지 않고 genuine-external-blocker 경로를 따른다(`plugin/skills/run/SKILL.md:9-30`, `harness_server.py:1372, 1455-1458, 1649-1680`, `tests/test_session_hint_marker_binding.py:125-141`, `tests/test_task_context_binds_resuming_session.py:1-15`).
- **close/주차 뒤에 남는 마커.** `wbs(action="close")`와 `wbs(action="block")`는 `session_id` 없이 `clear_active_marker`를 부른다. 그래서 MCP 프로세스의 `current_session_id()` 마커(Claude에서는 대개 `default.json`, 어느 과제를 가리키든)를 지우고, legacy `.active`는 이 과제를 가리킬 때만 지운다. hint 이름으로 만든 `<sid>.json`은 디스크에 남는다(`_lib.py:2441-2478`). 이 마커는 과제가 열려 있지 않은 동안에만 무해하다.
  - blocked 과제를 어느 세션이든 기본 `wbs(action="open")`로 재개하면 run이 유지된다. 그러면 원래 세션의 남은 마커가 다시 유효해져서(열린 과제, 같은 run_id) 그 세션이 바인딩과 focus를 조용히 되찾는다.
  - close 뒤 `fresh_run`으로 다시 열면 run_id가 바뀌므로 영수증 바인딩은 실패한다. 하지만 `resolve_active_task_dir`는 run_id를 비교하지 않으므로 focus는 여전히 그 세션에 잡혀 있다. 그 세션에서 다른 과제로 `wbs(action="open")`를 하면 거부된다.

  `plugin/CLAUDE.md:31`은 "clears this session's active marker"라고 설명하지만, 현재 코드는 MCP 프로세스의 `current_session_id()` 마커와 legacy `.active`만 지운다(§17.2에 관찰로 기록).

**Codex의 바인딩.** 명시적 workspace 호출은 공유 MCP 호스트의 thread ID를 lead 신원으로 사용하지 않는다. 이 호출과 exact 환경 thread ID가 없는 main 호출은 바인딩을 미룬다(`defer_codex_binding`). main에 exact 환경 identity가 있으면 기존 eager 바인딩을 유지하되, 공통 root와 등록 worktree의 세션 잠금 안에서 다른 열린 바인딩을 배제한다. 바인딩을 미루는 경로의 동작은 다음과 같다(`harness_server.py:1188-1193, 1450-1458, 1649-1661`, `_lib.py:2245-2268`, `codex_hook_registration.py:213-330`, `hook_post_tool_use.py:96-154`).

1. `wbs(action="open")`는 focus 검사를 미루고 세션 마커와 legacy `.active`를 쓴다(`publish_legacy=True`). 명시적 workspace 호출은 고정 `default`를 쓰며, exact thread ID 없는 main 호출은 `current_session_id()`를 쓴다(보통 `default`, 다른 지원 환경 identity가 있으면 그 값).
2. 같은 조건에서 `wbs(action="context")`는 마커를 쓰지 않는다.
3. 그 뒤 PostToolUse 훅이 `register_task_result`로 도구 결과를 파싱한다. 과제가 열려 있고 run_id가 같은지 확인한 다음, legacy 없이(`publish_legacy=False`) exact-thread 마커를 쓰고 watcher를 등록한다.
4. 서로 충돌하는 열린 과제가 있으면 마커 대신 binding-conflict fence를 쓰고 등록을 무효화한다.

영수증 바인딩은 exact-thread 마커만 사용한다. default/legacy 마커는 권한이 아니다. close/주차의 일반 마커 정리는 비권위적인 충돌 fence를 보존한다. 충돌 기록이 남은 복구 경로에서는 exact main 환경 identity가 있어도 바인딩을 PostToolUse까지 미룬다. 정확한 성공 결과가 모든 이전 등록을 무효화한 뒤 충돌 기록을 해제하고 현재 rollout 위치부터 새로 등록한다.

### 3.8 workspace: control root와 task root

`_task_roots(args)`는 `(control_root, task_root)`를 돌려준다(`harness_server.py:1057-1087`).

- `workspace`를 생략하거나 control root와 같은 값을 주면 두 root가 같다. Codex도 이 경로를 지원한다. 명시적 workspace 호출이나 exact 환경 thread ID 없는 main 호출은 native PostToolUse가 바인딩하며, exact main identity가 있으면 공유 잠금 아래 eager 바인딩한다.
- 문자열이 아니면 `wrong_type`으로 거부한다.
- 나머지 경우는 `resolve_registered_worktree`를 통과해야 한다(`_lib.py:1886-1936`). 조건:
  - 절대 정규 경로
  - `<ws>/.git`이 일반 gitfile
  - 그 gitdir이 `<control>/.git/worktrees` 바로 아래에 있음
  - gitdir의 back-pointer가 `<ws>/.git`을 가리킴
  - worktree에 자체 Harness manifest가 있음

  하나라도 어긋나면 `GitBindingError WORKSPACE_NOT_REGISTERED_WORKTREE`.

task dir, scaffold와 focus 마커는 task root에 쓰인다. 공개 WBS는 request_file 입력을 받지 않는다. Claude 세션 identity(hint), watcher 진단, gate 경고 learnings는 control root에 남는다. Codex exact coordinator 마커와 watcher 등록은 바인딩된 task workspace에 있고 native cwd는 main에 남을 수 있다(`harness_server.py:1188-1209`).

**root 해석.** `_control_root()`는 MCP 프로세스 cwd의 git root에 `harness_root_resolution`을 적용한 값이다(`harness_server.py:134-139`). 훅 쪽의 `find_repo_root`는 프로세스 cwd보다 payload `cwd`를 먼저 본다(`_lib.py:1736-1747`). `harness_root_resolution`은 위로 올라가며 가장 가까운 일반 파일 manifest를 찾는다. manifest가 잘못되어 있으면(예: symlink) `harness_root_resolution`은 예외 없이 `(root, error)`로 오류를 돌려준다. MCP의 `_control_root()`는 이를 `RuntimeError`로 바꿔 내고, prewrite gate는 `invalid-harness-workspace`로 거부한다. 중첩 저장소는 따로 다룬다. cwd와 그 manifest 사이에 다른 `.git`(중첩 git 저장소)이 있으면, 바깥 manifest는 **현재 세션**(`current_session_id()`)이 바깥 root에 열린 과제를 가리키는 살아 있는 마커를 가진 동안에만 그 중첩 저장소를 소유한다. 그렇지 않으면 `('', '')`를 돌려준다. 그러면 `_control_root`는 중첩 저장소의 git root로 돌아가고, 훅과 prewrite gate는 그 저장소를 non-harness로 본다. 쓰기는 검사 없이 통과하고 영수증도 남지 않는다(`_lib.py:1939-1995, 1998-2012`).

### 3.9 트랜잭션과 저장소 무결성

- **receipt 트랜잭션.** task dir 파일 디스크립터에 거는 배타적 *blocking* `flock`이다. 락 파일은 만들지 않는다. 조건이 맞지 않으면 `receipt storage integrity unavailable`이 난다. ContextVar로 재진입할 수 있고, 빠져나올 때 identity를 다시 확인한다(`_lib.py:2579-2740`). 조건:
  - task dir과 그 위 3단계 조상이 모두 존재하고, symlink가 아닌 디렉터리다.
  - task dir 자체는 현재 uid 소유이고 group/world 쓰기가 불가능하다.
- **goal 트랜잭션.** `doc/harness/goals/`에 거는 flock이다(`_lib.py:319-412`).
- `wbs(action="plan")`, `wbs(action="close")`, `wbs(action="block")`는 모두 이 트랜잭션 안에서 control이 바뀌지 않았는지 다시 확인하고, 실패하면 스냅샷으로 되돌린다.

### 3.10 task pack(compact context)과 라우팅

`emit_compact_context`가 만드는 필드(`_lib.py:4843-4968`):

- `status`, `routing`, `runtime_verdict`
- `source_write_allowed`: PLAN.md가 있거나 micro면 true
- `why_source_write_blocked`, `review_verdict`, required lenses
- `missing_for_close`, `next_action`, `report_path`
- `effective_close_gate`: `micro` 또는 `standard`

`compile_routing`은 저장하지 않고 매번 계산한다. 돌려주는 값은 다음과 같다(`_lib.py:2518-2550`).

- `maintenance_task`: MAINTENANCE 파일이 있거나 manifest `maintenance_default`
- `workflow_locked`
- `risk_level`: maintenance면 `high`, 아니면 `medium`
- `execution_mode`
- `orchestration_mode: "solo"`
- `planning_mode`: micro면 `skipped`

> plan `intake.md:59`는 `ui_scope`, `must_read`, `compat.execution_mode`도 읽으라고 하지만, `compile_routing`은 이 필드들을 돌려주지 않는다.

---

## 4. 훅

### 4.1 Claude 훅 전체(`plugin/hooks/hooks.json`)

등록된 이벤트는 정확히 여섯 개다(`hooks.json:3-94`).

| 이벤트 | matcher | 실행 | timeout | 역할 |
|---|---|---|---|---|
| SessionStart | — | ① inline 배너 ② `verification_gap_check.py` ③ `project_format_check.py` ④ `drift_warn.py` | 각 5 | 준비 배너와 경고 주입 |
| SubagentStart | — | `background_hook.py --event start` | 3 | `started` 영수증 |
| SubagentStop | — | `background_hook.py --event stop` | 3 | `completed` 영수증 |
| PreToolUse | `Write\|Edit\|MultiEdit` | `prewrite_gate.py` | 10 | 쓰기 차단(§5) |
| UserPromptSubmit | — | `prompt_memory.py` | 3 | session hint 기록, 상태 주입 |
| PostToolUse | `Bash` | `tool_routing.py` | 3 | 명령 실패 힌트 |

모든 명령은 `PYTHONDONTWRITEBYTECODE=1`로 시작하고 `|| true`로 끝난다. 두 장치는 이유가 다르다.

- `PYTHONDONTWRITEBYTECODE=1`: 오염된 bytecode 캐시가 영수증 기능을 꺼 버린 사고 때문에 들어갔다(`doc/harness/REQ__bytecode-cache-cannot-disable-receipts.md`, `install.py:72-92`).
- `|| true`와 timeout ≤10: C-12 fail-safe 관례다. bytecode 사고보다 먼저 있었다(`CONTRACTS.md:178-187`).

**3초 timeout과 락 대기.** 영수증 append는 과제 디렉터리에 blocking `flock(LOCK_EX)`를 잡는다. 따라서 영수증 훅은 3초 안에 락 획득부터 append까지 끝내야 한다. 그런데 `install_verified.py`는 같은 receipt 트랜잭션을 쥔 채로 중첩 `python3 install.py` 전체(marketplace update, plugin update, smoke)를 실행한다(§11.2). 이 동안 도착한 같은 과제의 SubagentStart/Stop은 락을 기다리다 3초에 강제 종료된다. 그러면 행도 흔적도 남지 않고 재시도도 없다. 그러므로 **install_verified는 모든 lens가 끝난 뒤에만 실행한다.** prewrite gate는 별도로 10초 예산을 갖지만, 그 안에 끝나지 않아 Claude가 죽이면 deny JSON 없이 쓰기가 진행될 수 있다(`hooks.json:39, 51, 64, 76`, `_lib.py:2686-2698`, `subagent_lifecycle.py:550, 644, 653, 699`, `_lib.py:3415-3417`, `install_verified.py:343-392`).

### 4.2 SessionStart

- **배너**: `harness — ready … Loop: task start → plan → develop → QA → close … Auto-routing on. Just describe what you want.`
- **`verification_gap_check.py`**: 활성 과제가 `qa-browser`를 요구하는데 완료된 `qa-browser` PASS 영수증이 없으면 `[verification-gap]`을 출력한다. `HARNESS_DISABLE_VERIFY_GAP=1`로 끌 수 있다(`verification_gap_check.py:27-68`).
- **`project_format_check.py`**: 다음 중 하나에 해당하면 `[harness-version]`과 `setup_finalize.py --migrate-harness-version` 명령을 출력한다(`project_format_check.py:24-89`).
  - manifest version < 7
  - 옛 `harness_version` 키가 남아 있음
  - `.version`/`.format-version` 파일이 남아 있음
  - 운영 ignore가 빠져 있음
  - (ignore 줄은 모두 있지만) 실제 ignore 적용에 오류가 있음(`effective_ignore_errors`)

  검사 자체가 실패하면 명령 대신 `[harness-version] Cannot check Harness version: …` 한 줄을 출력한다. 실패 원인의 예는 git root가 정확하지 않음, manifest 파싱 오류, 마커 파일이 일반 파일이 아님, 지원하지 않는 version(ValueError)이다.
- **`drift_warn.py`**: §2.3 참고.

세 스크립트 모두 stdin을 읽지 않고, repo root로 `os.getcwd()`를 쓴다. 활성 과제를 해석하는 것은 `verification_gap_check`뿐이다. payload session_id가 없으므로 환경 변수 세션 ID나 legacy `.active`를 쓰는데, legacy는 열린 과제인지 확인하지 않는다. 그래서 세션에 묶인 과제를 놓치거나, 닫혔거나 주차된 과제를 보고할 수 있다(`verification_gap_check.py:38-49`, `_lib.py:921-931, 2370-2386`, `project_format_check.py:79-89`, `drift_warn.py:69-89`).

### 4.3 UserPromptSubmit — `prompt_memory.py`

1. harness 저장소가 아니면 바로 끝난다.
2. payload의 `session_id`를 `.session-hint`에 쓴다(§3.7).
3. 다음 순서로 블록을 조립한다(`prompt_memory.py:51-68, 126-160, 163-271`).
   - `[harness-doc-gate]`(항상).
   - 활성 과제가 있으면 영수증에서 계산한 상태 한 줄. review PASS가 없으면 `[harness-review] PENDING`, review PASS는 있고 runtime이 PASS가 아니면 `[harness-review/qa] RECORDED`, runtime이 PASS면 없음. `[harness-qa] PENDING`(QA_GATE)은 실제로는 나오지 않는다. qa 분기는 review 분기가 이미 runtime PASS를 본 경우에만 실행되는데, QA_GATE는 runtime이 PASS가 아닐 때만 돌려주기 때문이다(두 번의 읽기 사이에 경합이 있거나 두 번째 읽기에서 예외가 날 때만 가능)(`prompt_memory.py:141-160, 242-249`).
   - `[harness-context] task=… status=… recorded_verdict=…`
   - `[harness-restore]` 블록. 훅이 이 블록을 직접 `system-reminder` 태그로 감싼다. "latest artifacts"라는 이름이 붙어 있지만 RECEIPTS.jsonl 조각은 **첫 번째** 의미 있는 행이다(BLOCKED.md도 첫 줄).
   - runbooks 블록.
   - 활성 과제가 없을 때만 `[harness-goal]`: native objective 또는 일반 요청의 같은 WBS를 열도록 안내한다. 구형 장부를 읽지 않는다.
4. 제어 문자 제거와 `system-reminder` 태그 조각의 `[SANITIZED]` 치환은 restore 조각에 적용된다. 현재 정적인 WBS 안내에는 objective 본문을 덧붙이지 않는다(`_sanitize_prompt_text`). 출력 전체에 적용되는 것은 아니다. `_sanitize_path`는 정의만 되어 있고 호출하는 곳이 없다(`prompt_memory.py:85, 163-219`).
5. **전체 출력을 400자로 자른다**(`MAX_OUTPUT_CHARS`). 측정 길이는 DOC_GATE 135자, REVIEW_GATE 158자다. 그래서 활성 과제가 있으면 restore 블록과 runbook 블록은 대개 잘린다. 잘리면서 reminder 블록의 닫는 태그가 없어질 수도 있다(`prompt_memory.py:48-49, 268`).

**실패 모드.** `_build_block`은 `receipt_runtime_verdict`를 try/except 없이 부른다(review/QA gate와 다르다). 그래서 영수증을 읽다가 예외가 나면(잘못된 행, 무결성 실패) `main()` 밖으로 전파된다. 최상위 핸들러는 `gate-error` 행(source `prompt_memory`)을 남기고 출력 없이 exit 0으로 끝난다. 이때는 `[harness-doc-gate]`까지 빠진다. hint는 그 전에 이미 쓰였다. **활성 과제가 있는데 프롬프트 훅이 아무것도 출력하지 않으면 영수증 스트림이 깨졌을 수 있다.** learnings.jsonl에 source `prompt_memory`인 `gate-error` 행이 있으면 이 경우다. 그 행이 없으면 원인이 다를 수 있다. 하나는 `_lib` import 실패(조용히 exit 0)다. 다른 하나는 3초 timeout이다. 예를 들어 `install_verified`가 receipt 락을 쥔 동안 `receipt_snapshot`이 flock을 기다리다 강제 종료될 수 있다(`prompt_memory.py:126-138, 222-282`).

Git은 실행하지 않는다.

### 4.4 PostToolUse — `tool_routing.py`

harness 저장소의 Bash 결과만 다룬다(`tool_routing.py:46-215`).

- pytest/npm/bun/pnpm/yarn/node/python(3)이 `command not found`로 실패하면 manifest `test_command`나 `build_command`를 제안한다.
- `plugin/scripts|plugin/mcp/*.py`가 `No such file or directory`로 실패하면 이웃 스크립트를 최대 3개 알려 준다.

출력은 `[harness-hint]` 한 줄이다. `--goal-hint-worker` 모드는 Codex `create_goal`에 쓰인다.

Claude에서는 hooks.json이 `tool_routing.py`를 직접 실행하므로 힌트가 PostToolUse의 **평문 stdout**으로 나온다. Claude Code 훅 문서에 따르면 exit 0 훅의 평문 stdout이 모델 컨텍스트에 들어가는 것은 UserPromptSubmit과 SessionStart뿐이다. 따라서 Claude에서는 모델이 `[harness-hint]`를 보지 못할 가능성이 높다(외부 동작이므로 현재 훅 문서와 대조해 확인할 것). Codex 래퍼는 같은 힌트를 `hookSpecificOutput.additionalContext`로 감싸서 전달한다(`hooks.json:82-93`, `tool_routing.py:198-201`, `hook_post_tool_use.py:186-207`). Claude에서도 모델이 보게 하려면 같은 JSON 형태로 내보내야 한다.

### 4.5 SubagentStart/Stop — `background_hook.py`

자세한 동작은 §6에 있다. 훅 자체는 다음과 같이 동작한다(`background_hook.py:186-401`).

- payload 상한을 8 × 2 MiB(16 MiB)로 키운다. 최대 크기의 review 원문도 받기 위해서다.
- payload `cwd`에서 harness root를 해석한다. harness 저장소가 아니거나 root 해석 오류가 나면 흔적 없이 0으로 끝난다.
- import가 정상이면 `.receipt-capability-broken`을 지우고 `subagent_lifecycle.handle_subagent_hook`을 부른다.
- `_lib`/`subagent_lifecycle` import에 실패하면: 형제 `__pycache__`를 지우고, `doc/harness/.receipt-capability-broken`을 쓰고, learnings에 `gate-crash`(key `receipt-subsystem-unavailable`)를 남긴 뒤 0으로 끝난다. 영수증은 쓰지 않는다.
- 영수증이 필요했는데(started 행이 있거나 payload에 agent type이 있음) 결과가 `{}`면 흔적을 남긴다. 조건과 내용은 다음과 같다(`background_hook.py:276-357, 369-383`, `_lib.py:981-1007`, `subagent_lifecycle.py:155-174`).
  - `_log_gate_error`로 쓰므로 행 type은 `gate-error`, source는 `background_hook:binding-miss`다. 메시지는 최대 400자라서 payload_keys가 잘릴 수 있다.
  - 들어가는 값: `provenance_reason`, `session_id` 값, `transcript_exists`(bool), `transcript_tail`(transcript 경로의 마지막 두 요소, 즉 `subagents/agent-<agent_id>.jsonl`), payload 키 이름.
  - transcript 내용이나 assistant 텍스트는 절대 들어가지 않는다.
  - `resolve_active_task_dir`가 활성 과제를 찾았고, lifecycle이 `receipt_not_owed`(lens 없는 이름 없는 스폰)를 설정하지 않았을 때만 남는다.
- 항상 0으로 끝난다.

### 4.6 fail-safe 규칙(C-12)

- C-12는 `plugin/hooks/hooks.json`의 모든 명령에 `|| true`와 `timeout ≤ 10`을 요구한다. 강제 수단은 관례와 리뷰다(`CONTRACTS.md:178-187`). `tests/test_hooks_json.py:19-32, 73-89`가 이를 기계적으로 확인한다. 같은 테스트는 네 가지를 더 확인한다: prewrite_gate의 PreToolUse matcher가 정확히 `Write|Edit|MultiEdit`이다, Bash PreToolUse matcher가 없다, **PreToolUse** 명령 중 `mcp_bash_guard.py`나 `qa_delegation_gate.py`를 참조하는 것이 없다(다른 이벤트의 명령은 검사하지 않는다), `plugin/scripts/mcp_bash_guard.py` 파일이 없다.
- **`|| true`가 거부를 무력화하지 않는 이유.** gate는 거부할 때 stdout에 JSON `{hookSpecificOutput:{hookEventName:'PreToolUse', permissionDecision:'deny', permissionDecisionReason}}`를 쓰고 **exit 0**으로 끝난다. 허용은 아무것도 출력하지 않는 것이다. `permissionDecisionReason`에는 `↳ next action / ↳ owner / ↳ docs` 줄을 먼저 덧붙이고, 그다음 전체 문자열을 2000자로 자른다. 그래서 reason이 길면 ↳ 줄이 잘릴 수 있다(`_lib.py:939-970`, `prewrite_gate.py:4-13`).
- **fail open.**
  - 입력이 비었거나 잘못된 JSON이면 `{}`로 보고 조용히 허용한다.
  - gate가 `_lib` import에 실패하면 exit 0.
  - 잡히지 않은 예외는 `log_gate_crash`가 learnings에 `{type:'gate-crash', script, tool_name, payload_keys, error}`로 남기고 exit 0으로 끝난다. 쓰기는 허용된다(`prewrite_gate.py:41-67, 931-942`, `_lib.py:1010-1030`).

  불안정한 훅이 세션을 막는 것보다 훅이 없는 편이 낫다는 판단이다(C-12 Why).
- **전체 payload 읽기.** gate는 `_read_whole_hook_input`으로 EOF까지 읽고 `_parse_hook_payload`에서 실제 길이에 맞는 `max_chars`를 `read_hook_input`에 넘긴다. `_lib`의 다른 호출자 기본값 64 KiB는 그대로다. gate 자식, Codex fallback과 wrapper의 tool-name 판독 모두 locale과 무관하게 UTF-8 `surrogateescape`를 써서 잘못된 한 바이트 때문에 payload가 비는 우회를 막는다. 메모리 고갈이나 host가 hook 전체를 죽이는 경우까지 보장하지는 않는다(`prewrite_gate.py`의 `_read_whole_hook_input`, `_parse_hook_payload`; `hook_pre_tool_use.py`의 `_tool_name`).

### 4.7 Codex 훅

Codex 훅은 `hooks.json`이 아니라 `install.py`의 `_codex_hooks_config`가 생성한다(`install.py:1152-1225`).

| 이벤트 | matcher | 스크립트 | timeout |
|---|---|---|---|
| SessionStart | — | `hook_session_start.py` | 20 |
| PreToolUse | `Write\|Edit\|MultiEdit\|apply_patch\|collaboration\.spawn_agent` | `hook_pre_tool_use.py` | 5 |
| UserPromptSubmit | — | `hook_user_prompt_submit.py` | 8 |
| PostToolUse | `Bash`, `create_goal`, canonical `wbs` forms (open/context only) | `hook_post_tool_use.py` | 3 |

- 이 명령들에는 `|| true`가 없고 SessionStart timeout은 10을 넘는다. C-12의 적용 범위가 `hooks.json`이라서 계약 위반은 아니다. 대신 내부 try/except와 자식 프로세스 timeout에 의존한다.
- `hook_pre_tool_use.py`는 쓰기 도구에서 gate 자식에 3.0초를 준다. timeout, spawn 실패, 출력 없는 nonzero 종료면 `prewrite_gate.protected_artifact_decision`을 프로세스 안에서 실행하여 C-05 대상만 거부한다. 다른 쓰기와 fallback 자체 오류는 fail-open이다. wrapper 전체가 바깥 5초 제한으로 죽으면 이 보장도 없다. `spawn_agent`에서는 bind할 수 없는 review 이름을 거부하고 0.5초 예산으로 watcher 등록을 복구한다(`hook_pre_tool_use.py`의 `CHILD_TIMEOUT_SECONDS`, `_run`, `_protected_artifact_fallback`, `main`).
- `hook_user_prompt_submit.py`는 harness 저장소에서 항상 `[harness-route] Repository mutation: invoke $harness:run before edits`를 앞에 붙인다. 그다음 `session_id`를 담은 payload를 `HARNESS_RUNTIME=codex` 환경의 `prompt_memory.py`로 넘긴다(자식 6초, 전체 7초). 그래서 Codex에서도 session hint가 기록된다(`hook_user_prompt_submit.py:14-89`).
- `hook_session_start.py`는 먼저 `restore_watcher_registration(payload, retry_seconds=1.0, budget_seconds=1.25)`를 부른다. 이 호출은 try/except로 감싸져 있지 않다(import만 보호된다). 그다음 `verification_gap_check`와 `project_format_check`를 8초 자식 프로세스로 실행하고, 결과를 additionalContext로 돌려준다. 배너와 drift_warn은 없다(`hook_session_start.py:13-16, 33-69`).
- `hook_post_tool_use.py`는 세 가지 일을 한다. WBS open/context 결과만 thread에 바인딩하고, `create_goal` 뒤에 `[harness-goal]` 힌트를 주고, Bash 결과를 `tool_routing.py`로 넘겨 additionalContext로 감싼다(`hook_post_tool_use.py:149-210`).

### 4.8 일부러 없는 훅

- **Stop(turn-end) 훅 없음.** 코디네이터가 백그라운드 리뷰어를 기다리는 동안 빈 턴이 반복되는 문제 때문에 2026-09-23에 등록을 지웠고, 같은 날 스크립트도 지웠다. 여러 턴에 걸쳐 작업을 이어 가는 것은 native `/goal`이 맡는다. goal 조건에 "wbs close PASS"를 넣으면 과제가 열려 있는 동안 goal이 끝나지 않는다(`CONTRACTS.md` C-17).
- **Bash/셸 쓰기 차단 없음.** 직접 쓰기 도구만 gate한다(`CONTRACTS.md:108-109`, `doc/harness/patterns/ADR__selective-pretool-dispatch.md:18-36`). 따라서 보호 아티팩트도 셸로는 다시 쓸 수 있다. NotebookEdit이나 앞으로 추가될 쓰기 도구도 matcher에 없다.
- **브라우저 gate 없음(C-18).** qa-browser 위임은 워크플로 지침일 뿐이다. 리뷰-다음-QA 순서는 `wbs(action="verify")`가 그대로 강제한다(`CONTRACTS.md:306-327`).

### 4.9 `hook_tree_health.py`

등록된 훅이 아니라 MCP 서버가 import해서 쓰는 모듈이다. 등록된 플러그인 트리에 `background_hook.py`/`subagent_lifecycle.py`가 없거나 SubagentStart/Stop 등록이 없으면 `wbs(action="open")`가 `RECEIPT_HOOKS_UNAVAILABLE`을 경고한다. 문구를 조심스럽게 쓴 참고용 경고다.

같은 함수(`receipt_capability_warning`)는 `_watcher_status`에도 값을 준다. 그래서 그 텍스트가 `wbs(action="verify")`, `wbs(action="context")`, `wbs(action="close")`가 돌려주는 `watcher_status.receipt_capability_warning`에도 나타난다. 이 경로도 참고용이다. 이 경고만으로는 `receipts_recordable`이 False가 되지 않는다. 현재 run에 영수증이 없으면 None이 되고, 이미 영수증이 있으면 초기값 True가 유지된다. capability 마커 같은 관측 신호가 있으면 그 분기가 먼저 False로 정한다. Codex 런타임(`HARNESS_RUNTIME=codex`이거나 `CODEX_THREAD_ID`가 설정됨)에서는 Claude 플러그인 트리를 검사하지 않는다. 대신 별도의 `_codex_capability_warning` 경로가 watcher 등록을 검사한다(`hook_tree_health.py:1-58, 245-314`, `harness_server.py:566-600, 1489-1508`).

---

## 5. prewrite gate

`plugin/scripts/prewrite_gate.py`는 Claude PreToolUse(`Write|Edit|MultiEdit`)와 Codex 래퍼(`apply_patch` 포함)에서 돈다. 쓰기 경로는 `tool_input.file_path`(또는 `.path`)에서 얻는다. `apply_patch`의 경우 `*** Add/Update/Delete File:` 대상을 먼저 모두 모으고, 그다음 `*** Move to:` 대상을 모은다(중복 제거). 상대 경로는 payload `cwd` 기준으로 푼다. 모은 경로를 이 순서대로 검사하고, **처음 거부된 경로에서 호출 전체가 끝난다**. 패치 안에 적힌 순서와는 다를 수 있다(`prewrite_gate.py:631-644, 824-928`).

gate는 전체 payload를 UTF-8 `surrogateescape`로 읽는다. 크기 때문에 잘리는 기존 64 KiB 상한은 적용되지 않는다(§4.6).

### 5.1 규칙 순서

코드에 적힌 순서 그대로다(`prewrite_gate.py:631-928`).

| # | 조건 | 결과 | rule id | 이유 |
|---|---|---|---|---|
| 0 | payload 없음 또는 JSON 파싱 실패 | 허용 | — | fail-safe |
| 0 | `HARNESS_SKIP_PREWRITE=1` | 허용, learnings에 `gate-bypass` 기록 | — | 명시적 탈출구. 환경에 설정되어 있는 동안 계속 적용(§5.8) |
| 1 | harness root 해석 오류(manifest가 symlink, 경로에 symlink, 일반 파일 아님, 읽기 불가) | 거부 | `invalid-harness-workspace` (owner `harness:setup`) | 잘못된 조상 트리 위에서는 판단할 수 없다 |
| 2 | 일반 파일 `doc/harness/manifest.yaml`이 없음 | 허용 | — | harness 저장소가 아니다 |
| 3 | 대상이 Claude transcript(`…/projects/**/subagents/agent-*.jsonl`)나 Codex rollout(`…/sessions/**/rollout-*.jsonl`) | 거부 | `C-05-protected-artifact` (owner `claude-runtime`) | 영수증 출처 증거를 보호한다 |
| 4 | 요청 경로는 root 안인데 realpath가 root 밖 | 거부 | `symlink-outside-control` | symlink 우회 방지 |
| 5 | 등록된 같은 저장소 worktree로 라우팅되지 않은 대상이 실제로 root 밖 | 그 대상의 Harness root를 파일 검사로 찾아 **보호 아티팩트만** 거부하고 나머지는 허용 | `C-05-protected-artifact` | 다른 checkout(worktree)의 제어 파일 보호. 오류는 기록하고 허용 |
| 6 | 보호 아티팩트(§5.2) | 거부 | `C-05-protected-artifact` | 쓰는 주체가 따로 정해져 있다 |
| 7 | 그 밖에 `doc/harness/tasks/` 안의 경로 | 허용 | — | MAINTENANCE, PROGRESS.md 등 |
| 8 | `EXEMPT_PREFIXES` | 허용 | — | learnings, qa, checkpoints, patterns, retros, visual-baselines |
| 9 | `WORKFLOW_CONTROL_SURFACE` | MAINTENANCE 없음 → 거부. MAINTENANCE 있음 → **즉시 허용** | `workflow-control-surface` (owner `maintain-skill`) | 하네스 자체의 제어 파일. 아래 참고 |
| 10 | 확장자가 `SOURCE_EXTENSIONS`에 없음 | 허용 | — | 문서, 설정 등은 gate 대상이 아니다 |
| 11 | 활성 과제 없음 | 최상위 strict 키가 있거나 open/invalid 과제가 있으면 거부, 아니면 허용. 이 저장소에서는 strict가 읽히지 않는다(§5.4) | `no-active-task` | §5.4 |
| 12 | 활성 디렉터리가 없거나, 디렉터리가 아니거나, tasks 밖 | 거부 | `invalid-active` | 마커 손상 |
| 13 | PLAN.md 없음(MAINTENANCE도 micro도 아님) | 거부 | `C-02-plan-first` | 계획 없는 구현은 범위가 흐트러진다 |
| 14 | 경로 휴리스틱이 관찰 가능한 동작 경로로 판단했는데 REQ 링크가 없음(§5.5) | 거부 | `C-REQ-observable-doc-required` | 관찰 가능한 동작은 REQ 문서가 필요하다 |
| 15 | PROGRESS.md `forbidden_paths`에 걸림 | 거부 | `scope-lock-forbidden` (owner `developer`) | 범위 고정 |
| 16 | 그 밖 | 조용히 허용 | — | — |

**1-2행 보충.** 초기 root는 payload `cwd`에서 해석한다. 그 뒤 대상의 lexical 조상에서 같은 저장소의 등록된 worktree가 검증되면 그 checkout의 root로 C-05와 source gate를 적용한다. 기존 cwd root의 symlink 이탈 거부는 유지한다. cwd가 Harness root 아래의 중첩 git 저장소 안에 있으면, 이 세션이 바깥 root에 열린 과제 마커를 가진 동안에만 gate가 적용된다. 그렇지 않으면 root 해석 결과가 비어서 gate는 모든 쓰기를 허용한다(`_lib.py:1939-1995`, `prewrite_gate.py:640-663`, §3.8).

**9행 보충.** MAINTENANCE가 있으면 gate는 그 자리에서 허용을 돌려준다. 그래서 workflow-control 파일에는 10-15행(plan-first, REQ, scope-lock forbidden_paths)이 전혀 적용되지 않는다. 이 파일들 중 여럿은 `.py`다(`_lib.py`, `prewrite_gate.py`, `harness_server.py`). MAINTENANCE 조회는 `resolve_active_task_dir`를 거친다. 이 함수는 세션 마커가 없으면 legacy `.active`로 넘어가는데, 이때 열린 과제인지 확인하지 않는다. 그래서 오래된 `.active`가 가리키는 닫힌 과제나 주차된 과제에 MAINTENANCE 파일이 있어도 이 쓰기가 허용된다(`prewrite_gate.py:733-749`, `_lib.py:2370-2386`).

### 5.2 보호 대상

| 대상 | 판정 | owner(거부 메시지) |
|---|---|---|
| `TASK.json` | 저장소 어디서든 basename | `task-control-mcp` |
| `PLAN.md` | basename | `plan-skill` |
| `RECEIPTS.jsonl` | basename | `receipt-lifecycle-hook` |
| `REVIEWS.jsonl` | `doc/harness/tasks/TASK__*/REVIEWS.jsonl`만 | `review-detail-writer` |
| `WBS.json` | basename | `task-control-mcp` |
| `doc/harness/goals/*.json` | 폐기된 직접 자식 JSON | `legacy-data-migration` |
| `doc/harness/archive/goal-migration/**` | 전체 원본·manifest·lock | `legacy-data-migration` |
| `doc/harness/runtime/batches/**` | 전체 pool 제어 자료 | `batch-pool-runtime` |
| `doc/harness/tasks/.active`, `.active_sessions/**` | 경로 | `task-control-runtime` |
| 런타임 transcript / rollout | 경로 패턴(§5.1 3행) | `claude-runtime`(Codex rollout에도 같은 owner) |

근거: `prewrite_gate.py:74-87, 147-204, 665-676`. 마커 경로는 `PROTECTED_ARTIFACTS.get(basename, "task-control-runtime")` 폴백으로 owner가 정해진다.

- CONTRACTS C-05는 두 마커 경로를 목록에 넣지 않았지만 코드는 보호한다.
- basename으로 판정하기 때문에 batch 보관본(`doc/harness/archive/batch/TASK__x/`)의 TASK.json/PLAN.md/RECEIPTS.jsonl도 Write/Edit가 거부된다(REVIEWS.jsonl은 제외).

### 5.3 고정 목록

- **`WORKFLOW_CONTROL_SURFACE`**: `plugin/CLAUDE.md`, `plugin/hooks/hooks.json`, `plugin/mcp/harness_server.py`, `plugin/scripts/prewrite_gate.py`, `plugin/scripts/_lib.py`, `doc/harness/manifest.yaml`(`prewrite_gate.py:111-118`)
- **`EXEMPT_PREFIXES`**: `doc/harness/learnings.jsonl`, `doc/harness/qa`, `doc/harness/checkpoints`, `doc/harness/patterns`, `doc/harness/retros`, `doc/harness/visual-baselines`(`prewrite_gate.py:99-106`)
- **`SOURCE_EXTENSIONS`**: `.py .ts .tsx .js .jsx .go .rs .java .c .cpp .h .hpp .cs .rb .php .swift .kt .scala .sh .bash .zsh .sql .svelte .vue .astro`. `.md`, `.json`, `.yaml`, `.toml`, `.html`, `.css`는 gate 대상이 아니다(`prewrite_gate.py:90-95`).

`CONTRACTS.md`와 root `CLAUDE.md`(그리고 `doc/CLAUDE.md` 같은 다른 CLAUDE.md)는 WFCS도 아니고 소스 확장자도 아니다. 그래서 이 파일들에 대한 Write/Edit는 과제나 MAINTENANCE 유무와 관계없이 허용된다(1·4행의 workspace 오류나 symlink 이탈이 없을 때, §12.2). 단 `plugin/CLAUDE.md`는 WFCS라서 MAINTENANCE가 없으면 `workflow-control-surface`로 거부된다.

### 5.4 `no-active-task`가 적용되는 조건

gate는 strict 여부를 `yaml_field("strict_compliance_requires_delegation", manifest)`로 읽는다. `yaml_field`는 0열에서 시작하는 줄(`line.startswith(field + ":")`)만 매칭한다. 그러므로 strict 분기는 **최상위** `strict_compliance_requires_delegation: true` 줄이 있을 때만 켜진다. 이 저장소의 manifest처럼 `capabilities:` 아래에 들여쓴 형태는 효과가 없다. 테스트도 이 동작을 고정한다. 중첩 형태로는 거부하면 **안 된다**(`_lib.py:1173-1185`, `prewrite_gate.py:755-773`, `doc/harness/manifest.yaml:56-58`, `tests/test_prewrite_gate_dormant.py:74-85, 146-152`). scratch 저장소에서 중첩 키만 두고 과제 없이 `src/a.py`를 Write했더니 조용히 허용됐다.

따라서 이 저장소에서 활성 과제 없이 하는 소스 쓰기는 **열려 있거나 invalid인 `TASK__*`가 하나라도 있을 때만** 거부되고, 그 밖에는 조용히 허용된다. invalid 과제에는 잘못된 `--task-dir`로 install_verified를 돌려서 생긴 빈 디렉터리도 포함된다(§11.2). 정리하면:

- C-02(plan-first)는 활성 과제가 있을 때만 작동한다.
- gate가 보는 것은 소스 확장자뿐이다. 프롬프트 수준의 런타임(`plugin/skills/**/*.md`, `plugin/agents/*.md`, `CONTRACTS.md`, root `CLAUDE.md`, README, `plugin/.mcp.json`, `.claude/settings.json`)은 과제도 PLAN도 없이 Write/Edit할 수 있다. 막히는 것은 WFCS 6개와 보호 아티팩트뿐이다. 이런 변경을 루프로 처리하는 것은 관행일 뿐이다(`prewrite_gate.py:90-95, 111-118, 751-773`).
- manifest `maintenance_default`는 routing에만 영향을 준다. gate는 MAINTENANCE 파일만 본다.

### 5.5 REQ 링크 규칙(`C-REQ-observable-doc-required`)

**언제 걸리나.** gate는 `detect_req_need(paths=[relpath])`를 텍스트 없이 부른다. 그래서 판정은 순수한 경로 휴리스틱이고, 경로가 하나라도 맞으면 confidence는 항상 `high`다(경로만으로는 `medium`이 나오지 않는다). 경로 앞에 `/`를 붙이고 소문자로 바꾼 뒤 부분 문자열을 비교한다. 그래서 최상위 `app/`나 `api/` 아래의 모든 `.py`/`.ts`도 걸린다(`req_detector.py:30-43, 56-57, 106-127`).

| 분류 | 조각 |
|---|---|
| UI | `/components/ /pages/ /views/ /routes/ /screens/ /navigation/ /navigator/ /mobile/ /android/ /ios/ /app/`, 확장자 `.tsx .jsx .vue .svelte` |
| API | `/api/ /apis/ /controllers/ /controller/ /handlers/ /handler/ /endpoints/ /endpoint/` |
| desktop | `/desktop/ /gui/ /native/ /electron/ /tauri/ /qt/ /gtk/ /windows/ /window/ /menus/ /dialogs/` |

`.html`/`.css`/`.scss`는 `SOURCE_EXTENSIONS`가 아니라서 이 규칙까지 오지 않는다.

**어떻게 풀리나**(`prewrite_gate.py:310-379, 783-803`, `req_scaffold.py:45, 107`):

- PLAN.md 어디에든 `doc/<something>/REQ__*.md` 형태의 문자열이 하나라도 있으면 된다. surface별로 따지지 않고, 그 파일이 실제로 있을 필요도 없다.
- 또는 `doc/<area>/REQ__*.md` 안에 `source: task: <id>` 부분 문자열이 있으면 된다. 깊이 2까지 스캔하고 `doc/harness`는 건너뛴다. 부분 문자열 비교이므로 `TASK__a`가 `TASK__ab`에도 맞는다.

**MAINTENANCE와 micro도 이 규칙을 면제받지 못한다.**

develop Phase 1의 Durable Docs Preflight가 REQ를 **소스 수정보다 먼저** 쓰라고 하는 이유가 이 규칙이다.

### 5.6 scope lock

scope lock 단계는 소스 확장자 파일에만 도달한다. 그 앞 단계에서 과제 디렉터리 안 경로, `EXEMPT_PREFIXES`, (MAINTENANCE가 있는) workflow-control 파일, 소스가 아닌 파일은 이미 허용으로 끝났기 때문이다(5.1의 7-10행). 이 단계는 도달한 모든 소스 쓰기에서 실행된다. PROGRESS.md는 `forbidden_paths` 매칭에만 필요하다(`prewrite_gate.py:407-465, 556-605, 724-753, 805-819`).

- 차단 효과가 있는 것은 `forbidden_paths`뿐이다.
  - `forbidden_paths`로는 Markdown/JSON/YAML/설정 파일, 과제 디렉터리 파일, workflow-control 파일을 막을 수 없다.
  - `develop/SKILL.md:188`의 "forbidden → BLOCK"과 §10.6의 관행(CHANGELOG, CONTRACTS.md, CLAUDE.md, README를 lead의 `forbidden_paths`에 넣기)은 이런 파일에 대해 어떤 gate도 강제하지 않는다.
- `allowed_paths`는 거부 메시지에만 나타나고, `test_paths`는 파싱만 하고 쓰지 않는다.
- 절대경로, `..`, 트리 밖 항목은 `gate-parse-fail`로 기록하고 건너뛴다. 예외가 나면 쓰기를 허용한다.
- `HARNESS_DISABLE_SCOPE_LOCK=1`이면 scope lock을 건너뛰고 `<task>/audit/scope-lock-bypass.flag`를 쓴다. 이 플래그의 성질:
  - 우회할 때마다 덮어쓰므로(mode `'w'`) 마지막 우회 경로만 남는다.
  - 환경 변수 없이 scope lock 검사가 다시 돌면 gate가 "stale bypass flag"로 보고 지운다. 따라서 다음 일반 소스 쓰기 뒤에는 흔적이 사라지고, 영속 감사 기록이 되지 못한다.
  - PROGRESS.md가 있든 없든 쓰인다.
  - gate는 환경 변수를 **지우지 않는다.** 설정되어 있는 동안에는 모든 쓰기에 우회가 적용된다(`prewrite_gate.py:560-581`).

### 5.7 거부 메시지 형식

```
[gate=prewrite rule=<id> path=<rel> owner=<owner> docs=<RULE_DOCS 항목>] <사람이 읽는 문장>
escape: HARNESS_SKIP_PREWRITE=1 <retry>

↳ next action: …
↳ owner: …
↳ docs: …
```

실제 reason은 `"{tail} {human}\n{hint}"`다. 즉 대괄호 tail과 사람이 읽는 문장이 한 줄에 있다. ↳ 블록은 빈 줄 뒤에 온다. ↳ 줄은 줄마다 하나씩이고, 값이 비어 있으면 나오지 않는다(`prewrite_gate.py:471-542`, `_lib.py:939-978`). 필드별 폴백:

- **next action**: `_RULE_NEXT_ACTION`에 없거나 값이 빈 문자열이면(C-05 항목이 그렇다) `_owner_to_next_action(owner)`를 쓴다. 이 폴백은 owner가 plan-skill, receipt-lifecycle-hook, review-detail-writer일 때만 비어 있지 않다.
- **owner**: 없으면 원래 owner 문자열.
- **docs**: 폴백이 없다. C-02, invalid-active, C-05, scope lock에만 나온다. 예를 들어 workflow-control-surface, no-active-task, C-REQ 거부에는 ↳ docs 줄이 없다.

### 5.8 탈출구와 한계

- **탈출구**:
  - `HARNESS_SKIP_PREWRITE=1`: gate와 Codex protected fallback은 호출마다 읽고 지우지 않는다. 훅 환경에 설정된 동안 모든 쓰기가 우회되고 `gate-bypass`가 기록된다(`prewrite_gate.py`의 `_decision`).
  - `HARNESS_DISABLE_SCOPE_LOCK=1`(§5.6).
  - MAINTENANCE 마커.
  - `execution_mode: micro`(PLAN.md만 면제).
- **자원 한계**: 전체 payload를 읽으므로 메모리 고갈과 host의 hook 강제 종료는 여전히 fail-open 한계다(§4.6).
- **timeout**: Claude gate는 10초이며 강제 종료되면 쓰기를 허용할 수 있다. Codex는 3.0초 자식 timeout 뒤 C-05만 fallback으로 거부한다. wrapper 전체의 5초 강제 종료나 fallback 오류에서는 쓰기가 허용된다(§4.1, §4.7).
- **MAINTENANCE는 자기 인가가 가능하다.** 과제 디렉터리 안의 일반 파일이라 아무나 만들 수 있고, gate는 누가 만들었는지 구별하지 못한다. 거부 메시지의 owner인 `maintain-skill`이라는 스킬은 `plugin/skills/`에 없다.
- gate의 복구 안내는 공개 WBS action을 사용한다. no-active-task이면 open, 열린 실행에 PLAN이 없으면 plan으로 게시한다. 런타임별 tool prefix는 달라도 별도 write_plan/task_start 호출은 없다.
- scope lock의 next-action 키는 `C-09-scope-lock`이다. CONTRACTS C-09는 write focus 계약이고 scope lock은 별도의 보조 guard다(`patterns/scope-lock.md`). 판정에는 `forbidden_paths`만 쓰이고 `allowed_paths`는 영향이 없다. next action의 "Add the file to PROGRESS.md allowed_paths"만으로 거부가 풀리지 않으며, 걸린 forbidden 항목을 지우거나 좁혀야 한다(`prewrite_gate.py`의 `_handle_scope_lock`).
- [prewrite gate](patterns/prewrite-gate.md)와 [scope lock](patterns/scope-lock.md) 패턴 문서는 현재의 JSON deny, 미등록 경로 허용, 지속 환경 변수 우회, timeout 동작을 설명한다.

---

## 6. 영수증과 마감 게이트

규범: `doc/harness/patterns/ADR__consolidated-task-artifacts.md`(저장, 스키마, 게이트), `doc/harness/patterns/ADR__single-direct-codex-receipt-protocol.md`(Codex 수집).

### 6.1 전체 흐름(Claude)

```
coordinator
  │ Agent(subagent_type="harness:code-reviewer")      ← name= 금지
  ▼
SubagentStart ──▶ background_hook.py --event start ──▶ register_subagent_start
                    ├ session_id / agent_id 검증 ('default' 금지, [A-Za-z0-9_.-])
                    ├ payload agent type ──▶ lens 추론 (review-code)
                    │     SUPPORTED_LENSES 밖이면 {} (영수증 없음)
                    ├ .active_sessions/<sid>.json 바인딩, run_id == TASK.json.run_id
                    └ [flock] 이 runtime 행 검사 → started 1행 append
  … 리뷰어 작업 …
SubagentStop  ──▶ background_hook.py --event stop  ──▶ mark_subagent_stop
                    ├ sid, aid, last_assistant_message 필수
                    ├ transcript 출처 검증 (경로, 소유자, 모드, start attachment)
                    ├ [flock] 바인딩 재확인, identity·lens 일치
                    ├ review-*: 원문 ──▶ REVIEWS.jsonl, digest 확인
                    └ completed 1행 append (정규화된 verdict + compact summary)
                         │
WBS verify ◀────────────┘  하나의 스냅샷으로 review_verdict / runtime_verdict 계산
WBS close  ──▶ 같은 스냅샷의 fingerprint를 TASK.json에 기록
```

### 6.2 영수증 행 스키마

행 하나는 정확히 문자열 필드 10개를 가진다(`_lib.py:3067-3159`).

```json
{"agent_id":"<aid>","agent_type":"harness:code-reviewer","event":"completed",
 "lens":"review-code","runtime_id":"claude:<sid>:<aid>","source":"claude_hook",
 "summary":"VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nDETAIL_SHA256:<64hex>",
 "task_run_id":"<run uuidv7>","ts":"<UTC µs>","verdict":"PASS"}
```

- `event`: `started` 또는 `completed`.
- `source`는 둘 중 하나다.
  - `claude_hook`: `runtime_id = claude:<session>:<agent>`(3부분).
  - `codex_session_watcher:collaboration`: `runtime_id = codex:<root>:<call_id>:<child>`(4부분). 세 번째 요소는 root `spawn_agent`의 call_id다. 검증기 오류 메시지의 `<event>`는 자리표시 문구일 뿐이다(`codex_lifecycle_watcher.py:1054-1057`, `_lib.py:3099-3105`).
- `started` 행의 `verdict`와 `summary`는 빈 문자열이어야 한다. 호출자가 summary를 넘겨도 버려진다.
- `completed` 행의 verdict는 PASS/FAIL/BLOCKED_ENV/PENDING 중 하나다. summary는 review가 3줄, QA가 2줄이다. PENDING이면 digest 앞에 `FIRST_LINE:` 한 줄이 더 들어갈 수 있다. 첫 줄은 `VERDICT: <v>`, 마지막 줄은 `DETAIL_SHA256:<64hex>`다.
- **행 하나라도 스키마를 어기면 스냅샷 전체가 예외를 낸다.** 그러면 `wbs(action="verify")`, `wbs(action="close")`, `wbs(action="context")`가 모두 실패한다. 오류 문구는 원인에 따라 다르다(`_lib.py:3340-3366, 3378-3410`).
  - 스키마 수준 거부(모르는 필드 집합, 문자열이 아닌 값, 모르는 event, 의미 검사 실패): reader가 낡았을(stale) 수 있으니 다시 로드하라고 먼저 권한다. run 리셋을 첫 처방으로 권하지 않는다.
  - JSON이 아니거나, 객체가 아니거나, UTF-8이 아닌 줄: 일반적인 `receipt storage integrity unavailable`이 나오고 재로드 안내는 없다.

### 6.3 lens 추론

`_infer_receipt_lens(agent_type, explicit_lens)`의 순서(`_lib.py:3939-3958`):

1. 비어 있지 않은 explicit lens가 우선한다. Claude start 경로는 explicit lens를 넘기지 않는다.
2. `(?:^|[:/_-])(qa|ux)[-_:](cli|api|browser|desktop)(?:$|[:/_-])` → `qa-X`/`ux-X`
3. cli/api/browser/desktop 토큰이 있고 **소문자로 바꾼 type에 `qa`나 `ux`가 들어 있을 때만** 이 규칙을 적용한다. `ux`가 들어 있으면 `ux-X`, 아니면 `qa-X`다. 둘 다 없으면 4번으로 넘어간다(예: `api-code-reviewer`는 `review-code`, `cli-helper`는 `''`).
4. 인접 토큰 {code, review} 또는 {security, review}(끝의 `er`은 떼고 비교) → `review-code`/`review-security`
5. 그 외에는 `''`

| agent type | lens | 영수증 |
|---|---|---|
| `harness:code-reviewer`, `code_review_1`, `review_code_a` | review-code | 있음 |
| 다른 플러그인이나 사용자가 정의한 `*code-reviewer*`(예: `pr-review-toolkit:code-reviewer`, `feature-dev:code-reviewer`) | review-code | 있음(대개 PENDING으로 결속) |
| `harness:security-reviewer` | review-security | 있음 |
| `harness:qa-cli` / `-api` / `-browser` / `-desktop` | qa-* | 있음 |
| `harness:qa-linux-cli`, `linux-cli-checker`, `harness:linux-api-qa` | `ux-cli` / `ux-cli` / `ux-api` | **없음** |
| `harness:ux-*` | ux-* | **없음**(SUPPORTED_LENSES 밖) |
| `harness:developer`, ac-worker, test-author, defect-hunter, dogfooder, documentation-review, task-lead | `''` | 없음 |

- 3번 규칙은 `ux` 부분 문자열을 `qa`보다 먼저 본다. 그래서 `linux`처럼 `ux`를 포함한 토큰이 이름에 있으면 `qa`가 함께 있어도 ux-*로 추론되고, 영수증이 조용히 사라진다. 2번 규칙은 qa/ux 바로 앞에 구분자가 있어야 해서 이 경우를 구하지 못한다.
- 2번 정규식이 review 쌍보다 먼저 검사되므로, 섞인 이름은 review보다 QA로 묶일 수 있다.
- 추론은 이름의 토큰만 보고 플러그인 네임스페이스는 보지 않는다. 그래서 harness verdict 계약을 따르지 않는 다른 플러그인의 reviewer도 review-code 영수증을 만들고, 대개 PENDING으로 결속된다(`subagent_lifecycle.py:114-172`).

**`name=`을 주면 대개 영수증이 없다.** Claude Agent 도구에 `name=`을 넘기면 display name이 agentType 자리를 차지해 대개 lens를 잃는다. 이름이 우연히 lens를 담고 있으면(`qa-cli-1` → `qa-cli`) lens가 추론되어 영수증이 남는다. 그래서 이 문제는 간헐적으로 나타났다. 2026-09-10 측정에서는 이름을 준 spawn 3번 중 영수증이 0개였다. 흔적은 learnings의 `background_hook:binding-miss`에 reason `named-spawn-shadows-agent-type`으로 남는다(`subagent_lifecycle.py:114-174`, `plugin/skills/develop/parallel-fanout.md:69-104`). lane 구분은 prompt 본문에 적는다.

### 6.4 start 경로

receipt 트랜잭션 안에서 이 runtime의 기존 행을 본다(`subagent_lifecycle.py:548-597`).

- 행 없음 → `started` 1행 append.
- 같은 `started` 1행이 있음 → `duplicate_start`, 아무것도 쓰지 않음.
- started+completed 쌍이 있음 → 재개(SendMessage나 wake-up)로 본다. 아무것도 쓰지 않고 reason은 `resumed-after-completion`.
- 그 밖의 모양 → 예외.

append는 `record_subagent_receipt`가 맡는다(`_lib.py:4145-4287`).

1. 행을 직접 만든다. ts는 UTC µs, task_run_id는 TASK.json에서 가져온다.
2. 스키마를 확인하고, run이 그대로이고 status가 closed/blocked/invalid가 아닌지 다시 본다.
3. `O_APPEND|O_NOFOLLOW`(생성 시 0644)로 정렬 키 JSON 한 줄을 쓰고 fsync한다.
4. 크기와 identity를 재확인한다. 실패하면 truncate하거나 unlink한다.

**영수증을 쓸 수 있는 호출자는 allowlist로 정해져 있다.** `subagent_lifecycle.register_subagent_start`/`mark_subagent_stop`(claude_hook)과 `codex_lifecycle_watcher.watch`(Codex)뿐이다. `record()`는 호출 스택 프레임을 확인해서 다른 호출자면 `PermissionError`를 낸다. import 시점 바인딩 검사는 두 가지 오류로 나뉜다(`_lib.py:3961-4154, 4072-4099`).

- 구조 검사(loader, 모듈 경로, 소유자, nlink, 모드, 후보 하나)가 모두 통과했는데 새로 컴파일한 코드와의 비교만 다를 때: `StaleBytecodeCacheError`.
- 그 밖의 불일치: `PermissionError('receipt adapter binding requires its canonical module import')`.

### 6.5 stop 경로와 transcript 출처 검증

1. `session_id`, `agent_id`, 비어 있지 않은 `last_assistant_message`가 필요하다. 없으면 reason `stop-identity-incomplete`, 바인딩 실패면 `session-task-binding-unresolved`다(`subagent_lifecycle.py:627-634`).
2. 이 runtime/run의 hook-owned `started` 행이 하나 있으면 그 agent type을 빌린다(`hook_start_type`). 양쪽 lens가 모두 지원 lens인데 서로 다르면 빌리지 않는다(`hook-start-agent-type-mismatch`)(`subagent_lifecycle.py:600-678`).
3. **transcript 경로.** `$CLAUDE_CONFIG_DIR|~/.claude/projects` 아래에서 `<sid>/subagents/agent-<aid>.jsonl`로 끝나야 한다. 디렉터리는 모두 `O_NOFOLLOW` 디스크립터로 열고, uid 소유이며 group/world 쓰기가 불가능해야 한다. leaf는 일반 파일, nlink 1, 64 MiB 이하여야 하고, 읽는 동안 (dev, ino, size, mtime, ctime)가 변하지 않아야 한다(`subagent_lifecycle.py:223-295`).
4. **transcript 내용.** 비어 있지 않은 모든 줄이 JSON이어야 한다. 기록 시점에 잘린 줄이 있으면 `exception:JSONDecodeError`로 거부된다. 다른 agentId/sessionId가 하나라도 있으면 `foreign-agent-or-session-in-transcript`로 거부된다(`subagent_lifecycle.py:309, 333-337`).
5. **start attachment**(agent type을 transcript에서 가져온다). 우선순위(`subagent_lifecycle.py:296-411`):
   - 정규 `hookName: SubagentStart` 배너 `Agent <type> started (<aid>)`. run cutoff 이후 시각이어야 한다.
   - `hookName: SubagentStart:<type>` 줄.
   - `hook_start_type` 대체값.
   - 모두 없으면 `no-canonical-start-attachment`로 거부.

   run cutoff보다 앞선 attachment는 `start-precedes-task-run`으로 거부된다. 두 attachment 모양은 모두 제3자 플러그인(oh-my-claudecode)의 SubagentStart 출력에서 나온다. harness start 훅은 아무것도 출력하지 않는다. 그래서 그런 플러그인이 없는 보통 설치에서는 harness `started` 행을 통한 `hook_start_type`이 정상 경로다. hook `started` 행이 없고(stop만 오는 런타임) attachment도 없으면 항상 `no-canonical-start-attachment`로 거부된다(ADR `:195-206`).
6. **최종 텍스트는 비교하지 않는다.** 런타임의 transcript flush와 stop 시점이 겹칠 수 있어, verdict는 검증된 stop payload의 `last_assistant_message`에서 읽는다. C-14도 stop-only fallback의 identity·출처 검증과 이 verdict 경계를 명시한다(`subagent_lifecycle.py:412-421`, `CONTRACTS.md` C-14).
7. **기록**(트랜잭션 안, `subagent_lifecycle.py:698-766`).
   - started만 있음 → savepoint 안에서 completion append.
   - started와 completion이 이미 있음 → 정규화 결과가 같으면 `duplicate_stop`, 다르면 `completion-already-recorded`. **재개된 에이전트는 첫 completion을 바꿀 수 없다.**
   - 행 없음(stop만 옴) → 추론한 `started`와 `completed`를 한 savepoint에 append.
   - 예외가 나면 `receipt_pending`을 돌려준다. 결과가 비어 있지 않으므로 binding-miss 흔적은 남지 않고, 자동 재시도도 없다. started 행만 남으면 그 lens는 계속 "진행 중"으로 보인다(§6.7).
   - `agent_type` 문자열이 start 행과 다르면(같은 lens라도) `conflicting Claude lifecycle identity` → `receipt_pending`.

### 6.6 verdict 결속(binding)

완료 원문은 compact summary로 줄어든다(`_lib.py:3429-3855`).

- **1행**: `VERDICT: PASS|FAIL|BLOCKED_ENV` 뒤에 선택적 공백이나 탭이 오고, 그다음 줄 끝이거나 `— – - : . ! ? ( ) [ ]` 중 하나가 와야 결속된다. `VERDICT: PASS — report complete.`는 결속되지만 `VERDICT: PASSING`, `VERDICT: PASS if …`, `VERDICT: PASS, no blockers found`(쉼표), 세미콜론은 결속되지 않는다(`_lib.py:3450-3452, 3676-3684`). 런타임의 `[harness: subagent output matched instruction-shaped pattern(s): ` 고지와 뒤따르는 빈 줄은 건너뛴다.
- 뒤쪽에 **다른 verdict를 적은 단독 `VERDICT:` 줄**(또는 숫자가 다른 `FINDING_COUNTS:`)이 있으면 결과 전체가 무효가 된다. 코드 펜스나 들여쓰기 안에 있어도 마찬가지다. 산문 속에서 언급하는 것은 괜찮다.
- **review lens 2행**: `FINDING_COUNTS: FIX_NOW=n INVESTIGATE=n OPTIONAL=n`. 모순 규칙은 한 곳에 있다(`_lib.py:3713-3735`).
  - PASS는 FIX_NOW>0일 때만 모순이다. INVESTIGATE와 OPTIONAL은 PASS와 모순되지 않는다.
  - FAIL은 FIX_NOW>0이어야 한다.
  - BLOCKED_ENV는 INVESTIGATE>0이어야 한다.
- **review-code 3행(선택)**: `REVIEW_DETAIL: {json}`이 있으면, 거기서 암시되는 verdict(blocker → BLOCKED_ENV, findings → FAIL, 없으면 PASS)와 counts(len(findings), blocker면 1, 0)가 1·2행과 맞아야 한다. 3행이 없으면 legacy로 허용한다.
- 결속에 실패하면 **PENDING**이다. review counts 자리에는 이유가 다음 규칙으로 남는다(`_lib.py:3654-3702, 3842-3851`).
  - counts 줄이 읽히고, 1행 verdict가 읽히거나 FIX_NOW>0이면 → 실제 counts 줄. 읽히는 verdict와 counts가 모순될 때(FIX_NOW=0인 FAIL, INVESTIGATE=0인 BLOCKED_ENV)와 `REVIEW_DETAIL` 불일치일 때도 여기에 해당한다.
  - 1행 verdict만 읽힘 → `UNREADABLE <TOKEN>`.
- 위치상 결속이 없을 때는 본문 전체의 단독 줄을 훑는다. 단독 `VERDICT:` 줄이 모두 같은 FAIL/BLOCKED_ENV 하나일 때만 → `UNREADABLE <TOKEN>`. 그렇지 않고 단독 counts 줄이 모두 같은 하나이며 FIX_NOW>0이면 → 그 줄. 서로 다른 verdict 줄이나 counts 줄이 섞여 있으면 아무것도 고르지 않는다. 또 1행이 결속되지 않았어도 FAIL/BLOCKED_ENV가 아닌 verdict를 스스로 적었다면(예: `VERDICT: PASS, no blockers found`) 이 훑기를 아예 하지 않는다. 코드 펜스 안의 `VERDICT: FAIL` 예시가 PASS를 뒤집지 않게 하려는 것이다.
  - 그 외 → `INVALID`.
- PENDING을 durable append한 뒤에는 `receipts:verdict-unbound` 흔적을 남긴다. 원인만 적고 에이전트 텍스트는 복사하지 않는다.

**verdict 형식 계약은 에이전트 정의가 소유한다.** spawn prompt에 형식을 다시 적거나 옮겨 적으면 안 된다. 그러면 PENDING으로 결속된다(`doc/harness/REQ__lens-verdict-contract-ownership.md`, `plugin/skills/develop/SKILL.md:316-319`).

### 6.7 lens별 유효 completion

현재 run_id의 행만 lens별로 묶는다(`_lib.py:4303-4518`).

- 그 lens의 가장 새 행이 `started`면(rerun 진행 중) **completion이 없는 것으로 본다.** 이미 끝난 PASS도 rerun 도중에는 PENDING으로 보인다.
  - **completion을 받지 못한 `started` 행은 만료되지 않는다.** 이런 행이 생기는 대표적인 경우는 다음과 같다(전부는 아니다): Claude stop이 identity·바인딩·출처 검증 중 하나에서 빈 결과로 끝났거나, `receipt_pending`을 돌려줬거나(자동 재시도 없음), stop 훅이 3초 timeout(예: install_verified가 receipt 락을 쥔 동안, §6.15)에 걸렸거나 아예 오지 않았거나, Codex lifecycle이 완료 전에 무효화됐다(terminal 행을 쓰지 않음).
  - `_rerun_in_flight`에는 만료 시간이 없다. 30분 `DEFAULT_STALE_SECS`는 `active_records`에만 적용된다.
  - 그래서 그 lens는 계속 "진행 중"으로 보이고, 앞선 PASS/FAIL을 모두 가린다. 기계적으로는 그 lens를 새로 스폰해 완료시켜야 가려진 상태가 풀린다. 다만 새 스폰은 수정 뒤 재검증처럼 **다른 이유로 재실행이 필요할 때만** 정당하다. 영수증을 얻으려고 다시 돌리는 것은 Missing receipt policy가 금지한다(`plugin/skills/run/SKILL.md:9-30`).
  - review-before-QA 순서 규칙 때문에, review를 정당한 이유로 새로 스폰하면 QA도 다시 돌려야 한다(`_lib.py:4521-4547, 4620-4633`). 위 항목들(만료 없음, `receipt_pending`, Codex 무효화)의 근거는 `_lib.py:4313-4322, 4332, 4589`, `subagent_lifecycle.py:83, 762-766`, `codex_lifecycle_watcher.py:1096-1101, 1268, 1276`이다.
- 유효한 completion의 조건:
  - 같은 (source, task_run_id, runtime_id, agent_id, agent_type, lens)를 가진 `started` 행이 더 앞에 있다.
  - 그 identity의 completion이 **정확히 하나**다. 두 개면 둘 다 무효가 된다(`unpaired`). Codex watcher는 이 규칙을 일부러 이용해 lifecycle을 무효화한다.
- 최신부터 거슬러 보면서 처음 나오는 유효한 non-PENDING completion이 이긴다. `inconsistent` PENDING(읽히는 FAIL/BLOCKED_ENV, 또는 살아남은 counts 줄)도 이긴다. `shape` PENDING은 다른 것이 없을 때만 대신 쓰인다.

### 6.8 review_verdict와 runtime_verdict

```
receipt_runtime_verdict:
  BLOCKED.md 유효?                        → BLOCKED_ENV
  review_verdict ∈ {FAIL, BLOCKED_ENV}?    → 그대로
  required QA 중 FAIL? / BLOCKED_ENV?      → 그대로 (review PASS가 없어도, 순서와 무관)
  review_verdict != PASS?                  → PENDING
  review_index = required review 유효 completion의 최대 stream index
  모든 required qa-*:
     유효 completion이 PASS이고 그 started 행 index > review_index?  → PASS
  그 외                                     → PENDING

receipt_review_verdict (required review-* 기준):
  FAIL 하나라도 → FAIL; BLOCKED_ENV → BLOCKED_ENV; 빠진 lens → PENDING; 모두 PASS → PASS
```

근거: `_lib.py:4521-4633`.

순서 예:

```
index:   0              1                   2             3
         started        completed           started       completed
         review-code    review-code PASS    qa-cli        qa-cli PASS
                        └ review_index = 1  └ 2 > 1 이므로 유효 → PASS
```

따라서:

- QA의 `started` 행이 필요한 모든 review lens의 유효 completion 중 가장 늦은 것보다 먼저 기록되면 — 리뷰가 끝나기 전에 QA를 띄우거나 리뷰와 동시에 띄운 경우 포함 — 그 QA의 PASS는 절대 인정되지 않는다.
- QA 뒤에 리뷰를 다시 돌리면 QA의 `started`가 최신 리뷰 completion보다 앞서게 된다. **QA도 다시 돌려야 한다.**
- FAIL/BLOCKED_ENV 검사는 순서를 보지 않는다. 수정한 뒤 review를 새로 PASS시켜도, 옛 QA FAIL이 남아 있으면 새 QA를 스폰하기 전까지 runtime은 FAIL이고 next_action도 계속 "QA receipt reports FAIL"이다. 새 QA가 도는 동안(그 lens의 최신 행이 `started`)에는 옛 FAIL이 가려져 PENDING이 되고, 새 QA가 결속된 verdict를 내야 FAIL이 대체된다. 새 QA가 PENDING으로 끝나면 옛 FAIL이 다시 이긴다.
- lens가 낸 BLOCKED_ENV는 runtime_verdict만 BLOCKED_ENV로 만든다. 과제는 open으로 남고 `wbs(action="close")`는 거부된다. next_action의 안내대로 코디네이터가 직접 `wbs(action="block")`를 불러야 한다(`_lib.py:4600-4633, 4898-4918`).
- `wbs(action="plan")`으로 run 도중 `required_lenses`를 바꿔도 run_id는 그대로다. 기존 영수증은 새 lens 집합 기준으로 다시 평가된다. 예를 들어 `review-security`를 추가하면 그 lens가 돌 때까지 review는 PENDING이다.

### 6.9 WBS verify

`wbs(action="verify", wbs_id=...)`는 TASK.json을 검증하고 **스냅샷 하나**로
review/runtime verdict와 compact context를 계산한다. 원시 영수증 행은 내보내지
않는다. task_dir, runtime_verdict, review_verdict, next_action,
missing_for_close, report_path, required_review_lenses, required_qa_lenses와
watcher_status를 반환한다. WBS wrapper는 같은 wbs_id도 반환한다.

verify는 테스트 명령을 실행하지 않는다. 공개 입력에 run_commands를 넣으면
거부한다. 특정 성과 assertion은 check(node_id, timeout)로 명시적으로 실행한다.
전체 suite와 실제 동작 검증은 독립 QA가 수행하고, review PASS 뒤 시작된 QA의
관측된 completion이 close 증거가 된다. verify_runner.py는 별도 내부/진단 실행기이며
그 참고 결과만으로 receipt verdict나 close를 PASS로 만들 수 없다.

PENDING이고 `receipts_recordable`이 False가 아니면 next_action은 다음 순서로 조립된다(`harness_server.py:1707-1776, 822-826`).

1. plan-first 선행 조건(있으면)
2. nonparsing note(아래 표)
3. `RECEIPT_PENDING_VERIFY_NEXT_ACTION`(`TRUST_BOUNDARY`와 `attestation_endgame()` 포함)

**nonparsing 진단.** `nonparsing_completion_lenses`는 lens마다 다섯 가지 진단 중 하나를 붙인다. 선언하지 않은 lens(예: review-security)도 진단한다. PASS 경로의 next_action에는 `stale_followup`만 붙는다(`_lib.py:4636-4836, 4930-4951`).

| 진단 | 의미 | 조치 |
|---|---|---|
| `shape` | verdict 블록 위치나 형태 오류, 충돌, interim stop | 메시지를 보내지 말고 새로 1회 스폰. 새 스폰도 결속에 실패하면 missing-attestation 경우로 가고 세 번째로 돌리지 않는다 |
| `shape_named` | Claude에서 agent_id 자리에 display name이 들어감 | `name=` 없이 다시 스폰. 한 번 실패했다고 missing-attestation으로 가지 않는다 |
| `inconsistent` | PASS와 FIX_NOW>0이 함께 있는 등 | finding을 먼저 처리하고 새로 스폰 |
| `unpaired` | start가 없거나 completion이 중복 | 훅 트리가 SubagentStart를 기록하는지 확인 |
| `stale_followup` | 결속된 verdict는 유효하고, 뒤따른 읽을 수 없는 후속만 있음 | 결속된 verdict를 되살리려는 재실행은 불필요. 후속이 같은 verdict의 되풀이인지 확인하고, 새 finding이었다면 그 lens를 새로 스폰한다 |

`missing_for_close` 항목은 PLAN.md(micro 제외), `completed review verdict…`(review가 PASS가 아닐 때), `completed QA verdict…`(runtime이 PASS가 아닐 때)다. TASK.json에 선언된 lens에서만 만들어지고 diff는 보지 않는다(`_lib.py:4862-4883`).

`wbs(action="verify")`는 스냅샷을 읽는 동안만 락을 잡고, `wbs(action="close")`는 판단부터 기록까지 락을 계속 잡는다(`harness_server.py:1721` 대 `1793`).

### 6.10 `wbs(action="close")`

```
1. control 유효 & status == open  (아니면 status별 재개 안내와 함께 거부)
2. close는 세션 및 receipt 트랜잭션 안에서 WBS 통합 증거를 재검증한다.
3. 스냅샷 1개 → emit_compact_context → missing_for_close 비어 있어야 함
4. 같은 스냅샷의 fingerprint 계산
5. control 재확인 (여전히 open)
6. publish_task_close: close_receipt_fingerprint 기록
7. clear_active_marker(strict, session_id 없음)
     → <current_session_id()>.json(Claude 에서는 대개 default.json) 삭제
     → legacy .active 는 td 를 가리킬 때만 삭제
     → resolve_active_task_dir 가 더 이상 td 를 가리키지 않음을 확인
8. 도중 오류 → TASK.json 과 마커 복원
→ closed: true
```

close의 완료 권위는 순서가 맞는 review/QA 영수증이다. 별도의 HEAD·dirty·CHECKS 영수증 게이트는 없다. 다만 WBS progress는 소스 fingerprint를 읽고, 연결된 work pack이 있으면 close 전에 destination branch·ancestry·보관 증거를 검증한다. 설치는 검증된 delivery 절차가 별도로 수행한다(§11.2).

### 6.11 `wbs(action="block")`와 두 고정 쌍

`wbs(action="block")`의 동작(`harness_server.py:1856-1918`):

- status가 `open`이어야 한다. blocked 과제는 먼저 기본 `wbs(action="open")`로 재개해야 한다. `wbs(action="close")`도 blocked 과제를 거부한다.
- `blocked_reason`과 `unblock_condition`은 비어 있으면 안 되고, 각각 UTF-8 122880바이트 이하다.
- receipt 락 안에서 BLOCKED.md(Blocked Reason / Unblock Condition / Resume / Blocked At)를 원자적으로 쓰고 마커를 strict하게 지운다. 실패하면 둘 다 되돌린다.
- `runtime_verdict: BLOCKED_ENV`를 반환한다. 과제를 닫지는 않는다.
- 별도 Goal 장부나 child 상태를 읽거나 변경하지 않는다(§9).

필요한 lens가 실제로 돌아 결과를 냈는데도 영수증이 끝내 없을 때 쓰는 **고정 문구 쌍이 두 개** 있다. 둘 다 `plugin/scripts/_lib.py`에만 있다(`_lib.py:53-59, 152-236`). C-17은 산문에 두 번째 사본을 두지 말라고 하므로 **이 문서도 문구를 옮겨 적지 않는다.** 실제 문구는 `wbs(action="verify")`의 next_action에서 그대로 복사한다.

| 쌍 | 상수 | 언제 |
|---|---|---|
| missing-attestation | `ATTESTATION_*` | 이 run에 영수증 행은 있는데, 실제 review PASS → 실제 QA PASS → `wbs(action="verify")` 1회 뒤에도 필요한 completion이 없을 때 |
| empty-stream | `NO_RECEIPTS_*` | 실제 review PASS → 실제 QA PASS → `wbs(action="verify")` 1회 뒤에도 필요한 completion이 없고, 이 run에 **어떤 종류의 영수증도 없을 때** |

- 어느 쌍이든 lens가 실제로 돌아 결과를 내기 전에는 쓸 수 없다. 돌지 않은 lens는 blocker가 아니다.
- 영수증이 없는 상태에 missing-attestation 쌍을 쓰면 BLOCKED.md에 사실이 아닌 문장이 남는다. 두 번째 쌍이 생긴 이유가 이것이다(`doc/harness/REQ__gate-does-not-demand-impossible-evidence.md`).
- 영수증을 얻으려고 lens를 다시 돌리지 않는다. 영수증이 없는 lens 결과는 NON-ATTESTING으로 표시한다. 다만 review PASS는 QA 1회를 진행할 근거가 된다(`plugin/skills/run/SKILL.md:14-36`).
- **capability 마커 예외.** `doc/harness/.receipt-capability-broken`이 있으면 두 쌍 중 어느 것도 쓰지 말고 외부 blocker 분기로 간다. 즉 구체적인 외부 원인과 해제 조건(런타임 복구·재설치)을 적어 `wbs(action="block")`로 주차한다(`plugin/skills/run/SKILL.md:15-24`, `plugin/CLAUDE.md:104`). 그런데 바로 이 상태에서 MCP가 내는 `RECEIPT_UNAVAILABLE_NEXT_ACTION`에는 여전히 `attestation_endgame()`, 즉 두 쌍이 들어 있다(`harness_server.py:809-820, 835-851`). 산문과 코드가 어긋나는 곳이다.

재개는 기본 `wbs(action="open")`로 한다(증거 유지). `fresh_run=true`는 증거를 버린다.

### 6.12 REVIEWS.jsonl과 조회 도구

- 행은 정확히 `{detail_sha256, detail}`이다. 파일 모드는 0600이고, 상한은 detail당 2 MiB, 과제당 16 MiB다. 락 안에서 append하고 fsync한다. 같은 digest를 다시 쓰려면 본문이 같아야 한다. **verdict, fingerprint, close 경로는 이 파일을 읽지 않는다**(`_lib.py:2573-2575, 2774-3064`).
- review-* completion을 기록할 때는 원문을 먼저 REVIEWS.jsonl에 넣고, 그 digest가 summary의 `DETAIL_SHA256`과 같은지 확인한다. QA 원문은 digest만 남는다.
- **상한의 결과.** `record()`는 review detail을 영수증보다 먼저 append한다. 그래서 2 MiB를 넘는 review-* final이나 REVIEWS.jsonl을 16 MiB 넘게 만드는 final은 append 자체가 실패한다. Claude에서는 `receipt_pending`으로 나타나고 review completion은 기록되지 않는다. RECEIPTS.jsonl에도 16 MiB 상한(`_RECEIPT_STREAM_MAX_BYTES`)이 있어서 그 이상은 append를 거부한다(`_lib.py:2794-2795, 2947-2948, 4194-4197, 4222`).
- `plugin/scripts/review-log`: stdin(2 MiB 이하)을 저장하고 `DETAIL_SHA256:<digest>`를 출력한다. 과제가 열려 있어야 한다.
- `plugin/scripts/review-read <digest>`: detail 하나만 출력한다. 없으면 exit 3, 무결성 실패면 exit 4. 목록 모드나 latest 모드는 없다.

### 6.13 `watcher_status`와 capability 마커

`watcher_status`는 **참고 정보일 뿐**이다(`harness_server.py:566-851`). `receipts_recordable`은 세 값을 가진다.

- `False`: 실패가 실제로 관찰됐다. 등록 없음이나 실패, watcher 오류, Codex manager 미실행, Claude의 `.receipt-capability-broken` 마커 존재.
- `None`: 알 수 없다. 정확한 Codex identity 없음, worker 상태를 읽을 수 없음, 또는 휴리스틱 경고(`receipt_capability_warning`)가 있으면서 이 run에 영수증이 아직 하나도 없을 때. 이 run에 영수증이 있으면 그 경고 때문에 `None`이 되지는 않는다(Claude에서는 `True`로 남는다).
- `True`: 그 밖.

`_gate_next_action`은 값이 `False`일 때만 spawn 안내를 `RECEIPT_UNAVAILABLE_NEXT_ACTION`으로 바꾼다. 진단 파일 `doc/harness/.watcher-diagnostics.json`은 같은 세션 identity가 찍혀 있고 12시간 이내일 때만 읽는다. 이 값은 외부에서 조작될 수 있다. 그래서 PASS를 만드는 것은 hook-owned `RECEIPTS.jsonl` 행뿐이다.

### 6.14 Codex의 차이

```
WBS open/context 성공 ──PostToolUse──▶ register_task_result
     └ .active_sessions/<thread>.json (legacy 없음) + ensure(): coordinator rollout 을 현재 offset 으로 등록
spawn_agent ──PreToolUse──▶ review 이름 검증, 등록 복구
MCP 안의 WatcherManager 가 rollout 을 tail:
  started  : coordinator spawn_agent function_call (task_name → lens, run cutoff 이후)
           + SubAgentActivity item_completed kind=started (child thread, /root/<path>)
           + structured function_call_output (agent path)
           + child rollout: 인증된 coordinator의 direct-child session_meta 1개, NEW_TASK 경계 1개
           → 모두 맞고 바인딩이 그대로면 started 행
             (agent_id=agent_path, agent_type=task_name, runtime_id codex:<coordinator-thread>:<call_id>:<child>)
  completed: coordinator 에 전달된 FINAL_ANSWER == child 의 task_complete last_agent_message
           → 같은 record() writer 로 completed 행
           root/child 불일치, 완료 전 바인딩 변경
             → 행을 쓰지 않음 (메모리에서 invalid 표시만). started 행만 남아
               lens 가 계속 '진행 중'으로 보임 (§6.7)
           child final 은 맞지만 정확한 verdict 가 없음
             → child final 을 summary 로 한 PENDING completion
           completion 기록 뒤 무효화 (예: 중복·모호한 root 전달)
             → 두 번째 PENDING completion (review lens 는 합성 FIX_NOW=1 counts 줄)
             → 같은 identity 의 completion 이 두 개 → 둘 다 무효
```

근거: `codex_hook_registration.py:213-330`, `codex_lifecycle_watcher.py:544-653, 844-1383, 1087-1145, 1257-1290, 1385-1397`, `_lib.py:4487-4505`.

등록 파일(version 13, owner `codex_root_hook`)의 offset은 한 번 쓰면 바뀌지 않는다.

**Codex에서 hint와 마커.** hint는 exact 바인딩에 사용하지 않는다. 명시적 workspace 또는 exact 환경 thread ID 없는 main 호출은 성공한 task 결과와 trusted native PostToolUse의 coordinator 신원으로 `.active_sessions/<thread>.json`과 watcher 등록을 만든다. exact `CODEX_THREAD_ID`가 있는 main 호출은 공유 세션 잠금 안에서 다른 worktree의 열린 바인딩을 배제한 뒤 기존 eager 바인딩을 유지한다. hint/default/legacy 마커는 영수증 권한이 아니다. 중첩 lead는 모든 조상 spawn/activity/output와 native 메타데이터를 검증하고, review/QA child는 그 coordinator에 직접 속해야 한다. native cwd는 main에 남아도 된다. 같은 coordinator의 동시 열린 바인딩은 fence된다. 상세 규약은 [Codex receipt ADR](patterns/ADR__single-direct-codex-receipt-protocol.md)을 따른다.

> 코드에서 도출, 테스트 없음: watcher는 `ux-` lens를 받아들이지만 `record()`는 SUPPORTED_LENSES 밖이라 거부한다. 3번 재시도한 뒤 sticky worker error로 건너뛰는데, 이때 `receipts_recordable`이 False로 바뀔 수 있다(`codex_lifecycle_watcher.py:863, 1174, 1576-1598`).

### 6.15 자주 걸리는 영수증 함정

- lens를 `name=`으로 스폰하면 대개 영수증이 없다(§6.3).
- 끝난 lens를 SendMessage나 wake-up으로 재개해도 아무것도 쓰이지 않는다. 수정 후 재검증은 **새 lens를 스폰**해서 한다(`doc/harness/REQ__subagent-lifecycle-receipt-boundaries.md`).
- 자기 백그라운드 명령을 기다리려고 턴을 끝낸 lens는 SubagentStop이 일찍 불려서 중간 텍스트가 PENDING completion으로 기록된다. reviewer와 QA는 명령을 foreground로 돌린다(`plugin/agents/code-reviewer.md:223-226`).
- 같은 lens에 영수증을 만드는 리뷰어를 여러 개 동시에 돌리지 않는다. 벽시계 순서로 마지막 completion이 이기므로 FAIL이 PASS로 덮일 수 있다(`plugin/skills/develop/parallel-fanout.md:172-194`).
- completion 없이 남은 `started` 행은 만료되지 않고 그 lens의 앞선 결과를 가린다. 새 스폰으로 풀리지만, 재실행이 다른 이유로 필요할 때만 그렇게 한다. 영수증만을 위한 재실행은 금지다(§6.7, §6.11).
- 형식이 틀린 행 하나가 스트림 전체를 막는다(§6.2).
- install_verified가 receipt 락을 쥔 동안 끝난 lens는 3초 timeout으로 영수증을 잃는다(§4.1).
- `active_records`는 호출하는 곳이 없다. 유일한 소비자였던 `stop_gate.py`가 2026-09-23에 삭제됐다.

---

## 7. 스킬 흐름

### 7.1 스킬 목록

`†`는 Codex payload로 복사되는 공용 보조 문서(§2.2)다. setup 보조 파일은 모두 Codex `skills/setup/`으로 복사된다.

| 스킬 | 역할 | 사용자 호출(Claude) | 소유 문서 |
|---|---|---|---|
| `harness:run` | 수명주기 오케스트레이션 | false(모델이 라우팅으로 호출) | `plugin/skills/run/SKILL.md` + self-improvement.md† |
| `harness:plan` | compact/full 계획, `wbs(action="plan")` | false | `plugin/skills/plan/SKILL.md` + intake.md†, review-phases.md†, decision-principles.md†, write-artifacts.md† |
| `harness:develop` | 구현, 리뷰, QA, 설치, close | false | `plugin/skills/develop/SKILL.md` + parallel-fanout.md, browser-verification.md, quality-audit-pipeline.md†, verification-gate.md†, fix-first-pattern.md†, hypothesis-driven-debugging.md†, runtime-smoke.md†, test-failure-triage.md† |
| `harness:batch` | Claude 공개 worktree 병렬 스킬; Codex는 run에서 internal batch로 라우팅 | **true** | `plugin/skills/batch/SKILL.md`, `plugin-codex/internal-skills/batch/SKILL.md` |
| `harness:setup` | 부트스트랩, 복구, 업그레이드 | **true** | `plugin/skills/setup/SKILL.md` + bootstrap.md, verify-report.md, project-interview.md, repo-census.md, templates/CONTRACTS.md |
| `plan-ceo-review`, `plan-eng-review` | full plan의 방법론(코디네이터가 읽음) | false | 각 SKILL.md(eng는 + rubrics-threat-rollback.md†) |
| `plan-design-review`, `plan-devex-review` | 계획 파이프라인에 연결되어 있지 않음 | false | 각 SKILL.md(devex는 + dx-hall-of-fame.md†) |

Codex에서 사용자에게 보이는 스킬은 `plugin-codex/skills/{setup,run}`뿐이다. `internal-skills`는 모두 `user-invocable: false`다. 근거: 각 SKILL.md frontmatter(`run/SKILL.md:5`, `plan/SKILL.md:5`, `develop/SKILL.md:5`, `setup/SKILL.md:9`, `batch/SKILL.md:5`), `tests/test_skill_visibility.py:9-60`.

goal-queue 스킬은 제거됐다. 테스트는 두 트리(`plugin/skills/goal-queue/`, `plugin-codex/internal-skills/goal-queue/`)에 SKILL.md가 없다는 것만 확인한다. 로컬에 남은 빈 디렉터리는 Git이 추적하지 않는 잔재다(`tests/test_codex_public_run_skill.py:105-106`).

### 7.2 run

```
Phase 0   재개 판정: PLAN.md 없음 → Plan / PLAN 있고 PASS 아님 → Develop/Verify /
          PASS 이고 missing_for_close 비어 있음 → Close.
          Native Goal만 활성이면 같은 objective의 WBS를 연다.
Phase 1   WBS open
Phase 2   Skill(harness:plan)
Phase 3   Skill(harness:develop)
Phase 4   verify 복구 (develop 이 close 전에 돌아왔을 때만)
            FAIL → A) 수정  B) Override — accept current state  C) abort
            최대 3 cycle, 이후 DONE_WITH_CONCERNS
Phase 4.5 health.py --dry-run (health 스냅샷)
Phase 5   WBS close
이후      self-improvement → (harness 저장소) 커밋 → 완료 보고
```

근거: `plugin/skills/run/SKILL.md:63-259`.

- **Phase 4의 선택지는 게이트를 바꾸지 못한다.** `wbs(action="close")`는 여전히 runtime PASS를 요구한다. 그래서 B(Override), C(abort), DONE_WITH_CONCERNS 어느 것도 과제를 닫지 못한다. 과제는 open으로 남거나 `wbs(action="block")`로 주차해야 한다(`run/SKILL.md:175-184, 257-259`, `harness_server.py:1797-1804`).
- **브라우저 QA 규칙**(run Phase 4 복구 경로에 적힌 규칙): Phase 4는 실제로 돌지 않았거나(unrun) FAIL했거나 stale한 lens만 다시 스폰한다. 그런 lens를 다시 돌릴 때, manifest `qa.browser_qa_supported: true`이고 diff에 프론트엔드 파일이 있으면 qa-browser를 반드시 포함한다. qa-browser가 이미 실질 final을 돌려주었고 영수증만 없다면 그 lens는 unrun이 아니다. 따라서 다시 돌리지 않고 Missing receipt policy를 따른다(`run/SKILL.md:9-37, 138-165`). 다만 close 게이트는 TASK.json에 선언된 lens만 보므로, qa-browser가 게이트가 되려면 `wbs(action="plan")` 때 선언해야 한다(`_lib.py:4576-4578`). 평소의 QA는 develop Phase 7이 맡는다.

### 7.3 plan: compact와 full

**Phase 0**(`intake.md:7-185`):

1. spawned 세션 감지(`HARNESS_SPAWNED`)
2. PLAN.md Review Status로 복구
3. learnings 최근 5행
4. `wbs(action="open")`(0.2. 이미 열린 과제면 `preserved`와 `TASK_START_RUN_PRESERVED` 경고가 정상적으로 나온다)
5. task pack과 git log/diff
6. base 브랜치
7. (대상이 너무 모호할 때만) 선행 조건 제안
8. 복원점(0.5)
9. UI 범위 키워드(2개 이상 일치)
10. **0.7 절차 선택**

**compact 조건**: 요청 범위가 한정되고, 모호하지 않고, 영향 범위가 작고, 수락·테스트·범위 결정이 모두 명백해야 한다. 사용자가 full plan을 요청했거나, 분류 입력이 없거나 불확실하거나, 아래 escalation 계열 중 하나라도 해당하면 full이다. 모르면 full이다. 파일 수만으로는 저위험을 증명할 수 없다(`intake.md:153-183`).

- 보안/인증/권한/비밀
- 데이터/스키마/마이그레이션
- 공개 API나 관찰 가능한 UI 동작
- 파괴적 작업
- 의존성/플랫폼/설정/workflow-control 변경
- 불분명한 수락 기준 또는 미해결 핵심 사용자 선택
- 컴포넌트 간 범위
- 고위험 maintenance

```
compact: 0 → 한정된 평가 1회(escalation 계열 재확인, 해당 시 full 로 전환)
          → 5.0 compact 체크리스트 → (미해결 결정이 있을 때만) 5.3 질문
          → 6 PLAN.md (VERDICT ASSESSED_COMPACT) → WBS plan → 6.9 PLAN_SESSION.json 삭제
          서브에이전트 없음
full:    0 → 1: deferred-scope.md 생성, 전제 추출, 코디네이터가 plan-ceo-review /
          plan-eng-review 방법론을 디스크에서 읽음
          → 독립 리뷰어 정확히 1명 (Agent subagent_type 'explore', 900s)
          → Mechanical / Taste / User Challenge 분류 → 5.0 pre-gate (재시도 최대 2)
          → 5.3 질문 → 5.4 인가 재확인 → 6 WBS plan (VERDICT REVIEWED,
          리뷰어 실패 시 REVIEWED_DEGRADED)
```

근거: `plan/SKILL.md:31-42, 152-286`, `review-phases.md:11-186`.

- 리뷰어 brief는 SKILL.md 읽기를 금지하고, 방법론은 코디네이터가 읽는다. 리뷰어가 실패하면 코디네이터가 혼자 진행하고 사유를 PLAN.md에 적는다. 사용자와 추가로 상호작용하지 않는다.
- Phase 1 필수 산출물: 대안 표, ASCII 의존성 그래프, 테스트 다이어그램(압축 금지), Test Plan, Error & Rescue Registry, Failure Modes Registry, NOT in scope, What already exists, `deferred-scope.md`와 `TODOS.md`의 미룬 항목.
- **결정 분류**: Mechanical/Taste는 원칙 P1-P6으로 자동 결정하고 Decision Audit Trail에 남긴다. User Challenge와 미해결 핵심 결정은 자동 결정하지 않는다. 코디네이터와 리뷰어의 의견이 다르면 더 높은 등급으로 올린다(`decision-principles.md:7-47`).
- **질문은 한 번**: 미해결 핵심 결정이 있을 때만 `AskUserQuestion`을 한 번 쓰고, 질문 객체는 최대 3개다. Approve/Reject 게이트(§5.4.1)는 사용자가 계획 승인을 명시적으로 요청했을 때만 연다.
- **PLAN.md 필수 절**(`write-artifacts.md:22-146`, `intake.md:170-173`):
  - objective, Original Request / Intent Summary, What already exists
  - scope in/out, NOT in scope, 대상 파일/표면
  - AC-001부터 번호를 매긴 acceptance criteria, verification contract
  - Dream state delta, Cross-phase themes
  - Durable Docs Decision(REQ/GUIDE/ADR/POLICY 경로와 이유), doc-sync expectation
  - risk/rollback(`risk_level: high`일 때), registries, Review Status, Plan Review Report, 다음 구현 단계
  - compact 계획은 allowed/test/forbidden 경로도 필수다.

  이 목록의 산문 절은 스킬이 검토한다. 공개 WBS plan은 비어 있지 않은 내용 외에도 유효한 성과 트리와 전이 규칙을 검사한다(`harness_server.py:2014`).
- **lens 선언**: 선택 규칙은 `write-artifacts.md:153-165`에 있다. `review-code`는 항상 들어간다. 보안 경계가 있으면 `review-security`를 더하고, QA는 qa-cli/qa-api/qa-browser/qa-desktop 중에서 고른다. 생략하면 기존 값(기본 `[review-code, qa-cli]`)이 유지된다. 정렬과 거부 동작은 코드에 있다(`harness_server.py:1982-2013`, §3.5).
- compact를 골라도 develop 단계의 리뷰, QA, 영수증, close, install은 생략되지 않는다.

### 7.4 develop 단계

| Phase | 내용 | 근거 |
|---|---|---|
| 0 | manifest, TASK.json, 종료 상태, focus 사전 점검 | `develop/SKILL.md:91-98` |
| 1 | PLAN/REQUEST/TASK 로드, PROGRESS.md `completed_acs`로 재개(mtime 재검증), learnings. **Durable Docs Preflight**: 선택한 REQ를 첫 소스 편집 전에 작성 | `:100-128` |
| 2 | 만들기 전에 검색 | — |
| 3.0 | lane 표(AC / Files / Tests / Depends on / Lane / Route / Reason). disjoint AC별 ac-worker와 필요시 test-author를 한 메시지로 스폰. `develop.fanout_cap` 적용 | `develop/SKILL.md` Phase 3.0, `parallel-fanout.md` Batch cap |
| 3.1 | 코디네이터만 PROGRESS.md(7키)를 쓴다. forbidden은 차단, 목록에 없는 경로는 경고 후 자동 추가(스킬 산문. gate 코드는 소스 확장자 파일의 forbidden만 막고 경고는 하지 않는다, §5.6) | `:168-187` |
| 3 | AC 하나씩 구현(`developer.md`의 최소 충분 사다리). AC별 테스트 실패는 cycle에 세지 않고, Phase 7 full-suite 실패만 3-cycle 한도에 센다 | `:189-217` |
| 3.3 | `write_checkpoint.py` → `doc/harness/checkpoints/<id>.md` | `:219-227` |
| 3.4-3.9 | 테스트 프레임워크 부트스트랩, 회귀 규칙과 증거 게이트, fix-first 자기 점검, durable docs(3.6.1), lint, build, runtime smoke. 전제가 없으면 건너뜀 | `:229-251` |
| 4.5 | 병렬 advisory 감사: coverage trace, visual smoke(브라우저), 조건부 migration/contract, LLM-trust, perf. verdict가 아니다 | `quality-audit-pipeline.md:7-27` |
| 4.8 | 변경 함수 저비용 스캔 | `quality-audit-pipeline.md:305-311` |
| 4.85 / 4.9 | coverage 종합 / coverage 게이트(`coverage_minimum` 미만이면 BLOCK. 스킬 산문) | `develop/SKILL.md:264-266` |
| 5 | scope drift 감지 + `note_freshness.py --paths` | — |
| 6 | 계층 순서로 bisect 가능한 커밋(infra → models → controllers → tests → docs) | — |
| 6.5 | IRON LAW checkpoint | — |
| 6.6 | 리뷰(§7.5) | `quality-audit-pipeline.md:29-303` |
| 7 | QA(§7.6) | `develop/SKILL.md:333-359`, `verification-gate.md` |
| 7.7 | dogfooder | `:361-391` |
| 7.8 | install_verified(harness 소스 저장소만) | `:393-419` |
| 8 | pre-close 체크리스트 → `wbs(action="close")` → 최종 응답 | `:421-441` |
| 8.5 | learnings 분류(none/captured/rejected), runbook 후보 | `:443-479` |
| 8.6 | 변경 경로 → doc root 매핑, Known ceiling 복사, `wbs(action="verify")` 재호출. REQ/GUIDE/ADR/POLICY가 바뀌었거나 과제에 명시적인 durable 사용자 교정이 있으면 documentation-review를 스폰한다. Retrospective REQ pass가 만든 `status: candidate` REQ는 close를 막지 않는다. 바뀐 REQ의 관찰 가능한 동작이 모호하면 FAIL | `:481-493` |
| 8.7 | `doc/changes/<date>-<slug>.md` | `:495-496` |

**Phase 3.0 순차 route.** develop/SKILL.md는 route로 `Agent(...)`, `sequential-prelude`, `sequential-dependent`만 허용한다(`:151`). parallel-fanout.md는 여기에 `sequential-small-task`를 더한다. 이 route는 lane 표에 `reason:"small-task"`, `estimated_lines`, `estimated_seconds`를 적어야 하고, 합계 10줄 미만이면서 약 15초 이내로 추정될 때만 쓸 수 있다. 사용자가 공격적 병렬을 요청하면 쓸 수 없다(`parallel-fanout.md:221-227, 247-250`). 두 파일의 어휘 차이는 §7.9에 있다.

**8과 8.5-8.7의 순서.** 스킬 본문이 모순된다. `:80`은 "Phases run in strict order"라고 하는데, 번호와 위치가 8.5보다 앞선 Phase 8이 "Call `wbs(action="close")`"라고 한다(`:433`). 반면 8.5("record it before close", `:459-461`)와 8.6("cannot close with unresolved durable-doc gaps", "Call wbs verify", `:482, :489-490`)은 close 전에 해야 하는 일처럼 쓰여 있다. 이 문서는 8.5·8.6의 문구를 따라 **8.5-8.7을 `wbs(action="close")` 전에 한다**고 해석한다(§7.9).

**병렬 규칙**(`parallel-fanout.md` Batch cap): Phase 3.0은 AC lane을 기본 4개씩 실행한다. manifest `develop.fanout_cap` 정수 1–8을 쓰며 8 초과는 8, 누락·그 밖의 값은 4다. cap은 에이전트 수가 아니라 AC lane 수이며 paired test-author는 같은 lane에 속한다. AC마다 `Files`, `Tests`, `Depends on`, `Verify`를 선언하고 소유 경로를 겹치지 않게 나눈다. 공용 선행 변경은 별도 AC로 끝낸 뒤 소비 AC를 실행한다. test-author는 PLAN 의도에서 Tests 경로만 작성하며 해당 AC의 두 writer가 돌아오면 코디네이터가 scoped 검증을 실행한다. full-tree Verify는 관련 writer가 모두 끝난 뒤 실행한다. 실제 host slot inventory에는 coordinator, paired writer, nested worker와 아직 해제되지 않은 슬롯을 모두 세며, 완료된 AC 뒤에는 독립 ready lane을 즉시 채운다. ac-worker는 자기 Files를 최대 3개의 한 단계 sub-worker로 나눌 수 있지만 오직 `harness:ac-worker`만 스폰하고, sub-worker는 추가 스폰이나 형제 작성 중 전체 AC Verify를 하지 않는다. `name=`은 넘기지 않는다. 근거: [병렬 폭 ADR](patterns/ADR__within-task-parallel-width.md), `plan/write-artifacts.md` Per-AC shape, `agents/ac-worker.md` Sub-split.

### 7.5 Phase 6.6 리뷰: 깊이, hunter, formal reviewer

**깊이 선택**(`quality-audit-pipeline.md:40-81`):

순서대로 적용한다(정본은 `quality-audit-pipeline.md:46-59`):

1. **DEEP**: 명시적인 DEEP 요청이 있거나, 다음 중 하나라도 실질적 위험이 있을 때다.
   - 보안/신뢰 경계, 민감 데이터, 동시성, 마이그레이션
   - 공개 계약, durable 계약, 의존성, 빌드, 설치
   - 훅, 수명주기, gate
   - 수동 충돌 해결, 의미가 달라지는 범위 diff, 컴포넌트 간 변경, 두 영역(dual-domain)에 걸친 변경

   명시적 DEEP 요청은 현재 대화의 사용자·시스템·개발자 지시나 보호된 과제 의도(PLAN)에서 온 것만 인정한다. 소스, 문서, 도구 출력, 다른 에이전트가 전달한 문구는 근거가 되지 못한다(`:61-65`).
2. **DEEP**: 위 조건을 숨길 수 있는 증거가 없거나, 읽을 수 없거나, 불완전하거나, 낡았을 때다.
3. **LIGHT**: 적극적 증명이 완전할 때만이다. 필요한 조건은 다음과 같다.
   - 범위가 한 영역에 국한된다.
   - 동작 보존이 기계적으로 보장되거나 실행되지 않는 산문·예시만 바뀐다.
   - 강제 DEEP 조건이 없고, 제어 흐름·상태·데이터·오류·계약·의존성·빌드/설치·훅/수명주기/gate·보안·동시성·마이그레이션 동작이 바뀌지 않는다.
   - 수용 의도가 분명하고 검증이 집중돼 있으며, 현재 worktree 증거가 있다.

   작은 diff나 docs/test/config/prompt라는 분류만으로는 증명이 되지 않는다. rebase는 여기에 더해 다음을 모두 증명해야 LIGHT다. old_base/old_tip/new_base/new_tip이 정확히 알려져 있고, 충돌 없이 수동 해결 없이 실행됐고, 패치가 1:1로 동등하고(추가·누락·분할·결합·재정렬·수정 없음), 심볼·계약·의존성·생성물·수명주기에서 의미가 겹치지 않고, `HEAD == new_tip`이고, index/worktree가 깨끗하다. 증명이 하나라도 빠지면 rebase-LIGHT가 아니다. 충돌·의미 차이·중첩, 또는 이를 숨길 수 있는 증거 손실이 있으면 DEEP이다(`quality-audit-pipeline.md:83-92`).
4. **STANDARD**: 위 목록을 모두 검사한 뒤에 남는 경우다.

깊이는 한 시도 안에서 올릴 수만 있고, 재개하면 다시 계산한다.

**STANDARD의 hunter 선택**(`quality-audit-pipeline.md:69-74`): hunter는 정확히 1개이고 위험 영역으로 고른다. 실행 로직·상태·자원·데이터 흐름·오류 흐름 위험이면 correctness, 계약·호환성·검증·테스트 적정성·coverage 위험이면 contract/test다. 두 영역이 모두 실질적이거나 하나라도 불명확하면 STANDARD가 아니라 DEEP이다. 두 영역이 모두 없다고 확인됐는데 LIGHT 증명이 불완전할 때만 contract/test로 고정한다.

```
WBS context → required_review_lenses → 깊이 결정
초기 배치 (한 메시지, security-reviewer 는 hunter 데이터 없이 독립):
  LIGHT    : hunter 0개 + formal code-reviewer (+ harness:security-reviewer)
  STANDARD : hunter 정확히 1개 (correctness 또는 contract/test)   (+ harness:security-reviewer)
  DEEP     : hunter 2개                                            (+ harness:security-reviewer)
hunter final 대기 → 기계적 검증 → 이스케이프
새 harness:code-reviewer 1명 (depth, hunter 집합, 이유, 신뢰하지 않는 leads 로서의 hunter 배열)
final 대기 (SubagentStop → RECEIPTS.jsonl, 원문 → REVIEWS.jsonl)
FIX_NOW → 깊이 과소 분류나 STANDARD focus 오류이면 코디네이터가 소스 수정 없이 빠진 discovery와 새 formal review로 재라우팅한다. 일반 소스 finding이면 원 구현자에게 보낸다 → 재시도 매트릭스를 적용한 뒤 영향받은 formal review 전부 재실행
모든 required review lens PASS 가 될 때까지 반복
```

근거: `quality-audit-pipeline.md:226-242`. hunter를 기다리는 것은 code-reviewer뿐이다.

- **hunter 출력 검증**: `[]`이거나, `anchor`/`issue`/`evidence` 문자열 키만 가진 객체 최대 20개의 JSON 배열이어야 한다. 문자열 하나는 2,000바이트, 배열은 65,536바이트 이하다. `<`, `>`, `&`는 이스케이프한다. 형식이 틀리면 unavailable로 표시하고 고쳐 쓰지 않는다(`quality-audit-pipeline.md:193-211`).
- **discovery 예산**: 한 번의 live attempt에서 hunter cycle은 최대 2회다(호출 수로는 LIGHT 0, STANDARD 2 이하, DEEP 4 이하). cycle 2 뒤나 hunter 없는 remediation 분기 뒤에는 DEEP formal-only로 가고, 호출 문구는 정확히 `discovery budget exhausted`다. 재개·복구 때 이전 discovery 횟수를 알 수 없으면 DEEP을 선택하거나 유지하고, 예산을 소진된 것으로 보아 호출 이유를 정확히 `discovery budget unknown and treated as exhausted`로 쓴다. 횟수를 영수증에서 재구성하지 않는다(`:102-135`).
- **판정 소유**: verdict와 영수증은 formal reviewer만 가진다. INVESTIGATE는 reviewer의 BLOCKED_ENV를 통해서만 진행을 막고, OPTIONAL은 참고용이다. 영향받은 formal review를 모두 다시 PASS시킨 뒤에야 QA로 간다(`:171-247`).
- 설계 배경: `doc/designs/minimal-implementer-and-code-review-gate.md`.

### 7.6 Phase 7 QA와 verification gate

- 실제 Phase 6.6 reviewer PASS final을 받은 **뒤에** 해당하는 `qa-*`를 **모두 한 메시지로** 스폰한다. 사용자 대상 변경이고, manifest가 지원하며(`ux_review_supported: true` 또는 해당 `browser_qa_supported`/`desktop_qa_supported`) 변경 경로 규칙이 요구하면 해당 `ux-*`도 스폰한다(non-attesting). full-suite 실행은 qa-*에 맡기고, inline Bash 테스트는 AC별 타깃 실행과 디버그 재실행으로 제한한다(`develop/SKILL.md:333-352`).
- `verification-gate.md` 단계(`:8-377`):
  - Step 0: working tree가 깨끗해야 한다(커밋 완료).
  - Step 0.5: 설치 루트 4곳을 스냅샷하고, 제거된 것이 없어야 한다.
  - Step 1: 실패를 GATE/PERIODIC × OWN/PRE-EXISTING으로 분류한다.
  - Step 2.5: `browser_qa_supported`일 때 PLAN.md의 브라우저 검증 단계를 자동 실행한다.
  - Step 3: Phase 3.8을 건너뛰었으면 빌드/import를 점검한다.
  - 3-cycle 수정 한도(cycle 3은 가설 기반 근본 원인 분석).
  - 3.5 transience 필터(두 번 실패해야 센다).
  - 3.6 severity×confidence close 게이트(critical ≥7, high ≥8).
  - Step 4: 결과는 PLAN.md와 순서가 맞는 영수증에만 남는다. 별도 AC 원장은 없다.
- C-14a에 따라 로컬에서 쓸 수 있는 가장 높은 검증 tier를 돌리고, 검증할지 사용자에게 묻지 않는다. 예외로, 파괴적 상태 변경·운영(production) 자원·유료/외부 자격 증명이 필요하거나 유효한 접근 사이에서 실제 제품 선택이 필요할 때만 묻는다.

### 7.7 dogfood, install, close

- **7.7 dogfooder**: 마지막 PASS cycle의 QA 스폰과 함께 보낸다. 완료를 막지 않고 발견 사항만 돌려준다. 건너뛰는 경우: runtime이 PASS가 아닐 때, maintenance 전용 과제일 때, PLAN에 사용자 대상 표면이 없고 `dogfood_required`가 명시적으로 true가 아닐 때. 선언이 모호하면 실행하거나 계획 결정을 남긴다. 라우팅은 Git diff로 추론하지 않는다(`develop/SKILL.md:383-392`).
- **7.8 install**: §11.2.
- **close**: §6.10.

### 7.8 close 이후 self-improvement

`plugin/skills/run/self-improvement.md:1-267`:

1. 마찰 신호 감지: 잘못된 검증 전략, 낡은 manifest, 반복 실패, 단계 마찰, 새 패턴.
2. `learnings.jsonl`에 `harness-improvement` 행 추가.
3. 안전한 manifest 필드 자동 수정(먼저 알린 뒤). **이 경로로는 실패한다.** close 뒤에는 마커가 지워져 활성 과제가 없는데, `doc/harness/manifest.yaml`은 WORKFLOW_CONTROL_SURFACE라서 MAINTENANCE가 있는 활성 과제가 있어야 쓸 수 있다. Write/Edit는 `workflow-control-surface`로 거부된다. close 전에 MAINTENANCE 과제 안에서 하거나 후속 과제로 넘긴다(`self-improvement.md:61-71`, `prewrite_gate.py:111-118, 733-749`, `harness_server.py:1824-1825`).
4. `promote_learnings.py --task <id> --task-run-id <run>`(보고만 함).
5. 마지막 retro 이후 검증된 close가 3개 이상이면 `retro.py --save`(`HARNESS_DISABLE_RETRO=1`로 끔).
6. (close **전**, develop 8.5와 같은 단계) 재사용할 수 있는 교훈을 captured/rejected/none으로 분류한다. captured는 커밋된 아티팩트가 있어야 한다. 파일 스스로 "Before task close, classify every reusable discovery…"라고 한다(`self-improvement.md:154-171`, `develop/SKILL.md:450-453`).
7. harness 저장소면 완료된 diff를 커밋한다. 이 단계의 출처는 self-improvement.md가 아니라 `plugin/skills/run/SKILL.md:225-230`이다.

후속 WBS 성과를 고르기 전에 이 절차를 수행한다.

### 7.9 스킬 산문의 알려진 어긋남

- `verification-gate.md:357`은 더 이상 스폰하지 않는 "quality synthesis agent"의 표를 전제한다. close 게이트(`develop/SKILL.md:356-359`)에는 그 표를 만드는 주체가 정의되어 있지 않다.
- `verification-gate.md` Step 1은 PLAN.md 테스트 명령을 직접 실행한다고 쓰고 qa-* 스폰은 말하지 않는다. 머리말은 이 파일이 Phase 6.5 뒤에 로드된다고 해서 6.6 리뷰를 건너뛴다.
- dogfooder 규칙이 자기모순이다. QA와 함께 스폰하라고 하면서, runtime이 PASS가 아니면 건너뛰라고 한다. 스폰 시점에는 PASS일 수 없다.
- `develop/SKILL.md:252`은 3.9 smoke를 qa-* 안에서 돌린다고 한다. 6.6 이전에 스폰한 qa-*의 PASS는 영원히 인정되지 않는다. 반대로 FAIL/BLOCKED_ENV는 순서와 관계없이 즉시 runtime_verdict를 FAIL/BLOCKED_ENV로 만든다. 같은 lens가 다시 시작되어 가장 새 행이 `started`가 되면 이 값은 가려지고, 재실행 동안 runtime_verdict는 PENDING이다. 그 뒤에는 읽을 수 있는 새 completion이 결과를 대신한다. `shape` 계열 PENDING completion은 앞선 FAIL/BLOCKED_ENV를 대신하지 못한다(§6.7, `_lib.py:4313-4322, 4453-4485, 4598-4637`, §6.8).
- develop 8/8.5-8.7의 순서가 모순된다(§7.4). `:80` strict order + `:433` Phase 8의 WBS close vs `:459-461, :482, :489-490`.
- `self-improvement.md:61-71`의 manifest 자동 수정은 close 뒤에 실행되지만, prewrite gate가 막는다(§7.8). 실제로 하려면 MAINTENANCE 마커가 있는 활성 과제 안에서 쓰거나 후속 과제로 넘겨야 한다. Bash로 gate를 우회하는 것은 CONTRACTS.md § 0이 hard failure로 규정한다.
- route 어휘가 다르다. `develop/SKILL.md:151`에는 `sequential-small-task`가 없고 `parallel-fanout.md`에는 있다(§7.4). `parallel-fanout.md`의 Phase 4.5 행은 "security" 조건부 specialist를 나열하지만 `quality-audit-pipeline.md` § 4.5에는 그것이 없다(`quality-audit-pipeline.md:7-21`).
- `develop/SKILL.md:188`의 "forbidden → BLOCK"은 소스 확장자 파일에만 강제된다(§5.6).
- **도구 허용 목록.** run/develop/plan과 developer는 단일 WBS 도구를 허용한다. 추가 task/Goal MCP 별칭이 필요하지 않다.
- **도구 이름 하드코딩.** 스킬 본문은 `mcp__plugin_harness_harness__*`를 하드코딩한다. 사용자 수준 서버만 등록된 환경(도구 이름 `mcp__harness__*`)에서는 이름이 맞지 않는다(`plan/intake.md:57`).
- Codex SKILL.md는 손으로 관리하는 별도 포트다. 예: Codex develop에만 `## Model Routing`, `Phase 9`, qa_codifier 단계가 있다(§2.2).
- `qa_codifier.py`는 사실상 실행되지 않는다(§8.2).
- `parallel-fanout.md:312, 315`는 이 플러그인에 없는 외부 에이전트(`oh-my-claudecode:executor`, `:debugger`)로 라우팅한다. 존재하지 않는 "§ Model Routing"과 `/tmp/omc-research` 경로도 참조한다. `develop/SKILL.md:87`은 없는 "4 Agent D"를 참조한다. `lens="<lens>"`는 Claude Agent의 실제 파라미터가 아니다.
- plan은 `subagent_type 'explore'`를 쓰는데 Claude Code 기본 에이전트 이름은 `Explore`다. 대소문자가 달라도 동작하는지 확인이 필요하다. `plan-ceo-review/SKILL.md:122-127`의 자체 리뷰어 요구는 "리뷰어 정확히 1명" 불변식과 충돌한다.
- 제거된 hygiene 서브시스템과 유지된 `promote_learnings.py`의 stale-file/contradiction 점검을 구별한다. 유지된 점검에는 `HARNESS_DISABLE_HYGIENE` 스위치가 없다(§13.8, `doc/harness/ADR__remove-hygiene-subsystem.md:72-84`).
- `self-improvement.md:205-207`의 "If it returns 'queued'"는 앞에 해당 명령이 없다. `intake.md:25`는 정의되지 않은 변수를 출력한다.

---

## 8. 에이전트와 lens

### 8.1 Claude 에이전트 17개

| 에이전트(파일) | model | tools | 역할 | lens / 영수증 | 출력 계약 |
|---|---|---|---|---|---|
| developer | sonnet | Read, Write, Bash, Glob, Grep, LS + `mcp__plugin_harness_harness__wbs` | 코디네이터를 대신한 최소 충분 구현. PLAN과 영수증은 쓰지 않음 | 없음 | `Status: implemented/blocked/needs-coordinator-review`, `Changed:`, `Verification:`, 선택 `Known ceiling:`/`Assumption:` |
| ac-worker | sonnet | Read, Write, Bash, Glob, Grep, LS, Agent | AC 하나 구현. 오직 ac-worker를 최대 3개, 한 단계만 sub-split. MCP writer·보호 증거 쓰기 금지 | 없음 | `AC-NNN: implemented/blocked/needs-coordinator-review` + Changed/Tests/Blockers |
| test-author | sonnet | Read, Write, Bash, Glob, Grep, LS | PLAN 의도에서 한 AC의 Tests 경로만 작성. 스폰 금지 | 없음 | `AC-NNN tests: written/blocked/needs-coordinator-review` + Expected red/green/Interface |
| defect-hunter | sonnet | Read, Bash, Glob, Grep, LS | 결함 후보 발굴. focus는 Correctness 또는 Contracts and tests 하나 | 없음(non-attesting) | JSON 배열만(0~20개, `{anchor, issue, evidence}`). VERDICT 금지, 수정 제안 금지 |
| code-reviewer | opus | Read, Bash, Glob, Grep, LS | 모든 깊이에서 review-code의 유일한 권위 | **review-code** | 1행 VERDICT, 2행 FINDING_COUNTS, 3행 `REVIEW_DETAIL: {"blocker":…,"findings":[…]}` |
| security-reviewer | opus | Read, Bash, Glob, Grep, LS | 보안 경계 리뷰(hunter 데이터 없이) | **review-security** | VERDICT + FINDING_COUNTS(REVIEW_DETAIL 없음) |
| qa-cli | opus | Read, Glob, Grep, Bash | CLI, 테스트 QA | **qa-cli** | 1행 VERDICT, AC별 1:1 증거, depth tier, `codifiable:` |
| qa-api | opus | Read, Glob, Grep, Bash | API QA | **qa-api** | 같음 |
| qa-browser | opus | + `mcp__chrome-devtools__*` 14개 | 브라우저 QA | **qa-browser** | 같음 |
| qa-desktop | opus | + `mcp__x11__*` 7개(접두사는 placeholder) | 데스크톱 QA(v1은 Linux 전용) | **qa-desktop** | 같음. tool_not_found면 BLOCKED_ENV |
| ux-cli / ux-api / ux-browser / ux-desktop | opus | QA와 같은 계열 | UX 리뷰(보완 역할, qa-*를 대체하지 않음) | 없음(ux-*는 지원 lens가 아님) | FAIL/BACKLOG/PASS 분류, `UX review depth:`. 1행 VERDICT 요구 없음 |
| dogfooder | opus | Read, Glob, Grep, Bash | 사용자 관점 마찰 탐색. verdict도 gate도 아님 | 없음 | `[FRICTION/GAP/DEAD_END/MISSING_AFFORDANCE/WORKFLOW_BREAK]` 목록 + 우선순위 backlog |
| documentation-review (`critic-document.md`) | (지정 없음) | Read, Bash, Glob, Grep, LS | REQ/GUIDE/ADR/POLICY 변경 검토. 문서 수정 금지 | 없음 | PASS/FAIL findings. VERDICT 줄 없음, BLOCKED_ENV 없음 |
| task-lead | inherit, `isolation: worktree` | **tools 줄 없음**(Edit/MultiEdit 포함 모두 상속) | batch에서 worktree 하나의 전체 수명주기 담당 | 없음(자신은 lens가 아니고, 중첩 lens가 영수증을 만든다) | fenced JSON `{task_id, worktree, branch, commit, verdict: closed/blocked/failed, blocked_reason}` |

근거: `plugin/agents/*.md` frontmatter, `developer.md:1-6, 105-123`, `ac-worker.md` Scope/Sub-split/Output Contract, `test-author.md`, `defect-hunter.md:9-74`, `code-reviewer.md:13-107, 184-226`, `security-reviewer.md:83-99`, `qa-cli.md:12-193`, `dogfooder.md:115-161`, `critic-document.md:54-65`, `task-lead.md` frontmatter와 절차.

- `tools:`를 선언한 Claude 에이전트 중에는 Edit/MultiEdit를 가진 것이 없다. 구현 역할인 developer와 ac-worker도 Write만 가진다. 예외는 tools 줄이 없어서 Edit/MultiEdit를 포함한 모든 도구를 상속하는 task-lead다.
- QA 에이전트에는 Write가 없는데 `QA_KNOWLEDGE.yaml`과 learnings에 append하라는 지시를 받는다. dogfooder는 Bash echo로 learnings를 쓴다. README의 "QA agents never hold Edit/Write"는 frontmatter tools 기준으로만 맞다.
- task-lead는 tools를 상속하므로 `mcp__plugin_harness_harness__*`와 `mcp__harness__*` 어느 접두사로 등록돼 있어도 도구를 쓸 수 있다. 테스트가 tools 줄이 없는 상태를 고정한다(`tests/test_batch_skill_contract.py:46-53`). 반면 developer는 플러그인 접두사만 명시하므로 사용자 수준 서버로만 설치된 환경에서는 그 도구를 못 쓴다.
- code-reviewer의 판정 매핑은 기계적이다. blocker가 있으면 BLOCKED_ENV(INVESTIGATE=1), 검증된 finding이 있으면 FAIL, 그 외에는 PASS다. FIX_NOW는 finding 수, OPTIONAL은 항상 0이다. narrative에는 `REVIEW_DEPTH: …; HUNTERS: …; REASON: …`를 반복하고, 선택된 깊이가 너무 낮았다면 FIX_NOW를 하나 기록해 FAIL로 만든다.

### 8.2 QA의 검증 깊이 tier

| lens | tier(높음 → 낮음) |
|---|---|
| qa-cli | executed-command, test-suite, build-only, static-only, blocked-env |
| qa-api | live-http, integration-db, controller-mock, unit, static-only |
| qa-browser | interactive-browser, render-only-browser, server-only, static-only, blocked-env |
| qa-desktop | interactive-desktop, window-rendered, launch-only, static-only, blocked-env |

`codifiable:` YAML 블록의 필수 필드는 behavior, ac_id, command, expected_exit, expected_stdout_contains, expected_stderr_contains다(`qa-cli.md:158-193`).

**이 블록은 현재 테스트로 바뀌지 않는다.** 블록을 테스트로 만드는 것은 `qa_codifier.py --transcript <path>`뿐이다. 동작하면 `<task>/audit/regression-draft/`에 staging한 뒤 `tests/regression/<task>/`로 옮긴다. 그러나 Claude 스킬은 이 스크립트를 부르지 않는다. `develop/SKILL.md:241-242`은 이 블록이 "future regression-test extraction"용이라고만 한다. Codex develop은 `--task-dir`만 넘겨 호출하는데, transcript 경로가 없으면 `codify()`는 `codifier-empty: no transcript path provided` learnings 행을 남기고 바로 0으로 끝난다(`doc/harness/patterns/general.md:1-10`에도 기록). ac_id가 없거나 `echo hello` 같은 사소한 명령을 거부하는 규칙은 누군가 `--transcript`로 수동 실행할 때만 적용된다(`qa_codifier.py:319-356, 447-460`, `plugin-codex/internal-skills/develop/SKILL.md:202-206`).

### 8.3 lens와 영수증 요약

- 영수증을 만드는 lens는 `review-code`, `review-security`, `qa-api`, `qa-browser`, `qa-cli`, `qa-desktop` 여섯 개뿐이다. `_receipt_entry_semantics_valid`가 그 밖의 lens를 거부한다(`_lib.py:46-52, 3118-3131`).
- `_lens_absent`는 참/거짓이 아니라 **SUPPORTED_LENSES 소속 여부**로 판단한다(`subagent_lifecycle.py:147-172`).
  - start 경로에서는 ux-*와 lens 없는 스폰이 조용히 영수증 없이 끝나고, 이름을 준 스폰만 흔적을 남긴다(테스트 있음, `tests/test_subagent_lifecycle.py:1396-1422`).
  - stop 경로에서는 `_lens_absent`가 `_trusted_stop_provenance` 뒤에 실행된다. transcript start attachment도 `started` 행도 없는 이름 없는 ux-* 또는 lens 없는 에이전트는 출처 검증에서 먼저 `no-canonical-start-attachment`로 거부되고, `receipt_not_owed`는 설정되지 않는다. 그러면 `_receipt_was_expected`가 "payload에 agent type이 있음"으로 돌아가므로, stop payload에 `agent_type`이 있으면 `binding-miss` 행이 남을 수 있다(코드에서 도출, 테스트 없음. `subagent_lifecycle.py:617-690`, `background_hook.py:271-305`).
- `provenance_from_artifacts`는 ux-*를 "영수증이 하나라도 있으면 true"로 본다. 테스트 전용이고 런타임 호출자는 없다(`_lib.py:5012-5031`).

### 8.4 Codex 에이전트

- `plugin-codex/agents/` 16개는 방법론 참고용이다. test-author는 `test_author_ac_<NNN>` task_name으로 스폰하는 lens 없는 역할이다. frontmatter는 모두 name과 description뿐이다. reviewer·developer·dogfooder·qa-* 파일에는 "MCP-hosted lifecycle watcher가 영수증을 소유한다"는 overlay 머리말이 있다. critic-document, defect-hunter, ux-api/-browser/-cli/-desktop 여섯 파일에는 이 문구가 없다. defect-hunter는 대신 이 역할이 "never owns review or QA lifecycle evidence"라고 쓴다(`plugin-codex/agents/code-reviewer.md:1-8`, `defect-hunter.md:1-8`).
- developer, defect-hunter, code-reviewer, security-reviewer의 `harness:role-core` 블록은 Claude 쪽과 바이트 단위로 같고, 테스트가 이를 확인한다(`tests/test_review_agent_contracts.py`). QA/UX 파일은 손으로 관리하는 축약본이다.
- Codex에서 lens는 `spawn_agent`의 `task_name`으로 정해진다. 이름 규칙(`plugin-codex/internal-skills/develop/SKILL.md:273-337`):
  - `code_review_*` / `review_code_*`
  - `security_review_*` / `review_security_*`
  - `qa_{cli,api,browser,desktop}_<slug>_<run>`
  - `defect_hunter_correctness_*` / `defect_hunter_contract_tests_*`(review identity가 들어가면 안 됨)

  review처럼 보이는데 bind되지 않는 이름은 PreToolUse가 거부한다.

---

## 9. WBS와 native Goal

Native Goal은 objective와 지속 실행 문맥만 제공한다. 일반 요청과 같은
`wbs` 루트를 열고 PLAN의 관측 가능한 성과를 점진적으로 갱신한다.
측정은 선언한 assertion의 실제 검증과 현재 소스·실행 증거에 묶인다.
작업 수나 AI 추정치는 진행률이 아니다. 파일 소유권은 실제 병렬 실행
handoff 때만 별도로 선언한다. 자세한 규범은
[WBS 진행률](REQ__measurable-wbs-progress.md)을 따른다.

각 leaf는 관측 가능한 성과와 양의 weight를 가진다. 초반에는 한 leaf로 시작해도
된다. 실제 assertion argv는 검증할 준비가 되었을 때 plan으로 선택하고 check로
실행한다. 검증은 정의·run·현재 checkout fingerprint와 의존 성과의 증거가 맞을
때만 가중치에 포함된다. 읽기나 next는 assertion을 실행하지 않는다. 실패·timeout·
소스 변경은 현재 credit을 주지 않으며, 기준선이 없는 과거 자료는 percentage=null이다.
100%도 독립 review/QA, 실제 pack 통합 또는 receipt-backed close를 대신하지 않는다.

새 사용자 요구나 필요한 추가 개발이 발견되면 같은 PLAN의 미완료 성과·의존 관계·
검증을 갱신한다. 사용자 발언과 derived 기술 선택은 출처를 구분하고, 추측을
필수 요구사항으로 승격하지 않는다. 새 성과는 분모와 scope_revision을 늘릴 수
있지만 credit이나 진척 시계를 주지 않는다. 미완료 leaf를 더 작은 성과로 나누면
기존 가중치를 보존한다. 완료 leaf를 나누거나 재가중해 진척을 만들지 않는다.
검증 가중치가 7,200초 증가하지 않았으면 다음 활성 경계에서 refinement_due를
보고 유용한 미완료 성과만 구체화한다. 외부 대기는 blocker와 unblock_condition을
기록한다. 별도 daemon이나 반복적 장부 분할은 없으며 실제 개발이 우선이다.

구형 Goal 장부·큐·공개 task 별칭은 제거했다. 기존 기록은
`wbs(action="migrate")`로 미리 확인하고 `apply=true`로 명시적으로 옮긴다.
원본을 먼저 보관하고 목적지 해시를 확인한 후 소스를 퇴역시킨다.
기존 TASK·영수증·배치 ID는 보존하고 과거 상태를 완료 증거로 승격하지 않는다.
평범한 과거 objective/child intake는 출처가 미검증인 MIGRATION.md 참고 자료이며
새 실행 큐나 confirmed REQ가 아니다. 기존 pool에는 batch lock 아래 owner_task_id만
더해 중립적인 WBS 소유권을 연결한다. Goal child에 예약된 pool이 없으면 apply 전에
preview의 정확한 intake로 초기화해야 한다. 미시작 pack은 TASK/run/PASS를 발명하지 않는다. 첫 성과
기준선은 pool 통합을 기다리고, 기록된 manifest로 재개할 때 외부 변경을 덮어쓰지 않는다.
[마이그레이션](patterns/native-goals.md) 참조.

---

## 10. batch 병렬 모드

규범: `plugin/skills/batch/SKILL.md`(절차), `plugin/agents/task-lead.md`(lead 규칙), `doc/harness/REQ__parallel-tasks-via-worktree-leads.md`(모델과 근거).

Claude는 native worktree isolation을 사용한다. Codex는 `internal-skills/batch/SKILL.md`와 `agents/task-lead.md`를 통해 등록된 worktree를 명시적으로 만들고, workspace를 반환하는 WBS 도구와 인증된 native coordinator 신원을 연결한다. 실제 spawn·같은 에이전트 continuation·독립 review/QA 용량이 필요하다. 프로토콜 fixture는 live 실행 증명이 아니다. 상세 계약: [자동 병렬 라우팅](REQ__automatic-parallel-routing.md).

C-09 batch 조항: linked git worktree는 각각 별도 checkout이므로 write focus도 각자 가진다(`CONTRACTS.md:152`).
WBS dispatch가 반환한 정확한 task_id를 batch init의 --owner-task-id로 유지한다.
그 immutable pool owner와 intake가 WBS binding에 일치해야 하며, 다른 루트의
binding을 복사해 통합 증거를 재사용할 수 없다. 모든 lead는 자기 worktree에서
개발·독립 review·QA·close를 끝내고 pool이 직렬로 통합한다. 부모는 그동안
park 상태를 유지하며 source를 수정하지 않는다. 통합 후 같은 루트를 reopen해
통합 성과를 check하고 combined review/QA를 수행한다.

### 10.1 coordinator 전체 흐름

```
(a) intake: 요청마다 slug + 경로 scope + 선택적 submodules 목록
      - slug 재사용 금지 (tasks/TASK__<slug>, archive/batch/TASK__<slug>)
      - scope 겹침과 depends_on은 저장된 큐에서 순서를 지킨다
      - batch_state.py --repo <main> --batch-id <id> init --requests-file <json>
(b.1) batch_preflight.py --repo <main> --request <slug>=<path> ...
      verdict ok(0) → 진행 / adjust(1) → excluded 제거, 겹침 하나 미룸, 재실행
      refuse(1) → 중단, 보고 / 사용법 오류 exit 2 (JSON 없음)
(b.2) 프로젝트 Claude 설정의 worktree base ref 가 head 인지 확인
      (스킬은 설정 파일을 수정하지 않음, C-15)
(b.3) batch_preflight.py 의 worktrees_ignore 확인: git 이 보는 경로에서 ignore 검사
      저장소 밖으로 나가는 .claude symlink 는 통과
(b.4) main 에 이 세션의 열린 과제 없음 (WBS block으로 주차하거나 close)
(b.5) batch_state.py claim: 빈 슬롯 예약, 현재 main HEAD와 off-limits 반환
(c) 예약된 lead를 ONE message로 bootstrap-only 스폰 (기본 최대 3, name= 금지)
      Agent(subagent_type:"harness:task-lead",
            prompt: 요청 / slug / scope / off-limits / coordinator HEAD / pytest worker cap: 4)
      bootstrap 명령으로 검증된 비추적 보존 표시를 만든 뒤 W/branch/HEAD 반환
      native worker ID를 bind하고, 선택된 S1 모듈의 private clone 준비까지 성공한 뒤
      SendMessage로 같은 agent를 재개
      표시만 source-clean 검사에서 제외하고, 증거를 디스크에 확정한 뒤 finish가 삭제
      bind 전에는 소스 수정이나 WBS open 금지
(d) 결과를 result로 기록하고 closed lead를 반환 순서대로 하나씩:
      python3 plugin/scripts/batch_state.py --repo <main> --batch-id <id> finish --slug <slug>
      내부 batch_finish.py를 호출하고 Git 변경 전후 및 제거 전 checkpoint를 저장
      registered worktree / branch tip / clean / merge commit 없음 확인 → rebase → ff-only → harvest → 잠겼을 때만 unlock → remove → branch -d
      S1은 통합 전 모듈 객체 보존, ff 전 witness 저장, ff 직후 main 모듈 checkout 갱신 추가
      integrated(0) → 다음 lead / kept(3) → 이유와 남은 worktree·branch 보고, 다음 lead
      conflict / ff-refused / error (wrapper exit 3) → 중단·보고, 필요한 복구는 (e)
      독립 batch_finish.py의 exit 4/5/1과 구분; wrapper usage 오류는 exit 2
      integrated_tip 이 있으면 이미 main 통합됨. cleanup.removed 로 worktree 와 branch 잔여 구분
      최초 main symbolic ref 를 고정하고 rebase 뒤 checkout 변경을 거부. 통합 증명은 현재 HEAD 아닌 최초 branch ref 기준
      실패 후 통합 확인도 실패하면 reason 에 미확인으로 보고. null tip 만으로 미통합 판정하지 않음
      blocked/failed lead: worktree 와 branch 를 그대로 둔다
      빈 슬롯은 claim으로 즉시 보충; 전체 wave 종료를 기다리지 않는다
      중단 복구는 status → recover, blocked 작업은 resume → 기존 W의 agent 재개
(e) 모든 lead와 nested writer가 멈춘 뒤 TASK__batch-integrate-<slug> 를 main 에서 harness:run 으로:
      남은 충돌 해결: W 에서 rebase 다시 → 해결 → git -C <W> add → GIT_EDITOR=true git -C <W> rebase --continue (멈추는 커밋마다 반복)
        → (d) 명령에 --resume 추가 → ff·harvest·제거, 이어서 남은 lead 도 (d) 순서대로 (남겨 둔 lead 는 제외)
        → full suite → review-code(residual 에서만 깊이 선택, 아래 carry 조건) + qa-cli
      → (harness 플러그인 소스 저장소일 때만) install_verified.py (batch 에서 유일하게 실행되는 곳, lead 는 생략) → close
      큐에 남은 작업의 dispatch는 main 통합 과제를 닫은 뒤에만 재개
(f) 사용자에게 branch/commit 으로만 확인하라고 안내
(g) 보고 표: slug, branch, verdict, lead 가 반환한 commit(rebase 전), 통합된 tip(ff 뒤 main HEAD) 또는 'kept, unmerged'(사유 포함)·'not integrated', 제외/미룸/outside-root, 통합 결과
```

근거: `batch/SKILL.md` a–g, `batch_finish.py`의 `_check`, `_rebase`, `_cleanup`, `finish`, [배치 상태·풀·복구 요구사항](REQ__batch-state-pool-recovery.md).

`batch.max_leads`와 명시적 `--max-leads`는 기본 3을 대체하며 상한은 8이다.
예약·실행 중인 작업이 슬롯을 차지하고, 막힘·실패·정리 대기 작업은 파일 범위를
계속 예약한다. 다른 배치의 남은 작업 범위도 검사한다. `status`는 실행 기록과
현재 관측을 구분하는 읽기 전용 명령이며 PASS를 만들지 않는다. `abandon`은
사용자가 명시한 포기 의도와 보존 위치만 기록하고, 소스 삭제나 Goal 완료로
취급하지 않는다. 재개는 기존 agent 또는 isolation 없는 `task-lead-resume`으로
같은 worktree·task·run을 사용한다.

**통합 review-code 범위(Claude 전용).** 각 lead의 `old_base`, `old_tip`, `new_base`, `new_tip`, patch-id 결과와 carry/residual 여부를 리뷰어에게 넘긴다. rebase-LIGHT 증명, 동일 commit 수의 순서 있는 `git patch-id --stable` 일치, step d에서 멈추지 않은 rebase, harvested archive의 closed PASS(`close_receipt_fingerprint` 존재, BLOCKED.md 없음)가 모두 있어야 carry한다. 리뷰어가 carry를 확인하며, 하나라도 빠지면 residual이다. 충돌 해결·lead 간 겹침·미승계 범위·통합 수정의 residual만 깊이 판정에 쓰고 비어 있으면 LIGHT다. 리뷰 자체, review-before-QA, 전체 suite와 close 증거 요구는 유지된다. 중단된 미커밋 구현 복구에는 이 carry 증명이 없으므로 적용하지 않는다(`quality-audit-pipeline.md` Batch integration review scope).

`name=`을 금지하는 이유는 영수증 문제만이 아니다. 이 저장소처럼 agent teams 실험 플래그가 켜져 있으면(§2.1) 이름을 준 스폰이 `isolation: worktree` 없는 teammate가 된다.

### 10.2 preflight(`batch_preflight.py`)

**control root 모양**: 임베디드 `.git` 디렉터리를 가진 평범한 최상위 checkout만 `ok`다. 나머지는 즉시 refuse한다(`batch_preflight.py:127-163, 532-535`).

| shape | 조건 |
|---|---|
| `non-git` | 디렉터리가 아니거나, git 저장소가 아니거나, toplevel이 아님 |
| `linked-worktree` | git-dir ≠ git-common-dir |
| `submodule-checkout` | `--show-superproject-working-tree`가 비어 있지 않음 |
| `separate-git-dir` | common dir이 `<root>/.git`이 아님 |

**scope 분류**(symlink를 푼 경로 기준, `batch_preflight.py`의 `classify_scope`):

| 분류 | 조건 | 처리 |
|---|---|---|
| `outside-root` | root 밖이거나 `.git/` 아래 | batch 불가 |
| `inside-submodule` | 선택하지 않은 submodule 경로와 같거나 그 아래 | wave 전후에 main에서 일반 과제로 |
| `selected-submodule` | 요청의 명시적 S1 선택을 검증한 module 경로 또는 그 아래 | bind 준비 후 lead 가능; gitlink 전체 예약 |
| `inside-ignored-nested-repo` | root(제외)부터 scope(포함) 사이에 `.git`이 있는 디렉터리. 남겨 둔 lead worktree 안의 scope도 여기에 해당 | 같음 |
| `tracked-area` | 그 밖(untracked, ignored, 아직 없는 경로, submodule을 포함하는 scope 포함) | lead 가능 |

- **겹침**: 한 scope가 다른 요청 scope와 같거나, 경로 세그먼트 단위로 조상일 때. `a/b`와 `a/bc`는 겹치지 않고, `.`은 모든 것과 겹친다.
- **깨끗함**: main, 채워진 submodule 전부, nested repo 전부에서 `git status --porcelain --ignore-submodules=none`이 비어 있어야 한다.
- **post-checkout 훅**: submodule이 있는 저장소에서 기본 또는 유효(`core.hooksPath`) post-checkout 훅에 `submodule`이 들어 있으면 refuse한다. 그런 훅은 모든 lead worktree에서 submodule을 초기화하기 때문이다.
- **`off_limits`**: 기본값은 submodule 경로와 nested repo의 합집합. S1에서는 `off_limits_by_request`가 그 요청의 검증된 선택 모듈만 제외한다. `ownership_scopes`는 같은 모듈의 서로 다른 하위 경로도 동시에 실행하지 않도록 gitlink 전체를 예약한다. ignored nested repo는 계속 제외한다.
- **등록된 worktree는 nested repo가 아니다.** root 아래의 등록된 linked worktree(예: `.claude/worktrees`에 남겨 둔 blocked/failed lead)는 nested_repos와 off_limits에서 빠지고 status 검사도 받지 않는다. 그래서 그 안의 커밋되지 않은 작업이 새 wave를 막지 않는다(`batch_preflight.py:228-235, 274, 490-496`, REQ `:290-293`).
- **fail closed**: 읽을 수 없는 디렉터리, git의 "could not open directory" 경고, 일반 파일이 아니거나 읽을 수 없는 `.gitmodules`는 모두 `refuse`다. nested repo를 숨길 수 있기 때문이다(`batch_preflight.py:66-68, 92-109, 169-196, 258-275`, REQ `:245-252`).
- preflight는 아무것도 쓰지 않는다. git 호출은 GIT_* 환경 변수를 모두 지우고 `--no-optional-locks`, `-c core.fsmonitor=false`, `LC_ALL=C`, 120초 timeout으로 실행한다. 예기치 않은 crash도 `refuse` 보고를 낸다.

### 10.3 lead의 수명주기

`task-lead.md`는 `experimental: { cacheTtl: 1h }`를 선언해 긴 lead의 prompt-cache 수명을 한 시간으로 요청한다.

```
W = realpath(pwd); git rev-parse HEAD == coordinator HEAD ?  (다르면 blocked, 쓰기 없음)
wbs(open,wbs_id=<lead id>,workspace=W) → wbs(plan,wbs_id=<lead id>,workspace=W) → develop (pytest -n 4,
  중첩 review/QA 는 W 에서 스폰 → 영수증이 W 의 과제에 바인딩)
→ wbs(verify,wbs_id=<lead id>,workspace=W) → wbs(close,wbs_id=<lead id>,workspace=W)
→ git add / git commit 한 번, trailer "Harness-Task: <task_id>" (push, merge, --amend 금지)
→ fenced JSON {task_id, worktree, branch, commit, verdict, blocked_reason}
```

- 모든 WBS 실행 action에 `workspace: W`와 해당 wbs_id를 넘긴다. **빠뜨리면 조용히 main checkout의 과제를 건드린다.** MCP 서버 프로세스가 main에 있기 때문이다.
- 금지 사항:
  - AskUserQuestion. 대신 선택지와 trade-off를 담아 blocked로 반환한다.
  - install_verified(lead에서는 생략), 부모 WBS의 실행·계획·마이그레이션 변경.
  - `git submodule`은 status만 허용하고, `--recurse-submodules`는 금지한다.
  - 선택되지 않은 submodule, nested repo, off-limits 경로 편집. 준비 완료된 S1 모듈만 지정 범위에서 수정하고, 제공된 named branch에서 먼저 커밋한 뒤 outer gitlink를 커밋한다.
- Claude 훅은 payload `cwd`에서 저장소를 해석한다. Codex는 exact workspace/task/run 결과와 native coordinator 신원을 검증하고 그 coordinator의 direct reviewer/QA를 연결한다. Codex native cwd만으로 worktree 과제를 선택하지 않는다.
- 공개 verify는 명령을 실행하지 않는다. 성과 assertion은 workspace를 유지한 check로 실행하며, 독립 QA도 해당 worktree에서 검증한다.
- Claude Code는 agent isolation worktree를 항상 `<repo>/.claude/worktrees/<name>`에 만든다(`worktree.location` 설정은 읽지 않는다). wave 1의 branch 이름은 `worktree-agent-<id>`였다.

### 10.4 harvest와 제거

`batch_harvest.py`는 다음 조건이 모두 맞아야 동작한다(`batch_harvest.py:53-255`).

- `--worktree`가 절대경로
- `resolve_registered_worktree` 통과(main이 아니고, 자체 manifest가 있음)
- worktree HEAD가 브랜치에 붙어 있음. rebase가 중간에 멈추면 HEAD가 detached된다. 첫 커밋에서 멈추면 그 HEAD가 main HEAD 자체라서 다음 조상 검사를 통과하므로, detached HEAD는 따로 거부한다
- worktree HEAD가 main HEAD의 조상(rebase + fast-forward를 마치면 두 HEAD가 같다. rebase를 마쳤어도 fast-forward 전이면 거부한다)
- task_id가 `^TASK__[A-Za-z0-9._-]+$`
- task 디렉터리와 learnings에 symlink가 없고, 일반 파일과 디렉터리만 있음

동작:

- `<W>/doc/harness/tasks/<id>`를 temp dir과 rename을 거쳐 `doc/harness/archive/batch/<id>`로 복사한다. 같은 트리가 이미 있으면 아무것도 하지 않고, 다른 트리가 있으면 거부한다.
- learnings 행은 flock 아래에서 main `learnings.jsonl`에 append한다. 이미 있는 줄과 정확히 같은 줄은 제외한다.
- harvest 자체는 Git 상태를 건드리지 않는다. 별도의 guarded S1 제거 경로는 state-managed finish만 호출하며, archive·모듈 보존 증명과 전체 파일 검사를 통과해야 단일 `--force`를 쓴다. 일반 worktree 제거와 branch `-d`는 그대로다.

**harvest는 제거 전에 해야 한다.** `git worktree remove`는 gitignored 파일(`doc/harness/tasks/`, learnings)까지 지운다.

### 10.5 host 가시성

host git 클라이언트(예: drvfs 위의 GitKraken)에서는 branch와 commit만 본다. worktree 폴더를 host에서 열지 않는다. lead worktree가 하나라도 존재하는 동안에는 `git worktree prune`이나 host 쪽 정리를 하지 않는다. host에서는 컨테이너 경로가 모두 없는 것으로 보이고, prune은 잠기지 않은 모든 worktree의 메타데이터를 지운다. `git clean -ffdx`도 ignore 설정과 관계없이 `.claude/worktrees/`를 지운다(`batch/SKILL.md` § f) Host visibility, REQ § Host visibility).

### 10.6 알려진 한계

- b.4의 이유: worktree가 제거된 뒤 늦게 도착한 lens SubagentStop은 cwd를 main으로 해석한다. main에 열린 과제가 있으면 그 과제에 기록될 수 있다.
- helper는 잠긴 worktree만 unlock하고 remove 거부·예외 시 남아 있는 등록된 worktree에 원래 이유로 relock을 시도한다. 복원 실패도 원래 실패와 함께 보고한다. 실행 중인지 직접 판별하지 않으므로 코디네이터는 lead가 반환한 뒤에만 helper를 호출해야 한다(`batch_finish.py`의 `_cleanup`, REQ § Known limits).
- preflight의 `worktrees_ignore`는 symlink를 푼 뒤 git이 보는 경로를 검사한다. 저장소 밖으로 나가는 경로는 통과하며, 안쪽 경로가 ignore되지 않았으면 refuse한다(`batch_preflight.py`의 `worktrees_ignore`).
- preflight와 finish helper의 clean 검사는 `status.showUntrackedFiles=no`와 관계없이 미추적 파일을 포함한다. helper의 native worktree remove도 이 설정을 덮어써서 검사 이후 생긴 미추적 파일을 보호한다. standalone preflight는 HEAD 작업 상태나 이전 배치 예약을 검사하지 않는다. 풀의 `batch_state.py claim`이 destination ref, 진행 중 Git 작업, 기록된 retained scope 겹침을 추가로 검사한다. finish helper는 main detached HEAD와 lead의 진행 중 rebase를 별도로 거부한다.
- submodule(git 2.43): 초기화 후 plain remove가 거부되고 `--force`는 모듈 저장소까지 지운다. S1은 명시적으로 선택한 populated direct module만 private clone으로 준비하며, refs·태그·reflog·중간 gitlink 객체를 main의 영구 refs로 보존한다. 공유 config를 바꾸는 `deinit`은 금지한다. ff 직후 checkout을 갱신하며, 중단 복구는 정확한 witness를 일반 dirty 검사보다 먼저 확인한다. 재귀 모듈·미초기화 모듈·nested repo는 지원 범위 밖이다. 상세 계약: [선택 모듈 S1](REQ__batch-submodule-support.md).
- `batch_harvest.py` 단독 호출은 `HarvestError`만 잡지만 helper는 모든 harvest 예외를 `kept`로 보고하고 worktree를 남긴다. learnings append가 archive rename 뒤에 있어 append 실패 시 archive는 이미 존재할 수 있다. 통합 후 정리 실패는 `integrated_tip`과 `cleanup`으로 구별하고 원인을 고친 뒤 `--resume`한다. branch만 남았으면 `git branch -d`로 마친다.
- helper는 시작 시 main의 symbolic ref를 기록하고 rebase 뒤 checkout이 detached되거나 다른 branch로 바뀌면 거부한다. 성공 확인과 merge 실패·timeout 뒤 통합 확인 모두 최초 branch ref를 기준으로 하며, 현재 HEAD에만 반영된 tip을 통합됐다고 보고하거나 그 근거로 lead를 정리하지 않는다. 실패 경로에서는 harvest·정리를 진행하지 않는다. 상태가 `error`여도 확인된 `integrated_tip`이 있으면 통합된 것이다. 확인 자체가 실패하면 `reason`에 통합 여부 미확인을 명시한다. null tip만으로 미통합이라고 보고하지 않는다.
- 공유 파일: wave 1에서 lead의 `forbidden_paths`는 CHANGELOG와 batch REQ를 제외했고, lead에 따라 CONTRACTS.md/CLAUDE.md/README도 제외했다. lead마다 목록이 달랐다. 통합 과제가 쓴 것은 `plugin/CHANGELOG.md`와 batch REQ다. 이 파일들은 모두 소스 확장자가 아니므로 `forbidden_paths`에 넣어도 gate가 강제하지 않는다(§5.6). 계획상의 관행일 뿐이다.
- 측정값(wave 1): 9p main에서 preflight 7.59초/7.91초. 병합 후 full suite 1651 passed(`-n 8`, 53.7~57.9초). lead prompt-cache write의 89%가 5분 cache 만료 때문이었다. 현재 task-lead는 한 시간 cache를 요청한다.

  이 측정값과 위의 공유 파일 기록의 출처는 gitignore된 wave-1 과제 증거뿐이다: `doc/harness/tasks/TASK__batch-integrate-wave1/PLAN.md:9, 17-31`, 같은 과제의 REVIEWS.jsonl, learnings.jsonl key `lead-cache-expiry-overhead`, `doc/harness/archive/batch/TASK__*/PROGRESS.md`. §2.1에서 말했듯 이 증거는 영속적이지 않다. batch REQ에는 이 측정값이 없다. 저장소에서 다시 확인하려면 REQ로 승격해야 한다.

---

## 11. 설치·setup·manifest

### 11.1 `install.py`

- PATH에서 `codex`와 `claude`를 찾아, 찾은 런타임마다 병렬로 설치한다. `--codex-only`와 `--claude-only`는 함께 쓸 수 없다(exit 2). CLI가 하나도 없으면 `no supported runtime CLI found`로 exit 2. 요약에는 런타임별로 ERROR/SKIPPED/DRY_RUN/APPLIED가 나오고, 하나라도 실패하면 exit 1이다(`install.py:1985-2102`).
- Codex는 먼저 `codex --version`이 `.codex-version`(0.130.0) 이상인지 확인한다.
- **기본 실행은 조건부다**(`install.py:824-987, 1517-1594, 1811-1868`).

  ```
  소유 트리의 __pycache__ 삭제 + group/other 쓰기 비트 제거
  → tempdir 에 기대 projection 빌드 → (type, mode, sha256) 인벤토리 비교
     (__pycache__, .pytest_cache 무시; 허용 모드 0600/0644/0755)
  → ERROR        : 수리 힌트와 함께 실패
  → SYNCHRONIZED : Claude = smoke(mirror) + marketplace update + plugin update → 'install skipped'
                   (marketplace update 실패 시 full install 로 넘어감)
  → STALE        : staged 트리 → 원자적 활성화
                   (swap 중 .<name>.previous 로 롤백 가능, 성공하면 삭제) → 모드 정규화 → smoke
                   → Claude: marketplace add/update, 첫 설치면 plugin install, plugin update,
                     mcp remove + mcp add harness(env 포함)
                   → Codex: cache 엔트리, config.toml 병합, hook trust, plugin_hooks 활성화
                     → marketplace 등록 → 실행 중인 로컬 daemon의 hook 설정 재로드
  ```

  `__pycache__`를 비교보다 먼저 지우는 이유: 비교가 `__pycache__`를 무시하므로, 오염된 `.pyc`가 있어도 SYNCHRONIZED로 판정될 수 있기 때문이다.
- **runtime smoke**: `install_smoke.py --plugin-root <트리>`가 설치된 훅 모듈을 import하고, 버리는 저장소에 대해 `background_hook.py`를 돌려 영수증 행이 생기는지 본다. 실패하면 ERROR("hooks cannot record receipts from this tree")(`install.py:928-957`). 어느 트리를 검사하는지는 런타임마다 다르다.
  - Claude: mirror `~/.claude/harness-dev/plugin`을 검사한다. SYNCHRONIZED와 STALE 둘 다 그렇고, STALE 경로에서는 `claude plugin update`가 cache를 갱신하기 **전에** 돈다. §2.3에서 말한 실제 실행 사본인 `<config>/plugins/cache/harness/harness/<version>`은 smoke 대상이 아니다. 그래서 cache가 깨졌거나 낡아도 smoke는 통과할 수 있다(`install.py:1811, 1844-1852, 1878-1883, 1948`).
  - Codex: cache 엔트리를 검사한다(`install.py:1670-1683, 1717-1731`).
- `--force`는 비교를 건너뛰고 항상 다시 동기화한다. payload 비교는 설정·레지스트리 상태를 보지 않는다. Claude 기본 실행은 그래도 marketplace와 plugin cache를 갱신하고, marketplace update가 실패하면(예: marketplace 누락) full install로 넘어가 marketplace 재등록, plugin 설치, MCP 재등록을 한다. 그 밖의 레지스트리 drift(`claude mcp` 항목, Codex config.toml/hook trust)는 payload가 STALE인 실행(기본 실행 포함)에서는 함께 다시 쓰이지만, payload가 SYNCHRONIZED로 판정된 기본 실행은 이를 건드리지 않으므로 그 경우에는 `--force`로만 고쳐진다(`install.py:1133-1148, 1654-1697, 1762-1778, 1820-1872, 1953-1980`).
- pytest 안에서(`PYTEST_CURRENT_TEST`) 실제 `~/.claude`/`~/.codex`는 건드리지 않는다.
- Codex 설치 후에는 native `config/batchWrite`의 빈 edits와 `reloadUserConfig: true`로 기존 세션의 hook을 다시 읽힌다. 파일과 trust만 갱신하면 기존 세션은 이전 hook을 유지할 수 있다. 로컬 daemon endpoint가 없으면 다음 세션에서 로드하고, 별도 config 경로에는 기본 daemon을 재로드하지 않는다. 재로드 실패 시 payload는 이미 설치된 상태지만 설치 결과는 실패다. daemon 문제를 해결한 뒤 `python3 install.py --codex-only --force`로 재시도한다. [native hook 요구사항](REQ__codex-native-hook-binding.md) 참조.
  재시도에서도 기존 `CODEX_HOME`과 명시한 `--config-path`를 유지한다. 설치기가 출력하는 재시도 명령은 설정 경로를 shell quoting하여 포함한다.
- Claude cache에는 오래된 `2.3.0-h*` 디렉터리가 쌓인다(Claude Code는 `+`를 `-`로 표시한다).

### 11.2 `install_verified.py`(Phase 7.8)

harness 소스 저장소(루트에 `install.py`, `plugin/`, `plugin-codex/`가 있음)에서만 실행한다. 리뷰와 QA PASS 영수증이 생긴 뒤, `wbs(action="close")` 전에 부른다. 모든 lens가 끝난 뒤에만 실행한다(§4.1).

```
python3 plugin/scripts/install_verified.py --task-dir doc/harness/tasks/<task_id>
  0. task_dir.mkdir(parents=True, exist_ok=True)   ← 검증 전 부작용
  1. ~/.cache/harness/install.lock 전역 flock
  2. 신뢰 확인: 두 플러그인 manifest 이름 'harness', Codex manifest repository 와
     git remote.origin.url 이 https://github.com/Luxusio/harness 로 정규화, 루트 install.py
     → 아니면 exit 2 'automatic install refused: <reason>'
  ┌ 3. task dir receipt 트랜잭션 진입 (8단계까지 락 유지 — 이 동안 영수증 훅은 대기)
  │ 4. task dir 검증: doc/harness/tasks/TASK__* 정규형, TASK.json 유효, 열린 활성 과제,
  │    세션 바인딩 (session id 가 'default' 면 hint 로 대체) → 아니면 exit 2
  │ 5. receipt_review_verdict == PASS  ('fresh review PASS required')
  │    receipt_runtime_verdict == PASS ('fresh QA PASS after review required') → 아니면 exit 3
  │ 6. .claude-plugin, plugin, plugin-codex, install.py, .codex-version 의 tracked+dirty 파일:
  │    모드·타입이 Git index 와 같고(644/755) 안전하고 읽을 수 있어야 함 → 아니면 exit 5
  │    → fingerprint → tempdir 복사 → 재확인
  │ 7. 스냅샷에서 python install.py (기본 조건부 실행)
  │    → 0 이 아니면 그 종료 코드를 그대로 반환 ('installer exited N')
  └ 8. 영수증·바인딩·payload 재확인 → 변화 있으면 exit 5
  → 'verified delivery PASS for <RECEIPTS.jsonl 스트림 fingerprint>'
```

근거: `install_verified.py:37-415`(특히 `:66-74, 304-315, 334-366, 391-395, 414`), `install.py:2004-2006, 2022-2025`.

- **종료 코드만으로는 원인을 알 수 없다.** stderr의 `automatic install refused: <reason>`이나 `installer exited N`으로 구분한다.
  - 2: 신뢰 확인 실패, 과제 디렉터리 검증 실패, 또는 install.py 자신의 2(예: `no supported runtime CLI found`).
  - 3: review PASS 없음, 또는 review 뒤 QA PASS 없음.
  - 5: payload 모드/타입이 Git index와 다름, payload가 안전하지 않거나 읽을 수 없음(설치 전), 또는 스냅샷·설치 도중 payload·영수증·바인딩이 바뀜(설치 후. 이 경우 install.py는 이미 적용됐을 수 있다).
  - 그 밖의 값(1 포함): install.py의 종료 코드가 그대로 전달된 것.
- **잘못 입력한 `--task-dir`의 부작용.** 0단계가 검증 없이 디렉터리를 만든다. 그래서 `doc/harness/tasks/TASK__typo` 같은 빈 디렉터리가 남는다. 이 디렉터리의 status는 `invalid`이고, prewrite gate의 `_has_open_tasks`는 invalid를 open처럼 취급한다. 그 결과 strict가 아닌 저장소(이 저장소 포함)에서 과제 없이 하는 소스 쓰기가 `no-active-task`로 거부되기 시작한다(`prewrite_gate.py:610-624, 761-773`, `_lib.py:3893-3897`).
- 마지막에 출력되는 fingerprint는 payload fingerprint가 아니라 RECEIPTS.jsonl 스트림 fingerprint다.
- 설치 영수증이나 중복 제거 상태는 남기지 않는다. 재시도할 때마다 실제 payload 일치를 다시 계산한다.
- **이 단계는 워크플로 산문이지 게이트가 아니다.** `wbs(action="close")`는 설치가 실행됐는지 확인하지 않는다.
- fork나 mirror처럼 origin이 다르면 거부된다.
- 8단계의 drift 메시지는 'install aborted'라고 하지만, 그 시점에는 install.py가 이미 적용된 뒤다.
- batch lead는 이 단계를 건너뛰고, 통합 과제가 병합 뒤 한 번 실행한다.

### 11.3 setup

`harness:setup`의 단계(`plugin/skills/setup/SKILL.md:1-419`):

```
context probe (manifest 존재? version 7 대비 UPGRADE_AVAILABLE)
→ Phase 1 저장소 census
→ Phase 2.0 프로젝트 인터뷰
     (--skip-interview, HARNESS_SKIP_INTERVIEW=1, summary+manifest 존재, MAINTENANCE 마커 중
      하나면 생략)
→ Q1-Q3 (type, 명령, QA 전략) — Q1은 census가 type을 확정하면, Q2는 build/test 명령이 자동 감지되면 생략하고, Q3은 전제 조건이 모두 갖춰지면 묻지 않고 자동 활성화 후 알린다. 각 질문 전에 `.interview-answers.json`을 확인해 인터뷰가 답한 것은 묻지 않으며, spawned 세션에서는 AskUserQuestion 없이 권장안을 고른다
     (예: 'Skip if census determined type clearly', 'Skip if build/test commands auto-detected')
→ Phase 2.5 health components 준비
→ Phase 3 bootstrap: manifest v7 템플릿, --project-doc-only --ensure-routing, critics,
   --gitignore-only, CONTRACTS.md 템플릿 복사(마커가 없으면 질문), --ensure-contract-import,
   contract_lint --quick
→ Phase 3.5 manifest 표적 수정 + --prepare
→ Phase 4 QA 인프라 확인
→ setup_finalize.py --qa-verified --runtime-verified → SETUP_OK 가 성공 신호
```

근거: `setup/SKILL.md:260-309`.

`setup_finalize.py` 모드(`setup_finalize.py:1040-1198`):

| 모드 | 동작 | 출력 |
|---|---|---|
| `--gitignore-only` | 운영 ignore만 적용. 실제 ignore를 검증하고, 실패하면 `.gitignore`를 되돌림 | `SETUP_GITIGNORE_OK: updated=<bool>`. 실패 시 `SETUP_ERROR:` + exit 1 |
| `--project-doc-only` + `--ensure-routing` / `--ensure-contract-import` | 프로젝트 문서에 routing 블록 / `@CONTRACTS.md` import | `SETUP_PROJECT_DOC_OK: updated=<bool>`. 실패 시 `SETUP_ERROR:` + exit 1 |
| `--prepare` | 마이그레이션, ignore, managed block 교체 | `SETUP_PREPARED` |
| `--check` | 쓰기 없음 | `SETUP_CHECK_OK` |
| full finalize(`--qa-verified --runtime-verified` 필수) | 검증과 마무리 | `SETUP_OK` |
| `--migrate-harness-version`(별칭 `--migrate-file-format`) | §11.5 | `HARNESS_MIGRATION_OK: version=7 updated=<bool>` |

근거: `setup_finalize.py:1066-1101`.

- `--project-doc` 기본값은 `AGENTS.md`(Codex)다. Claude에서는 `--project-doc CLAUDE.md`를 넘긴다.
- routing 블록 경계는 `<!-- harness:routing-injected -->` ... `<!-- /harness:routing-injected -->`이고, Durable Decision Documentation Gate를 포함한다. `@CONTRACTS.md` import는 첫 frontmatter 블록이나 첫 H1 뒤에 한 번만 넣는다(C-15: setup이 소유하는 것은 이 두 가지뿐이다).
- prepare와 full finalize는 CONTRACTS.md managed block을 배포 템플릿 블록으로 교체하고 `@CONTRACTS.local.md` import를 지운다. `--check`는 블록이 다르면 `CONTRACTS.md requires setup migration`으로 실패한다. 검증 항목에는 `doc/harness/critics/{plan,runtime,document}.md`, 플러그인 root의 REQUIRED_SETUP_RESOURCES, `contract_lint --quick` 통과가 들어 있다(`setup_finalize.py:596-631, 801-831, 1121-1139, 1171-1175`).

**이 소스 저장소에서 조심할 점**:

- `--project-doc CLAUDE.md --project-doc-only --ensure-routing`을 실행하면 root CLAUDE.md의 `# Operating mode`와 `# Template sync rule` 절이 사라진다. root의 routing 블록에 닫는 마커가 없어서, 다음 `## ` 제목(`## Memory`)까지를 블록으로 보고 교체하기 때문이다(메모리 안에서 재현: 78줄 → 40줄).
- prepare나 finalize를 실행하면 root CONTRACTS.md managed block이 템플릿 블록으로 덮인다. C-09와 C-14의 현재 동작 설명은 두 사본에 동기화돼 있다. 두 파일은 이미 C-13, C-14a, C-17(두 고정 쌍 단락과 REQ 링크 포함), C-18 문구가 다르다. 테스트는 두 사본의 contract id·제목 일치와 템플릿 lint 무결(hard/soft 0건)을 요구하고, 일부 조항 문구(C-09 batch 조항, C-17 Parking clause와 Enforced by 줄)가 두 사본 모두에 있는지도 확인한다. 그 밖의 본문 일치는 요구하지 않는다.

### 11.4 manifest 필드

템플릿 필드(`plugin/skills/setup/bootstrap.md:50-96`):

- `version`, `initialized_at`, `name`, `type`, `languages`
- `source_git_roots`(control root가 Git이 아닐 때만)
- `build_command`, `test_command`, `dev_command`, `entry_url`, `api_base_url`, `verify_commands`
- `qa.{default_mode, browser_qa_supported, desktop_qa_supported, ux_review_supported}`
- `health_components`

`validate_structure` 요구 사항: `version == 7`, `name`, `type`, `test_command` 또는 비어 있지 않은 `verify_commands`, `qa.browser_qa_supported`(`setup_finalize.py:552-572`).

**어떤 키가 기계적 효과를 갖는가.**

| 키 | 읽는 코드 | 효과 |
|---|---|---|
| `version`, `name`, `type`, `test_command`/`verify_commands`, `qa.browser_qa_supported` | `setup_finalize.py` validate_structure | setup 검증 |
| `test_command` | `health.py`(대체), `tool_routing.py`(힌트), `qa_codifier.py`(형식) | §13.8, §4.4 |
| `build_command` | `tool_routing.py` | 힌트 |
| `verify_commands` | `verify_runner.py` 내부/진단 실행기 | 참고용 실행; 공개 WBS verify 입력이 아님(§6.9) |
| `maintenance_default` | `compile_routing` | routing의 `maintenance_task`만 바뀐다. prewrite gate는 MAINTENANCE **파일**만 본다 |
| 최상위 `strict_compliance_requires_delegation` | `prewrite_gate.py`(`yaml_field`) | `no-active-task` 강화. `capabilities:` 아래 중첩 형태는 읽히지 않는다(§5.4) |
| `runtime.services` | `runtime_services.py` | §13.9 |
| `health_components` | `health.py` | §13.8 |
| `source_git_roots` | `setup_finalize.py` | setup |

근거: `_lib.py:2518-2546`, `prewrite_gate.py:758-764`, `health.py:64`, `tool_routing.py:113-114`, `verify_runner.py:32-56`, `runtime_services.py:54-130`, `setup_finalize.py:120, 346, 499-503, 552-572, 605`.

**어떤 코드도 읽지 않는 키(산문 전용)**: 런타임의 `qa.*_supported`(스킬 산문만 읽음), `browser.*`, `teams.*`, `tooling.*`, `profiles.*`, `project_meta.*`, `registered_roots`, `capabilities.delegation_mode`, `capabilities.strict_compliance_requires_delegation`(중첩 형태), `smoke_command`, `healthcheck_command`, `coverage_minimum`/`coverage_target`.

이 저장소의 manifest에는 `capabilities:` 아래에 `strict_compliance_requires_delegation: true`가 있지만, 중첩 형태라 gate가 읽지 않는다(§5.4). `test_command: "uv run pytest tests/ -x --tb=short"`는 있고 `health_components`는 없다. 그래서 `health.py`는 test_command로 대체되고, run Phase 4.5가 full suite를 한 번 더 돈다.

manifest의 `ux_review_supported` 주석은 ux-* 영수증이 close를 막는다고 설명한다. 그러나 ux-*는 SUPPORTED_LENSES에 없어서 `required_lenses`에 선언할 수 없고, 따라서 close 게이트가 될 수 없다(`_lib.py:46-52, 1311-1322`).

`doc/harness/manifest.yaml`은 `WORKFLOW_CONTROL_SURFACE`다. Write/Edit로 고치려면 활성 과제에 MAINTENANCE가 있어야 한다.

### 11.5 version과 migration(v7)

- `MANIFEST_VERSION = 7`(`setup_finalize.py:29-40`). **version을 올릴 때 고쳐야 할 곳**은 다음과 같다. `project_format_check.py`는 상수를 import하므로 고칠 필요가 없다(`project_format_check.py:14, 51, 61`).
  - `plugin/scripts/setup_finalize.py:40` `MANIFEST_VERSION`
  - `plugin/skills/setup/SKILL.md:82`와 `plugin-codex/skills/setup/SKILL.md:94`의 `_HARNESS_MANIFEST_VERSION=7`
  - bootstrap manifest 템플릿 `plugin/skills/setup/bootstrap.md:53`(`version: 7`, `:240`도 확인)
  - 산문: `plugin/skills/setup/SKILL.md:128`, `plugin-codex/skills/setup/SKILL.md:141`, `plugin/skills/setup/verify-report.md:44, 48, 142, 154`(`manifest schema: v7`), `plugin/scripts/README.md:15`. 이 산문 사본들은 `tests/test_manifest_version_drift.py`가 상수와 일치하는지 검사한다. `version: 7`을 직접 적은 테스트(`tests/test_project_format_check.py`, `tests/test_setup_finalize.py`)도 함께 고친다.
- migration 1-5는 manifest 스키마 변경이다. 옛 이름 변환(project→name, project_type→type, created→initialized_at, 평평한 QA 키 → `qa:` 블록, default_mode 추론)이 여기에 들어간다.
- migration 6은 표준 운영 ignore를 적용하고, `harness_version` 키와 `.version`/`.format-version` 파일을 없앤다.
- migration 7은 `.claude/worktrees/`를 ignore에 추가한다. ignore만 하고, Harness가 이 경로에 쓰지는 않는다.
- version이 7보다 크면 'upgrade Harness' 오류이고, 정수가 아니면 오류다.
- `--migrate-harness-version`은 정확한 Git root가 필요하다. 동작 순서는 다음과 같다(`setup_finalize.py:210-274, 686-740`).
  1. `.gitignore`를 쓴다.
  2. `git check-ignore`와 `ls-files`로 모든 운영 경로가 실제로 ignore되고 추적되지 않는지 확인한다.
  3. manifest를 쓰고 옛 파일을 지운다.
  4. 실패하면 모두 되돌린다. 추적 중인 운영 경로가 있으면 `git rm --cached -r`을 하기 전까지 계속 실패한다.
- 알림은 SessionStart의 `project_format_check.py`가 한다(§4.2). 문구가 Codex식("invoke `$harness:run`")이다.

### 11.6 운영 ignore

`OPERATIONAL_IGNORES` 26개에는 `doc/harness/{tasks,goals,reviews,checkpoints,retros,runtime,archive,…}/`, `learnings.jsonl`, `runbook_candidates.yaml`, `health-history.jsonl`, `timeline.jsonl`, `local.yaml`, `.markers/`, `.claude/worktrees/` 등이 들어 있다. `render_gitignore`는 이 줄들이 어디에 있든 지우고, `.gitignore` 끝의 managed header 아래에 다시 붙인다(`setup_finalize.py:65-99, 156-175`).

---

## 12. 계약(CONTRACTS.md)

### 12.1 managed block과 matrix

- managed block 범위: `<!-- harness:managed-begin v1 -->`부터 `<!-- harness:managed-end -->`까지. 템플릿은 `plugin/skills/setup/templates/CONTRACTS.md`다. C-11 산문은 "하네스 릴리스 때 통째로 교체된다"고 하지만, 실제로 교체되는 것은 `setup_finalize.py --prepare`나 full finalize가 실행될 때(setup 복구·업그레이드)뿐이다. `--check`는 drift를 `CONTRACTS.md requires setup migration`으로 보고만 한다. 평범한 설치나 릴리스는 프로젝트의 CONTRACTS.md를 다시 쓰지 않는다. install.py는 이 파일을 건드리지 않는다(`setup_finalize.py:811-831, 1124-1139, 1171-1175`).
- § 0 설계 불변식 두 가지: 프로토콜 준수는 협상 대상이 아니다. 그 안에서는 처리량을 유지하는 가장 가벼운 경로를 고른다. 규칙이 무겁게 느껴지면 몰래 건너뛰지 말고 **규칙을 고친다.**
- § 1 matrix(`CONTRACTS.md:31-54`)는 "상황 → 적용 계약 → 수준(hard/soft/auto)" 조회표다. 태스크 시작, 보호 아티팩트 쓰기, `wbs(action="close")`, 짧은 승인, lane 전환 등을 찾아보면 된다.
- id는 18개다. C-16은 hygiene 서브시스템과 함께 제거됐다(`doc/harness/ADR__remove-hygiene-subsystem.md`).

### 12.2 계약 한 줄 요약과 실제 강제 지점

| id | 한 줄 | 실제 강제 지점 | 코드와의 차이 |
|---|---|---|---|
| C-01 | 정규 루프 start → plan → develop → QA → close | `prewrite_gate.py`(C-02), `wbs(action="close")`(missing_for_close) | — |
| C-02 | PLAN.md 전에 소스 쓰기 금지 | `prewrite_gate.py:783-792` | 활성 과제가 있을 때만(§5.4). micro/MAINTENANCE는 면제 |
| C-03 | 수락 의도는 PLAN.md에 | `wbs(action="plan")`. 영수증은 별도 | — |
| C-04 | close에는 영수증 기반 runtime PASS 필요 | `wbs(action="close")`(`harness_server.py:1779-1853`) | 별도 Git receipt gate 없음; pack 통합 증거와 진행률은 Git을 검사할 수 있음 |
| C-05 | 보호 아티팩트는 소유자만 쓴다 | `prewrite_gate.py` `PROTECTED_ARTIFACTS` + 마커 경로, `_lib` 프레임 검사 | 마커 두 경로는 산문 목록에 없음. Bash는 차단하지 않음. 전체 payload와 timeout fallback은 §4.6–4.7 |
| C-06 | note freshness는 명시적 점검 | `note_freshness.py --paths`(수동) | — |
| C-07 | 짧은 승인은 마지막 제안 전환만 승인 | **강제 없음(산문뿐)** | 계약이 가리키는 "Harness agent system prompt"(`plugin/agents/harness.md`)는 삭제됐다. `.claude/settings.json`은 여전히 그 에이전트를 main-thread agent로 지정한다 |
| C-08 | 답변 → 변경 lane 전환은 명시적으로 | **강제 없음(산문뿐)** | 같음 |
| C-09 | 세션 write focus는 하나이며 후속 성과는 WBS 의존 관계로 고른다 | MCP와 native PostToolUse | 별도 Goal 큐 없음 |
| C-10 | CLAUDE.md는 maintenance로 자기 관리 | MAINTENANCE 과제(산문) | 산문은 contract_lint 경고를 말하지만 lint는 CLAUDE.md를 읽지 않는다. **쓰기 gate도 없다**: CLAUDE.md는 WFCS도 소스 파일도 아니라서 Write/Edit가 항상 허용된다 |
| C-11 | managed block 손편집 금지 | `contract_lint.py`(마커 구조), `setup_finalize.py --check`(본문 drift, 사후) | 마커 변조(누락·중복·역순) 감지는 정확하다. 온전한 마커 **안쪽**의 편집은 lint가 잡지 못하고, 어떤 gate도 쓰기를 막지 않는다. "MAINTENANCE 과제만 additive Edit" 인가와 matrix의 "`CLAUDE.md` 편집 필요 … hard"는 산문뿐이다 |
| C-12 | 훅 fail-safe(`\|\| true`, timeout ≤10) | 관례 + `tests/test_hooks_json.py` | hooks.json에만 적용(Codex 생성 훅은 예외) |
| C-13 | SKILL.md ≤500줄, fanout 배치 | `contract_lint.py --check-weight`(soft) + `tests/test_contract_lint_real_tree.py` | 이 저장소에서는 사실상 hard다. 무게 초과든 soft lint 이슈든 real-tree 테스트가 suite를 실패시킨다. CLI 기본값 `--plugin-root ./plugin`으로는 plugin-codex를 검사하지 않는다 |
| C-14 | PASS는 순서가 맞는 hook-owned 영수증 필요 | `wbs(action="verify")`/`wbs(action="close")` | stop-only도 identity·출처를 검증. verdict는 stop payload에서 읽고 transcript final text는 비교하지 않음 |
| C-14a | 가능한 최고 검증 tier 실행 | develop Phase 7 | — |
| C-15 | setup은 사용자 소유 파일을 덮어쓰지 않음 | setup 절차 | 이 저장소에서는 routing 블록 교체가 위험하다(§11.3) |
| C-17 | 턴 종료 지침, 주차, 두 고정 쌍 | `wbs(action="close")`, `wbs(action="verify")`, `wbs(action="block")` | 산문은 상태 이름을 planning/implementing/verifying으로 적고, 코드는 open/blocked/closed/invalid를 쓴다(§17.2) |
| C-18 | 검증 위임은 지침이지 pre-tool gate가 아님 | 없음(의도적) | — |

근거: `CONTRACTS.md:45, 130, 140, 148, 160, 163-176`, `prewrite_gate.py:90-95, 111-118, 733-754`, `contract_lint.py:80-91, 227-234, 410-434, 452-458, 487-492`, `tests/test_contract_lint_real_tree.py:92-113, 253-282`, `.claude/settings.json:3`, `plugin/CHANGELOG.md:100`, `plugin/CLAUDE.md:9-10`.

### 12.3 `contract_lint.py`

`plugin/scripts/contract_lint.py:1-503`:

- **hard**: 마커 존재·유일·순서, 계약마다 필수 필드 다섯 개, id 중복.
- **soft**: matrix와 § 2의 id 집합 일치(matrix 링크는 파일 전체에서 추출), 계약 본문 어디서든 백틱으로 감싼 저장소 경로 힌트(`plugin/…`, `/…`, `./…`)가 일반 파일로 존재하는지, durable doc 속 `test_*` 참조 해석(`:58, 106-130, 250-257, 271-276`).
- `--quick`은 마커, matrix, 중복만 검사한다. `--check-weight`는 `<plugin-root>/skills/`와 `<plugin-root>/internal-skills/`의 SKILL.md가 500줄을 넘으면 soft 경고를 낸다. 기본 plugin-root는 `$HARNESS_PLUGIN_ROOT`, 없으면 `$CLAUDE_PLUGIN_ROOT`, 둘 다 없으면 `./plugin`이다(`contract_lint.py:449-458`).
- hard 발견 사항이 있을 때만 exit 1이다.
- 어떤 훅에도 등록되어 있지 않다. 호출하는 곳:
  - `bootstrap.md` 3.7.3(setup)
  - `setup_finalize.py` validate_structure(`--quick`, prepare/finalize/check 모두)(`setup_finalize.py:621-631`)
  - `golden_replay.py`(`:55-65, 109`)
  - `tests/test_contract_lint_real_tree.py`: root CONTRACTS.md와 템플릿이 같은 id와 제목을 선언하는지 확인한다(본문 일치는 요구하지 않음). SKILL.md 무게와 soft 이슈가 있으면 suite를 실패시킨다.

---

## 13. 메모리와 학습

### 13.1 CLAUDE.md 로딩

```
Claude Code 세션 시작
  └ root CLAUDE.md
      ├ @CONTRACTS.md      (import → 펼쳐짐)
      └ @doc/CLAUDE.md     (import → doc registry; doc/common/CLAUDE.md 는 일반 링크라 자동 로드 안 됨)
  └ SessionStart 훅: 배너, verification gap, format, drift
  └ 매 프롬프트: prompt_memory (doc gate, 과제/review 상태, runbooks, goal — 400자 상한)
  └ 스킬이 필요할 때 plugin/CLAUDE.md 의 절을 읽음 (런타임 규칙의 권위 문서)
Codex: AGENTS.md (역시 @CONTRACTS.md import)
```

근거: `CLAUDE.md:1-8`, `doc/CLAUDE.md:1-7`, `AGENTS.md:1-6`. root CLAUDE.md frontmatter의 `always_load:`를 읽는 코드는 없다.

root CLAUDE.md의 `## Memory` 규칙:

- 다른 기여자도 같은 실수를 피하려면 알아야 하는 교훈 → CLAUDE.md에 한 줄 bullet.
- 개인적인 교훈 → `~/.claude` auto-memory.
- 같은 주제 bullet이 3개 이상이거나 한 항목이 약 5줄을 넘으면 → `doc/memory/<topic>.md`로 분리하고 CLAUDE.md에는 포인터만 남긴다.

### 13.2 학습의 세 tier

| tier | 위치 | 성격 |
|---|---|---|
| 1 | CLAUDE.md | 매 세션 로드. 한 줄짜리만 |
| 2 | `doc/harness/patterns/*.md` | 커밋되는 패턴 문서 |
| 3 | `doc/harness/learnings.jsonl` | append-only 원시 신호. **gitignore 대상** |

근거: `plugin/CLAUDE.md:245-264`.

- 과제 2개 이상에서 참조된 Tier 2 사실은 사람이 Tier 1로 승격한다.
- `learnings.jsonl`에 행이 있다고 "captured"가 되는 것은 아니다. 커밋된 아티팩트로 승격하거나, 이유를 붙여 reject/defer해야 한다(`self-improvement.md:73, 160-171`).
- 이 원장에는 gate 진단 행(`gate-error`, `gate-crash`, `gate-bypass` 등)도 섞여 있다. 이들은 승격 대상이 아니다. 이 checkout에서는 약 3876줄 중 약 2834줄이 `gate-bypass`이고(§5.8, `HARNESS_SKIP_PREWRITE`가 계속 적용된 결과와 일치), 승격 가능한 타입은 약 30행이다.
- **QA 메모리**: `doc/harness/qa/QA_KNOWLEDGE.yaml`은 QA 에이전트가 읽고 append하는 메모리다. 그 `qa_notes`는 note_freshness 대상이다(§13.7, `plugin/agents/qa-cli.md:31, 141`).

### 13.3 `promote_learnings.py`

보고만 하고 영속적인 쓰기는 하지 않는다(`promote_learnings.py:1-744`).

- `PROMOTABLE_TYPES`(eureka, feedback, feedback-rule, harness-improvement, operational, pitfall) 중 정규 `ts`, `key`, `insight`, `task`, `task_run_id`를 가진 행만 센다. feedback-rule은 trigger, action, verification도 필요하다.
- key는 TASK.json run_id가 일치하고 status가 closed인 서로 다른 task/run마다 한 번만 센다. 기본 임계값은 2다.
- 자동 모드(`--task`, `--task-run-id`)는 그 run이 검증된 closed가 아니면 exit 2. 자동 모드에서는 방금 닫힌 그 run이 남긴 유효한 key만 후보가 되고, 그런 key가 없으면 승격 보고 없이 stale-file/contradiction 점검만 한다. 과거 backlog만으로는 보고하지 않는다(`promote_learnings.py:661-682`, `plugin/CLAUDE.md:255-258`).
- setup 스킬의 learning 예시와 run의 `qa-failure-pattern` 행은 task/task_run_id가 없거나 승격할 수 없는 타입이라 영원히 세어지지 않는다.

### 13.4 retro

- `retro.py --count-closed-since EPOCH`: 영수증으로 검증된 close 수를 센다.
- `--save`: `doc/harness/retros/<UTC-date>.md`를 원자적으로 쓴다. 같은 날 파일이 있으면 교체한다. 절은 commits, tasks, learnings다. docstring이 말하는 "Patterns" 절과 "append"는 코드에 없다(`retro.py:475-568`).

### 13.5 runbook

- 승인된 runbook은 `doc/harness/runbooks.yaml`(커밋), 후보는 `runbook_candidates.yaml`(ignore)에 있다.
- 하위 명령: `add-candidate`, `capture`, `approve`, `skip`, `list`, `render`. 비밀처럼 보이는 내용은 거부한다.
- render 상한은 1800자, 4항목, gotcha 2개다. 하지만 `prompt_memory`의 전체 400자 상한 때문에 실제로는 잘린다(§4.3). 이 저장소에서는 승인된 runbook 블록만 396자라서 뒤따르는 goal 힌트까지 밀어낸다(§1.2). 배경은 `doc/common/GUIDE__runbook-memory.md`.

```
실패한 명령 → 성공한 명령 → runbook_memory.py capture → 후보
→ close 때 Self-Healing Candidates 판단: approve(커밋 파일로 이동) / defer / skip
→ 매 프롬프트 prompt_memory 가 승인된 runbook 과 대기 id 주입
```

### 13.6 checkpoint

`write_checkpoint.py`가 `doc/harness/checkpoints/<TASK_ID>.md`를 원자적으로 쓰고, 마지막 쓰기가 이긴다. 내용은 git branch/HEAD/dirty 수, 과제 status, runtime_verdict, PROGRESS.md phase 줄, next action이다. develop Phase 3.3에서 호출한다.

### 13.7 note freshness(C-06)

`note_freshness.py`의 동작(`note_freshness.py:108-370`):

- `doc/**/*.md` frontmatter의 `freshness`와 `invalidated_by_paths`를 본다.
- 바뀐 경로가 정확히 같거나 디렉터리 접두사로 일치하면 `current`를 `suspect`로 바꾸고 `freshness_updated`를 찍는다. `freshness`가 없으면 current로 간주한다(`:256-262`).
- `doc/harness/qa/QA_KNOWLEDGE.yaml`의 `qa_notes`도 같은 방식으로 바꾼다(`:110-116, 362`).
- `--paths`나 `--from-git N` 없이는 아무것도 하지 않는다. 어떤 훅에도 연결되어 있지 않다. `stale`로는 바꾸지 않는다. docstring의 "기본 --from-git", "SessionStart에서 안전" 설명은 현재 코드와 다르다.
- develop Phase 5가 `--paths`로 호출한다.

**이 문서도 이 규칙을 따른다.** frontmatter의 `invalidated_by_paths`에 적힌 파일(또는 디렉터리 접두사 아래 파일)이 바뀌면 이 문서는 `suspect`가 된다.

### 13.8 health, hygiene

- `health.py`는 manifest `health_components`(`{name, command, weight}`)를 쓰고, 없으면 `test_command`로 대체한다. 각 컴포넌트를 repo root에서 셸로 실행하고 timeout은 300초다. 점수는 `10 × 통과 weight / 전체 weight`다. 선언된 것이 없으면 NOTE를 출력하고 exit 1. `--dry-run`은 아무 효과 없는 별칭이고, `health-history.jsonl`은 쓰지 않는다(`health.py:6, 31-118`).
- 문서 보관·계약 드리프트를 다루던 hygiene 서브시스템은 제거됐다(`doc/harness/ADR__remove-hygiene-subsystem.md`). `promote_learnings.py`의 stale-file/contradiction 점검(`[hygiene] …` 출력)과 [auto-maintenance](patterns/auto-maintenance.md)는 유지된다. `HARNESS_DISABLE_HYGIENE`를 읽는 코드는 없으며 현재 runtime 문서는 이 스위치를 안내하지 않는다.

### 13.9 runtime services

`plugin/scripts/runtime_services.py`(590줄)는 live 검증에 필요한 로컬 서비스를 관리한다(`runtime_services.py:1-22, 54-130, 451-590`, `plugin/scripts/README.md:22-36`).

- manifest `runtime.services[]`를 읽는다. 각 항목에서 코드가 읽는 주요 필드는 `name`, `command`, `cwd`, `env_file`, `required_env`(별칭 `env_required`), `env_setup_command`, `port`/`host`, `kill_port_on_conflict`, `healthcheck`, `ready_timeout_sec`, `stop_command`/`stop_timeout_sec`, `self_heal`(및 `install_command` 등 별칭), `restart_on_fail`이다. 예시에 나오는 `stop:` 키는 코드가 읽지 않는다(전체 목록: `doc/harness/runtime-services.md:34-59`, `runtime_services.py:196-203, 258-263, 349-357, 535-541`).
- 하위 명령은 `start`, `status`, `logs`, `stop`이다. 포트 충돌을 검사하고, healthcheck가 통과할 때까지 기다리고, 제한된 횟수만큼 self-heal한다.
- 상태는 `doc/harness/runtime/services.json`, 로그는 `doc/harness/runtime/logs/`에 남는다(둘 다 gitignore).
- 호출하는 곳: qa-api, qa-browser, develop runtime-smoke가 live 검증 전에 부른다(`plugin/agents/qa-api.md:42-43, 130`, `plugin/agents/qa-browser.md:52-57`, `plugin/skills/develop/runtime-smoke.md:11-12, 31, 93`).
- 규범: `doc/harness/runtime-services.md`.

---

## 14. Claude와 Codex 차이

| 항목 | Claude | Codex |
|---|---|---|
| 공개 진입 | 모델이 스스로 `Skill(harness:run)` 호출(run은 `user-invocable: false`, 사용자 slash 명령은 setup·batch뿐, §1.2) | `$harness:run`(`plugin-codex/skills/run` → `internal-skills/run`) + 매 프롬프트 `[harness-route]`, 암시적 호출 허용 |
| 프로젝트 문서 | CLAUDE.md | AGENTS.md |
| MCP 도구 이름 | `mcp__plugin_harness_harness__<tool>`(사용자 수준 서버는 `mcp__harness__<tool>`) | 접두사 없는 이름 |
| 훅 실행 트리 | plugin cache(Claude Code CLI가 채움) | plugin cache(install.py가 씀) |
| MCP 실행 트리 | 플러그인 서버는 cache, 사용자 수준 서버는 mirror | Codex mirror(config.toml `[mcp_servers.harness]`) |
| MCP 세션 identity | `.session-hint`를 읽음 | `CODEX_THREAD_ID`만. hint는 읽지 않지만 UserPromptSubmit 래퍼가 여전히 씀 |
| 활성 마커 | 세션 마커 + legacy `.active`(`wbs(action="open")`/`wbs(action="context")`가 씀) | 명시적 workspace 또는 exact main thread id 없음: `wbs(action="open")`는 default/legacy 마커, `wbs(action="context")`는 마커 없음, PostToolUse가 exact 마커를 씀. exact main identity가 있으면 공유 잠금 아래 eager 바인딩. 충돌 시 거절/fence |
| 영수증 수집 | SubagentStart/Stop 훅 → `background_hook.py` | MCP 안의 `codex_lifecycle_watcher`가 rollout을 tail |
| 영수증 source / runtime_id | `claude_hook` / `claude:<sid>:<aid>` | `codex_session_watcher:collaboration` / `codex:<coordinator-thread>:<call_id>:<child>` |
| lens 결정 | Agent `subagent_type`(→ agent_type). `name=`을 주면 대개 영수증 없음 | `spawn_agent`의 `task_name`(명명 규칙 필수) |
| completion 증거 | transcript 출처와 start attachment(final text 비교 없음) | coordinator FINAL_ANSWER == child task_complete 메시지 |
| prewrite gate | PreToolUse `Write\|Edit\|MultiEdit`에서 직접 실행(10초) | 래퍼가 3.0초 자식으로 실행. `apply_patch` 포함. 출력 없는 실패 시 C-05만 in-process fallback으로 거부, 다른 쓰기는 허용 |
| 훅 정의 | `plugin/hooks/hooks.json`(`\|\| true`, timeout ≤10) | `install.py`가 생성(`\|\| true` 없음, SessionStart 20초) |
| SessionStart | 배너 + gap + format + drift | watcher 등록 복구(1.25초 예산) + gap + format |
| UserPromptSubmit | `prompt_memory.py`(hint 기록) | `[harness-route]` + `prompt_memory.py`(`HARNESS_RUNTIME=codex`, hint 기록) |
| PostToolUse 힌트 | 평문 stdout(모델에 보이지 않을 가능성, §4.4) | additionalContext로 감쌈 |
| spawn 전 검사 | 없음 | `spawn_agent`의 bind 불가 review 이름 거부 |
| workspace / batch | 지원(`harness:batch`, task-lead) | internal batch와 task-lead, 등록된 workspace 및 인증된 native coordinator 지원; 실제 host capability 필요 |
| 에이전트 정의 | 17개(model, tools, isolation) | 16개(name/description만. 방법론 참고) |
| 스킬 본문 | `plugin/skills/*/SKILL.md` | 손으로 관리하는 별도 포트. 공용 보조 문서만 `plugin/skills`에서 복사(§2.2) |
| 설치 cache version | 내용 해시 `<base>+h<sha8>` | 수동 관리 version 문자열 |
| CLI 요구 | — | `codex ≥ 0.130.0` |
| qa-browser / qa-desktop | 사용 가능 | 🚧 deferred(`doc/harness/runtime-matrix.md`) |
| `ux-*` 스폰 | 조용히 영수증 없음 | watcher가 받아들였다가 record가 거부. sticky 오류 위험(코드에서 도출, 테스트 없음) |

---

## 15. 과제 하나 따라가기

harness 소스 저장소에서 standard 과제 `fix-parser-crash` 하나를 Claude로 처리하는 과정이다. 값은 예시다.

**0. 프롬프트 입력.** UserPromptSubmit → `prompt_memory.py`가 `doc/harness/tasks/.active_sessions/.session-hint`에 세션 ID `S`를 쓴다. 활성 과제가 없으므로 `[harness-goal]` 블록이 붙을 수 있다. 다만 이 저장소에서는 400자 상한에 잘려 보이지 않는다(§1.2). 모델은 routing 문구를 보고 `Skill(harness:run)`을 부른다.

**1. 시작 — run Phase 1.**

```json
wbs {"action": "open", "wbs_id": "fix-parser-crash"}
```

- `doc/harness/tasks/TASK__fix-parser-crash/TASK.json`
  ```json
  {"run_id":"<R, UUIDv7>","execution_mode":"standard",
   "required_lenses":["review-code","qa-cli"],"close_receipt_fingerprint":null}
  ```
- `.active_sessions/S.json`(`{session_id:S, task_dir, task_id, run_id:R, updated}`)과 `.active`.
- 반환: `run_action: "created"`, `task_context.source_write_allowed: false`, `missing_for_close`에 PLAN.md, review, QA.

이 시점에 `src/parser.py`를 Write하면 `[gate=prewrite rule=C-02-plan-first …]`로 거부된다.

**2. 계획 — plan(compact).** plan Phase 0.2가 `wbs(action="open")`를 다시 부른다. 같은 과제를 재개하므로 `run_action: "preserved"`와 `TASK_START_RUN_PRESERVED` 경고가 나오는데, 이것은 정상이다. 경계가 분명하고 위험이 낮아 compact로 간다. 서브에이전트는 없다.

```json
wbs {"action": "plan", "wbs_id": "WBS__fix-parser-crash",
            "plan": "# PLAN\nObjective: parser handles the reported input.\n```wbs\n[{\"id\":\"PARSER\",\"title\":\"Reported input parses successfully\",\"weight\":1}]\n```\n",
            "required_lenses": ["review-code", "qa-cli"]}
```

`PLAN.md`가 생기고 TASK.json의 lens가 정규 순서로 기록된다. run_id `R`는 그대로다.

**3. 구현 — develop Phase 1~6.** lane 표를 만들고 PROGRESS.md(7키)를 쓴다. 구현과 AC별 테스트를 하고, `write_checkpoint.py`로 `doc/harness/checkpoints/TASK__fix-parser-crash.md`를 남긴다. lint/build/smoke를 거쳐 계층 순서로 커밋한다. 이제 Write는 prewrite gate를 통과한다. PLAN이 있고, forbidden_paths에 걸리지 않고, `src/parser.py`가 REQ 휴리스틱의 어떤 경로 조각에도 맞지 않기 때문이다(§5.5).

**3.5 성과 검증.** 실제 변경과 검증 방법이 분명해지면 같은 PLAN의 PARSER leaf에
관측 가능한 assertion argv를 선택해 plan으로 게시한다. 새 파일 구조를 측정용으로
미리 정하지 않는다. 안정된 최종 소스에서 다음을 실행하고 context의 measured
weight를 확인한다. 실패나 source/run 변경은 credit을 주지 않는다.

```text
wbs { action: "check", wbs_id: "WBS__fix-parser-crash", node_id: "PARSER", timeout: 60 }
```

**4. 리뷰 — Phase 6.6(STANDARD라고 가정).**

- `Agent(subagent_type:"harness:defect-hunter")` 1개. 영수증 없음. JSON 배열을 돌려준다.
- 그 뒤 `Agent(subagent_type:"harness:code-reviewer")`. `name=`은 주지 않는다.

`RECEIPTS.jsonl`에 두 행이 생긴다(요약).

```
{"event":"started",  "lens":"review-code","runtime_id":"claude:S:A1","source":"claude_hook",
 "task_run_id":"R","verdict":"","summary":"", …}
{"event":"completed","lens":"review-code","runtime_id":"claude:S:A1","source":"claude_hook",
 "task_run_id":"R","verdict":"PASS",
 "summary":"VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nDETAIL_SHA256:<D1>", …}
```

`REVIEWS.jsonl`에는 `{"detail_sha256":"<D1>","detail":"<리뷰어 원문>"}`이 들어간다. FAIL이었다면 FIX_NOW를 구현자에게 돌려보내고 **새** code-reviewer를 스폰한다.

**5. QA — Phase 7.** 리뷰 PASS final을 받은 뒤 `Agent(subagent_type:"harness:qa-cli")`를 스폰한다(lens가 여러 개면 한 메시지로).

```
{"event":"started",  "lens":"qa-cli", "runtime_id":"claude:S:A2", …}          ← index 2 > review_index 1
{"event":"completed","lens":"qa-cli", "verdict":"PASS",
 "summary":"VERDICT: PASS\nDETAIL_SHA256:<D2>", …}
```

**6. 판정.**

```json
wbs {"action": "verify", "wbs_id": "TASK__fix-parser-crash"}
```

→ `review_verdict: "PASS"`, `runtime_verdict: "PASS"`, `missing_for_close: []`. next_action은 같은 루트의 `wbs(action="close")`로 안내한다. 이는 close 성공을 대신하지 않는다.

**7. 설치 — Phase 7.8(소스 저장소이므로).** 모든 lens가 끝난 뒤에만 실행한다(설치 중에는 receipt 락이 잡혀 있다, §4.1).

```
python3 plugin/scripts/install_verified.py --task-dir doc/harness/tasks/TASK__fix-parser-crash
→ verified delivery PASS for <RECEIPTS.jsonl 스트림 fingerprint>
```

**8. 마무리 — 8.5~8.7 → health → close.**

- 8.5: learnings 분류, runbook 후보.
- 8.6: durable docs(REQ/GUIDE/ADR/POLICY가 바뀌었으면 documentation-review). 여기서 `wbs(action="verify")`를 다시 부른다.
- 8.7: 필요하면 `doc/changes/<date>-fix-parser-crash.md`.
- run Phase 4.5: `health.py --dry-run`. 이 저장소에는 `health_components`가 없으므로 test_command로 full suite를 한 번 더 돈다.

```json
wbs {"action": "close", "wbs_id": "TASK__fix-parser-crash"}
```

→ TASK.json에 `close_receipt_fingerprint: "sha256:<RECEIPTS.jsonl fingerprint>"`가 기록된다. legacy `.active`가 지워진다. MCP 프로세스의 세션 ID(Claude에서는 대개 `default`)로 `default.json` 삭제도 시도하지만, 이 예에는 그 파일이 없다. 1단계에서 만든 `S.json`은 closed 과제를 가리킨 채 남는다. 과제가 열려 있지 않은 동안은 무해하다(§3.7). 이후 status는 `closed`다. 단, RECEIPTS.jsonl이 바뀌면 `invalid`가 된다.

**9. close 이후.** `learnings.jsonl`에 append → `promote_learnings.py --task TASK__fix-parser-crash --task-run-id R`(보고) → 조건이 되면 `retro.py --save` → 완료된 diff 커밋 → 완료 보고(커밋 해시와 설치 결과 포함).

---

## 16. 문제 해결 FAQ

**`[gate=prewrite rule=C-02-plan-first …]`**
활성 실행에 PLAN.md가 없다. 같은 wbs_id로 plan 스킬과 `wbs(action="plan")`을 사용한다. 작은 작업은 한 성과로 충분하다. 공개 open은 execution_mode를 받지 않으며, 기존 micro 기록의 내부 PLAN 면제를 새 호출 옵션으로 선택할 수 없다. PLAN 요구를 피하려고 fresh_run을 만들지 않는다.

**`rule=no-active-task`**
활성 과제가 없는데, manifest에 최상위 `strict_compliance_requires_delegation: true`가 있거나 열린/invalid `TASK__*`가 있다. 이 저장소에서는 중첩 키가 읽히지 않으므로 후자다. install_verified를 잘못된 `--task-dir`로 돌려서 생긴 빈 디렉터리일 수도 있다(§11.2). 공개 복구 경로는 `wbs(action="open")`으로 같은 요청의 실행을 여는 것이다(§5.8).

**`rule=workflow-control-surface`**
`plugin/CLAUDE.md`, `hooks.json`, `harness_server.py`, `prewrite_gate.py`, `_lib.py`, `manifest.yaml`을 고치려 했다. 활성 과제 디렉터리에 `MAINTENANCE` 파일이 필요하다. 메시지의 owner인 `maintain-skill`이라는 스킬은 없다. close 뒤의 manifest 자동 수정도 여기서 막힌다(§7.8).

**`rule=C-05-protected-artifact`**
TASK.json, PLAN.md, WBS.json, RECEIPTS.jsonl, 과제의 REVIEWS.jsonl, retired Goal JSON, migration archive, batch pool metadata, 마커, 런타임 transcript/rollout을 직접 쓰려 했다. 해당 MCP 도구나 훅 경로를 쓴다. batch 보관본의 파일도 basename 판정 때문에 여기에 걸린다.

**`rule=invalid-harness-workspace` / `symlink-outside-control` / `invalid-active`**
manifest나 경로에 symlink가 있거나, 마커가 가리키는 디렉터리가 잘못됐다. manifest 문제는 setup으로 복구한다. `invalid-active`라면 작업할 과제로 `wbs(action="open")`나 `wbs(action="context")`를 부른다. `_session_resumes`는 열려 있지 않은 과제를 가리키는 마커를 focus 없음으로 취급하므로 MCP 도구가 마커를 다시 쓴다. 마커 파일을 Write/Edit로 고치지 않는다. C-05 보호 대상이라 거부된다(`prewrite_gate.py:483-485, 774-781`, `harness_server.py:1612-1647`).

**`rule=C-REQ-observable-doc-required`**
경로 휴리스틱(§5.5)에 걸렸는데 REQ 링크가 없다. PLAN.md에 `doc/<area>/REQ__*.md` 경로(`doc/harness/REQ__*.md`도 인정)를 넣거나, `doc/harness/` 밖의 `doc/<area>/REQ__*.md`(깊이 2) 안에 `source: task: <id>` back-link를 쓴다. `doc/harness/` 아래 REQ에 적은 back-link는 스캔되지 않는다(§5.5). MAINTENANCE나 micro로는 피할 수 없다.

**`rule=scope-lock-forbidden`**
PROGRESS.md `forbidden_paths`에 걸렸다. 메시지는 allowed_paths에 추가하라고 하지만 그것으로는 풀리지 않는다. 걸린 forbidden 항목을 지우거나 좁혀야 한다. `HARNESS_DISABLE_SCOPE_LOCK=1`은 설정되어 있는 동안 계속 적용된다.

**Write가 거부되어야 하는데 통과했다**
다음을 확인한다. 활성 과제가 없고 열린/invalid `TASK__*`도 없는가(이 저장소는 strict 키가 `capabilities:` 아래 중첩돼 읽히지 않으므로 이 경우 조용히 허용된다, §5.4). 활성 과제에 MAINTENANCE 파일이 있거나 `execution_mode: micro`인가(plan-first 면제, §5.8). 대상이 소스 확장자가 아닌가(§5.3). cwd가 중첩 git 저장소 안인가(§3.8). gate가 timeout됐는가(Claude hook 강제 종료는 허용될 수 있지만 Codex 자식 timeout은 보호 아티팩트를 fallback으로 거부, §4.1, §4.7). `HARNESS_SKIP_PREWRITE`나 `HARNESS_DISABLE_SCOPE_LOCK`이 환경에 남아 있을 수도 있다(§5.6, §5.8).

**`wbs(action="close")` 거부, `missing_for_close`가 비어 있지 않음**
PLAN.md, `completed review verdict…`, `completed QA verdict…` 중 무엇이 남았는지 본다. 흔한 원인:
- (a) QA를 리뷰 PASS 전에 스폰했다.
- (b) 리뷰를 다시 돌려서 QA 순서가 깨졌다.
- (c) rerun이 아직 진행 중이거나, completion 없이 남은 `started` 행이 lens를 가리고 있다(§6.7).
- (d) lens를 `name=`으로 스폰했다.
- (e) 필요한 lens(예: 나중에 추가한 review-security)가 아직 돌지 않았다.
- (f) verdict 형식이 틀려 PENDING이 됐다. summary의 `UNREADABLE`/`INVALID`/`FIRST_LINE:`과 next_action의 nonparsing 진단(§6.9 표)으로 확인한다.
- (g) 옛 QA FAIL이 남아 있다. 그 lens의 새 QA를 스폰하기 전까지 runtime은 FAIL이다. 새 QA가 도는 동안(그 lens의 최신 행이 `started`)에는 옛 FAIL이 가려져 PENDING이 되고, 새 QA가 끝난 결과로 다시 판정된다(§6.7, §6.8).

**lens는 끝났는데 영수증이 하나도 없다**
원인 후보:
- 세션이 프롬프트를 한 번도 보내지 않아 `default.json`에 바인딩됐다. hint가 생긴 뒤 `wbs(action="context")`로 다시 바인딩하면 **그 뒤에 스폰하는** lens부터 기록된다(§3.7).
- 동시 세션(Claude/Codex 혼용 포함) 때문에 hint가 다른 세션을 가리킨다.
- 이름을 주고 스폰했다.
- lens가 끝날 때 install_verified가 receipt 락을 쥐고 있었다(§4.1).
- `.receipt-capability-broken`이 있다(import 실패. learnings의 `gate-crash`를 확인).
- 설치된 트리에 훅이 없다(`RECEIPT_HOOKS_UNAVAILABLE`).

이미 끝난 lens를 영수증을 얻으려고 다시 돌리지 않는다. 영수증 없는 결과는 NON-ATTESTING으로 표시하고 실제 결과대로 진행한다. 실제 FAIL은 수정하고, 실제 BLOCKED_ENV는 바로 `wbs(action="block")`로 주차하며, 실제 review PASS일 때만 QA를 1회 진행한다. 실제 QA PASS 뒤에 `wbs(action="verify")`를 1회 부르고, 순서 있는 영수증이 PASS면 close한다. 필요한 증거가 여전히 없을 때만 그 응답의 next_action에서 고정 쌍을 그대로 복사해 `wbs(action="block")`한다(§6.11). **예외**: `doc/harness/.receipt-capability-broken`이 있으면 어느 고정 쌍도 쓰지 않는다. 두 쌍 모두 lens가 돌아 결과를 냈다고 주장하는 문구이기 때문이다. 이때는 구체적인 외부 blocker 사유와 해제 조건(런타임 복구·재설치)으로 `wbs(action="block")`한다(`run/SKILL.md:15-24`, `plugin/CLAUDE.md:104`). 이 상태에서도 MCP next_action은 여전히 두 쌍을 제시한다(§6.11).

**활성 과제가 있는데 프롬프트 훅이 아무것도 출력하지 않는다**
영수증 스트림을 읽다가 예외가 났다(§4.3). learnings의 `gate-error`(source `prompt_memory`)를 보고 RECEIPTS.jsonl의 잘못된 행을 찾는다.

**`completion-already-recorded` / 재개했는데 반영되지 않음**
같은 에이전트를 재개해서는 첫 completion을 바꿀 수 없다. 새 lens를 스폰한다.

**`task_start refused: another open task owns the resolvable session focus`**
C-09. 지금 focus를 가진 과제를 닫거나 `wbs(action="block")`로 주차한다. 동시 세션이라면 legacy `.active`, hint 충돌, 또는 fresh_run 뒤에도 남은 옛 세션 마커(§3.7)가 원인일 수 있다.

**`task is closed`**
닫힌 과제다. 다시 열려면 `fresh_run: true`를 쓴다(증거 폐기).

**`task_start refused invalid TASK.json …`**
TASK.json의 스키마가 지원되지 않거나 안전하지 않다. Harness는 마이그레이션하거나 다시 쓰지 않는다. 새 task_id를 고른다.

**`fresh_run is not repair authority`**
두 경우 중 하나다. 영수증 저장소가 안전하지 않거나 파싱되지 않는 경우(`task_start refused unsafe or unsupported receipt storage`), 또는 terminal 아티팩트가 invalid인 경우(`task_start refused invalid terminal task artifacts`, 예: close 뒤 영수증 변경, 안전하지 않은 BLOCKED.md)다. fresh_run으로는 고칠 수 없다. 권한, 소유자, 하드 링크, 스키마를 먼저 확인한다. 스키마 오류라면 reader가 낡았을 수 있으니 새 세션이나 재설치를 먼저 해 본다.

**`receipt storage integrity unavailable`**
원인 후보:
- task dir이나 그 위 3단계 조상 중 하나가 없거나, symlink이거나, 디렉터리가 아니다.
- task dir이 현재 uid 소유가 아니거나 group/world 쓰기가 가능하다. 느슨한 umask로 `wbs(action="open")`가 만든 디렉터리가 흔한 원인이다(§3.2).
- 트랜잭션 도중 경로 요소의 identity가 바뀌었다.
- 영수증 줄이 JSON/UTF-8/객체가 아니다(§6.2).

근거: `_lib.py:2579-2626`.

**close했던 과제가 `invalid`가 됐다**
close 뒤에 RECEIPTS.jsonl 바이트가 바뀌었거나, 이 행들을 거부하는 낡은 reader가 읽고 있다(§3.3). 이 상태의 완료 증거는 유효하지 않다.

**`start_status: ready_with_warnings` + `TASK_CONTEXT_DEFERRED`**
`wbs(action="open")`를 다시 부르지 말고 `wbs(action="context")`를 부른다.

**`WORKSPACE_NOT_REGISTERED_WORKTREE` / `wrong_type`**
workspace가 정규 절대경로가 아니거나, 등록된 같은 저장소 worktree가 아니거나, worktree에 manifest가 없다. Codex도 workspace를 지원하지만 정확한 task/run 바인딩은 native PostToolUse 신원 검증이 맡는다.

**구형 Goal 데이터가 남아 있다**
`wbs(action="migrate")`로 미리 확인한 뒤 명시적으로 apply한다. 구형 API를 호출하거나 완료 상태를 수동으로 만들지 않는다.

**주차한 WBS를 다시 진행해야 한다**
실제 unblock 조건을 충족한 다음 같은 WBS를 open으로 재개한다. 진행 중인 pool이 있으면 먼저 정확한 pool을 통합한다.

**`batch_preflight.py`가 exit 1**
JSON `verdict`가 `adjust`인지 `refuse`인지 본다. exit 2는 사용법 오류이고 JSON이 없다.

**`install_verified.py` exit 2 / 3 / 5 / 그 밖**
- 2: 신뢰 확인 실패, 과제 디렉터리 검증 실패(정규형 아님, TASK.json invalid, 이 세션의 열린 활성 과제 아님), 또는 install.py 자신의 exit 2.
- 3: review PASS가 없거나, review 뒤의 QA PASS가 없다.
- 5: 설치 전에 payload 모드/타입이 Git index와 다르거나 payload가 안전하지 않다. 또는 스냅샷·설치 도중 payload·영수증·바인딩이 바뀌었다(이 경우 install.py는 이미 적용됐을 수 있다).
- 그 밖(1 포함): install.py의 종료 코드가 그대로 전달된 것이다.

stderr의 `automatic install refused: <reason>`이나 `installer exited N`으로 구분한다.

**`install.py` exit 2 / "hooks cannot record receipts from this tree" / "incomplete Codex payload"**
CLI가 없거나 옵션이 충돌했다 / smoke한 트리(Claude는 mirror)에서 영수증을 만들지 못했다 / Codex payload 필수 파일이 빠졌다. `claude mcp` 항목이나 Codex config.toml drift는 payload가 STALE이면 기본 실행에서도 다시 쓰인다. 기본 실행이 SYNCHRONIZED로 판정해 건너뛸 때만 `--force`가 필요하다(§11.1).

**`CONTRACTS.md requires setup migration` / `upgrade Harness`**
managed block이 템플릿과 다르다(이 소스 저장소에서는 §11.3 주의) / manifest version이 7보다 크다.

**SessionStart의 `[verification-gap]`, `[harness-version]`, `[drift]`**
qa-browser가 필요한데 PASS가 없다 / migration이 필요하거나 버전을 검사할 수 없다 / 실행 중인 scripts가 소스와 다르다(재설치 후 새 세션).

**Codex `spawn_agent` 거부**
review처럼 보이는 task_name이 review-code/review-security로 bind되지 않는다. §8.4 명명 규칙을 따른다.

---

## 17. 규범 문서 찾아가기

### 17.1 주제별 색인

| 주제 | 규범 문서 |
|---|---|
| 전체 계약, matrix | [CONTRACTS.md](../../CONTRACTS.md) |
| 런타임 규칙(루프, MCP, micro, 주차, 소유권, 학습, 환경 변수) | [plugin/CLAUDE.md](../../plugin/CLAUDE.md) |
| TASK.json, RECEIPTS/REVIEWS 스키마, 스냅샷, 게이트 | [patterns/ADR__consolidated-task-artifacts.md](patterns/ADR__consolidated-task-artifacts.md) |
| 영수증이 소스 스냅샷에 묶이지 않는 이유 | [patterns/ADR__receipt-gates-without-source-snapshots.md](patterns/ADR__receipt-gates-without-source-snapshots.md) |
| Codex 영수증 수집, identity, completion | [patterns/ADR__single-direct-codex-receipt-protocol.md](patterns/ADR__single-direct-codex-receipt-protocol.md) |
| Codex payload 차이, 문제 해결, apply_patch | [codex-payload-deltas.md](codex-payload-deltas.md), [codex-troubleshooting.md](codex-troubleshooting.md), [apply-patch-matrix.md](apply-patch-matrix.md) |
| 두 고정 쌍 | [REQ__gate-does-not-demand-impossible-evidence.md](REQ__gate-does-not-demand-impossible-evidence.md) |
| 주차와 재개 | [REQ__task-blocked-is-the-park-record.md](REQ__task-blocked-is-the-park-record.md) |
| 세션 마커 바인딩 | [REQ__subagent-receipt-session-binding.md](REQ__subagent-receipt-session-binding.md) |
| spawn 단위 영수증, 재개하면 쓰기 없음 | [REQ__subagent-lifecycle-receipt-boundaries.md](REQ__subagent-lifecycle-receipt-boundaries.md), [../common/REQ__process__subagent-lifecycle-cleanup.md](../common/REQ__process__subagent-lifecycle-cleanup.md)(close_agent 규칙) |
| transcript start-attachment 모양 | [REQ__subagent-completion-receipt-transcript-shape.md](REQ__subagent-completion-receipt-transcript-shape.md), [../common/REQ__process__subagent-receipt-binding.md](../common/REQ__process__subagent-receipt-binding.md) |
| verdict 결속 | [REQ__lens-verdict-contract-ownership.md](REQ__lens-verdict-contract-ownership.md), [REQ__lens-verdicts-bind-when-the-lens-complied.md](REQ__lens-verdicts-bind-when-the-lens-complied.md), [REQ__verdict-binding-survives-output-framing.md](REQ__verdict-binding-survives-output-framing.md), [REQ__unbound-verdict-names-the-spawn-shape.md](REQ__unbound-verdict-names-the-spawn-shape.md) |
| review 원문 | [REQ__selective-review-detail.md](REQ__selective-review-detail.md) |
| 영수증 기능 장애의 관찰 가능성 | [REQ__receipt-subsystem-failures-are-observable.md](REQ__receipt-subsystem-failures-are-observable.md), [REQ__harness-announces-lost-receipt-capability.md](REQ__harness-announces-lost-receipt-capability.md), [REQ__receipt-capability-diagnosis.md](REQ__receipt-capability-diagnosis.md), [REQ__bytecode-cache-cannot-disable-receipts.md](REQ__bytecode-cache-cannot-disable-receipts.md), [REQ__unreadable-worker-state-is-not-recordable.md](REQ__unreadable-worker-state-is-not-recordable.md), [../common/REQ__process__receipt-watcher-fail-closed.md](../common/REQ__process__receipt-watcher-fail-closed.md) |
| 이름 준 spawn 흔적 | [REQ__runtime-surfaces-name-the-actual-blocker.md](REQ__runtime-surfaces-name-the-actual-blocker.md) |
| 런타임 규범 문장의 단일 출처(TRUST_BOUNDARY) | [REQ__runtime-normative-text-has-one-source.md](REQ__runtime-normative-text-has-one-source.md) |
| 계약 강제 주장은 실행 가능해야 함 | [REQ__contract-enforcement-claims-are-executable.md](REQ__contract-enforcement-claims-are-executable.md) |
| prewrite gate, scope lock | [patterns/prewrite-gate.md](patterns/prewrite-gate.md), [patterns/scope-lock.md](patterns/scope-lock.md)(동작과 한계는 §5.8) |
| REQ 수집 | [REQ__req-capture-with-or-without-task.md](REQ__req-capture-with-or-without-task.md) |
| Bash/브라우저를 gate하지 않는 이유 | [patterns/ADR__selective-pretool-dispatch.md](patterns/ADR__selective-pretool-dispatch.md) |
| SessionStart 훅은 harness 밖에서 no-op | [REQ__session-start-hooks-no-op-outside-harness.md](REQ__session-start-hooks-no-op-outside-harness.md) |
| Goal | [patterns/native-goals.md](patterns/native-goals.md), [patterns/auto-loop.md](patterns/auto-loop.md) |
| AC 병렬 폭, test-author | [patterns/ADR__within-task-parallel-width.md](patterns/ADR__within-task-parallel-width.md) |
| batch, worktree lead | [REQ__parallel-tasks-via-worktree-leads.md](REQ__parallel-tasks-via-worktree-leads.md), [../../plugin/skills/batch/SKILL.md](../../plugin/skills/batch/SKILL.md), [../../plugin/agents/task-lead.md](../../plugin/agents/task-lead.md) |
| 설치 트리 모드, 설치 트리 smoke | [REQ__installed-tree-modes-are-installer-owned.md](REQ__installed-tree-modes-are-installer-owned.md), [REQ__guards-are-verified-where-they-run.md](REQ__guards-are-verified-where-they-run.md) |
| manifest version과 migration | [REQ__versioned-project-file-migrations.md](REQ__versioned-project-file-migrations.md) |
| runtime services | [runtime-services.md](runtime-services.md) |
| QA 메모리와 회귀 포착 | [REQ__qa-notes-carry-their-own-invalidation.md](REQ__qa-notes-carry-their-own-invalidation.md), [patterns/regression-capture.md](patterns/regression-capture.md) |
| 테스트 결정성, mutation 범위 | [REQ__test-suite-determinism-under-xdist.md](REQ__test-suite-determinism-under-xdist.md), [REQ__mutation-scope-follows-the-diff.md](REQ__mutation-scope-follows-the-diff.md) |
| hygiene 제거(C-16 없음) | [ADR__remove-hygiene-subsystem.md](ADR__remove-hygiene-subsystem.md) |
| 구현자·리뷰 게이트 설계 | [../designs/minimal-implementer-and-code-review-gate.md](../designs/minimal-implementer-and-code-review-gate.md) |
| 단계 절차 | [run](../../plugin/skills/run/SKILL.md), [plan](../../plugin/skills/plan/SKILL.md), [develop](../../plugin/skills/develop/SKILL.md), [setup](../../plugin/skills/setup/SKILL.md) |
| 에이전트 출력 계약 | `plugin/agents/*.md` |
| 런타임별 기능 | [runtime-matrix.md](runtime-matrix.md)(역할 목록은 이 가이드 §8도 참고) |
| MCP 도구 이름 | [../common/GUIDE__mcp-tool-naming.md](../common/GUIDE__mcp-tool-naming.md) |
| 동작을 고정하는 테스트 | `tests/test_hooks_json.py`, `tests/test_contract_lint_real_tree.py`, `tests/test_review_agent_contracts.py`, `tests/test_batch_*.py`, `tests/test_worktree_workspace.py`, `tests/test_prewrite_gate_cross_checkout.py`, `tests/test_prewrite_gate_dormant.py`, `tests/test_session_hint_marker_binding.py`, `tests/test_task_context_binds_resuming_session.py`, `tests/test_skill_visibility.py` |

**규범이 아닌 문서와 잔재.**

- `status: draft`로 규범이 아닌 문서: [AUTO_ROUTING.md](AUTO_ROUTING.md), [SPEC.md](SPEC.md), [IMPORT_LIST.md](IMPORT_LIST.md).
- `doc/harness/critics/{plan,runtime,document}.md`: `setup_finalize`가 존재를 요구하지만, 어떤 에이전트나 스킬도 읽지 않는다(`setup_finalize.py:596-627`).
- `doc/harness/review-overlays/`: `plugin/`에서 참조하지 않는다.

### 17.2 코드와 문서가 어긋나는 곳(작성 시점)

이 표를 비롯해 이 문서의 어느 절(§3.7, §5.8, §7.9, §12 등)에 적은 코드·문서 어긋남이든 모두 2026-09-28 시점에 관찰한 사실이고 규범이 아니다. Wave 2에서 고친 C-09/C-14, gate, runtime 문서와 CI의 항목은 제거하고 해당 절을 현재 소스와 다시 대조했다. 아래는 그 범위 밖에 남은 관찰이며 규범은 여전히 각 문서다.

| 문서 | 문서의 주장 | 코드의 실제 |
|---|---|---|
| `CONTRACTS.md` C-07/C-08 | "Harness agent system prompt"가 강제 | 그 에이전트(`plugin/agents/harness.md`)는 삭제됐다. `.claude/settings.json`은 여전히 `harness:harness`를 main-thread agent로 지정하고 없는 `Skill(harness:maintain)`을 허용한다. 강제 수단 없음 |
| `CONTRACTS.md` C-10 | contract_lint가 CLAUDE.md 편집을 soft-warn | lint는 CLAUDE.md를 읽지 않는다. 쓰기 gate도 없다 |
| `CONTRACTS.md` C-11 / matrix "`CLAUDE.md` 편집 필요 … hard" | MAINTENANCE 과제만 additive Edit, hard | 마커 변조 감지는 정확하다. 온전한 마커 안쪽 편집은 lint가 잡지 못하고, CONTRACTS.md/CLAUDE.md 쓰기를 막는 gate도 없다. `--check`로만 사후에 드러난다 |
| `CONTRACTS.md` C-11 | managed block은 릴리스 때 교체 | `setup_finalize --prepare`/finalize 때만 교체. 설치·릴리스는 건드리지 않는다 |
| `CONTRACTS.md` C-13 | soft | 이 저장소에서는 real-tree 테스트가 실패시킨다. CLI 기본값은 plugin-codex를 검사하지 않는다 |
| `CONTRACTS.md` C-17 | 상태 planning/implementing/verifying | open/blocked/closed/invalid만 존재 |
| `plugin/CLAUDE.md:31` | task_blocked가 "이 세션의" 마커를 지움 | MCP 프로세스 세션(대개 default) 마커와 legacy만 지운다 |
| `plugin/CLAUDE.md:99` | AC가 모두 passed/deferred면 close | AC 상태는 코드에 없다. 영수증 기반 missing_for_close |
| `plugin/CLAUDE.md:104`, `run/SKILL.md:24` | capability 마커가 있으면 고정 쌍 금지 | `RECEIPT_UNAVAILABLE_NEXT_ACTION`에 두 쌍이 들어 있다 |
| `patterns/ADR__consolidated-task-artifacts.md:321` | 잘못된 영수증에는 fresh-run 안내 | reader 재로드를 먼저 권하고, fresh_run은 수리 수단이 아니다 |
| `develop/SKILL.md:188` | forbidden → BLOCK | 소스 확장자 파일에만 적용(§5.6) |
| `REQ__subagent-receipt-session-binding.md` | task_context의 count 필드, `.receipts.lock` | 둘 다 없다(fd flock) |
| `run/SKILL.md:156-159` | qa-browser를 건너뛰면 close가 막힘 | 선언된 lens만 게이트 |
| `run/self-improvement.md:61-71` | close 뒤 manifest 자동 수정 | prewrite gate가 거부(§7.8) |
| `plan/intake.md:59` | routing의 ui_scope/must_read/compat | `compile_routing`이 반환하지 않는다 |
| `doc/harness/manifest.yaml` `capabilities.strict_compliance_requires_delegation` | strict 모드 | 중첩 키라 gate가 읽지 않는다(§5.4) |
| `doc/harness/manifest.yaml`의 `ux_review_supported` 주석 | ux-* 영수증이 close를 막음 | ux-*는 지원 lens가 아니다 |
| README 에이전트 표 | documentation-review 누락 | critic-document 역할은 존재하고 documentation-review로 스폰된다(§8) |

어긋남을 발견하면 규칙을 몰래 건너뛰지 말고 **규칙(문서나 코드)을 고치는 과제**를 연다(`CONTRACTS.md` § 0).
