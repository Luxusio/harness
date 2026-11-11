# Wiki·컨텍스트 개선 후보와 현행 구현 대조

tags: [inf, proposal, knowledge, context-engineering]
summary: 연구에서 얻은 아이디어를 현재 Harness 코드와 대조하고 작은 측정·변경 순서를 제안한다.
updated: 2026-11-11

**가장 먼저 할 일은 현재 검색·읽기 경로에서 답을 놓치는지, 요약이 근거를 과장하는지, 실제 주입 문맥이 필요한 정보를 남기는지 확인하는 것이다.** 새 저장소·벡터 DB·범용 DAG를 만드는 일은 우선순위가 아니다. 이 문서는 [조사 GUIDE](GUIDE__llm-wiki-context-engineering-research.md)와 [HarnessMM 검토](OBS__harnessmm-2610-32813.md)에서 도출한 **미채택 제안**이다. 사용자 요구·승인된 정책·이미 구현한 기능으로 취급하지 않는다.

대조 기준은 이 저장소 `a6b3ae8a4a87e40a179173b85a4cf7cd457d88a2`다. Kotlin·Python·스킬·관련 테스트를 읽었으며 실제 회사 데이터나 원격 계정으로 검색·성능·권한 실험을 하지 않았다. 아래 코드 링크와 함수 이름은 그 시점의 위치를 가리킨다. 코드에서 조건을 발견한 것과 실제 운영 장애를 재현한 것을 구분한다.

## 이미 있는 것: 다시 만들지 않는다

| 관측한 구현 | 실제 근거 | 보장 범위·남은 구분 |
|---|---|---|
| 원자료·wiki·지침, 사실·가설·제안·결정 구분 | [knowledge 스킬](../../wiki-atlassian/skills/knowledge/SKILL.md), [문서 reviewer](../../wiki-atlassian/agents/document-reviewer.md), [migrate 스킬](../../wiki-atlassian/skills/migrate-docs/SKILL.md) | 이미 지침이 있음. 런타임이 모든 문장의 진실·권한을 판정하는 것은 아님 |
| 제목·본문의 6단계 어휘 검색, Unicode/NFC 처리 | [LocalSearch.kt](../../wiki-atlassian/src/main/kotlin/io/crscube/clitools/knowledge/LocalSearch.kt) `LocalSearchQuery.match` | 알려진 이름·ID·구문에 유용. BM25·형태소·학습된 동의어·의미 인덱스는 별개 |
| excerpt와 이어 읽을 locator, heading 경로 | [LocalCorpus.kt](../../wiki-atlassian/src/main/kotlin/io/crscube/clitools/knowledge/LocalCorpus.kt) `searchHit`, `read` | doc/version/Unicode offset/length/snapshot으로 읽기 연결. 인용의 의미적 지지까지 보장하지 않음 |
| bounded read와 snapshot 일치 검사 | 같은 파일 `snapshot`, `read`, `withManifest` | 본문·Markdown hash 반영, 같은 버전의 내용 변경도 거부. 문자·byte 한도는 모델 token 한도와 다름 |
| 여러 공간의 조회 범위·continuation·불완전성 | [MultiSpaceKnowledge.kt](../../wiki-atlassian/src/main/kotlin/io/crscube/clitools/knowledge/MultiSpaceKnowledge.kt) `search`, `searchCoverage` | snapshot-bound cursor와 공간별 freshness/partial 표시. 첫 페이지 전역 순위화와는 다름 |
| native 버전·접근·쓰기 대상·동시 변경 검사 | [WikiTools.kt](../../wiki-atlassian/src/main/kotlin/io/crscube/clitools/confluence/WikiTools.kt) `basedOn`, `checkSources`, mutation 경로; [ContextMirror.kt](../../wiki-atlassian/src/main/kotlin/io/crscube/clitools/confluence/ContextMirror.kt) `recordRootDenial` | source_changed/CAS/readback/denial 경로 존재. 동기화된 읽기 사본이 원격 권한을 매 순간 재조회하는 것은 아님 |
| 유형 있는 지식 관계·부분 그래프 | 같은 파일 `knowledgeRelations`, `knowledgeGraph` | 현재 문서·권한을 확인하고 잘림을 표시. 전체 논리 의존성·모순 검출 엔진은 아님 |
| 자동 변경 알림 | [wiki_change_notice.py](../../plugin/scripts/wiki_change_notice.py) `render_notice` | ID·버전 중심, 세션 cursor·gap·공간별 한도와 오류 구분. 본문 전체를 매번 주입하지 않음 |
| 로컬·외부 목적지와 오류 구분 | [document_destination.py](../../plugin/scripts/document_destination.py) | 잘못된 외부 설정을 조용히 로컬 쓰기로 바꾸지 않음 |
| 재사용 가능한 검증 대상·입력 무효화 | [verification-gate.md](../../plugin/skills/develop/verification-gate.md), [verify_runner.py](../../plugin/scripts/verify_runner.py) | 관련 입력·환경과 새 실패의 우선순위를 확인하는 경로가 이미 있음. 새 receipt·검증 원장 불필요 |
| 짧은 WBS 요약·실행 로그 참조 기반 | [wbs.py](../../plugin/scripts/wbs.py) `summary`, 기존 검증 실행 경로 | “현재 요약 기능이 없다”는 제안은 중복. 실제 출력의 누락·비용을 봐야 함 |

현재 로컬 조회는 [Main.kt](../../wiki-atlassian/src/main/kotlin/io/crscube/clitools/knowledge/Main.kt) → `MultiSpaceKnowledge` → `WikiService.localReads` → `LocalCorpus.execute`로 연결된다. history/outline/section 등 네이티브 경로도 있다. 내부 `documents::execute` callback은 LLM host의 reasoning callback이 아니다. MCP에 선택 도구를 추가하는 것만으로 HarnessMM의 호출 대체를 얻는다는 전제는 성립하지 않는다.

## 후보별 가장 작은 조치

우선순위는 개발 순서 제안이며 확정 roadmap이 아니다. **P1은 기존 경로의 작은 검증, P2는 그 검증에서 문제를 확인했을 때만 바꾸는 후보**다. 문서 추가 자체를 성능 개선으로 세지 않는다.

### P1-A. 검색부터 답변까지의 실패를 작은 사례로 분리

분류: **기능 일부는 이미 있음 / 실제 품질 문제는 재현 필요**.

현재 어휘 검색의 범위와 순위는 확인됐지만 한국어 질문의 실제 누락률은 측정하지 않았다. 먼저 원문·기대 구간·정답/유보 조건이 있는 작은 합성 문서와 실제 반복 질문의 비식별 변형을 기존 테스트·독립 문서 리뷰에 넣는다. 새 benchmark 서비스나 기억 DB는 만들지 않는다.

필수 사례는 정확한 문서명, 표현이 다른 질문, 두 문서를 연결해야 하는 질문, 같은 이름의 다른 공간 문서, 비슷한 방해 문서, 원문에 답이 없는 질문이다. 질문에 필요한 원문을 못 찾았는지, 찾아 놓고 잘못 읽었는지, 답변이 근거보다 넓어졌는지를 따로 본다. retrieval-only PASS를 제품 답변 PASS로 합산하지 않는다.

채택 조건: 현재 방식과 개선 후보를 같은 문서·질문·예산에서 비교하고 실제 개선된 실패 유형을 제시한다. 어휘 개선이 필요하면 우선 제목·본문 가중치나 명시적 용어 보완을 검토한다. BM25·질의 재표현·형태소 처리는 이득과 의존성·오탐·호출 비용이 확인될 때 선택한다. 벡터 DB는 이번 범위에 포함하지 않는다.

### P1-B. 출처가 있어도 과장된 요약을 잡는다

분류: **출처·상태 지침은 이미 있음 / 의미적 충실도는 별도 검증**.

`basedOn`·snapshot·locator는 근거의 위치와 버전을 연결한다. 그 원문이 실제로 주장을 지지하는지는 별도다. 기존 문서 reviewer의 입력으로 다음 사례를 검증한다: “실험 성공→공식 지원” 과장, AI 제안→사용자 결정 승격, 예외를 빼먹은 요약, 서로 독립적인 근거를 하나의 출처처럼 합치기, 현재 정책과 과거 정책의 충돌.

가장 작은 변경은 기존 작성·리뷰 경로에서 핵심 주장만 원문 범위로 되돌아가 확인하는 예제·검사다. 모든 문장에 새 영구 레코드나 점수를 저장하지 않는다. 중요한 예외·시간·대상·결정 주체를 보존하고, 없으면 유보하도록 평가한다. 인용 링크가 있다는 것만으로 PASS가 되면 개선 실패다.

### P1-C. 관계와 `basedOn` 경계의 회귀를 먼저 확인

분류: **구현 확인 / 테스트 근거 일부 확인 / 커버리지 보완 후보**.

현재 관계 파서는 보이는 지식 연결과 유형 있는 관계를 읽고, 코드·인용·매크로 등의 표현과 구별한다. `knowledgeGraph`는 부분 결과의 `complete=false`를 반환할 수 있다. 관련 native/Python 테스트를 조사했지만 관계 파서·graph·`basedOn`을 직접 단언하는 사례는 찾지 못했다. 이 관측은 기능이 고장났다는 증거가 아니다.

기존 [NativeDocumentContractTest.kt](../../wiki-atlassian/src/test/kotlin/io/crscube/clitools/confluence/NativeDocumentContractTest.kt)에 실제 경로를 통과하는 좁은 사례를 고려한다: 근거 버전 변경으로 쓰기 거부, superseded 관계와 독립 근거 보존, 접근 불가 근거의 내용·메타데이터 비노출, 부분 그래프를 전체 의존성 증명으로 해석하지 않기. 관계가 있다는 이유로 사용자 결정·모든 논리 의존성을 자동 생성하지 않는다.

### P1-D. 실제 주입 문맥의 생존·누락을 확인

분류: **출력 순서·상한은 소스에서 확인 / 작업 방해 정도는 재현 필요**.

[prompt_memory.py](../../plugin/scripts/prompt_memory.py)는 목적지·기억 안내·리뷰·상태·restore·runbook을 조합한 compact 부분에 **400자** 상한을 적용한다. 반면 worktree recovery와 wiki 변경 알림은 따로 붙인다. 따라서 “전체 hook이 400자”나 “변경 알림이 잘린다”는 주장은 틀리다. [runbook_memory.py](../../plugin/scripts/runbook_memory.py)는 자체적으로 1,800자까지 만들 수 있으나 바깥 compact 한도에 다시 들어간다.

먼저 기존 hook fixture에서 실제 출력에 어떤 block이 남는지 확인한다. 필요한 경우 짧은 생략 표시나 목적지·현재 핵심 정보의 우선순위를 기존 renderer 안에서 조정한다. 진단용 원문을 매 prompt에 추가하지 않는다. 채택 조건은 대표적인 긴 상태에서도 필요한 안내·복구 경로를 찾을 수 있고 실제 출력·모델 토큰·후속 조회 비용이 함께 개선되는 것이다. 권한·쓰기 gate 정보를 잘라 토큰을 줄이면 탈락이다.

### P2-A. 여러 공간 첫 페이지의 선택 편향

분류: **구현 동작 확인 / 실제 품질 영향은 재현 필요**.

`MultiSpaceKnowledge.search`는 설정된 공간 순서로 결과를 채워 전역 `limit`에서 멈춘다. 각 공간 내부 순위와 전체 공간을 섞은 순위는 다르다. 뒤 공간의 정확한 제목보다 앞 공간의 약한 일치가 첫 페이지를 채울 수 있다. continuation이 있으므로 뒤 공간 문서가 영구 누락된다는 뜻은 아니다.

앞 공간의 약한 hit와 뒤 공간의 정확한 hit가 있는 고정 사례로 첫 페이지·전체 페이지·명시적 space 조회를 비교한다. 실제 문제가 확인되면 공간별 소량 후보 후 병합·분배를 검토한다. 추가 scan·cursor 복잡도·점수 정규화 비용이 있으며, 명시적 space 사용으로 충분하면 현재 방식 유지가 더 작다. 권한 경계·partial·snapshot 일치는 그대로 보존한다.

### P2-B. 절차 기억의 관련성·유효 범위

분류: **선택 방식 확인 / 오래된 기억의 실질 방해는 재현 필요**.

`runbook_memory.render_prompt_block`은 ID 순 첫 네 개를 고르고 각 gotcha 수를 제한한다. 저장된 `learned_at`·`verified_at`은 이 선택의 관련성·최신성 점수가 아니다. 외부 지식 모드에서는 로컬 runbook 주입을 의도적으로 하지 않는다.

같은 프로젝트에서 무관한 runbook이 먼저 나오거나 폐기된 절차가 재사용되는 사례를 확인한 뒤, 현재 저장하는 description·command·gotchas·source_wbs·시각 정보와 관련 지식 문서를 참고해 선택·재검증하는 방향을 검토한다. runbook 상태에는 명시적인 적용 범위·대체 관계 필드가 없다. 그 저장 지원은 필요성이 확인돼야 하는 별도 제안이며, 기존 문서 참조로 충분하면 상태를 늘리지 않는다. 날짜만으로 유효한 지식을 자동 삭제하거나 별도 점수 원장·백그라운드 큐를 만들지 않는다. 더 많은 기억이 더 나쁜 선택을 만들지 않는지가 판정 기준이다.

### P2-C. 이미 결정된 조회·변환은 다시 추론하지 않기

분류: **논문에서 얻은 적용 가설 / host 차이 검증 필요**.

필요한 ID·snapshot·구간이 확정된 독립 조회와 결정적 변환은 기존 orchestration에서 묶을 수 있다. 미정인 조사·변경·승인·실패 복구는 관측 후 다음 판단을 해야 한다. 이는 새 실행 DAG 도입보다 작은 출발점이다. 현재 host가 실제로 어떤 모델 호출을 줄였는지 측정하지 않았다면 “CCS 적용”이나 “토큰 절감”으로 보고하지 않는다.

비교에는 묶음 준비·도구 정의·결과 전달·오류·fallback까지 포함한다. source snapshot·권한·범위가 달라지면 이전 판단을 재사용하지 않는다. 검증 결과의 재사용은 기존 verification-target 흐름에서 처리하고, 명령 문자열만 같다는 이유로 리뷰·QA를 승격하지 않는다.

## 작은 비교 실험과 채택 조건

새 테스트 플랫폼 대신 기존 Kotlin/Python fixture와 독립적인 문서 검토를 사용한다. 모델 비교가 필요할 때만 그 실행을 별도 승인 범위·비용 안에서 수행한다. 현재 조사에서 그러한 실험을 실행한 것으로 기록하지 않는다.

| 사례 | 같게 둘 조건 | 관측·실패 기준 |
|---|---|---|
| 직접 검색 vs wiki 경유 | 같은 질문·원본·버전·모델·근거 예산 | 필요한 근거 도달, 정확성·예외 보존, 원문 fallback·총 비용 |
| 현재 vs 과거 정책 | 현재/as-of 질문을 분리 | 최신과 과거 구분; 새 요약이 예전 주장을 무조건 삭제하지 않음 |
| 없는 정보 vs 불완전 조회 | 답 없는 문서, 일부 공간 장애 | 미상·부분·없음 구별; 전역 부재를 과장하면 실패 |
| 정정·삭제·권한 회수 | 정의된 동기화 완료 시점, 같은 계정·root | 검색·요약·인용의 stale/노출 처리. 제목·링크 누출도 별도 실패 |
| 악성 본문 | 비신뢰 문서가 지시·승인을 가장 | host가 지시로 승격·무단 공유하지 않음. 텍스트만 보고 보안 완성 판정하지 않음 |
| 같은 이름·다른 공간 | 상충하는 사실·권한과 고정된 조회 의도 | 공간 혼합·첫 페이지 편향·승인 범위 혼동 확인 |
| 짧은 작업·장기 재개·실패 복구 | 같은 목적과 가용 자료 | 압축·추가 도구·병렬화가 복구까지 줄이는지 확인 |

작은 사례의 단계별 결과를 먼저 본다. 대표성이 확보된 비교에서는 task별 짝을 맞추고 반복 실행이 필요한 변동도 분리한다. 질문·모델·prompt·도구·문서·환경·캐시 조건을 기록하며 튜닝하지 않은 변형을 남긴다. 작은 표본을 “통계적 증명”으로 포장하거나 서로 다른 benchmark 수치를 합치지 않는다. 권한 누출·가짜 승인·근거 없는 완료는 평균 점수로 상쇄하지 않는다.

도입 순서는 **P1-A/B의 품질 기준선 → P1-C/D의 실제 경계 확인 → 확인된 병목에 한해 P2 후보 비교**다. 품질을 유지하면서 전체 토큰·시간·수작업이 줄어드는 후보만 채택한다. 기준선에서 문제가 없거나 유지 비용이 이익보다 크면 기능을 추가하지 않는 것도 정상적인 결론이다.

## 보류하는 것과 승인 범위

이번에는 새 벡터 DB·범용 graph/DAG·자가 개선 controller·모든 발언 자동 기억·문장별 영구 claim 원장·새 receipt를 구현하지 않는다. 로컬·외부 목적지의 정본 분리, 계정·사이트·공간·root 경계, 실제 독립 리뷰 후 QA, 검증 대상 변경 무효화, 부분 실패 보존은 유지한다.

이 문서의 제안은 구현 결함을 모두 확정한 목록이 아니다. 후속 개발에서는 선택한 작은 후보만 같은 실제 경로에서 확인하고 승인된 WBS에 반영한다. 조사 문서를 작성한 것과 제품 동작·비용을 개선한 것을 구분해 보고한다.
