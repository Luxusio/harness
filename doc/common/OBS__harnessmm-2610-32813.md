# HarnessMM 논문: 토큰 절감, 통계와 적용 범위

tags: [obs, paper, harness, token-efficiency, statistics]
summary: arXiv 2610.32813의 측정 결과·재현 범위와 최초 주장에 대한 선행 연구를 구분한다.
updated: 2026-11-11

사용자가 제시한 논문은 [arXiv 2610.32813](https://arxiv.org/abs/2610.32813)이다. **v1은 2026-10-23, 검토한 v2는 2026-11-06**이다. 초록 페이지 제목은 “HarnessMM: Recursive Self-Improvement for Harness Token Efficiency”, [64쪽 PDF v2](https://arxiv.org/pdf/2610.32813v2)의 제목은 “HarnessMinusMinus: A Technical Report on Efficient Agent Harnesses — Minus Overhead, Minus Cost”다. Yuhua Jiang·Chang Xu·Feifei Gao·Li Zhao의 같은 연구를 가리킨다.

아래 수치는 저자의 측정이다. 우리는 원문·중요 도표의 시각 확인과 공개 자료의 산술 대조를 했으며 모델 실험을 재실행하지 않았다. 해석과 적용 제안은 우리의 분석이다. 전체 조사와 현재 구현 제안은 각각 [GUIDE](GUIDE__llm-wiki-context-engineering-research.md), [INF](INF__wiki-context-improvement-candidates.md)에 있다.

## 논문의 핵심과 실제 통계

**논문 요약:** EDG에 정해진 행동·의존성을 저장하고 GDR이 관측된 조건에서 재사용한다. CCS는 원래 reasoning callback을 실제 대체한다. RSI는 모델 가중치보다 하네스를 바꾼다. 도구 하나를 추가하고 기존 추론도 계속하면 대체 이익이 사라진다. [v2 §3–5, Figure 1, pp.3–6](https://arxiv.org/pdf/2610.32813v2)

HumanEval의 같은 98개 test task, 한 번씩 실행한 두 비교다. **Codex/DeepSeek는 host이며 두 actor 모두 Qwen3.5-4B**다. 다음 설정·분할은 [저자의 재현 안내](https://github.com/YuhuaJiang2002/HarnessMM/blob/1c2666fa98a6c0b474a5639a5d205e3e179325e1/REPRODUCE.md)와 [SDK 설정](https://github.com/YuhuaJiang2002/HarnessMM/blob/1c2666fa98a6c0b474a5639a5d205e3e179325e1/docs/DEEPSEEK_REPRODUCTION.md)에서 교차 확인했다.

| Host | 성공률 전→후 | kTokens/task 전→후 | 절감률 |
|---|---:|---:|---:|
| Codex | 90.82→90.82% | 66.72→47.02 | 29.54% |
| DeepSeek Harness | 71.43→85.71% | 96.28→11.62 | 87.93% |

통합 설정의 결과이며 재사용만의 기여율은 아니다. SDK 비교는 authoring·compaction·fallback·finalization도 포함한다. 공개 안내는 HumanEval 원 측정은 **External**, MBPP aggregate 산술은 **Local**, synthetic mechanism checks는 실험 재현과 별개라고 명시한다. 본 조사에서는 [MBPP aggregate](https://github.com/YuhuaJiang2002/HarnessMM/blob/1c2666fa98a6c0b474a5639a5d205e3e179325e1/reproduction/mbpp_sanitized/aggregate.json)의 `100×(1−2,726,738/13,874,096)=80.34655%`만 재계산했다. 이것은 모델 실행·통계 재현이 아니다.

**논문 ablation:** 별도의 MBPP 257개 test task에 네 조건을 적용한 Table 4–5다. Full의 정확도는 93.77%, 13.17 kTokens/task다. task 단위 paired bootstrap 10,000회, 세 비교에 Bonferroni 보정한 **98⅓% 구간**이다. [v2 Appendix E, pp.24–25](https://arxiv.org/pdf/2610.32813v2)

| Full 대비 대조 조건 | 절감률 % [구간] | 정확도 차이 pp [구간] |
|---|---:|---:|
| 재사용을 single-use로 제한 | 26.91 [4.01,45.57] | +0.78 [−3.50,+5.06] |
| 원래 callback 유지 | 19.52 [−2.07,38.68] | +0.39 [−2.72,+3.50] |
| EDG 실행 제거 | 18.62 [−9.53,39.78] | −1.56 [−5.45,+2.33] |

반복 실행 간 변동까지 측정한 구간은 아니다. 미상 개발 비용과 불완전한 다른 benchmark 기록도 남아 있다. [v2 Appendix E, I–K](https://arxiv.org/pdf/2610.32813v2)

## 통계에서 읽을 수 있는 것 — 우리의 해석

위 표에서 비용 구간이 0을 제외하는 것은 첫 비교다. 그러므로 **모든 구성 요소가 각각 통계적으로 절감을 입증했다**고 쓸 수 없다. 나머지 점 추정치는 유망하더라도, 이 실험의 보정 구간에서는 절감 없음도 포함한다.

정확도 차이 구간이 0을 포함하는 것 역시 **정확도 비열등성 입증**과 다르다. 비열등성 주장은 미리 정한 허용 손실과 그에 맞는 검정이 필요하다. 관측상 같은 성공률, 유의한 차이를 못 찾음, 허용 손실 이내를 입증함은 서로 다른 문장이다. 전체 평균 비용의 절감과 모든 개별 작업의 절감도 구분해야 한다.

또한 task 하나를 한 번씩 실행한 paired 비교의 불확실성과 동일 task를 여러 seed로 반복했을 때의 변동은 다르다. 전자는 해당 task 집합에서의 차이를 판단하는 데 의미가 있지만 후자의 재현성을 대신하지 않는다. 실패·시간 초과·복구까지 포함한 분모와 비용 범위가 같아야 절감률이 비교 가능하다.

SDK 설정의 [설명](https://github.com/YuhuaJiang2002/HarnessMM/blob/1c2666fa98a6c0b474a5639a5d205e3e179325e1/docs/DEEPSEEK_REPRODUCTION.md)은 실행에 필요한 native host·모델 serving·격리 executor·scorer·전체 metering을 요구한다. 예제의 synthetic PASS를 이 조건의 실제 성능 재현으로 승격하면 안 된다. “공개 코드가 있다”와 “누구나 같은 수치를 완전히 재현할 수 있다”도 동일하지 않다.

## “통계적으로 증명한 최초”인가

사용자의 “토큰 절감을 주장하는 하네스 방법론 중 처음으로 통계적으로 증명했다”는 설명을 검토 출발점으로 삼았다. **이 조사로 넓은 의미의 최초는 확인하지 못했다.** 선행 연구와 무엇을 측정했는지 구분하면 다음과 같다.

| 선행 자료 | 먼저 다룬 내용·근거 | 이 논문과의 차이 |
|---|---|---|
| [LLMCompiler](https://proceedings.mlr.press/v235/kim24y.html) · arXiv 2023-12, ICML 2024 | 의존성 기반 함수 호출 병렬화, ReAct 대비 시간·비용 실험 | 통계적 최초를 단독 반증하는 자료로 사용하지 않음. 실행 구조를 바꾸는 효율화 자체는 선행 |
| [The Complexity Trap v3](https://arxiv.org/pdf/2508.21433v3) · 2025-10-27, Appendix C.1 pp.15–16/Table 4 | SWE-bench Verified 500개 task에서 paired bootstrap 10,000회, 비용·해결률 차이의 95% CI와 p값. 일부 설정의 **금액 비용** 구간이 0을 제외 | 이미 하네스 컨텍스트 관리 비용의 통계 분석이 존재. raw-token 지표와 금액 지표, masking과 callback 대체는 다름 |
| [AgentDiet v2](https://arxiv.org/pdf/2509.23586v2) · 2026-03-15, v1 2025-09 | 불필요한 trajectory 입력 축소; reflection·캐시 손실을 포함한 비용 비교 | 절감률 점 추정치만으로 통계적 유의성을 단정하지 않음. 입력 축소와 호출 대체 구분 |
| [AutoSaddler](https://arxiv.org/abs/2608.23041) · 2026-08-24 | 실패 trace를 이용한 하네스 개선·일반화 평가 | 주로 품질 개선 목적. 모든 하네스 최적화를 토큰 절감 연구로 합치지 않음 |
| [HarnessPro v2](https://arxiv.org/pdf/2610.26770v2) · 2026-10-20 | 하네스 자가 개선과 품질·비용 tradeoff, 반복 실행 mean±SD | 표준편차는 paired 검정·신뢰구간과 다름. benchmark·actor가 달라 절감률 직접 순위화 불가 |

Complexity Trap의 일부 설정에는 해결률 하락도 있고 비용 차이가 불명확한 조건도 있다. 표의 `p=0.0000`은 bootstrap 해상도에서의 표기이며 진짜 확률 0을 뜻하지 않는다. 저렴함과 품질 보존을 따로 판단해야 한다. [원 논문 Appendix C](https://arxiv.org/pdf/2508.21433v3)

따라서 현재 안전한 표현은 **“이미 결정된 행동의 재사용과 실제 callback 대체를 다루고, 특정 task-paired ablation에 다중 비교를 보정한 통계 구간까지 보고한 연구”**다. “해당 세부 메커니즘의 raw-token 절감을 최초로 검정했는가”라는 좁은 질문은 별도 우선권 조사 없이 확정하지 않는다. 선행 연구의 존재만으로 서로 다른 지표의 최초를 반박하지도, 관련 논문 몇 개를 못 찾았다고 최초를 입증하지도 않는다.

## Harness에 가져올 판단 기준

다음은 적용 가능성에 대한 우리의 추론이며 구현 승인이나 측정된 제품 개선이 아니다.

1. **이미 결정된 일을 다시 결정하는지 먼저 찾는다.** 읽을 문서 ID·버전·구간이나 고정된 독립 조회 묶음은 기존 코드에서 처리할 수 있다. 경로·판정·수정 방향이 아직 불명확한 탐색은 그대로 reasoning이 필요하다.
2. **추가 controller가 기존 추론을 제거하는지 확인한다.** 일반 MCP 서버·hook은 host의 모든 reasoning callback을 통제하지 않는다. 현재 Kotlin `documents::execute`는 문서 도구 내부 callback이다. 두 의미를 혼동해 CCS를 구현했다고 말하면 안 된다.
3. **조건 검사와 의미적 정당성을 구분한다.** 명령의 인자가 고정됐다는 사실은 그 명령이 사용자 의도에 맞거나 안전하다는 증거가 아니다. 실패·입력 변경·권한 문제는 관측된 결과로 처리해야 한다.
4. **전체 경로를 계측한다.** 선택기·authoring·fallback·최종 응답·복구를 포함해 기존 방식과 같은 품질 조건에서 비교한다. 사전 개발비가 미상이면 전체 손익분기점을 계산하지 않는다.
5. **용어 때문에 과거 구조를 되살리지 않는다.** 이 연구의 native tool receipt를 Harness에서 제거한 lifecycle 영수증 원장으로 옮길 필요는 없다. 실제 독립 리뷰·QA와 검증 대상 연결은 기존 흐름에서 유지한다.

현재 소스에서 필요한 작은 후보와 탈락 조건은 [구현 대조 제안](INF__wiki-context-improvement-candidates.md)에 있다. 새로운 범용 실행 DAG·지식 그래프·영수증 시스템을 연구 제목만으로 도입하지 않는다.
