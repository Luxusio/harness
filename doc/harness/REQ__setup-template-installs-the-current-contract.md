---
tags: [harness, contracts, setup, template, lint]
summary: setup이 설치하는 계약은 런타임이 강제하는 계약과 같은 규칙 집합이어야 하고, 그 일치는 기계가 검사해야 한다. 접미사 계약 id는 lint 사각지대였다.
updated: 2026-09-04
freshness: current
invalidated_by_paths:
  - CONTRACTS.md
  - plugin/skills/setup/templates/CONTRACTS.md
  - plugin/scripts/contract_lint.py
  - plugin/skills/setup/bootstrap.md
  - tests/test_contract_lint_real_tree.py
---

# REQ — setup은 런타임이 강제하는 그 계약을 설치한다

## Expected behavior

`plugin/skills/setup/bootstrap.md` 는 `plugin/skills/setup/templates/CONTRACTS.md`
를 새 프로젝트의 `CONTRACTS.md` 로 복사한다. 그 문서는 사용자가 자기 규칙을
읽는 유일한 곳이다. 따라서:

1. **규칙 집합이 같아야 한다.** 루트와 템플릿의 계약 id 집합은 동일하다.
   런타임이 강제하는 규칙 중 사용자 계약서에 없는 것이 있어서는 안 된다.
2. **산문은 달라도 된다.** 루트는 이 저장소의 증거 — 커밋 해시, 세션 날짜,
   줄 번호 — 를 담는다. 템플릿은 일반 규칙만 담는다. 맞춰야 하는 것은
   *규범적 주장*이지 문장이 아니다.
3. **규범적 주장이 서로 모순되면 안 된다.** 템플릿이 런타임 스킬과 반대되는
   지침을 담으면, 사용자는 자기 계약서를 따를수록 하네스와 어긋난다.
4. **이 일치는 기계가 검사한다.** 산문 규약은 § 0 의 정의상 commentary 다.

## 관측된 격차 (2026-09-04)

### 두 계약이 통째로 없었다

```
root:     C-01..C-15, C-14a, C-17, C-18   (18)
template: C-01..C-15, C-18                (16)
```

**새 프로젝트는 turn-end 규칙(C-17)이 적힌 적 없는 계약을 받는데
`stop_gate.py` 는 첫 세션부터 그것을 강제한다.** 사용자가 자기 계약서에 없는
규칙에 막힌다. C-14a(최고 가용 검증 수행) 역시 없었다.

### lint 가 접미사 id 를 못 봤다 — 그래서 루트에서도 검증된 적이 없다

`contract_lint.py` 의 `CONTRACT_HEADING` 은 `^###\s+(C-\d+)\s*$` 였다.
`C-14a` 는 끝의 `a` 때문에 매치되지 않았고, `MATRIX_LINK` 도 같은 사각지대였다.
결과:

- C-14a 에 4-필드 검사, 매트릭스 상호참조, 경로 힌트 검사가 **한 번도 적용되지
  않았다.**
- 루트 § 1 매트릭스에 C-14a 행이 실제로 **없었다.** lint 가 비교의 양쪽에서
  모두 못 보기 때문에 `17 contracts, 17 matrix refs OK` 로 통과했다.
- 정규식을 고치자마자 `18 contracts, 17 matrix refs` 와 함께 그 SOFT 가 처음
  떴다.

**카운트가 두 번 속였다.** 17 대 16 이라 격차가 하나로 보였다 —
`REQ__contract-enforcement-claims-are-executable.md` 가 예측한 그대로다:
숫자만 보고 스코프를 잡으면 C-17 만 고치고 C-14a 빠진 템플릿을 재배포한다.

### 템플릿이 현재 지침과 모순되는 옛 텍스트를 담고 있었다

관리 블록 diff 는 12 hunk, 루트 +101 / −29 줄. 단순 누락이 아니었다:

| 위치 | 템플릿이 사용자에게 말하던 것 | 런타임이 실제로 요구하는 것 |
|---|---|---|
| § 0 | "fewer parallel agents … preferred" | 병렬이 **선호**된다; 직렬이 비싼 경로다 |
| C-13 | "parallel agents = 1 by default" | develop fanout 은 parallel-first |
| C-14 | 영수증은 "written by runtime hooks" | **오직** lifecycle 훅만 쓴다 |
| C-05 | "or lifecycle hook" | "or hook-owned receipt path" |
| C-11 | "setup/continuous maintenance regenerates" | setup 만 재생성한다 |

§ 0 과 C-13 이 특히 나쁘다 — 사용자의 계약서가 병렬을 줄이라고 하는데 develop
스킬은 fanout 을 요구한다. 계약서를 충실히 따를수록 하네스와 어긋난다.

### 템플릿을 lint 하긴 했다 — 그런데 그 방식으로는 볼 수 없는 결함이었다

`plugin/scripts/golden_replay.py::test_contract_lint_template` 은 이 태스크
이전부터 템플릿에 `contract_lint` 를 돌리고 있었다. 그럼에도 드리프트가
누적된 이유:

- **파일별 lint 는 구조상 divergence 를 못 본다.** 매트릭스 검사는 파일을
  자기 자신과 비교하므로, 두 파일이 서로 다른 규칙 집합을 선언한 채로
  각각 clean 하게 통과한다.
- 그 실행은 **exit code 만** 본다. SOFT 는 통과한다.
- pytest 가 수집하지 않는다.
- `tests/test_setup_finalize.py` 는 템플릿을 가짜 저장소로 **복사만** 하고
  읽지 않는다.

즉 "템플릿이 검사되지 않았다"가 아니라 **"검사가 이 결함을 볼 수 있는 형태가
아니었다"** 가 정확한 진술이다. 필요한 것은 두 파일을 **서로** 비교하는 검사다.

## Enforcement

`tests/test_contract_lint_real_tree.py::SetupTemplateShipsTheSameContracts`:

- `test_root_and_template_declare_the_same_contract_ids` — id 집합 동일.
  실패 메시지가 **어느 쪽에만 있는지**를 지목한다.
- `test_the_id_scan_sees_suffixed_contracts` — 양쪽에서 `C-14a` 가 실제로
  스캔된다. 이게 없으면 위 동등성은 **정작 그것을 촉발한 id 들에 대해 공허하게**
  통과한다.
- `test_root_and_template_agree_on_every_contract_title` — Title 은 증거가 아니라
  규범적 주장이므로 유일하게 본문 중 비교 가능한 필드다. 2026-09-04 드리프트
  8건 중 2건(C-13, C-14)이 Title 전용이었고 id 동등성으로는 보이지 않았다.
- `test_the_template_lints_clean_against_this_tree` — 템플릿도 lint 대상이다.
  이름이 "이 트리에 대해"인 이유는 아래 잔여 격차 참조.

mutation 으로 확인: 정규식 두 개 되돌리기, 루트 매트릭스 행 삭제, 템플릿에서
C-17/C-14a 삭제, 그리고 **역방향** — 템플릿에만 계약을 추가 — 전부 지명 테스트를
붉게 만든다.

산문이 아니라 id 를 단언하는 이유는 (2) 다. 루트에는 이 저장소의 증거가 있어야
하고 템플릿에는 없어야 한다. 본문까지 동등성을 요구하면 그 둘 중 하나를 망친다.

## 알려진 잔여 격차 — 이번 스코프 밖

### 항목 (3) 모순 없음은 아직 사람이 지킨다

위 Expected behavior 의 (3)("규범적 주장이 서로 모순되면 안 된다")은 **기계로
검사되지 않는다.** 이번 드리프트 12 hunk 중 8건이 그 부류였고 — § 0 의
"fewer parallel agents", C-13 의 "parallel agents = 1" — 내일 다시 조용히
생길 수 있다.

Title 동등성이 가장 싼 부분 커버리지이고 그 8건 중 2건을 잡는다. 나머지
6건(본문 문장 수준의 반대 지침)은 두 문서의 산문을 결합하지 않고는 기계로
잡기 어렵고, 산문 결합은 (2)를 깨뜨린다.

§ 0 의 정의상 산문만 있는 규칙은 commentary 다. (3)이 현재 그 상태라는 것을
숨기지 않고 적어둔다.

### 경로 힌트는 사용자 저장소에서 해석되지 않는다

신선한 프로젝트에 템플릿을 설치하고 lint 를 돌리면 **SOFT 5개**가 뜬다:
C-01·C-02·C-05·C-11·C-12 가 `plugin/scripts/prewrite_gate.py` 같은 경로를
지목하는데, 사용자 저장소에는 `plugin/` 이 없다(플러그인은 `$CLAUDE_PLUGIN_ROOT`
에 있다).

기존 16개 계약 전부에 해당하는 선행 결함이며 이번 변경이 늘리지 않았다
(변경 전후 모두 정확히 그 다섯). 새로 넣은 C-14a 와 C-17 은 경로 힌트를
의도적으로 쓰지 않고 "the develop workflow", "the Stop gate" 로 적었다 — 사용자
저장소에서 해석되지 않을 경로를 계약서에 넣지 않기 위해서다.

고치려면 계약 본문이 런타임 상대 경로를 지목하는 방식 자체를 바꿔야 하므로
별도 태스크다.
