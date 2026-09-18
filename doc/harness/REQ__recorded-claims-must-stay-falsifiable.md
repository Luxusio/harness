---
tags: [harness, qa, knowledge, diagnostics, setup]
summary: 축적된 QA 지식은 재검증되지 않으면 이후 렌즈를 잘못 이끈다. known-red 주장은 기계적으로 반증 가능해야 하고, 가용성 프로브는 추측한 명령이 아니라 manifest 가 선언한 명령을 대상으로 해야 한다.
updated: 2026-09-18
freshness: current
invalidated_by_paths:
  - doc/harness/qa/QA_KNOWLEDGE.yaml
  - tests/test_qa_knowledge_shape.py
  - tests/conftest.py
  - plugin/skills/setup/verify-report.md
  - tests/test_setup_verify_report_probe.py
  - doc/harness/manifest.yaml
  - plugin/mcp/harness_server.py
---

# REQ — recorded claims must stay falsifiable

## Context

세 개의 후속 후보를 조사하며 시작했는데, 그중 하나의 전제가 반증됐다. 후보는
"manifest `test_command` 가 `uv run pytest` 인데 서브에이전트 셸에 `uv` 가 없다"
였고, 근거는 `QA_KNOWLEDGE.yaml` 에 QA 렌즈가 기록한 문장이었다.

실측 (2026-09-18, 서브에이전트 셸 내부에서 실행):

```
command -v uv      -> /home/ccc/.local/share/mise/shims/uv
uv run pytest --version -> pytest 9.0.3   (rc 0)
python3 -m pytest --version -> No module named pytest
```

기록된 주장은 **거짓**이었다. manifest 는 옳았고, 실제로 실패하는 건 `python3 -m
pytest` 뿐이다. 같은 파일의 더 오래된 노트는 이미 그렇게 적어 뒀는데, 나중 노트가
그 한 가지 실패를 엉뚱한 도구로 일반화했고 아무도 두 문장을 대조하지 않았다.

이 저장소에서 같은 형태가 반복 관측된다:

| 기록된 주장 | 실제 | 대가 |
|---|---|---|
| "uv is NOT on PATH inside subagent shells" | 거짓 | 이후 렌즈가 정확한 manifest 명령을 우회 |
| "the suite runs **only** via `uv run pytest`" | 과장 (`.venv/bin/python -m pytest` 도 동작) | 위 노트가 자기모순으로 읽히게 됨 |
| "known pre-existing RED / baseline" (2회) | 실제 회귀 | 4개 이상 세션이 조사 없이 통과 |

known-red 건의 전말은
`doc/harness/REQ__unreadable-worker-state-is-not-recordable.md` 가 소유한다.
여기서 중요한 건 공통 구조다: **QA_KNOWLEDGE 는 append-only 로 쌓이고 아무도
재검증하지 않는다.** 틀린 주장은 무기한 살아남고, 그 주장의 기능이 정확히
"다음 독자가 확인하지 않게 만드는 것"이라서 스스로를 보호한다.

## Requirement

1. **기계 검증이 가능한 부류는 기계가 검증한다.** known-red 주장은 그 부류다 —
   지명된 테스트를 돌려보면 참/거짓이 갈린다. live(SUPERSEDED 아님) known-red
   주장은 실재하는 pytest node id 를 지명해야 하고, 그 node 는 지금도 실제로
   실패해야 하며, 도입 커밋을 명시해야 한다. 고쳐진 red 를 계속 변호하는 노트는
   중립이 아니라 다음 진짜 실패를 위한 변명이다.
2. **주장의 범위는 해악의 범위와 같아야 한다.** 강제 대상은 "실패를 언급한
   노트"가 아니라 "그 실패를 예상된 것으로 취급하도록 허가하는 노트"다.
   Intermittent 장애를 서술하는 노트는 경계를 요청하는 것이지 면제를 주는 것이
   아니므로 대상이 아니다. 마커 단어만으로는 부족하다 — `baseline`/`pre-existing`
   은 이 파일에서 평범한 뜻으로도 쓰인다 (baseline recipe, baseline lint 출력,
   스위트 baseline 합계, `handle_task_verify` 의 pre-existing 동작). 그래서
   마커는 실패 단어와 **붙어 있어야** 한다: `Known pre-existing RED`,
   `Baseline RED confirmed`. 이건 실제 known-red 노트들이 쓰인 방식 그대로다.
3. **면제는 강제의 구멍이 되어서는 안 된다.** known-red 어법을 쓰면서 아무
   node id 도 지명하지 않은 노트는 검사 대상에서 빠지는 게 아니라, 반증 불가능한
   주장으로 **보고된다**. 첫 구현은 그런 노트에서 빈 리스트를 돌려줬고, 그 결과
   "node id 를 지명해야 한다"는 요구사항은 문서에만 존재하고 코드에는 없었다.
   리뷰가 정확히 그걸 지적했다 — 이 저장소에서 반복 관측되는, 실행되지 않는
   enforcement point 를 명시한 durable note 의 또 한 사례다.
4. **가용성 프로브는 선언된 명령을 대상으로 한다.** setup 의 pytest 점검은
   manifest `test_command` 에서 유도한다. 고정된 `python3 -m pytest --version`
   은 launcher (`uv`/`poetry`/`hatch`/`tox`/venv) 를 쓰는 정상 저장소를 blocking
   으로 오보한다 — 예외가 아니라 기본값이다.
5. **판단을 내놓는 표면은 근거도 내놓는다.** `task_verify` 는 `_watcher_status`
   로 `next_action` 을 재작성하면서 그 status 를 응답에서 빼고 있었다.
   `task_start`/`task_context` 는 싣는다. 프로토콜에서 가장 권위 있는 표면이
   결론만 주고 관측은 감추면, 호출자는 읽을 수 없는 워처와 건강한 워처를
   구분할 수 없다.

## Enforcement

- `tests/test_qa_knowledge_shape.py::KnownRedClaimsTests` — `known_red_claims`
  가 live 주장을 추출하고, `red_claim_violation` 이 지명된 node 를 실제로
  돌린다. pytest exit 1 만이 유효한 red 다. 0 은 주장이 낡았다는 뜻이고, 4/5 는
  돌려볼 수 없는 node 를 지명했다는 뜻이며, 그 밖의 코드(2 interrupted,
  3 internal error)는 **프로브 자신의 실패**라고 말한다 — 중단되거나 깨진 실행을
  노트 탓으로 돌리지 않는다. 셋 다 위반으로 보고되므로 `{0,1}` 밖은 전부 위반,
  즉 fail-closed 다.
  - node id 를 지명하지 않은 known-red 노트는 `(text, None)` 으로 보고되고
    `test_live_known_red_claims_name_a_node_that_is_still_red` 가 그걸 실패로
    처리한다. 조용히 건너뛰지 않는다.
  - 프로브에는 `PROBE_TIMEOUT_SECONDS` 상한이 있다. 없으면 hang 한 테스트를
    지명한 주장 하나가 스위트를 무한정 붙잡는다 — 반증 불가능한 주장을 막는
    가드가 스스로 반증 불가능한 hang 이 되는 셈이다. timeout 은 주입 가능하고
    `test_a_hanging_test_is_reported_not_waited_on` 이 2초 예산으로 그 arm 을
    덮는다. `timeout=` 인자를 지우면 그 테스트가 61초 뒤 red 가 된다 (측정).
  - 현재 live 주장은 0건이므로 실제 파일 스캔만으로는 공허하다. 그래서
    합성 입력 검사와, **SUPERSEDED 마커를 지운 실제 파일**에서 known-red 매치가
    최소 한 건 남고 그중 최소 하나가 receipt-watcher node 를 지명하는지 보는
    검사가 함께 있다. 패턴이 조용히 아무것도 매칭하지 않게 되는 실패 모드를 그
    검사가 막는다.
    의도적으로 subset 검사이고, live 주장이 몇 건인지에 대해서는 아무 말도 하지
    않는다. 앞선 판본은 "live 주장 0건" 과 "되살아난 주장이 *전부* receipt-watcher
    node" 를 단언했는데, 그러면 누군가 이 메커니즘을 규정대로 쓰는 순간 — 올바른
    형식의 live 주장 하나, 혹은 다른 테스트에 대한 올바른 SUPERSEDED 주장 하나 —
    스위트가 red 가 됐다. 올바른 사용을 처벌하는 가드는 삭제되고, 그때 진짜 검사도
    함께 사라진다. live 주장의 정당성은
    `test_live_known_red_claims_name_a_node_that_is_still_red` 가 소유한다.
  - **exit 1 자체는 지명된 node 에 대한 증거가 아니다.** 중첩 세션은 pytest 세션
    전체라서, 세션 스코프 teardown 이나 fixture 가 에러 나도 rc 1 이 된다
    (`1 passed, 1 error`) — 선택된 테스트는 통과했는데도. 그걸 "아직 red" 로
    읽으면 낡은 주장을 변호하게 되고, 그게 이 가드가 막으려는 바로 그 false
    green 이다. 그래서 summary 에 failed 가 있고 error 가 없을 때만 red 로
    인정한다.
  - **프로브는 `HARNESS_NESTED_PROBE=1` 로 자신을 표시하고, `tests/conftest.py`
    의 세션 훅과 install-tree 가드가 그걸 보고 no-op 한다.** 없으면 프로브가
    개발자의 live `doc/harness/tasks/.active` 를 자기 실행 동안 renaming 해
    치우고, `pytest_sessionfinish` 는 복원 전에 발견한 `.active` 를 unlink
    하므로 동시에 도는 형제 테스트가 방금 만든 마커를 파괴할 수 있다.
    `REQ__test-suite-determinism-under-xdist.md` 규칙 2 가 금지하는 형태다.
    10ms 폴러 실측: 수정 전 13 샘플 연속 부재, 수정 후 0 샘플.
    `is_nested_probe()` 는 import 시점이 아니라 호출 시점에 환경을 읽는다 —
    모듈 상수면 이 가드를 소스 읽기로만 확인할 수 있고, 실제로 첫 판본의 테스트는
    실패할 수 없었다 (바깥 세션이 이미 `.active` 를 치워둔 상태라 가드가 있으나
    없으나 같은 결과였다). 그래서 테스트는 backup 전역이 아니라 **lock 획득
    여부**를 본다.
  - 프로브 argv 는 `-o addopts=` 를 쓴다. 이 저장소 addopts 가 `-n`/`--dist` 를
    싣기 때문에 `-p no:xdist` 로 끄면 모든 프로브가 pytest exit 4 가 되고,
    검사기가 자기 argv 실수를 노트 탓으로 보고한다. 실제로 첫 구현이 그랬다.
    tmpdir 합성 스위트에는 inifile 이 없어 이 버그를 잡지 못하므로,
    `test_the_probe_survives_this_repo_s_own_addopts` 가 실제 트리에서 돈다.
  - 프로브의 격리는 `tests/test_qa_knowledge_shape.py` 와 `tests/conftest.py`
    **두 파일에 나뉘어** 있다. 한쪽만 되돌리면 조용히 깨지므로 양쪽 모두
    `invalidated_by_paths` 에 있다. conftest 쪽 가드 지점은 **셋**이고
    (`install_trees_lose_no_files`, `pytest_sessionstart`,
    `pytest_sessionfinish`), 셋을 각각 따로 지웠을 때 모두
    `test_conftest_honours_the_nested_probe_flag` 가 red 가 되는 것을 확인했다.
    처음에는 세션 훅 둘만 덮여 있었고 fixture 쪽은 되돌려도 스위트가 green 이었다 —
    리뷰가 그걸 잡았다. 셋 중 둘만 덮고 "가드를 확인했다"고 쓰는 게 이 문서가
    막으려는 바로 그 형태다.
- `tests/test_setup_verify_report_probe.py` — 스킬이 manifest 에서 프로브를
  유도하도록 지시하는지(문장 언급이 아니라 지시를 매칭한다), 그리고 선언된
  명령의 truncated 프로브가 실제로 실행 가능한지 확인한다.
- `handle_task_verify` 응답의 `watcher_status`, 그리고
  `tests/test_harness_mcp_server.py` 의 exact key-set 검사와
  `test_task_verify_reports_the_watcher_status_it_gated_on`.

## 강제하지 않는 것, 그리고 이유

자유서술 환경 주장 일반에 대한 검증기는 만들지 않았다. "uv 가 PATH 에 없다"
같은 문장은 기계가 반증할 수 있는 형태가 아니고, 그런 검증기를 흉내 내면
검증하지 않으면서 검증한 척하는 표면이 하나 더 늘 뿐이다. 이 문서의 표 첫 두
줄은 정정과 SUPERSEDED 마커로 처리했고, 강제는 실제로 판정 가능한 부류에만
걸었다.

실패 단어를 `red` 로 좁힌 대가도 기록해 둔다. `known failure`, `known test
failure` 같은 표현은 평범한 영어이고 — "두 번 돌리면 known failure mode 가
있다" 는 경계 대상을 서술하는 것이지 건너뛰기를 허가하는 게 아니다 — node id 를
요구하기 시작한 뒤로는 그런 오탐이 스위트를 red 로 만든다. 가드가 시끄러우면
결국 약화되거나 삭제되므로, 실제 세 건이 모두 쓰는 `RED` 라는 은어만 잡는다.
따라서 `pre-existing failure in tests/x.py::y` 처럼 쓴 미래의 노트는 잡히지
않는다. 이건 알고 받아들인 구멍이다.

리뷰가 이 선택을 측정해 줬다 (2026-09-18): adjacency 규칙이 있는 상태에서 실패
단어를 `red|failing|failure` 로 다시 넓혀도 현재 파일에서 **추가 매치는 0건**이다.
즉 오탐 방지라는 명분은 오늘의 파일에서는 값이 0이고, 구멍 쪽에만 비용이 있다.
그래도 좁은 쪽을 유지하는 이유는 이 파일이 QA append 로 계속 자라고, `known
failure mode` 류 표현이 들어오는 순간 오탐이 곧 스위트 red 가 되기 때문이다 —
시끄러운 가드는 약화되거나 삭제된다는 게 이 저장소가 이미 겪은 일이다. 나중에
구멍을 닫기로 한다면 adjacency + 넓은 실패 단어 조합이 오늘 기준 안전하다는
측정치가 여기 있다.

parametrize suffix 는 node id 의 일부로 취급한다. `[param-1]` 을 버리고 기본
node 를 돌리면 모든 variant 가 실행되어, 무관한 variant 의 실패가 특정 case 에
대한 낡은 주장을 대신 변호하게 된다.

`python3 -m pytest --version` 이 이 호스트에서 실패한다는 사실 자체도 테스트로
고정하지 않았다. 평범한 셸에서는 실패하지만 pytest 의 서브프로세스로 돌면 venv
를 상속해 0 으로 끝난다. 그 단언은 호스트가 아니라 러너의 환경을 기록하게 되고,
누가 돌리느냐에 따라 반대 결과를 보고하는 가드는 없느니만 못하다.

## 다음 독자를 위한 노트

이 태스크의 첫 구현은 "실패를 언급한 노트" 전부를 known-red 주장으로 잡았고,
곧바로 실제 파일에서 intermittent `PermissionError` 를 서술한 노트를 걸어
그 노트가 지명한 (멀쩡히 통과하는) 테스트들이 red 여야 한다고 요구했다.
검사기가 스스로의 과잉 범위를 즉시 드러낸 셈인데, 그게 가능했던 건 검사가
실제 파일을 대상으로 돌았기 때문이다. 합성 입력만 봤다면 통과했을 것이다.

그리고 이 태스크는 **자기 논지를 자기가 위반했다.** AC4 가 `task_verify` 에
`watcher_status` 를 실으면서, 같은 `QA_KNOWLEDGE.yaml` 의 두 항목 아래에 살아
있던 "task_verify does NOT return watcher_status. The tri-state is observable
there only through next_action" 를 거짓으로 만들었는데, 그걸 그대로 두고
close 하려 했다. QA 렌즈가 MCP stdio 경로로 직접 확인해 잡았다. 요구사항 1이
막으려는 바로 그 형태 — 자신을 반증한 변경보다 오래 살아남아 다음 렌즈를
잘못 이끄는 append-only 주장 — 이고, 하필 그 파일을 청소하던 태스크가
새 거짓 주장을 그 파일에 집어넣은 것이다.

여기서 나오는 규칙: **AC3 의 기계 강제는 known-red 부류만 덮는다. 나머지 절반은
사람 몫이고, 그 절반은 실제로 빠졌다.** 코드 표면을 바꾸는 태스크는 close 전에
"이 diff 가 QA_KNOWLEDGE 에 기록된 것 중 무엇을 거짓으로 만들었는가" 를 명시적으로
grep 해야 한다. 자동화 후보는 QA 렌즈가 남겼다: `qa_notes` 항목별 선택적
`invalidated_by_paths` + `note_freshness.py` 연동. 그러면 
`plugin/mcp/harness_server.py` 를 건드린 순간 그 줄이 표시됐을 것이다.

한 가지 더, 규모에 대해: 이 태스크는 리뷰 6라운드를 돌았고 그중 다섯 라운드에서
"문서가 강제한다고 주장하는데 실제로는 실행되지 않는 지점" 이 새로 발견됐다.
같은 버그의 재발이 아니라 **매번 다른 인스턴스**였고, 세 번은 직전 라운드의
*수정*이 만들어낸 것이었다. 정규식을 좁히자 AC 문구가 코드보다 넓어졌고,
non-vacuity 검사를 subset 으로 바꾸자 REQ 가 "3건 모두" 를 주장하게 됐고,
conftest 가드를 셋으로 늘리자 테스트는 둘만 덮었다. 강제와 그 강제에 대한
서술은 항상 같은 커밋에서 같이 움직여야 하며, "고쳤다" 는 사실 자체가 새로운
불일치의 가장 흔한 원인이다.
