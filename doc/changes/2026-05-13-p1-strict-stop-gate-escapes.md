---
task: TASK__p1-strict-stop-gate-escapes
date: 2026-05-13
---

# P1 strict stop-gate escapes — 변경 요약

회고 #1 에서 드러난 silent-scope-kill 근본 원인(stop_gate.py 가 "AskUserQuestion to cancel the task"를 정당한 turn 종결 경로로 제시)을 봉쇄한 변경이다. `plugin/scripts/stop_gate.py` 에서 cancel-push 안내 문자열을 제거하고, runtime_verdict 기반 machine gate 로 교체했다 — PASS 에 더해 BLOCKED_ENV 도 stop 허용 경로로 추가했으나, 이 경로는 반드시 신규 `plugin/agents/stop-judge.md` 에 정의된 `harness:stop-judge` 서브에이전트가 진짜 blocker 임을 의미 판단한 뒤 `write_critic_qa(lens="stop-judge", verdict="BLOCKED_ENV")` 를 통해 열린다. 산문 규칙만으로는 모델 회귀에 취약하다는 §0 압박에 응답해, `plugin/CLAUDE.md §4a Turn-end rule` 과 `CONTRACTS.md C-17` 을 추가해 런타임 게이트와 문서 계약을 동기화했다. 결과적으로 stop-judge 의 의미 판단이 task 종결의 유일한 비-PASS 권한자가 되며, AskUserQuestion 옵션 라벨을 통한 취소 유도는 규약 위반으로 기록된다.
