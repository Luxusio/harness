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
Never ask Q2-Q4; apply their fixed defaults silently.

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
  기본값은 `standard`; 명시적 one-shot no-plan 작업만 `micro`를 사용한다.
  compact/full planning은 persisted execution mode가 아니라 plan 절차 선택이다.
- Q4 (Wedge) →
  - `manifest.yaml` `maintenance_default:`
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
- E 선택 시에도 별도 prose contract를 만들지 않는다. Harness의 기존 verification
  gate가 적용되며, executable configuration은 manifest에만 둔다.

## After the interview and fixed defaults

### Step 1 — Write answers atomically

Before any permanent file write, dump all five answers to
`doc/harness/.interview-answers.json` (tmp). This is the single
authoritative record. If the setup crashes mid-apply, this file lets a later
setup or active harness task replay the config without re-asking the user.

**Schema (v1):**
```json
{
  "schema_version": 1,
  "interviewed_at": "<ISO8601>",
  "manifest_version": "<from doc/harness/manifest.yaml version>",
  "answers": {
    "q1_purpose":    { "value": "<str|null>", "skipped": false },
    "q2_audience":   { "value": "D", "value_detail": "public library/SaaS", "skipped": false, "source": "setup_default" },
    "q3_status_quo": { "value": "B", "value_detail": "standard plan-review-merge", "skipped": false, "source": "setup_default" },
    "q4_wedge":      { "value": "C", "skipped": false, "source": "setup_default" },
    "q5_verify":     { "value": "<A|B|C|D|E|null>", "verify_commands": [], "skipped": false }
  }
}
```

Older interview snapshots may contain `harness_release_version` or
`harness_version` as a legacy release-string field. Ignore both on replay;
they have no equivalent since the manifest carries a single integer `version`.

`schema_version` bump on breaking changes — setup/continuous maintenance refuses
to apply unknown versions and prompts user.
Existing v1 records may contain the retired `q6_avoid` answer; ignore that
extra field when replaying them.

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
### Step 3 — Durable project memory

Do not append a full interview transcript. Persist only the durable outcomes:
`doc/common/REQ__project__primary-goals.md`, `doc/common/CLAUDE.md`,
and `doc/harness/manifest.yaml`. Do not create a contract overlay or another
interview-derived prose state file.

### Step 4 — Log re-interview trigger for continuous maintenance

```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","type":"operational","source":"project-interview","key":"initial-interview-done","insight":"wedge=<Q4>, verify=<Q5 short>","task":"setup"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

## Re-interview flow (continuous maintenance)

When project character drifts, re-open only Q1 and Q5. Reapply Q2-Q4
from the fixed setup defaults without presenting them as questions.

## Safety invariants

- Never overwrite an existing `doc/common/CLAUDE.md` body. Insert only
  into empty `summary:` or append new sections.
- Every manifest write goes through Edit on specific fields, never a
  bulk Write that could clobber other keys.
- If the user skips Q1 or Q5, record `null` for that question. Never replace
  the fixed Q2-Q4 defaults with `null`.
