# repo-os 프로젝트 기획서

상태: Planning / Implementation Context  
목적: 이 문서는 `repo-os` Claude Code plugin을 계속 구현할 때 사용하는 **source context**다.  
우선순위: 코드보다 이 문서의 방향성과 제약을 우선한다. 문서와 구현이 충돌하면 먼저 문서를 갱신하거나, 변경 이유를 ADR로 남긴다.

---

## 1. 프로젝트 한 줄 정의

`repo-os`는 **단 하나의 setup 명령으로 저장소를 AI가 일할 수 있는 운영체제로 바꾸는 Claude Code plugin**이다.

사용자는 setup 이후에 별도의 플러그인 명령을 외우지 않는다. 그냥 평소처럼 요청한다.

예:
- 로그인 버그 고쳐줘
- payment 도메인 문서 정리해줘
- 이 부분 테스트 더 써줘
- 주문 모듈 리팩토링해줘
- 앞으로 auth 변경은 무조건 먼저 확인받아

그러면 Claude Code가 내부적으로:
1. 의도를 분류하고
2. 관련 컨텍스트를 로드하고
3. 위험도를 판단하고
4. 필요한 경우 사용자에게 확인을 받고
5. 코드/문서/테스트 작업을 수행하고
6. 검증하고
7. 프로젝트 지식에 반영한다.

핵심은 **모델이 똑똑해지는 게 아니라, 저장소가 점점 더 학습된 상태가 되게 만드는 것**이다.

---

## 2. 문제 정의

기존 AI 개발 흐름의 문제는 대부분 아래에 있다.

- 사용자가 매번 명령어와 workflow를 외워야 한다.
- AI가 이전 결정과 제약을 지속적으로 구조화하지 못한다.
- 버그 수정이 검증 루프 없이 끝나서 회귀가 생긴다.
- brownfield 프로젝트에서는 맥락이 부족해 위험한 수정을 하기 쉽다.
- 문서와 코드가 따로 놀아서 다음 세션에 지식이 날아간다.
- AI가 볼 수 없는 지식은 사실상 존재하지 않는다.

`repo-os`는 이 문제를 다음 방식으로 푼다.

- setup 한 번으로 repo-local control plane을 설치한다.
- 이후 자연어 요청을 내부 workflow로 자동 라우팅한다.
- 사용자 제약과 작업 중 발견한 사실을 repo 내부 기억으로 축적한다.
- 모든 변경은 검증 루프와 문서 동기화 루프를 거친다.
- brownfield에서는 먼저 이해하고 보호장치를 설치한 뒤 작업한다.

---

## 3. 제품 비전

### 북극성
사용자가 “무엇을 원하는지”만 말하면, Claude Code가 저장소 맥락과 이전 결정들을 바탕으로 **안전하게** 개발을 진행하는 환경을 만든다.

### 기대 효과
- 사용자는 workflow를 외우지 않는다.
- Claude Code는 프로젝트 고유 지식을 누적한다.
- 기능 개발, 버그 수정, 테스트 보강, 리팩토링, 문서화가 같은 운영 체계 안에서 돌아간다.
- 위험한 수정은 자동으로 멈추고 확인을 받는다.
- 다음 세션으로 갈수록 같은 질문을 덜 반복한다.

### 비전 문장
> One setup, plain-language development, repo-local memory, evidence-based validation.

---

## 4. 핵심 제품 원칙

### 4.1 공개 명령은 하나만
공개 surface는 오직 아래 하나다.

```text
/repo-os:setup
```

그 이후의 workflow는 모두 내부 skill / agent / hook 레이어에서 자동 실행되어야 한다.

### 4.2 세션 기억이 아니라 repo 기억
대화는 사라질 수 있어도 저장소에 남은 기억은 다음 세션에서도 활용 가능해야 한다.

### 4.3 증거 있는 것만 기억
기억은 아무 말이나 저장하는 것이 아니다.

좋은 기억의 기준:
- 사용자가 명시적으로 말함
- 코드/테스트/로그/브라우저로 검증됨
- 다음 작업에서도 중요함
- 틀리게 기억하면 위험함

### 4.4 문서보다 먼저 실행 가능한 제약으로 내리기
중요한 규칙은 가능하면 다음 순서로 강제한다.
1. 테스트
2. 아키텍처/정적 검사 규칙
3. 설정값/가드
4. 문서

### 4.5 모든 작업은 검증 루프를 닫아야 함
코드를 썼다는 사실보다, **수정이 검증되었다**는 사실이 중요하다.

### 4.6 brownfield는 먼저 이해하고 보호
기존 프로젝트에서는 바로 많이 고치지 않는다. 먼저 구조를 파악하고, 핵심 흐름을 보호하고, unknown을 명시한 뒤 작업한다.

### 4.7 아키텍처는 설명이 아니라 제약
레이어와 의존 방향은 문서만으로 지키지 않는다. 검사 규칙으로 내려야 한다.

---

## 5. 사용자 경험

## 5.1 최초 경험
사용자는 plugin을 로드한 뒤 한 번만 setup을 실행한다.

```text
/repo-os:setup
```

setup은 가능한 한 repo를 스스로 읽고 판단한 뒤, 꼭 필요한 질문만 한다.

질문 예시:
- 이 프로젝트의 주된 형태는 무엇인가
- build/test/dev 명령은 무엇인가
- 가장 중요한 사용자 여정 1~3개는 무엇인가
- 절대 건드리면 안 되는 구역이 있는가
- 어떤 변경은 항상 먼저 확인받아야 하는가

### 5.2 setup 이후 경험
그 뒤부터는 평소처럼 말한다.

예:
- “주문 취소 플로우 손봐줘”
- “로그인 관련 flaky test 정리해줘”
- “앞으로 payment 응답 포맷 바꾸지 마”

Claude Code는 요청을 내부적으로 라우팅해서 적절한 workflow를 선택하고, 필요한 맥락을 읽고, 작업 후 지식을 갱신한다.

---

## 6. 시스템 개념 모델

시스템은 크게 세 층으로 나뉜다.

### 6.1 Plugin Layer
Claude Code plugin 자체에 들어가는 운영 컴포넌트.

포함 요소:
- default main agent: `repo-os-orchestrator`
- hidden skills
- specialized subagents
- session start / stop hooks
- setup skill

### 6.2 Repo Control Plane
setup 이후 저장소 안에 생성되는 운영 레이어.

포함 요소:
- `CLAUDE.md`
- `.claude-harness/manifest.yaml`
- `.claude-harness/router.yaml`
- `.claude-harness/policies/*.yaml`
- `.claude-harness/state/*`
- `.claude-harness/workflows/*`
- `docs/*`
- `scripts/agent/*`

이 레이어가 있어야 AI가 매번 처음부터 추측하지 않고 일관되게 동작할 수 있다.

### 6.3 Project Evidence Layer
실제 작업의 증거와 실행 결과.

포함 요소:
- 코드
- 테스트
- 브라우저 결과
- 로그
- 메트릭/트레이스
- 문서
- diff

---

## 7. 요청 처리 런타임 루프

모든 요청은 아래 상태기계로 처리된다.

```text
사용자 요청
-> Intent Router
-> Scope / Context Loader
-> Risk Gate
-> Execute
-> Validate
-> Knowledge Sync
-> Summary / Ask for Approval if needed
```

### 7.1 Intent Router
요청을 다음 카테고리 중 하나 이상으로 분류한다.
- 설명/질문 응답
- 요구사항 정리
- 기능 개발
- 버그 수정
- 테스트 보강
- 리팩토링
- 문서화
- 정책/결정 기록
- brownfield 이해/인벤토리
- cleanup

### 7.2 Scope / Context Loader
관련 도메인과 파일 범위를 추정하고, 필요한 기억만 가져온다.

항상 우선 로드:
- 전역 제약
- 승인 규칙
- 최근 관련 결정

범위 기반 로드:
- 관련 도메인 문서
- 해당 경로와 연결된 observed facts
- 관련 버그 이력
- runbook

### 7.3 Risk Gate
다음 경우에는 자동 진행보다 사용자 확인이 우선이다.
- 요구사항 해석이 애매함
- 외부 동작이 바뀜
- DB / API contract / auth / billing / infra 영향
- 큰 삭제나 대규모 이동
- dependency upgrade
- brownfield에서 영향 범위가 불명확함

### 7.4 Execute
요청에 맞는 내부 workflow를 실행한다.

### 7.5 Validate
프로젝트 타입에 맞는 검증을 수행한다.

### 7.6 Knowledge Sync
이번 작업에서 확정된 규칙, 사실, 결정, runbook 노트를 저장소 기억으로 반영한다.

### 7.7 Summary
마지막에는 다음을 짧고 명확하게 보고한다.
- 무엇을 바꿨는가
- 무엇을 검증했는가
- 무엇이 확정되었는가
- 무엇이 아직 unknown인가
- 추가 확인이 필요한가

---

## 8. 핵심 workflow 구성

이 plugin은 setup 이후 보이는 명령을 늘리지 않고, 내부적으로 여러 workflow를 숨겨서 사용한다.

### 8.1 feature-workflow
사용자 기능 요청을 구현한다.

역할:
- 요구사항 정리
- 관련 문맥 로드
- 구현 계획 작성
- 코드 수정
- 테스트 추가
- 문서 갱신
- 검증

### 8.2 bugfix-workflow
버그 수정과 회귀 방지에 집중한다.

역할:
- 재현 경로 식별
- 변경 전 증거 수집
- 수정
- 변경 후 확인
- 회귀 테스트 추가
- 원인/교훈 기록

### 8.3 test-expansion
빠진 테스트를 보강한다.

역할:
- 경계 조건 식별
- 회귀 테스트 추가
- flaky 가능성 검토
- 기존 테스트 구조 정리

### 8.4 refactor-workflow
외부 동작을 유지하면서 구조를 개선한다.

역할:
- 의존 관계 파악
- 영향 범위 확인
- 구조 개선
- 규칙 위반 제거
- 검증

### 8.5 docs-sync
코드 변경과 문서 변경을 동기화한다.

역할:
- 관련 docs index 갱신
- domain docs 갱신
- ADR 필요 여부 판단
- runbook 갱신

### 8.6 decision-capture
사용자 대화에서 나온 지속적 규칙이나 결정을 구조화한다.

예:
- “retry는 2번까지만”
- “auth 변경은 항상 나에게 확인”
- “주문 취소는 결제 완료 전까지만 허용”

### 8.7 brownfield-adoption
기존 프로젝트에 안전하게 AI 운영 레이어를 심는다.

역할:
- 인벤토리 작성
- 핵심 흐름 보호
- unknown 정리
- 위험 구역 표시
- 최소 보호용 스모크/계약 테스트 추천

### 8.8 validation-loop
수정 후 검증 루프를 닫는다.

### 8.9 architecture-guardrails
레이어 경계와 의존 방향 규칙을 정의하고 강화한다.

---

## 9. Greenfield / Brownfield 전략

## 9.1 Greenfield
신규 프로젝트에서는 아래를 빠르게 세팅한다.
- product / domain 문서 초안
- 초기 아키텍처 구조
- 핵심 사용자 여정
- 기본 validation / smoke 스크립트
- 메모리 정책 및 승인 정책

### 9.2 Brownfield
기존 프로젝트에서는 아래 순서를 따른다.

1. Inventory
   - 언어, 프레임워크, 구조, 주요 패키지, 빌드/테스트 명령 추정

2. Protect
   - 핵심 흐름과 고위험 구역을 먼저 보호

3. Encode
   - 암묵지와 발견사항을 repo 문서로 끌어오기

4. Constrain
   - 최소한의 경계 규칙과 승인 규칙 설치

5. Operate
   - 이후 일반 feature / bugfix / refactor workflow로 진입

brownfield의 핵심 원칙은 **모르는 것은 감추지 않고 unknown으로 남긴다**는 것이다.

---

## 10. 메모리 시스템 설계

이 프로젝트의 중요한 차별점은 repo-local memory다.

## 10.1 메모리 종류

### A. 작업 기억
현재 작업 중만 필요한 임시 상태.

예:
- 현재 범위
- 다음 실험 계획
- 보류 질문

위치 예시:
- `.claude-harness/state/current-task.yaml`

### B. 확정 기억
사용자 확인을 받았거나 명시적으로 주어진 장기 규칙.

예:
- auth 변경은 항상 확인받기
- retry 최대 2회
- payment contract 변경 금지

위치 예시:
- `docs/constraints/`
- `docs/decisions/`
- `.claude-harness/policies/approvals.yaml`

### C. 관측 기억
코드/테스트/로그/브라우저/런타임으로 확인된 사실.

예:
- 버그 원인
- 실제 의존 방향
- flaky 원인
- 런타임 특성

위치 예시:
- `docs/runbooks/`
- `docs/architecture/`
- `docs/brownfield/findings/`

### D. 가설 기억
추정했지만 아직 확정되지 않은 것.

예:
- billing 도메인 추정
- 캐시 일관성 위험 추정

위치 예시:
- `.claude-harness/state/unknowns.md`

## 10.2 기억 저장 정책

### 자동 저장 가능
- explicit user constraint
- explicit approval rule
- verified bug root cause
- verified architecture fact
- verified runtime fact
- repeated project pattern

### 확인 후 저장
- business rule interpretation
- architecture principle
- ownership assignment
- external contract meaning
- breaking behavior policy

### 저장 금지
- transient chat
- emotional comment
- unverified guess as fact
- duplicate memory
- repo-irrelevant preference

## 10.3 기억 승격 단계

```text
hypothesis -> observed_fact -> confirmed -> enforced
```

설명:
- hypothesis: 추정
- observed_fact: 코드/테스트/로그로 확인됨
- confirmed: 사용자 또는 명시 규칙으로 확정됨
- enforced: 테스트/룰/설정으로 강제됨

## 10.4 메모리의 궁극 목표
메모리를 쌓는 것이 목적이 아니다. 다음 작업에서 더 적은 질문으로, 더 높은 일관성과 안전성으로 움직이게 만드는 것이 목적이다.

---

## 11. 승인 정책 기본 원칙

자동 진행 가능:
- 문서 정리
- 테스트 추가
- 명확한 범위의 내부 리팩토링
- 동작 보존형 구조 정리
- 검증 가능한 버그 수정

항상 확인 필요:
- 외부 API 계약 변경
- DB migration / schema 변경
- 인증/권한 로직 변경
- billing / payment 정책 변경
- dependency upgrade
- 대량 삭제/이동
- CI/CD / infra / deployment 변경
- brownfield에서 위험 범위가 큰 수정

---

## 12. 검증 전략

검증은 비용이 낮은 것부터 점진적으로 확대한다.

1. 빠른 정적 검사
   - format
   - lint
   - typecheck

2. 범위 기반 검사
   - 관련 단위 테스트
   - 관련 통합 테스트

3. 스모크 / 핵심 흐름 검사
   - 핵심 사용자 여정
   - contract-level replay

4. 런타임 증거
   - 브라우저 상호작용
   - 로그
   - 메트릭 / 트레이스

5. 문서 / 제약 동기화 검사
   - public behavior 변경 문서화 여부
   - decision / constraint / runbook 반영 여부

### 프로젝트 타입별 전략

#### Web app
- before / after snapshot
- UI journey smoke
- console error 확인

#### API service
- request/response replay
- contract test
- 로그/메트릭/트레이스 확인

#### Worker / batch
- fixture replay
- retry / idempotency 경로 확인

#### Library / SDK
- public API 예제 검증
- snapshot / examples 실행

---

## 13. setup 명령 설계

공개 명령:

```text
/repo-os:setup
```

### setup의 목표
- repo-local operating layer 생성
- greenfield / brownfield 판별
- 최소 질문으로 프로젝트 특성 확보
- 메모리 정책 / 승인 정책 / workflow 스텁 설치
- 문서 구조와 검증 스크립트 생성

### setup 상세 절차

1. 기존 setup 존재 확인
   - 이미 `.claude-harness/manifest.yaml`이 있으면 repair / upgrade / re-run 여부 확인

2. 프로젝트 형태 탐지
   - greenfield vs brownfield
   - web / api / worker / library / monorepo 추정
   - 언어 / 프레임워크 / 패키지 매니저 추정
   - 빌드/테스트/개발 명령 추정

3. 최소 질문
   - repo가 알려주지 못하는 정보만 묻기

4. control plane 생성
   - `CLAUDE.md`
   - `.claude-harness/*`
   - `docs/*`
   - `scripts/agent/*`

5. brownfield 추가 처리
   - `docs/brownfield/inventory.md`
   - `docs/brownfield/findings.md`
   - 초기 unknowns

6. 메모리 bootstrap
   - 명시 제약
   - 승인 규칙
   - 초기 key journeys
   - inferred commands
   - 초기 risk zones

7. 완료 요약
   - 생성/업데이트 파일
   - inferred vs confirmed 구분
   - 남은 unknowns

---

## 14. 생성될 저장소 구조

```text
CLAUDE.md

.claude-harness/
  manifest.yaml
  router.yaml
  policies/
    approvals.yaml
    memory-policy.yaml
  state/
    current-task.yaml
    recent-decisions.md
    unknowns.md
  workflows/
    feature.md
    bugfix.md
    tests.md
    refactor.md
    brownfield-adoption.md

.docs/  # optional internal structured memory index if needed

docs/
  index.md
  constraints/
    project-constraints.md
  decisions/
    ADR-0001-repo-os-bootstrap.md
  domains/
    README.md
  architecture/
  runbooks/
    development.md
  brownfield/
    inventory.md
    findings.md

scripts/
  agent/
    validate.sh
    smoke.sh
    arch-check.sh
```

주의: 실제 디렉터리 구조는 Claude Code plugin spec과 현재 repo 구조를 보고 조정할 수 있다. 다만 위 구조의 역할 분리는 유지한다.

---

## 15. Plugin 내부 아키텍처 목표

plugin 자체에는 아래가 필요하다.

### 15.1 main agent
`repo-os-orchestrator`

역할:
- 자연어 요청 1차 해석
- scope / risk 판단
- 적절한 subagent / skill 선택
- 마지막 결과 통합

### 15.2 specialized subagents
권장 후보:
- requirements agent
- brownfield mapper
- implementation agent
- test agent
- refactor agent
- docs agent
- browser validation agent

### 15.3 hidden skills
- feature-workflow
- bugfix-workflow
- test-expansion
- refactor-workflow
- brownfield-adoption
- repo-memory-policy
- decision-capture
- docs-sync
- validation-loop
- architecture-guardrails

### 15.4 hooks
- SessionStart: repo-local memory와 최근 결정을 로드
- Stop: 이번 작업의 검증 누락 여부와 기억 반영 필요 여부 확인

---

## 16. 현재 구현 상태에 대한 정직한 메모

현재 만들어진 scaffold는 **초기 시작점**으로 보아야 한다. 다음 항목이 아직 완성되지 않았을 가능성이 높다.

부족할 수 있는 것들:
- 공식 Claude Code plugin 구조 파일
- plugin manifest / settings
- hidden skills 실제 구현
- agents / hooks 실제 파일
- setup에서 참조하는 template 파일들
- routing / memory / validation 스텁의 실제 코드 또는 문서
- plugin validate 기준 검증

즉, 현재 상태는 “방향성과 기본 스캐폴드”이며, 다음 작업에서는 **정식 plugin 구조 완성**과 **templates + agents + hooks + hidden workflows 구현**이 우선이다.

---

## 17. 구현 로드맵

## Phase 0. 기획/정렬
목표:
- 제품 방향, 단일 setup, 메모리 정책, workflow 구조 확정

완료 기준:
- 이 문서가 source context로 사용 가능

## Phase 1. 정식 plugin skeleton 완성
목표:
- Claude Code plugin spec에 맞는 파일 구조 완성
- `/repo-os:setup` 동작 가능

필수 작업:
- plugin manifest / settings 정리
- `repo-os-orchestrator` agent 추가
- setup skill과 연결된 템플릿 디렉터리 추가
- 기본 hidden skills 파일 생성
- hooks 스텁 추가

완료 기준:
- Claude Code가 plugin을 정상 로드 가능
- `/repo-os:setup` 실행 가능

## Phase 2. setup 산출물 완성
목표:
- repo-local control plane 생성 자동화

필수 작업:
- 템플릿 보강
- build/test/dev command 추론 로직 보강
- greenfield / brownfield 분기
- approvals / memory-policy / docs index 생성

완료 기준:
- 빈 repo와 기존 repo 모두에서 유효한 기본 구조 생성

## Phase 3. 자동 라우팅과 메모리 운영
목표:
- setup 이후 자연어 요청을 workflow로 자동 라우팅
- 기억 반영 루프 활성화

필수 작업:
- orchestrator prompt 정교화
- intent routing 규칙 작성
- memory extraction / storage 규칙 정리
- docs sync 규칙 추가

완료 기준:
- 일반 자연어 요청으로 feature / bugfix / docs / tests / refactor 중 적절한 workflow 실행

## Phase 4. 검증 루프와 brownfield 강화
목표:
- 안전한 수정과 회귀 방지

필수 작업:
- validation-loop 강화
- browser / runtime evidence 전략 정리
- brownfield inventory / findings 템플릿 개선
- architecture guardrail 강화

완료 기준:
- brownfield에서도 unknown / risk / evidence가 명시되며 안전하게 작업 가능

## Phase 5. polish / validation / 배포 준비
목표:
- plugin 품질 안정화

필수 작업:
- 테스트 시나리오 문서화
- 샘플 repo 적용
- plugin validate
- README / examples / install docs 정리

완료 기준:
- 다른 개발자가 바로 설치하고 사용 가능

---

## 18. Definition of Done

이 프로젝트는 아래가 만족되면 핵심 목표를 달성한 것이다.

1. 사용자는 공개 명령을 `/repo-os:setup` 하나만 쓴다.
2. setup 이후에는 일반 자연어 요청만으로 개발 작업이 가능하다.
3. plugin은 사용자 제약과 검증된 발견사항을 repo-local memory로 축적한다.
4. 위험한 변경은 자동으로 확인을 요구한다.
5. 기능 개발 / 버그 수정 / 테스트 보강 / 리팩토링 / 문서화가 공통 운영 루프 안에 있다.
6. brownfield에서도 unknown과 risk가 명시된다.
7. 중요 규칙은 문서만이 아니라 테스트/룰/설정으로도 내려간다.
8. 작업 후 검증과 문서 동기화가 누락되지 않는다.

---

## 19. 리스크와 대응

### 리스크 1. 문서가 너무 많아져서 retrieval 품질 저하
대응:
- scope-first retrieval
- 중복 제거
- unknown / confirmed 분리
- concise docs 유지

### 리스크 2. AI가 추정을 사실처럼 저장
대응:
- hypothesis / observed / confirmed / enforced 단계 분리
- 고위험 정책은 반드시 확인 후 저장

### 리스크 3. brownfield에서 위험한 자동 수정
대응:
- inventory -> protect -> encode -> constrain -> operate 순서 고수
- 위험 구역 승인 정책 기본값 보수적으로 설정

### 리스크 4. plugin이 너무 많은 공개 명령을 가지게 됨
대응:
- setup 외에는 모두 hidden workflow로 유지
- 사용자는 자연어만 사용

### 리스크 5. 검증 비용이 너무 커짐
대응:
- 빠른 검사 -> 범위 기반 검사 -> 핵심 스모크 -> 런타임 증거 순서로 점진 확대

---

## 20. 다음 Claude Code 작업을 위한 즉시 우선순위

이 문서를 컨텍스트로 다음 구현 세션에서 우선 처리할 일:

1. **현재 scaffold를 정식 Claude Code plugin 구조로 확장**
   - plugin manifest / settings / agents / hooks / hidden skills 구조를 추가
   - 공식 spec은 구현 시점의 최신 문서를 확인해서 맞춘다

2. **setup skill이 참조하는 templates 전부 생성**
   - `CLAUDE.md`
   - `.claude-harness/*`
   - `docs/*`
   - `scripts/agent/*`

3. **`repo-os-orchestrator` main agent 작성**
   - plain-language 요청을 내부 workflow로 분기하는 instructions 작성

4. **hidden workflows 실제 파일 작성**
   - feature
   - bugfix
   - tests
   - refactor
   - brownfield-adoption
   - decision-capture
   - docs-sync
   - validation-loop
   - architecture-guardrails

5. **memory policy와 approvals를 실제 동작 가능한 기본값으로 내리기**
   - setup 산출물과 orchestrator가 함께 쓰게 만들기

6. **예시 repo에서 end-to-end로 검증**
   - greenfield 샘플
   - brownfield 샘플

7. **마지막으로 packaging / validate / 문서화 정리**

---

## 21. 최종 요약

이 프로젝트의 본질은 “Claude Code를 위한 플러그인”이 아니라, **저장소 안에 설치되는 AI 운영체제**다.

- 공개 명령은 하나만 있다: `/repo-os:setup`
- setup 이후 사용자는 일반 자연어만 쓴다
- workflow는 내부적으로 자동 라우팅된다
- 사용자 결정과 검증된 발견사항은 repo-local memory로 축적된다
- 중요한 규칙은 테스트/룰/설정/문서로 내려간다
- brownfield에서는 먼저 이해하고 보호한 뒤 작업한다
- 매 작업은 검증과 문서 동기화 루프를 거친다

이 방향을 유지하면 `repo-os`는 단순한 prompt pack이 아니라, 장기적으로 프로젝트를 더 잘 이해하는 **repo-aware development system**이 된다.
