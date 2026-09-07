---
tags: [harness, receipts, attestation, task-blocked, c-17]
summary: park 사유는 관측되지 않은 전제를 단언하지 않는다. 그리고 하네스는 "렌즈를 안 돌렸다"와 "돌렸는데 기록이 안 됐다"를 구별할 수 없다 — 그 판단은 조정자 몫이다.
updated: 2026-09-07
freshness: current
invalidated_by_paths:
  - plugin/scripts/_lib.py
  - plugin/scripts/stop_gate.py
  - tests/test_lib_gate_helpers.py
  - tests/test_review_agent_contracts.py
  - CONTRACTS.md
---

# REQ — park 사유는 관측되지 않은 전제를 단언하지 않는다

## Expected behavior

1. **고정 park 쌍은 기계가 관측할 수 있는 것만 단언한다.** 영수증이 0건인
   상태에서 "review PASS 와 QA PASS 가 있었다" 는 주장은 아무 근거가 없다.
2. **상태가 다르면 쌍도 다르다.** 두 쌍이 존재하고, 각각 어떤 상태에 맞는지
   함께 제시된다.
3. **어느 쪽도 park 를 지시하지 않는다.** 아직 렌즈를 안 돌린 조정자에게
   park 명령이 도달하면 그것이 곧 이 REQ 가 없애려는 실패다.
4. **게이트의 차단 로직은 이 문제의 해법이 아니다.** 아래 참조.

## 관측된 격차

`ATTESTATION_BLOCKED_REASON` 은 이렇게 말한다:

> "Required hook-owned review/QA attestation remains missing **after
> substantive review PASS, QA PASS, and one fresh task_verify**."

`task_verify` 의 `next_action` 은 증거가 없는 모든 상태에서 이 쌍을 그대로
싣는다. 영수증 0건이면 저 전제는 거짓이다.

필드 리포트 A 가 이걸 지적했다 — 고정 문구가 실제 상태와 무관하게 강제돼
`BLOCKED.md` 를 오염시킨다는 것. 그 세션은 **완료된 작업을 잘못된 사유로**
기록했다. 2026-09-07 세션에서 같은 자리에 섰고, 거짓을 쓰지 않으려고 고정
쌍을 버리고 사유를 직접 작성해야 했다 — 즉 프로토콜이 규정한 어휘를 쓸 수
없는 상태였다.

## 폐기된 접근 — 게이트가 자동 인식하게 하기

**이 절이 이 REQ 에서 가장 중요하다.** 지우면 같은 설계를 다시 하게 된다.

최초 시도: "이 run 에 영수증이 0건이면 런타임이 기록 불가라고 판단하고,
유한 횟수 뒤 Stop 게이트가 차단을 푼다."

구현했고 리뷰가 측정으로 무너뜨렸다.

**(1) 영수증 0건은 열린 태스크의 정상 상태다.** PLAN 을 쓰고 구현 중이면
렌즈를 아직 안 돌렸으니 0건이다. 그 분기는 평범한 개발 태스크에서 4번째
턴부터 게이트를 끄고, 첫 영수증이 생길 때까지 계속 끈다. 측정:
`block / block / block / proceed / proceed / proceed`. **C-17 이 막으려는
조용한 방치 창문을, 대부분의 턴이 일어나는 구간에 열어준다.**

**(2) 턴 종료 횟수는 증거 시도 여부에 대해 아무 정보가 없다.** 리뷰어 표현:
*"카운터가 개발자의 키 입력을 센다."* 상한을 둔다고 두 상태가 갈리지 않는다.

**(3) 동기가 된 사건에서는 발동조차 안 한다.**
`TASK__verdict-binder-loses-real-results` 는 리뷰 영수증이 있어
`no_receipts_recorded = False` 다. 고치려던 것을 안 고치고, 안 건드려야 할
것을 망가뜨린다.

**근본 이유: 하네스는 "렌즈를 안 돌렸다" 와 "돌렸는데 기록이 안 됐다" 를
구별할 수 없다.** 유일한 증거 채널이 바로 고장 난 그것이기 때문이다. 어떤
카운터나 임계값도 이 구별을 만들어내지 못한다 — 정보가 없기 때문이지 임계값을
잘못 골라서가 아니다.

그 구별을 아는 것은 **조정자뿐**이다. 그래서 하네스가 할 일은 추론이 아니라
**참인 어휘를 제공하는 것**이다. 이것이 현재 스코프다.

부수 발견: 그 시도는 손상된 `RECEIPTS.jsonl` 에서 조용히 fail-open 했다 —
HEAD 는 block 하는데 새 분기는 stdout·stderr 둘 다 비운 채 exit 0 했다.
게이트 경로에 `try` 없이 스냅샷을 읽은 탓이다. 게이트를 건드리지 않는
현재 스코프에는 그 위험이 없다.

## 현재 해법

`_lib` 이 두 번째 고정 쌍을 소유한다. `NO_RECEIPTS_BLOCKED_REASON` 은 **종류가
다른 두 사실을 의도적으로 접속**한다 — 기계가 관측한 것(이 run 에 어떤
영수증도 없다)과 조정자가 관측한 것(렌즈가 실제로 돌아 결과를 냈다).

두 번째를 빼면 durable 기록이 **아무것도 스폰한 적 없는 태스크와 문자적으로
구별되지 않는다.** 그런데 그 상태는 park 하면 안 되는 상태다. 즉 기록이 왜
멈춤이 정당했는지를 말하지 못하게 된다.

이것은 attestation 쌍의 문제와 다르다. 그쪽은 review PASS 와 QA PASS 를
단언하는데, 그건 정확히 영수증이 존재 이유로 삼는 것들이다 — 스트림이 빈
상태에서 그걸 주장하면 **부재가 곧 blocker 인 그 증거를 있다고 말하는 것**이
된다. 이 쌍은 작업이 있었다는 것만 주장하고, 조정자가 자기가 관측한 blocker 를
주장하는 것이 `task_blocked` 자체의 전제다.

다만 **런타임이 기록 불가라고는 주장하지 않는다.** 여기서 관측되는 것은 부재뿐
capability 가 아니며, 그 혼동이 첫 시도를 무너뜨렸다.

`attestation_endgame()` 은 **판별 질문을 먼저** 던지고("영수증 스트림이
무엇을 보여주는가") 그 다음에 두 쌍을 각각의 조건과 함께 제시한다. 순서가
중요하다 — 기존 쌍의 조건("필수 hook-owned 증거가 아직 없다")은 빈 스트림
상태에서도 참이므로, 그것을 먼저 제시하고 뒤에서 정정하면 첫 `task_blocked(...)`
호출에서 읽기를 멈춘 조정자가 틀린 쌍에 도달한다. 그게 이 태스크가 고치려는
실패다.

마지막 문장은 **범위가 한정된** 부인이다: "렌즈가 실제로 돌아 결과를 내기
전에는 어느 쪽도 해당하지 않는다." 무조건 "둘 다 지시가 아니다" 로 쓰면
기존 쌍의 조건이 진짜로 성립할 때 C-17 이 **요구하는** 직접 `task_blocked`
경로까지 부인하게 된다.

`stop_gate.py` 의 **차단 로직은 diff 0 줄**이다. 메시지 텍스트만 바뀌었다 —
`_next_action_for_missing` 이 두 쌍을 스트림 조건과 함께 싣는다. 턴 종료
시점이 필드 리포트 A 의 조정자가 서 있던 자리이므로, 그 표면이 적용 가능한
쌍을 전달하지 못하면 이 수정은 정작 사건이 일어난 곳에 닿지 않는다.

`plugin/CLAUDE.md`, `develop/SKILL.md`, `run/SKILL.md` 의 "고정 쌍"
단수 표현도 함께 쓸었다 — 템플릿 동기화 규칙(CRITICAL)상 런타임 동작 변경은
`plugin/` 전체에서 일관되어야 한다.

## Enforcement

`tests/test_lib_gate_helpers.py::ParkReasonsDoNotAssertUnobservedPreconditions`
와 `tests/test_review_agent_contracts.py` 의 두 독립 작성 핀
(`test_missing_attestation_pair_has_exactly_one_authoritative_location` 이
두 쌍 네 문자열을 모두 직접 적어 비교하고,
`test_lib_owns_exactly_one_literal_trust_boundary` 가 endgame 전문을 적는다).

| mutation | 붉어지는 테스트 |
|---|---|
| 사유가 review/QA PASS 를 단언 | `test_the_empty_stream_reason_asserts_only_what_was_observed` + 쌍 핀 |
| unblock 조건을 "증거 없이 닫아라" 로 반전 | `test_the_empty_stream_unblock_names_a_check_not_a_diagnosis` + 쌍 핀 |
| 범위 한정 disclaimer 삭제 | `test_the_endgame_disclaimer_does_not_negate_the_required_route` + endgame 핀 |
| 판별자 선두 배치 제거 | `test_the_endgame_leads_with_the_discriminator` + endgame 핀 |

`test_the_attestation_reason_still_states_its_preconditions` 가 반례다: 기존
쌍을 새 쌍처럼 약화시키면 한 문장이 두 상태를 덮어 이 REQ 가 복원하려는
구별이 사라진다.

**측정 방법 주의.** 이 표를 처음 쓸 때 `pytest` 출력에서 `^FAILED` 만 grep 해
1행이 커버리지 공백인 줄 알았다. 실제로는 subTest 실패가 `SUBFAILED` 로
보고되어 필터에 걸리지 않은 것이었다. mutation 표를 만들 때는 **실패 요약
전체**를 보라 — 리뷰가 이 REQ 의 이전 판에서 두 행이 서로 반대 방향으로
틀렸다고 지적했고, 그 원인이 이것이었다.

## 이미 기계가 아는 슬라이스 하나

`RECEIPT_UNAVAILABLE_NEXT_ACTION` (`plugin/mcp/harness_server.py`) 은
`receipts_recordable is False` — **관측된** 기록 실패 — 일 때만 나온다. 그
분기에서는 하네스가 이미 답을 안다. 위 "원리적으로 불가능" 은 그 분기 밖,
즉 트리는 멀쩡한데 이벤트가 오지 않는 경우에 대한 진술이다. 두 경우를 하나로
묶어 읽지 말 것.

## 이 REQ 가 다루지 않는 것

- 런타임이 `SubagentStart`/`SubagentStop` 을 발행하게 만드는 것 — 하네스
  소관이 아니다. 2026-09-07 측정으로 훅 시스템은 살아 있고 이 두 이벤트만
  오지 않는다는 것까지 확인했다.
- 게이트가 그 상태를 자동 인식하는 것 — 위 근거로 **원리적으로 불가능**하다고
  판단했다.
