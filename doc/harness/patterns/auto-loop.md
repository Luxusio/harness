---
tags: [harness, hooks, stop-gate, auto-loop, claude-goal]
summary: Native /goal과 harness stop_gate.py가 함께 Goal child-task close loop를 유지하는 방식, 두 Stop hook primitive의 차이, 동시 사용 방법.
freshness: current
updated: 2026-06-12
---

# Auto-loop primitive — native `/goal`과 harness `stop_gate.py`

## 한 줄 요약
**현재 사용자-facing 진입점은 native `/goal`이다.** Harness는 Goal을 durable state에 동기화하고, 각 Goal child task의 plan → develop → verify → close 루프는 `plugin/scripts/stop_gate.py`가 close-gate를 감지해 자동 재개시킨다.

## 동작 메커니즘 비교

### Anthropic `/goal` (Claude Code v2.1.139+)
1. 사용자가 `/goal <자연어 조건>` 입력.
2. 세션 메모리에 **prompt-based Stop hook**이 설치됨:
   ```json
   {"type": "prompt",
    "model": "<small_fast_model>",
    "prompt": "<조건을 포함한 evaluator 프롬프트>"}
   ```
3. 조건 텍스트 자체가 즉시 첫 turn directive로 발사됨.
4. 매 turn 종료 시 Haiku가 transcript 전체를 읽고 `{"ok": true|false, "reason": "..."}` 리턴.
5. `ok:false`면 `reason`이 Claude 다음 turn 입력으로 주입 → 자동 재개.
6. `ok:true`면 goal clear, 사용자에게 컨트롤 반환.
7. `/goal clear` 또는 `/clear`로 취소.

### harness `stop_gate.py`
1. 사용자가 native `/goal <objective>` 입력 → harness Goal state가 생성/동기화되고, 필요한 경우 `task_start` + `goal_add_task`가 `doc/harness/tasks/.active` 마커를 만든다.
2. `plugin/hooks/hooks.json` Stop 엔트리에 등록된 `python3 plugin/scripts/stop_gate.py`가 매 turn 종료 시 실행.
3. `stop_gate.py:77-151`이 active task 마커 확인 → `emit_compact_context`로 `missing_for_close` 계산:
   - PLAN.md 없음, HANDOFF.md 없음, qa-browser evidence 없음, `runtime_verdict ≠ PASS` 등.
4. 미완료이면 `_gate_response.block(...)`이 `{"decision": "block", "reason": "...", "hookSpecificOutput": {...}}` JSON을 stdout으로 emit.
5. Claude Code Stop hook contract에 따라 `reason`이 **Claude 다음 turn 입력으로 주입** → 자동 재개. `next_action_command`까지 함께 줘서 정확한 다음 호출을 명시.
6. `runtime_verdict=PASS` + `task_close` 성공 시 `.active` 마커 제거 → 다음 Stop hook은 silent allow.
7. `runtime_verdict=BLOCKED_ENV` (fresh, `stale=false`)일 때만 silent allow (`stop_gate.py:103`). Stale BLOCKED_ENV는 그대로 block 유지 (C-17 staleness clause).

### 핵심 동일성
양쪽 모두 **Stop hook 응답 contract**의 동일한 surface를 사용한다:
- `{decision/ok}: block/false` 키
- `reason` 문자열 → Claude 다음 turn 입력으로 fed back
- 매 turn 종료 시 자동 발사

따라서 "develop 완료될 때까지 Claude를 멈추지 못하게 한다"는 핵심 동작은 `stop_gate.py`로 **이미 구현되어 있다**.

## 남은 차이

| 항목 | `/goal` | `stop_gate.py` |
|---|---|---|
| Evaluator | Haiku (자연어 transcript 판단) | Python 규칙 (`missing_for_close`, mtime staleness) |
| 조건 입력 | 자연어 4000자 (`/goal …`) | 하드코딩 close-gate (PLAN.md / CHECKS.yaml / runtime_verdict) |
| 초기 kickoff | 조건 텍스트가 first directive로 즉시 발사 | native Goal sync 후 Goal child task가 plan→develop 체이닝 |
| 저장 위치 | 세션 메모리 (휘발) | `plugin/hooks/hooks.json` (영속) |
| Turn cap | "or stop after N turns" 명시 가능 | 없음 (close-gate 충족까지 지속) |
| Cancel UX | `/goal clear` | task_close 또는 stop-judge → task_blocked |
| 신뢰성 | LLM 판단 의존 (transcript 잘못 읽으면 오판) | 파일 mtime + YAML 필드 기반 (deterministic) |

규칙 기반 평가는 결정성이 강점이지만, `/goal`의 자연어 조건은 더 유연하다. 둘 다 같은 turn-주입 primitive 위에 올라간 다른 정책일 뿐이다.

## 동시 사용 (선택)

Anthropic 실제 `/goal`을 함께 켜고 싶다면 develop 진입 시점에 수동으로:

```
/goal harness task <task_id>이 닫힐 때까지 진행. HANDOFF.md와 DOC_SYNC.md 작성, CHECKS.yaml 모든 AC status: passed, runtime_verdict=PASS, task_close 성공. 또는 25 turn 후 중단.
```

매 turn 종료 시 Stop hook 두 개가 모두 발사된다:
1. `stop_gate.py` (deterministic) → close-gate 미충족 시 block + reason.
2. `/goal` prompt-based hook → Haiku가 transcript 보고 block + reason.

두 reason이 모두 Claude 입력으로 합쳐져 들어온다. **주의**: deterministic 쪽이 BLOCKED_ENV(fresh)로 silent allow를 결정해도 `/goal` 쪽은 자체 판단을 계속한다. `/goal`도 같이 멈추려면 `/goal clear`를 따로 쳐야 한다.

대부분의 경우 native `/goal` + harness Goal child task close gate만으로 충분하므로 별도 prompt-based `/goal` stop hook을 수동으로 겹쳐 쓰는 것은 권장 default가 아니다.

## 코드 인용

- `plugin/scripts/stop_gate.py:103` — fresh BLOCKED_ENV silent allow:
  ```python
  if verdict == "BLOCKED_ENV" and not ctx.get("stale", False):
      return 0  # silent allow — fresh BLOCKED_ENV from stop-judge
  ```
- `plugin/scripts/stop_gate.py:133-150` — reason 본문과 `gate_block` emit:
  ```python
  reason = (
      f"Active harness task {task_id} is open. Do not stop — finish the "
      "plan -> develop -> verify -> close loop. ..."
  )
  payload = gate_block(reason=reason, next_action_command=next_action, ...)
  json.dump(payload, sys.stdout)
  ```
- `plugin/hooks/hooks.json` Stop 엔트리에 `stop_gate.py` 등록되어 매 turn 발사.

## 참고
- Anthropic 공식 문서: <https://code.claude.com/docs/en/goal>
- Prompt-based hooks: <https://code.claude.com/docs/en/hooks-guide#prompt-based-hooks>
- 관련 contract: [`CONTRACTS.md` C-17 Stop gate freshness](../../../CONTRACTS.md#c-17)
- 관련 task: `TASK__harness-run-auto-goal-loop` (2026-05-21 — 본 조사 결과 doc-only로 종결)
