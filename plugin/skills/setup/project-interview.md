# Lightweight project interview

Two useful questions at install time, plus fixed operating defaults. Captures
project purpose and verification facts without asking users to design Harness.
Runs once during `setup` Phase 2.0, and can be re-opened by an active harness
task when the project character has drifted (re-anchor).

## When to skip

- User passed `--skip-interview`.
- `doc/common/CLAUDE.md` already has a non-empty `summary:` field AND
  `doc/harness/manifest.yaml` exists (upgrade/rerun case). In that case,
  the active/next harness task may re-open this interview when drift is suspected.
- `MAINTENANCE` marker in task dir (maintenance-only install).

## Voice

Direct and brief. Ask Q1 only when the user has not already supplied a project
purpose. Ask Q5 only when verification commands or QA mode cannot be detected.
Never ask Q2-Q4 or Q6; apply their fixed defaults silently.

## Questions

Ask in this order. Each question has a stated **purpose** (for the user's
context) and a **maps to** row (for you — so you know where the answer
lives). Do NOT show "maps to" to the user.

### Q1 — One-sentence project purpose

```
AskUserQuestion:
  Question: "이 프로젝트를 한 문장으로 설명하면? (누가 쓰는 무엇인가)"
  Context: "이 답은 모든 세션의 summary로 사용됩니다."
  Options:
    - A) 답변 입력 (free text)
    - B) 건너뛰기 — 나중에 직접 채움
```

**Maps to:** `doc/common/CLAUDE.md` frontmatter `summary:` field.
Also seeds `doc/common/REQ__project__primary-goals.md` first paragraph.

### Q2–Q4 — Fixed operating defaults (never ask)

Record these values as if selected during every setup:

- Q2 audience: `D` — public library/SaaS
- Q3 status quo: `B` — standard plan, review, merge
- Q4 wedge: `C` — full task start → plan → develop → QA → close loop with automatic internal review and verification

**Maps to:**
- Q2 (Audience) → `doc/harness/manifest.yaml` `audience:` (신규 필드).
  Design-review 스킬의 default persona 판단에 사용.
- Q3 (Status quo) → `doc/harness/manifest.yaml` `execution_mode_default:`.
  light → 기본 maintenance 많음. sprinted → 리뷰 강제 많음.
- Q4 (Wedge) →
  - `manifest.yaml` `maintenance_default:`
  - `CONTRACTS.local.md` C-101 — "이 프로젝트에서 활성화된 하네스 범위" 선언
  - 훅 스파서시티(hooks.json 항목 수) 설정에 힌트

### Q5 — Verification today

```
AskUserQuestion:
  Question: "변경이 작동한다는 걸 지금까지 어떻게 확인했나요?"
  Options:
    - A) 자동 테스트 (명령어 입력받기)
    - B) 수동 CLI 실행
    - C) 브라우저에서 직접 확인
    - D) 프로덕션 모니터링 / 사용자 피드백
    - E) 확인 안 함 (코드만 보고 머지)
```

**Maps to:**
- `manifest.yaml` `verify_commands:` (A 선택 시 명령어 배열)
- `manifest.yaml` `qa.browser_qa_supported: true` (C 선택 시)
- E 선택 시: `CONTRACTS.local.md` C-102 — "verify 규율 없음, 하네스가 강제" 경고성 규약

### Q6 — Fixed failure mode (never ask)

Always record this exact answer:

`말하지 않은 범위도 멋대로 수정하는 것`

**Maps to:** `CONTRACTS.local.md` C-100 — 최상위 실패 회피 규약.

템플릿:
```markdown
### C-100
**Title:** 말하지 않은 범위도 멋대로 수정하는 것
**When:** 사용자가 하네스 설치 시 이 조건을 회피 요청함.
**Enforced by:** SessionStart/close-time continuous maintenance detects this
condition and asks the user before changing project-level rules.
**On violation:** AskUserQuestion으로 "하네스 재조정 필요"를 제안.
**Why:** 사용자 신뢰가 최우선 제약 (C-15 재강조).
```

## After the interview and fixed defaults

### Step 1 — Write answers atomically

Before any permanent file write, dump all six answers to
`doc/harness/.interview-answers.json` (tmp). This is the single
authoritative record. If the setup crashes mid-apply, this file lets a later
setup or active harness task replay the config without re-asking the user.

**Schema (v1):**
```json
{
  "schema_version": 1,
  "interviewed_at": "<ISO8601>",
  "harness_version": "<from doc/harness/.version>",
  "answers": {
    "q1_purpose":    { "value": "<str|null>", "skipped": false },
    "q2_audience":   { "value": "D", "value_detail": "public library/SaaS", "skipped": false, "source": "setup_default" },
    "q3_status_quo": { "value": "B", "value_detail": "standard plan-review-merge", "skipped": false, "source": "setup_default" },
    "q4_wedge":      { "value": "C", "skipped": false, "source": "setup_default" },
    "q5_verify":     { "value": "<A|B|C|D|E|null>", "verify_commands": [], "skipped": false },
    "q6_avoid":      { "value": "말하지 않은 범위도 멋대로 수정하는 것", "skipped": false, "source": "setup_default" }
  }
}
```

`schema_version` bump on breaking changes — setup/continuous maintenance refuses
to apply unknown versions and prompts user.

### Step 2 — Apply to target files after bootstrap

On a fresh setup, keep these edits staged until setup Phase 3 creates the
canonical files. On Repair/Upgrade, the files already exist and targeted edits
may run immediately. Never create a partial manifest before bootstrap.

In this order (each uses Edit/Write with the appropriate gate):

1. `doc/common/CLAUDE.md` — insert `summary:` (Q1) if missing
2. `doc/common/REQ__project__primary-goals.md` — seed with Q1 + Q2
3. `doc/harness/manifest.yaml` — set `audience`, `execution_mode_default`,
   `maintenance_default`, `verify_commands`, `qa.browser_qa_supported` per
   Q2-Q5
4. `CONTRACTS.local.md` — replace the setup-owned C-100 block with the fixed
   Q6 value and apply C-101/C-102 as needed. This setup-owned block is
   intentionally overwritten on rerun; do not append duplicates.

### Step 3 — Durable project memory

Do not append a full interview transcript. Persist only the durable outcomes:
`doc/common/REQ__project__primary-goals.md`, `doc/common/CLAUDE.md`,
`doc/harness/manifest.yaml`, and any CONTRACTS.local.md rules from the answers.

### Step 4 — Log re-interview trigger for continuous maintenance

```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","type":"operational","source":"project-interview","key":"initial-interview-done","insight":"wedge=<Q4>, verify=<Q5 short>","task":"setup"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

## Re-interview flow (continuous maintenance)

When project character drifts, re-open only Q1 and Q5. Reapply Q2-Q4 and Q6
from the fixed setup defaults without presenting them as questions.

## Safety invariants

- Never overwrite an existing `doc/common/CLAUDE.md` body. Insert only
  into empty `summary:` or append new sections.
- The setup-owned `CONTRACTS.local.md` C-100 block is replaced idempotently
  with the fixed Q6 text on every setup run. Other C-## entries remain untouched.
- Every manifest write goes through Edit on specific fields, never a
  bulk Write that could clobber other keys.
- If the user skips Q1 or Q5, record `null` for that question. Never replace
  the fixed Q2-Q4 or Q6 defaults with `null`.
