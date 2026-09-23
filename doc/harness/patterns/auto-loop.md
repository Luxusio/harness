---
tags: [harness, auto-loop, claude-goal]
summary: native `/goal`은 Claude에서 유일한 auto-continue primitive다. Harness는 Goal을 durable task state에 동기화하고, close-gate(`task_close` PASS)를 `/goal` condition에 넣는 것으로 지속 루프를 만든다. `stop_gate.py`는 등록되지 않아 자동 재개에 관여하지 않는다.
freshness: current
updated: 2026-09-23
---

# Auto-loop primitive — native `/goal`

## 한 줄 요약
**native `/goal`이 Claude에서 유일한 auto-continue primitive다.** Harness는
Goal을 durable state에 동기화하고, 각 Goal child task 또는 direct task의
task start → plan → develop → QA → close 공개 루프는 코디네이터가 직접
진행한다. 독립 리뷰와 `task_verify`는 close 전 내부 게이트다. Turn-end는
Claude에서 hook으로 게이트되지 않는다 — Claude `Stop` 훅 등록은 2026-09-23
제거됐다 (`plugin/hooks/hooks.json`에 `Stop` 엔트리 없음). `plugin/scripts/stop_gate.py`는
트리에 남아 있지만 어느 런타임에서도 등록된 caller가 없는 dormant 스크립트이며,
자동 재개에 관여하지 않는다.

## 동작 메커니즘

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

### Harness Goal 동기화
1. 사용자가 native `/goal <objective>` 입력 → hook이 harness Goal state를
   생성/동기화한다. 이후 agent가 Goal context를 보고 필요한 경우
   `task_start` + `goal_add_task`로 `doc/harness/tasks/.active` 마커를
   만든다. Plain repo-mutating request에서도 hook이 task를 자동 생성하지
   않으며, agent가 필요성을 판단해 `task_start`로 direct task를 연다.
2. Turn-end 자체는 hook으로 강제되지 않는다. 태스크가 `in_progress`인 동안
   완결로 인정되는 것은 receipt-backed `runtime_verdict: PASS` 를 거친
   `task_close` 뿐이다 (C-04, C-17).
3. `/goal` 조건에 close 조건을 넣으면 native Stop hook이 그 조건이
   충족될 때까지 자동 재개시킨다: `/goal harness task <task_id>이 닫힐
   때까지 진행. PLAN.md의 acceptance intent를 충족하고, 필수 review/QA
   lifecycle receipt로 runtime_verdict=PASS를 만든 뒤 task_close 성공.`
4. Task가 `.active`로 열린 채 turn이 끝나면, 태스크는 다음 turn 또는 다음
   session에서 `task_start`/`task_context`로 재개된다 — 이것이 native
   `/goal`을 쓰지 않는 경우의 지속 경로다. `/goal`을 쓰면 native Stop hook의
   재호출이 그 재개를 자동화한다.

## 왜 `stop_gate.py`가 아닌가

2026-09-23 이전에는 `plugin/hooks/hooks.json`의 `Stop` 엔트리가
`stop_gate.py`를 등록해, close-gate 미충족 시 매 turn을 block했다. 그 설계는
백그라운드 리뷰 서브에이전트를 기다리는 동안 "아직 돌고 있다"는 내용만 담은
반복적인 빈 turn을 만들어냈고, 사용자가 그 등록을 제거하라고 명시적으로
요청했다 (2026-09-23). 지속적인 non-stop 진행이 필요할 때는 native `/goal`을
쓴다는 것이 확정 방침이다.

`stop_gate.py` 자체는 되돌리기(revert)를 위해 트리에 남아 있지만 어느
런타임의 hook 등록에도 연결돼 있지 않다 (`install.py`의
`_codex_hooks_config`도 `hook_stop.py`를 배선하지 않는다). 삭제는 별도
follow-up이다.

## 참고
- Anthropic 공식 문서: <https://code.claude.com/docs/en/goal>
- Prompt-based hooks: <https://code.claude.com/docs/en/hooks-guide#prompt-based-hooks>
- 관련 contract: [`CONTRACTS.md` C-17 turn-end/continuation guidance](../../../CONTRACTS.md#c-17)
- 관련 task: `TASK__harness-run-auto-goal-loop` (2026-05-21 — 최초 조사, doc-only로 종결); `TASK__plan-single-voice-review` (2026-09-23 — Stop hook 등록 제거)
