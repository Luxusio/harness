---
tags: [harness, stop-gate, task-verify, diagnostics, turn-end]
summary: 런타임 표면은 호출자가 다음 행동을 정하는 데 필요한 정보를 가리지 않는다. 일반 안내로 선행 blocker를 덮어쓰지 않고, 거부는 실패 범주를 지목하며, 게이트는 작업을 만들지 못하는 블록을 하지 않는다.
updated: 2026-09-04
freshness: current
invalidated_by_paths:
  - plugin/scripts/stop_gate.py
  - plugin/scripts/_gate_response.py
  - plugin/scripts/_lib.py
  - plugin/mcp/harness_server.py
  - tests/test_stop_gate.py
  - tests/test_lib_gate_helpers.py
  - tests/test_receipt_watcher_fail_closed.py
---

# REQ — 런타임 표면은 실제 blocker를 지목한다

## Expected behavior

하네스가 코디네이터에게 내보내는 모든 런타임 응답 — 게이트 결정, MCP
`next_action`, 거부 예외 — 은 **호출자가 다음 행동을 정하는 데 필요한 정보를
가리지 않는다.** 구체적으로 세 규칙:

1. **일반 안내가 선행 blocker를 덮어쓰지 않는다.** 어떤 상태에 더 앞선
   미충족 조건이 있으면, 그 조건이 먼저 오고 일반 안내가 뒤따른다. 구조화된
   필드에만 남기는 것은 가린 것이다.
2. **거부는 실패 *범주*를 지목한다.** 하나의 예외가 여러 상황을 덮고 그
   처방이 서로 다르면, 메시지는 어느 쪽인지 말해야 한다. 범주만이며 비교된
   값은 절대 아니다.
3. **작업을 만들지 못하는 블록은 하지 않는다.** 게이트가 막았을 때 호출자가
   할 수 있는 일이 없으면 그 블록은 턴만 소비한다. 막지 말고 보고하라.

## 규칙 3의 근거 — 측정된 것

`stop_gate.py`는 `stop_hook_active`(직전 Stop 훅이 강제한 연속 턴)일 때
백그라운드 서브에이전트가 살아 있으면 이미 통과시켰고, 그 주석은 이유를
*"re-blocking here loops until Claude Code's consecutive hook cap fires"*
라고 적고 있었다.

같은 논리가 **새 Stop에도 그대로 적용되는데** 2026-09-04까지 그 분기는
막았다. 실질 턴을 내면 `stop_hook_active`가 리셋되므로, lens를 기다리는
코디네이터는 매 턴 차단 분기를 탄다. 한 세션에서 약 20회 관측했고, 그때마다
생산된 것은 **"리뷰가 아직 돌고 있다"가 내용의 전부인 턴**이었다.

블록은 없는 증거를 만들지 못한다. 오직 서브에이전트만 만들 수 있다.

### 재개는 런타임 동작이지 하네스가 보장하는 것이 아니다

이 설계는 "서브에이전트 완료 알림이 코디네이터를 재호출한다"에 기댄다. 그건
**관측된 런타임 동작이고 이 레포가 구현하는 것이 아니다** — 유일한
`SubagentStop` 핸들러인 `background_hook.py` 는 영수증 줄만 쓰고 stdout 으로
아무것도 내지 않는다. 이 세션에서 모든 lens 완료가 실제로 재호출로 이어지는
것을 반복 관측했지만, 그 관측은 게이트가 *막고 있던* 동안 모인 것이므로
양보된 턴이 재개된다는 증명은 아니다.

전제가 틀렸을 때의 결과는 유계이고 눈에 보인다: 태스크는 열린 채 남고,
`.active` 마커도 그대로고, systemMessage 가 왜 멈췄는지 설명하며, 레코드가
정리된 뒤의 Stop 은 정상적으로 막는다. 최악의 경우 사용자가 한 번 깨우면
된다. 이 유계성이 전제를 감수할 만하게 만드는 근거이며, 전제 자체가
검증되었다는 뜻은 아니다.

### C-17과 충돌하지 않는다

C-17은 태스크가 `in_progress`인 동안 임의 종결을 막는다. 여기서 바뀐 것은
**증명 가능하게 돌고 있는 작업에 턴을 양보하는 것**이며 포기가 아니다:

- 태스크는 계속 열려 있고 `.active` 마커는 그대로다.
- 허용은 **이 태스크·이 세션의 백그라운드 레코드**에만 조건부다
  (`active_records` 가 session binding, task_id, `run_id`,
  `claude:<sid>:` 런타임 접두사로 필터한다).
- 레코드가 사라지면 Stop 은 종전대로 막힌다
  (`test_yielding_to_a_lens_does_not_survive_the_record_clearing`).

### 양보는 레코드의 수명이지 에이전트의 수명이 아니다 — 그래서 횟수로 묶는다

이 구분을 처음에 틀리게 적었다가 리뷰에서 잡혔다. **하트비트가 없다**:
`subagent_lifecycle` 은 `updated_ts` 를 `started` 영수증에서 찍고 갱신하지
않는다. 그래서 게이트는 에이전트의 죽음을 볼 수 없다.

에이전트를 죽이거나 그 `SubagentStop` 이 provenance 검증에서 거부되면
완료 없는 고아 `started` 행이 남고, 이는 `HARNESS_BACKGROUND_STALE_SECS`
(기본 1800초) 까지 "활성"으로 읽힌다
(`REQ__subagent-lifecycle-receipt-boundaries.md`, 관측 사례 ~1450초).
그 레코드만 믿고 양보하면 **아무것도 돌지 않고 완료가 영원히 오지 않는
태스크에서 C-17 의 유일한 기계 강제가 30분간 침묵한다** — C-17 이 막으려는
바로 그 방치 상태다.

**나이로는 구분할 수 없다.** 이 레포의 실제 리뷰 lens 는 1800초 창에 대해
수 분에서 수십 분까지 돈다(이 세션 관측). 고아를 잡을 만큼 촘촘한 나이
한계는 정상 작업도 죽인다. 논증은 특정 숫자가 아니라 두 경우가 같은
관측이라는 데 기댄다.

**반복은 구분한다.** 살아 있는 에이전트는 한 번 양보시키고 그 완료 알림이
실행을 재개시킨다. 같은 레코드 집합이 계속 양보를 요구하면 올 것이 없다는
뜻이다. 게이트는 변하지 않은 레코드 집합에 대해
`_MAX_CONSECUTIVE_YIELDS`(3) 회까지만 양보하고, 그 뒤에는 막으면서
**죽은/미보고 에이전트 경우와 그 처방(새 lens 재spawn — 재개된 에이전트는
영수증을 쓰지 않는다)** 을 지목한다. 레코드 집합이 바뀌면 예산은 초기화된다.

원장을 유지할 수 없으면 막는다. 반대 방향으로 실패하면 이 카운터가 닫으려는
침묵 구간이 그대로 돌아온다.

### 허용하되 침묵하지 않는다

이전 재귀 경로는 stdout에 **아무것도** 내지 않아 운영자에게 설명 없는 정지를
남겼다. 두 경로 모두 이제 `_gate_response.proceed()` — `{"continue": true,
"systemMessage": ...}` — 로 무엇을 기다리는지 보고한다.

수반되는 문구도 함께 바뀌었다. `_background_reason`은 "do not stop until
lifecycle hooks mark it complete"로 끝났는데, 통과시키면서 멈추지 말라고
명령하는 것은 자기모순이다. 이제 보고문이다.

## 규칙 1의 근거 — `task_verify`가 PLAN.md 부재를 숨겼다

`handle_task_verify`의 PENDING 분기는 `emit_compact_context`가 계산한
`ctx["next_action"]`을 버리고 attestation 안내로 대체한다. PLAN.md가 없으면
실제 지시 — "Create PLAN.md via plan skill before source writes." — 가
사라지고 132단어짜리 영수증 안내만 남았다. 진짜 blocker는
`missing_for_close`에만 생존했다.

프로토콜이 가장 권위 있게 취급하는 표면이 **엉뚱한 장애물을 지목**하고 있었다.
`3ec78a7`에도 있던 기존 결함이고, 덮어쓰기 텍스트가 31단어 길어지면서 유용한
신호가 비례해서 더 묻혔다.

선행 조건은 **문장이 아니라 구조화된 필드**로 판별한다:
`emit_compact_context`는 plan-first 조건이 미충족일 때 정확히
`why_source_write_blocked`를 채우므로, 지시문을 다시 쓴다고 이 연결이 조용히
끊기지 않는다.

## 규칙 2의 근거 — 하나의 거부가 네 상황을 덮었다

`_lib`의 control-writer 거부는 `PermissionError("TASK.json mutation requires
the task-control MCP")` 하나였다. `authorized()`가 False를 반환하는 경로는
최소 넷이고, 그중 둘은 **처방이 정반대**다:

| 상황 | 처방 |
|---|---|
| 아무것도 바인딩되지 않음 | 호출 지점에서 할 수 있는 게 없음 — 정규 MCP 모듈 임포트, 또는 `_lib` reload 제거 |
| 바인딩은 있으나 이 caller가 아님 | 일반적인 wrong-writer — MCP를 경유 |

2026-09-03 세션에서 실제로 겪은 것은 첫 번째였다: 테스트 `setUp`의
`importlib.reload(_lib)`가 같은 모듈 dict에서 authority factory를 재실행해
`bindings` 클로저를 비웠고, 이후 그 프로세스의 모든 쓰기가 wrong-writer
문구로 실패했다. 범주 하나만 구분됐어도 traceback 한 번이면 끝날 일이었다.

### 진단이 가드를 약화시키지 않는 방법

- **불리언 판정은 한 비트도 바뀌지 않는다.** 범주는 판정 *이후*에 계산된다.
- **범주만 말하고 값은 말하지 않는다.** ~18개 신원 검사 중 어느 것을
  건드렸는지 알려주는 것은 가드를 풀이 가능한 퍼즐로 만든다. inode, uid,
  경로, 모드는 절대 나가지 않는다 —
  `test_the_refusal_leaks_no_identity_values`가 두 범주 모두에 대해 고정한다.

그 테스트의 첫 판본에는 구멍이 있었다. 인프로세스 호출은 unbound 분기만
타므로, 다른 분기에 `uid {os.getuid()}`를 넣는 mutation을 통과시켰다. 두
범주를 한 서브프로세스에서 모두 얻어 검사하도록 고쳤다. **여러 분기를 가진
술어의 테스트는 모든 분기에 실제로 도달하는지부터 증명해야 한다.**

## Enforcement

- `test_yields_the_turn_to_an_active_background_subagent` — 새 Stop이 lens
  대기 중 양보하고, 무엇을 기다리는지 보고하며, 옛 지시문을 반복하지 않는다.
- `test_yielding_to_a_lens_does_not_survive_the_record_clearing` — 스코핑
  절반. 레코드가 사라지면 양보도 끝난다.
- `test_repeated_yields_on_an_unchanged_record_set_block` — 수명 절반.
  25분 된 고아 행으로 재현: 3회 양보 후 막고, 처방을 지목한다.
- `test_a_changed_record_set_restarts_the_yield_budget` — 진행이 있으면
  예산이 초기화되어 실제 두 번째 lens 가 벌받지 않는다.
- `test_stop_hook_active_with_active_background_allows_and_reports` — 재귀
  경로도 같은 결정·같은 보고.
- `TestTaskVerifyKeepsThePrerequisiteBlocker` — 실제 핸들러를 구동해 PLAN.md가
  `next_action`에 먼저 오고, attestation 안내가 온전히 남는지.
- `ControlWriterRefusalNamesTheCategory` — 두 범주 구분, 값 미노출, 판정 불변.

mutation 전부 지명 테스트를 붉게 만든다: 다시 블록하기, 살아 있는
서브에이전트 없이 양보하기, 옛 지시문 복원, 양보 횟수 제한 제거, 원장 실패 시
막지 않기, 선행 조건 보존 제거, 보존을 대체로 바꾸기, 범주 구분 제거, 범주에
uid 넣기.

## 방법론 — 이 태스크에서 두 번 반복된 실패

테스트가 **자기가 이름 붙인 성질에 실제로 도달하는지**부터 증명해야 한다.
같은 형태로 두 번 걸렸다:

1. uid 유출 방지 테스트가 인프로세스 호출만 써서 2분기 술어의 unbound 분기만
   탔다. 다른 분기에 `uid` 를 넣는 mutation 을 통과시켰다.
2. AC-1b 테스트가 "양보는 서브에이전트 수명만큼"이라고 적었지만 실제로는
   `RECEIPTS.jsonl` 을 손으로 비웠다. 증명한 것은 *레코드*가 사라지면
   끝난다는 것뿐이고, 에이전트가 죽었을 때는 한 번도 도달하지 않았다.

둘 다 mutation 이 잡았고 산문 검토는 못 잡았다. 분기가 여럿인 술어나 "X 만큼
지속된다" 류의 주장에는, 그 분기·그 조건에 도달했음을 보이는 픽스처가 필요하다.
