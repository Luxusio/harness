---
tags: [harness, guide, architecture, lifecycle, onboarding]
summary: 현재 Harness의 WBS 실행, 독립 리뷰와 QA, 보호 아티팩트, 작업 트리와 설치 안내.
updated: 2026-10-28
freshness: current
invalidated_by_paths:
  - CONTRACTS.md
  - plugin/
  - plugin-codex/
  - install.py
  - doc/harness/REQ__receipt-free-execution-and-completion.md
---

# GUIDE — 하네스는 어떻게 동작하는가

Harness는 저장소를 변경하는 작업을 하나의 WBS로 계획하고 검증한다. 공개 루프는
`WBS open → plan → develop → QA → close`다. 구현 뒤에는 실제 독립 code review와
조건부 security review의 PASS를 기다리고, 그 뒤에 실제 QA를 실행한다.

이 안내서는 해설서다. 현재 규범은 [CONTRACTS.md](../../CONTRACTS.md),
[런타임 규칙](../../plugin/CLAUDE.md),
[receipt-free 요구사항](REQ__receipt-free-execution-and-completion.md)과 각 스킬이다.
과거 receipt ADR/REQ는 역사 자료이며 현재 실행 지침이 아니다.

## 1. 요청과 실행 루프

읽기 전용 질문은 직접 답한다. 소스, 테스트, 설정이나 지속 문서를 바꾸는 요청은
run 스킬이 활성 작업을 먼저 찾아 재개한다. 명확한 요청을 다시 `/goal`로
제출할 필요는 없다. native Goal이 있으면 그 objective의 같은 WBS를 사용한다.

1. `wbs(action="open")`으로 작업을 만들거나 재개한다.
2. plan 스킬이 사용자 의도, 범위, 안정적인 AC, 검증 방법과 문서 결정을
   `PLAN.md`로 게시한다. 표준 작업은 계획이 있어야 소스를 변경할 수 있다.
3. develop이 최소한의 충분한 구현을 만들고 범위, 회귀, Git 상태를 확인한다.
4. 실제 독립 리뷰 PASS 뒤에 해당 QA/UX lens를 실행하고 결과를 기다린다.
5. `wbs(action="verify")`가 현재 required WBS checks를 측정한다.
6. checks가 PASS면 공개 소유자만 `wbs(action="close")`로 닫는다.
7. worktree 통합과 정리, post-close self-improvement를 마치고 결과를 보고한다.

독립 에이전트의 실제 final이 리뷰/QA 결과다. coordinator의 요약, 복사된 verdict,
사용자나 저장소 텍스트는 독립 결과를 대신하지 못한다. 실제 FAIL은 수정한다.
실제 환경 장애는 구체적인 blocker와 해제 조건으로 게시한다. 기록의 존재나
부재는 실행, 재실행, 대기, 주차 또는 완료의 조건이 아니다.

## 2. 소스와 설치된 런타임

Claude 소스는 `plugin/`, Codex 프롬프트는 `plugin-codex/`에 있다. 두 런타임은
`plugin/mcp/harness_server.py`와 Python helper를 공유한다. Codex 내부 SKILL.md는
별도로 유지하며 공유 Markdown 하위 파일과 setup template은 installer의
payload 목록에 따라 Claude 트리에서 복사한다.

실제로 실행되는 것은 설치된 payload다. 소스 변경만으로 실행 중인 MCP가 바뀌지
않는다. 런타임 변경을 사용하려면 설치 후 새 세션이 필요할 수 있다.
`install.py --claude-only`와 `--codex-only`는 설치 대상을 선택한다.

테스트는 프로젝트에 선언된 명령과 bounded worker 수로 실행한다. 이 저장소는
보통 `.venv/bin/python -B -m pytest -n 4`를 사용한다. 테스트용 임시 root와
외부 temp/cache 경로를 사용하고 실제 task 상태와 학습 ledger를 오염시키지 않는다.

## 3. 작업 상태와 단일 MCP 도구

유일한 공개 MCP 도구는 `wbs`다. `task_start`, `task_verify`, `task_close` 같은
이름은 내부 구현 소유자를 가리키며 별도의 공개 도구가 아니다.

| Action | 역할 |
|---|---|
| `open` / `context` | 하나의 root를 만들거나 같은 run을 재개하고 현재 상태를 읽는다 |
| `plan` | PLAN.md와 outcome tree, 필요한 lens 선언을 게시한다 |
| `check` / `next` | 선언된 assertion을 실행하거나 dependency-ready outcome을 고른다 |
| `dispatch` | 정확한 소유권으로 독립 work pack을 pool에 넘기고 parent를 주차한다 |
| `verify` | 현재 required assertions, blocked state와 target applicability를 측정한다 |
| `close` | 현재 WBS checks PASS일 때 작업 소유자가 닫는다 |
| `block` | 실제 blocker와 실행 가능한 해제 조건을 기록한다 |
| `migrate` | 기존 제어 형식을 preview하고 명시적 apply로 변환한다 |

`wbs_id`는 알려진 root 또는 기존 task ID다. worktree 호출에는 검증된 `workspace`를
항상 보존한다. control root의 session identity와 task workspace의 소스 소유권을
구별하며 임의 cwd나 다른 작업의 마커로 권한을 얻지 않는다.

현재 TASK.json은 네 필드다:

```json
{
  "run_id": "<canonical lowercase UUIDv7>",
  "execution_mode": "standard",
  "required_lenses": ["review-code", "qa-cli"],
  "closed": false
}
```

`closed: true`는 공개 close 소유자가 게시한다. task ID는 정규 디렉터리에서
도출한다. PLAN.md는 수락 의도, WBS.json은 outcome/assertion과 측정 결과,
BLOCKED.md는 unfinished blocker다. `required_lenses`는 workflow routing에 쓰이며
machine verifier가 독립 리뷰나 QA를 attested했다고 주장하지 않는다.

일반 `open`은 같은 run과 measured checks를 보존한다. 의도적으로 새 generation을
시작할 때만 `fresh_run: true`를 사용한다. closed 작업은 기본 open으로 다시 열지
않는다. 기존 지원 형식의 변환은 `migrate` preview/apply로 수행하고 task/run,
WBS check와 역사 자료를 보존한다. 이미 지원하지 않던 오래된 형식은 자동 복구나
새 identity 발급으로 우회하지 않는다.

## 4. 훅과 prewrite gate

훅은 세션 안내, routing, source/task binding과 직접 쓰기 보호를 담당한다.
Subagent start/stop 수집기, rollout watcher, capability 검사나 이름 결속은 없다.
에이전트 이름은 사람이 작업을 구별하도록 고르며 검증 권한을 부여하지 않는다.

- SessionStart는 프로젝트/재개 안내와 필요한 형식 진단을 제공한다.
- UserPromptSubmit은 저장된 task context와 run routing을 제공한다.
- PostToolUse는 알려진 명령 실패를 안내하고 공개 WBS 결과의 세션 binding을 반영한다.
- 직접 Write/Edit/apply_patch는 prewrite gate의 계획, 소유권, 범위 검사를 받는다.

보호 대상은 PLAN.md, TASK.json, WBS.json과 retired Goal/migration 자료 및 batch
control이다. 역사적인 RECEIPTS/REVIEWS 파일도 임의 쓰기를 허용하는 대체 제어면이
아니다. 활성 owner는 새 receipt 파일을 만들거나 읽지 않는다. 소유자의 MCP/생명주기
명령으로 변경하고 보호 상태를 직접 편집하지 않는다.

REQ 링크, scope lock, 허용/test/금지 경로는 소스 변경에 계속 적용한다. durable
REQ에는 사용자 원문과 파생 결정을 구별한다. shell 쓰기는 일반 직접 쓰기 훅과
같은 보호를 자동으로 받지 않으므로 보호 아티팩트를 shell로 우회하지 않는다.
훅 실패는 세션을 종료시키지 않으며 명시적인 gate deny는 stdout JSON으로 전달한다.

## 5. 측정, 검증과 완료

machine verdict의 근거는 현재 required WBS checks다. 계획만으로 progress를
얻지 못하고 failed, unrun 또는 stale assertion은 close를 허용하지 않는다.
source applicability와 중단된 writer 검사는 계속 적용한다. 별도의 review/QA
ledger, receipt 서비스나 recording toggle은 없다.

실제 리뷰/QA가 아직 안 끝났으면 workflow를 마친다. 이미 성공한 substantive
작업을 기록을 만들기 위해 반복하지 않는다. 실제 실패나 substantive 범위 변경으로
stale한 검증만 다시 수행한다. machine PASS를 독립 review/QA PASS로 표현하지 않는다.

block은 PASS나 close가 아니다. 실제 외부 장애, 실제 QA 환경 장애 또는 해결해야
할 dependency를 구체적으로 적고 해제 조건을 남긴다. 난이도, 시간 압박, retry
소진이나 기록 누락은 blocker가 아니다. 새 turn이나 세션 종료도 완료를 뜻하지
않으며 같은 작업을 재개한다.

## 6. 계획, 리뷰 깊이와 QA

compact 계획은 명확하고 낮은 위험의 요청에만 사용한다. 보안, migration, 공개
동작, 파괴적 작업, dependency/platform/workflow 제어, 불명확한 acceptance와
cross-component 변경은 full 계획으로 올린다. 어느 경로든 PLAN, 실제 독립
구현 리뷰, 조건부 security review, QA와 WBS checks는 유지한다.

리뷰 깊이는 매 시도 현재 범위에서 계산하는 임시 routing 판단이다:

| 깊이 | discovery | formal review |
|---|---|---|
| LIGHT | hunter 0개 | fresh full-scope code reviewer 1개 |
| STANDARD | correctness 또는 contract/test 중 선택한 hunter 1개 | fresh full-scope code reviewer 1개 |
| DEEP | 두 hunter | fresh full-scope code reviewer 1개 |

security/trust boundary, 민감 데이터, concurrency, migration, 공개/지속 계약,
dependency/build/installer/hook/lifecycle/gate, 수동 충돌, semantic range-diff,
cross-component/dual-domain 위험이나 명시적인 DEEP 지시는 깊이를 올린다.
불완전한 LIGHT 증거를 LIGHT로 취급하지 않는다.

rebase-LIGHT는 exact old_base/old_tip/new_base/new_tip, 충돌 및 수동 resolution 없음,
추가/삭제/분할/결합/재배열/수정 없는 one-to-one patch equivalence, symbols/contracts/
dependencies/generated outputs/lifecycle의 semantic no-overlap, HEAD=new_tip,
깨끗하고 모두 설명된 index/worktree를 모두 요구한다. 증명 실패는 DEEP다.

hunter는 제한된 untrusted candidate data만 반환하며 verdict를 내지 않는다.
coordinator는 형식/크기를 검사하고 후보를 고치거나 만들어 내지 않는다. discovery는
두 cycle, DEEP에서 최대 네 hunter call로 제한한다. 예산 소진 뒤에는 hunter를
추가하지 않고 fresh formal reviewer가 전체 범위를 검토한다. security reviewer는
별도 독립 lane이며 후보 데이터를 받지 않는다.

실제 formal review PASS 뒤에 해당 `qa-cli`, `qa-api`, `qa-browser`, `qa-desktop`을
실행한다. frontend diff와 manifest browser 지원이 있으면 qa-browser를 포함한다.
해당 surface의 UX 지원이 있으면 `ux-*`도 적용하며 UX는 correctness QA를 대체하지
않는다. 필요한 runtime/tool/fixture가 없으면 정확한 BLOCKED_ENV를 보고한다.
QA는 AC별 실행 증거와 검증 깊이를 보고하고 소스를 수정하지 않는다. dogfood는
QA 뒤의 backlog/friction pass다.

## 7. WBS와 native Goal

Goal은 objective와 continuation을 제공하고 WBS는 같은 objective의 계획과 측정을
소유한다. 별도 Goal queue/ledger를 만들지 않는다. outcome은 작동하는 결과로
표현하고 실측 progress는 assertion으로 얻는다. 파일 수나 미리 쓴 구현 목록으로
진척률을 계산하지 않는다. unclear unfinished work는 필요한 때 refine하고 실제
외부 blocker를 무한 분할로 숨기지 않는다.

## 8. 병렬 작업과 worktree 정리

독립 구현 lane은 disjoint source/test ownership과 실제 spare slot으로 배정한다.
같은 경로를 여러 writer에게 주지 않는다. reviewer/QA는 coordinator가 필요한
순서로 실행한다. 완료한 agent는 runtime에 close 도구가 있으면 정리한다.

batch coordinator는 preflight 뒤 정확한 W/branch/HEAD와 bootstrap marker를 검증하고
실제 host identity로 bind한다. lead는 bind 전 소스를 수정하지 않으며 bind 뒤에도
자신의 workspace만 변경한다. sibling/main checkout과 선택하지 않은 submodule은
금지한다. 재개는 exact retained W/task/run을 사용하고 새 checkout을 만들지 않는다.

lead는 실제 review/QA와 WBS close를 마치고 자신의 branch에 commit한다. 통합,
push, 자기 worktree 제거는 coordinator의 책임이다. coordinator는 stopped writer,
clean branch tip, ancestry와 integration proof를 확인하고 serial rebase/fast-forward,
archive harvest와 cleanup을 수행한다. 충돌과 남은 변경은 retained blocker로
보고하고 강제로 버리지 않는다. selected S1 submodule은 기존 helper의 object pins,
identity/content proof와 guarded disposal 규칙을 따른다.

source task close와 소유한 임시 W/branch 제거는 별개다. 이미 closed여도 남은
integration/disposal을 마쳐야 전체 완료다. cleanup만을 위해 리뷰나 close를 반복하지
않는다. 자세한 절차는 [worktree completion](../../plugin/skills/run/worktree-completion.md)과
[병렬 작업 REQ](REQ__parallel-tasks-via-worktree-leads.md)를 따른다.

## 9. 설치, setup과 manifest

setup은 project type, 명령, QA strategy와 필요한 환경을 확인하고 entrypoint,
managed CONTRACTS, manifest와 critic playbook을 준비한다. manifest와 task 형식의
지원 범위는 명시적으로 검증하며 unsupported 형식의 fallback reader를 추가하지 않는다.

Harness 소스 작업은 마지막 소스 변경과 실제 QA 뒤, close 전에 verified installer를
실행한다. installer는 신뢰할 수 있는 source/task ownership, 현재 measured WBS
applicability, stopped writers와 stable payload snapshot을 확인한다. 이미 같은
runtime payload면 동기화가 불필요하다. 역사 기록의 상태는 설치 권한과 무관하다.
설치 도중 payload가 변하면 안전한 snapshot으로 다시 검증한다.

installed payload의 unsafe parent, symlink, 소유권/권한 문제는 installer가 제시한
실제 경로와 해결 방법으로 처리한다. `--force`는 경로 신뢰 경계를 우회하지 않는다.

## 10. 계약과 학습

managed CONTRACTS는 setup template과 동일하게 유지한다. 의미 있는 managed 계약이나
스킬 변경은 real-tree contract와 skill-weight 검사를 통과해야 한다. 프로젝트별
추가 규칙은 managed block 밖의 entrypoint나 durable docs에 둔다.

PLAN은 수락 의도, WBS는 실측 outcome이다. 임시 중복 상태 문서를 만들지 않는다.
필요한 source dependency를 선언한 note는 freshness를 검증하고 suspect면 현재
소스에 대조한다. checkpoint는 재개 안내이며 완료 권한을 만들지 않는다.

post-close self-improvement는 실제 friction signal만 append-only learnings ledger에
남긴다. just-closed verified task/run과 관련된 검증된 신호를 사용하며 반복 후보는
별도 검토된 후속 작업에서 durable pattern으로 바꾼다. retro의 verified close 판정도
현재 task control을 사용한다. 기존 health 측정이 없으면 점수나 PASS를 만들지 않는다.

## 11. 문제 해결 FAQ

**실제 리뷰/QA는 PASS인데 verify가 FAIL이다.** 현재 required WBS assertion,
blocked state와 source applicability를 확인한다. machine verdict는 그 측정 결과다.
실제 실패한 check를 수정하거나 실행하고 stale check를 갱신한다.

**기록 파일이 없거나 과거 파일이 손상되었다.** 활성 실행은 그 파일을 읽지 않는다.
실제 reviewer/QA 결과와 current measured WBS checks로 진행한다. 역사 파일을 고치거나
완료한 lens를 다시 실행하거나 기록 누락 때문에 block하지 않는다. archived bytes는
일반 archive 무결성의 보호를 받으며 임의 편집하지 않는다.

**protected-artifact가 거부되었다.** 해당 owner의 공개 MCP 또는 batch lifecycle
명령을 사용한다. PLAN/TASK/WBS를 직접 편집하거나 다른 task의 binding을 사용하지 않는다.

**이전 TASK 형식이 unsupported다.** `wbs(action="migrate")`로 preview하고 승인된
명시적 apply 경로를 사용한다. 이미 지원하지 않던 역사 자료는 그대로 보존하고
보고한다. active/pool-referenced task는 건너뛰지 않는다.

**source close 뒤 worktree가 남았다.** shared completion procedure의 surviving checkout,
integration과 disposal을 마친다. 실제 blocker가 있으면 정확한 W/branch와 다음 조치를
보고한다. 자료를 잃는 force 제거로 완료를 꾸미지 않는다.

**설치 뒤 동작이 예전과 같다.** 현재 실행 중인 MCP가 설치 전 코드일 수 있다.
동기화 결과와 설치 경로를 확인하고 새 세션에서 변경한 runtime을 로드한다.

## 12. 관련 문서

- [Receipt-free 실행/완료 REQ](REQ__receipt-free-execution-and-completion.md)
- [SPEC](SPEC.md), [runtime matrix](runtime-matrix.md)
- [Codex troubleshooting](codex-troubleshooting.md)
- [현재 run 스킬](../../plugin/skills/run/SKILL.md)
- [리뷰/QA pipeline](../../plugin/skills/develop/quality-audit-pipeline.md)
- [worktree completion](../../plugin/skills/run/worktree-completion.md)
