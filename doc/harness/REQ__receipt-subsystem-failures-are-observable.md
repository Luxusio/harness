---
tags: [harness, receipts, hooks, install, session-binding, observability]
summary: 영수증 서브시스템은 조용히 죽지 않는다. import 실패는 payload 가 지목한 repo 에 항상 breadcrumb 를 남기고, 설치 트리의 stale bytecode 는 인스톨러가 제거하며, 열린 태스크를 resume 한 세션은 그 태스크에 바인딩된다.
updated: 2026-09-09
freshness: current
invalidated_by_paths:
  - plugin/scripts/background_hook.py
  - plugin/scripts/subagent_lifecycle.py
  - plugin/scripts/_lib.py
  - plugin/mcp/harness_server.py
  - install.py
  - tests/test_background_hook_import_failure.py
  - tests/test_session_hint_marker_binding.py
  - tests/test_task_context_binds_resuming_session.py
  - tests/regression/task__unified_install/test_install_py.py
  - tests/conftest.py
---

# REQ — 영수증 서브시스템의 실패는 관측 가능하다

## Context

2026-08-09 무렵부터 약 한 달간 이 프로젝트에서 영수증을 얻을 수 없었다.
2026-09-08/09 진단에서 **서로 독립적인 결함 두 개**가 나왔고, 그중 하나가
나머지 하나와 다른 모든 신호를 가리고 있었다. 둘은 하나의 이야기다:
Defect A 가 Defect B 를 — 그리고 그 외 모든 것을 — 보이지 않게 만들었다.

## Expected behavior

### 1. Import 실패는 언제나 breadcrumb 를 남긴다

`background_hook` 이 자기 의존성을 import 하지 못하면, **훅 payload 의 `cwd`
가 지목한 저장소**의 `doc/harness/learnings.jsonl` 에
`gate-crash` / `receipt-subsystem-unavailable` 항목이 기록된다. 스크립트
위치를 거슬러 올라가는 탐색은 payload 가 없는 호출자를 위한 fallback 으로만
남는다. 훅은 여전히 exit 0 이다 (C-12).

**왜 payload 인가.** 훅은 *설치된* 트리에서 실행된다
(`~/.claude/harness-dev/plugin/scripts`). 그 위에는 `doc/harness` 가 없으므로
스크립트 기준 탐색은 아무것도 찾지 못하고 조용히 반환했다. 즉 breadcrumb 를
가장 필요로 하는 유일한 환경에서 breadcrumb 가 동작하지 않았다.

이것이 **재발**이라는 점이 핵심이다. 동일한 `PermissionError` 가
2026-08-26 에 영수증을 죽였고, 그때 추가된 것이 바로 이 guard 였다
(`_report_import_failure` docstring: "세 세션이 세 가지 다른 원인으로
진단했다"). 그 guard 는 설치 트리에서 한 번도 발화할 수 없었기 때문에,
같은 고장이 한 달을 더 갔다. **가드는 그것이 실행되는 환경에서 검증되지
않으면 가드가 아니다.**

Payload 를 읽기 위한 stdin 소비는 import 실패 경로에서만 일어난다. 그 경로는
직후 `sys.exit(0)` 이므로 정상 경로의 `read_hook_input()` 을 굶기지 않는다.

### 2. Stale bytecode 는 영수증을 무력화할 수 없다

인스톨러는 자기가 소유한 런타임 payload 트리에서 `__pycache__` 디렉터리를
제거한다. **payload 비교가 SYNCHRONIZED 를 보고해 설치를 건너뛰는 경로에서도
제거한다.**

**왜 인스톨러가 소유자인가.** `subagent_lifecycle` 은 import 시점에 영수증
어댑터를 바인딩하고, `_lib` 은 호출 모듈의 code object 가 그 파일을 새로
컴파일한 결과와 일치하지 않으면 거부한다
(`PermissionError: receipt adapter binding requires its canonical module
import`). 소스와 어긋난 `.pyc` 하나가 이 검사를 깨뜨리면 모든
`SubagentStart`/`SubagentStop` 이 `main()` 전에 죽고 영수증이 하나도 쓰이지
않는다.

그리고 그 상태는 **스스로 낫지 않는다**: `install.py` 의 `_VOLATILE_DIR_NAMES`
가 `__pycache__` 를 비교 대상에서 제외하므로, 망가진 트리도 SYNCHRONIZED 로
보고되고 `--if-stale` — 하네스 자신의 전달 경로인
`install_verified.py` 가 쓰는 바로 그 경로 — 는 트리를 교체할 설치를 건너뛴다.
나쁜 캐시는 이후 모든 실행에서 살아남는다. 이 루프를 끊는 것이 요구사항이다.

삭제 범위는 좁다: `__pycache__` 디렉터리만, 인스톨러가 이미 쓰는 트리
안에서만, 실제 디렉터리만 (`os.walk` 은 심링크를 따르지 않고 `shutil.rmtree`
는 심링크를 거부한다). 제거는 항상 안전하다 — Python 이 다음 import 에서
다시 만든다.

### 2a. 테스트 스위트는 실제 설치 트리에 닿을 수 없다

인스톨러의 mutating 진입점 — `_prune_bytecode_caches`,
`sync_claude_payload`, `sync_codex_payload`, `install_codex_plugin_cache` —
은 pytest 아래에서 실행될 때 실제 사용자 홈의 `~/.claude` 와 `~/.codex`
아래 경로를 **거부한다**. 그리고 `tests/conftest.py` 의 autouse fixture 가
`HARNESS_DEST` 기본값을 tmp 경로로 돌려, 루트를 명시하지 않은 테스트가
실 런타임을 상속하지 않게 한다. 홈 경로는 `Path.home()` 이 아니라 password
database 에서 읽는다 — `HOME` 을 `tmp_path` 로 돌린 테스트는 정확히 가드가
허용해야 하는 격리된 경우이기 때문이다.

**왜 REQ 인가.** §2 의 수정 자체가 같은 종류의 조용한 실패를 반대 방향에서
다시 만들었다. 프룬을 staleness 판정 **앞**에 두는 것은 옳지만(그 자리가
버그가 살던 곳이다), 그 결과 `rmtree` 가 다른 모든 가드보다 앞에 놓였고
대상 루트의 기본값은 실제 `~/.claude/harness-dev` 와 `~/.codex/harness` 다.
2026-09-09 리뷰 라운드가 `rmtree(cache)` → `rmtree(cache.parent)` mutation 을
전체 스위트로 돌렸을 때, 스위트가 설치된 `plugin/scripts` 와 `plugin/mcp`
(둘 다 `__pycache__` 의 부모) 를 지웠다. `background_hook.py: No such file or
directory` — 영수증 기록이 또 한 번, 같은 무증상으로 죽었다.

**여섯 번의 리뷰가 놓친 이유가 이 문서의 요점이다.** 리뷰어들은 불변식
검사를 걸어 두었지만 그 대상이 `doc/harness/tasks/.active_sessions/` 같은
**태스크 산출물**이었고, **설치 트리**는 아무도 보지 않았다. 테스트가
건드릴 수 있는 실 상태는 저장소 안에만 있지 않다. 훅이 실제로 실행되는
트리 — 설치된 런타임 — 도 스위트의 blast radius 안에 있으며, 검사 목록에
그것이 없으면 정확히 §1 이 말한 상태가 된다: 가장 필요한 환경에서만
관측되지 않는 고장.

검증(`test_suite_run_cannot_reach_the_real_install_roots`)은 두 층을 각각
고정한다: conftest 기본값이 실 홈 밖을 가리키는 것, 그리고 각 진입점이 실
루트를 거부하는 것. 각 probe 는 가드를 지웠을 때 **기존 상태를 읽지도 바꾸지도
지우지도 않는 형태**로 고른다 — 없는 하위 디렉터리, 없는 payload 소스, 트리가
활성화되기 전에 예외를 던지는 payload 빌드 — 그래서 가드를 증명하는 일이
가드가 지키는 트리를 위협하지 않는다.

문구를 "아무것도 mutate 하지 않는다" 로 쓰지 않는 이유는 측정 결과다. 마지막
두 probe 는 `sync_*_payload` 가 `_build_*_payload` 에 도달하기 전에 실제 홈
아래에 staging 디렉터리를 하나 만들고, 그 예외를 받는
`except BaseException` 이 다시 지운다. `_activate_staged_tree` 에는 닿지
않으므로 기존 트리는 안전하지만, 강한 쪽 문구를 그대로 두면 나중에 누가
`_build_claude_payload` mock 을 중복이라 판단해 지울 수 있고 — 그러면 가드가
뚫린 상태에서 payload 가 실 런타임 위로 **활성화**된다. §2a 가 다루는 실패가
정확히 "검증되지 않은 blast-radius 주장" 이므로, 이 문서 안에 그런 주장을
남겨둘 수 없다.

### 3. 열린 태스크를 resume 한 세션은 그 태스크에 바인딩된다

열린 태스크를 이어받는 세션은 **자기 `session_id` 로 키가 잡힌** 세션 마커를
얻어야 하며, 그 마커는 같은 `task_dir` 과 `run_id` 를 가리킨다. 쓰기는
**additive** 다: 다른 세션의 마커를 지우지 않으므로 두 번째 세션이 바인딩을
훔칠 수 없고, 첫 세션이 이미 만든 영수증은 계속 유효하다. 다중 마커 상태는
이미 지원되며 이 저장소에서 실제로 발생한다
(`TASK__lightweight-workflow-orchestration` 은 run_id 를 공유하는 마커 두 개를
가진다).

바인딩 표면은 `task_context`와 기본 `task_start`다. `task_context`는 상태를
읽으면서 안전 조건이 맞을 때 바인딩하고, 기존 open/blocked task를 명시적으로
재개하는 plain `task_start`도 같은 `run_id`와 영수증을 보존해 바인딩한다.
`fresh_run: true`만 새 generation을 의도적으로 만들고 기존 증거를 폐기한다.

바인딩은 그 호출이 **실제 resume 일 때에만** 일어난다. 조건은 두 개이고 둘
다 필요하다: 대상 태스크가 `open` 이어야 하고, **이 세션이 지금 붙잡고 있는
열린 태스크가 없거나 그것이 곧 같은 태스크여야** 한다.

**판정과 쓰기는 같은 정체성으로 한다.** 마커 키가 될 `session_id` 를 한 번만
구하고(`read_session_hint(...) or current_session_id()`), 그 값으로 판정한 뒤
그 값으로 쓴다. 첫 구현은 이것을 어겼다: 판정은 hint 로, 쓰기는 hint 가 비면
`current_session_id()` 로 fallback 했다. `.session-hint` 를 쓰는 곳은
`prompt_memory.py` 하나뿐이고 그것은 Claude 의 `UserPromptSubmit` 훅으로만
등록되어 있으므로 **Codex 에서 hint 는 영구히 비어 있다**. 빈 id 와 `default`
는 둘 다 `resolve_session_task_binding` 이 거부하므로, 세션 조건은 Codex 의
모든 세션에서 항상 참 — 즉 없는 것과 같았다. 그래서 fix 이전 상태에서
§3 의 "다른 열린 태스크 peek 은 focus 를 훔치지 않는다" 는 **Claude 에서만**
참이었고 Codex 에서는 거짓이었다. Codex 는 `CLAUDE.md` 가 지시하는 기본
경로이며, `codex_hook_registration` 은 pre-spawn 훅에서 `default` 마커를 실제
thread id 로 승격하므로, peek 한 태스크로 다음 subagent 영수증이 실제로
떨어졌다. 이것은 이 diff 가 만든 회귀였다 — 변경 전 `handle_task_context` 는
마커를 아예 쓰지 않았으므로 Codex 의 peek 은 무해했다.

세션이 "붙잡고 있는" 값은 `resolve_active_task_dir(repo_root, session_id=sid)`
로 읽는다. 이 함수는 이 쓰기가 덮어쓰는 두 마커를 reader 가 보는 순서 그대로
읽는다 — 자기 세션 마커 먼저, 그 다음 공유 legacy `.active` — 그리고 `""` 와
`default` 를 모두 정상적으로 해석한다. 단, 그 값이 더 이상 `open` 이 아닌
태스크를 가리키면 붙잡은 focus 가 없는 것으로 본다.

이 완화가 방어하는 상태는 **park 이 아니다.** `clear_active_marker` 는
legacy `.active` 가 떠나는 태스크를 가리키기만 하면 세션 조건 없이 지우고,
`task_blocked`/`task_close` 는 `strict=True` 로 호출하므로 포인터가 남으면
예외가 난다. 같은 세션 park 과 다른 세션 park 을 실제 MCP 표면으로 재현해
확인했다 — 두 경우 모두 legacy 는 사라지고 완화가 아니라 "붙잡은 것 없음"
분기로 간다. 실제로 이 분기에 도달하는 것은 **control 이 더 이상 검증되지
않는** 태스크를 가리키는 포인터다: 태스크 디렉터리가 지워졌거나 `TASK.json`
이 잘리거나 읽을 수 없으면 `task_control_status` 가 `invalid` 을 돌려준다.
태스크 트리는 gitignore 된 임시 산출물이고 `.active` 는 그 형제 파일이라
어느 쪽도 서로를 정리해 주지 않는다. 이때 완화가 없으면 그 포인터 하나가
이후 모든 마커 없는 세션의 resume 을 영구히 막는다. 2026-09-09 당시
빠져나갈 길은 run의 영수증을 파괴하는 `task_start`뿐이었지만, 현재 plain
`task_start`는 같은 run을 보존하는 명시적 재개 경로다.

같은 태스크로 이미 resolve 되는 경우에도 마커를 다시 쓴다. 이것은 no-op 이
아니다: 다른 세션의 명시적 `task_start(..., fresh_run=true)`가 `run_id`를
회전시키면 이 세션의 마커는
태스크는 맞지만 run 이 틀린 상태로 남고, `resolve_session_task_binding` 은
정확히 그 불일치를 거부한다. 재기록이 그것을 복구한다. (`updated` 필드가
읽히지 않는다는 것은 맞지만, `run_id` 는 읽힌다.)

`write_active_marker` 는 이 세션의 마커 파일만 만드는 것이 아니라 단일 값인
legacy `.active` 파일도 다시 쓰고, stop gate 가 그것을 읽는다. 그래서 조건이
하나라도 빠지면 *읽기*가 write focus 를 훔친다:

- 상태 확인이 없으면 park 된 태스크를 읽기만 해도 focus 가 그리로 간다.
- 세션 확인이 없으면 — C-09 는 두 번째 mutating 요청을 **큐잉**할 뿐 첫
  태스크를 닫지 않으므로 열린 태스크는 늘 여럿이다 — 다른 열린 태스크를
  `task_context` 로 한 번 들여다보는 것만으로 이 세션의 마커와 `.active` 가
  거기로 옮겨간다. 그러면 다음 reviewer 영수증이 엿본 태스크에 떨어지고
  진행 중인 태스크에는 아무것도 남지 않는다 — 이 문서가 다루는 실패
  유형이 반대 방향에서 재현된 것이다.

**알려진 잔여 위험 (의도적으로 남김).** 마커를 실제로 쓰는 모든 경우 — 최초
resume 과 위의 focus 재확인 — 에서 legacy `.active` 는 함께 다시 쓰인다.
세션 B 가 태스크 X 를 resume 하면, 세션 A 가 태스크 Y 를 작업 중이어도
`.active` 는 X 를 가리킨다. 남긴 이유: `resolve_active_task_dir` 은 세션
마커를 먼저 보고 legacy 는 fallback 으로만 읽으므로, **자기 마커를 가진
세션은 영향을 받지 않는다**. 관측 가능한 대상은 마커 없는 reader 뿐이고,
그런 reader 에게는 방금 focus 를 잡은 세션을 가리키는 것이 오히려 맞는 값이다.
이것을 닫으려면 legacy 를 건드리지 않는 두 번째 마커 쓰기 경로가 필요한데,
그러면 `task_start` 와 `task_context` 의 바인딩 의미가 갈라진다 — 실제로
관측된 피해가 나오기 전까지는 지불할 가치가 없는 비용이다.

**알려진 잔여 위험 2 — 안전한 쪽으로의 거절 (의도적으로 남김).** 마커가 없는
세션이 legacy `.active` 가 가리키는 것과 *다른* 열린 태스크를 resume 하면
바인딩이 거절된다. 가드는 legacy 포인터를 "이 세션이 붙잡고 있는 것" 으로
읽고, 그 태스크가 열려 있으므로 훔치기로 판정한다. 그 세션은 Defect B 상태로
남는다 — subagent stop 마다 `session-task-binding-unresolved` 이고, 증상은
또다시 아무 신호도 없는 부재다. 우회는 다른 태스크를 먼저 닫거나 park 한 뒤
plain `task_start`로 같은 run을 보존해 재개하는 것이다.

**경고를 띄우지 않기로 한 이유.** 이 표면은 resume 의도와 peek 의도를 구분할
수 없다 — 구분할 수 없다는 사실이 가드가 안전한 쪽으로 거절하는 이유 자체다.
같은 상태에서 훨씬 흔한 쪽은 peek 이고, 거기에 "이 세션은 바인딩되지 않았다"
는 경고를 붙이면 사용자를 불필요한 처방으로 밀어붙인다. 특히 파괴적인
`fresh_run: true`를 일반 복구로 권해서는 안 된다. 없는 신호보다 나쁜 것은
틀린 신호다. 이것은 회귀가
아니라 좁힘이기도 하다: 이 diff 이전의 `task_context` 는 어떤 세션도
바인딩하지 않았다. resume 의도를 표면에 실어 보낼 수 있는 명시적 인자나,
markerless 세션의 실제 피해 관측이 나오면 그때 다시 연다.

마커 쓰기 거부는 삼키지 않는다. `write_active_marker` 가 거부하면
`task_context` 는 오류를 반환한다 — 그것은 어떤 세션도 바인딩될 수 없다는
런타임 결함이지 감춰도 되는 열화가 아니며, 이 문서 전체의 주제다.
`handle_task_start` 도 같은 호출을 같은 방식으로 다룬다.

## 관측된 사실 (2026-09-09)

- 설치된 scripts 디렉터리의 `__pycache__` 를 지우자 import 가 복구되었고,
  곧바로 실제 subagent 가 이 태스크의 `RECEIPTS.jsonl` 에 `started` +
  `completed` 쌍을 썼다.
- 2026-09-09 당시 `handle_task_start` 는 신규 생성과 resume **양쪽 모두**에서
  `write_active_marker` 를 호출한다. PLAN 이 적은 "신규 생성일 때만" 은
  사실이 아니었다. 당시 실제 공백은 다른 곳에 있었다: 안전한 resume 표면은
  `task_context`뿐이었고 (`task_start`를 다시 부르면 run id가 교체되고
  영수증 스트림이 초기화됐다), `handle_task_context`는 마커를 쓰지 않았다.
  따라서 이어받은 세션의 모든 subagent 는
  `session-task-binding-unresolved` 로 거부되고 영수증을 남기지 못한다.
- 세션 `d1866f9e-1d00-4179-91f8-dc676f461d3e` 는 `.session-hint` 에 기록되어
  있으나 마커 파일이 없다. 원래 태스크를 만든 세션의 마커는 `task_start`
  시각 그대로이고 run_id 도 회전하지 않았다 — 즉 그 세션은 `task_start` 를
  부른 적이 없다. 위 진단과 일치한다.

## Rejected options

**영수증 바인딩에서 세션 스코프 제거.** 거부. 세션 스코프는 장식이 아니라
**귀속 키**다: subagent payload 에는 태스크 정체성이 없으므로 session → task
매핑이 유일하게 이용 가능한 연결이다. 이번 진단 중에도 사용자의 태스크가
열린 상태에서 headless probe 세션 두 개가 같은 저장소를 상대로 돌았다. 세션
스코프가 없었다면 그 probe 들의 subagent 가 사용자 태스크에 review 영수증을
썼을 것이다.

**`_lib` 의 어댑터 정체성 guard 완화.** 거부. 그 guard 는 제 일을 하고 있었다.
버그는 guard 가 잡은 실패를 **보고할 수 없었다**는 것이다.

## §3 구현 (2026-09-09)

`plugin/scripts/_lib.py` 의 `_make_control_writer_authority()` 허용 목록에
`"handle_task_context"` 하나를 additive 하게 추가하고
(`harness_server` 역할의 handler 는 7개 → 8개), `harness_server.py` 의
`_bind_control_writer` 루프에 `handle_task_context` 를 등록했다.
`handle_task_context` 는 `session_id = read_session_hint(repo_root) or
current_session_id()` 를 한 번 구한 뒤, 태스크 상태가 `open` 이고
`_session_resumes(repo_root, td, session_id)` 가 참일 때 — 즉
`resolve_active_task_dir(repo_root, session_id=session_id)` 가 비었거나,
열려 있지 않은 태스크를 가리키거나, 이미 이 `task_dir` 로 resolve 될 때 —
`write_active_marker(repo_root, td, session_id=session_id)` 를 호출한다.
판정과 쓰기가 같은 `session_id` 를 쓰는 것이 이 구현의 핵심 불변이다.
다른 허용 항목, `authorized()`, `_lib` 의 어댑터 정체성 guard 는
건드리지 않았다.

우회로가 없었다는 판단은 그대로 유효하다:

- 허용 프레임 워킹은 체인 어딘가에 바인딩된 writer 를 요구하고,
  `handle_task_context` 는 어떤 바인딩된 writer 로부터도 호출되지 않는다.
- `_atomic_text_write` 로 마커 파일을 직접 쓰는 것은 marker-writer guard 를
  우회하는 것이므로 허용되지 않는다.
- Codex 쪽 `codex_hook_registration.restore_watcher_registration` 은
  rollout/thread 식별자에 묶인 Codex 전용 경로이며 Claude 세션에 재사용할 수
  없다.

`_lib.py` 는 MAINTENANCE 마커를 요구하는 workflow-control-surface 파일이므로,
이 변경은 `TASK__session-rebinds-receipt-marker` 의 MAINTENANCE 마커가
승인한 범위 안에서만 이루어졌다.

`task_context` 는 이제 **쓰기 표면**이다. 그래서 핸들러를 실 저장소 루트로
호출하는 테스트는 자기 tmp 태스크를 실 저장소의 세션 마커와 legacy `.active`
에 써 넣는다. 실제로 `test_task_context_returns_structured_content` 가
`canonical_task_dir` 만 갈아끼우고 `find_repo_root` 는 그대로 두어, 실 저장소
`.active_sessions/` 에 `/tmp/.../TASK__mcp` 를 가리키는 마커를 남기고 (살아
있는 세션의 마커까지 덮어썼다) `.active` 를 순간적으로 옮겨 다른 워커의
`scratch_task_in_real_repo` 테스트를 간헐적으로 red 로 만들었다. 그 테스트는
`_call_in_repo` 로 옮겨 tmp 루트에 묶었다. 이 표면을 호출하는 테스트는
반드시 control root 를 tmp 로 고정해야 한다.

계획했던 "이미 일치하는 마커면 no-op" 단축 경로는 넣지 않았다. 대신 같은
태스크로의 재기록을 **의도된 focus 재확인**으로 유지한다: 위 §3 이 적은 대로
`run_id` 회전을 복구하는 관측 가능한 효과가 있다.

검증: `tests/test_task_context_binds_resuming_session.py` 는 다른
`session_id` 로의 resume 후 실제 `SubagentStart`/`SubagentStop` 쌍을 재생해
`RECEIPTS.jsonl` 에 `started` + `completed` 가 붙는 것을 확인하고, 첫 세션의
마커가 그대로 resolve 되는 additive 성질, `run_id` 불변, 영수증 스트림 불변,
resume 없이 같은 재생이 아무것도 남기지 않는 반대 사례, 마커도 legacy
포인터도 없는 세션이 실제로 바인딩되는 것, park 된 태스크가 write focus 를
훔치지 않는 것, 마커 없는 세션이 다른 열린 태스크를 읽어도 focus·바인딩·
`.active` 가 그대로이고 영수증이 계속 진행 중 태스크로 가는 것, **hint 가
없는 (Codex 모양) 세션에서 열린 태스크 두 개일 때 peek 이 `default` 마커와
`.active` 를 옮기지 않는 것**, control 이 더 이상 유효하지 않은(`invalid`)
태스크를 가리키는 legacy 포인터가 resume 을 막지 않는 것, 회전된 `run_id` 가
재기록으로 복구되는 것, 거부가 오류로 보고되는 것을 각각 고정한다.

여기서 `park 된 태스크가 남긴 포인터` 라고 쓰지 않는 이유는 위 §3 의 측정
결과다 — park 은 `clear_active_marker` 가 `strict=True` 로 포인터를 지우므로
그 상태를 만들 수 없다. 이 문단은 2026-09-09 5차 리뷰에서 그 정정이 문서
전체로 이어지지 않은 채 남아 있던 것을 고친 것이다.

가드 분기별 mutation 과 그때 red 가 되는 테스트:

| Mutation | red 가 되는 테스트 |
|---|---|
| 허용 목록에서 `handle_task_context` 제거 | 이 파일 전부 |
| 마커 쓰기 삭제 | `..._receipts_flow_again` 외 4 |
| 쓰기를 `handle_task_start` 위임으로 교체 (읽기 표면에 task-start 상태 전이를 혼입) | `..._receipts_flow_again`, `..._refreshes_a_rotated_run_id` |
| 호출부 상태 conjunct 삭제 | `..._parked_task_does_not_steal_write_focus` |
| 세션 가드 전체 삭제 | `..._another_open_task...`, `..._without_a_session_hint...` |
| 가드에서 legacy fallback 삭제 (자기 마커만 조회) | `..._another_open_task_does_not_steal_write_focus` |
| not-open 완화 삭제 | `..._legacy_pointer_to_an_unvalidatable_task_does_not_block_a_resume` |
| `not held` 분기 삭제 | `..._session_holding_nothing_at_all_binds_on_resume` |
| 같은 태스크 분기 삭제 | `..._refreshes_a_rotated_run_id` 외 2 |
| 첫 버전 가드 복원 (hint 정체성 + `resolve_session_task_binding`) | `..._without_a_session_hint...`, `..._another_open_task...` |
| 예외 삼킴 | `..._refused_marker_write_is_reported_not_swallowed` |

**이 표는 정정본이다.** 이전 판은 "열 개 mutation 이 모두 red 로 잡힌다" 고
적었으나 그중 둘 — 호출부 상태 conjunct 삭제와 가드의 legacy fallback 삭제 —
은 전체 스위트에서 **살아남았다**. 원인은 하나다: 모든 fixture 가 peek 하는
세션에게 *다른 열린 태스크를 가리키는 자기 마커*를 미리 쥐여주었으므로 같은
세션 분기 하나로 모든 단언이 만족되었고, 나머지 두 분기는 한 번도 실행되지
않았다. 특히 parked 케이스의 docstring 은 "상태 guard 가 없으면 park 된
태스크를 읽기만 해도 focus 가 옮겨간다" 고 단언했지만, 그 fixture 자신이
legacy 포인터를 열린 태스크에 남겨두어 그 문장을 관측할 수 없었다. 지금은
parked 케이스에서 legacy 포인터를 실제 `task_blocked` 가 그러듯 지우고, 다른
열린 태스크 케이스에서 peek 세션의 마커를 없애 두 분기를 각각 강제한다.
`not held` 분기도 같은 이유로 미검증이었으므로 전용 케이스를 추가했다.

단독 mutation "판정만 hint 로 되돌리기" 는 지금도 살아남으며, 그것은 정확한
사실이다: 가드가 `resolve_session_task_binding` 이 아니라
`resolve_active_task_dir` 를 쓰므로 빈 id 로도 legacy fallback 이 같은 값을
돌려준다. 정체성 불변식(판정과 쓰기가 같은 `session_id`)은 첫 버전 가드 전체를
복원하는 mutation 으로 검증한다. 그 조합에서만 관측 가능한 차이가 난다.

이 정정 자체가 §1 의 명제가 한 층 위에서 재현된 것이다: **가드는 그것이
실행되는 환경에서 검증되지 않으면 가드가 아니다** — 그리고 테스트는 자기가
이름 붙인 분기에 실제로 도달하지 않으면 그 분기의 테스트가 아니다.
