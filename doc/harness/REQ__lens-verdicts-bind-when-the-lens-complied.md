---
tags: [harness, receipts, verdict, lenses, review, qa]
summary: 렌즈가 자기 agent definition 을 지킨 보고서를 내면 그 판정은 바인딩돼야 한다. 바인딩 실패는 미실행과 구별 가능해야 하고, 그 진단은 수신자가 실제로 할 수 있는 조치를 지시해야 한다.
updated: 2026-09-07
freshness: suspect
invalidated_by_paths:
  - plugin/scripts/_lib.py
  - plugin/agents/code-reviewer.md
  - plugin/agents/security-reviewer.md
  - plugin-codex/agents/code-reviewer.md
  - plugin-codex/agents/security-reviewer.md
  - tests/test_verdict_binding_survives_reruns.py
  - tests/test_receipt_watcher_fail_closed.py
freshness_updated: 2026-09-18T08:10:49Z
---

# REQ — 렌즈가 계약을 지켰으면 판정은 바인딩된다

## Expected behavior

1. **렌즈가 자기 agent definition 을 준수한 보고서를 냈으면 그 판정은
   바인딩된다.** 바인더가 보고서를 거부할 수 있는 근거는 그 보고서의 작성자가
   읽을 수 있는 규칙뿐이다. agent definition 에 없는 규칙으로 강등하면 렌즈는
   준수할 방법이 없고, 재실행은 같은 결과를 무한히 재생산한다.
2. **바인딩 실패는 미실행과 구별 가능해야 한다.** `PENDING` 하나로 "안 돌았다"와
   "돌았는데 못 읽었다"가 겹치면 조정자는 있지도 않은 영수증을 찾는다.
3. **그 진단은 수신자가 실행할 수 있는 조치를 지시해야 한다.** 조정자가 바꿀 수
   없는 것을 바꾸라고 지시하는 진단은 진단이 아니라 루프다.
4. **바인딩된 결과는 바인딩 안 되는 후속 기록에 지워지지 않는다.** 판정을 얻는
   것은 비싸다. 같은 렌즈의 읽을 수 없는 후속 발화가 그것을 무효화해서는 안 된다.

## 관측된 격차 (2026-09-07, 필드 리포트 2건)

서로 다른 프로젝트·세션의 두 리포트가 같은 증상을 냈다: 렌즈는 실제로 돌고
실제로 보고하는데 `RECEIPTS.jsonl` 의 verdict 가 전부 `PENDING`.

리포트 B 실측:

```
verdicts: {'PENDING': 18}
completed by lens: {'review-code': 8, 'review-security': 8, 'qa-cli': 2}
```

`task_close` 는 `runtime_verdict: PASS` 를 요구하고, C-17 이 제시하는 유일한
탈출구(attestation park)는 **선행 review PASS 와 QA PASS 를 전제**한다. PASS 를
바인딩할 수 없으면 **두 출구가 동시에 닫힌다.** 리포터가 정확히 지적했듯이,
남는 수단은 `RECEIPTS.jsonl` 을 손으로 쓰는 것뿐이고 그건 증거 조작이다.
리포트 A 세션은 이 상태에서 완료된 작업을 `BLOCKED_ENV` 로 잘못 기록했다.

### 원인 1 — 준수한 리뷰어의 PASS 가 강등됐다 (지배적)

`code-reviewer.md` / `security-reviewer.md` 가 선언한 계약은 1행 verdict, 2행
`FINDING_COUNTS`, 그리고 *"The counts must match the findings that follow"* 가
전부였다. **PASS 에 대한 카운트 제약은 없었다.** 그런데 바인더는:

```python
if verdict == "PASS" and (fix_now or investigate):
    verdict = "PENDING"
```

`INVESTIGATE` 는 정의상 지금 고칠 것이 아니다. PASS 와 모순되지 않는다. 결함을
성실히 세어 보고한 리뷰어일수록 판정을 잃었고, **그 규칙을 들은 적이 없으므로
재실행해도 같은 출력을 냈다.** 두 리포트의 review 완료 16건 전부 PENDING 은 이
하나로 설명된다.

`FIX_NOW` 는 다르다 — 지금 고쳐야 할 것이 있는데 PASS 는 실제 모순이므로
강등이 옳다. 다만 **그 규칙이 이제 agent definition 에 적혀 있다.** 이것이
원인 1 수정의 절반이다. 나머지 절반 없이 바인더만 고치면 같은 부류가 재발한다.

### 원인 2 — 1행 fullmatch 가 재진술을 전부 버렸다

`^VERDICT: (PASS|FAIL|BLOCKED_ENV)$` 는 리포트 B 가 transcript 에서 관측한
`VERDICT: PASS — report complete.` 를 매치하지 않는다.

리포트 A 의 `No action. **PASS**.` 는 **이 수정으로 고쳐지지 않는다.** 그것은
`VERDICT: ` 로 시작하지조차 않으므로 1행 완화 후에도 여전히 `""` 를 반환한다.
그건 계약을 지킨 보고서가 아니라 계약을 아예 따르지 않은 보고서이고, 이 REQ 의
Expected behavior (1) 이 보호하는 대상이 아니다. 그 경우의 올바른 결과는
`shape` 진단이며, 지금은 그 진단이 조정자가 할 수 있는 조치를 지시한다.

렌즈가 **한 번 제대로 보고한 뒤 다시 호출되면** 짧은 재진술로 답한다. 이건
게으름이 아니라 후속 턴의 자연스러운 출력이다. 꼬리 텍스트는 그 줄이 이미 명명한
판정에 대한 주석이므로 바인딩을 막을 이유가 없다.

완화는 **1행에만** 적용한다. 2행 이후의 충돌 스캔에 같은 완화를 적용하면
`VERDICT: FAIL was last round's result` 같은 문장이 보고서를 무효화한다 — bare-only
규칙이 없애려던 자기파괴 리뷰 실패이고, **이 서브시스템을 리뷰하는 보고서가
반드시 쓸 수 있어야 하는 문장**이다.

### 원인 3 — 선택이 검증보다 앞섰다

세 선택자(`_completed_review_by_lens`, `_completed_qa_by_lens`,
`nonparsing_completion_lenses`)가 모두 `latest[lens] = item` 으로 **마지막
레코드를 취한 뒤** 사용 가능한지 물었다. 렌즈가 PASS 를 바인딩한 뒤 재호출되어
읽을 수 없는 재진술을 뱉는 순간 **좋은 영수증이 사라졌다.**

규칙은 "마지막이 이긴다"가 아니라 **"마지막으로 읽힌 것이 이긴다"** 여야 한다.

여기서 `PENDING` 을 일괄로 "못 읽음"으로 취급하면 **이 결함보다 나쁜 것을
만든다.** 리뷰가 측정으로 보여준 경우: 1라운드가 PASS 를 바인딩한 뒤 2라운드
리뷰어가 `VERDICT: FAIL` 과 blocker 를 보고했는데 카운트 일관성 검사에 걸려
`PENDING` 이 되면, 이전 라운드의 PASS 가 살아남아 **살아있는 반대 의견 위에서
태스크가 닫힌다.** 어떤 표면도 그것을 언급하지 않는다.

구분은 영수증의 카운트 슬롯에 기록된다. 값은 셋이다:

| 슬롯 값 | 의미 | 부류 | 선행 판정을 밀어내나 |
|---|---|---|---|
| 실제 카운트 줄 | 모순이 있거나(`verdict` 읽힘) 차단 finding 이 있다(`FIX_NOW>0`) | inconsistent | **그렇다** |
| `UNREADABLE FAIL` / `UNREADABLE BLOCKED_ENV` | 부정 판정을 읽었고 카운트 줄이 없거나 모호 | inconsistent | **그렇다** |
| `UNREADABLE PASS` | 비부정 판정을 읽었고 카운트 줄 없음 = **재진술** | shape | 아니다 |
| `FINDING_COUNTS: INVALID` | verdict 토큰조차 못 읽었다 | shape | 아니다 |

**토큰의 값을 보존해야 한다. 존재 여부로는 부족하다.** 이 구분을 "읽혔는가"로만
두었을 때 `VERDICT: PASS — report complete.` — 이 태스크를 만든 바로 그 필드
관측 문자열 — 이 실질적 부정 판정으로 분류돼 **자기가 재진술하던 PASS 를
축출했다.** 교착이 좁은 형태로 되살아났고, 심지어 계약이 요구하는 최소 형태인
`VERDICT: PASS` 한 줄도 같은 운명이었다.

읽힌 `FAIL` 은 반대다. `VERDICT: FAIL — three blockers found.` 는 카운트 줄이
없어도 사람이 "안 됐다"고 말한 것이므로 이전 PASS 보다 오래 살아야 한다.

QA 렌즈는 카운트 줄을 저장하지 않으므로 항상 `shape` 다. 거기서는 재진술과 새
발견을 가를 데이터가 실제로 없으므로 **밀어내지 않는 것이 옳다** — 밀어내면
교착이 돌아온다. 대신 침묵하지 않는다: 아래 `stale_followup` 참조.

**진행 중인 재실행은 여전히 선행 완료를 억제한다.** 다시 리뷰 중인 렌즈는 아직
보고하지 않았고, 그 이전 PASS 를 현재로 취급하면 리뷰 도중에 태스크가 닫힐 수
있다. 이 구분은 기존 테스트
`test_new_qa_start_invalidates_older_completed_pass` 가 이미 고정하고 있었다 —
바꾼 것은 완료 대 완료 선택뿐이다.

### 원인 3-a — 강등 규칙이 두 벌 있었다 (가장 위험했던 것)

verdict/카운트 일관성 규칙이 `normalize_receipt_completion` 과
`_receipt_entry_semantics_valid` **두 곳에** 복사돼 있었다. 원인 1 을 고칠 때
바인더 쪽만 고쳤더니:

```
binder says: PASS
persisted-schema validator accepts it: False
```

컴플라이언트한 리뷰의 completion 이 `ValueError` 를 내고 **영수증이 아예 기록되지
않는다.** 훅은 C-12 대로 fail-safe 하므로 조용히 사라진다. 읽기 경로에서는
`RuntimeError` 로 해당 태스크의 **모든** 영수증을 오염시킨다.

즉 지배적 원인(필드 18건 중 16건)이 "PENDING 으로 바인딩됨"에서 **"기록이 존재하지
않음"** 으로 악화된다. `nonparsing_completion_lenses` 는 레코드가 없는 렌즈를
지목할 수 없으므로 조정자에게는 **실행되지 않은 렌즈**로 보인다. 이 REQ 의
Expected behavior (2) 가 정확히 뒤집힌다.

전체 스위트가 초록인 채로 살아남았다. AC-1 테스트가 `normalize_receipt_completion`
을 직접 호출했기 때문이다 — writer 를 거쳐 reader 로 나오는 왕복 경로를 아무도
지나가지 않았다. **규칙을 한 곳으로 합치는 것**(`_counts_contradict_verdict`)이
수정이고, 왕복 테스트가 그것을 고정한다.

### 원인 3-c — 위치 권한이 "무엇이 바인딩되나"를 넘어 "부정적이었나"까지 결정했다

verdict 블록이 **한 줄 밀린** 보고서는 토큰과 카운트를 둘 다 잃는다. 카운트를
`summary_lines[1]` 고정 위치에서만 읽기 때문이다. 그래서
`VERDICT: FAIL` + `FIX_NOW=3` 을 담은 리뷰가 `INVALID` → `shape` 로 분류돼
이전 라운드의 PASS 를 밀어내지 못했고, **태스크가 그대로 닫혔다.**

이 형태는 가설이 아니다 — `REQ__lens-verdict-contract-ownership.md` 가
2026-09-02 실제 관측 사례로 기록해 둔 것이다(조정자가 스폰 프롬프트에서 판정
블록을 응답 끝에 두라고 요청한 경우).

**바인딩 권한은 위치 기반으로 유지한다.** 그건 rule 3 이고 load-bearing 이다.
다만 "바인딩 못 한 보고서가 부정적이었나"는 보고서 전체를 훑어 판단한다. 그
신호는 PASS 를 **만들 수 없고** 오래된 PASS 가 부정 판정을 가리는 것만 막으므로,
넓히는 방향이 항상 보수적이다. 모호하면(서로 다른 bare verdict 줄이 공존)
아무것도 취하지 않는다 — 그 모호성을 정리하는 것이 위치 권한의 존재 이유다.

### 원인 3-b — 진단이 identity 실패를 형식 실패로 오진했다

바인딩 실패를 설명하는 진단이 두 부류(shape/inconsistent)만 알았다. 그런데
`started` 영수증과 짝지어지지 않은 completion — 즉 **훅이 시작을 기록하지 못한
경우** — 도 같은 자리로 떨어졌고, "verdict 블록의 형식이 틀렸다"는 문장을 받았다.
그 문장은 **모든 절이 거짓**이며, 읽는 사람을 애초에 문제가 아니었던 보고서 텍스트로
보낸다. 이제 `unpaired` 라는 자기 부류를 갖고, `SubagentStart`/`SubagentStop`
기록 여부를 확인하라고 지시한다.

### 왜 진단이 있었는데도 11라운드가 돌았나

`nonparsing_completion_note` 는 이미 "Recorded but unusable … 이건 안 돌아간
렌즈도, 없는 영수증도 아니다"를 만들어 `next_action` 에 실었다. 즉 **격차 (2)는
이미 충족돼 있었다.** 무너진 것은 (3)이다. 그 문장이 지시한 조치는:

> Rerun that lens so it emits one verdict block in the required position, and do
> not restate, relocate, or paraphrase the verdict format in the spawn prompt —
> the agent definition owns it.

포맷은 agent definition 이 소유하고 조정자는 그것을 지정할 수 없다. **조정자가
다르게 만들 수 없는 재실행을 지시한 것이다.** 게다가 원인 1 의 경우 애초에 포맷
문제가 아니었으므로 방향까지 틀렸다.

지금은 조정자가 실제로 할 수 있는 것을 지시한다: 이미 보고한 에이전트에 메시지를
더 보내는 대신 **새로 스폰**할 것(재진술이 바인딩에 실패한 원인이므로), 그리고
신선한 스폰도 실패하면 그건 세 번째 실행이 아니라 attestation 누락 경로라는 것.
`inconsistent` 분기는 finding 라우팅을 지시하고 `INVESTIGATE`/`OPTIONAL` 이
PASS 와 모순되지 않음을 명시한다.

## 리포터 진단과의 차이 — 기록해 두는 이유

리포트 B 는 원인을 *"충돌하는 verdict 줄들 때문에 바인더가 거부"* 로 특정했다.
**그 가설은 틀렸다.** 해당 규칙은 이미 완화되어 있었고 — 2행 이후는 *다른*
verdict 를 명시한 bare 줄일 때만 무효화 — 동일 반복이나 산문 언급은 무해했다.

같은 증상(전부 PENDING)에서 같은 결론으로 갈 사람이 또 나온다. 증상이 "모든
판정이 안 붙는다"일 때 확인할 순서는 **1행 형태 → 카운트 일관성 규칙 → 선택
순서**이며, 충돌 줄 가설은 마지막이다.

## Enforcement

주 파일은 `tests/test_verdict_binding_survives_reruns.py` 이고, 분류기 관련
행 일부는 `tests/test_receipt_watcher_fail_closed.py` 에 있다. 각 수정에 반례가
짝지어져 있고, 아래 mutation 이 **지명 테스트**를 붉게 만드는 것을 확인했다:

| mutation | 붉어지는 테스트 |
|---|---|
| `(fix_now or investigate)` 로 되돌리기 | `test_pass_binds_beside_investigate_and_optional_findings` |
| `FIX_NOW` 모순 검사 제거 | `test_pass_still_does_not_bind_beside_a_fix_now_finding` |
| 1행 `fullmatch` 로 되돌리기 | `test_a_verdict_line_with_trailing_commentary_binds` |
| 꼬리 구두점 요구를 토큰 경계만으로 되돌리기 | `test_a_hedge_that_continues_the_sentence_does_not_bind` |
| 꼬리 제약을 과하게 조이기 | `test_the_field_measured_restatements_still_bind` |
| 완화형을 충돌 스캔에 적용 | `test_prose_discussing_a_different_verdict_is_still_safe` |
| shape PENDING 이 바인딩된 것을 밀어내게 | `test_an_unbindable_rerun_does_not_evict_the_bound_verdict` |
| inconsistent PENDING 이 밀어내지 못하게 | `test_a_rerun_that_reports_something_negative_always_displaces_the_pass` |
| `UNREADABLE` 을 `INVALID` 로 되돌리기 | `test_a_readable_negative_evicts_even_when_its_counts_line_is_unreadable` |
| 검증기 쪽 강등 규칙만 되돌리기 | `test_a_compliant_pass_survives_the_whole_write_and_read_path` |
| 일관성 규칙의 어느 분기든 변경 | `test_the_verdict_counts_rule_behaves_the_same_at_every_branch` |
| 검증기 쪽 사본만 되돌리기 (= 사본 재도입) | `test_a_compliant_pass_survives_the_whole_write_and_read_path` |
| off-position 부정 판정 스캔 제거 | `test_an_offposition_negative_still_displaces_a_bound_pass` |
| off-position PASS 도 축출하게 | `test_an_offposition_pass_is_recorded_as_unreadable_not_as_a_pass` |
| all-PASS 분기에서 note 버리기 | `test_an_unreadable_followup_to_a_bound_verdict_is_named_not_silent` |
| shape/inconsistent 분류기 반전 | 위 두 개 (`test_inconsistent_verdict_is_not_reported_as_a_format_problem` 는 이 행에 걸리지 않는다) |
| verdict 없는 비차단 보고서가 축출 | `test_a_report_with_no_verdict_line_cannot_evict_on_its_counts_alone` |
| 읽히지 않은 verdict 옆의 카운트를 그대로 보존 | 위 + `test_a_verdictless_report_does_not_evict_a_bound_pass` |
| 스캔이 self-described PASS 를 무시 | `test_a_selfdescribed_pass_stops_the_offposition_scan` |
| 권위 위치(2행) 카운트를 스캔에 태우기 | `test_authoritative_counts_never_depend_on_the_offposition_scan` |
| self-describe 경계를 대소문자 구분으로 | `test_the_selfdescribe_guard_matches_whole_tokens_only` |
| 스캔이 self-described FAIL 까지 물러남 | `test_a_selfdescribed_negative_still_scans` |
| closable 분기에 전체 note 를 싣기 | `test_only_the_advisory_kind_rides_the_closable_branch` |
| pending-bound 분기의 identity 검사 제거 | `test_the_pending_bound_branch_also_names_a_pairing_failure` |
| 워처 무효화를 비차단으로 되돌리기 | `test_the_watcher_emits_a_blocking_invalidation` (형태 테스트는 요약을 직접 구성하므로 이 행에 걸리지 않는다 — 생산자와 형태를 일부러 분리했다) |
| `unpaired` 를 `inconsistent` 로 접기 | `test_an_unpaired_completion_is_not_described_as_a_format_failure` |
| `stale_followup` 침묵시키기 | `test_an_unreadable_followup_to_a_bound_verdict_is_named_not_silent` |
| in-flight 억제 제거 | `test_a_rerun_still_in_flight_suppresses_the_earlier_completion` |
| 최초 바인딩이 이기게 | `test_a_binding_rerun_still_supersedes` |
| presence 기반 축출로 되돌리기 | `test_a_restatement_after_a_bound_pass_leaves_the_verdict_standing` |
| 슬롯에서 토큰 제거 | 위 + `test_the_retained_token_decides_eviction_not_its_presence` |
| 읽힌 FAIL 을 shape 로 강등 | `test_a_readable_negative_evicts_even_when_its_counts_line_is_unreadable` |
| unpaired follow-up 을 다시 오분류 | `test_an_unpaired_followup_names_the_pairing_failure_not_the_followup` |

### 테스트는 필드가 관측한 경로를 지나가야 한다

`test_the_field_measured_restatements_still_bind` 는 이름이 주장하는 속성에
도달하지 못했다. `extract_qa_verdict` 만 호출했는데, **재진술이 유실되는 지점은
거기가 아니다** — 추출은 성공하고 그 뒤 분류·선택 단계에서 사라진다. 그래서
그 문자열들이 자기 판정을 축출하는 회귀가 들어왔는데도 초록이었다.

일반화: **필드에서 관측된 동작의 이름을 단 테스트는 수정에 가장 가까운 함수가
아니라 필드가 지나간 경로를 지나가야 한다.** 이 태스크에서 같은 부류가 네 번
나왔다 — 이것, 아래의 조용히 무력해진 행, `stale_followup` 노트가
`emit_compact_context` 를 지나지 않은 것, 그리고 pending-bound 분기 수정에
테스트가 아예 없던 것.

**두 번째 일반화: 같은 결과에 이르는 두 번째 경로를 추가하면 첫 번째를 지키던
mutation 이 무력해진다.** off-position 스캔을 넣자 `or fix_now` 절이 죽었고, 그
절을 지우는 mutation 이 스캔에 가려 초록이 됐다.

**그런데 그 절을 삭제한 것은 틀린 수정이었다.** 두 경로는 같은 결과에 이르지만
**서로 다른 위치**를 담당한다. 2행 카운트는 계약이 지정한 **권위 위치**이고
`counts_reported` 가 이미 검증했다. 그것을 off-position 스캔에 태우자 스캔의
self-describe 거부권까지 딸려와 판별자가 뒤집혔다 — `VERDICT: PASS` 를 자칭하며
`FIX_NOW=3` 을 보고한 리뷰가 **가려지고**, 아무것도 자칭하지 않은 리뷰는 안
가려졌다. 게다가 표면은 그 기록에 대해 *"that follow-up changes nothing"* 이라고
**적극적으로 거짓을 말했다.**

교훈은 "중복을 없애라"가 아니라 **"위치가 다르면 경로도 다르다"** 이다. 중복을
합칠 때는 합쳐지는 두 경우가 같은 권한을 갖는지 먼저 확인해야 한다. mutation 이
가려지는 문제는 각 경로에 **서로 다른 것을 검사하는** 테스트를 붙여 푼다.

**한 행이 조용히 무력해졌던 적이 있다.** 꼬리 구두점 제약을 넣자
`test_prose_discussing_a_different_verdict_is_still_safe` 가 쓰던 문자열이
완화형 정규식으로도 거부되면서, 충돌 스캔을 완화하는 mutation 이 **전체 스위트
초록인 채로 통과**했다. 지금은 구두점이 붙은 변형(`VERDICT: FAIL — …`)을 함께
단언한다. 수정이 테스트를 무력화할 수 있다는 것 자체가 이 표를 재실행해야 하는
이유다 — 표를 신뢰하지 말고 돌려라.

`test_the_reviewer_definitions_state_the_rule_the_binder_enforces` 는 두 트리
네 파일에서 강등 규칙의 존재를 단언한다. 바인더만 고치고 계약을 안 고치면
원인 1 이 그대로 재발하므로, 이 단언이 수정의 나머지 절반이다.

## 이 수정이 옮긴 판단 — 기록해 두는 이유

`INVESTIGATE` 강등을 제거하면서 **집행 지점이 하나 사라졌다.**
`plugin/skills/develop/quality-audit-pipeline.md` 는
*"`INVESTIGATE`: obtain the missing evidence. It cannot silently become PASS."*
라고 적고 있었고, 그것을 실제로 강제하던 것은 바인더뿐이었다.

이 판단은 리뷰어에게 옮겨졌다 — `BLOCKED_ENV` 라는 형태로. 그 편이 옳은 위치다:
**바인더는 blocking INVESTIGATE 와 non-blocking INVESTIGATE 를 구분할 수 없다.**
카운트만 보고 강등하는 것은 구분이 아니라 일괄 거부였고, 그래서 준수한 보고서를
같이 버렸다.

대가는 명시해 둔다: **이제 리뷰어가 실제 문제를 보고하면서 PASS 를 낼 수 있고,
그것을 하류에서 탐지할 수 없다.** 그건 이제 리뷰어가 틀릴 수 있는 판단이다.
해당 파이프라인 문장은 리뷰어를 소유자로 지목하도록 다시 썼고, 두 리뷰어 정의는
"INVESTIGATE 가 안전한 판정을 막으면 그것이 BLOCKED_ENV 이며 그 판단을 할 수
있는 것은 너뿐"이라고 명시한다. § 0 기준으로 이 규칙은 이제 prose 다 — 숨기지
않고 적어둔다.

## 이 REQ 가 다루지 않는 것

- **닫힌 루프의 출구.** `task_blocked` 가 호출 시점에 증거를 재계산하지 않고
  고정 `ATTESTATION_BLOCKED_REASON` 을 사실로 기록하는 문제는 별도 태스크다.
  이 REQ 는 루프가 닫히는 **원인**을 없앤다.
- **`EVIDENCE_RUN_SUPERSEDED` 의 HEAD 승계**, **capability 경고 노이즈**.
- 리포트 A 의 "문서 편집이 verdict 를 STALE 로 만든다"는 재현되지 않았다.
  `receipt_runtime_verdict` 는 영수증만 입력으로 받으며, C-14 가 소스 fingerprint
  를 명시적으로 배제한다.
