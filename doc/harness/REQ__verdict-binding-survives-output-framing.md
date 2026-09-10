---
tags: [harness, receipts, verdict, lenses, qa, claude-runtime]
summary: 런타임이 자기 스캐너 공지로 최종 응답을 감싸도 준수한 판정은 바인딩된다. 그 공지 하나만 예외이고 나머지 선행 텍스트는 여전히 판정을 무효화한다. 그리고 바인딩에 실패한 completion 은 기록 시점에 스스로를 알린다.
updated: 2026-09-10
freshness: current
invalidated_by_paths:
  - plugin/scripts/_lib.py
  - plugin/scripts/subagent_lifecycle.py
  - tests/test_verdict_binding_survives_output_framing.py
---

# REQ — verdict binding survives output framing

Extends `REQ__lens-verdicts-bind-when-the-lens-complied.md`, which owns the
compliant-lens principle and the positional rule this document assumes. Nothing
here restates that rule as a second authority; this REQ only says which framing
around a compliant report is the runtime's and therefore not the lens's fault,
and what must happen when a report binds nothing.

## Expected behavior

1. **A lens is not punished for framing it did not write.** When the Claude Code
   binary rewrites a subagent final by prepending its own output-scanner notice,
   the verdict that lens delivered still binds.
2. **Exactly that framing, and nothing else.** Any other leading content —
   agent prose, a horizontal rule, a quoted or reworded notice — keeps voiding
   the verdict.
3. **A recorded completion that bound no verdict announces itself.** It does so
   at the moment it is recorded, without anyone calling a tool and without
   anyone reading `RECEIPTS.jsonl` by hand, and it names the slot that actually
   failed — the verdict, the counts, or the disagreement between them. A
   correction that describes a different failure than the one that occurred
   sends the lens back to re-deliver the shape it already delivered.
4. **A completion that binds announces nothing.** PASS, FAIL and BLOCKED_ENV are
   the healthy path; a diagnostic that fires there is noise.
5. **The row that failed to bind is diagnosable afterwards.** The stored receipt
   identifies what the binder actually read at the verdict position.

## 관측된 격차 (2026-09-09)

`TASK__suite-guards-the-install-tree` 를 닫는 중에 `qa-cli` 렌즈가 실제로 일을
하고 `VERDICT: PASS` 를 반환했다. 영수증에는 `PENDING` 이 기록됐다. **그 사실을
말한 표면은 하나도 없었고**, 두 단계 뒤 `install_verified.py` 가
"fresh QA PASS after review required" 로 거절하고 나서야 드러났다. 즉 원인에서
두 단계 떨어진 이유로 처음 알게 됐다.

수신한 태스크 알림에 남아 있던 최종 응답의 실제 모양(재구성이 아니라 관측):

```
[harness: subagent output matched instruction-shaped pattern(s): settings-json. …]

---

Verification complete. Working tree restored …

VERDICT: PASS
```

`extract_qa_verdict` 는 `lines[0]` 만 읽으므로 아무것도 바인딩하지 못했다.

### 그 공지는 우리 것이 아니다 — 그리고 이름이 겹친다

`grep -c` 결과: `~/.local/bin/claude` 에 2건, `plugin/` 아래 **0건**. 바이너리
안의 리터럴은
`"[harness: subagent output matched instruction-shaped pattern(s): "` 이고,
sanitize 는 `f"{notice}\n\n{original}"` 로 조립된다 — 공지 한 줄, 빈 줄 한 줄,
그다음이 에이전트가 쓴 원문 그대로다.

그 문자열의 `harness` 는 **런타임 자신의 subagent-output 스캐너 이름**이며 이
플러그인과 무관하다. 세션 내 첫 진단이 하네스 코드를 범인으로 지목한 이유가
정확히 이 이름 충돌이다. 다음 사람이 같은 길로 가지 않도록 적어 둔다.

`plugin/` grep 은 이제 0건이 아니다 — 이 변경이
`_RUNTIME_OUTPUT_NOTICE_PREFIX` 상수를 `plugin/scripts/_lib.py` 에 넣었기
때문이다. 구분해야 할 것은 "우리가 저 문구를 **내보내는가**"이지 "우리 코드에
저 문구가 **있는가**"가 아니다. 확인 방법: `plugin/` 안의 히트가 그 상수
정의와 그것을 읽는 코드뿐이고 어느 것도 그 문자열을 **쓰지** 않는지 보라
(`grep -rn "\[harness: subagent output" plugin/`). 우리 쪽은 매칭만 하고,
방출은 바이너리가 한다.

우리는 그것을 없애거나, 옮기거나, 순서를 바꿀 수 없다. **바인더가 견디는 것이
유일한 선택지다.**

### 공지만 견디는 것으로는 이 사건이 고쳐지지 않았다

판정 앞에 있던 것은 **둘**이었다: 런타임 공지 *와* 에이전트가 쓴 문장(그리고
수평선). 공지만 건너뛰는 규칙은 이 사건을 그대로 남긴다. 그래서 이 REQ 의
무게중심은 (1)이 아니라 (3)이다 — 잃은 PASS 는 렌즈를 다시 돌리면 복구되지만,
아무도 말해주지 않은 것은 복구되지 않는다.

### 위치를 읽는 곳은 하나여야 한다 (2026-09-10, QA 가 잡은 회귀)

첫 구현은 `extract_qa_verdict` 만 앞으로 옮기고
`normalize_receipt_completion` 이 counts 를 `summary_lines[1]` 에서 계속 읽게
두었다. 감싸인 **review** 최종에서 그 줄은 런타임이 넣은 빈 줄이므로
`counts_reported` 가 false 가 되고, **이미 바인딩된 PASS 가 PENDING 으로
강등**됐다. 기록된 행은 스스로를 반박한다 — `FIRST_LINE: VERDICT: PASS` 를
저장하면서 진단은 "판정을 1행에 놓고 다시 보내라"고 말한다. 그대로 따르면
같은 행이 다시 나오므로 **루프가 끝나지 않는다**. 드문 경우도 아니다:
런타임 스캐너의 `settings-json` 패턴은 하네스 settings 코드를 리뷰하는 렌즈의
출력에 구조적으로 걸린다.

그래서 위치를 읽는 모든 지점 — 판정 줄, counts 줄, 그 아래에서 시작하는 충돌
스캔, `_offposition_negative` 의 1행 자기서술 veto, 보존 줄 — 은
`_verdict_aligned_lines` **하나**를 통해 같은 원점을 본다. 좁은 수정(카운트
줄에만 오프셋 적용)도 가능했지만 원점이 둘로 남고, 그 둘을 맞춰 두는 일은
리뷰 두 라운드가 이미 한 번 놓쳤다. 원점이 하나면 이 부류의 실수는 리뷰 대상이
아니라 불가능이 된다.

### PLAN 이 틀렸던 지점

PLAN 의 AC-1 은 공지에 이어지는 빈 줄과 **lone `---` 수평선까지 "공지와 함께
따라온다"** 고 적었다. 바이너리를 읽어 확인한 결과 그건 사실이 아니다. 런타임이
삽입하는 것은 공지 한 줄과 빈 줄 하나뿐이고, 관측된 `---` 는 **에이전트가 쓴
본문의 첫 줄**이었다. 따라서 구현은 공지와 그에 이어지는 빈 줄만 건너뛴다.
`---` 를 건너뛰면 그것은 런타임 프레이밍 수용이 아니라 **에이전트 본문 건너뛰기**,
즉 C-14 가 기대는 위치 권한의 완화가 된다. 어느 쪽이든 관측된 사건의 결과는
동일하게 `PENDING` 이므로(그 뒤에 산문이 또 있었다), 좁은 쪽을 택했다.

## 거부한 선택지 — 임의의 preamble 건너뛰기

"판정 줄이 나올 때까지 앞줄을 건너뛴다"는 규칙은 이 사건을 한 줄로 고친다.
그리고 **어떤 에이전트든 판정을 산문 밑에 묻어 두고도 바인딩시킬 수 있게 만든다.**
그것이 C-14 가 기대는 위조 표면이고, 같은 날 `TASK__verdict-binder-loses-real-results`
가 굳혀 놓은 성질이다. 위치 권한은 "무엇이 바인딩되나"의 유일한 판별자로 남는다.

이 사건은 위치 권한이 틀렸다는 증거가 아니다. **바인딩하지 못한 completion 이
조용해서는 안 된다**는 증거다.

## 알려진 천장

런타임이 공지 문구를 바꾸면 (1)은 조용히 적용을 멈춘다. 결과는 오늘 이전의
동작으로 되돌아가는 것이므로 fail-safe 이지만, 눈에 띄지는 않는다.
`test_the_notice_prefix_is_the_one_the_runtime_emits` 가 설치된 바이너리를 읽을
수 있을 때 그 리터럴을 대조한다 — 읽을 수 없으면 (CI 등) 조용히 통과한다.
그때는 이 문서의 리터럴을 새 것으로 갱신해야 한다.

또 하나: 리터럴 앞부분만 맞춰 보므로, 에이전트가 그 접두사를 **스스로 타이핑**
하면 한 줄의 preamble 을 얻는다. 얻는 것은 그뿐이다 — 판정은 여전히 계산된
위치에 정확히 있어야 하고, 없으면 바인딩되지 않는다.

## Enforcement

주 파일은 `tests/test_verdict_binding_survives_output_framing.py` 이고, 모든
테스트가 필드가 지나간 경로(stop 훅 → `mark_subagent_stop` →
`record_subagent_receipt`)를 지나간다. 아래 mutation 이 지명 테스트를 붉게
만드는 것을 확인했다:

| mutation | 붉어지는 테스트 |
|---|---|
| 판정 줄이 나올 때까지 앞줄을 전부 건너뛰기 | `test_agent_prose_ahead_of_the_verdict_does_not_bind` |
| 공지 수용을 통째로 되돌리기 | `test_a_wrapped_but_compliant_final_still_binds` |
| 리터럴 대신 `[harness:` 접두사만 보기 | `test_a_notice_lookalike_grants_nothing` |
| 기록 시점 breadcrumb 제거 | `test_a_completion_that_bound_no_verdict_is_announced` |
| 모든 completion 에 breadcrumb 남기기 | `test_a_bound_verdict_announces_nothing` |
| 실패한 첫 줄 보존 제거 | `test_the_stored_row_identifies_the_line_that_failed_to_bind` |
| 판정 위치 대신 최초 non-blank 줄 보존 | `test_the_retained_line_is_the_verdict_position_not_the_first_non_blank` |
| 보존 길이 제한 제거 | `test_the_retained_line_is_bounded_and_stays_one_line` |
| 보존 줄을 읽기 시점에 필수로 만들기 | `test_a_receipt_written_before_the_retention_line_still_reads` |
| counts 슬롯을 다시 원본 줄 목록에서 읽기 | `test_a_wrapped_review_final_keeps_its_counts_line`, `test_the_offposition_scan_reads_from_the_same_origin_too` |
| breadcrumb 이 세 부류에 한 가지 원인만 주장하기 | `test_the_breadcrumb_names_the_slot_that_actually_failed` |
| breadcrumb 의 분류·메시지·root 조회를 가드 밖으로 되돌리기 | `test_a_failing_announcement_cannot_destroy_the_row_it_announces` |

**알림은 자기가 가리키는 행을 파괴할 수 없어야 한다.** `log_unbound_completion`
은 `receipt_stream_savepoint` 안에서 불리므로, 그 안 어디서든 예외가 나면
알림만 사라지는 게 아니라 completion 행이 **롤백되고** stop 이
`receipt_pending` 을 반환한다. `_log_gate_error` 는 자기 실패를 삼키지만
분류·메시지 조립·root 조회는 그 앞에서 돌았다. 지금은 본문 전체가 가드 안에
있고, 실패 시 `return` 한다 — 일반 알림으로 degrade 하지 않는 이유는 그것이
이 문서가 방금 제거한 무조건적 거짓 지시("판정이 안 묶였으니 1행에 다시
보내라")를 **가장 변형되기 쉬운 행에서** 되살리기 때문이다.

마지막 행이 중요하다. 보존 줄은 **쓸 때는 항상, 읽을 때는 선택**이다. 필수로
만들면 이미 디스크에 있는 모든 `PENDING` 영수증이 persisted-schema 검사를
통과하지 못하고, `receipt_snapshot` 은 한 줄만 어긋나도 예외를 던지므로
**진행 중인 모든 태스크의 스트림이 오염된다** — 이 REQ 가 없애려는 침묵보다
나쁜 결과다.

## 신호가 나가는 두 경로

두 경로는 **일치해야 한다**. 초기 breadcrumb 은 모든 `PENDING` 에 대해
"판정이 바인딩되지 않았다 — 1행에 판정 블록을 놓고 다시 보내라"를 무조건
적었는데, `VERDICT: PASS` 와 `FIX_NOW=2` 를 함께 낸 리뷰에서는 그것이 거짓이다
(판정은 바인딩됐고 counts 가 모순이었다). 읽기 시점
`nonparsing_completion_note` 는 `shape` 과 `inconsistent` 를 이미 구분하고
있었으므로 두 경로가 서로 다른 말을 하고 있었다. 이 저장소는 진단에 거짓
문장을 쓰지 않는다 — `REQ__gate-does-not-demand-impossible-evidence.md` 가
정확히 그 이유로 존재한다. 지금 breadcrumb 은 방금 기록된 행에서
`_unbound_completion_cause` 로 세 부류(판정 / counts / 둘의 불일치)를 갈라
각각의 교정 지시를 낸다.

기록 시점 breadcrumb 은 `doc/harness/learnings.jsonl` 에 `source` 가
`receipts:verdict-unbound` 인 한 줄로 남는다 —
`background_hook:binding-miss` 와 같은 원장, 같은 모양이다. `source` 로 grep
하라(`REQ__receipt-capability-diagnosis.md` 와 동일한 규칙).

읽기 시점 문장은 기존 `nonparsing_completion_note` 가 계속 소유한다. 그쪽은
`task_verify` / `task_context` 의 `next_action` 으로만 나가므로 **조정자가
그것을 호출해야** 보인다. 2026-09-09 의 조정자는 그 사이에 인스톨러를 먼저
돌렸고, 그래서 아무것도 보지 못했다. 두 경로가 함께 있어야 하는 이유가 그것이다.

## 이 REQ 가 다루지 않는 것

- **위치 권한 자체.** 무엇이 바인딩되는지, 재진술이 바인딩된 판정을 밀어내는지는
  `REQ__lens-verdicts-bind-when-the-lens-complied.md` 가 소유한다.
- **런타임 변경.** 공지의 존재·문구·위치는 우리 통제 밖이다.
- **`task_close` 게이트와 영수증 저장 계약.** 보존 줄 한 개의 추가를 제외하면
  그대로다.
