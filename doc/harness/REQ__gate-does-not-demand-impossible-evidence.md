---
tags: [harness, receipts, attestation, task-blocked, c-17]
summary: park 사유는 관측되지 않은 전제를 단언하지 않는다. 그리고 하네스는 "렌즈를 안 돌렸다"와 "돌렸는데 기록이 안 됐다"를 구별할 수 없다 — 그 판단은 조정자 몫이다.
updated: 2026-09-18
freshness: current
invalidated_by_paths:
  - plugin/scripts/_lib.py
  - plugin/scripts/stop_gate.py
  - plugin/mcp/harness_server.py
  - plugin/scripts/background_hook.py
  - tests/test_lib_gate_helpers.py
  - tests/test_review_agent_contracts.py
  - tests/test_stop_gate_receipt_outage.py
  - plugin/CLAUDE.md
  - plugin/skills/develop/SKILL.md
  - plugin/skills/run/SKILL.md
  - CONTRACTS.md
freshness_updated: 2026-09-18T08:05:00Z
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
- 게이트가 **영수증 부재로부터** 그 상태를 추론하는 것 — 위 근거로 **원리적으로
  불가능**하다고 판단했다. 2026-09-18 정정: 이 문장은 원래 "게이트가 그 상태를
  자동 인식하는 것" 이라고 쓰여 있었고, 그건 너무 넓었다. 관측된 마커를 읽는
  것은 추론이 아니며 아래 절이 그 슬라이스를 다룬다. 넓은 표현을 그대로 두면
  다음 독자가 **고칠 수 있는 것까지 불가능으로 읽는다**.


## 턴 종료 표면 — 관측된 마커 슬라이스 (2026-09-18)

필드 리포트: `task_start` 가 `receipts_recordable: false`
(`StaleBytecodeCacheError`) 를 돌려줬고 `RECEIPTS.jsonl` 은 끝내 생기지 않았다.
Stop 훅은 매 턴 `missing: completed review verdict PASS ..., completed QA
verdict: qa-cli` 로 막았다 — 고장 난 하위 시스템만이 만들 수 있는 항목이다.
조정자는 렌즈를 띄우고 기다리기를 **약 15턴** 반복했고 사용자가 끊었다.

게이트 문구는 이미 park 경로를 이름으로 대고 있었다("call task_blocked directly
for a genuine external blocker"). 그런데 부족했고, 이유가 중요하다: 메시지는
격차를 **빠진 verdict** 로 제시한다. 그건 *남은 일* 로 읽힌다. "빠진 verdict" 를
"genuine external blocker" 로 바꾸는 단 하나의 사실은 여러 턴 전에 **다른
표면**(task_start)이 한 번 말하고 다시는 나타나지 않았다.

측정: `receipts_recordable` 는 `stop_gate.py` 에 0회, `_lib.py` 에 0회 등장했다.
`harness_server.py` 는 그것을 읽고 `RECEIPT_UNAVAILABLE_NEXT_ACTION` 으로
갈아끼운다. **교착이 실제로 일어나는 표면만 그 신호에 눈이 멀어 있었다.**

### 왜 이것이 폐기된 설계가 아닌가

위 "폐기된 접근" 은 **영수증 스트림이 비었다는 사실로부터** 능력 상실을
추론하는 설계다. 그건 여전히 거부된다 — 영수증 0건은 리뷰에 도달하지 않은
태스크의 정상 상태이고, 측정된 결과가 `block / block / block / proceed /
proceed / proceed` 였다.

여기서 읽는 것은 스트림이 아니라 `doc/harness/.receipt-capability-broken` —
**import 에 실패한 훅이 자기 자신에 대해 남긴 기록**이다. `background_hook.py`
의 주석이 그 목적을 명시한다: 읽는 쪽이 추측 없이 `None` 에서 확정된 `False`
로 갈 수 있게 하려고 쓴다. MCP 는 이미 읽고 있었다. 게이트만 안 읽었다.

이 REQ 의 "이미 기계가 아는 슬라이스 하나" 절이 정확히 이 구분을 세워 뒀다.
이번 변경은 그 절을 턴 종료 표면에 적용한 것뿐이다.

### 산문만 바꾸는 것으로는 부족했다

초안은 `reason` 문장만 바꿨다. 그런데 같은 payload 의 `next_action_command` 와
`owner_skill` 은 그대로 "리뷰 서브에이전트를 띄우고 기다려라" 를 가리키고
있었다. `_gate_response` 는 이 두 필드를 **블록을 푸는 정확한 호출**과 **다음
단계의 소유자**로 정의한다. 즉 payload 가 자기 문장이 하지 말라는 바로 그것을
지시하고 있었고, 이 태스크가 없애려는 루프가 정확히 **코디네이터가 성실하게
렌즈를 띄우는 것**이다.

테스트도 그걸 못 봤다. payload 전체를 `json.dumps` 한 문자열에 대고 문구를
grep 했기 때문에 `next_action_command` 를 한 번도 관측하지 않았고, 두 단언 중
하나("genuine external blocker")는 마커가 없어도 기본 reason 에 늘 들어 있어
**기능이 없어도 통과**하는 상태였다. 리뷰 3라운드가 잡았다.

지금은 관측된 outage 분기에서 두 라우팅 필드도 함께 교체하고, 테스트는 payload
덤프가 아니라 그 필드에 직접 단언한다. MCP 는 같은 상태에서 이미 같은 일을
하고 있었다 — `ctx["next_action"]` 을 `RECEIPT_UNAVAILABLE_NEXT_ACTION` 으로
덮어쓴다.

그런데 첫 라우팅 수정은 **너무 넓었다.** 조건이 "빠진 항목이 있는가" 뿐이라
`missing_for_close` 가 `PLAN.md` 로 시작하는 상태 — 아직 계획되지 않은 태스크 —
에서도 발동했다. 그 상태의 올바른 다음 단계는 plan 이고, PLAN.md 는 **만들 수
있는 일**이다. park 로 갈아끼우면 아직 증거를 모으기 시작도 안 한 태스크를
포기하라고 지시하는 셈이고, "위의 빠진 verdict 는 계속해서 만들 수 없다" 는
문장이 첫 항목이 만들 수 있는 목록을 가리키게 된다.

MCP 는 이 질문을 이미 묻고 있었다. `_gate_next_action` 은 라우팅이 spawn
지시문일 때만 교체하며, `Create PLAN.md via plan skill before source writes.`
가 그 술어에 걸리지 않는다는 것이 테스트로 고정돼 있다. 게이트만 그 질문을
건너뛰면서 주석으로는 "MCP 와 같다" 고 주장하고 있었다 — 행동과 주장 중 하나는
움직여야 했다. 술어 `is_spawn_instruction` 을 `_lib` 으로 옮겨 **두 표면이 같은
하나를 묻게** 했다. 리뷰 4라운드가 잡았다.

라우팅 텍스트는 **세 번째 고정 park 쌍이 아니다.** C-17 의 verbatim-copy 쌍은
missing-attestation 분기 전용이고 둘 다 "렌즈가 돌아 결과를 냈다" 를 단언한다.
여기서 쓰는 것은 C-17 의 다른 경로 — 진짜 외부 환경 blocker 에 대한 직접
`task_blocked` 이고, 그 사유는 조정자가 관측한 것으로부터 직접 쓴다. 그래서
라우팅 텍스트는 **모양만 알려주고 문구는 호출자에게 맡긴다.**

### 차단 결정은 0줄 변경

막던 턴은 그대로 막는다. 문구만 바뀐다. 게이트를 푸는 설계는 C-17 이 막으려는
조용한 방치 창문을 열기 때문에 여기서도 채택하지 않았다 — 출구는 침묵이 아니라
durable park 다.

### attestation 쌍을 붙이지 않는 이유

`attestation_endgame()` 은 두 park 쌍 중 하나를 고르게 해 준다. 그런데 두 쌍
모두 "렌즈가 실제로 돌아 결과를 냈다" 를 단언하고, 관측된 outage 상태에서는
바로 그게 미지다. 해당 없는 쌍을 먼저 제시하는 것이 이 REQ 가 고치려고 쓰인
실패이므로, 붙이면 한 상태 옆에서 같은 실패를 재생산한다. 대신 do-not-rerun
금지를 게이트가 쓸 수 있는 한 가지 형태("rather than spawning another lens")로
싣는다.

### Enforcement

`tests/test_stop_gate_receipt_outage.py` — 마커 판독(부재/존재/디렉터리/
심볼릭 링크/fifo/빈 root/예외), 두 상태의 문구, **마커 없는 평범한 열린
태스크가 바이트 단위로 불변**이라는 핀, close-ready 핀, 그리고 단일 출처
핀(마커 경로 4개 보유자 일치, 게이트가 공유 문장을 다시 적지 않음, MCP 방출
문자열 불변, outage 문구가 두 쌍을 싣지 않음).

리뷰 1라운드가 잡은 두 결함, 둘 다 회귀 핀이 생겼다:

1. **close-ready 에도 문장이 붙었다.** 마커는 close-ready 까지 정당하게
   살아남는다 — `harness_server` 가 마커 분기를 `_run_has_receipts` 위에 두는
   이유가 "run 앞쪽의 영수증이 그 뒤에 일어난 고장을 반증하지 않는다" 이다.
   그런데 그 상태에는 "위의 빠진 verdict" 가 없고 올바른 행동은 `task_close`
   다. 즉 게이트가 **닫으라고 말하는 같은 메시지 안에서 park 하라고** 말하고
   있었다. 이제 문장은 마커가 아니라 *빠진 항목이 있다는 것*에 걸린다.
2. **`os.path.isfile` 은 심볼릭 링크를 따라간다.** 같은 마커를 읽는
   `harness_server` 는 정확히 그 위장을 막으려고 `lstat` + `S_ISREG` 를 쓰고
   테스트로 고정돼 있는데, 게이트만 `isfile` 이었다. 한 파일의 두 판독기가
   같은 디스크 상태를 두고 서로 다른 답을 내는 상태였다. 더 나쁜 건 게이트
   docstring 이 심볼릭 링크를 **거부한다고 적고 있었다는 것** — 이 저장소가
   반복해서 겪는, 실행되지 않는 강제를 주장하는 durable 텍스트다. 이제 두
   판독기가 같은 술어를 쓰고, 네 가지 모양(부재/정규/링크/디렉터리)에 대해
   두 답이 일치하는지 직접 비교하는 테스트가 있다.

mutation 누계 **23건 RED, 이 스윕 기준 생존자 0건.** ("알려진 생존자 0건" 이라고 쓰지 않는다 — 스윕은 짜 넣은 변이만 검사한다. 리뷰가 독립 스윕에서 동치 변이 하나를 찾았다: `_receipt_capability_broken` 의 `except Exception` 을 `except OSError` 로 좁혀도 green 이다. `repo_root` 는 이미 `str` 이고 다른 호출은 `os.lstat` 뿐이라 도달 가능한 raise 를 전부 덮으므로 동치다.) 내역: 최초 스윕 12건, 리뷰가
잡은 결함 4건의 회귀 핀 5건(close-ready, symlink, 고아 mock, 라우팅 필드 2건),
라우팅 관련 추가 3건, spawn-instruction 좁히기 2건, QA 가 잡은
`missing_summary` 가드 1건. 12+5+3+2+1 = 23.

이 문단은 **세 번 틀렸다.** 처음에는 누계만 올리다 내역과 어긋났고, 그래서
내역을 적기 시작했는데 그 판본은 합이 22 라고 적었다 — 항목의 합은 23 이다.
숫자를 세지 않는 이유를 설명하는 문단에서 숫자를 잘못 셌다. 세 번째는 아래
생존자 항목이다.

`is_spawn_instruction` 의 `"spawn"` 갈래는 **더 이상 생존자가 아니다.** 이전
판본은 현재 `_lib` 이 렌더하는 모든 spawn 지시문이 `"subagent"` 를 포함하므로
그 갈래를 지워도 green 이라고 적었다. QA 결함 F1 을 고치며 추가한 테스트가
`stale_followup` 노트를 픽스처로 쓰는데, **그 노트는 `"subagent"` 가 아니라
`"spawn"` 으로 매칭된다.** 그래서 갈래를 지우면 그 테스트의 전제 단언이 붉어진다
(측정: 1 failed / 1477 passed). 커버리지를 늘린 건 의도가 아니었고 부수 효과였다.
리뷰 6라운드가 잡았다 — 이 문단이 기록한 세 번째 거짓 측정이다.

최초 12개 뒤에 붙은 것들은 리뷰가 잡은 결함들의 회귀 핀이다 (아래). 처음
스윕을 돌렸을 때 그중 상당수는 **분기가 아예 없어서** 나타나지 않았다 —
mutation 스윕은 짜 넣은 분기만 검사하며, 빠뜨린 분기에 대해서는 아무 말도
하지 않는다.

세 번째는 더 나쁜 종류였다. 1라운드 수정이 `isfile` 을 `lstat` 으로 바꾸면서
fail-safe 테스트의 mock 은 `os.path.isfile` 을 가리킨 채 남았다. 함수가 더는
호출하지 않는 API 를 mock 하면 **아무것도 단언하지 않으면서 커버리지처럼
보인다**: `except Exception` 을 `except FileNotFoundError` 로 좁혀도 스위트가
green 이었다. 그 사이 docstring 과 이 REQ 는 둘 다 그 경로가 고정돼 있다고
주장하고 있었다. 리뷰 2라운드가 잡았다.

### 도달 가능성 — 측정 기록 (2026-09-18)

**분쟁 중이던 주장:** "오염된 `plugin/scripts/__pycache__` 가 있으면
`python3 plugin/scripts/stop_gate.py` 가 import 단계에서 죽고, `|| true` 가
그걸 삼켜 게이트가 조용히 사라진다."

이 절은 **주장이 아니라 측정과 그 전제만** 싣는다. 메커니즘의 소유자는
`doc/harness/REQ__bytecode-cache-cannot-disable-receipts.md` 와
`TASK__pycache-poisoning-breaks-receipts` 다. 아래 기록은 그 태스크가 가져가기
위한 것이고, 가져간 뒤에는 여기서 지워도 된다.

| # | 측정자 / 전제 | 결과 |
|---|---|---|
| 1 | QA 1 · 자체 샌드박스(`/tmp` 아래) | 크래시 (`StaleBytecodeCacheError`) |
| 2 | 리뷰 7 · 깨끗한 checkout, `__pycache__` 없음 | 재현 안 됨 |
| 3 | QA 2 · e291dae 추출 + venv 심볼릭 링크 + pytest | 크래시 (`PermissionError`) |
| 4 | 개발자 · 3 시퀀스, cwd = 실제 저장소 | 재현 안 됨 |
| 5 | 리뷰 9 · QA 공개 시퀀스 그대로 | 재현 안 됨 |
| 6 | QA 3 · 자기 시퀀스 재실행 | 재현 안 됨 |
| 7 | 리뷰 9·10 · 인위적 오염(헤더 유효 + 내용 어긋남) | 재현함 |
| 8 | QA 4 · `python3` 해석 추적 | **생산자 확인** — 아래 |
| 9 | 개발자 · `_lib.pyc` 만 오염, `subagent_lifecycle` 캐시 없음 | 크래시 `EXIT=1`, stdout 0 |
| 10 | QA 5 · base 트리 + stale-but-valid 형태 | 크래시 — 9와 일치 |

**생산자 (8, 개발자 재측정).** 이 호스트의 `python3` 는 cwd 에 따라 다른
인터프리터로 해석되는 mise shim 이다: 저장소 안 → 3.12.13 (Clang 22.1.3),
`/tmp` → `/usr/bin/python3` 3.12.3 (GCC 13.3.0). 둘 다 MAGIC_NUMBER
`b'\xcb\r\r\n'`, cache_tag `cpython-312` — **서로의 `.pyc` 를 헤더 검증에서
통과시킨다.** 빌드가 다르니 코드 생성이 다르고 차이는 line table 에 몰린다.
조작 없이 "헤더 유효 + 내용 어긋남" 이 만들어지는 경로다. 1·3 은 `/tmp` 아래,
2·4·5·6 은 저장소 안 — 공개된 시퀀스가 **어느 `python3` 가 각 단계를 돌렸는지**
기록하지 않은 것이 빠진 전제였다. 다만 QA 2 와 3 은 같은 샌드박스·같은
시퀀스이므로 cwd 만으로 갈리지 않는다. 남은 변수 확정은 소유 태스크 몫이다.

**linetable-only 어긋남도 가드를 건드린다.** 동등성(개발자 재측정): linetable
계열에서만 다른 두 code 객체는 `==` 가 False. 가드(QA 3, 리뷰 10 재현): 그렇게
만든 `.pyc` 로 e291dae 게이트 → `EXIT=1`, stdout 0, `PermissionError`. 한때
이 절은 이 메커니즘이 "원리적으로 불가능" 하다고 적었다. 거짓이었다.

**`background_hook` (QA 5 측정, 개발자 코드 확인).** `_report_import_failure`
는 `__pycache__` 를 정리한 **뒤 같은 경로에서** 마커를 쓰고 `sys.exit(0)` 한다 —
그 실행에 재-import 는 없다. 마커는 **다음 정상 훅 실행**이 지운다. 정리는 루트
해석보다 먼저라, 루트가 해석되지 않으면 정리만 남는 형태도 있다. 즉 참인 것은
"마커가 잘 안 생긴다" 가 아니라 **"마커는 생기고 수명이 한정된다"** 이다. 현장
보고의 `receipts_recordable: false` 는 그 마커에서만 나오므로, 현장에서 마커는
쓰였고 읽혔다.

**논쟁과 무관하게 참인 것 (리뷰 8 구성, 개발자 재측정).** `stop_gate.py` 는
여러 모듈을 모듈 최상단에서 `try` 없이 import 한다 (`_lib`,
`subagent_lifecycle`, `_gate_response`). **그중 어느 하나가 깨져도** 게이트는
마커를 읽기 전에 죽고, stdout 은 비고, `plugin/hooks/hooks.json` 의 `|| true` 가 exit 1 을 0 으로 바꾼다.
그러면 이 기능이 만든 문장은 아무에게도 도달하지 않는다 — **영수증을 죽이는
고장이 이 기능도 함께 끌 수 있다.** (이전 판본은 "`_lib` 만 오염시키면
크래시하지 않는다" 고 적었다. QA 3·리뷰 10·13 이 그렇게 보고했고 내가 옮겼는데
9·10 이 반증했다. 방향이 위험한 쪽이었다 — 가장 흔한 모듈을 안전한 쪽이라고
알려주고 있었다.)

**교훈.** 이 태스크에서 durable 텍스트가 거짓으로 잡힌 사례들 — 없는 보호를
주장한 docstring, 죽은 mock, 산문과 어긋난 라우팅, 그리고 이 절의 여러 문장 —
이 공유하는 형태는 하나다: **다른 렌즈의 측정을 재측정 없이 옮겼다.** 한 렌즈가
다른 렌즈를 뒤집으면 기록할 것은 승자가 아니라 **각 측정이 성립한 전제**다.
위 표가 그 형태이고, 이 절에서 거짓으로 잡힌 문장은 전부 표가 아니라 표를
해석하던 산문에 있었다.

### 왜 위 절이 저 모양인가 (리뷰 14 진단)

이 태스크에서 durable 텍스트가 거짓으로 잡힌 사례는 위 절의 **"수입된" 문장에
몰려 있었다.** ("전부" 는 아니다 — 리뷰 1이 잡은 `_receipt_capability_broken`
docstring 의 symlink 오주장은 게이트 자체 코드였다.) 원인은 소유권 불일치다:
이 절은 메커니즘을 소유하지 않는다고 선언하면서 그 메커니즘을 계속 들여왔고,
"옮기는 쪽이 재측정한다" 는 규칙은 **수입 문장 수에 비례하는 세금**이라 렌즈
라운드가 늘 때마다 같은 결함의 기회가 같이 늘었다.

그래서 규칙을 강화하는 대신 **수입 형태를 바꿨다.** 위 절은 절반 이하로
줄었고, 줄인 방식은 삭제가 아니라 **해석 산문을 측정 기록으로 환원**하는
것이었다 — 측정 10건은 전부 남아 있고, 그것을 해석하던 주장 문단이 사라졌다.
표는 거짓이 된 적이 없고, 거짓이 된 문장은 전부 표를 해석하던 산문이었다.

삭제가 아니라 환원인 이유: **QA 렌즈의 측정은 이 저장소 어디에도 영속되지
않는다.** `RECEIPTS.jsonl` 은 verdict 과 해소되지 않는 `DETAIL_SHA256` 만 싣고,
상세는 리뷰 쪽만 `REVIEWS.jsonl` 에 남는다 (QA 5라운드 측정: 모든 review 해시는
`REVIEWS.jsonl` 로 해소되고, **모든 qa 해시는 어디로도 해소되지 않는다.** 개수는
적지 않는다 — 라운드마다 영수증이 늘어 아무도 편집하지 않아도 숫자가 낡는다). 지금 지웠다면 QA 가 만든
측정은 사라졌을 것이다. 소유 태스크가 표를 가져간 뒤에는 위 절을 포인터 한
줄로 줄여도 된다.

### 다루지 않는 것 (이번 변경 기준)

- **세 번째 고정 park 쌍.** C-17 은 진짜 외부 환경 blocker 를 직접
  `task_blocked` 사유로 이미 허용하고, 관측된 outage 가 그것이다. 기존 두 쌍은
  missing-attestation 분기 전용이다. 세 번째 쌍은 C-17 managed block,
  `plugin/CLAUDE.md`, develop/run SKILL 과 그 codex 쌍둥이까지 번진다.
- **일반 꼬리가 새 문장을 희석한다 (QA 2라운드 관측, 후속 해결).** 영수증 장애가
  확인되어 `task_blocked` 로 가는 경우, reason 은 park 안내 뒤에 일반적인
  "Do not stop; finish task start -> plan -> develop -> QA -> close" 를 붙이지
  않는다. `Next:` 와 `next_action_command` 는 같은 park 동작을 가리킨다.
  장애가 없거나 PLAN 작성·종결처럼 다른 단계인 경우에는 일반 안내를 유지한다.
- **`task_verify` 와 게이트가 같은 상태에서 반대로 말한다 (QA 2라운드 관측,
  후속).** 마커가 있고 영수증이 0건일 때 `task_verify` 는 "Continue and await
  the required review and QA" 를, 게이트는 "publish it through task_blocked" 를
  돌려준다. PLAN AC3 이 이를 의도적으로 구분했고(중간 조언 대 턴 종료 조언),
  MCP 문구도 do-not-rerun 금지를 싣고 있어 렌즈를 띄우라는 지시는 아니다. 다만
  C-17 은 park 로 가는 길에 `task_verify` 를 거치게 하고, "Continue and await"
  는 보고된 루프를 이루던 바로 그 문장이다. 별도 태스크에서 판단할 값이 있다.
- **`_MAX_CONSECUTIVE_YIELDS` 가 물지 않는 문제 — 비결함으로 닫힘.** 위
  참조. 처음에는 별개 결함으로 보고 후속으로 미뤘는데, QA 측정 결과 그 상한은
  애초에 연속 block 을 세지 않는다.

  이 문단의 첫 판본은 **코드와 반대로** 적혀 있었다. "outage 분기가 yield
  경로보다 앞서므로" 라고 썼는데 순서는 정반대다: yield 경로는
  `stop_gate.py` 의 `if active_background:` 에서 먼저 들어가고 그 안의 모든
  가지가 `return` 한다. outage 는 한참 뒤에서야 계산되므로, yield 경로에
  들어가면 **outage 문장은 아예 도달하지 않는다.**

  보고된 세션이 풀리는 진짜 이유는 순서가 아니라 `active_background` 가 비어
  있다는 것이다. **import 에 실패한 훅은 `started` 행도 쓰지 못한다.** 그래서
  살아 있는 것으로 읽힐 레코드 자체가 없다.

  같은 사실이 첫 판본의 다른 추측 — "카운터가 시체 앞에서 계속 리셋됐을
  가능성이 크다" — 도 반증한다. 현장 보고는 `RECEIPTS.jsonl` 이 끝내 생기지
  않았다고 적고 있다. 리셋될 레코드가 없었으므로 yield 는 한 번도 쓰이지
  않았다. 리뷰가 두 문장 모두 잡았다.

  **QA 가 나머지를 풀었다 (2026-09-18).** `_MAX_CONSECUTIVE_YIELDS` 는 연속
  **block** 상한이 아니라 연속 **proceed** 상한이다. `_consecutive_yields` 는
  `if active_background:` 안에서 단 한 번 호출되고 그 블록의 모든 가지가
  `return` 한다. import 에 실패한 훅은 `started` 행을 쓰지 못하므로
  `active_background` 가 비어 있고, 예산을 보는 분기에 **애초에 들어가지
  않는다**. 마커가 있는 픽스처로 15턴 연속 측정: `['block'×15]`, yield 원장
  파일은 끝내 생성되지 않음.

  따라서 **찾을 결함이 없었다.** 게이트에는 연속 block 상한이 없고, 그게
  설계다 — C-17 에서 무기한 차단은 올바른 동작이며 출구는 PASS, durable park,
  명시적 cancel 뿐이다. 현장의 15턴은 게이트가 명세대로 동작한 것이고, 없던
  것은 카운터가 아니라 **park 출구에 도달할 어휘**였다. 그게 AC1–AC3 이
  공급하는 것이다. 이 항목은 후속 과제가 아니라 **비결함으로 닫는다.**
