---
tags: [harness, qa, knowledge, freshness]
summary: qa_notes 항목은 자기 주장이 의존하는 소스 경로를 선언하고, note_freshness.py 가 그 경로를 건드린 diff 에 대해 항목을 suspect 로 뒤집는다. 표시할 뿐 판정하지 않는다.
updated: 2026-09-18
freshness: current
invalidated_by_paths:
  - plugin/scripts/note_freshness.py
  - tests/test_note_freshness_qa_notes.py
  - doc/harness/qa/QA_KNOWLEDGE.yaml
  - plugin/skills/develop/SKILL.md
  - plugin-codex/internal-skills/develop/SKILL.md
---

# REQ — qa_notes 는 자기 무효화 조건을 들고 다닌다

## Context

`doc/harness/qa/QA_KNOWLEDGE.yaml` 은 이 저장소의 소스에 대한 주장을 append-only
로 쌓는다. 그 소스가 움직여도 아무도 주장을 다시 읽지 않는다.

2026-09-18, `TASK__surfaces-teach-the-runnable-command`(66e0fa3) 가
`task_verify` 응답에 `watcher_status` 를 추가하면서 같은 파일의 두 항목 아래에
살아 있던 노트 — "task_verify 는 watcher_status 를 반환하지 않는다" — 를
거짓으로 만들었다. **그 태스크의 목적이 바로 그 파일의 거짓 주장을 청소하는
것이었다.** 모든 AC 의 문구는 충족됐고, QA 렌즈가 읽어서 잡았다. 기계는 아무것도
잡지 못했다.

`doc/**/*.md` 노트는 이미 이 문제를 푼다: frontmatter 에 `invalidated_by_paths`
를 선언하고 `note_freshness.py --paths` 가 매칭되는 노트를 `current -> suspect`
로 뒤집는다. `QA_KNOWLEDGE.yaml` 은 frontmatter 가 없는 mapping 문서라서 그
walk 가 그냥 지나쳤다.

선행 REQ(`REQ__recorded-claims-must-stay-falsifiable.md`)가 남긴 결론이
그대로다: **기계 강제는 known-red 부류만 덮었고, 나머지 절반은 사람 몫이었고, 그
절반이 실제로 빠졌다.** 이 문서는 그 절반 중 자동화 가능한 부분을 가져온다.

## Requirement

1. **주장은 자기 의존 경로를 선언할 수 있다.** `qa_notes` 항목은 선택적으로
   `invalidated_by_paths:` 리스트를 갖는다. 선언 기준은 하나다 — **그 경로를
   편집하면 이 노트의 문장이 거짓이 될 수 있는가.** 장식으로 다는 경로는
   오탐을 만들고, 오탐이 쌓인 가드는 삭제된다.
2. **같은 도구, 같은 의미론.** `note_freshness.py --paths` 가 `doc/**/*.md` 와
   함께 이 파일도 스캔한다. 별도 스크립트도, 별도 플래그도 없다. "내 diff 가
   무엇을 무효화했는가" 에 답하는 표면은 하나여야 한다.
3. **stdlib 로 읽는다.** 이 저장소는 서드파티 의존을 싣지 않고, 이 호스트의
   system python3 에는 PyYAML 이 없다 (`qa_venv_has_no_pip_or_pyyaml`).
   (이 스크립트를 SessionStart 에서 돌리는 훅은 **없다** — C-06 이 명시하듯
   note freshness 는 개발자가 명시적으로 돌리는 검사다. 스크립트 docstring 의
   "safe to run on every SessionStart" 는 안전성 진술이지 실행 진술이 아니다.)
   그래서 리더는 YAML 파싱이 아니라
   `qa_notes:` 블록의 알려진 들여쓰기에 대한 라인 단위 판독이다. 손으로 만든
   리더가 자기가 읽는다고 주장하는 문서와 어긋날 수 있다는 게 실제 위험이므로,
   PyYAML 이 있으면 실제 파일에 대해 두 결과를 대조한다.
4. **쓰기는 문서를 깨뜨리지 않는다.** 이 파일이 실제로 겪은 사고는 항목 하나의
   값이 틀리는 게 아니라 잘못된 append 가 누적된 모든 섹션을 한 번에 읽을 수
   없게 만든 것이다(`tests/test_qa_knowledge_shape.py`). flip 후에도 문서는
   로드 가능해야 하고, 매칭이 없으면 파일은 **아예 다시 쓰이지 않는다** —
   같은 파일에 append 하는 qa-* 에이전트와의 불필요한 경합을 만들지 않는다.
5. **develop 이 실제로 돌린다.** `plugin/skills/develop/SKILL.md` 와 codex 쌍둥이
   의 scope-drift 패스가 이 명령을 이름으로 지시한다. 아무도 돌리지 않는
   메커니즘은 메커니즘이 아니다 — 이 저장소가 반복해서 겪은 실패 형태다.

## Enforcement

- `tests/test_note_freshness_qa_notes.py`
  - `QaNoteReaderTests` — 선언한 항목만 후보가 되고, 블록 스칼라 **본문에 쓰인**
    `freshness:` / `invalidated_by_paths:` 는 필드로 읽히지 않으며, 마지막 항목은
    다음 top-level 키에서 끝난다.
  - `QaNoteFlipTests` — supersession/기존 freshness 면제, 디렉터리 prefix 매칭,
    멱등성, 무매칭 시 바이트 동일성 **및 `_atomic_write` 미호출**, flip 후
    PyYAML 로드와 항목별 동등성.
  - `CliWiringTests` — `main()` 이 실제로 qa_notes 스캔에 도달한다. export 만
    해두고 부르지 않는 경우를 잡는다.
  - `RealFileTests` — 실제 파일의 선언 경로가 **전부 존재**하고, 라인 리더가
    PyYAML 과 같은 결과를 내며, 선언이 최소 15건 남아 있다(경로 존재 검사가
    공허해지는 것을 막는 non-vacuity 가드).
  - `DevelopSkillTests` — 두 develop 스킬이 명령을 이름으로 싣는다.
- 새 분기는 전부 mutation 으로 확인했다(2026-09-18): supersession 가드,
  freshness 가드, 무매칭 early-return, in-place 재작성 2건, `discovered:` 기준
  삽입 위치, 섹션 끝 판정, dedent, 빈 선언 거부, `main()` 배선, 역순 적용,
  섹션 키의 주석 절단 — 12개 전부 RED, 주석만 바꾼 control 은 GREEN.
  in-place 재작성 2건은 **더 가는 입자**로 다시 확인했다: 대입문만 지우고
  `wrote_freshness = True` 를 남기면 항목은 hit 로 보고되면서 실제로는 여전히
  `current` 로 남는다. 리뷰 2라운드가 그 생존자를 잡았고, 이제
  `test_an_existing_freshness_field_is_rewritten_in_place` 가 키 개수가 아니라
  **값**을 본다. 실제 파일의 두 항목이 명시적 `freshness:` 줄을 갖고 있으므로
  이건 죽은 분기가 아니다.

**명령은 install root 기준으로 쓴다.** 첫 판본은 두 스킬 모두에 저장소 상대
경로 `plugin/scripts/note_freshness.py` 를 적었다. 그 경로는 harness 가 설치된
어떤 프로젝트에도 존재하지 않는다 — `install.py` 가 payload 를
`<plugin root>/scripts/` 로 복사하고, `plugin-codex` 에는 `scripts/` 자체가
없다. 리뷰가 잡았다. 테스트가 리터럴을 고정하고 있었으므로 **가드가 이식
불가능한 형태를 고정해 버리는** 상태이기도 했다.

정확히 말하면, 전부가 예외 없이 install-root 형태였던 건 **codex 쌍둥이뿐**이다
(`plugin-codex/internal-skills/develop/SKILL.md` 의 스크립트 호출 6곳 전부
`${HARNESS_PLUGIN_ROOT}`). `plugin/skills/develop/SKILL.md` 는 66e0fa3 시점에
이미 저장소 상대 경로를 섞어 쓰고 있었다 — :48 은 소유권을 가리키는 산문,
:405 는 harness 소스 저장소 전용 Phase 7.8 블록 안이라 실제로 해석되지만,
**:113 의 `plugin/scripts/req_detector.py` 는 그런 한정이 없고 설치된
프로젝트에서 아무것도 가리키지 않는다.** 같은 결함의 미수정 인스턴스이고,
codex 쌍둥이는 같은 자리(:96)에서 `${HARNESS_PLUGIN_ROOT}` 를 쓰므로 twin
divergence 이기도 하다. 이 태스크의 AC 밖이라 손대지 않고 **후속으로 남긴다** —
초안이 "이 줄만 예외" 라는 거짓 전칭으로 그 인스턴스를 가리고 있었다는 게
리뷰 4라운드의 지적이었다.

첫 수정은 테스트를 접두사 없는 `/scripts/note_freshness.py --paths` 로 느슨하게
바꿨는데, 그 문자열은 **깨진 형태 `plugin/scripts/note_freshness.py --paths` 의
부분 문자열**이다. 즉 완화가 자기가 잡으라고 쓰인 결함을 그대로 다시
통과시켰다 — 리뷰 2라운드가 스킬을 원래대로 되돌려도 스위트가 green 인 것으로
확인했다. 지금은 트리별로 install-root 변수를 **명시해서** 고정한다
(`${CLAUDE_PLUGIN_ROOT}` / `${HARNESS_PLUGIN_ROOT}`). 이 저장소에서 반복되는
형태의 또 한 사례다: **직전 라운드의 수정이 다음 결함을 만든다.**

`PYTHONDONTWRITEBYTECODE=1` 접두사는 내가 고른 게 아니라 스위트가 요구했다.
`${*PLUGIN_ROOT}/scripts/` 형태로 바꾸는 순간
`tests/test_bytecode_cache_cannot_disable_receipts.py` 의 스캔 범위에 들어왔고,
문서화된 모든 스크립트 호출은 그 접두사를 달아야 한다.

역순 적용은 처음에 살아남았다(survivor). 한 항목에 줄을 끼우면 그 아래 모든
항목의 span 이 밀리는데, 픽스처가 우연히 "밀린 만큼이 삽입한 줄 수와 같은"
배치여서 문서 순서로 적용해도 결과가 같았다. `discovered:` 줄이 없는 항목을
픽스처에 넣어야 비로소 깨진다 — 그 경우 writer 가 "키 바로 아래" 로 폴백하고,
밀린 창에서 그 위치는 **위 항목의 중간**이다. 커버리지가 우연에 기대고 있었다는
뜻이므로 픽스처를 고쳤다.

## 측정 — 자기 자신에게 돌려본 결과

이 태스크의 diff 에 대해 그대로 실행했다. 6건 표시, 그중 **진짜 2건**, 그리고 재독 과정에서 부수적으로 1건을 더 고쳤다:

| 표시된 노트 | 매칭 경로 | 판정 |
|---|---|---|
| `REQ__recorded-claims-must-stay-falsifiable.md` | `QA_KNOWLEDGE.yaml` | **참** — "자동화 후보" 라고 미래형으로 적힌 문단이 이 태스크로 구현되면서 낡음. 고쳤다. |
| `qa_notes#setup_template_contract_drift_qa` | `note_freshness.py` | **참** — 이 도구가 이제 이 파일도 더럽힌다는 사실이 빠져 있었다. 문장을 보강했다. |
| `qa_notes#contract_lint_mutation_probes` | codex `develop/SKILL.md` | 거짓 양성(이 diff 기준). 다만 재독 중에 "(exactly 500)" 이 66e0fa3 에서 이미 499 인 것을 발견해 함께 고쳤다. |
| `REQ__selective-review-detail.md` | `develop/SKILL.md` | 거짓 양성 |
| `REQ__lens-verdict-contract-ownership.md` | `develop/SKILL.md` | 거짓 양성 |
| `REQ__runtime-surfaces-name-the-actual-blocker.md` | `develop/SKILL.md` | 거짓 양성 |

6건 중 2건이 실제 수정을 요구했고, 3번째 행은 표시 자체는 거짓 양성이지만
재독이 다른 낡은 숫자를 드러냈다. 나머지 3건은 순수 노이즈다.

이 표는 이 문서가 존재하기 **전에** 돌린 결과다. 지금 같은 경로 집합으로 다시
돌리면 7건이 나온다 — 이 REQ 자신이 `note_freshness.py` 를 의존 경로로
선언하므로 스스로를 뒤집는다. 자기 참조는 구조적이고, 나머지 6행은 그대로
재현된다.

거짓 양성의 원인은 **파일 단위 granularity** 다. 500줄짜리 `develop/SKILL.md`
한 곳을 고치면 그 파일에 의존을 선언한 모든 노트가 뒤집힌다. 이건 이미 관측된
불편이다 — `HANDOFF__verdict-binder-session-blocked.md` 가 "이 diff 에 무관한
REQ 3건이 딸려 나온다" 고 적어 뒀다. 그래도 노이즈 쪽을 택한다: 놓친 주장
하나의 비용(다음 세션을 잘못 이끄는 것)이 재독 몇 분보다 크고, 두 건 중 한 건은
실제로 고칠 것이 있었다.

씨앗을 심는 과정 자체가 하나를 더 잡았다. `mcp_bash_guard_false_positive` 가
지명한 `plugin/scripts/mcp_bash_guard.py` 는 5d29f55 에서 **삭제된 스크립트**였다.
경로 존재 검사가 씨앗을 거부해서 다시 읽게 됐고, 그 노트는 존재하지 않는
가드에 대한 우회법을 계속 권하고 있었다. SUPERSEDED 로 표시했다.

## 강제하지 않는 것, 그리고 이유

- **close 게이트가 아니다.** suspect 노트가 남아 있다고 `task_close` 가 거부하지
  않는다. 판정("이 주장이 지금 거짓인가")은 사람 몫이고, working tree 의 diff 를
  아는 테스트는 무관한 이유로 red 가 된다. 표시하는 것까지가 이 도구의 역할이다.
- **`current` 로 되돌린 것은 다음 실행에서 다시 뒤집힌다.** 이 도구에는
  "커밋 X 에서 확인했음" 이라는 기억이 없다. 재독 후 살아남는 해소는 **노트를
  고치거나 SUPERSEDED 로 표시하는 것**이고, `freshness` 를 되돌리는 것은 그
  실행 한 번에 대한 응답일 뿐이다. `doc/**/*.md` 노트의 기존 의미론과 같으며,
  이 태스크가 새로 만든 한계가 아니다.
- **선언은 옵트인이다.** 30개 항목 중 23개가 선언을 갖는다. 나머지 7개 중 4개는
  이미 superseded 이고, 3개(`sandbox_suite_pollution`,
  `qa_venv_has_no_pip_or_pyyaml`, `suite_is_green_and_xdist_stable`)는 호스트
  상태·스위트 합계·방법론에 대한 주장이라 특정 파일이 반증하지 않는다.
  (`preexisting_single_worker_order_dependency` 는 처음에 이 3개와 함께 묶여
  있었다. 리뷰 3라운드가 그 항목의 "이 노트로 재개하지 말 것" 이
  `tests/test_harness_mcp_server.py` 의 reuse 가드에 기대고 있다고 지적했고,
  맞는 지적이라 분류를 바꾸는 대신 **씨앗을 심었다**.) 새로 append 되는 노트가 선언을
  달도록 강제하는 장치는 **없다** — 그걸 강제하려면 "이 주장이 파일에
  의존하는가" 를 기계가 판단해야 하고, 그건 이 문서가 피하려는 종류의 흉내다.
- **`patterns:` / `known_issues:` 등 다른 top-level 섹션은 덮지 않는다.**
  관측된 사고가 `qa_notes` 에서 났고, 나머지 섹션에는 **축적된 주장이 없다** —
  `services`/`selectors`/`test_data`/`known_issues` 는 비어 있고, `patterns:` 는
  출하 시 스캐폴드 기본값 3개(`data_reset`, `auth_flow`,
  `screenshot_evidence`)만 들고 있다. 소스 편집으로 거짓이 될 수 있는 문장이
  거기엔 아직 없다.
- **prefix 매칭은 디렉터리/파일 수준이다.** 심볼이나 함수 단위 의존은 표현할 수
  없다. 위 표의 거짓 양성 3건이 그 대가다.
