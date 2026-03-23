# harness 실행 계획서 — Shared Compiled Memory Index + Agentic Retrieval

> 대상: Claude Code / maintainers  
> 상태: 실행 가능한 구현 계획  
> 원칙: **plugin/runtime 먼저 → dogfood fixture 다음 → setup/template 마지막**

## 1. 목표

harness의 기존 repo-local memory 체계를 유지하면서, 검색 정확도와 stale fact 방지를 크게 올리기 위해 다음 구조를 도입한다.

- **System of record는 그대로 유지**한다.
  - `harness/docs/*`
  - `harness/state/*`
  - `harness/policies/*`
- 그 위에 **모든 사용자가 함께 pull해서 쓸 수 있는 committed shared compiled memory index**를 추가한다.
  - `harness/memory-index/`
- 현재 브랜치의 미커밋 변경, 세션 임시 상태는 **local overlay**로 분리한다.
  - `.harness-cache/memory-overlay/`
- retrieval은 **vector DB/embeddings 없이** deterministic prefilter + optional agentic fan-out으로 처리한다.

핵심 효과:

1. 새 세션/새 사용자가 들어와도 같은 memory advantage를 즉시 공유한다.
2. 오래된 결정이 최신 사실을 가리는 문제를 줄인다.
3. direct response는 빠르게 유지하고, memory-sensitive 질의만 무겁게 처리한다.

---

## 2. 현재 저장소 기준으로 고정해야 하는 전제

이 저장소에서는:

- shipped runtime source of truth는 `plugin/` 이다.
- 루트 `harness/` 는 dogfood fixture다.
- setup로 생성되는 제어 평면은 `plugin/skills/setup/templates/harness/` 아래 템플릿이 기준이다.

따라서 구현 순서는 반드시 아래를 따른다.

1. `plugin/` 런타임 동작 정의를 먼저 수정
2. 루트 `harness/` dogfood fixture 반영
3. `plugin/skills/setup/templates/harness/` mirror 반영

이 순서를 어기면 runtime / dogfood / template 간 drift가 생긴다.

---

## 3. 설계 결정

### 3.1 shared compiled memory는 **커밋한다**

새로 추가할 경로:

```text
harness/memory-index/
```

이 디렉토리는 git에 커밋한다. 다른 사용자는 pull만 해도 동일한 retrieval 이점을 누린다.

### 3.2 committed index는 **LLM이 아니라 deterministic compiler가 생성**한다

중요:

- committed artifact를 LLM observer가 직접 쓰면 diff churn이 커지고 재현성이 무너진다.
- 따라서 **공유 인덱스 생성은 deterministic script compiler가 담당**한다.
- agentic retrieval은 **읽기 단계**에서만 사용한다.

즉:

- durable docs/state/policies = 사람이 읽는 source of truth
- `harness/memory-index/` = script가 재생성하는 compiled artifact
- agentic fan-out = query 시 ephemeral reasoning only

### 3.3 local overlay는 계속 비커밋

새로 ignore할 경로:

```text
.harness-cache/
```

여기에는 아래만 둔다.

- dirty worktree overlay
- query-specific memory pack
- per-session scratch
- experimental fan-out intermediate results

### 3.4 setup templates에는 **generated index 전체를 정적으로 넣지 않는다**

이건 매우 중요하다.

- dogfood repo의 `harness/memory-index/*` 는 커밋한다.
- 그러나 setup templates에는 generated shard 전체를 그대로 미러링하지 않는다.
- template에는 **scaffold + scripts**만 넣고,
- `/harness:setup` 마지막 단계에서 **설치된 실제 repo의 harness docs/state/policies를 기준으로 index를 생성**한다.

즉, template은 “빈 골격 + 빌더”를 제공하고, 실제 프로젝트에 설치될 때 처음 index를 빌드한다.

---

## 4. 최종 아키텍처

```text
harness/docs/* + harness/state/* + harness/policies/*
  -> deterministic compiler
  -> harness/memory-index/ (committed shared compiled memory)
  -> query prefilter (deterministic, no embeddings)
  -> optional agentic fan-out (facts / context / timeline)
  -> single memory pack
  -> harness-orchestrator

uncommitted changes + session state
  -> .harness-cache/memory-overlay/
  -> merged at query time before final pack assembly
```

---

## 5. 디렉토리 목표 상태

```text
harness/
  docs/
  policies/
  state/
  memory-index/
    README.md
    VERSION
    manifest.json
    source-shards/
      docs/
      policies/
      state/
    active/
      by-subject/
      by-domain/
      by-path/
    timeline/
  scripts/
    build-memory-index.sh
    build-memory-index.py
    check-memory-index.sh
    query-memory.sh
    query-memory.py
```

로컬 전용:

```text
.harness-cache/
  memory-overlay/
    manifest.json
    records.jsonl
```

---

## 6. compiled index 데이터 계약

## 6.1 공통 규칙

- 동일 입력이면 동일 출력이어야 한다.
- key ordering은 고정한다.
- records는 stable sort한다.
- generated timestamp는 넣지 않는다.
- commit hash 같은 churn 유발 값은 넣지 않는다.
- record id는 stable hash 기반으로 만든다.
- index는 항상 **source에서 재생성 가능**해야 한다.

## 6.2 record schema

각 record는 아래 필드를 가진다.

```json
{
  "id": "mem:constraint:approvals.source_of_truth:8d8b71d2",
  "kind": "constraint",
  "subject_key": "approvals.source_of_truth",
  "statement": "harness/policies/approvals.yaml is the enforcement gate source of truth.",
  "index_status": "active",
  "authority": "confirmed",
  "source_status": null,
  "scope": {
    "paths": ["harness/policies/approvals.yaml"],
    "domains": ["approval-gates"],
    "api_surfaces": []
  },
  "provenance": {
    "source_path": "harness/docs/architecture/README.md",
    "source_section": "Key Patterns",
    "locator": "## Key Patterns",
    "source_type": "doc"
  },
  "temporal": {
    "documented_at": "2026-03-22",
    "effective_at": "2026-03-22",
    "last_verified_at": "2026-03-22"
  },
  "relations": {
    "supersedes": [],
    "extends": [],
    "resolves": [],
    "conflicts_with": []
  },
  "tags": ["architecture", "approval-gate"]
}
```

### 필드 의미

- `kind`
  - `constraint`
  - `decision`
  - `approval_rule`
  - `observed_fact`
  - `runbook_note`
  - `requirement`
  - `hypothesis`
  - `open_question`
- `index_status`
  - `active`
  - `superseded`
  - `resolved`
- `authority`
  - `hypothesis`
  - `observed`
  - `confirmed`
  - `enforced`
- `source_status`
  - REQ/ADR 원문 상태가 있을 때만 사용 (`accepted`, `implemented`, `verified` 등)

## 6.3 source-shards 규칙

- `source-shards/` 아래에는 **source path를 반영하는 디렉토리 구조**를 만든다.
- 예:

```text
harness/memory-index/source-shards/docs/constraints/project-constraints.json
harness/memory-index/source-shards/docs/decisions/ADR-0001-harness-bootstrap.json
harness/memory-index/source-shards/policies/approvals.json
harness/memory-index/source-shards/state/recent-decisions.json
harness/memory-index/source-shards/state/unknowns.json
```

한 shard에는 해당 source file에서 파생된 records만 담는다.

## 6.4 active / timeline indexes

- `active/by-subject/<subject>.json`
- `active/by-domain/<domain>.json`
- `active/by-path/<path-key>.json`
- `timeline/<subject>.json`

규칙:

- `active/*` 는 기본 조회 경로다.
- `timeline/*` 는 temporal query 또는 conflict resolution에만 강하게 사용한다.
- `superseded` record는 기본 query에서 제외하고, temporal trigger가 있을 때만 올린다.

---

## 7. source별 파싱 규칙

## 7.1 반드시 컴파일 대상에 포함

- `harness/docs/constraints/project-constraints.md`
- `harness/docs/decisions/ADR-*.md`
- `harness/docs/requirements/REQ-*.md`
- `harness/docs/architecture/*.md`
- `harness/docs/runbooks/*.md`
- `harness/policies/approvals.yaml`
- `harness/state/recent-decisions.md`
- `harness/state/unknowns.md`

## 7.2 v1 파싱 규칙

### constraints
- section 아래 bullet/numbered rule을 `constraint` 로 파싱
- confirmed rule만 compiled active index에 올린다

### ADR
- 기본 1 record = ADR의 핵심 decision
- `Status:` 또는 본문에 `superseded by ADR-XXXX` 가 있으면 relation 생성
- 결과는 `decision` record로 저장

### REQ
- 기본 1 record = requirement summary
- acceptance criteria는 record 내부 metadata 또는 별도 field로 보존 가능
- 상태는 `source_status` 에 저장

### approvals.yaml
- `always_ask_before` 각 rule마다 `approval_rule` record 생성
- `ask_when.* = true` 도 개별 `approval_rule` record로 생성

### architecture / runbooks
- heading 아래 bullet 항목 중심으로 파싱
- `confirmed | inferred | hypothesis` 표기 규칙이 있으면 authority에 반영

### recent-decisions.md
- 각 line entry를 recent context record로 생성
- authority는 높지 않게 둔다
- canonical durable memory보다 낮은 우선순위로 취급한다

### unknowns.md
- active 항목은 `hypothesis` 또는 `open_question`
- resolved 항목은 `resolved` relation 또는 inactive record로 남긴다

---

## 8. retrieval 동작 계약

## 8.1 fast path

기본 조회 흐름:

1. `harness/memory-index/active/*` 조회
2. `.harness-cache/memory-overlay/` 있으면 overlay merge
3. top candidates 선정
4. top N source file만 원문 검증용으로 연다
5. orchestrator가 답변 또는 workflow context 구성

## 8.2 heavy retrieval trigger

아래 중 하나면 heavy path를 켠다.

- query에 temporal/change 표현이 있음
  - `latest`, `current`, `changed`, `still`, `now`, `before`, `after`, `superseded`
- 같은 `subject_key` 에 active 후보가 2개 이상 충돌
- 관련 unknown이 존재
- retrieval 후보 수가 많아 conflict resolution이 필요함
- 질문이 “왜 이 결정을 했는가 / 지금도 유효한가 / 이전과 뭐가 달라졌는가” 류임

## 8.3 heavy path

v1.5 이후 optional:

- `memory-search-facts`
- `memory-search-context`
- `memory-search-timeline`

세 agent가 compiled index + top raw sources를 읽고,
aggregator가 **하나의 authoritative memory pack** 을 만든다.

중요:

- 이 pack은 **커밋하지 않는다**.
- reasoning traces도 커밋하지 않는다.

---

## 9. Claude Code 작업 규칙

Claude Code는 아래 규칙을 지킨다.

1. 한 번에 **하나의 work package / 하나의 PR** 만 처리한다.
2. generated index file을 수동 편집하지 않는다. 항상 build script로 재생성한다.
3. merge conflict는 source docs/state/policies를 해결한 뒤 index를 재생성해서 푼다.
4. direct response latency를 악화시키는 무조건 fan-out retrieval을 넣지 않는다.
5. shared compiled index는 deterministic compiler가 만들고, agentic logic은 query 단계에만 둔다.
6. template mirror는 **runtime과 dogfood가 안정화된 뒤에만** 건드린다.
7. setup template에는 generated shard 전체를 정적으로 넣지 않는다.
8. `approvals.yaml` 외에 두 번째 approval source of truth를 만들지 않는다.
9. durable source of truth를 `memory-index/` 로 바꾸지 않는다.
10. source files가 바뀌면 `harness/scripts/check-memory-index.sh` 가 stale 상태를 잡아내야 한다.

---

## 10. 작업 순서 (PR 단위)

## PR 1 — Shared compiled memory foundation

### 목표

committed shared compiled memory index의 스키마, scaffold, deterministic build/check 체계를 만든다.

### 수정 파일

- `.gitignore`
- `harness/manifest.yaml`
- `harness/policies/memory-policy.yaml`
- `plugin/skills/repo-memory-policy/SKILL.md`
- `harness/docs/index.md`
- `harness/docs/architecture/README.md`
- `harness/docs/runbooks/development.md`
- `harness/memory-index/README.md` (new)
- `harness/memory-index/VERSION` (new)
- `harness/scripts/build-memory-index.sh` (new)
- `harness/scripts/build-memory-index.py` (new)
- `harness/scripts/check-memory-index.sh` (new)

### 해야 할 일

- `.gitignore` 에 `.harness-cache/` 추가
- `manifest.yaml > memory` 섹션에 아래 개념 추가
  - `shared_compiled_index: harness/memory-index/`
  - `local_overlay: .harness-cache/memory-overlay/`
- `memory-policy.yaml` 에 retrieval trigger / overlay / active-vs-superseded 규칙 추가
- `repo-memory-policy` skill에 “durable source 변경 후 compiled index 재생성” 규칙 명시
- `build-memory-index.py` 작성
  - source files 파싱
  - records 생성
  - source-shards 생성
  - active/timeline index 생성
- `build-memory-index.sh` 는 thin wrapper로 `python3` 실행
- `check-memory-index.sh` 는 temp dir에 재빌드 후 committed output과 diff
- 현재 dogfood `harness/` 기준으로 index 한 번 생성 후 커밋

### 수용 기준

- `bash harness/scripts/build-memory-index.sh` 실행 시 `harness/memory-index/` 가 deterministic 하게 생성된다.
- 두 번 연속 실행해도 git diff가 생기지 않는다.
- source file 하나를 바꾸면 `bash harness/scripts/check-memory-index.sh` 가 stale 상태를 감지한다.
- `harness/memory-index/README.md` 에 source of truth와 generated artifact의 관계가 명시된다.

---

## PR 2 — Deterministic query prefilter + orchestrator fast path

### 목표

orchestrator가 raw docs를 무작정 열지 말고 shared compiled index를 먼저 활용하게 만든다.

### 수정 파일

- `plugin/agents/harness-orchestrator.md`
- `plugin/CLAUDE.md`
- `plugin/scripts/session-context.sh`
- `harness/scripts/query-memory.sh` (new)
- `harness/scripts/query-memory.py` (new)
- `harness/docs/index.md`
- `harness/docs/runbooks/development.md`

### 해야 할 일

- `query-memory.py` 작성
  - 입력: `--query`, `--paths`, `--domains`, `--top`, `--include-overlay`
  - 출력: JSON 또는 stable markdown summary
  - scoring 규칙:
    - exact path match 우선
    - same domain boost
    - subject token overlap boost
    - authority가 높은 record 우선
    - `superseded` 는 기본 제외
- `query-memory.sh` thin wrapper 작성
- orchestrator Step 2 수정
  - direct_response / workflow 둘 다 우선 `query-memory.sh` 사용
  - top raw sources만 verification 용도로 연다
  - index가 없거나 손상되었으면 기존 raw fallback 사용
- `session-context.sh` 에 아래 추가
  - memory index 존재 여부
  - active record count 요약
  - overlay 존재 여부

### 수용 기준

- direct question에서 raw docs 전체를 읽기 전에 `query-memory.sh` 결과를 우선 사용한다.
- index가 없을 때도 기존 fallback로 동작한다.
- `session-context.sh` 출력에서 memory index 상태를 확인할 수 있다.

---

## PR 3 — Memory sync integration + stale prevention

### 목표

durable memory가 바뀐 뒤 compiled index가 항상 따라오도록 workflow를 연결한다.

### 수정 파일

- `plugin/skills/docs-sync/SKILL.md`
- `plugin/skills/decision-capture/SKILL.md`
- `plugin/skills/repo-memory-policy/SKILL.md`
- `plugin/skills/feature-workflow/SKILL.md`
- `plugin/skills/bugfix-workflow/SKILL.md`
- `plugin/skills/refactor-workflow/SKILL.md`
- 필요 시 `plugin/agents/docs-scribe.md`

### 해야 할 일

- durable docs/state/policies를 수정한 workflow는 마지막에 아래를 실행하도록 명시
  1. docs/state/policies 업데이트
  2. `bash harness/scripts/build-memory-index.sh`
  3. `bash harness/scripts/check-memory-index.sh`
- docs-sync에 “generated artifact 수동 편집 금지” 규칙 추가
- decision-capture에 `supersedes`, `resolves`, `conflicts_with` relation 기록 규칙 추가
- feature/bugfix/refactor workflow의 knowledge sync 단계에 index refresh 명시

### 수용 기준

- durable memory를 바꾸는 workflow가 끝나면 compiled index도 최신 상태다.
- stale index를 남긴 채 완료했다고 주장할 수 없다.
- conflict / supersession 정보가 source doc에 기록되면 timeline index에도 반영된다.

---

## PR 4 — Local overlay for dirty worktree

### 목표

커밋되지 않은 현재 작업도 retrieval에서 놓치지 않도록 local overlay를 추가한다.

### 수정 파일

- `.gitignore`
- `harness/scripts/build-memory-index.py`
- `harness/scripts/query-memory.py`
- `plugin/agents/harness-orchestrator.md`
- `plugin/scripts/session-context.sh`

### 해야 할 일

- `.harness-cache/memory-overlay/` 스키마 정의
- dirty worktree 또는 gitignored session files (`current-task.yaml`, `last-session-summary.md`) 에서 overlay records 생성
- query 시 overlay records를 shared compiled index 위에 merge
- overlay가 같은 `subject_key` 를 덮으면 overlay 우선
- overlay는 커밋 대상에서 제외

### 수용 기준

- 커밋 전 변경이 retrieval에 반영된다.
- clean worktree에서는 overlay가 없어도 정상 동작한다.
- overlay는 git status에 잡히지 않는다.

---

## PR 5 — Validation + dogfood docs + repo-level scripts

### 목표

dogfood repo 자체가 새 구조를 공식적으로 설명하고 검증하도록 만든다.

### 수정 파일

- `harness/scripts/validate.sh`
- `plugin/skills/validate/SKILL.md`
- `scripts/check-dogfood-sync.sh`
- `harness/docs/index.md`
- `harness/docs/architecture/README.md`
- `harness/docs/runbooks/development.md`
- `PROJECT_PLAN.md`
- 필요 시 `README.md`

### 해야 할 일

- `validate.sh` 에 memory index check step 추가
- `validate` skill의 required files 목록에 아래 추가
  - `harness/memory-index/README.md`
  - `harness/memory-index/VERSION`
  - `harness/scripts/build-memory-index.sh`
  - `harness/scripts/check-memory-index.sh`
  - `harness/scripts/query-memory.sh`
- `docs/index.md` 에 `harness/memory-index/README.md` 와 새 scripts 반영
- `architecture/README.md` 에 shared compiled memory + local overlay + temporal relations 설명
- `runbooks/development.md` 에 build/check/query usage 기록
- `PROJECT_PLAN.md` 의 memory model 설명을 업데이트
- `check-dogfood-sync.sh` 에 static mirror 대상 추가
  - 단, generated shard 전체는 비교 대상에서 제외

### 수용 기준

- `bash harness/scripts/validate.sh` 가 memory index 일관성까지 검사한다.
- dogfood docs만 읽어도 새 memory architecture를 이해할 수 있다.
- sync check가 새 static mirror files를 추적한다.

---

## PR 6 — setup/template rollout

### 목표

새로운 harness setup이 설치 직후 shared compiled memory index를 갖도록 만든다.

### 수정 파일

- `plugin/skills/setup/SKILL.md`
- `plugin/skills/setup/templates/harness/policies/memory-policy.yaml`
- `plugin/skills/setup/templates/harness/manifest.yaml`
- `plugin/skills/setup/templates/harness/docs/index.md`
- `plugin/skills/setup/templates/harness/docs/architecture/README.md`
- `plugin/skills/setup/templates/harness/docs/runbooks/development.md`
- `plugin/skills/setup/templates/harness/scripts/build-memory-index.sh` (new)
- `plugin/skills/setup/templates/harness/scripts/build-memory-index.py` (new)
- `plugin/skills/setup/templates/harness/scripts/check-memory-index.sh` (new)
- `plugin/skills/setup/templates/harness/scripts/query-memory.sh` (new)
- `plugin/skills/setup/templates/harness/scripts/query-memory.py` (new)
- `plugin/skills/setup/templates/harness/memory-index/README.md` (new)
- `plugin/skills/setup/templates/harness/memory-index/VERSION` (new)

### 해야 할 일

- setup가 scaffold files를 설치하도록 변경
- setup 마지막 단계에 아래를 수행
  1. templated harness files 생성
  2. `bash harness/scripts/build-memory-index.sh`
  3. `bash harness/scripts/check-memory-index.sh`
- `docs/index.md` 생성 로직에 memory-index root 파일과 새 scripts 포함
- template에는 generated shard 전체를 정적으로 넣지 않음

### 수용 기준

- 새 repo에서 `/harness:setup` 직후 `harness/memory-index/` 가 생성된다.
- 새 repo에서 `/harness:validate` 통과 가능하다.
- template의 static scaffold와 dogfood static scaffold 사이에 drift가 없다.

---

## PR 7 — Optional agentic fan-out for temporal/conflict queries

### 목표

deterministic prefilter 위에 ASMR 스타일의 가벼운 agentic retrieval fan-out을 추가한다.

### 수정 파일

- `plugin/agents/memory-search-facts.md` (new)
- `plugin/agents/memory-search-context.md` (new)
- `plugin/agents/memory-search-timeline.md` (new)
- `plugin/agents/harness-orchestrator.md`
- 필요 시 `plugin/CLAUDE.md`

### 해야 할 일

- heavy trigger일 때만 3 search agents를 fan-out
- facts: direct facts / explicit statements
- context: related rules / nearby decisions / implied constraints
- timeline: latest-valid fact / supersession / resolution tracing
- aggregator는 final authoritative memory pack 하나만 만든다

### 수용 기준

- 일반 질의는 기존 fast path를 유지한다.
- temporal/conflict 질의에서만 specialist fan-out이 동작한다.
- final answer에는 superseded fact와 current fact가 섞이지 않는다.

---

## 11. 필수 정책 변경

## 11.1 `harness/policies/memory-policy.yaml`

아래 성격의 필드를 추가한다.

```yaml
retrieval:
  mode: hybrid
  shared_compiled_index_root: harness/memory-index
  local_overlay_root: .harness-cache/memory-overlay
  prefer_index_first: true
  include_raw_source_verification: true
  max_pack_items: 12
  raw_verify_top_n: 4
  prefer_status_order:
    - active
    - resolved
    - superseded
  prefer_authority_order:
    - enforced
    - confirmed
    - observed
    - hypothesis
  heavy_triggers:
    temporal_terms:
      - latest
      - current
      - changed
      - still
      - now
      - before
      - after
      - superseded
    conflict_candidate_threshold: 2
    related_unknowns_trigger: true
```

## 11.2 `repo-memory-policy` skill

반드시 아래를 추가한다.

- compiled index는 generated artifact이며 source of truth가 아니다
- durable docs/state/policies 수정 후 index 재생성
- index manual edit 금지
- superseded fact는 삭제하지 말고 relation으로 남김
- resolved unknown은 단순 삭제보다 resolution link 우선

---

## 12. validate / CI 계약

최소 검증 명령:

```bash
bash harness/scripts/build-memory-index.sh
bash harness/scripts/check-memory-index.sh
bash harness/scripts/query-memory.sh --query "approval source of truth"
bash harness/scripts/validate.sh
```

필수 보장:

- build는 idempotent 해야 한다.
- check는 stale output을 검출해야 한다.
- query는 index missing 시 fallback reason을 명확히 보여줘야 한다.
- validate는 memory-index drift를 포함해 검사해야 한다.

---

## 13. 문서화 기준

반드시 문서에 남겨야 할 것:

- shared compiled memory의 목적
- local overlay의 목적
- source of truth와 generated artifact의 차이
- generated shard를 수동 편집하면 안 되는 이유
- supersedes / resolves / conflicts_with 관계 의미
- setup가 initial index를 생성한다는 사실
- template에 generated shard 전체를 정적으로 들고 가지 않는 이유

---

## 14. 비목표

이번 작업에서 하지 않는다.

- vector database 도입
- embeddings 도입
- committed index를 LLM output으로 직접 생성
- query pack / reasoning trace 커밋
- durable source of truth를 index로 교체
- trivial question마다 fan-out retrieval 수행
- template에 dogfood generated index 전체 복사

---

## 15. 완료 정의

다음이 모두 만족되면 이 계획은 완료로 본다.

1. `harness/memory-index/` 가 committed shared compiled memory로 동작한다.
2. `check-memory-index.sh` 가 stale index를 잡는다.
3. orchestrator가 index-first retrieval을 사용한다.
4. dirty worktree는 local overlay로 반영된다.
5. docs-sync / decision-capture / feature / bugfix / refactor workflow 뒤에 index refresh가 따라온다.
6. dogfood docs와 validate가 새 구조를 설명하고 검사한다.
7. `/harness:setup` 으로 새 repo에 scaffold + initial index가 설치된다.
8. 일반 질의 latency를 해치지 않는다.

---

## 16. Claude Code에게 바로 줄 실행 지시문

아래 지시문을 Claude Code에 그대로 줘도 된다.

```text
Read this plan and implement only PR 1 first.
Do not touch templates yet.
Do not add vector DBs or embeddings.
Do not make the committed memory index LLM-generated.
Use deterministic scripts for compiled memory artifacts.
After code changes, regenerate the dogfood harness/memory-index and verify it is idempotent.
Run build-memory-index, check-memory-index, and validate before claiming done.
```

