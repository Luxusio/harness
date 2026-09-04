# HANDOFF — GOAL__6-1-delta-90-130k-2-61d452ed (하네스 개선 6건)

작성 2026-09-04. 세션 종료 시점 상태.

## 완료

| 커밋 | 내용 | 상태 |
|---|---|---|
| `6d0b646` | C-14 `next_action` 단일 소스 | 닫힘 |
| `4b2d317` | 런타임 표면이 실제 blocker 를 가리던 3건 (승인 항목 2·4·5) | 닫힘 |
| `d38faf4` | setup 템플릿 계약 드리프트 (승인 항목 6) | 닫힘 |

`d38faf4` 는 QA 진행 중 사용자 요청으로 커밋됐다. 이후 QA 가 **PASS** 로
돌아왔고 `task_verify` → `install_verified` → `task_close` 를 마쳤다. 커밋
메시지의 "QA lens was still running" 문구는 커밋 시점 기준으로 정확하며,
사후에 PASS 된 사실은 이 문서가 기록한다.

## 즉시 처리할 것 — `d38faf4` 가 남긴 결함

### F2. 정규식 비대칭 (한 글자)

`plugin/scripts/contract_lint.py`:

```
CONTRACT_HEADING = ... (C-\d+[a-z]*) ...      # 소문자만
MATRIX_LINK      = ... (C-\d+[A-Za-z]*) ...   # 대소문자
```

QA 가 `### C-19B` 로 **계약 하나를 통째로 템플릿에 밀어 넣었고 전부 초록이었다.**
`### C-19b` 는 잡힌다. 대문자 접미사 관례가 없어 노출은 낮지만, **이것이 바로 이
태스크가 없애려던 "스캔이 볼 수 없는 heading 형태" 부류다.**

수정: `CONTRACT_HEADING` 을 `[A-Za-z]*` 로. 그리고
`tests/test_contract_lint_real_tree.py` 에 대문자 접미사 mutation 케이스를
추가할 것 — 안 그러면 같은 방식으로 재발한다.

### F1. durable doc 의 틀린 숫자

`doc/harness/REQ__setup-template-installs-the-current-contract.md` 와 해당
PLAN 이 관리 블록 diff 를 "루트 **+101 / −29**" 로 적었다. 실측은
**+100 / −28** 이다. 원인: `diff -u | grep -c '^+'` 가 `+++`/`---` 헤더 줄을
같이 센다. hunk 12 개는 맞다.

숫자가 규범적으로 쓰이지는 않지만, **측정이 반박하는 수치를 durable REQ 가
공표하는 것**은 이 Goal 전체가 없애려는 부류다.

### F3. 선행 결함 (이번 diff 소관 아님)

`plugin/scripts/golden_replay.py` 의 docstring 이 self-test 를 **7개**로
열거하는데 `TESTS` 는 **6개**이고 `--json` 도 `"total": 6` 을 낸다. 이번에
golden_replay 를 증거로 인용했으므로 함께 기록한다.

## 남은 승인 항목 — T3

Goal 의 세 번째 child 는 아직 만들지 않았다. 승인된 6건 중 (1) 과 (3):

**(1) 리뷰 라운드 경제성.** 하네스에 delta 리뷰 개념이 없어 주석 한 줄을
고쳐도 리뷰어가 상수 8개·mutation 13개·산문 표면 6개를 처음부터 재측정한다.
이번 세션 실측: 라운드당 90~130k 토큰, 태스크당 5~12 라운드.

**(3) QA 가 리뷰 대상 파일에 쓴다.** `doc/harness/qa/QA_KNOWLEDGE.yaml` 은 QA
가 쓰는 파일이면서 동시에 리뷰 diff 에 들어간다. PASS 이후 리뷰 표면이 다시
열린다 — `TASK__next-action-single-source` 의 마지막 blocker 두 개가 정확히 그
구조였다.

### T3 설계에 쓸 근거

**줄일 것은 재측정의 범위이지 재측정 자체가 아니다.** 이번 Goal 에서 프로세스
자체에 대해 관측된 것:

- 같은 결함 부류(주장 ↔ 측정 불일치)가 압도적 다수였고, **산문 검토는 한 번도
  못 잡았다. 전부 측정과 mutation 이 잡았다.**
- `TASK__setup-template-contract-drift` 하나에서만 내가 그 부류를 **세 번**
  저질렀다: 이전 REQ 를 거짓으로 만들고 방치, Title 줄바꿈 드리프트, 세 곳에
  반복한 거짓 근거 문장("`contract_lint` 는 루트에만 돈다" — 실제로는
  `golden_replay` 가 `6303b0f6` 부터 돌리고 있었다).
- 따라서 delta 리뷰는 "바뀐 줄만 보기"가 아니라 **"이 변경이 무효화하는 기존
  주장을 찾기"** 여야 한다. 후자가 실제로 결함을 낸 축이다.

## 기타 이월 (QA self-healing 후보)

- `note_freshness.py --paths` 는 이름과 달리 문서를 **수정한다.** QA 가 이걸
  프로브로 돌려 REQ 두 개의 frontmatter 를 건드렸다가 복원했다. `--check`
  모드가 필요하다.
- `contract_lint.py` 가 위치 인자를 거부한다 (`contract_lint.py CONTRACTS.md`
  → exit 2). argparse 두 줄.
- `golden_replay` 는 exit code 만 본다. 현재 SOFT 5개를 내면서 PASS 로
  보고한다. `--strict` 가 있으면 실제 게이트가 된다.
