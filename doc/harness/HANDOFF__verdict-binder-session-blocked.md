# HANDOFF — TASK__verdict-binder-loses-real-results, 세션 재시작 필요

작성 2026-09-07. 작업은 끝났고 **검증 기록만 남지 않았다.** 태스크는
`in_progress` 이며 `BLOCKED.md` 도 없다 — park 조차 이 세션에서 기록하지
못했기 때문이다. 손으로 쓰지 않았다.

## 정정 (2026-09-07, 재시작 이후 실측)

이 문서의 최초 판은 두 가지를 틀리게 적었다. 남겨두고 고친다.

1. **"새 세션이 두 blocker 를 동시에 없앤다" — 절반만 맞았다.** 재시작은
   MCP 서버만 고쳤다(아래 blocker 1 해결됨). 훅은 **여전히 안 붙는다.**
2. **"`task_blocked` 도 낡은 서버라 실패했다" — 오진이었다.** 실제 원인은
   내 도구 호출에서 `unblock_condition` 파라미터 태그를 잘못 쓴 것이고,
   서버는 그 값을 받은 적이 없다. 에러 메시지가 그대로 말하고 있었는데
   내가 `field: task_id` 만 보고 오독했다. 형식을 고치자 즉시 성공했다.

## 현재 상태

태스크는 `blocked` (BLOCKED_ENV) 로 park 됐다. 사유는 사실대로 기록돼 있다.

- **blocker 1 (MCP 서버 staleness) — 해결됨.** 서버가 15:38 에 수정 후
  페이로드로 재시작했고, `task_verify` 가 정상 동작한다:
  `review_verdict: PASS`, `missing_for_close: [qa-cli]`.
- **blocker 2 (훅 미부착) — 미해결.** 아래 참조.

재개 조건은 하나다: 런타임이 새 서브에이전트에 대해
`SubagentStart`/`SubagentStop` 을 발행하는가. **확인은 싸다** — `qa-cli` 렌즈
하나 띄우고 1분 안에 `RECEIPTS.jsonl` 에 `started` 행이 생기는지만 보면 된다.
생기면 QA 완주 후 `task_verify` → `task_close`. `review-code` 는 이미 attested
PASS 영수증을 갖고 있으므로 재실행 불필요하다.

## blocker 1 — MCP 서버가 수정 전 코드를 메모리에 들고 있다

```
$ ps -eo pid,lstart,cmd | grep harness_server
901  Mon Sep  7 10:26:25 2026  .../harness-dev/plugin/mcp/harness_server.py
```

프로세스가 **10:26:25** 에 떴다. 이 태스크의 페이로드 설치는 그보다 한참
뒤다. 따라서 서버는 수정 전 `_lib` 을 들고 있다.

오늘 기록된 리뷰 영수증:

```
VERDICT: PASS
FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=1 OPTIONAL=0
```

| 검증기 | 이 영수증을 거부하나 |
|---|---|
| 수정 전 (`PASS and (fix_now or investigate)`) | **True** |
| 수정 후 (`PASS and fix_now`) | False |

그래서 `task_verify` 가 `unsupported RECEIPTS.jsonl schema` 로 실패한다.
**이 태스크가 고친 결함이 이 태스크의 검증을 막아섰다.** 원인 3-a(강등 규칙
두 벌)의 실물 사례이며, 심각도가 REQ 가 적은 것보다 크다는 증거다 — 판정이
`PENDING` 으로 남는 정도가 아니라 **영수증 스트림 전체가 읽기 단계에서
거부된다.**

**이 blocker 는 세션 재시작으로 해결됐다.** 15:38 에 새 서버가 수정 후
페이로드로 떴고, 같은 영수증에 대해 `task_verify` 가 `review_verdict: PASS` 를
낸다. 진단이 맞았다는 사후 확인이다.

(최초 판은 `task_blocked` 실패도 이 서버 탓으로 적었다. 오진이다 — 위 정정 2
참조. 그 실패는 내 호출 형식 오류였고 서버와 무관했다.)

## blocker 2 — 새 서브에이전트에 라이프사이클 훅이 안 붙는다

이 세션에서 스폰한 에이전트 중 영수증을 남긴 것은 **하나뿐**이고, 그것은
페이로드 리로드 시점에 **이미 40분째 돌고 있던** 에이전트다.

```
05:22:05  started    review-code   ← 리로드 시각
05:35:48  completed  review-code   PASS
05:37     qa-cli (async, 무명)  → 기록 없음
05:47     qa-cli-2 (named)      → 기록 없음
```

즉 리로드는 **이미 살아 있던** 에이전트의 라이프사이클만 붙잡았다. 이름 유무는
변수가 아니다 (둘 다 기록 없음). 세션 시작 시점에만 붙는 것으로 보인다.

`/reload-plugins` 로는 부족하다는 것이 이 태스크의 실측 결론이다.

## 이미 확보된 증거 (재실행 불필요, 다만 attest 는 안 됨)

- **독립 코드 리뷰 PASS** — 4라운드. 최종 sweep 에서 mutation 28행 전부 red
  (이 태스크 통틀어 첫 완전 통과). 리뷰어가 설치된 페이로드와 트리가
  byte-identical 임을 직접 확인했다.
- **CLI QA PASS 2회** — 각각 게이트 전부 재현, 슬롯 표 행별 검증, 네 진단
  문구의 실행 가능성 확인, mutation 10행 자체 재실행.
- 전체 스위트 1082 passed / 286 subtests, `contract_lint` 18/18,
  `--check-weight` clean, `git diff --check` clean, `golden_replay` 6/6.

QA 가 남긴 관측 하나: `tests/test_runtime_services.py::
test_self_heal_command_runs_then_service_becomes_ready` 는 **clean HEAD 에서도**
타임아웃으로 간헐 실패한다. 선행 flakiness 이며 이 diff 소관이 아니다. 다만
"1082" 는 이 머신에서 결정적 수치가 아니다.

## 미해결로 넘기는 것

- QA self-healing 후보: `note_freshness.py --paths` 가 진단처럼 보이는
  이름으로 문서를 **수정한다**. `--check` / `--dry-run` 모드 필요.
- 이 diff 에 `freshness: suspect` 로 바뀐 무관한 REQ 3건이 딸려 나간다
  (`runtime-normative-text-has-one-source`,
  `runtime-surfaces-name-the-actual-blocker`,
  `test-suite-determinism-under-xdist`). C-06 이 명시적 개발자 검사인 것과
  일관되지만, 초록 태스크와 함께 suspect 노트가 들어오는 것으로 보인다.
- closable 분기에서 **선언되지 않은** 렌즈의 `shape`/`inconsistent`/`unpaired`
  는 `next_action` 뿐 아니라 모든 필드에서 빠진다. 회귀는 아니다(이전에는 노트
  자체가 없었다). 다만 blocker 를 보고한 렌즈에 대해 표면이 침묵한다.

## 이 세션이 남긴 운영 지식

**`/reload-plugins` 는 실행 중인 프로세스를 재시작하지 않는다.** MCP 서버는
세션 시작에 묶여 있다. 페이로드를 설치한 뒤 MCP 쪽 동작을 확인하려면 세션을
새로 시작해야 한다. 설치 직후 같은 세션에서 관측한 결과로 "고쳐지지 않았다"
고 결론내면 안 된다 — 이 세션에서 내가 두 번 그렇게 잘못 판단했다.

**단, 세션 재시작이 훅 부착까지 고치지는 못했다.** 재시작 후 스폰한 `qa-cli`
도 영수증을 남기지 않았고, `background_hook` breadcrumb 도 0건이라 "뛰고
실패" 가 아니라 "안 뜬다" 가 맞다. 같은 세션에서 `prewrite_gate`,
`qa_codifier`, Stop 게이트는 정상 동작한다 — 훅 시스템 자체는 살아 있고
`SubagentStart`/`SubagentStop` 만 오지 않는다. 세션 id 도 재시작 전후로
동일했고(`8c803808-…`) `.active_sessions` 바인딩도 정상이므로 바인딩 문제도
아니다.

즉 **이건 하네스가 고칠 수 있는 범위 밖일 가능성이 높다.** 하네스가 할 수 있는
대응은 그 상황을 인식해 게이트가 만들 수 없는 증거를 요구하지 않게 하는
것이고, 그것이 다음 태스크다.

**진단 방법론 교훈:** 표본 하나로 결론내지 말 것. 05:22 에 영수증 한 쌍이
찍힌 것을 보고 "훅이 살아났다" 고 단정했는데, 그건 리로드가 **이미 돌고 있던**
에이전트를 붙잡은 일회성이었다. 그 뒤 새로 띄운 셋은 전부 무기록이다.
