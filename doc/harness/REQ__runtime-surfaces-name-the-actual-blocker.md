---
tags: [harness, stop-gate, task-verify, diagnostics, turn-end]
summary: 런타임 표면은 호출자가 다음 행동을 정하는 데 필요한 정보를 가리지 않는다. 일반 안내로 선행 blocker를 덮어쓰지 않고, 거부는 실제 실패 범주와 입력 필드를 지목하며, 게이트는 작업을 만들지 못하는 블록을 하지 않고, 파킹은 재개의 증거 비용을 미리 밝힌다.
updated: 2026-09-11
freshness: current
invalidated_by_paths:
  - plugin/scripts/stop_gate.py
  - plugin/scripts/_gate_response.py
  - plugin/scripts/_lib.py
  - plugin/scripts/subagent_lifecycle.py
  - plugin/scripts/background_hook.py
  - plugin/mcp/harness_server.py
  - plugin/skills/develop/SKILL.md
  - plugin/skills/develop/parallel-fanout.md
  - tests/test_develop_parallel_fanout_contract.py
  - tests/test_stop_gate.py
  - tests/test_subagent_lifecycle.py
  - tests/test_lib_gate_helpers.py
  - tests/test_no_git_receipt_model.py
  - tests/test_receipt_watcher_fail_closed.py
  - tests/test_harness_mcp_server.py
freshness_updated: 2026-09-18T06:40:00Z
---

# REQ — 런타임 표면은 실제 blocker를 지목한다

## Expected behavior

하네스가 코디네이터에게 내보내는 모든 런타임 응답 — 게이트 결정, MCP
`next_action`, 거부 예외 — 은 **호출자가 다음 행동을 정하는 데 필요한 정보를
가리지 않는다.** 구체적으로 다섯 규칙:

1. **일반 안내가 선행 blocker를 덮어쓰지 않는다.** 어떤 상태에 더 앞선
   미충족 조건이 있으면, 그 조건이 먼저 오고 일반 안내가 뒤따른다. 구조화된
   필드에만 남기는 것은 가린 것이다.
2. **거부는 실패 *범주*를 지목한다.** 하나의 예외가 여러 상황을 덮고 그
   처방이 서로 다르면, 메시지는 어느 쪽인지 말해야 한다. 범주만이며 비교된
   값은 절대 아니다.
3. **작업을 만들지 못하는 블록은 하지 않는다.** 게이트가 막았을 때 호출자가
   할 수 있는 일이 없으면 그 블록은 턴만 소비한다. 막지 말고 보고하라.
4. **파킹은 재개의 증거 비용을 미리 밝힌다.** 기본 재개는 같은 run과
   review/QA 영수증을 보존하고, 새 generation을 명시적으로 선택한 경우에만
   그것을 폐기한다. `task_blocked` 응답과 `BLOCKED.md`는 두 선택과 결과를
   blocker 본문과 분리해 함께 보여준다.
5. **입력 오류는 실제 필드를 보존한다.** 런타임은 일반 예외에서 selector를
   추측하지 않고 실패한 인자를 직접 지목한다. 유효한 증거 본문은 명시된
   경계 안에서 원문 보존하며, 거부 진단에는 본문 내용을 반사하지 않는다.

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

### 양보는 레코드의 수명이지 에이전트의 수명이 아니다 — 하트비트로 구분하고 횟수로 받친다

에이전트를 죽이거나 그 `SubagentStop` 이 provenance 검증에서 거부되면
완료 없는 고아 `started` 행이 남고, 이는 `HARNESS_BACKGROUND_STALE_SECS`
(기본 1800초) 까지 "활성"으로 읽힌다
(`REQ__subagent-lifecycle-receipt-boundaries.md`, 관측 사례 ~1450초).
그 레코드만 믿고 양보하면 **아무것도 돌지 않고 완료가 영원히 오지 않는
태스크에서 C-17 의 유일한 기계 강제가 30분간 침묵한다** — C-17 이 막으려는
바로 그 방치 상태다.

**레코드 자신의 나이로는 구분할 수 없다.** `subagent_lifecycle` 은
`updated_ts` 를 `started` 영수증에서 한 번 찍고 갱신하지 않는다. 이 레포의
실제 리뷰 lens 는 1800초 창에 대해 수 분에서 수십 분까지 돌므로, 고아를 잡을
만큼 촘촘한 나이 한계는 정상 작업도 죽인다. 논증은 특정 숫자가 아니라 두
경우가 `updated_ts` 상에서 같은 관측이라는 데 기댄다.

**2026-09-11 정정 — "반복이 구분한다"는 틀렸다.** 이 문서의 이전 판본은
*살아 있는 에이전트는 한 번 양보시키고 그 완료 알림이 실행을 재개시킨다* 를
전제로 카운터를 유일한 판별자로 삼았다. 그 전제는 거짓이다. 살아 있는
에이전트의 레코드 집합은 **실행 내내 불변**인 반면, 코디네이터는 그 창 안에서
무관한 이유로 여러 번 재호출된다 — 다른 백그라운드 Bash 완료(관측 세션에서
6회 이상), 다른 에이전트의 알림, 팀메이트 메시지, 동시 검증 중인 자기 도구
호출. 그 턴들도 전부 Stop 으로 끝나고 같은 지문에 대해 카운터를 올린다.

즉 카운터가 재던 것은 **에이전트의 죽음이 아니라 코디네이터의 턴 수**다.
`_MAX_CONSECUTIVE_YIELDS = 3` 이면 동시 작업을 하는 코디네이터는 1분 안에
예산을 소진하는데, 리뷰 lens 는 정상적으로 8-20분을 돈다. 2026-09-10 관측:
게이트가 **연속 8턴을 막으면서** 그 메시지 자신은
`harness:code-reviewer a2ed9d740251b466c active for ~194s`, 이어 218s, 235s,
240s, 245s, 250s 라고 적고 있었다. 나이가 매 메시지마다 증가하는 것은
레코드를 매번 새로 읽기 때문이고, 지문이 불변인 것은 **에이전트가 설계대로
살아 있기 때문**이다. 게이트는 그 에이전트만이 만들 수 있는 증거를
그 에이전트를 죽이라며 요구하고 있었다. 규칙 3 위반이다.

**진짜 하트비트는 있다 — 영수증이 아니라 트랜스크립트에.** 실행 중인
서브에이전트의 트랜스크립트는

    <CLAUDE_CONFIG_DIR>/projects/<project>/<session>/subagents/agent-<agent_id>.jsonl

에 실행 내내 append 된다. `agent_id` 는 영수증의 것과 정확히 같고
(`a6b82fbc186344eb9` → `agent-a6b82fbc186344eb9.jsonl`),
`_trusted_stop_provenance` 가 stop 경로에서 이미 이 레이아웃을
(`[sid, "subagents", f"agent-{aid}.jsonl"]`) 검증하고 있다. 따라서
`RECEIPT_FIELDS` 를 바꾸지 않고 얻을 수 있다 — 스키마 확장은 exact-schema
검증기, replay 호환성, `ADR__consolidated-task-artifacts.md` 의 저장 규약을
동시에 건드리므로 이 결함에 비해 지나치게 넓다.

신선도 창은 `HARNESS_SUBAGENT_HEARTBEAT_SECS` (기본 300초) 로 조정한다.
트랜스크립트는 서브에이전트가 메시지나 도구 호출을 낼 때만 전진하므로, 한 번의
긴 도구 호출 안에 머무는 렌즈는 이 창 동안 조용해 보일 수 있다. 그런 워크로드를
돌리는 운영자는 이 값을 늘린다. 상한은 `HARNESS_BACKGROUND_STALE_SECS` 가 따로
잡으므로(위), 이 값을 키워도 침묵이 무한해지지 않는다.

`stop_gate._heartbeat_age` 는 mtime 만 읽는다. 내용을 신뢰하지 않으므로
provenance 수준의 하드닝이 필요 없다. 적대적 트랜스크립트가 할 수 있는 최악은
죽은 에이전트를 살아 보이게 하는 것이고, 그 구간은 아래 카운터가 이미 묶는다.
프로젝트 디렉터리 성분은 glob 한다 — 세션 id 가 이미 유일하고, 슬러그는
하네스가 소유하지 않는 CLI 인코딩이다.

**카운터는 남되 fallback 이다.** 어떤 레코드도 최근 트랜스크립트 활동을
보이지 않을 때만 도달하고, 그때만 원장을 쓴다. 하트비트가 있는 턴은 예산을
**소비하지 않는다** — 매 코디네이터 턴마다 소비하던 것이 위 결함의 메커니즘
자체였다. 소진되면 막으면서 **죽은/미보고 에이전트 경우와 그 처방(새 lens
재spawn — 재개된 에이전트는 영수증을 쓰지 않는다)** 을 지목한다.

원장을 유지할 수 없으면 막는다. 반대 방향으로 실패하면 이 카운터가 닫으려는
침묵 구간이 그대로 돌아온다.

**막을 때는 활성이라고 말하지 않는다.** 블록 경로는 정의상 하트비트가 없는
상태이므로 레코드를 `active for ~Ns` 로 적으면 메시지가 자기 결정과
모순된다 — 2026-09-10 의 여덟 블록이 정확히 그 모양이었다.
`_active_record_lines` 는 이제 상태 라벨을 받고, 블록은
`no recent transcript activity` 로 적는다.

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

## 규칙 2의 근거 — lens 없는 spawn 이 crash 로 기록됐다

`harness:developer`, `oh-my-claudecode:critic`, `harness:documentation-review`
는 어느 lens 쌍으로도 토큰화되지 않는다. 이들의 영수증은
`_receipt_entry_semantics_valid` 의 `lens not in SUPPORTED_LENSES` 절에서
거부됐고, 그 `ValueError` 가 `register_subagent_start` 를 빠져나가
`background_hook` 의 `except` 로 들어가 **spawn 마다 `gate-crash` 행**을
남겼다. 2026-08-25 이후 37행, 2026-09-10 하루에만 19행.

이들은 애초에 영수증을 **져야 할 이유가 없다**. 예상된 부재를 crash 로 적는
것은 거짓 신호이고, 그 볼륨은 `6689dd7`(receipt-subsystem 실패를 관측 가능하게)
이 드러내려던 진짜 실패를 그대로 묻는다. 같은 예외 경로가 stop-only 런타임의
stop 경로에도 있어 양쪽 모두 막았다.

**기대 동작:** 적용 가능한 lens 가 없는 에이전트는 영수증도, 오류도, 빵부스러기도
남기지 않는다. 결함은 *필수* lens 가 기록에 실패했을 때뿐이다.

**"lens 가 없다"는 빈 문자열이 아니라 `SUPPORTED_LENSES` 비소속이다.**
`_infer_receipt_lens` 에는 `ux` 분기가 있어 `harness:ux-cli` 는 `"ux-cli"` 를
반환하지만, `SUPPORTED_LENSES` 는 여섯 개 `review-*`/`qa-*` 값뿐이라
`_receipt_entry_semantics_valid` 가 이를 거부한다. 가드를 truthiness 로 쓰면
그 spawn 들이 정확히 이 절까지 통과해 버린다 — `develop` 과 `run` 스킬 모두
사용자 대면 변경에 UX 렌즈를 지시하므로, 고친 줄 알았던 `gate-crash` 가 그대로
계속 쌓인다. `ux-*` 는 `required_lenses` 검증도 같은 집합으로 하므로 애초에
필수가 될 수 없고, 따라서 침묵 경로가 옳다. 3라운드 리뷰가 잡았다.

## 규칙 2의 근거 (2) — `name=` 이 lens 를 지워 PASS 를 불가능하게 했다

Agent 도구에 `name=` 을 넘기면 CLI 는 그 표시 이름을 `agentType` 자리에
넣는다. CLI 자신의 서브에이전트 메타가 손실을 직접 보여준다:

    named   {"agentType":"qa-cli-1","name":"qa-cli-1","taskKind":"in_process_teammate",...}
    unnamed {"agentType":"harness:code-reviewer","toolUseId":"toolu_01GHq...",...}

해결된 에이전트 타입은 payload 어디에도 없다. 따라서 lens 는 **복구 불가능**하다.

2026-09-10 측정, 같은 세션·같은 에이전트 타입:

| spawn | 결과 |
|---|---|
| `name=` 세 번 | 영수증 0행, `gate-crash` 3행 (08:56:50 / 08:57:11 / 08:57:31) |
| 이름 없이 한 번 | 영수증 즉시 기록 (09:04:19 started review-code harness:code-reviewer) |

이름이 우연히 lens 를 담으면(`qa-cli-1` → `qa-cli`) 동작하고, 담지 않으면
(`review-scoring`) 조용히 실패한다. 결함이 전면적이 아니라 간헐적이었던
이유이자 한 달간 격리되지 않은 이유다. 레인에 이름을 붙이는 것은 가독성을
위한 자연스러운 행동인데, 그렇게 한 코디네이터는 리뷰가 아무리 정확히 돌아도
PASS 를 만들 수 없다. 한 달짜리 영수증 공백의 유력한 일부다.

바로 위 절이 lens 없는 spawn 을 침묵시키므로, 이 경우까지 침묵하면 crash 보다
**더 나쁘다**. 그래서 자체 신호를 유지한다.

판별자는 id 모양이고, 이 CLI 의 트랜스크립트 파일명으로 확인했다: 이름 없는
spawn 의 id 는 `a` + 16 hex(`a6b82fbc186344eb9`), 이름 있는 spawn 은 이름을
품는다(`aqa-cli-1-ee8c583a936c4ed3`, `adiscover-adversarial-b96cb3d1a3b38065`).
하네스가 소유하지 않는 CLI 인코딩이므로 **침묵과 빵부스러기 중 무엇을 낼지
고르는 데에만** 쓰고, 영수증의 수락/거부에는 절대 쓰지 않는다. 인코딩이
바뀌면 결과는 빵부스러기 과다이지 PASS 유실이 아니다.

**기대 동작:** lens 가 없고 id 가 이름 없는 모양이 아닌 spawn 은 원인과 처방을
지목하는 구분 가능한 빵부스러기를 남긴다(`gate-crash` 가 아니다). 라이프사이클
이벤트당 최대 한 줄이므로 start 와 stop 이 각각 기여할 수 있다.

**`name=` 는 lens 에이전트뿐 아니라 어떤 spawn 에도 넘기지 않는다.** 이 판별자는
id 모양에 기대므로, 이름 붙은 lens-less 레인(예: `harness:ac-worker`)도 항상
`named-spawn-shadows-agent-type` 으로 분류된다. 4-워커 배치 하나가 거짓 항목
여덟 개(start+stop)를 이 채널에 쏟아붓고, 그러면 AC-3 이 만들려던 신호가 AC-2
가 없앤 잡음으로 되돌아간다.

**이 규약이 두 번 새어나갔다 — 둘 다 리뷰가 잡았다.**
1라운드: `parallel-fanout.md` 의 인라인 AC-worker 템플릿 자신이 `name=` 을
쓰고 있었다. 2라운드: 진짜 소비자인 `plugin/skills/develop/SKILL.md:150` 이
같은 것을 지시하고 있었고, **같은 파일이 "`parallel-fanout.md` 는 드문 라우팅
경우에만 읽어라"** 라고 적고 있어 일반 경로의 코디네이터는 고쳐진 템플릿을
아예 보지 않았다. 게다가 `tests/test_develop_parallel_fanout_contract.py` 가
`Agent(name="<task_id>:AC-001"` 문자열을 **고정하고 있었다** — 스위트가 결함을
강제하고 있었던 셈이다.

교훈: 규약을 적을 때는 그것을 *실제로 읽는* 파일과 그것을 *고정하는* 테스트를
함께 grep 하라. 참조되는 쪽만 고치면 기본 경로는 바뀌지 않는다. 지금은
`Agent(name=` 부재가 그 계약 테스트로 강제된다.

## 규칙 2의 근거 (3) — 영수증 판독 오류가 증거 파기를 권했다

2026-09-10 관측: `TASK__scoped-mutation-gate` 에 대한 `task_verify` 와
`task_context` 가 모두
`unsupported RECEIPTS.jsonl schema; start a fresh task run to reset receipts`
로 실패했다. 파일은 **완전히 유효했다** — 판독기가 낡았다. 실행 중인 MCP 서버
(PID 928, 09:51:12 기동)가 커밋 `8432d22`(12:09:39, `_lib.py:2618` 에
`PENDING` 수용과 retained `FIRST_LINE:` 슬롯 추가)보다 앞선 빌드였다. 설치된
`_lib.py`(mtime 23:55)는 49행 전부를 받아들이고, `PENDING` 행이 없는 다른
태스크는 같은 서버에서 정상 판독된다.

그 안내를 따랐다면 정당한 review→QA PASS 쌍이 파기됐을 것이다. 게이트 메시지
결함과 같은 부류다: 런타임이 실제 blocker 가 아닌 처방을 지목했다.

판독기는 "기록자가 손상됐다"와 "내가 기록자보다 낡았다"를 **원리적으로
구분할 수 없다.** 그러므로 그 모호성의 파괴적 분기를 권해서는 안 된다.

**기대 동작:** 오류는 거부된 줄 번호와 고정 사유 코드
(`unknown-field-set` / `non-string-value` / `unknown-event` / `entry-semantics`)
를 지목하고, reset 대신 stale-reader 가능성을 먼저 말한다. 사유는 고정
문자열뿐이다 — 영수증 내용은 절대 나가지 않는다(summary 에 어시스턴트 텍스트가
실리고, 이 문자열은 훅 피드백과 MCP 오류 payload 로 나간다).

## 규칙 4의 근거 — 파킹 뒤 기본 재개가 증거를 삭제했다

2026-09-11 관측: `task_blocked`로 파킹한 두 태스크를 `task_start`로 다시
열자 `run_id`가 회전하고 `RECEIPTS.jsonl`이 파일째 삭제됐다. 각각 49행과
10행에 있던 review PASS → QA PASS 쌍을 잃어 이미 끝난 lens를 다시 돌려야
했다. 그러나 파킹 응답은 `status: blocked`만 돌려줬고 `BLOCKED.md`에도 그
비용이 없었다. 호출자에게는 평범한 재개처럼 보이는데 실제 동작은 증거를
폐기하는 새 generation 시작이었다.

**기대 동작:** 별도 `task_resume` API는 두지 않는다. 유효한 기존 task에
`task_start`를 호출할 때 상태별 계약은 다음과 같다.

| 기존 상태 | `fresh_run` 생략/`false` | `fresh_run: true` |
|---|---|---|
| 없음 | 새 run 생성 | 새 run 생성 |
| open | 같은 `run_id`와 영수증을 byte-exact 보존하고 현재 session/watcher만 다시 연결 | 새 `run_id`로 회전하고 영수증 초기화 |
| blocked | 같은 run/영수증을 보존해 marker를 게시한 뒤 blocker를 해제 | 새 run으로 회전·초기화하고 blocker 해제 |
| closed | 무변경 거부, 새 generation이 필요하면 `fresh_run: true`를 지목 | 새 run으로 회전·초기화해 reopen |
| invalid/unsafe | 무변경 거부 | 무변경 거부 — flag는 repair 권한이 아니다 |

회전·초기화는 오직 실제 불리언 `fresh_run: true`가 명시된 경우에만 허용한다.
성공한 `task_blocked` payload와 생성된 `BLOCKED.md`는 plain `task_start`가
같은 run과 review/QA 증거를 보존한다는 선택, 그리고
`task_start(..., fresh_run=true)`가 새 generation을 만들며 현재 증거를
폐기한다는 선택을 둘 다 적는다. 이 안내는 caller가 제공한
`blocked_reason`/`unblock_condition`을 바꾸거나 그 안에 섞지 않는다.

source: C-100 (`CONTRACTS.local.md`) — 파킹 뒤 재개가 review/QA 증거를
조용히 삭제한다는 2026-09-11 사용자 결함 보고의 expected normal behavior.

## 규칙 5의 근거 — `task_blocked`가 실제 실패 필드를 잃었다

2026-09-11 관측: 긴 `blocked_reason`과 `unblock_condition`을 전달하던 호출에서
`unblock_condition`이 누락되자, 최상위 오류 문자열은
`unblock_condition required`라고 했지만 구조화 payload는 `field=task_id`,
거부값은 정상 task ID, `expected`와 `next_action`은 selector 교정이라고
응답했다. 호출자는 실제 누락 필드를 아홉 번 찾지 못했고 두 본문을 짧게 바꾼
뒤에야 성공했다. 직접 호출, newline JSON-RPC, Content-Length framing에서는
서로 다른 12 KiB 이상의 Unicode·여러 줄 문자열이 그대로 보존되어, 확인된
서버 결함은 장문 절단이 아니라 입력 오류의 출처를 selector로 다시 추측하는
공통 `ValueError` adapter였다.

**기대 동작:** `task_blocked`의 각 필드는 독립적으로 검증된다.

- `blocked_reason`과 `unblock_condition`은 공백이 아닌 문자열이어야 하고,
  각각 UTF-8 122,880 bytes 이하이면 trim, 정규화, 줄바꿈 변환, 교환, 절단 없이
  자신의 `BLOCKED.md` section에 원문 그대로 기록된다.
- 각 필드가 122,880 bytes를 넘으면 artifact 작성이나 active-marker 정리 전에
  그 필드, 실제 byte 수, 허용 byte 수를 지목해 거부한다. 저장 가능한 값을
  조용히 줄이지 않는다. 두 필드 제한은 고정 template를 포함한 artifact가 기존
  256 KiB trusted snapshot 경계 안에 남도록 한다.
- 누락, 비문자열, 공백 문자열, UTF-8로 인코딩할 수 없는 Unicode, 초과 입력은
  실제 실패 필드와 고정 reason,
  정확한 `expected`와 필드별 복구 동작을 반환한다. blocker 본문은 오류 문자열,
  `rejected_value`, 로그에 반사하지 않고 `<missing>`, `<blank string>`,
  `<non-string: TYPE>`, `<string: N UTF-8 bytes>`처럼 내용 없는 메타데이터만 쓴다.
- `task_id`의 안내는 구현이 실제로 받는 bare safe ID 또는
  `TASK__<safe-id>`만 말한다. safe ID는 ASCII 문자·숫자·점·밑줄·하이픈
  1–180자다. canonical task path는 `task_dir`의 형식이며 `task_id`의
  `expected`에 섞지 않는다.
- 같은 module의 명시적인 입력 검증 오류는 메시지 substring으로 selector를
  추측하지 않는다. 최소한 `fresh_run`과 `execution_mode`도 자신의 필드를
  지목한다. 기존 MCP `error` / `structuredContent` / `isError` envelope와
  성공 payload는 유지한다.

source: C-100 (`CONTRACTS.local.md`) — 실제 blocker를 지목하지 못해 정상
task ID 수정과 본문 축약을 유도한 2026-09-11 사용자 결함 보고의 expected
normal behavior.

## Enforcement

- `test_yields_the_turn_to_an_active_background_subagent` — 새 Stop이 lens
  대기 중 양보하고, 무엇을 기다리는지 보고하며, 옛 지시문을 반복하지 않는다.
- `test_yielding_to_a_lens_does_not_survive_the_record_clearing` — 스코핑
  절반. 레코드가 사라지면 양보도 끝난다.
- `test_repeated_yields_on_an_unchanged_record_set_block` — 수명 절반.
  25분 된 고아 행으로 재현: 3회 양보 후 막고, 처방을 지목한다.
- `test_a_live_transcript_heartbeat_yields_past_the_turn_budget` — 하트비트가
  있는 6턴(예산의 2배)이 전부 양보되고, 원장은 **생성조차 되지 않는다**.
- `test_a_stale_transcript_falls_back_to_the_turn_budget` — 트랜스크립트가
  존재하되 전진하지 않으면 옛 경계가 그대로 적용되고, 블록 문구에
  `active for` 가 없다.
- `test_a_lens_less_agent_writes_no_receipt_and_no_crash` — 세 lens-less
  에이전트 타입이 영수증도 예외도 남기지 않는다.
- `test_a_ux_lens_agent_owes_nothing_and_says_so_silently` /
  `test_a_ux_lens_spawn_through_the_hook_logs_neither_crash_nor_miss` —
  `ux-*` 는 lens 문자열을 추론하더라도 침묵 경로로 간다.
- `test_a_name_shadowed_lens_spawn_stays_observable` — 이름에 가려진 lens
  spawn 은 `receipt_not_owed` 를 받지 않는다.
- `test_a_named_spawn_whose_name_still_encodes_a_lens_is_unaffected` — 가드가
  "이름 있는 spawn 은 영수증 없음" 으로 넓어지지 않는다.
- `test_lens_less_spawn_through_the_hook_logs_neither_crash_nor_miss` /
  `test_named_lens_spawn_through_the_hook_leaves_a_breadcrumb` — 두 잡음
  채널(`gate-crash`, binding-miss)이 각각 옳은 쪽으로만 열린다.
- `test_a_stop_that_is_owed_a_completion_is_never_silenced` — start 의 타입이
  lens 를 담았는데 트랜스크립트 타입이 잃은 경우, 그 stop 은 침묵되지 않는다.
- `test_a_glob_metacharacter_in_an_agent_id_cannot_forge_a_heartbeat` /
  `test_a_config_root_containing_pattern_syntax_still_resolves` — 하트비트
  경로의 보간값은 전부 이스케이프되고, 우리 `*` 만 패턴이다.
- `test_claude_develop_forbids_collapsing_independent_acs_into_one_executor` —
  `SKILL.md` 에 `Agent(name=` 이 없고 금지 문장이 있다.
- `test_old_unified_schema_fails_closed_without_advising_a_reset` /
  `test_exact_schema_rejects_an_unknown_event` — 네 사유 코드가 각각 도달
  가능하고, 줄 번호를 담고, 영수증 내용을 담지 않으며, reset 을 권하지 않는다.
- `test_malformed_receipt_stream_blocks_normal_stop` /
  `..._recursive_stop` — 게이트 문구도 같은 성질.
- `test_a_changed_record_set_restarts_the_yield_budget` — 진행이 있으면
  예산이 초기화되어 실제 두 번째 lens 가 벌받지 않는다.
- `test_stop_hook_active_with_active_background_allows_and_reports` — 재귀
  경로도 같은 결정·같은 보고.
- `TestTaskVerifyKeepsThePrerequisiteBlocker` — 실제 핸들러를 구동해 PLAN.md가
  `next_action`에 먼저 오고, attestation 안내가 온전히 남는지.
- `ControlWriterRefusalNamesTheCategory` — 두 범주 구분, 값 미노출, 판정 불변.
- `test_task_start_open_resume_preserves_generation_and_evidence` — 기본 open
  재개가 같은 run과 영수증을 보존한다.
- `test_task_start_explicitly_resumes_blocked_task` — 기본 blocked 재개가
  task/receipt bytes를 보존한 뒤 blocker를 해제한다.
- `test_task_start_fresh_run_rotates_generation_and_discards_old_evidence` —
  명시적 `fresh_run: true`만 회전·초기화하고 superseded 경고를 낸다.
- `test_task_start_reopens_closed_task_and_clears_close_attestation` — closed
  기본 호출은 무변경 거부하고 명시적 fresh 선택을 지목한다.
- `test_task_blocked_records_pause_state_and_artifact` — payload와
  `BLOCKED.md`가 보존 재개와 폐기 재개의 두 선택을 모두 설명한다.
- `test_task_blocked_preserves_long_unicode_fields_through_public_transports` —
  direct/newline/framed 호출이 서로 다른 장문 본문을 원문 그대로 보존한다.
- `test_task_blocked_argument_errors_name_the_actual_field_without_echoing_content` —
  누락·비문자열·공백 오류가 실제 필드와 안전한 거부 메타데이터를 반환한다.
- `test_task_blocked_enforces_utf8_byte_limit_before_mutation` — 정확한 byte
  경계는 통과하고 한 byte 초과는 artifact와 marker를 바꾸기 전에 거부한다.
- `test_task_blocked_invalid_unicode_names_the_field_over_direct_and_framed_calls` —
  escaped lone surrogate도 실제 blocker 필드의 `invalid_utf8`로 무변경 거부한다.
- `test_task_selector_errors_report_only_forms_the_field_accepts` — bare/canonical
  ID 수용과 path형 `task_id` 거부 안내가 같은 문법을 말한다.
- `test_local_argument_errors_do_not_fall_back_to_task_id` — `fresh_run`과
  `execution_mode`을 포함한 명시적 로컬 오류가 자신의 필드를 유지한다.

mutation 전부 지명 테스트를 붉게 만든다: 다시 블록하기, 살아 있는
서브에이전트 없이 양보하기, 옛 지시문 복원, 양보 횟수 제한 제거, 원장 실패 시
막지 않기, 선행 조건 보존 제거, 보존을 대체로 바꾸기, 범주 구분 제거, 범주에
uid 넣기.

2026-09-11 추가분도 mutation 으로 확인했다(각 mutation → 붉어지는 테스트):

| mutation | 결과 |
|---|---|
| 하트비트가 절대 켜지지 않게 | `..._yields_past_the_turn_budget` |
| 하트비트가 항상 켜지게 (stale 을 live 로) | 양보 예산 계열 6건 |
| 신선도 상한을 무한으로 | `..._falls_back_to_the_turn_budget` 1건 — 나머지 예산 테스트는 트랜스크립트를 아예 쓰지 않으므로 폭발 반경이 더 좁다 |
| `glob.escape` 제거 | `..._cannot_forge_a_heartbeat` |
| `receipt_not_owed` 에서 `expected_receipt` 조건 제거 | `..._is_never_silenced` |
| `unknown-event` 분기 제거 | `test_exact_schema_rejects_an_unknown_event` |
| `claude_root` 이스케이프 제거 | `..._config_root_containing_pattern_syntax_still_resolves` |
| `SKILL.md` 에 `Agent(name=` 복원 | `..._forbids_collapsing_independent_acs_into_one_executor` |
| 블록 문구를 다시 `active for` 로 | `..._falls_back_to_the_turn_budget` |
| 하트비트 턴도 원장을 쓰게 | `..._yields_past_the_turn_budget` 1건 |
| lens 가드를 membership 대신 truthiness 로 | ux 계열 2건 |
| start 경로의 lens 가드 제거 (옛 crash 경로) | lens 계열 4건 |
| 이름 있는 경우까지 침묵시키기 | 관측 가능성 2건 |
| `background_hook` 이 `receipt_not_owed` 무시 | hook 잡음 1건 |

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

3. (2026-09-11) glob 이스케이프 테스트의 첫 판본이 전체 게이트를 구동해
   `[a]gent-bg` 영수증을 넣고 "블록이 한 번은 나온다"를 주장했다. 그런 id 는
   `_receipt_entry_semantics_valid` 를 통과하지 못해 레코드가 애초에 활성이
   되지 않고, 게이트는 **무관한 이유로** 1턴째부터 막는다. 이스케이프를 빼는
   mutation 에도 초록이었다. `_heartbeat_age` 를 직접 고정하고, 리터럴 id 가
   해석되는지를 대조군으로 함께 주장하도록 고쳤다.

세 번 모두 같은 교훈이다: **테스트가 통과하는 이유를 mutation 으로 확인하기
전까지, 통과는 그 테스트가 이름 붙인 성질의 증거가 아니다.** end-to-end 픽스처는
특히 위험하다 — 실패 경로가 많아서 엉뚱한 이유로 원하는 관측을 만들어낸다.
분기가 실제로 도달 가능한 가장 높은 수준에서 고정하라.
