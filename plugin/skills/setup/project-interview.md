# Project interview (office-hours style)

Six forcing questions at install time. Captures WHY before configuring HOW.
Runs once — either during `setup` Phase 2.0, or re-invoked by `maintain` when
the project character has drifted (re-anchor).

## When to skip

- User passed `--skip-interview`.
- `doc/common/CLAUDE.md` already has a non-empty `summary:` field AND
  `doc/harness/manifest.yaml` exists (upgrade/rerun case). In that case,
  `maintain` may re-open this interview when drift is suspected.
- `MAINTENANCE` marker in task dir (maintenance-only install).

## Voice

Direct, conversational. One question at a time via `AskUserQuestion` —
never bundle all six (bundling gets shallow answers). Record each answer
before asking the next.

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

### Q2 — Audience

```
AskUserQuestion:
  Question: "이 프로젝트를 쓰는 사람/시스템과 이 repo를 만지는 사람은?"
  Context: "쓰는 사람 (end-user)과 개발자가 다르면 둘 다 적어주세요."
  Options:
    - A) End-user + developer 구분해서 입력
    - B) 개인 프로젝트 (내가 쓰고 내가 고침)
    - C) 내부 도구 (팀만 사용)
    - D) 공개 라이브러리/SaaS
```

**Maps to:** `doc/harness/manifest.yaml` `audience:` (신규 필드).
Design-review 스킬의 default persona 판단에 사용.

### Q3 — Status quo workflow

```
AskUserQuestion:
  Question: "하네스 없이 지금까지 변경은 어떻게 진행됐나요?"
  Options:
    - A) 단독 작업, 작은 변경 바로 커밋 (light)
    - B) 플랜 문서 쓰고, 리뷰 받고, 머지 (standard)
    - C) 크로스 루트 영향 큰 변경이 잦음 (sprinted)
    - D) 기타 — free text
```

**Maps to:** `doc/harness/manifest.yaml` `execution_mode_default:`.
light → 기본 maintenance 많음. sprinted → 리뷰 강제 많음.

### Q4 — Narrowest wedge

```
AskUserQuestion:
  Question: "이번에 하네스를 받아서 당장 도움받고 싶은 가장 작은 범위는?"
  Context: "전부 다 받을 필요 없습니다. 작게 시작해서 확장 가능."
  Options:
    - A) 태스크 트래킹만 (TASK_STATE, 훅 최소)
    - B) 태스크 + 플랜 강제 (plan-first rule 적용)
    - C) 풀 루프 (plan→develop→verify→close + 자동 리뷰)
    - D) 유지보수 모드 (maintenance_default: true — 가벼운 가드만)
```

**Maps to:**
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
- `manifest.yaml` `browser_qa_supported: true` (C 선택 시)
- E 선택 시: `CONTRACTS.local.md` C-102 — "verify 규율 없음, 하네스가 강제" 경고성 규약

### Q6 — Failure mode to avoid

```
AskUserQuestion:
  Question: "어떤 상황이 벌어지면 하네스를 지우고 싶어질까요?"
  Context: "이 답은 프로젝트 전용 규약으로 저장됩니다. 하네스가 이런 상황을
            만들지 않도록 스스로를 제약."
  Options:
    - A) 답변 입력 (free text — '느려진다', '커밋이 너무 번거롭다' 등)
```

**Maps to:** `CONTRACTS.local.md` C-100 — 최상위 실패 회피 규약.

템플릿:
```markdown
### C-100
**Title:** <Q6 답변 한 줄>
**When:** 사용자가 하네스 설치 시 이 조건을 회피 요청함.
**Enforced by:** 정기 `maintain` 스킬 실행 시 이 규약 위반 여부 감지.
**On violation:** AskUserQuestion으로 "하네스 재조정 필요"를 제안.
**Why:** 사용자 신뢰가 최우선 제약 (C-15 재강조).
```

## After all 6 answers

### Step 1 — Write answers atomically

Before any permanent file write, dump all six answers to
`doc/harness/.interview-answers.json` (tmp). This is the single
authoritative record. If the setup crashes mid-apply, this file lets
`maintain` replay the config without re-asking the user.

**Schema (v1):**
```json
{
  "schema_version": 1,
  "interviewed_at": "<ISO8601>",
  "harness_version": "<from doc/harness/.version>",
  "answers": {
    "q1_purpose":    { "value": "<str|null>", "skipped": false },
    "q2_audience":   { "value": "<A|B|C|D|null>", "value_detail": "<str|null>", "skipped": false },
    "q3_status_quo": { "value": "<A|B|C|D|null>", "value_detail": "<str|null>", "skipped": false },
    "q4_wedge":      { "value": "<A|B|C|D|null>", "skipped": false },
    "q5_verify":     { "value": "<A|B|C|D|E|null>", "verify_commands": [], "skipped": false },
    "q6_avoid":      { "value": "<str|null>", "skipped": false }
  }
}
```

`schema_version` bump on breaking changes — `maintain` refuses to apply
unknown versions and prompts user.

### Step 2 — Apply to target files

In this order (each uses Edit/Write with the appropriate gate):

1. `doc/common/CLAUDE.md` — insert `summary:` (Q1) if missing
2. `doc/common/REQ__project__primary-goals.md` — seed with Q1 + Q2
3. `doc/harness/manifest.yaml` — set `audience`, `execution_mode_default`,
   `maintenance_default`, `verify_commands`, `browser_qa_supported` per
   Q2-Q5
4. `CONTRACTS.local.md` — append C-100 (Q6) and, if needed, C-101/C-102

### Step 3 — Record in AUDIT_TRAIL

Append to `doc/harness/AUDIT_TRAIL.md` (create if missing):

```markdown
## <ISO timestamp> — project-interview

Q1 (purpose): <answer>
Q2 (audience): <answer>
Q3 (status quo): <answer>
Q4 (wedge): <answer>
Q5 (verify today): <answer>
Q6 (failure to avoid): <answer>
```

### Step 4 — Log re-interview trigger for maintain

```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","type":"operational","source":"project-interview","key":"initial-interview-done","insight":"wedge=<Q4>, verify=<Q5 short>","task":"setup"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

## Re-interview flow (maintain-invoked)

When `maintain` detects signals that project character has drifted (e.g.,
`summary:` doesn't match recent commits, or user explicitly asks), it
re-reads `.interview-answers.json`, shows the prior answers, and asks
which questions to re-answer. Only the changed answers update their
target files — others stay put.

## Safety invariants

- Never overwrite an existing `doc/common/CLAUDE.md` body. Insert only
  into empty `summary:` or append new sections.
- `CONTRACTS.local.md` inserts are always additive — never modify
  existing C-## entries. If C-100 already exists (prior interview), use
  C-103+ for new failure-mode contracts.
- Every manifest write goes through Edit on specific fields, never a
  bulk Write that could clobber other keys.
- If user skips a question, record `null` in `.interview-answers.json`
  and apply nothing for that question — do NOT guess a default.
