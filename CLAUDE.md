# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 하네스: dart-search 개발팀

**목표:** 백엔드/파이프라인/DB(dart-backend), 감사보고서 원문 파싱(dart-parser),
프론트엔드(dart-frontend) 전문 에이전트와 읽기 전용 리뷰 에이전트(dart-qa,
dart-design-review)를 조율해 구현 → 검증까지 일관되게 수행한다.

**트리거:** 백엔드/파이프라인/DB/파서/프론트엔드 구현 작업이나 QA·디자인 리뷰 요청 시
`dart-search-team` 스킬을 사용하라(`.claude/skills/dart-search-team/SKILL.md`). 단순
질문이나 문서 조회는 직접 응답 가능.

**변경 이력:**
| 날짜 | 변경 내용 | 대상 | 사유 |
|------|----------|------|------|
| 2026-07-21 | 하네스 최초 등록 — 오케스트레이터 스킬(`dart-search-team`) 신설 + 기존 5개 에이전트(dart-backend/frontend/parser/qa/design-review) 내용을 M8 최신 상태로 갱신 | 전체 | 에이전트 정의 파일만 존재하고 오케스트레이터·CLAUDE.md 등록이 없던 구조적 누락 발견. 에이전트 내용도 M1~M5 스캐폴딩 단계에 머물러 M8까지의 아키텍처 재설계(지역 필터가 금융위 API 사전 스크리닝→dart_corp_index 로컬 쿼리로 전환, Phase1/Phase2 분리, 참고값/확정치 분리 등)를 전혀 반영하지 못하는 drift를 감사로 확인 |

## 프로젝트 현황

**M1(기반 구축) 스캐폴딩 완료.** `backend/`는 상세개발계획.md §3 트리를 따라 생성되어 있고
(`app/api`, `app/core`, `app/parsers`, `app/models`, `app/exporters`, `tests/fixtures`),
`config.py`/DB 모델 6종/`dart_client.py`/`corp_cache.py`/`meta.py`(quota, validate-key)/
`main.py`가 구현되어 있다.

**M2(수집 파이프라인, STEP 1~4) + M3(재무제표 파싱, STEP 5~6)까지 구현 완료
(2026-07-15).** `app/core/pipeline.py`가 STEP 1(corp_cache 갱신) → STEP 2(list.json
페이징 수집, pblntf_ty=F) → STEP 3(금융위 API 지역 사전 추림(대응 1) + corp_profiles
캐시 활용 지역/업종 필터 + results 선삽입) → STEP 4(document.xml 다운로드 + zip 해제 +
로컬 캐시 재사용) → STEP 5(`app/parsers/xml_parser.py`/`pdf_parser.py`로 재무제표
당기·전기 13항목 파싱 + `audit_opinion.py`로 감사의견 추출, API 호출 없음) → STEP 6
(매출액 범위 사후 필터, `excluded_by_revenue`)까지 오케스트레이션하며, `QuotaExceededError`
발생 시 Job을 `PAUSED_QUOTA`로 자동 전환하고(체크포인트 보존), `CANCELLED`는 다음
체크포인트에서 감지해 중단한다. `app/api/jobs.py`
(`POST /api/jobs`, `GET /api/jobs`, `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel`,
`POST /api/jobs/{id}/resume`, `POST /api/jobs/{id}/retry-failed` — parse_status=FAILED
건만 STEP5 재실행)와 `app/api/results.py`(`GET /api/jobs/{id}/results`, 페이징/
`parse_status`/`excluded_by_revenue` 필터, 실제 값이 채워짐)가 `main.py`에 등록되어
실제 OpenDART API로 end-to-end 스모크 테스트를 통과했다.

**M4 백엔드 TODO 3종 구현 완료(2026-07-15).** ① `GET /api/meta/regions`/
`GET /api/meta/industries`를 `app/api/meta.py`에 추가했다 — 정적 데이터는
각각 신규 파일 `app/core/region_data.py`(17개 시도 + 시군구, `filters.py`의
`SIDO_ALIASES` key와 1:1 일치. 세종특별자치시는 하위 시군구가 없는 단층제라
빈 배열로 처리)와 `app/core/industry_data.py`(KSIC 10차 대분류 21개(A~U) +
중분류 2자리 코드, `induty_code` prefix 매칭 체계와 동일)에 두었다. ②
`app/exporters/excel.py`의 `export_results()`를 구현했다 — `results` DB
필드명은 그대로 유지하고(`RESULT_COLUMN_LABELS` dict로 파일 출력 시에만
한국어 헤더로 매핑), pandas DataFrame을 거쳐 xlsx(openpyxl)/csv(`utf-8-sig`
BOM)로 직렬화한다. ③ `app/api/results.py`에 `GET /api/jobs/{id}/export?
format=xlsx|csv`를 추가했다 — 기존 `/results`의 쿼리 빌더를
`_build_results_query()`로 공유 추출해 `parse_status`/`excluded_by_revenue`
필터를 동일하게 지원하고, 페이징 없이 전체를 내려준다. `format`이 xlsx/csv가
아니면 400, `Content-Disposition: attachment`, xlsx/csv 각각 정확한
`Content-Type`을 반환한다. 신규 테스트: `backend/tests/test_api_meta.py`,
`backend/tests/test_exporters.py`, `backend/tests/test_api_results.py`.

**M4 프론트엔드 구현 완료(2026-07-15).** `frontend/`를 Vite + React 18 +
TypeScript + Mantine 9로 스캐폴딩했다(UI 라이브러리는 상세개발계획.md에서
권장한 Mantine 채택). `vite.config.ts`에 dev proxy(`/api` →
`http://localhost:8000`)를 설정해 프론트가 DART API 키를 전혀 다루지 않고
백엔드의 `/api/...`만 호출하도록 했다. 3개 라우트(`/search`, `/jobs`,
`/jobs/:id/results`)를 react-router-dom으로 구성했고, `frontend/src/api/`에
`jobs.ts`/`meta.ts`/`results.ts`/`client.ts`로 백엔드 REST 계약을 타입과 함께
정리했다. SearchPage(지역/매출액/업종/기간 입력 → `POST /api/jobs`,
"예상 규모 미리보기" 버튼은 대응 API가 없어 스코프 제외), JobsPage(RUNNING
Job이 있을 때만 2초 폴링 + `clearInterval` 정리, PAUSED_QUOTA/FAILED/RUNNING
상태별 액션 버튼), ResultPage(핵심 컬럼 기본 표시 + 35개 컬럼 표시/숨김 토글,
parse_status/excluded_by_revenue 필터 탭, 행 클릭 시 당기·전기 전 항목 +
DART 원문 링크 Drawer, 현재 필터를 반영한 Excel/CSV 다운로드)까지 구현했다.
백엔드(M2~M4 TODO 포함)를 실제로 띄운 채 Playwright로 end-to-end 스모크
테스트(폼 제출 → Job 생성 payload 검증 → 목록/진행률/버튼 노출 → 결과
테이블/상세 패널/DART 링크 조립)를 수행해 콘솔/런타임 에러가 없음을
확인했고, `npm run build`(tsc 타입체크)/`npm run lint`(oxlint) 모두
통과했다. 상세는 `frontend/README.md`와 상세개발계획.md M4 체크리스트 참고.
스모크 테스트 중 STEP 2(list.json)가 `corp_code` 미지정 시 조회 기간을
3개월까지만 허용한다는 DART API 제약을 발견했다(1년 기본값으로 Job을
만들면 즉시 FAILED) — 프론트는 상세개발계획.md §7-1 명세("기본값 최근
1년")를 그대로 구현했으므로 UI를 임의로 바꾸지 않았고, 이 제약은 STEP 2를
분할 조회하도록 보강할지 여부를 dart-backend/dart-parser 에이전트가
판단할 사항으로 남겨둔다. **→ 이 건은 같은 날(2026-07-15) 백엔드에서
90일 단위 분할 조회로 해결했다(아래 "M2에서 확정된 설계 판단"의 STEP 2
분할 조회 항목 참고)** — 프론트/§7-1의 "기본값 최근 1년" UI는 그대로 두고
백엔드 STEP 2 내부에서만 분할하므로 계약 변경 없음.

**STEP 7(최근 N년 재무이력) 추가 구현 완료(2026-07-15, 백엔드 전용).**
사용자가 "최근 N년치 재무 이력"을 요청해, 기존 STEP 1~6(당기·전기 2개년만
수집) 뒤에 STEP 7을 추가했다 — `results` 테이블의 `_cur`/`_prv` 컬럼 의미는
전혀 건드리지 않고("가장 최근 감사보고서 1건의 당기·전기" 그대로 유지),
새 테이블 `financial_snapshots`(`app/models/financial_snapshot.py`, 회사×
회계연도 단위, `UNIQUE(result_id, fiscal_year)`)에 이력을 별도로 쌓는다.
핵심 설계:
- **"필터 통과 후에만" 원칙 재사용**: STEP 3(FSC 사전 추림)와 같은 철학으로,
  STEP 7은 STEP 1~6을 다 통과하고 `excluded_by_revenue=0`인 최종 결과만
  대상으로 한다 — 쿼터 영향이 최종 결과 건수에만 비례.
- **실측(2026-07-15): `list.json`에 `corp_code`를 지정하면 3개월 조회기간
  제한이 사라진다.** `corp_code=01552935`(시대산업)로 10년 범위
  (`bgn_de=20160101`~`end_de=20260630`)를 조회해 2021~2025 회계연도 감사
  보고서 5건을 한 번에 받았다 — STEP 2가 겪은 "corp_code 없이는 90일
  제한"과 달리, STEP 7은 `_split_period_into_windows()`가 필요 없다(단일
  기간 조회로 충분).
  `history_years`(짝수 2/4/6/10, 기본 4)만큼의 서로 다른 회계연도를 모을
  때까지 최신 공시부터(newest-first) document.xml을 열어 파싱하고, 목표에
  도달하면 더 오래된 공시는 다운로드하지 않는다 — 자세한 이유(oldest-first로
  하면 오히려 최신 연도를 놓칠 수 있음)와 회계연도 판정 규칙은
  `app/core/pipeline.py`의 STEP 7 설계 메모, `app/models/financial_snapshot.py`
  참고. 다운로드/파싱은 STEP 4/5 로직(`_ensure_document_cached()`로 STEP 4와
  공유 추출, `parse_xml_financials`/`parse_pdf_financials`)을 그대로 재사용했다
  — 새 파서를 만들지 않았다. `POST /api/jobs`에 `history_years` 필드가
  추가됐고(`Job.history_years` 컬럼), Job DONE 시점이 STEP 6에서 STEP 7
  완료 시점으로 다시 이동했다(M3에서 STEP4→STEP6로 옮긴 전례와 동일한
  패턴). 신규 조회 API `GET /api/jobs/{id}/results/{result_id}/history`
  (오래된 연도 → 최신 연도 순 반환, `app/api/results.py`)를 추가했다 —
  기존 `/results` 목록 응답과 `/export`는 스코프 밖이라 건드리지 않았다.
  단위 테스트: `backend/tests/test_pipeline.py`의 STEP 7 섹션(조기 중단/
  연도 부족/resume 단축/STEP7 드라이버 필터링/run_job 통합/쿼터초과+resume
  총 7종), `backend/tests/test_api_jobs.py`(history_years 검증 2종),
  `backend/tests/test_api_results.py`(history 엔드포인트 5종).

**M5 착수: 루트 README.md 작성 완료(2026-07-15).** 설치/실행(백엔드·프론트엔드
공통)/API 키 발급 안내(OpenDART, 공공데이터포털)/사용 흐름/현재 진행 상황을
정리한 [README.md](README.md)를 루트에 추가했다 — `frontend/README.md`(M4,
프론트 세부 구조)는 그대로 유지하고 루트 README는 프로젝트 전체 개요 + 두
백엔드/프론트를 아우르는 실행 안내를 담당하도록 역할을 나눴다. 상세개발계획.md
§8 M5 체크리스트의 "README 작성"/"파싱 실패 건 재시도 기능"(M3에서 이미 구현)
항목을 완료로 표시했다. 남은 M5 항목이던 "샘플 10개사 수동 검수"는 이후 같은 날
완료됐고(아래 참고), "실전 조건 풀 실행"은 진행하다 성능 병목을 발견해 미완료로
남아 있다(아래 "M5 '실전 조건 1건 풀 실행'은 미완료" 단락 참고).

**STEP 7 프론트엔드 연동 완료(2026-07-15).** `frontend/src/pages/SearchPage.tsx`에
"재무 이력 조회 기간" `SegmentedControl`(2/4/6/10년, 기본 4년)을 추가해
`POST /api/jobs` payload 최상위에 `history_years`를 함께 보낸다.
`frontend/src/types/index.ts`에 `HistoryYears`/`FinancialSnapshotResponse`
타입과 `JobCreateRequest.history_years`/`JobResponse.history_years`를
추가했다. `frontend/src/api/results.ts`에 `getResultHistory(jobId, resultId)`
(`GET /api/jobs/{id}/results/{result_id}/history`)를 추가했고,
`frontend/src/components/ResultDetailDrawer.tsx`의 당기·전기 표 아래에
연도를 열로, 재무 13항목을 행으로 배치한 이력 표를 추가했다 — Drawer가
열려 선택된 `result.id`가 바뀔 때만(목록 로드 시 전체 미리 fetch하지 않고)
`useEffect`로 lazy fetch하며, 빈 배열(매출액 제외 등)은 에러가 아니라
안내 문구로 표시한다. 기존 `/results` 목록 API 호출 방식과 당기·전기
표시 로직은 그대로 두었다. `npm run build`(tsc)/`npm run lint`(oxlint)
통과 확인. **실제 화면에서의 end-to-end 확인을 다음 세션(같은 날, 2026-07-15
후반)에 완료했다.** 이전 세션 종료 시점에는 기동 중이던 백엔드 프로세스(port 8000)가
STEP 7 반영 이전의 구버전 코드로 떠 있었고 `jobs.history_years` 컬럼 관련 오류가
있었으나, 이번에 다시 확인해보니(누가/언제 고쳤는지는 불명확) `dart_search.db`에
이미 `jobs.history_years` 컬럼과 `financial_snapshots` 테이블이 정상 존재했다.
venv(Python 3.11.3)로 백엔드를 재기동(port 8000)한 뒤, `dart-frontend` 에이전트가
Vite dev 서버 + Playwright(headless Chromium, `npm install --no-save`로 임시 설치,
package.json 변경 없음)로 실제 폼 제출(경상남도 김해시, 2026-06-01~05,
history_years=4 기본값) → Job 폴링 → Job #7 완료 → 결과 상세 Drawer의 "재무 이력
(최근 N년)" 표에 2023~2026년 4개 연도 × 재무 13항목 값이 정상 렌더링됨을 확인했다
(콘솔/네트워크 에러 없음, `GET /api/jobs/{id}/results/{result_id}/history` 정상
응답). 코드 수정은 필요 없었다 — 실제 버그는 없었고 초기 스크립트 실패 2회는
Playwright 셀렉터 문제였다. 테스트 중 실제 DART API 쿼터 30건 사용(555→585).

### M2에서 확정된 설계 판단 (상세개발계획.md §4-1과 함께 참고)

- **지역 필터는 대응 1(금융위 API 사전 추림)을 구현했고, 대응 2(corp_profiles 전역
  캐시)는 안전망으로 함께 유지한다 (2026-07-15 갱신).** 커버리지 스파이크(경남 표본
  19/19, 100%)로 대응 1 채택이 확정된 뒤, `app/core/pipeline.py`의
  `_resolve_candidate_profile()`을 다음처럼 구현했다: (1) `corp_profiles` 캐시가
  fresh하면 그대로 재사용(API 호출 없음, 대응 2와 동일), (2) 캐시 미스면 DART
  `company.json`을 바로 부르지 않고 먼저 `FscCorpInfoClient.get_corp_basic_info()`로
  회사명을 조회해 주소를 가볍게 확인, (3) FSC로 지역이 명백히 다르다고 확인되면
  `company.json` 호출을 생략하고 FSC의 sido/sigungu만 `corp_profiles`에 부분
  upsert(나머지 컬럼은 NULL), (4) FSC에서 매칭이 안 되거나(이름 검색 결과 없음)
  FSC 호출 자체가 실패하면 **보수적으로** 기존처럼 `company.json`을 직접 호출해
  확정한다(안전망). STEP 3 호출부(`_run_region_industry_filter`)의 나머지 로직
  (results 선삽입 등)은 그대로 유지했다. 새 DB 테이블은 추가하지 않았다 —
  `corp_profiles` 하나로 풀 데이터/부분 데이터 upsert를 모두 처리한다. 별도
  추상클래스/플러그인 시스템, 금융위 API 대량 사전 적재 배치 스크립트는
  과설계로 판단해 만들지 않았다(Job 실행 중 건별로 자연스럽게 채워지는 방식
  유지, 기존 대응 2 방식과의 일관성).
  **실측 쿼터 절감 효과(스모크 테스트, 2026-07-15)**: 경남 조건, 2025-06-01~05
  기간, STEP2 후보 108개사 → STEP3 `company.json` 호출 9건(대응 2였다면 108건
  전부 호출 필요) — **약 92% 절감**. 관련 테스트는
  `backend/tests/test_pipeline.py`의 `test_resolve_candidate_profile_fsc_*`
  (지역 일치/불일치/FSC 미매칭/FSC 호출 실패 4분기) +
  `test_run_region_industry_filter_fsc_reduces_company_json_calls` +
  `test_run_job_fsc_prefilter_reduces_dart_company_calls_end_to_end`.
- **resume은 STEP별로 의미가 다르다.** §5 스키마에는 STEP 2(공시 목록) 결과를 위한
  전용 테이블이 없으므로, STEP 1(corp_cache TTL 체크)과 STEP 2(list.json 페이징)는
  resume 시 항상 처음부터 다시 실행한다(멱등이고 비용이 낮음 — list.json은 페이지
  수만큼만 호출). 실제로 비용이 큰 STEP 3(company.json)과 STEP 4(document.xml)는 각각
  `corp_profiles` TTL 캐시와 `DOCUMENT_CACHE_DIR` 로컬 파일 캐시로 "이미 처리된 건은
  재호출하지 않는" 방식의 resume을 구현했다. `results` 테이블도 이미 삽입된 corp_code는
  중복 삽입하지 않는다.
- **주소 파싱은 `adres` 문자열의 앞 두 토큰(시도/시군구)을 사용한다**
  (`app/core/filters.py`의 `parse_address()`). 예: `"전라북도 군산시 현충로 35 (나운동)"`
  → 시도 `전북특별자치도`(SIDO_ALIASES 표준명), 시군구 `군산시`. 세종특별자치시처럼
  시군구가 없는 주소 등 예외 케이스는 M3/M5 검수 단계에서 실측 후 보강 대상.
- STEP 6(매출액 필터) 완료 시점에 Job을 `DONE`으로 표시한다(M2 시점에는 STEP4
  완료 시 바로 DONE으로 표시했으나, M3에서 STEP5/6이 붙으면서 이 지점으로 이동했다).
- **STEP 2(list.json)는 `bgn_de`~`end_de`를 90일 단위로 분할해 구간별로 페이징
  호출한다 (2026-07-15 실측 발견 후 당일 수정).** M4 프론트 스모크 테스트에서
  corp_code 없이 날짜 범위만으로 list.json을 조회하면 조회 기간이 3개월(90일)을
  넘을 수 없다는 제약을 실측했다(`status=100, message="corp_code가 없는 경우
  검색기간은 3개월만 가능합니다"`가 즉시 반환되고 Job이 FAILED 처리됨). 상세개발
  계획.md §7-1이 SearchPage 기간 입력의 기본값을 "최근 1년"으로 명시하고 있어
  사용자가 기본값 그대로 제출하는 가장 흔한 경로가 100% 실패하는 문제였다.
  `app/core/pipeline.py`의 `_split_period_into_windows()`가 전체 구간을 90일
  이하(달력월 경계 계산의 엣지케이스를 피하기 위해 보수적으로 고정 일수 사용)
  구간으로 나누고, `_collect_candidates()`가 구간마다 `by_corp` dict를 그대로
  재사용하며 페이징 순회한다 — 여러 구간에 걸쳐 같은 회사가 여러 건(정정 포함)
  잡혀도 corp_code 기준 dedup + rcept_no 최신 우선 로직이 구간을 넘나들며 그대로
  동작한다. 진행률(`progress_done`/`progress_total`)은 "구간마다 최소 1페이지"로
  초기 추정한 뒤 각 구간의 실제 `total_page`를 알게 되는 시점에 차이만큼 보정하는
  방식으로 페이지 단위 누적 카운트를 유지한다. 별도 "기간 분할기" 클래스나 분할
  일수를 설정값으로 빼는 등의 추상화는 과설계로 판단해 만들지 않았다 — STEP 2
  함수 범위 안에서 처리한다. `POST /api/jobs`가 받는 조건 스키마나 프론트 계약은
  바뀌지 않는다(프론트는 여전히 "최근 1년"을 그대로 보낼 수 있고, 분할은 백엔드
  STEP 2 내부에서만 일어난다). 관련 테스트:
  `backend/tests/test_pipeline.py`의
  `test_split_period_into_windows_chunks_by_90_days`,
  `test_split_period_into_windows_single_window_when_within_90_days`,
  `test_collect_candidates_splits_period_over_90_days_and_dedupes_across_windows`.

### M3에서 확정된 설계 판단 (상세개발계획.md §4-4와 함께 참고)

- **실제 원문 30건(2026-07-15 DART API로 확보) 실측 결과 전부 XML이었다** —
  2026년 4~6월 접수분 25건은 물론 2012년 초 접수분 5건까지도 `document.xml`
  API가 XML로 반환했다. 계획 당시 우려했던 PDF/HWP 비중은 이 표본에서는 0%.
  `pdf_parser.py`는 그래서 실제 표본으로 검증되지 못한 best-effort 구현이며
  (pdfplumber 기반, xml_parser와 동일한 계정과목 사전/금액 파싱 규칙 재사용),
  HWP는 여전히 미구현 상태(Phase 1 계획대로 실패 기록만). M5에서 실제 PDF/HWP
  원문이 발견되면 그때 정확도를 실측해야 한다.
- **XML 파싱은 `lxml.etree.XMLParser(recover=True)`로 복구 모드를 쓴다** — 실측
  샘플 다수가 본문 서술형 텍스트에 `<`/`&`를 이스케이프하지 않은 채 담고 있어
  (예: "&cr;" 같은 정의되지 않은 엔터티) 엄격 모드로는 파싱 자체가 실패했다.
  복구 모드로 손상된 부분만 건너뛰고 앞부분(재무상태표/손익계산서가 있는 구간)
  구조는 그대로 활용한다 — 실측상 깨지는 지점은 대개 뒤쪽 서술형 주석이었다.
- **계정과목 정규화(`app/parsers/base.py::normalize_account_label()`)는 실측
  기반으로 다음을 모두 흡수한다**: 유니코드 로마숫자("Ⅰ.") vs 아스키
  로마숫자("I.") 접두어 혼용(같은 문서 안에서도 섞여 쓰는 회사가 있었다),
  번호/가나다 접두어, "(주석13)"/"(주6)"처럼 회사마다 축약 방식이 다른 각주
  접미어(순수 숫자/콤마/공백일 때만 제거 — "당기순이익(손실)"처럼 괄호가 의미를
  구성하는 경우는 보존), 글자 사이 공백·전각 공백("자 산 총 계"). 금액 파싱은
  "-"(명시적 0)과 빈 문자열(그 열은 안 쓰는 열 — None)을 구분한다.
- **"영업손실"/"당기순손실"처럼 손실이 명시된 행은 원문 숫자가 부호 없이
  양수로 찍혀 있다** — `xml_parser.py`가 저장 시 부호를 뒤집는다(`_apply_sign`).
  "매출총이익율(%)"은 원문의 "매출총이익"(금액) 행을 그대로 쓰지 않고
  `compute_gross_margin()`으로 매출액/매출원가에서 계산한다(PRD 3-2절 정의).
- **감사의견 추출(`audit_opinion.py`)은 신서식("공정하게 표시하고 있습니다")과
  2014년 이전 구서식("적정하게 표시하고 있습니다")을 모두 처리한다** — 실측
  2012년 원문에서 신서식과 다른 문구를 확인했다. 띄어쓰기 변형("표시하고있습니다"
  처럼 붙여 쓴 경우)도 정규식 `\s*`로 흡수한다.
- **매출원가/판매비와관리비가 구조적으로 없는 회사가 실측 5건 있었다**
  (부동산임대업/통신서비스 등 "영업수익/영업비용" 서식 — 매출원가·판관비를
  구분하지 않고 비용을 한 줄로 합산한다). 이는 파싱 실패가 아니라 원문 자체의
  서식 차이이므로 해당 두 필드만 `None`으로 두고 `parse_status=PARTIAL`로
  정확히 반영한다(파서 버그로 오인해 억지로 채우지 않는다).
- **재무제표 자체가 첨부되지 않은 경우(실측 10건, 전부 의견거절)**는
  `ACLASS="FINANCE"` 테이블이 원문에 아예 없다 — `parse_status=FAILED`가
  아니라 `PARTIAL`로 판정한다(파싱 실패가 아니라 "원문에 없음").
  `determine_parse_status()`가 이 판정 로직을 xml_parser/pdf_parser가
  공유하도록 순수함수로 분리해 둔다.
- **STEP 5(파싱)는 API 호출이 전혀 없다** — `DOCUMENT_CACHE_DIR`에 이미
  받아둔 원문 파일만 읽으므로 쿼터와 무관하다. resume은 `results.parse_status
  IS NULL`인 건만 다시 열어 처리하는 방식으로 구현했고(`_run_financial_parsing`),
  `POST /api/jobs/{id}/retry-failed`는 `parse_status=FAILED`인 건만 NULL로
  리셋한 뒤 같은 함수를 재사용해 재파싱한다(`retry_failed_parsing`) — 별도
  재시도 로직을 새로 만들지 않았다.
  단위 테스트: `backend/tests/test_parsers.py`(정규화/금액 파싱 유틸 + 실제
  원문 30건 중 선별한 표본의 실측 수치 검증), `backend/tests/test_pipeline.py`의
  `test_run_financial_parsing_*`/`test_retry_failed_parsing_*`/
  `test_run_revenue_filter_*`.
- **M5 샘플 10개사 수동 검수(2026-07-15) — 총계 밑줄 괘선 버그 1건 발견·수정,
  나머지는 정규화 사전 보강 불필요.** 기존에 상세 검증된 5건 외 10건
  (2012년 구서식 4건 + 2026년 6건)을 원문 XML의 재무상태표/손익계산서 TE 셀과
  파서 결과·감사의견을 1:1 대조했다. **발견한 실제 버그는 1건**: 2012년 원문
  `20120110000471`의 자산총계 셀이 `"16,507,429,508 ==============="`처럼 총계
  행의 이중 밑줄이 ASCII 괘선(`=`)으로 금액 셀에 그대로 섞여 들어와 있었고,
  `parse_won_amount`가 float 변환에 실패해 `total_assets`만 None으로 누락됐다
  (유동/비유동자산은 정상이라 더 눈에 띔). `base.py`의 `parse_won_amount`가
  앞뒤 괘선(`=`/전각 `＝`)을 제거하도록 고쳤다(`=`는 정상 금액에 절대 안 나오므로
  안전). **계정과목 정규화 사전(`ACCOUNT_NAME_ALIASES`)은 한 건도 추가할 필요가
  없었다** — 10건의 모든 표준 라벨이 기존 사전+정규화(로마숫자/각주/글자간 공백
  흡수)로 정확히 매핑됐다. cogs/sga가 None인 PARTIAL 3건
  (`20120110000471`/`20120110000508` 온천호텔/`20260630000753` 싱가폴텔레콤)은
  전부 "영업수익/영업비용" 서비스업 서식의 구조적 부재(태보산업과 동일 원리)이고,
  의견거절 4건(`...634`/`...704`/`...826`/`...967`)은 ACLASS="FINANCE" 테이블이
  0개인 재무제표 미첨부로 모두 PARTIAL 정상 판정이었다. 감사의견도 10건 전부
  원문 문구와 일치(2012 구서식 "적정하게" 4건, 2026 "공정하게" 적정 2건,
  의견거절 4건). 회귀 테스트 추가: `test_parse_won_amount`에 괘선 케이스 3종,
  `test_parse_xml_financials_2012_manufacturing_full_values`(2012 제조업 13항목
  전값 + 아스키 "X." 접두어 + 전기 비유동부채 "-"=0),
  `test_parse_xml_financials_recovers_underlined_total_cell`(괘선 복구),
  감사의견 파라미터 2건. 전체 129 테스트 통과.

**M5 "실전 조건 1건 풀 실행"은 미완료 — 대신 중요한 성능 병목을 실측으로
발견했다(2026-07-15).** 프론트 기본값인 "최근 1년" 기간으로 실전 조건(경남 +
매출 60~150억) Job(#8)을 실제로 실행했더니, STEP 2가 전국 단위로 감사보고서
제출 후보 **39,503건**을 모았다(지역 필터는 DART에 검색 API가 없어 STEP 3에서만
가능 — 상세개발계획.md §9 "지역 검색 API 부재" 리스크 그대로). STEP 3(지역/업종
필터링)의 실측 처리 속도는 **초당 약 0.22건**이라 39,503건 전체 처리에 STEP 3만
약 **49시간**이 걸린다는 계산이 나와 실행 중 취소했다. 기간을 2개월로 좁힌
재실행(Job #9, 후보 1,408건)은 현실적인 속도로 진행되는 것까지는 확인했지만
사용자 요청으로 STEP 3 도중(40/1,408) 다시 취소해, STEP 4 이후 단계는 이번
세션에서 실측하지 못했다. 조사 중 DART `company.json`(기업개황) 개발문서
원문(`https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019002`)의
HTML을 직접 파싱해 요청 인자가 `crtfc_key`/`corp_code`(단일값, `STRING(8)`)
둘뿐이며 배치 조회나 지역 필터 파라미터가 없음을 재확인했다(에러코드 021
"최대 100건"은 이 API 전용이 아니라 모든 API 상세페이지에 공통으로 실리는
범용 에러표의 일부였다) — 배치 조회로 STEP 3를 근본적으로 빠르게 할 여지는
없다. **다음 세션에서 판단할 것**: (a) 넓은 기간 조건 선택 시 예상 소요시간을
안내하는 UX를 추가할지, (b) STEP 3 동시성(현재 5건 제한)을 높여 처리량 자체를
개선할지, (c) 현재 설계를 그대로 받아들이고 M5 "실전 조건 풀 실행"은 더 좁은
기간 조건(예: 2개월)으로 끝까지 실행해 완료 처리할지. Job #8/#9 모두 `CANCELLED`
상태로 DB에 남아 있다. **→ 같은 날 후속 세션에서 (d) 근본적 아키텍처 재설계로
방향을 잡았다 — 아래 참고.**

**아키텍처 재설계 스파이크 완료 + 설계 문서화 완료(2026-07-15, 구현 전).**
위 성능 병목의 근본 원인("DART에 지역 검색이 없어 STEP 2가 전국 후보를 모은
뒤에야 STEP 3에서 지역을 걸러낼 수 있다")을 뒤집을 수 있는지 확인하기 위해,
공공데이터포털의 **금융위원회_기업 재무정보 API**(`GetFinaStatInfoService_V2`
— 대응 1이 쓰는 "기업기본정보"(`getCorpOutline_V2`)와는 별개 API)를 실제 키로
스파이크 검증했다. 핵심 발견 4가지: ① 이미 파싱된 매출액 5건과
`getSummFinaStat_V2`(crno=DART `jurir_no`, bizYear로 조회)의 `enpSaleAmt`가
**5/5 정확히 일치**(천원 단위 절삭 차이만 있음) — DART 원문을 열어보지
않고도 당기 매출액을 알 수 있음을 실측 확인했다. ② 다만 전기(`bizYear` 1년
전)는 `totalCount=0`으로 **최신 연도만 보유** — STEP 7(다년치 이력)을
대체할 수는 없다. ③ `getCorpOutline_V2` 응답의 `fssCorpUnqNo` 필드가 DART
`corp_code`와 **5/5 정확히 일치** — 기존 "대응 1"의 회사명 정규화 매칭보다
신뢰도 높은 직접 조인 키다(단, 소스기관별 중복 레코드 중 일부에만 채워짐,
병합 로직 필요). ④ `corpNm` 없이 전체 페이징하면 `totalCount=1,282,065`
(원래 §4-1 추정치 4만 개사보다 훨씬 큼). 이 4가지를 근거로 **파이프라인을
"Phase 1(공공데이터로 지역·업종·매출액 다 걸러 후보 확정) → Phase 2(확정된
소수 후보만 DART로 다년치 크롤링)"로 분리하는 재설계**를 확정하고
[상세개발계획.md §4-7](상세개발계획.md)에 상세 설계(STEP별 로직, 새 DB
테이블 `fsc_corp_index`, 열린 질문 6가지)를, §8에 구현 체크리스트(M6, 전체
미완료)를 문서화했다. `GetFinaStatInfoService_V2`가 감사보고서 외부감사여부를
구분해서 조회할 수 있는지도 공식 FAQ(`fsc.go.kr/in060501` Q11)로 확인했다 —
"구분 조회 불가, 외감/비외감 데이터가 섞여서 나온다"가 답이었지만, 이 프로젝트는
STEP 2에서 이미 외감법인만 다루므로 무관하다. **이번 세션에서는 설계·문서화만
했고 실제 코드 변경은 없다** — `app/core/pipeline.py`, DB 스키마 등은 그대로다.
스파이크에 사용한 임시 스크립트는 커밋하지 않고 스캐치패드에만 남겼다(DART
`company.json` 5건 + FSC 두 API 합쳐 약 20여 건 쿼터 소모, 무시할 수준).

**§4-7 재설계 문서 확정 보강 — 총자산 필터 + Phase1/2 버튼 UX (2026-07-15,
설계만, 구현 전).** 사용자가 PRD.md/상세개발계획.md를 재검토하며 "① 공공데이터로
지역/매출액/총자산/업종을 먼저 걸러 후보 회사·코드를 확보하고, ② 사용자가
수집기간(2/4/6/10년)을 지정해 버튼을 눌러야 그 후보들의 DART 재무정보를
크롤링한다"는 흐름을 요청했다 — 확인해보니 이는 이미 위 §4-7/M6에서 스파이크
검증까지 마친 2단계 파이프라인 설계와 정확히 일치했고, ①은 총자산이라는
새 필터 차원의 추가, ②는 M6 체크리스트에 "열린 질문"으로만 남아있던 "Job
생성 흐름에 Phase1/2 구분을 프론트에 노출할지"에 대한 확정 답이었다. 이번
세션에서 두 가지를 상세개발계획.md에 확정 설계로 반영했다(**문서만 수정,
코드 변경 없음**):
- **§4-7-1(신규)**: `jobs.phase`(`'CANDIDATES'`|`'FINANCIALS'`) 컬럼을 추가해
  `POST /api/jobs`는 Phase 1(A1~A4, 후보 확정)까지만 실행하고 멈추도록 하고,
  신규 API `POST /api/jobs/{id}/start-financials`(body: `history_years`)를
  사용자가 후보 목록을 검토한 뒤 명시적으로 호출해야 Phase 2(B1~B5, 구
  STEP4~7 재사용)가 시작되도록 확정했다. SearchPage의 "공시 대상 기간" 입력은
  제거하고(Phase 1은 FSC 스냅샷 기반이라 기간 개념이 없음), 그 자리의 "기간"
  개념은 후보 목록 화면의 수집기간(2/4/6/10년) 선택으로 옮긴다.
- **§4-7-2(신규, 총자산 필터)**: 매출액과 동일한 패턴으로 설계했다 —
  `jobs.cond_total_assets`/`results.excluded_by_assets` 컬럼을 신설해 **최종
  판정(B4, 사후 필터)은 이미 확보되는 `results.total_assets_cur` 기준으로
  항상 정확히 동작**하도록 하고, Phase 1 사전 스크리닝(A3)에서 DART 호출
  전에 미리 거르는 최적화는 금융위 API의 재무상태표 오퍼레이션(`getBs_V2`
  추정, 정확한 이름/필드 미확인)이 확인돼야 가능하다고 명시했다 — 기존
  "열린 질문 3"이 "있으면 좋음" 수준에서 "총자산 사전필터의 전제조건"으로
  우선순위가 올라갔다. **사전 스크리닝이 끝내 안 되더라도 총자산 필터 기능
  자체의 실현 가능성에는 영향이 없다**는 점(사후 필터가 항상 보장)이
  이번 검토의 핵심 결론이다.
- PRD.md §2/§3-2/§4/§5도 같은 취지로 갱신했다(총자산을 조건 입력값·검색
  필터로 명시, 처리 파이프라인에 Phase1/2 버튼 트리거 흐름 반영).
- M6 체크리스트(§8)에 `jobs.phase`/`cond_total_assets`/`excluded_by_assets`
  컬럼 추가, `start-financials` 엔드포인트 구현, SearchPage/ResultPage UI
  변경 항목을 구체화해 추가했다.

**M6 실제 구현 완료(2026-07-15, 같은 세션 후반) — A1 전수 크롤 실행만 남음.**
위 확정 설계를 바로 이어서 구현까지 완료했다. 구현 전 미확인 사항을 먼저
실제 API 호출로 검증했다: ① `getSummFinaStat_V2`(기존에 매출액 확인용으로
이미 쓰던 그 오퍼레이션) 응답에 `enpTastAmt`(총자산)/`enpTdbtAmt`(총부채)/
`enpTcptAmt`(총자본)가 이미 포함돼 있어 `getBs_V2` 없이도 총자산 사전
스크리닝이 바로 가능함을 확인(그라운드 트루스와 만원 단위 오차로 일치),
② `getCorpOutline_V2` 50페이지 파일럿 실측으로 전체(1,282,065건,
12,821페이지) 크롤에 순차로 약 10.2시간이 걸림을 확인, ③ **설계 문서의
버그를 구현 전에 발견** — `fsc_corp_index.fss_corp_unq_no`를 PK로 설계해
뒀었는데(§4-7 최초 설계), 실제로 무작위 표본(n=100)에서 24%가 빈 값이었고
`crno`도 해외 레코드는 `"0000000000000"` 더미값이라 어느 쪽도 그대로는
PK가 될 수 없었다 — `id` autoincrement PK + `crno`/`fss_corp_unq_no` 부분
UNIQUE 인덱스로 상세개발계획.md §5를 정정한 뒤 구현했다(스파이크 스크립트는
스캐치패드에만 남기고 커밋하지 않음, 기존 관행과 동일).

dart-backend 에이전트에 위 검증 결과를 모두 명시해 위임했다: `fsc_corp_index`
테이블, `FscCorpInfoClient.get_summary_financial_stat()`, 신규
`app/core/fsc_index.py`(A1~A4), `run_job_phase1()`/`run_job_phase2()`(기존
STEP1~7 함수는 무변경 재사용 — Phase2는 corp_code만 다르고 다운로드/파싱
로직은 그대로), `POST /api/jobs/{id}/start-financials`, `jobs.phase`에 따른
resume 분기, `excluded_by_assets` 필터를 구현했고 **`pytest tests/ -q`
150 passed**(기존 129건 + 신규 21건)를 직접 재실행해 확인했다. 이어서
dart-frontend 에이전트로 SearchPage("공시 대상 기간" 입력 제거 + 총자산
범위 입력 추가)/JobsPage(`phase` 배지)/ResultPage(`phase`로 "후보 목록
뷰"/"확정 결과 뷰" 분기, 후보 목록 뷰에 재무정보 수집 시작 버튼 + 기간
선택)를 구현했다 — `npm run build`/`npm run lint` 통과, 실제 백엔드 기동한
채 Playwright 스모크 확인(에러 없음, `fsc_corp_index`가 비어 있어 Job이
FAILED로 끝나는 것까지 정상 확인 — 아래 참고).

프론트 구현 중 발견한 자잘한 설계 흠집도 그 자리에서 고쳤다 — 백엔드
`JobCreateRequest.period`가 여전히 required라 프론트가 의미 없는 고정
날짜값(`bgn_de: "20250101"` 등)을 매 요청마다 채워 보내야 했는데, Phase 1이
이 값을 쓰지 않는 이상 required로 둘 이유가 없어 `period: PeriodCondition |
None = None`으로 바꾸고 프론트도 그 필드를 아예 안 보내도록 정리했다
(오케스트레이터가 직접 수정, `pytest`/`npm run build` 재확인 완료).

**남은 것은 A1 전수 크롤 실제 실행뿐이다.** `fsc_corp_index`가 비어 있는
채로는 Phase 1 Job이 즉시 "fsc_corp_index가 비어 있습니다" 메시지와 함께
`FAILED`로 끝난다 — 실제로 프론트 스모크 테스트에서 이 경로를 확인했다
(화면이 깨지지 않고 에러가 잘 보임). 관리자 전용 `POST /api/meta/fsc-index/
refresh`가 구현·테스트는 됐지만 **이번 세션에서 실제로 호출하지 않았다** —
약 10.2시간이 걸리고 data.go.kr API를 12,821회 호출하는 장시간 작업이라
사용자 승인 후 실행하는 게 맞다고 판단했다. 다음 세션에서 판단할 것:
(a) 지금 바로 실행할지(수 시간 백그라운드로 흘려보내야 함), (b) 동시성을
도입해 단축할지(§4-7 — data.go.kr 동시 접속 정책 미검증 상태), (c) 실행
후 M5 "실전 조건 1건 풀 실행"을 새 파이프라인으로 마무리할지.

**FSC 인덱스 갱신 시각 화면 노출 추가(2026-07-15, 같은 세션 후속).** 사용자가
"TTL 180일이 지나면 다음엔 누가 먼저 알려주는 거냐"고 물어, 확인해보니
`run_job_phase1()`이 TTL 초과를 로그에만 남기고 화면에는 전혀 알리지 않는
상태였다. `GET /api/meta/fsc-index/status`(신규, `app/core/fsc_index.py::
get_fsc_index_status()`)를 추가해 행 수/마지막 완료 갱신 시각/TTL 초과
여부/크롤 진행 중 여부를 반환하게 했고, 프론트 `FscIndexStatusNote`
컴포넌트로 SearchPage(조건 입력 화면)와 JobsPage(호출량 표시 옆)에
노출했다 — 비어 있으면 노란 경고, TTL 초과 시에도 경고, 정상이면 마지막
갱신 시각만 조용히 표시. 상세는 상세개발계획.md §4-7 M6 체크리스트 아래
"갱신 시각 화면 노출 추가" 참고. `pytest tests/ -q` 153 passed(신규 3건
포함), `npm run build`/`npm run lint` 통과.

**A1 전수 크롤 실행 시작(2026-07-15, 같은 세션 후속) — 사용자 승인 후 백그라운드
진행 중, 이번 세션에서는 완료를 기다리지 않았다.** 위에서 남겨둔 3가지 선택지
중 사용자가 "지금 순차로 백그라운드 실행"을 선택해, `uvicorn app.main:app`을
`--reload` 없이(장시간 작업 도중 파일 변경으로 재시작되면 안 되므로) 백그라운드로
띄운 뒤 `POST /api/meta/fsc-index/refresh`(`max_pages`/`force` 미지정 — 전체
12,821페이지)를 호출해 트리거했다. 시작 15초 뒤 `GET /api/meta/fsc-index/status`로
`row_count=101`, `crawl_in_progress=true`를 확인해 정상 진행을 검증했다(동시성
도입 없이 §4-7 설계 그대로 순차 실행 — 실측 약 10.2시간 소요 예상, data.go.kr
동시 접속 정책이 미검증 상태라 이번엔 안전한 순차 방식을 그대로 택했다). **다음
세션 시작 시 가장 먼저 `GET /api/meta/fsc-index/status`로 크롤 완료 여부(`row_count`가
전체 규모에 근접했는지, `crawl_in_progress=false`, `last_completed_at` 값 존재)를
확인할 것** — 완료됐다면 CLAUDE.md의 "다음 세션에서 판단할 것" 중 (c) M5 "실전
조건 1건 풀 실행"을 새 Phase1/2 파이프라인으로 마무리하는 작업으로 이어간다.
크롤이 중단된 채 남아 있어도 `crawl_fsc_index()`가 `cache_meta`에 마지막 페이지를
체크포인트로 남기므로 같은 엔드포인트를 다시 호출하면 이어서 진행된다(force 없이).

**M1의 ★스파이크(금융위 API 커버리지 실측)는 2026-07-15 실행 완료 — 대응 1 채택 확정.**
경상남도 소재 비상장 외감법인 그라운드 트루스 표본 19건(목표 20건, DART 호출 예산 내에서
19건 확보) 중 금융위원회_기업기본정보 API(`getCorpOutline_V2`)에서 19건 모두 매칭 —
**커버리지율 100%** (채택 기준 80% 이상 충족). 스크립트 실행 중 응답 스키마 파싱 버그를
발견해 수정했다: `response.body.items`는 결과 없음일 때도 `{"item": []}`로 비어있지 않은
dict로 오기 때문에 `if items:` 같은 truthy 판정은 항상 참이 되어 커버리지율이 항상 100%로
잘못 계산되는 버그였다 — `item` 리스트 자체의 길이로 판정하도록 고쳤다
(`spike_financial_committee_coverage.py`). **대응 1의 실제 구현도 같은 날(2026-07-15)
완료했다** — 위 "M2에서 확정된 설계 판단"에서 설명한 `_resolve_candidate_profile()`
함수 경계에 "캐시 미스 시 금융위 API로 먼저 회사명 조회 → 지역 불일치 시 company.json
생략" 로직을 구현했고, 실제 API 스모크 테스트로 약 92% DART 쿼터 절감을 확인했다.

**로깅 보안 주의**: 스파이크 실행 중 `httpx`의 기본 INFO 로그가 `DART_API_KEY`/
`DATA_GO_KR_API_KEY`를 쿼리 파라미터(`crtfc_key=`, `serviceKey=`) 형태로 그대로
로그에 남기는 것을 확인했다. `dart_client.py`에 로그 필터를 추가해 마스킹 처리했다
(아래 참고). 향후 로그를 파일로 남기거나 외부로 전송할 계획이 생기면 이 필터가
적용된 로거를 통해서만 나가는지 재확인할 것.

**M6 QA/디자인 리뷰(2026-07-15, A1 크롤 대기 중 진행) — 발견한 버그 다수 수정, 설계 갭 1건은
다음 세션 판단으로 남김.** `dart-qa`/`dart-design-review` 에이전트로 M6 신규 코드를 리뷰해
아래를 고쳤다(모두 `pytest`/`npm run build`·`lint` 재확인 완료 — 상세는 각 커밋 참고):
- **Phase 2가 감사보고서 공시를 못 찾은 후보의 정합성**: `_backfill_latest_rcept_no_for_job()`이
  `latest_rcept_no`를 못 찾으면 조용히 넘어가던 것을, `parse_status=FAILED`+안내 메모로 명시하고
  `revenue_cur`/`total_assets_cur`의 A3 추정치를 지우도록 고쳤다(전에는 Phase 1 추정치가 확정치인
  것처럼 영구히 남아 B4 필터가 추정치 기준으로 판정했다). `retry-failed`가 `rcept_no IS NULL`인
  FAILED건까지 리셋하면 같은 문제가 재발하므로, 그런 건은 리셋 대상에서 제외하도록 함께 고쳤다.
- **`start-financials`/`resume`의 TOCTOU**: 상태 확인+전환을 조건부 `UPDATE` 하나로 묶어(원자적)
  버튼 연타 등으로 인한 Phase 2 중복 기동을 막았다.
- **`crawl_in_progress` 상태 버그**: 최초 완주 이후의 두 번째 이상 크롤(증분 재개/`force` 재구축)
  중에는 실제로 몇 시간 돌고 있어도 항상 `False`로 보고되던 버그를 고쳤다(`crawl_fsc_index()`가
  시작 시점에 `_META_KEY_UPDATED_AT`을 비운다). 이 수정 자체가 도입한 2차 버그(빈 문자열로
  비웠더니 `last_completed_at`이 `None`이 아니라 `""`로 응답되어 `pytest`가 바로 잡아냈다)도
  같은 자리에서 고쳤다 — `get_fsc_index_status()`가 반환 직전에 `updated_at_raw or None`으로
  정규화한다. 회귀 테스트 추가.
- **`sigungu`만 있고 `sido`가 없는 요청 검증**: `filter_local_candidates()`가 이 경우 `sido` SQL
  선필터 없이 `fsc_corp_index` 전체(최대 약 128만 행)를 메모리로 로드하는 위험이 있어,
  `RegionCondition`에 `sigungu`가 있으면 `sido`도 필수라는 Pydantic validator를 추가했다(422).
- **CandidatesView(후보 목록) UX**: 제출 버튼을 `fsc_corp_index`가 비어 있을 때(row_count=0)
  비활성화했고, 전화번호/대표자도 매출액/총자산과 마찬가지로 "미확정" 값임을 라벨로 명시했고,
  "재무정보 수집 시작" 버튼에 소요시간(수 분~수십 분) 안내를 추가했고, 후보 목록에 업종을 전혀
  보여줄 방법이 없던 것을 고쳐 `results.induty_name`에 `fsc_corp_index.sic_name`(A2가 쓰는 느슨한
  텍스트 매칭 값)을 채워 "업종 (참고용)" 컬럼으로 노출했다.
- **다음 세션에서 판단할 새 설계 갭(이번엔 고치지 않음)**: 위 업종 표시를 구현하며 발견한 것인데,
  M6 재설계 이후 `results.phone`/`ceo_name`/`induty_name`은 Phase 1(FSC 데이터)이 채운 값 그대로
  영구히 남는다 — Phase 2(`_apply_parsed_result`)는 재무 13항목/감사의견/parse_status만 덮어쓰고
  이 세 필드는 건드리지 않는다. 구 파이프라인(`run_job`, STEP3)은 `company.json`으로 이 값들을
  DART 기준으로 확정했었는데, M6에는 그 확정 단계가 없다 — "확정 결과 뷰"에서도 이 값들이 FSC
  추정치일 뿐이라는 게 화면에 전혀 표시되지 않는다(현재 `resultColumns.ts`는 "업종"/"전화번호"
  라벨을 그대로 씀). 세무회계사무소가 이 번호로 실제 연락을 시도할 수 있다는 점을 고려하면
  중요도가 낮지 않다 — 다음 세션에서 (a) Phase 2 B1에 `company.json` 확정 호출을 추가할지,
  (b) 화면에 "미확정(FSC 기준)" 라벨만 추가할지, (c) 우선순위상 보류할지 판단할 것.

**A1 크롤 중 `ReadTimeout`으로 조용히 죽는 버그 발견·수정(2026-07-16, A1 크롤 진행 중
후속 세션).** 순차 실행 중이던 A1 크롤이 자정 무렵 data.go.kr 요청 1건의
`httpx.ReadTimeout`으로 멈췄는데, `POST /api/meta/fsc-index/refresh`의
`_run_crawl()`(`app/api/meta.py`)이 예외를 잡아 로그만 남기고 백그라운드 태스크를
그대로 종료시키는 구조라 프로세스는 안 죽었지만 크롤 자체가 6시간 넘게 멈춰 있었다.
게다가 `get_fsc_index_status()`(`app/core/fsc_index.py`)의 `crawl_in_progress`
판정이 실제 태스크 생존 여부가 아니라 체크포인트 존재 여부만으로 추론하는 방식이라
멈춘 뒤에도 화면에는 계속 "진행 중"으로 표시되는 2차 문제도 있었다. 근본 원인은
`FscCorpInfoClient`(`get_corp_basic_info`/`get_summary_financial_stat`)에
`DartClient._request`(429/5xx + 타임아웃 지수 백오프 재시도, 최대
`Settings.max_retries`회)와 같은 재시도 로직이 아예 없었던 것 — 두 메서드가 각각
직접 `self._client.get()`을 호출하고 끝이었다. `FscCorpInfoClient._get_with_retry()`를
추가해 `DartClient._request`와 동일한 정책(타임아웃/네트워크 오류/429/5xx 시 지수
백오프 재시도)을 두 메서드 모두에 적용했다 — 별도 공유 유틸로 뽑아내진 않고
`FscCorpInfoClient` 안에 그대로 둔 것은 `DartClient`가 쿼터 카운터 증가 로직과
얽혀 있어 그대로 재사용하기 어려웠기 때문(과설계 방지). `crawl_in_progress`가 죽은
크롤을 "진행 중"으로 오판하는 문제는 이번엔 고치지 않았다(재시도 로직 추가로
발생 빈도 자체를 줄이는 게 우선이라고 판단) — 다음에 또 크롤이 죽어 있는데
`crawl_in_progress=true`로 나온다면 그때는 이 판정 로직도 손볼 것. 재시도 로직
반영을 위해 `uvicorn`을 재기동(체크포인트는 `cache_meta`에 남아 있어 데이터 손실
없음, 재기동 직전 `row_count=175,374`)한 뒤 `refresh`를 다시 호출해 이어서
진행 중이다. 재시도 로직 자체에 대한 신규 테스트는 추가하지 않았고(재현하려면
타임아웃을 모킹해야 해서 이번엔 생략), `pytest tests/ -q` 156 passed로 기존
테스트가 깨지지 않았음만 확인했다.

**A1 크롤 감독(supervisor) 재시도 루프 추가(2026-07-16, 같은 세션 곧바로 후속).**
위 요청/응답 단위 재시도를 반영하고 재기동한 지 약 4시간 만에 크롤이 또 죽었다 —
이번엔 `httpx.ConnectError: [Errno 11001] getaddrinfo failed`(DNS 조회 실패)가
`Settings.max_retries`(3회, 총 대기 7초 남짓)를 넘겨 `DartApiError`로 올라갔고,
`_run_crawl()`이 이를 로그만 남기고 태스크를 종료시키는 건 이전과 동일했다 —
재확인해보니 그 시점엔 이미 네트워크가 복구돼 있어(수동으로 DNS/HTTPS 연결
확인) 단순 일시 단절이었다. 요청 단위 재시도만으로는 "몇 초 이상 이어지는
끊김"을 못 버틴다는 게 확인된 셈이라, `app/api/meta.py::refresh_fsc_index`의
`_run_crawl()`을 바깥쪽 감독 루프로 바꿨다 — `crawl_fsc_index()`가 예외로
죽으면 30초 대기 후 같은 체크포인트에서 다시 호출하기를 최대 100회
반복한다(`_FSC_CRAWL_OUTER_RETRIES`/`_FSC_CRAWL_OUTER_BACKOFF_SEC`). `force`는
최초 시도에만 적용하고 이후 재시도는 항상 이전 시도가 남긴 체크포인트를 그대로
이어간다. `max_pages`를 지정한 파일럿/테스트 호출(단발성 의도)은 이 감독
루프를 타지 않고 기존처럼 1회만 시도한다 — 프로덕션 전수 크롤(`max_pages=None`)에만
적용된다. 이 로직에 대한 전용 단위 테스트는 추가하지 않았다(백그라운드 태스크
전체를 장시간 모킹해야 해서 기존에도 이 함수를 직접 테스트하는 케이스가 없었음)
— `pytest tests/ -q` 156 passed로 회귀만 재확인했다. 반영을 위해 `uvicorn`을
다시 재기동(재기동 직전 `row_count=220,116`)한 뒤 `refresh`를 재호출해 이어서
진행 중이다. **다음 세션에서 확인할 것**: 이 감독 루프 도입 이후에도 크롤이
사람 개입 없이 스스로 완주하는지(`crawl_in_progress=false`,
`last_completed_at` 값 존재) — 만약 또 죽어 있다면 이번엔 100회 재시도(총
백오프 최대 50분)까지 소진한 "영구적인" 문제(예: API 키 만료, data.go.kr
서비스 장애)일 가능성이 높으므로 로그의 마지막 예외 종류부터 확인할 것.

**A1 크롤 성능 병목의 진짜 원인 발견·수정(2026-07-16, 같은 세션 후속) — 사용자가
"진행율"을 반복 질의하며 예상 소요시간을 물어 실측하다가 발견했다.** row_count
증가 속도를 실측하니 약 3.3~4.8행/초로, 파일럿 추정(시간당 약 125,700행 ≈
34.9행/초)보다 7~8배 느려 전체 완주까지 약 58~85시간이 걸릴 상황이었다.
원인을 단계적으로 좁혀갔다:
1. **1차 가설(틀림): 건별 커밋의 fsync 비용.** `_upsert_fsc_corp_index_item()`이
   페이지(100건)마다 매 건 별도 세션을 열어 `db.commit()`하고 있어(건당
   SELECT 최대 2회 + INSERT/UPDATE + 커밋), 전체 약 128만 번의 SQLite 커밋이
   발생했다. 이를 페이지 단위 세션 1개 + 커밋 1번으로 배칭하고,
   `app/core/db.py::get_engine()`에 `PRAGMA journal_mode=WAL` +
   `synchronous=NORMAL`(엔진 `connect` 이벤트로 적용)을 추가했다. **그러나
   실제 배포 후 재측정하니 속도가 거의 그대로였다** — 이 가설은 틀렸다.
2. **2차 가설(부분 원인): 페이지 내 중복 처리용 `db.flush()`.** 배칭 후에도
   같은 페이지 내에서 같은 `crno`가 중복 등장할 경우를 대비해 아이템마다
   `db.flush()`를 넣었었는데, 이것만으로도 항목당 약 70~85ms가 들어 페이지당
   write 단계가 7~8초로 여전히 느렸다. `_dedupe_batch_items()`를 추가해
   DB를 건드리기 전에 파이썬 dict로 페이지 내 중복을 먼저 병합하도록 바꿔
   flush 자체를 없앴다 — write가 4~7초로 소폭 개선됐지만 여전히 느렸다.
3. **진짜 원인(3차, 확정): SQLite 부분(partial) UNIQUE 인덱스가 조회에서
   전혀 쓰이지 않고 있었다.** `FscCorpIndex.crno`/`fss_corp_unq_no`는 NULL/더미값을
   제외하는 부분 인덱스로 정의돼 있는데(`sqlite_where=...`), 실제 DB 파일에
   `sqlite_master`로 확인하면 인덱스는 존재하지만 `EXPLAIN QUERY PLAN`으로
   확인하니 단순 `WHERE crno = ?`(바인드 파라미터) 조회가 `SCAN fsc_corp_index`
   (전체 테이블 스캔)로 실행되고 있었다 — SQLite 쿼리 플래너는 바인드
   파라미터가 부분 인덱스의 WHERE 조건(`IS NOT NULL AND != '0000000000000'`)을
   만족하는지 정적으로 증명할 수 없으면 부분 인덱스를 아예 후보에서 제외한다.
   276,892행 기준 건당 약 100~130ms(raw sqlite3 모듈로도 동일하게 재현 —
   ORM 문제가 아니라 SQLite 자체의 동작), 페이지당 SELECT 최대 200회라 이것이
   실측 지배적 병목이었다(테이블이 더 커질수록 더 느려질 구조이기도 했다).
   `_upsert_fsc_corp_index_item()`의 두 SELECT에 부분 인덱스 조건과 동일한
   조건(`crno.isnot(None)`, `crno != "0000000000000"` 등)을 WHERE 절에
   명시적으로 반복 추가해 플래너가 `SEARCH ... USING INDEX`를 선택하도록
   고쳤다(같은 방법으로 `EXPLAIN QUERY PLAN` 재확인해 검증) — **100건 조회
   기준 약 10~13초 → 0.03초로 약 300배 개선**. 이 세 가지(커밋 배칭 + WAL/
   synchronous + 인덱스 사용 수정)를 모두 반영한 뒤 실측 속도는 페이지당
   write 0.02~0.09초, 전체 페이지당 소요시간이 사실상 API 응답 대기(약
   3.0~3.4초)에 수렴했다 — **약 17.7행/초, 잔여 약 15.8시간**으로 원래
   파일럿 추정치에 근접했다(완전히 일치하지 않는 것은 재시도/백오프 오버헤드
   등 잔여 변동 요인). `pytest tests/ -q` 156 passed(회귀 없음, `_upsert_fsc_corp_index_item`
   시그니처를 `session_factory` 대신 `db: Session`을 직접 받도록 바꾸고
   `_dedupe_batch_items()`를 신규 추가한 것에 맞춰 기존 테스트도 그대로
   통과했다). 진단에 썼던 벤치마크/타이밍 스크립트는 스캐치패드에만 남기고
   커밋하지 않았다(기존 관행과 동일). **교훈**: SQLite 부분 인덱스는 바인드
   파라미터 조회에서 기본적으로 활용되지 않으므로, 이 프로젝트에서 부분
   인덱스를 새로 추가할 때는 조회 쿼리에도 인덱스 조건을 명시적으로 반복
   추가해야 한다는 점을 항상 함께 고려할 것.

**A1 전수 크롤 완주 완료(2026-07-17).** `fsc_corp_index` 전수 크롤이 최종 완료됐다 —
`last_completed_at="2026-07-17T06:58:43"`, `row_count=633,968`, `is_stale=false`.
세션 시작 시점에 백엔드 프로세스 자체가 꺼져 있어(장시간 무인 실행 중 재부팅 등으로
추정, 로그상 원인은 확인하지 못함) 크롤도 함께 멈춰 있었는데, `uvicorn`을
재기동하고 `POST /api/meta/fsc-index/refresh`를 다시 호출하니 감독 루프가
기존 체크포인트(페이지 11,966)부터 정상적으로 이어서 완주했다 — 별도 코드 수정
없이 기존 재개 설계(체크포인트 기반 resume)가 그대로 동작함을 확인했다.
최종 `row_count`(633,968)가 원본 총량(`totalCount=1,282,065`)의 절반 정도인
것은 버그가 아니라 예정된 동작이다 — `crno`/`fss_corp_unq_no` 기준 병합
비율이 크롤 전 구간에서 약 49% 안팎으로 일정하게 유지됐다(페이지 11,966
시점 49.12%, 페이지 12,100 시점 49.1%, 최종적으로도 동일 수준) — 여러
소스기관에 중복 등록된 동일 회사가 한 행으로 upsert되기 때문. 이제
`fsc_corp_index`가 채워졌으므로 Phase 1 Job이 더 이상 "fsc_corp_index가
비어 있습니다" FAILED로 끝나지 않는다.

**M5 "실전 조건 풀 실행"을 새 Phase1/2 파이프라인으로 재개(2026-07-17, 크롤 완주 직후)
— A3 단계에서 공공데이터포털 자체 일일 쿼터 소진 발견, Job #13 취소 후 Job #14로
범위 축소.** 기존 Job #8/#9(구 파이프라인, STEP3 병목으로 취소)와 동일한 조건
(경상남도, 매출액 60~150억)으로 Job #13을 생성해 A2(지역 로컬 필터, 24,869개사
통과)까지는 정상 동작을 확인했으나, A3(`getSummFinaStat_V2`로 매출액/총자산
스크리닝) 진행 중 로그에서 `HTTP 429: API token quota exceeded`가 대량 발생하는
것을 발견했다 — **공공데이터포털의 `GetFinaStatInfoService_V2`(금융위 재무정보
API)는 DART의 일일 20,000건 한도와 별개로, data.go.kr 자체의 일일 호출 쿼터를
따로 가지고 있고 이번 세션에서 소진됐다**(A1 크롤이 쓰는 `getCorpOutline_V2`는
동일 계정이라도 별도 활용신청 건이라 무관 — A1은 정상 완주했다). A3는 실패를
"조회 실패 → 안전하게 통과"로 설계돼 있어 Job 자체는 끝나지만, 쿼터 소진 이후
남은 후보 전부가 스크리닝 없이 Phase 2로 넘어가게 되고(재설계가 없애려던 문제
재현) 재시도 백오프(1초→2초→4초) 때문에 실패 처리조차 느려서(24,869개사 기준
잔여 약 4~5시간 추정) 시간 낭비였다. 사용자 판단으로 Job #13을 취소하고
`region.sigungu`를 `["김해시"]`로 좁힌 Job #14를 새로 생성했다 — 후보 수 자체가
작아지면 쿼터 소진 상태에서도(전부 실패 처리라 해도) 그리드아웃 시간이 수십 분
내로 끝나 오늘 M5를 마무리할 수 있다는 판단(스크리닝 품질 저하는 Phase 2 B4가
실제 DART 원문으로 최종 검증하므로 결과 정확성에는 영향 없음). **다음 세션에서
판단할 것**: (a) `getSummFinaStat_V2` 실패를 429 응답 시 즉시(백오프 없이) 통과
처리하도록 최적화할지(현재는 성공/실패 무관하게 동일한 3회 재시도 정책을 그대로
씀 — 쿼터 소진이 확실한 429 "token quota exceeded" 메시지는 재시도해도 절대
성공하지 않으므로 낭비), (b) data.go.kr 쿼터 리셋 시각(추정 자정, 미확인)과
실제 일일 허용량(오늘 처리량으로 역산 가능)을 확인해 Phase 1 실행 전 사전
경고를 화면에 노출할지, (c) 대규모 지역(전체 도 단위) Phase 1 실행은 여러 날에
걸쳐 나눠 실행하는 게 기본 전제가 되어야 하는지.

**M5 실전 실행 중 실제 버그 발견·수정 — STEP5(파싱) 단일 문서 예외가 Job 전체를
FAILED로 죽임(2026-07-17).** Job #14(김해시 축소판) Phase 2가 945건 중
대부분(약 900여 건)을 정상 처리한 뒤, 특정 감사보고서 원문 1건에서
`lxml.etree.XMLSyntaxError: Input is not proper UTF-8, indicate encoding !
Bytes: 0xB0 0xA8 0xBB 0xE7`(EUC-KR로 인코딩된 "감사" 두 글자의 바이트 그대로 —
즉 이 원문은 UTF-8이 아니라 EUC-KR 계열로 인코딩돼 있었다)가 발생했는데,
`app/core/pipeline.py::_run_financial_parsing()`의 루프 안에서
`parse_xml_financials()` 호출에 try/except가 전혀 없어서 이 예외가 그대로
`run_job_phase2()`까지 전파돼 **Job 전체가 FAILED로 끝났다** — CLAUDE.md에 이미
명시된 핵심 설계 원칙("파싱은 100% 자동화되지 않는다 ... parse_status를
결과마다 남기고")과 정면으로 어긋나는 실제 회귀였다(`xml_parser.py`의
`recover=True`는 XML 구조 오류는 복구하지만, 인코딩 자체가 깨진 경우는 애초에
파싱 진입 단계에서 fatal error로 처리되어 복구 대상이 아니다). 이미 처리된
결과들은 `_apply_parsed_result()`가 건별로 즉시 commit하므로 유실되지 않았고,
멈춘 지점 이후 미처리 건만 남은 상태였다.
**수정**: `_run_financial_parsing()`의 파싱+감사의견/회계연도 추출 블록 전체를
try/except로 감싸 — 어떤 예외든(이번 EUC-KR 건 포함, 향후 다른 원인이라도)
해당 1건만 `parse_status=FAILED`(`parse_note`에 예외 메시지 기록)로 남기고
루프를 계속 진행하도록 고쳤다. 인코딩 자체를 감지해 재시도하는 등의 근본
파서 보강(예: EUC-KR/CP949 폴백 디코딩)은 이번엔 하지 않았다 — 이번 발견의
핵심은 "원문 서식이 다양해 파싱이 실패할 수 있다"는 것 자체가 아니라 "실패가
Job 전체를 무너뜨려선 안 된다"는 이미 합의된 원칙이 지켜지지 않고 있었다는
것이었고, 그 원칙만 복원하면 이 EUC-KR 문서는 정상적으로 `parse_status=FAILED`
1건으로만 기록되고 나머지는 전혀 영향받지 않는다. `pytest tests/ -q` 156
passed(회귀 없음)로 확인 후 백엔드를 재기동하고 `POST /api/jobs/14/resume`으로
Phase 2를 재개했다 — 결과는 다음 기록 참고. **다음 세션에서 판단할 것**: (a)
EUC-KR 등 비UTF-8 원문이 실제로 얼마나 흔한지(M3 검수 30건 표본에는 없었던
새로운 패턴 — dart-parser 에이전트가 표본을 늘려 재조사할 만한 가치가 있음),
(b) 발견되면 `xml_parser.py`에 인코딩 자동 감지/폴백 디코딩을 추가해 이 문서도
`FAILED`가 아니라 실제로 파싱되게 할지.

**M5 "실전 조건 풀 실행" 완료(2026-07-17, 새 Phase1/2 파이프라인 기준).** 위
파싱 예외 수정을 반영한 뒤 Job #14(경상남도 김해시, 매출액 60~150억,
history_years=4)를 `POST /api/jobs/14/resume`으로 재개해 Phase 2(B1~B5,
1,233건 대상)까지 끝까지 정상 완주했다(`status=DONE`). 최종 결과:
- `results` 총 1,835건(Phase 1에서 corp_code 확정된 후보 전체)
- `parse_status` 분포: OK 724 / PARTIAL 179 / FAILED 932(이 중 890건은
  애초에 해당 기간에 감사보고서 공시 자체가 없어 B1 단계에서 바로 FAILED
  처리된 건, 나머지 42건이 실제 원문을 열어봤지만 파싱이 안 된 건 — EUC-KR
  인코딩 문서 1건 포함, Job을 죽이지 않고 정상적으로 이 42건 각각의
  FAILED로만 기록됨을 확인했다)
- `excluded_by_revenue`: 통과 1233 / 제외 602
- `financial_snapshots`(STEP7 재무이력): 1,044건 적재

이로써 M5의 마지막 미완료 항목("실전 조건 풀 실행")이 새 Phase1/2 설계로
완료됐다 — 기존 구 파이프라인(Job #8/#9)이 STEP3 병목(초당 0.22건, 완주까지
약 49시간 추정)으로 취소할 수밖에 없었던 것과 대조적으로, 새 파이프라인은
지역 로컬 필터(A2, DB 쿼리라 즉시)로 24,869→7,160(김해시 축소 시)개사까지
줄인 뒤 진행해 전체가 완료됐다(다만 오늘은 A3 자체가 data.go.kr 쿼터
소진으로 스크리닝 없이 통과하는 상태였다는 점은 위 기록 참고 — 그래도
결과 정확성은 Phase 2 B4가 실제 원문으로 보장한다).

**우선순위 1 작은 개선 2건 반영(2026-07-17, 같은 세션 후속).** Job #13에서
겪은 data.go.kr 쿼터 소진 문제와 "확정 결과 뷰"의 미확정 항목 노출 문제,
두 가지 후속 조치를 처리했다.
- **`getSummFinaStat_V2` 429(쿼터 소진) 즉시 통과 처리**: `FscCorpInfoClient.
  _get_with_retry()`(`app/core/dart_client.py`)가 429 응답 본문에 "quota"
  또는 "LIMITED_NUMBER_OF_SERVICE_REQUESTS" 문구가 있으면 백오프 재시도 없이
  즉시 예외를 던지도록 고쳤다(`_is_quota_exceeded_response()` 헬퍼 추가) —
  기존에는 매 요청마다 1초+2초 백오프 후 포기했는데(건당 최대 약 3초),
  data.go.kr 자체 쿼터가 소진되면 이후 모든 호출이 반드시 같은 429를
  반환하므로 재시도가 항상 무의미했다. `_fetch_financial_stat_with_retry()`
  (`app/core/fsc_index.py`)는 이 예외를 그대로 받아 기존처럼 "조회 실패,
  안전하게 통과"로 처리한다(호출부 로직은 변경 없음). 대규모 지역(예:
  경남 전체, 24,869개사) 조건에서 쿼터가 도중에 소진되면 잔여 처리 시간이
  수 시간 단위로 줄어들 것으로 기대된다(정확한 재실측은 다음 대규모 실행
  때 확인). 재시도 로직은 timeout/네트워크 오류에는 여전히 기존 백오프를
  그대로 적용한다 — 이번 변경은 "쿼터 소진임이 명백한 429"만 예외로 뺀
  것이다. 이 프로젝트가 `_get_with_retry` 계열 로직에 신규 단위 테스트를
  추가하지 않는 기존 관행(2026-07-16 재시도 로직 도입 시에도 동일하게
  생략)을 따라 이번에도 전용 테스트는 추가하지 않았고, `pytest tests/ -q`
  156 passed로 회귀만 재확인했다.
- **phone/ceo_name/induty_name "미확정(FSC 기준)" 라벨 추가**: M6 QA 리뷰에서
  남겨둔 설계 갭("확정 결과 뷰에서도 이 값들이 Phase 1(FSC) 추정치일 뿐이라는
  게 전혀 표시되지 않는다") 중 옵션 (b)를 택해 처리했다 — Phase 2 B1에
  `company.json` 확정 호출을 추가하는 옵션 (a)는 이번엔 하지 않았다(더 큰
  판단이 필요해 보류). `frontend/src/util/resultColumns.ts`의
  `BASIC_COLUMNS`에서 세 컬럼 라벨을 "전화번호 (미확정·FSC 기준)" 등으로
  바꿨고, `backend/app/exporters/excel.py`의 `RESULT_COLUMN_LABELS`도 같은
  취지로 "전화번호(미확정,FSC기준)" 등으로 맞췄다(Excel/CSV로 내려받아 실제
  연락에 쓰일 수 있는 파일이라 화면과 동일하게 라벨링). CandidatesView(Phase 1
  후보 목록 뷰)는 이미 "(미확정)"/"(참고용)" 라벨이 있었으므로 그대로 두고
  건드리지 않았다. `pytest tests/ -q` 156 passed(export 테스트는
  `RESULT_COLUMN_LABELS` dict 자체를 참조해 하드코딩 문자열에 의존하지 않아
  라벨 문구 변경에 영향받지 않음), `npm run build`/`npm run lint` 통과.
- **EUC-KR 등 비UTF-8 원문 재조사는 이번 세션에서 보류했다** — 사용자
  판단으로 "당장 조치하지 않고 앞으로 몇 차례 실행에서 `parse_note`에 인코딩
  관련 실패가 얼마나 쌓이는지 관찰"하기로 했다. 다음 세션에서 Job 실행 결과의
  `parse_note`를 확인해 EUC-KR류 실패 빈도가 늘고 있으면 그때 `xml_parser.py`
  인코딩 자동 감지/폴백 디코딩 추가를 재검토할 것.

**"우선순위 2" 판단 사안 3건 처리(2026-07-17, 같은 세션 후속).** 위에서 열어둔
data.go.kr 쿼터 한도 확인/취소된 Job 정리/제품 Phase 2(전단지 생성) 착수 시점
3가지를 사용자에게 직접 물어 결정했다:
- **취소된 Job(#1,2,3,4,8,9,10,12,13)은 그대로 둔다** — 개발/스파이크 과정에서
  생긴 기록이라 삭제하지 않고 DB에 이력으로 남긴다(코드 변경 없음).
- **제품 Phase 2(전단지/진단자료 생성) 착수는 아직 이르다** — Phase 1 범위의
  미해결 사안(아래 두 항목)을 먼저 정리한 뒤 판단하기로 했다.
- **data.go.kr 쿼터 한도는 Claude가 알 수 없어(로그인 필요) 사용자가 직접
  마이페이지에서 확인하기로 했다** — "마이페이지 > 활용신청 현황 >
  금융위원회_기업 재무정보 API(`GetFinaStatInfoService_V2`)"의 일일 트래픽
  허용량. **다음 세션 시작 시 이 값을 확인했는지 먼저 물어볼 것** — 확인됐다면
  아래 "넓은 지역 검색 시 다일간 실행" 항목의 경고 UX 여부를 그 숫자를 근거로
  구체적으로 판단할 수 있다.

이어서 EUC-KR 재조사 건과 "넓은 지역(도 단위) Phase 1 실행이 하루 만에 안
끝날 수 있는 문제"(§8 M6 QA 리뷰 이후 계속 열려 있던 사안 — data.go.kr 자체
일일 쿼터 소진 시 A3 사전 스크리닝이 건너뛰어지고 안전하게 통과 처리되므로
**최종 결과 정확도에는 영향 없음**, Phase 2가 실제 DART 원문으로 항상
재검증하기 때문. 다만 사전 필터링 효과가 줄어 Phase 2 처리량/시간이 늘 수
있음)을 사용자에게 알기 쉽게 설명한 뒤, **두 사안 모두 지금 코드를 바꾸지
않고 계속 관찰/보류하기로 재확인했다** — EUC-KR은 위와 동일한 이유(표본이
아직 1건뿐), 다일간 실행 경고 UX는 위 data.go.kr 쿼터 한도 숫자가 나오기
전까지는 "얼마나 자주/심각하게 발생하는지"를 구체적으로 판단할 근거가 없어서다.
**다음 세션에서 판단할 것**: (a) 사용자가 확인한 data.go.kr 쿼터 한도 숫자를
근거로 "넓은 지역 조건 선택 시 예상 소요시간/다일간 분할 필요 여부" 경고
UX를 SearchPage에 추가할지, (b) 그 사이 실행에서 `parse_note`에 EUC-KR류
실패가 더 쌓였는지 확인해 인코딩 자동 감지/폴백 디코딩 착수 여부 재검토,
(c) 위 두 가지가 정리되면 제품 Phase 2(전단지 생성) 착수를 다시 검토.
**→ 같은 날 후속 세션에서 (c)는 "착수 시점 검토"가 아니라 "범위 제외 확정"으로
결론났다 — 아래 "Phase 2(전단지 생성) 범위 제외 확정" 참고.**

**→ (a) 같은 세션 후속으로 구현 완료(2026-07-17).** 사용자가 "너가 확인해봐"라고
요청해, data.go.kr 로그인 없이도 공식 API 상세페이지(공개 정보)에서 확인 가능한
`GetFinaStatInfoService_V2`의 **개발계정 기본값(일일 10,000건)**을 WebFetch로
확인했다(운영계정으로 승급했는지는 여전히 미확인이라 "최소 보장 하한"으로
다룸 — 실제 계정이 더 넉넉하면 경고가 보수적으로만 어긋나고 결과 정확도에는
영향 없음). 이 숫자를 근거로 신규 API `POST /api/meta/candidates-preview`
(§6, `app/api/meta.py`)를 추가했다 — Phase 1 A2(`filter_local_candidates`,
로컬 DB 쿼리만 사용, 외부 API 호출 없음)만 실행해 후보 수를 즉시 계산하고,
10,000건을 넘으면 `exceeds_daily_quota=true`+`estimated_days`(올림 나눗셈)를
함께 반환한다. **M4 시점에는 이런 미리보기가 후보 전체의 DART company.json
호출을 요구해 스코프 제외됐었는데(§7-1 원 계획), M6 재설계로 A2가 순수 로컬
쿼리가 되면서 처음으로 실현 가능해졌다.** SearchPage(`frontend/src/pages/
SearchPage.tsx`)가 시도/시군구/업종 선택 시 400ms 디바운스로 이 API를 호출해
Alert로 "예상 후보 수 약 N개사"(+쿼터 초과 시 "약 D일에 걸쳐 나눠 진행될 수
있습니다, 결과 정확도에는 영향 없음")를 실시간 표시한다. 실제 데이터로 검증
(경남 전체 24,869개사 → 3일 경고, 김해시로 좁히면 7,160개사 → 정상 문구로
전환)했고, Playwright로 실제 브라우저에서 두 경로 모두 콘솔 에러 없이 정상
렌더링됨을 확인했다. 테스트 중 백엔드 프로세스가 이전 세션부터 계속 떠 있던
구버전 코드 상태(port 8000, RUNNING/PENDING Job 없음 확인 후 재기동)였던 것을
발견해 재기동했다 — 앞으로 코드 변경 후 스모크 테스트 시 이 점 주의(백엔드가
장시간 켜져 있는 경우 `--reload` 없이 떠 있으면 재기동해야 새 코드가 반영됨).
신규 단위 테스트 2건 추가(`test_api_meta.py`,
`test_get_candidates_preview_counts_local_matches_without_quota_warning`/
`test_get_candidates_preview_flags_quota_exceeded`) — `pytest tests/ -q`
158 passed, `npm run build`/`npm run lint` 통과. (b) EUC-KR 재조사와
(c) 제품 Phase 2 착수는 이번 세션에서 다루지 않았다 — 다음 세션에서 계속
판단할 것.

**Phase 2(전단지 생성) 범위 제외 확정(2026-07-17, 같은 세션 후속).** 위에서
계속 "다음 세션에서 판단할 것"으로 열어두고 있던 "제품 Phase 2(전단지/진단자료
생성) 착수 시점"을 사용자가 직접 정리했다 — **Phase 2는 이 프로젝트의 범위에
포함하지 않기로 확정**했다(착수 시점을 미루는 것이 아니라 아예 제외). 이에 따라
PRD.md/상세개발계획.md/CLAUDE.md 전반의 "Phase 1(현재 범위)/Phase 2(전단지
생성, 추후 착수)" 2단계 프레이밍을 제거하고, 이 프로젝트를 처음부터 "데이터
수집기 + 결과 조회 웹앱"으로 완결된 단일 범위로 재서술했다 — PRD.md §1/§2/§3/
§8/§9(§9는 전단지 관련 논의만 있어 섹션째 삭제), 상세개발계획.md §10("Phase 2
대비 설계 포인트", 섹션째 삭제) + §4-1의 크레탑/KISLINE 대안 검토 문구,
`.claude/agents/dart-design-review.md`의 "Phase 2 확장 여지" 체크리스트 항목을
모두 정리했다. **주의**: 이 "Phase 2"는 상세개발계획.md §4-7/M6의 파이프라인
내부 2단계 구조(`jobs.phase` = `CANDIDATES`/`FINANCIALS`, 코드베이스 전반에서
여전히 "Phase 1"/"Phase 2"로 부르는 후보확보/재무크롤링 단계)와는 **이름만
같고 무관한 개념**이다 — 그 기술적 Phase 1/Phase 2 구조는 이번 정리에서
전혀 건드리지 않았다(파이프라인 코드/문서 모두 그대로). 코드 변경은 없음(문서만
수정) — `results` 테이블의 당기순이익/유동부채 등 필드는 이미 수집 중이고
그 자체로 유용한 재무 데이터이므로 계속 유지한다(Phase 2 대비용이라는 설계
근거만 제거했을 뿐 필드 자체는 삭제하지 않았다).

**Phase 1 업종(A2) 필터 회귀 발견·수정(2026-07-18) — 중분류 단독 선택 시
0건, 대분류 포함 시 사실상 무필터라는 이중 버그.** 사용자가 실제 화면에서
"경상남도 창원시 + 업종 1개(식료품 제조업, 코드 "10")"로 Job #15를 만들었더니
후보가 0건이었고, 반면 "경상남도 창원시 + 업종 26개(대분류 "C" 포함)"로 만든
Job #16은 227건이 나왔다 — 둘 다 정상이 아니었다. 원인은
`app/core/fsc_index.py::_industry_labels_for_codes()`/`_sic_name_matches()`가
FSC `sic_name`을 `industry_data.py`의 KSIC **중분류** 라벨("식료품 제조업")과
그대로 부분 문자열 매칭한다는 점인데, 실측해보니 FSC의 `sic_name`은 그보다
훨씬 세분화된 텍스트("곡물 도정업", "배합 사료 제조업", "떡류 제조업" 등)라
중분류 라벨이 문자 그대로 등장하는 경우가 거의 없었다(창원시 5,691개사 중
"식료품 제조업" 문자열을 포함하는 곳 0개, 직접 쿼리로 실측 확인). 반대로
대분류("C")가 선택되면 그 이름 "제조업"이 라벨에 포함되는데, 이 라벨은
거의 모든 제조업 회사의 `sic_name`에 부분 문자열로 걸려 사실상 업종
필터가 무력화된다. **수정**: `_industry_labels_for_codes()`가 자식(중분류)
코드가 매칭될 때 그 자식의 구체적 라벨뿐 아니라 소속 대분류의 라벨도 함께
추가하도록 고쳤다 — 대분류를 직접 선택했을 때와 동일하게 "해당 대분류
전체"로 느슨하게 통과시켜, 중분류 단독 선택도 최소한 대분류 수준에서는
일관되게 동작한다(문서 docstring에 이미 명시된 "정밀 확정이 아닌 1차
스크리닝" 원칙 안에서의 수정 — 정밀 확정은 여전히 Phase 2가 담당해야
하지만, 아래 문단에서 보듯 Phase 2도 현재는 이를 하지 않는다). 회귀 테스트
추가: `backend/tests/test_fsc_index.py::
test_filter_local_candidates_matches_narrow_industry_selection_against_fine_grained_sic_name`.
`pytest tests/ -q` 163 passed(신규 1건 포함). 코드 변경 반영을 위해 port
8000 백엔드 프로세스를 재기동했다(RUNNING Job 없음을 확인한 뒤 진행).
**남은 한계(이번엔 고치지 않음, 다음 세션 판단 필요)**: 이 수정으로도
**중분류 단위의 실제 정밀 필터링은 여전히 불가능하다** — 이제는 어떤
중분류를 선택하든 결과적으로 "해당 대분류 전체"가 통과한다(예: "식료품
제조업"만 선택해도 "아파트 건설업"은 제외되지만 "곡물 도정업"/"자동차
부품 제조업" 등 다른 제조업 세부업종도 모두 함께 통과) — 사용자가 세부
업종으로 좁혀 선택하는 UI 자체의 실효성이 대분류 수준으로 제한된다는
뜻이다. `_industry_labels_for_codes()`의 docstring이 이미 "정밀 확정은
Phase 2 B1의 DART company.json이 담당"이라고 명시하고 있지만, 실제로는
Phase 2(B1~B5, `run_job_phase2`)가 `company.json`을 호출하지 않고
`corp_code`로 바로 감사보고서 원문을 내려받을 뿐이라 이 약속이 지켜지고
있지 않다(M6 QA 리뷰에서 남겨둔 phone/ceo_name/induty_name "미확정" 갭과
같은 뿌리). 다음 세션에서 판단할 것: (a) Phase 1이 A2/A3 통과 후 확정한
소규모 후보 집합(수백~수천 건 수준, 이미 STEP3 병목 문제와 무관한 규모)에
한해 `company.json`을 호출해 실제 `induty_code`로 정밀 재검증하고
`excluded_by_industry` 컬럼을 신설할지, (b) 중분류 선택은 애초에 "대분류
수준의 참고용 좁히기"일 뿐이라고 화면에 명시하고 현재 동작을 그대로
받아들일지.

**CandidatesView(Phase 1 후보 목록) "선택 취소" 기능 추가(2026-07-18).** 사용자
요청으로, 후보 확정(Phase 1) 후 재무정보 수집(Phase 2)을 시작하기 전에 특정
회사를 후보에서 제외할 수 있는 기능을 추가했다. `results.excluded_manually`
컬럼(신규, `excluded_by_revenue`/`excluded_by_assets`와 동일한 int 0/1 패턴)을
추가하고, `PATCH /api/jobs/{id}/results/{result_id}/exclude`(body:
`{excluded: bool}`)로 자유롭게 켰다 껐다 토글할 수 있게 했다 — `job.phase !=
CANDIDATES`면 400으로 거부한다(이미 확정 처리에 들어간 결과를 건드리지
못하게). **실제 삭제는 토글 시점이 아니라 `POST /api/jobs/{id}/start-financials`
호출 시점에 일괄 수행**하도록 설계했다(`db.execute(delete(Result).where(...
excluded_manually == 1))`를 상태 전환 UPDATE 직후에 추가) — 이 시점엔 아직
`financial_snapshots`가 생기지 않아 results만 지우면 되고, 무엇보다 Phase 2
파이프라인(B1~B5, `_backfill_latest_rcept_no_for_job`/`_run_document_download`/
`_run_financial_parsing`/`_run_history_collection`)을 전혀 수정할 필요가
없다는 게 이 설계의 핵심 — 남은 `results`만 대상으로 기존 로직이 그대로
동작한다. 프론트 `CandidatesView.tsx`는 후보 목록 표에 "포함" 체크박스
컬럼을 추가해(기본 체크됨) 체크 해제 시 `setResultExcluded()`로 즉시 토글
API를 호출하고, 낙관적으로 해당 행만 로컬 state에서 갱신한다(dimmed +
취소선으로 표시, 페이지 이동해도 서버에 저장된 상태라 유지됨). 신규 테스트:
`backend/tests/test_api_results.py`(토글 성공/phase=FINANCIALS 거부/404 3종),
`backend/tests/test_api_jobs.py::test_start_financials_deletes_manually_excluded_results`.
`pytest tests/ -q` 167 passed, `npm run build`/`npm run lint` 통과. 실제
port 8000 백엔드(RUNNING/PENDING Job 없음 확인 후 재기동, 스키마 마이그레이션은
기존 `_ensure_columns()` ad-hoc 방식 그대로 재사용) + 기존 실제 Job #16(238건
확정 후보)으로 Playwright 스모크 테스트를 수행해 체크박스 토글 → 행 표시
변경 → 원복까지 콘솔 에러 없이 정상 동작함을 확인했다(테스트 후 원 상태로
복원, DB에 잔여 변경 없음).

**현금흐름표 파싱 + 원문 섹션 열람 확장 — 설계 확정·문서화 완료(2026-07-19,
설계만, 구현 전).** 사용자가 "현금흐름표(주석 포함)도 DART에서 제공할 수
있잖아?"라고 물어 fixtures 30건을 실측 확인한 결과, **이미 STEP 4/B2에서
내려받는 `document.xml` 안에 현금흐름표 본표(`ACLASS="FINANCE"` 테이블,
영업/투자/재무활동 구조)와 주석 구간이 함께 들어 있어 추가 API 호출/쿼터
0건으로 확장 가능**함을 확인했다(주석이 아예 없는 원문도 실측 존재 —
`20260630001111` "주석을 제시하지 아니함", 정상 케이스로 처리해야 함).
사용자가 범위를 확정했다: ① 현금흐름표 4항목(`cf_operating`/`cf_investing`/
`cf_financing`/`cf_ending_cash`) 당기·전기 파싱, ② 다년치 이력
(`financial_snapshots`)에도 동일 4항목 포함, ③ 주석 원문 통째 보기, ④
재무상태표/손익계산서/현금흐름표 원문 보기. 핵심 설계 판단: CF는
`determine_parse_status()` 판정에 반영하지 않는 best-effort 항목(기존 완료
Job의 OK/PARTIAL 재분류 방지), 원문 열람은 신규 API
`GET .../document-sections/{section}`(bs|is|cf|notes)이 로컬 문서 캐시에서
on-demand로 섹션을 잘라 서버 조립 HTML(이스케이프 처리)로 반환하는 방식
(DART 뷰어 섹션 딥링크는 rcept_no만으로 조립 불가라 자체 렌더링 채택, 기존
DART 전체 보고서 링크는 유지). **이번 세션에서는 사용자 지시("일단 수정은
하지 말고 개발계획에 반영")에 따라 문서만 갱신했고 코드 변경은 없다** —
상세 설계는 [상세개발계획.md §4-8](상세개발계획.md)(열린 질문 3건 포함:
CF alias 실측 범위/기존 완료 Job 소급 재파싱/손상 원문 표시), 구현
체크리스트는 §8 M7(전체 미완료), 스키마 주석은 §5, API 표는 §6, 화면
설계는 §7-3, PRD.md §2/§3-2에도 같은 취지로 반영했다.

**M7(현금흐름표 파싱 + 원문 섹션 열람) 구현 완료(2026-07-19).** 위 §4-8
설계를 그대로 구현했다. 코드 변경 전 첫 단계로 **CF 계정 라벨을 fixtures
30건으로 실측**했다 — CF 섹션 보유 19건 전부에서 간접법 구서식 "영업활동
으로 인한 현금흐름"/"투자활동..."/"재무활동..."/"기말의 현금" 계열이
19/19(100%)로 일관됐고, "기말의 현금(Ⅳ+Ⅴ)"처럼 소계 산식이 괄호로 붙은
표기 1건만 예외라 `normalize_account_label`에 **산식 접미어 제거**
(`_FORMULA_SUFFIX_RE` — 괄호 안이 로마숫자·숫자·공백·"+"로만 이뤄지고 "+"를
최소 1개 포함할 때만 제거, "당기순이익(손실)" 등 한글 괄호는 보존)를 추가해
해결했다. 구현 내역:
- **파서(`base.py`/`xml_parser.py`)**: `CF_ACCOUNT_NAME_ALIASES`(실측 계열 +
  신서식 방어적 추가) + `CF_FINANCIAL_FIELDS` 신설, "현금흐름표"를
  `_OTHER_TITLE_MARKS`(종료 마커)에서 새 섹션 `"cf"`로 승격,
  `_extract_section(aliases=...)`로 alias 사전을 파라미터화해 CF 구간에서만 CF
  사전을 쓴다. **`determine_parse_status()`는 무변경** — fixtures 30건의
  OK/PARTIAL 분포가 CF 추가 전후 완전히 동일함을 실측 재확인했다(CF는
  `found_any_table`/`DIRECT_FINANCIAL_FIELDS`에 관여하지 않음). CF 미확보는
  `found_any_table`이 True일 때만 `parse_note`에 "현금흐름표 미확보(best-effort)"
  로 부기(미첨부 건은 중복 방지).
- **DB/파이프라인**: `results` 8컬럼(`cf_*_cur/prv`)·`financial_snapshots`
  4컬럼(`cf_*`)을 모델 + `run_schema_migrations()` ad-hoc ALTER로 추가,
  `_apply_parsed_result()`/`_upsert_financial_snapshot()` 적재 필드에
  `CF_FINANCIAL_FIELDS`를 추가(STEP 7은 파서 확장이 자동 전파). 실제 기동으로
  `dart_search.db`의 두 테이블에 CF 컬럼이 ALTER된 것을 확인했다.
- **원문 섹션 열람 API**: 신규 `app/parsers/document_sections.py` +
  `GET /api/jobs/{id}/results/{result_id}/document-sections/{section}`
  (`bs|is|cf|notes`, `?rcept_no=`). 실측 구조상 BS/IS/CF는 `<TABLE-GROUP>`,
  주석은 `<SECTION>`에 담기므로 "섹션 마크 첫 `<TITLE>`의 부모 컨테이너를
  통째로 화이트리스트 HTML(텍스트 전부 이스케이프, COLSPAN/ROWSPAN만 통과)로
  조립"한다 — 원문 마크업 미통과라 XSS 안전. 캐시 없음 404, PDF는 안내,
  주석 미제시/섹션 미첨부는 `available=false`+안내(에러 아님), `?rcept_no=`는
  해당 result의 최신 공시 또는 `financial_snapshots` 이력 공시만 허용.
- **프론트(`resultColumns.ts`/`ResultDetailDrawer.tsx`/신규
  `DocumentSectionModal.tsx`)**: CF 8컬럼(`CASH_FLOW_COLUMNS`, 기본 숨김·토글
  노출), 당기·전기 표에 CF 4행 + 이력 표에 CF 4행, "원문 보기" 버튼 4개 +
  이력 표 연도별 "원문" 링크 → 섹션 탭 전환이 되는 Modal(서버 조립 HTML을
  `dangerouslySetInnerHTML`로 렌더).
- **소급 재파싱은 하지 않음(확정, §4-8 열린 질문 2)** — 신규 Job 실행분부터만
  CF를 채운다(관리자 전체 재파싱 트리거 미추가). 원문 섹션 열람은 로컬 캐시
  기반이라 기존 Job(#18 등)에서도 즉시 동작함을 실측 확인했다(DB CF는 NULL이어도
  원문 현금흐름표가 정상 렌더링).
- **검증**: `pytest tests/ -q` **180 passed**(기존 167 + 신규 13: CF 파서
  실측/산식 접미어/document-sections 6종), `npm run build`(tsc)/`npm run
  lint`(oxlint) 통과. 실제 백엔드(port 8000, 캐시 문서 1,454건) 재기동 후 실
  result(#3042, ㈜와이케이건기)로 document-sections API(cf/bs/notes 정상, 잘못된
  섹션 400, 타/자기 rcept_no 검증 404/200) + Playwright로 결과 상세 Drawer의
  CF 행·현금흐름표 원문 모달 렌더링을 콘솔 에러 0으로 확인했다.
- **구현 중 발견**: §4-8 배경이 CF 예시로 든 `20260630000651`은 실제로는
  재무상태표/손익계산서 `<TITLE>` 자체가 없는 특수 서식(기존 BS/IS 파서도
  못 잡는 문서)이라 CF도 못 잡는다 — 예시가 부정확했다(§4-8 구현 메모에 기록).
  백엔드는 재기동해 새 코드로 떠 있다(port 8000).

**EUC-KR 등 비UTF-8 원문 재조사 완료 + 인코딩 폴백 구현(2026-07-19).** 우선순위
2에서 "표본이 1건뿐이라 관찰 보류" 상태로 여러 세션 열려 있던 사안을 종결했다.
수동 관찰(Job 실행 결과의 `parse_note`) 전략은 데이터가 안 쌓이고 있었다 —
현재 `dart_search.db`엔 실제 재무 파싱을 돌린 Job이 #18(5건)뿐이고 EUC-KR
사례가 나왔던 Job #14 데이터는 리셋돼 없었다. 대신 **이미 받아둔 로컬 문서
캐시(`data/documents`, 1,453건)를 직접 스캔**해(쿼터 0) 실측했다:
- **1,453건 전부 XML 선언부는 `encoding="utf-8"`이지만, 실제 바이트가
  EUC-KR/CP949인 원문이 64건(약 4.4%)** — 1회성 예외가 아니라 체계적 패턴이고,
  선언부가 거짓이라 선언부만 믿으면 절대 못 잡는다. 이 64건 전부 `bytes.decode('cp949')`
  → 파싱하면 복구됐고(하드 실패 0), 그 중 51건은 재무 테이블(FINANCE)까지 살아났다.
  기존 코드는 이 51건을 전부 FAILED로 버리고 있었다(Job #14를 통째로 죽였던
  그 인코딩이 근본 원인).
- **수정**: `app/parsers/xml_parser.py`에 `_decode_raw_xml()`을 추가해 파싱
  진입 전에 인코딩을 UTF-8로 정규화한다 — UTF-8 디코딩 성공 시 바이트를 그대로
  통과(정상 원문 약 95.6%는 기존 동작 무변경), `UnicodeDecodeError`일 때만 CP949로
  폴백 디코딩 후 (거짓이 된) 인코딩 선언부를 제거하고 UTF-8로 재인코딩한다.
  CP949마저 실패하면 최후 수단 `errors="replace"`. 기존 `recover=True`·계정과목
  사전·`determine_parse_status()`는 무변경 — 인코딩만 앞단에서 흡수한다.
  `recover=True`가 인코딩 오류를 복구 못 하는 이유는 인코딩 오류가 XML 구조
  오류가 아니라 파싱 진입 단계의 fatal error이기 때문(§4-8 EUC-KR 발견 시점에도
  같은 이유로 try/except만 임시 적용했었다 — 이번에 근본 복구를 넣었다).
- **회귀 테스트**: 실측 EUC-KR 원문 1건(rcept_no=20220127000408, 남경산업)을
  `tests/fixtures/`에 추가(manifest.json 등록)하고, `_decode_raw_xml` 유닛
  테스트 2종(UTF-8 passthrough/CP949 폴백) + 이 원문이 이제 `parse_status=OK`로
  재무 13항목까지 복구됨을 검증하는 테스트 1종을 추가했다. `pytest tests/ -q`
  **183 passed**(기존 180 + 신규 3). **소급 재파싱은 하지 않았다**(M7 CF 소급을
  "안 함"으로 정한 전례와 동일) — 신규 Job 실행분부터 자동으로 이 51건류가 살아난다.
  백엔드 프로세스 재기동은 다음 실제 Job 실행 시 하면 된다(이번엔 코드/테스트만).

**동명이인 corp_code 오매칭 버그 발견·수정 — B1에 주소 기반 재해석 추가(2026-07-20).**
사용자가 Job #20에서 "유성정밀은 DART에 감사보고서가 멀쩡히 있는데 왜 FAILED냐"고
물어 추적한 결과, DART 데이터 부재가 아니라 **Phase 1 A4가 후보를 폐지된 동명이인
corp_code에 묶은 것**이 원인이었다. 실측 경위:
- 결과 행의 `parse_note`는 "최근 감사보고서 공시를 찾을 수 없음", `rcept_no=NULL`
  — B1(`_backfill_latest_rcept_no_for_job`)이 공시를 못 찾아 FAILED 처리한 것.
- 배정된 `corp_code=00433989`로 list.json(F, 2022~2026)을 실제 조회하니 `status=013`
  (**공시 0건**, `modify_date=20170630`인 폐지 추정 법인)이었다.
- 원인은 A4의 이름 매칭 폴백: FSC 레코드에 `fss_corp_unq_no`가 없으면
  `_build_corp_cache_name_index`로 회사명 매칭을 하는데, 이 인덱스는 **같은 이름당
  corp_code를 하나만**(`norm not in index`, 먼저 만난 것) 보관한다. corpCode.xml에
  '유성정밀'이 3개(`00433989` 폐지 / `00840383` 부산 사상구 / `01647297` 경남 사천시)
  라 하필 폐지 법인이 채택됐다.
- **수정(사용자가 옵션 (a) 선택)**: B1에서 배정 corp_code의 공시가 0건이면
  `_resolve_alternative_corp_code()`로 같은 정규화 이름의 **다른** corp_code 중
  "실제 감사보고서가 있고 주소(시도/시군구)가 일치"하는 것을 찾아 **교체**한다
  (`results.corp_code`도 갱신 — 이후 STEP 4/5/7이 그 코드로 동작). 주소 대조 기준은
  후보의 FSC 주소를 우선 쓰고, 못 파싱하면 Job의 `cond_region`(`region_matches`)으로
  폴백한다. 같은 Job의 다른 결과가 이미 쓰는 corp_code는 중복 방지로 건너뛴다.
  company.json은 **공시가 실제로 있는 후보에만** 호출해 쿼터 낭비를 막는다.
  이름 다중매핑은 신규 `_build_corp_cache_name_multimap()`(A4의 단일 인덱스와 달리
  같은 이름의 corp_code를 전부 보관, `modify_date` 내림차순)이 담당한다.
- **실제 API로 검증**: 이 로직이 `01647297`(경남 사천시 사남면 외국기업로 21 — 결과
  행의 FSC 주소와 정확히 일치)을 골라 rcept_no `20260331003150`을 찾아냈다.
  **부산의 동명이인 `00840383`은 주소 대조로 정확히 탈락했다** — 만약 옵션 (b)
  "최신 갱신(modify_date) 우선"으로 골랐다면 부산 회사를 잘못 채택했을 것이므로,
  주소 대조를 판정 기준으로 삼은 것이 결정적이었다.
- 신규 회귀 테스트 2종(`test_pipeline.py`): 주소 대조로 동명이인/폐지 코드를 가르는
  케이스, FSC 주소가 없을 때 `cond_region`으로 폴백하는 케이스. 후자는 구현 중 실제로
  잡은 버그를 잠근다 — `Job.cond_region`은 DB에 **JSON 문자열**로 저장되므로
  `json.loads`로 파싱해 넘겨야 하는데(기존 코드의 관행), 원시 문자열을 넘기면
  `region_matches`가 AttributeError로 터진다.
- **기존 Job #20 데이터는 자동 복구되지 않는다**: `retry-failed`는 `rcept_no IS NULL`
  건을 명시적으로 제외하고(M6 QA에서 의도적으로 넣은 조건), `resume`은
  PAUSED_QUOTA/FAILED만 허용하는데 Job #20은 DONE이다. 이 수정은 **신규 Phase 2
  실행분부터** 적용된다(소급 재파싱을 안 하는 M7 CF/EUC-KR 전례와 동일).
- **주의(이번 세션 관찰)**: 작업 중 같은 워킹트리에서 다른 세션이
  `financial_snapshots.from_current_period`(연도별 1차 자료 우선 규칙) 기능을 동시에
  편집하고 있었다 — 그 미커밋 작업 때문에
  `test_collect_history_for_result_stops_once_target_years_reached`/
  `..._skips_api_when_already_sufficient` 2건이 실패 상태다(스냅샷 fixture가
  `from_current_period=1`을 설정하지 않아 short-circuit이 안 됨). **이 2건은 위
  동명이인 수정과 무관하며 건드리지 않았다** — 해당 기능 작성자가 테스트를 갱신해야
  한다. 이 2건을 제외하면 `pytest tests/ -q` 189 passed(신규 2 포함).

**재무이력 연도별 "원문 보기"가 그 연도를 당기로 하는 원문을 열도록 수정
(2026-07-20).** 사용자가 화면에서 "2025년 아래 원문보기면 당기 2025·전기 2024,
2024년 아래면 당기 2024·전기 2023이 보여야 한다"고 지적했다 — 실제로는 2024년
열의 버튼이 2025년 보고서(2024는 그 보고서의 전기)를 열고 있었다. 원인은 STEP 7이
newest-first로 훑으며 "이미 확보한 연도는 건너뜀" 규칙을 쓴 것이었다: 각 공시가
당기·전기 2개 연도를 채우므로, 연도 Y는 대개 Y+1년 보고서의 **전기** 열로 먼저
채워지고 그 행의 `rcept_no`도 Y+1 보고서가 됐다.
- **수정**: `financial_snapshots.from_current_period`(0/1) 컬럼을 신설하고
  (`db.py::_FINANCIAL_SNAPSHOTS_NEW_COLUMNS`에 ad-hoc ALTER 추가, 기존 관행 동일),
  `_collect_history_for_result()`가 **그 연도를 당기로 하는 공시를 항상 우선**하도록
  바꿨다 — 전기 열로 먼저 채운 연도(`0`)는 나중에 자기 공시를 열면 값·rcept_no·
  parse_status를 통째로 덮어쓴다(`1`). 조기 중단 조건도 "연도 수를 채우면 중단"에서
  "연도마다 당기 원문을 확보하면 중단"으로 바뀌어 회사당 문서 다운로드가 최대 1건
  늘었다(N개 연도 ≈ N건). 목표 연도를 다 채운 뒤 더 오래된 공시만 남으면 즉시
  중단해 헛다운로드를 막는다. resume 조기 반환은 "가장 오래된 연도를 뺀 나머지가
  전부 당기 유래"일 때만 — 가장 오래된 연도는 자기 공시가 조회 기간 밖이라 전기
  유래로 남는 게 정상이고, 이 예외가 없으면 resume마다 헛되이 list.json을 부른다.
- **화면**: 연도별 버튼에 Tooltip(당기/전기 연도 안내)을 달고, 전기 유래 연도에는
  "전기 기준" 라벨을 붙였다. 원문 모달 제목도 `2025년(당기 2025년 · 전기 2024년)` /
  `2022년(전기 기준 — 당기 2023년 보고서)`처럼 명시한다 — 표의 연도 열과 원문의
  당기가 다를 수 있는 유일한 경우를 화면에서 숨기지 않는다.
- **기존 데이터도 재수집해 교정 완료**: 먼저 로컬 문서 캐시만 읽어(쿼터 0) 기존
  38행의 플래그를 실제 결산기준일 기준으로 바로잡은 뒤, 사용자 승인을 받아
  이력이 있는 12개사 전체에 `_collect_history_for_result()`를 다시 돌렸다(기존
  Job은 DONE이라 resume 경로를 안 타므로 스캐치패드 일회성 스크립트로 직접 호출
  — 파이프라인 로직은 그대로 재사용, 스크립트는 커밋하지 않음). **DART 호출은
  14건만 소모**됐다(대부분의 원문이 이미 로컬 캐시에 있어 list.json만 새로 호출).
  결과: Job #18/#20의 4개년 이력 회사는 전 연도가 당기 유래로 확정, Job #11(2년
  이력)과 우성정공 2022년만 가장 오래된 연도가 전기 유래로 남았다(조회 기간 밖
  — 정상, 화면에 "전기 기준" 표시).
- **검증**: `pytest tests/ -q` 192 passed(STEP 7 테스트 2건 갱신 + 전기 유래 연도
  판정 테스트 1건 신규), `npm run build`/`npm run lint` 통과, 실제 백엔드·Vite dev
  서버 + Playwright로 Job #20 / 대신정공(주)(사용자가 지적한 그 회사) 상세에서
  2022~2025년 버튼 4개를 모두 눌러 모달 제목(`2024년(당기 2024년 · 전기 2023년)`)과
  원문 본문("제29(당)기 : 2024년 12월 31일 현재 / 제28(전)기 : 2023년 …")이 연도별로
  정확히 일치함을 콘솔 에러 0으로 확인했다.

**FAILED 대량 발생의 실체 규명 — 상장사 후보 제외(A4) + 실패 사유 구분 표시
(2026-07-20).** 사용자가 Job #21(경상남도 김해시) 결과에서 "실패가 대부분"이라고
지적해 조사한 결과, 실제 분포는 OK 249 / PARTIAL 38 / FAILED 36(실패율 약 11%)이었고
"실패(검수 필요)" 탭만 보고 있던 것이었다. 다만 그 FAILED 36건 중 **35건이
`parse_note="최근 감사보고서 공시를 찾을 수 없음"`(`rcept_no IS NULL`)** — 파서
실패가 아니라 B1이 열어볼 원문 자체를 못 찾은 건이었고, 실제 DART API로 표본을
확인해 두 가지 구조적 원인을 특정했다:
- **상장사 9건**: 스맥/유니크/디케이락/상신전자 등은 코스닥 상장사(`corp_cls=K`)라
  감사보고서를 별도 공시(`pblntf_ty="F"`)로 내지 않고 사업보고서에 첨부한다 —
  `_fetch_all_disclosures_for_corp()`의 F 조회가 `status=013`(0건)으로 돌아온다
  (스맥은 전체 공시 234건 중 F는 0건). `fsc_corp_index`가 금융위 **전체** 기업
  DB라 상장사가 섞여 들어오는데 Phase 1에 이를 거를 수단이 없었던 것이 원인.
- **조회 기간 밖 26건**: 과거엔 감사보고서를 냈지만 최근엔 안 내는 회사들(외감
  대상 제외/폐업 추정). 실측 예: 알켄즈 2021-04, 우창공업 2020-04, 삼우네오텍
  2018-04이 마지막 제출인데, B1이 쓰는 `_history_window(history_years=4)`가
  `bgn_de=20220101`이라 전부 0건이 된다 — **화면의 "재무 이력 조회 기간"이 최신
  공시 탐색 기간까지 함께 결정한다**는 점이 드러났다.

사용자 선택으로 **(a) 상장사 사전 제외 + (b) 실패 사유 구분 표시** 두 가지를
구현했다(공시 탐색 기간을 이력 기간과 분리하는 (c)안은 "최근 자료가 아닌 데이터가
섞인다"는 정책 판단이 필요해 이번엔 하지 않았다 — 다음 세션 판단 대상):
- **(a)** `app/core/fsc_index.py::resolve_candidate_pairs()`(A4)가
  `_build_listed_corp_codes()`(신규 — `corp_cache.stock_code`가 채워진 corp_code
  집합, 상장사만 조회하므로 가볍다)로 상장사를 후보에서 제외한다.
  `fss_corp_unq_no` 직접 매칭 경로와 이름 매칭 폴백 경로 **양쪽 모두**에 적용되도록
  루프를 corp_code 확정 → 단일 지점 검사 구조로 정리했다. 새 DB 컬럼/테이블 없음
  (`corp_cache`에 이미 있는 `stock_code`를 처음으로 활용).
- **(b)** `rcept_no IS NULL`을 판별 신호로 삼아(M6 QA에서 `retry-failed` 제외
  조건으로 이미 쓰던 것과 같은 신호) `_build_results_query()`에 `has_disclosure`
  파라미터를 추가하고 `/results`·`/export` 양쪽에 노출했다. 프론트는 탭을
  "파싱 실패 (검수 필요)"(`has_disclosure=true`)와 "감사보고서 없음"
  (`has_disclosure=false`)으로 분리하고, 후자에 "검수 대상이 아닙니다" 안내
  Alert를 띄운다. 표의 파싱상태 셀도 이 경우 "감사보고서 없음"으로 표기한다
  (`ResultColumn.formatRow` 신설 — 값 하나로 표기를 정할 수 없는 컬럼용, `format`보다
  우선). Excel/CSV 출력의 `parse_status` 원값은 건드리지 않았다(사유는 `parse_note`에
  이미 들어 있다).
- **소급 적용 없음**: (a)는 신규 Phase 1 실행분부터 적용된다(M7 CF/EUC-KR 전례와
  동일). 기존 Job #21의 상장사 9건은 그대로 남아 있고, (b) 덕분에 화면에서는
  "감사보고서 없음" 35건으로 분리돼 보인다.
- **검증**: `pytest tests/ -q` **194 passed**(기존 192 + 신규 2:
  `test_resolve_candidates_excludes_listed_companies`,
  `test_list_results_splits_failed_by_has_disclosure`), `npm run build`/`npm run
  lint` 통과. 백엔드 재기동(Job #21 완료 확인 후) + 실제 Job #21 데이터로
  API 검증(검수 필요 1건 / 감사보고서 없음 35건) + Playwright로 두 탭 렌더링·
  안내 문구·셀 표기를 콘솔 에러 0으로 확인했다.

**원문 보기 모달에 "감사의견" 탭 추가(2026-07-20).** 사용자가 원문 열람 모달
(재무상태표/손익계산서/현금흐름표/주석)에 감사의견을 보는 탭을 요청했다. 별도
파서/추출 로직 없이 기존 §4-8 섹션 추출 방식을 그대로 재사용했다 —
`document_sections.py::SECTION_TITLE_MARKS`에 `"audit": "감사보고서"`를 추가한
것이 변경의 전부다. fixtures 30건을 실측해보니 감사보고서 본문 TITLE이 두
서식으로 나뉘는데(신서식 "독립된 감사인의 감사보고서" / 2012 구서식 "외부감사인의
감사보고서") **공통 부분문자열 "감사보고서"로 30/30 전부 매칭**되고, 그 부모
`<SECTION-1>`이 감사의견 문단 + 표를 통째로 담고 있어(1,400~2,500자) 기존
"TITLE의 부모 컨테이너를 렌더링" 규칙이 그대로 통했다(목차 TITLE은 "목 차"라
오탐 없음). 프론트는 `DocumentSection` 타입 유니온과 `SECTION_LABELS`에 항목을
추가하고 **탭 맨 앞에 배치**했다 — 재무 수치를 읽기 전에 그 수치를 신뢰할 수
있는지(적정/한정/의견거절)부터 확인하는 게 검수 순서상 자연스럽다는 판단. 모달의
기본 진입 탭은 기존대로 `bs`다. `pytest tests/ -q` **196 passed**(신규 2:
신/구 서식 각각 실제 fixture로 검증), `npm run build`/`npm run lint` 통과.
백엔드 재기동(RUNNING Job 없음 확인, 중복 기동돼 있던 uvicorn 프로세스 2개를
정리하고 venv 것 1개만 기동) 후 실제 Job #21의 (주)동우TMC(result 3241)로
API 응답을 확인했다 — 감사의견 전문("...중요성의 관점에서 공정하게 표시하고
있습니다")이 정상 렌더링됐다. **Playwright 브라우저 스모크는 이번엔 생략했다**
(변경이 탭 항목 1개 추가 수준이고 API 응답을 실데이터로 직접 확인했다).

**결과 조회 정렬·검색 + 감사인 컬럼 추가(2026-07-20).** 사용자 요청으로 두 가지를
구현했다.
- **정렬/검색**: `/results`·`/export`에 `sort_by`/`sort_dir`/`q`를 추가했다.
  `sort_by`는 `SORTABLE_COLUMNS` 화이트리스트로 제한하고(밖의 값은 500이 아니라
  기본 정렬로 무시), `_apply_sort()`가 **값이 없는 행(NULL)을 오름차순·내림차순
  모두 항상 뒤로** 보낸다 — SQLite 기본대로 두면 "매출액 낮은 순"에서 파싱 실패
  행이 첫 페이지를 채워 정렬이 무의미해진다. 동률은 `id`로 안정 정렬해 페이지를
  넘겨도 순서가 흔들리지 않는다. `q`는 회사명/주소/대표자/업종/감사인명 부분일치.
  프론트는 컬럼 헤더 클릭으로 오름차순 → 내림차순 → 해제 순환(`sortKeyOf()`,
  `ResultColumn.sortKey`로 컬럼별 비활성 가능)하고, 검색 입력은 400ms 디바운스한다.
  Excel/CSV 다운로드도 화면의 필터·정렬을 그대로 반영한다(`query`를 공유).
- **감사인 컬럼**: 신규 `app/parsers/auditor.py`가 감사보고서 원문에서 감사인
  이름과 사무소 주소를 뽑아 `results.auditor_name`/`auditor_address`에 저장하고,
  화면은 "안경회계법인(경상남도 창원시)" 한 칸으로 합쳐 보여준다(기본 표시 컬럼).
  이미 내려받은 원문만 읽으므로 **추가 API 호출/쿼터 0건**이다. 실측으로 확정한
  파싱 규칙: ① 이름은 표지(31/31)와 본문 끝 서명란(28/31) 양쪽에 나오는데
  **주소를 함께 확보한 마지막 후보(=서명란)를 우선**하고 없으면 표지를 쓴다,
  ② "삼 일 회 계 법 인"처럼 글자 사이가 벌어진 표기를 공백 제거로 흡수한다
  (`normalize_account_label`과 같은 전례), ③ 이름이 접미어 **뒤**에 오는 서식
  ("회계법인 원지")이 로컬 캐시 250건 표본의 12%라 앞뒤를 모두 이어붙인다,
  ④ "기타사항" 문단의 **직전 감사인**("...성문회계법인이 감사하였으며")을 현재
  감사인으로 오인하지 않도록 후보를 짧은 단독 줄로 제한하고 접미어 뒤에 한글이
  이어지면 제외한다, ⑤ 주소 첫 토큰은 `normalize_sido()`로 표준 시도명으로
  정규화해 저장한다("서울시 서초구" → "서울특별시 서초구") — 프론트가 시도
  약칭 표를 따로 들 필요가 없다. 실측 커버리지: fixtures 31건 이름 100%/주소
  94%, 로컬 캐시 250건 무작위 표본 이름 99%/주소 80%(주소 미확보분은 서명란이
  원문에 아예 없는 서식). `pytest tests/ -q` **244 passed**(신규: 감사인 파서
  10종 `tests/test_auditor.py`, 정렬/검색 API 3종), `npm run build`/`npm run
  lint` 통과. 실 데이터(Job #21, 323건)로 정렬(매출액 오름/내림 + NULL 뒤로)·
  검색(q=김해 323건)을 별도 포트(8010, 검증 후 종료 — port 8000은 다른 세션이
  쓰고 있을 수 있어 재기동하지 않았다)로 확인했다.
- **소급 재파싱은 하지 않았다**(M7 CF/EUC-KR 전례와 동일) — 기존 결과 295건은
  `auditor_name`이 NULL이라 화면에서 "-"로 보이고, 신규 Phase 2 실행분부터
  채워진다. 다만 감사인 추출은 **로컬 문서 캐시만 읽어 쿼터가 0건**이고 NULL인
  컬럼만 채우면 되므로, 기존 결과 소급 채움은 언제든 안전하게 가능하다 —
  다음 세션에서 사용자가 원하면 일회성 스크립트로 처리할 것.

**M8 3단계 완료 — Phase 1 파이프라인을 DART 인덱스 기준으로 교체하고 A3/A4를
제거했다(2026-07-20).** 1~2단계(`dart_corp_index` 크롤 + `fsc_financial_stat`
스냅샷)에 이어 `run_job_phase1()` 호출부를 갈아끼웠다. 핵심은 **Phase 1이 외부
API를 0건 호출하는 로컬 쿼리 단계가 됐다**는 것이다:
- **A4(이름 매칭 corp_code 해석) 삭제** — `dart_corp_index`는 `corp_code`가 PK라
  이름 매칭 자체가 불필요하다(동명이인 오매칭 실측 11.6%가 구조적으로 소멸).
  상장사 제외도 인덱스의 `corp_cls`로 A2에서 끝난다.
- **A3(FSC 건별 재무 사전 스크리닝) 삭제** — §4-10-C 확정 정책. 1년 묵은 값으로
  거르면 조건에 맞는 회사의 25.3%를 **조용히** 놓친다. `run_job_phase1()`은 이제
  `cond_revenue`/`cond_total_assets`를 읽지도 않는다 — 판정 지점은 B4 한 곳이다.
  `FscCorpInfoClient`/`QuotaExceededError` 경로도 Phase 1에서 함께 사라졌다.
- **참고값은 `_cur`가 아니라 신설 `ref_*` 컬럼에 넣는다**(`ref_revenue`/
  `ref_total_assets`/`ref_fin_year`, `_RESULTS_NEW_COLUMNS` ad-hoc ALTER).
  구 A3는 추정치를 확정치 자리(`revenue_cur`)에 임시 저장해 B4가 추정치로 판정할
  위험을 안고 있었고 그래서 B1에 "추정치 지우기" 보정이 붙어 있었다 — 컬럼을
  분리하니 그 위험 자체가 없어졌다(B1의 보정은 구 Job 데이터용으로만 남겼다).
  기준연도는 회사마다 다르므로(`fsc_financial_stat`은 최신 연도일수록 비어 있다)
  반드시 함께 저장해 화면에 명시한다.
- **`app/core/fsc_index.py`는 A1(크롤/상태)만 남기고 A2/A3/A4를 삭제**했다.
  `fsc_corp_index` 테이블과 크롤러 자체는 롤백 여지로 남긴다(§4-10-E).
- **구현 중 발견한 조용한 0건 버그**: `GET /api/meta/industries`는 대분류를
  알파벳(A~U)으로 주는데 `dart_corp_index.induty_code`는 KSIC 숫자라, 사용자가
  "제조업"만 고르면 `like 'C%'`가 되어 **0건이 조용히 나온다**.
  `_expand_industry_prefixes()`가 대분류를 소속 중분류 2자리 코드 전체로 펼치도록
  고치고 회귀 테스트를 추가했다(§4-10-C가 폐기한 "조용한 누락"과 같은 종류).
- **라벨 정리**: 주소/대표자/업종이 DART 원본이 됐으므로 "미확정(FSC 기준)"
  라벨을 없앴고, 전화번호만 "(미수집)"으로 바꿨다 — 기업개황 엑셀에 전화번호 열이
  아예 없다(§4-10-G 열린 질문 4). `resultColumns.ts`/`excel.py` 양쪽 반영.
- **`candidates-preview`도 함께 교체**했다. A3가 사라져 병목이 data.go.kr 쿼터
  → **DART 일일 한도**로 바뀌었다(하루 처리 가능 후보 ≈ `daily_quota_limit / 5`).
  응답 계약은 무변경이고 필드 의미만 바뀌었다 — 프론트 안내 문구는 5단계 몫.
- **검증**: `pytest tests/ -q` **237 passed**. 실 DB(`dart_corp_index` 118,268행)로
  A2를 실측해 김해시 1,127개사 / 재무 참고값 매칭 433건(38.4%)이 **§4-10-A·B의
  스파이크 수치와 정확히 일치**함을 확인했고, 실제 Job #22(김해시 + 중분류 25)를
  `run_job_phase1()`로 끝까지 돌려 121건이 `ref_*`만 채워진 채(`_cur`는 NULL)
  DONE/CANDIDATES로 멈추는 것을 확인했다. `npm run build`/`npm run lint` 통과.
**M8 4단계 완료 — Phase 2 처리 순서를 조건 밴드 근접도順으로 바꿨다(2026-07-20).**
3단계에서 A3(사전 스크리닝)를 폐기한 대가로 Phase 2가 다뤄야 할 후보가 늘었고,
일일 한도로 중단(`PAUSED_QUOTA`)되면 그날 확보되는 결과가 무작위 표본이 되는
문제가 있었다. Phase 1이 남긴 참고값(`results.ref_revenue`/`ref_total_assets`)을
**정렬에만 쓰고 제외에는 절대 쓰지 않아** false negative 위험 0인 채로 이를 완화한다
(판정은 여전히 B4 한 곳 — §4-10-C·D).
- **정렬 키**: 조건 밴드 중심(상·하한이 모두 있으면 **기하평균** — 금액은 자릿수
  차이가 커서 로그 척도가 자연스럽다)과의 **로그 거리**. 매출액·총자산 조건이 둘 다
  있으면 두 거리의 평균, 한쪽만 있으면 그쪽만. 조건이 없거나 참고값이 하나도 없으면
  전원 동점이 되어 기존 id順이 그대로 유지된다.
- **참고값이 없는 후보는 중간 순위**(제외하지 않는다) — 확보된 점수들의 중앙값을
  부여한다. 짝수 개일 때 위쪽 중앙값을 그대로 쓰면 그 후보와 동점이 되어 안정
  정렬상 항상 뒤로 밀리는 버그가 있었고(신규 테스트가 바로 잡아냈다) 두 중앙값의
  평균을 쓰도록 고쳤다.
- **구현 방식**: SQL `ORDER BY`가 아니라 `ORDER BY id`로 읽은 뒤 파이썬에서 정렬한다
  — 로그 거리는 SQL로 표현하기 번거롭고 행 수가 수천 건 수준이라 메모리 정렬로
  충분하다. 세 루프(`_run_document_download`/`_run_financial_parsing`/
  `_run_history_collection`)의 **시그니처는 바꾸지 않았다** — `_load_band_conditions()`가
  각 루프 안에서 Job 조건을 직접 읽으므로 구 파이프라인(`run_job`)과
  `retry_failed_parsing`도 별도 수정 없이 같은 순서를 따른다. B4
  (`_run_revenue_filter`/`_run_assets_filter`)는 손대지 않았다.
- **실 데이터 검증(Job #22, 김해시 121개 후보 / 참고값 보유 60건, 매출 60~150억)**:
  밴드 내 18건이 **전부 상위 1~18위**(상위 15%), 참고값 없는 후보는 31~91위(중간),
  매출 880억·1,670억 등 먼 후보가 마지막 — §4-10-D 설계 의도대로 동작했다.
- **검증**: `pytest tests/ -q` **240 passed**(기존 237 + 신규 3). 코드 변경이 백엔드
  내부 처리 순서뿐이라 프론트 변경/백엔드 재기동은 하지 않았다.
**M8 5단계 완료 — 업종 트리를 DART 정본으로 교체(소분류 한 층 추가)하고 화면을 새
파이프라인에 맞췄다(2026-07-20).** 3단계가 백엔드 의미를 바꿔놓은 뒤 화면이 아직 옛
전제(FSC 인덱스, A3 추정치)를 말하고 있던 것을 정리했다.
- **업종 트리 교체**: `industry_data.py`의 손으로 쓴 정적 데이터를 DART 기업개황 화면의
  업종 트리(`selectCorpTree.do`)로 생성한 결과물로 교체하고 **소분류(3자리) 한 층을
  추가**했다(대 21 / 중 77 / 소 234 = 332, §4-10-G가 예고한 수치와 정확히 일치).
  생성 스크립트는 스캐치패드에만 두고 커밋하지 않았다(기존 관행) — 대신 파일 상단에
  "손으로 고치지 말 것 + 재생성 방법"을 명시했다. **코드 체계는 무변경**임을 생성
  시점에 검증했다: 중분류 77개 코드 집합과 대분류 21개 알파벳 집합이 교체 전과 1건도
  다르지 않다(이름 표기만 일부 다르고 DART 표기를 따른다). 대분류를 DART 내부
  ID(`ROOT0103`)가 아니라 기존 알파벳으로 유지한 것이 계약을 지킨 핵심 —
  `_expand_industry_prefixes()`가 이미 알파벳을 전제하고 있다. 응답 모델은 재귀
  (`IndustryEntry.children: list[IndustryEntry]`)로 바꿔 세 층이 같은 모양으로 나간다.
  **세분류·세세분류는 트리에 있어도 노출하지 않는다** — 회사별 분류 깊이 편차 때문에
  prefix 매칭에서 각각 20.9%/41.3%를 놓쳐, "정밀하게 골랐는데 조용히 누락"이 된다
  (§4-10-C가 폐기한 것과 같은 종류의 실패).
- **`IndustryTreeSelect` 3층 지원**: 소분류는 중분류 아래 접힌 채 두고 "▸ 소분류 N개"를
  눌러야 펼쳐진다(제조업은 중분류 25 / 소분류 70여 개라 전부 펼치면 목록에 묻힌다).
  상위를 켜면 하위 선택을 걷어낸다 — prefix 매칭이라 상위 코드 하나로 이미 전부
  포함되고, 남겨두면 같은 회사를 가리키는 코드가 조건에 중복으로 실린다.
- **`CandidatesView`의 실제 버그 수정**: 3단계가 참고값을 `ref_*` 컬럼으로 분리한 뒤에도
  화면은 `revenue_cur`/`total_assets_cur`를 읽고 있어 **매출액·총자산이 계속 빈 값으로
  보이고 있었다**. `ref_revenue`/`ref_total_assets`로 고치고 `ref_fin_year`(기준연도)
  컬럼을 신설했다 — 회사마다 확보된 연도가 다르므로 값만 보여주면 오해를 부른다.
  "추정"/"미확정" 라벨을 "참고"로 바꾸고, 안내문을 §4-10-C 정책대로 "이 값으로 후보를
  제외하지 않습니다 — 판정은 2단계 원문 파싱 뒤"로 재작성했다. 항상 비는 전화번호
  컬럼은 제거했다.
- **`FscIndexStatusNote` → `IndexStatusNote`**(DART 인덱스 + 재무 스냅샷 2종). 두
  인덱스의 **심각도를 구분**하는 것이 요점이다 — `dart_corp_index`가 비면 후보 확정이
  즉시 실패하므로 빨간 경고, `fsc_financial_stat`이 비어도 결과 정확도에는 영향이
  없으므로(참고 표시와 처리 순서만 잃는다) 노란 안내.
- **화면에 노출하며 발견한 백엔드 버그 1건**: `get_financial_stat_status()`의 `years`가
  마지막 크롤이 **요청한** 연도(`cache_meta`)를 반환하고 있어, 2023년만 보강 크롤한 실
  DB가 4개년(2023~2026)을 갖고도 "2023년 기준"으로만 표시됐다. 테이블의
  `DISTINCT biz_year`를 세도록 고쳤다(회귀 테스트 추가).
- **`candidates-preview` 안내 문구 재작성**: 병목이 data.go.kr 쿼터 → DART 일일 한도로
  바뀐 것을 반영하되, **후보 확정(1단계) 자체는 외부 호출 0건이라 규모와 무관하게 즉시
  끝나고 여러 날로 나뉘는 것은 재무정보 수집(2단계)** 이라는 점을 명확히 했다.
- **검증**: `pytest tests/ -q` **243 passed**(4단계 240 + 신규 3), `npm run build`/
  `npm run lint` 통과. 백엔드 재기동(RUNNING Job 없음 확인, 또 중복 기동돼 있던 uvicorn
  2개를 정리하고 venv 것 1개만 기동) 후 Playwright로 실 데이터 확인: 인덱스 상태 2줄
  (118,268개사 / 300,331건 2023~2026년), 대분류 C → 중분류 25 → 소분류 251 선택,
  후보 목록(Job #22) 새 헤더와 참고값·기준연도(2025년) 렌더링, 경상남도 선택 시
  미리보기 경고(4,544개사 → 약 2일) — 콘솔 에러 0.
**M8 6단계 완료 — 실전 완주로 구 파이프라인의 오매칭 12.7%·누락 59.5%를 실측
확인했다(2026-07-20). M8 전체 종료.** Job #22(김해시 + 중분류 25 금속가공)를
`start-financials`로 Phase 2까지 완주시키고 기존 Job #21(김해시, 구 파이프라인)과
비교했다. **DART 쿼터는 약 1,500건만 소모**됐다(1,473 → 시작, 완주 후에도 여유
충분) — 김해시 전체(1,129후보, 약 5,600콜·2~4시간)가 아니라 업종을 좁힌 121후보로
돌린 판단이 적중했다. 검증 목적(오매칭·회귀 유무)에는 121건 표본으로 충분했다.
- **결과**: 후보 121건 → OK 72 / PARTIAL 6 / FAILED 43. FAILED 43건은 **전부
  `rcept_no IS NULL`(감사보고서 없음)이고 파싱 실패는 0건**이다. 매출 밴드 통과
  69 / 제외 52, `financial_snapshots` 91건, 감사인 78건 적재.
- **최대 소득 — 지역 오매칭 41건 소멸**: Job #21의 323건 중 **41건(12.7%)이 김해시가
  아닌 회사**였고, 그중 **31건은 `parse_status=OK`**로 정상 결과와 구분되지 않은 채
  섞여 있었다. A4(이름 매칭)를 없앤 것만으로 0이 됐다. 상장사 혼입도 8건(전부 FAILED로
  쿼터만 낭비) → 0(A2의 `corp_cls` 제외).
- **"감사보고서 없음" 비율 상승(10.8% → 35.5%)은 회귀가 아니다**. 43건 중 4건만 #21에도
  있었고(동일하게 없음) **39건은 #21이 후보로 잡지도 못한 회사**다 — 회귀 0건. 이 39건은
  실제 DART API로 표본 5건을 직접 조회해 전부 `status=013`(공시 없음)임을 확인했다.
  계획서가 예상한 "감사보고서 없음 비율 감소"는 **프레이밍이 틀렸다** — 신 파이프라인은
  후보를 줄이는 게 아니라 정확한 모집단으로 교체하므로, 판정 지표는 건수가 아니라
  **오매칭 0 / 회귀 0 / 재현율**이어야 한다(상세개발계획.md §8 M8 6단계 표에 기록).
- **구 파이프라인 재현율 40.5%**: 김해시 중분류 25의 실제 대상 121개사 중 Job #21은
  49개사만 찾아냈다(업종 무필터였는데도) — **72개사(59.5%)를 조용히 놓치고 있었다**.
  §4-10-C가 A3를 폐기한 근거와 같은 종류의 실패가 A4에도 있었음이 실측됐다.
- **`fsc_corp_index`는 유지하기로 판단했다**(633,968행). 잔존 참조는 관리자용
  `fsc-index/refresh`·`/status` 둘뿐이고 파이프라인 경로는 없지만, §4-10-E의 롤백
  여지이고 실전 검증이 아직 1건뿐이라 지금 지우면 문제 발생 시 10시간 재크롤이 필요하다.
  **삭제 조건은 "신 파이프라인 실전 Job 3건 이상 완주 + 오매칭 0 유지"**로 문서에
  명시했다. 프론트 `getFscIndexStatus()`/`FscIndexStatus`는 5단계 이후 **호출처 0인 죽은
  코드**로 남아 있다 — 그때 함께 걷어낼 것.
- 단위 테스트/회귀 항목은 3~5단계에서 이미 충족돼 있었음을 확인했다.

**M8 6단계 후속 — 검증이 실제 인덱스 버그를 잡아냈다(2026-07-20, 같은 세션).**
위 비교표를 근거로 오염된 Job #21을 지우기 전에 "Job #24가 #21의 완전한 상위집합인가"를
게이트로 걸었는데, **이 게이트가 실제 버그를 잡았다.** 게이트 없이 지웠다면 데이터를
잃었을 상황이다.
- **버그**: `merge_by_position()`이 위치 결합의 어긋남을 **회사명으로만** 감지하기 때문에
  (`_names_align`), 동명 회사끼리 자리가 바뀌면 검사를 그대로 통과하고 주소·업종이
  조용히 교차된다. 실측 표본 70건에서 위험군(동일 정규화명 + 동일 크롤 업종)
  **불일치 42.5%**, 대조군(이름 유일) **0.0%**였다. 오염 추정 약 2,100행(1.8%).
- **영향**: 첫 실행(Job #23)에서 **진짜 김해 회사 23건이 후보에서 통째로 빠졌다** —
  인덱스 주소가 익산·인천·순천 등으로 찍혀 A2가 잡지 못했다. §4-10-C가 A3를 폐기한
  근거인 "조용한 누락"과 정확히 같은 실패다.
- **수정**: `find_ambiguous_corp_codes()` / `reconcile_ambiguous_rows()` +
  `POST /api/meta/dart-index/reconcile`. 전수 재크롤(23분) 대신 위험 그룹(3.69%)만
  DART 정본으로 되돌린다. **덮어쓰기가 아니라 `jurir_no` 기준 순열 교정**이라
  `company.json`이 주지 않는 `induty_name`·대표자까지 함께 복원된다(상세개발계획.md
  §8 "동명 회사 교차" 참고). 실행 결과 2,040그룹 / 4,366건 조회 → **2,098행 교정**,
  실패 0. 김해 누락 23건 **전부 해소**.
- **재검증**: Job #24(김해시 전체 1,127후보, 12분/약 2,700콜) — **지역 오매칭 0 /
  상장사 혼입 0 / 파싱 실패 0 / #21 대비 진짜 누락 0건**으로 게이트 통과.
- **오염 Job 삭제 완료**: #21(오염 323건) / #23(교정 전 실행분, #24로 대체) /
  #18·#20(테스트, #20은 오매칭 40%)을 삭제했다. 고아 레코드 0건 확인. 남은 Job은
  #16/#17/#19(Phase 1 후보 목록)와 #22/#24다.
- **`pytest tests/ -q` 247 passed**(기존 243 + 신규 4: 그룹 판별/순열 교정/멱등성/
  jurir_no 미매칭 폴백).
- (c) reconcile 자동화는 아래에서 완료했다. (a)/(b)는 다음 기록("`fsc_corp_index` 삭제
  완료")에서 함께 처리했다.

**크롤 → 동명 회사 교정 자동 연결 완료(2026-07-20, 위 (c)).** "재크롤 시
`reconcile`을 반드시 이어서 호출할 것"을 사람이 기억해야 하는 수동 2단계로 둔 것
자체가 위험이었다 — 이번 버그도 "크롤만 하면 끝"이라는 전제에서 나왔다.
- `POST /api/meta/dart-index/refresh`가 크롤 완료(`completed=True`) 시
  `_run_reconcile_after_crawl()`(`app/api/meta.py`)로 교정을 이어서 실행한다.
  요청 필드 `reconcile`(기본 `true`)로 끌 수 있다 — 쿼터를 아껴야 할 때만.
  **코어 함수는 여전히 분리돼 있다**(쿼터를 쓰는 것과 안 쓰는 것을 섞지 않는다).
- **교정 실패는 `_run_reconcile_after_crawl()` 안에서 전부 흡수한다** — 크롤은 이미
  성공했는데 예외가 바깥 감독 루프까지 올라가면 23분짜리 크롤을 통째로 재시도한다.
- **중단돼도 조용히 묻히지 않는다**: `cache_meta.dart_index_reconciled_at`을 남기고
  (`reconcile_ambiguous_rows()`가 **전체를 돌았을 때만** 기록 — `max_groups` 파일럿은
  일부만 손대므로 완료로 표시하면 남은 위험 그룹이 묻힌다),
  `GET /api/meta/dart-index/status`가 `last_reconciled_at`/`reconcile_pending`을
  노출하며, `IndexStatusNote`가 노란 경고("동명 회사 교정이 아직 완료되지 않았습니다 —
  해당 지역 회사가 후보에서 빠질 수 있습니다")로 재실행을 유도한다.
- 수동 `POST /api/meta/dart-index/reconcile`은 그대로 남는다(교정만 다시 돌리는 경로).
- **로컬 DB에 `dart_index_reconciled_at`을 소급 기입했다** — 실제 교정은 오늘 이미
  마쳤는데(Job #24가 오매칭 0으로 검증) 기록이 없어 잘못된 경고가 뜨는 상태였다.
  정확한 완료 시각을 알 수 없어 검증이 끝난 시점(Job #24 생성 시각 21:06:20)을 썼다.
- `pytest tests/ -q` **248 passed**(기존 247 + 신규 1: 전체 통과에서만 pending이 풀리는지),
  `npm run build`/`npm run lint` 통과. 백엔드 재기동(RUNNING Job 없음 확인, 또 중복
  기동돼 있던 uvicorn 2개를 정리하고 venv 것 1개만 기동) 후 실제 응답으로
  `reconcile_pending=false`·`last_reconciled_at` 확인. **Playwright 브라우저 스모크는
  생략했다** — 프론트 변경이 상태 문구 1줄 + 경고 분기 1개이고 API 응답을 실데이터로
  직접 확인했다.

**매출총이익율(%) → 매출총이익(금액) 교체 + 결과 상세의 "당기·전기" 섹션 제거
(2026-07-20).** 사용자가 재무 이력 표에 "매출총이익율"만 있고 "매출총이익"
(금액) 자체가 없는 이유를 물어, M3 설계 당시 PRD가 원문의 "매출총이익" 행을
직접 저장하지 않고 매출액/매출원가로 계산한 비율만 저장하기로 정했던 결정임을
확인한 뒤 — 사용자 선택으로 비율을 완전히 금액으로 교체했다(비율은 더 이상
저장하지 않는다). 같은 대화에서 "재무정보 (당기·전기)" 섹션도 "재무 이력"
표(가장 최근 연도 열이 사실상 당기)와 중복이라 완전히 제거하기로 했다 —
세부계정 펼쳐보기는 재무 이력 표가 이미 독자적으로 지원하고 있었고, DART
원문 링크는 애초에 그 섹션에 속한 게 아니라 Drawer 상단에 독립적으로 있어
그대로 남는다.
- **`gross_profit`이 다른 12항목과 동일하게 원문에서 직접 파싱된다** —
  `ACCOUNT_NAME_ALIASES`에 "매출총이익"/"매출총손실"/"매출총이익(손실)"을
  `gross_profit`으로 추가했고, "매출총손실"은 다른 손실 라벨(영업손실 등)과
  동일한 `_apply_sign()` 규칙으로 자동으로 음수가 된다(별도 처리 불필요).
  `STANDARD_FINANCIAL_FIELDS`가 "gross_margin" 대신 "gross_profit"을 담게
  되면서 `DIRECT_FINANCIAL_FIELDS`(파서가 원문에서 직접 채우는 필드)가 이제
  표준 13항목과 완전히 같아져, "계산값이라 제외한다"는 예외 필터 자체가
  없어졌다 — `compute_gross_margin()` 함수를 삭제했다.
- **DB 컬럼은 교체(rename)가 아니라 신규 추가**다 — `results.gross_profit_cur/prv`
  (INTEGER)와 `financial_snapshots.gross_profit`(INTEGER)를 `_RESULTS_NEW_COLUMNS`/
  `_FINANCIAL_SNAPSHOTS_NEW_COLUMNS`에 추가했다. 기존 `gross_margin_cur/prv`
  (REAL, %) 컬럼은 이미 ALTER된 실 DB에 물리적으로 남아있지만 모델/API/화면
  어디에서도 더 이상 읽거나 쓰지 않는다 — 이 프로젝트가 지금까지 스키마를
  바꿀 때 항상 써온 방식(추가만 하고 소급 재파싱은 하지 않는다, M7 CF/EUC-KR
  전례와 동일)을 그대로 따른 것이다. **기존 결과의 `gross_profit_cur/prv`는
  신규 Phase 2 실행분부터만 채워진다** — 실제로 확인해보니(port 8000, Job #24
  결과 4811) 기존 행은 `gross_profit_cur=null`이었다(정상, 소급 없음).
- **`gross_profit`이 있으니 세부계정 펼치기 예외도 없어졌다**: 프론트
  `canExpand()`가 "현금흐름표만 제외"로 단순화됐다(이전에는 계산값이라
  하위계정이 없는 gross_margin도 별도 예외 처리했었다) — 매출총이익도 이제
  원문의 실제 테이블 행이라 다른 항목(영업이익 등)과 동일하게 펼치기를
  시도하고, 하위계정이 없으면 기존과 동일하게 "세부 내역이 원문에 없습니다"
  안내가 뜬다.
- 라벨 변경: 화면(`매출총이익`, `formatNumber`로 금액 표시)/Excel·CSV
  (`매출총이익(당기)`/`매출총이익(전기)`)/정렬 화이트리스트(`SORTABLE_COLUMNS`)
  모두 `gross_profit_cur/prv`로 맞췄다.
- **검증**: 실제 fixture(한국학술정보, `20260630000641`)의 "Ⅲ.매출총이익" TE
  셀 값(당기 15,242,639,160 / 전기 15,541,193,733)으로 새 테스트를 작성해
  실측 검증했다(매출액-매출원가와 일치하지만 계산이 아니라 원문에서 직접
  옮겨온 값임을 확인). `pytest tests/ -q` **248 passed**(기존
  244 + 신규 4: alias 매핑 2종 + gross_profit 직접 파싱 검증, `compute_gross_margin`
  테스트 삭제). `npm run build`/`npm run lint` 통과. 백엔드가 이전 세션부터
  구버전 코드로 떠 있던 것(RUNNING/PENDING Job 없음 확인 후) 재기동해 API로
  `gross_profit_cur`/`gross_profit`이 응답에 나오고 `gross_margin_cur`는 더
  이상 나오지 않음을 확인했다. Playwright로 실제 Job #24 결과 상세 Drawer를
  열어 "재무정보 (당기 · 전기)" 제목이 사라지고 "매출총이익율" 문구가 전혀
  남지 않았으며 "매출총이익"/"DART 원문 보기"는 정상 노출되는 것을 콘솔
  에러 0으로 확인했다.

**"손실" 라벨 부호 반전 규칙 정교화 — 조합형 라벨(이익(손실))과 순수 손실
라벨을 구분(2026-07-20, 같은 세션 곧바로 후속).** 위에서 기존 결과의
`gross_profit_cur`가 전부 `None`인 게 소급 미적용(정상)임을 확인한 직후,
사용자가 "손실" 라벨 부호 처리 규칙 자체를 지적했다 — 기존 `_apply_sign()`은
`"손실" in raw_label and value > 0`일 때만 반전해, 라벨이 "손실"이지만 원문
값이 이미 음수(괄호 표기)인 경우는 반전하지 않고 그대로 뒀다. 실제 fixture
로 두 갈래를 모두 실측 확인했다:
- **순수 손실 라벨**("영업손실"/"매출총손실"/"당기순손실" — "이익"이 없음,
  예: 20260630000895의 "Ⅴ. 영업손실")은 원문에 부호 없이 양수로만 찍힌다
  (실측 다수) — 라벨 자체가 "손실 금액"이라는 뜻이라 부호와 무관하게 항상
  반전해야 한다. 드물게 이미 음수로 찍힌 경우("음의 손실")는 사실 이익이므로
  양수로 반전해야 한다.
- **조합형 라벨**("영업이익(손실)"/"당기순이익(손실)" — 회사가 흑자든 적자든
  같은 줄을 재사용하는 템플릿, 실측: EUC-KR 원문 20220127000408의 "영업이익
  (손실)" 행이 "(6,308,961,098)"로 이미 음수 표기)은 원문 부호가 이미
  정확히 반영돼 있어 **절대 반전하지 않아야** 한다.
  구 코드는 `value > 0` 가드 덕분에 이 조합형 사례가 이미 음수인 경우는
  우연히 맞았지만(그대로 둠), 같은 조합형 라벨의 실제 값이 흑자라 원문이
  양수로 찍힌 경우까지 상정하면 구 코드는 그 흑자를 손실로 잘못 반전시키는
  잠재 버그가 있었다(기존 fixture 표본에는 이 조합이 없어 여태 발견되지
  않았다). "이익"이 라벨에 있는지로 두 갈래를 구분하도록 `_apply_sign()`
  (`xml_parser.py`)을 고쳤고, `pdf_parser.py`의 동일 로직(`-abs()`로 항상
  음수를 강제하던 것 — 조합형의 "음의 손실=이익" 케이스를 다시 놓치는
  코드였다)도 같은 규칙으로 맞췄다. `gross_profit`/`operating_income`/
  `net_income` 세 필드 모두 `_apply_sign()`을 공유하므로 별도 필드별 처리
  없이 한 곳만 고치면 셋 다 적용된다(사용자 요청 그대로).
- 새 규칙으로 EUC-KR 회귀 테스트(`test_parse_xml_financials_recovers_euckr_encoded_document`,
  20220127000408 "영업이익(손실)" = -6,308,961,098)가 처음엔 깨졌다 — 단순히
  "손실이면 항상 반전"으로 고쳤을 때 이 조합형 사례를 다시 양수로 잘못
  뒤집었기 때문이다. 이 실패가 바로 "조합형/순수 손실을 구분해야 한다"는
  결론의 근거가 됐다. `_apply_sign` 직접 단위 테스트를 신설해 순수 손실
  라벨(양수/음수 원문 둘 다)과 조합형 라벨(양수/음수 원문 둘 다) 4가지
  조합을 모두 검증한다. `pytest tests/ -q` **256 passed**(기존 249 + 신규
  7: `_apply_sign` 단위 테스트 6종 파라미터화 + 관련 회귀 없음 재확인).
  전체 31개 fixture를 스캔해 `gross_profit`/`operating_income`/`net_income`
  값이 서로 부호·규모상 모순 없음을 육안 재확인했다(예: 적자 fixture는
  operating_income/net_income이 함께 음수, 흑자 fixture는 함께 양수 등).
  **백엔드는 재기동하지 않았다** — 확인 시점에 Job #26이 RUNNING 상태라
  (다른 세션이 실행 중으로 추정) 중단시키지 않기 위해 코드만 반영하고
  다음 자연스러운 재기동을 기다리기로 했다. 프론트 변경 없음(순수 백엔드
  파싱 로직).

**현금흐름표 3항목 세부계정 펼치기 + 재무 이력 표에 연도별 감사의견 행 추가
(2026-07-20).** 사용자가 영업/투자/재무활동현금흐름도 클릭하면 재무상태표·
손익계산서처럼 하위 세부계정이 펼쳐지길 원했고, 재무상태표 위에 감사의견
(적정/한정/의견거절)을 보여 달라고 요청했다. 실제 원문(fixture
`20260630000641`)을 확인해 현금흐름표도 동일한 `ALEVEL` 계층 구조(L0=
"Ⅰ.영업활동으로인한현금흐름", L1="1.당기순이익", L2="감가상각비" 등)를
쓴다는 것을 실측한 뒤 구현했다:
- **`app/parsers/account_detail.py`**: 세부계정 수집 함수(`_collect_table`)를
  파라미터화해(`aliases`/`valid_fields`) 재무상태표·손익계산서
  (`ACCOUNT_NAME_ALIASES`/`DIRECT_FINANCIAL_FIELDS`)와 현금흐름표
  (`CF_ACCOUNT_NAME_ALIASES`/`CF_FINANCIAL_FIELDS`)를 같은 로직으로
  처리한다(xml_parser의 `_extract_section` 파라미터화와 동일한 방식). "기말의
  현금"은 그 자체가 총계라 하위 대분류가 없다(자산총계 등과 동일한 패턴) —
  버그가 아니라 원문 구조상 정상이라 별도 처리를 하지 않았다.
  `AccountDetail`에 `audit_opinion` 필드를 추가해 기존 `audit_opinion.py`
  판정 로직을 원문 전체에 재사용한다(감사의견은 계정 상세가 아니라 원문
  1건당 하나의 값이라 계층 파싱과 무관하게 별도로 뽑는다).
- **API**: `GET .../account-detail` 응답(`AccountDetailResponse`)에
  `audit_opinion`을 추가했다 — 추가 API 호출/쿼터 0건(로컬 문서 캐시만 읽음,
  기존 계정 상세 엔드포인트와 동일).
- **프론트**: `canExpand()`를 단순화해(그 사이 다른 세션이 "재무정보 (당기·
  전기)" 섹션을 없애고 재무 이력 표 하나로 통합해 둔 상태라, `group.section
  !== 'cf'` 예외만 있던 버전이었다) 현금흐름표도 다른 항목과 동일하게 펼칠 수
  있게 했다. 재무 이력 표(`FinancialHistorySection`) 최상단에 "감사의견" 행을
  추가했고, 이 행은 사용자가 세부계정을 펼치지 않아도 값이 보여야 하므로
  이력 로드 시점에 관련 연도의 원문을 모두 미리 조회해 두는 `useEffect`를
  추가했다(기존 "그룹을 펼칠 때만 fetch"하던 지연 로딩과 별개 경로 — 로컬
  캐시만 읽으므로 쿼터 영향 없음).
- **검증**: 신규 테스트 1건(`test_account_detail_returns_cash_flow_children_and_audit_opinion`,
  fixture `20260630000641`로 영업/투자/재무활동 각각 3개 초과 세부계정 +
  감사의견 "적정" + "기말의현금" children 빈 배열까지 확인) 포함
  `pytest tests/ -q` **249 passed**, `npm run build`/`npm run lint` 통과.

**`fsc_corp_index` 삭제 완료 — 삭제 조건을 3개 시도로 채웠다(2026-07-21).**
2026-07-20에 "실전 3건 이상 완주 + 오매칭 0 유지"를 삭제 조건으로 걸어두고
보류했던 것을 이번 세션에서 채웠다. 서로 다른 시도 3곳(경상남도 김해시 —
Job #24, 부산광역시 사상구 — Job #25, 전라남도 여수시 — Job #26)으로
Phase 1+2를 전부 완주시켜 **지역 오매칭 0 / 상장사 혼입 0 / corp_code 중복
0 / 진짜 파싱 실패 0**(FAILED는 전부 `rcept_no IS NULL`, 즉 감사보고서
자체가 없는 정상 케이스)을 확인했다 — 표는 상세개발계획.md
"`fsc_corp_index` 정리 판단" 참고.
- **Job #26 진행 중 실제 인프라 문제를 겪었다**: 이 저장소 환경에서 반복
  관찰돼 온 "uvicorn 중복 기동" 패턴이 이번엔 Job을 완전히 정지시켰다 —
  두 프로세스가 동시에 port 8000을 두고 있다가(원인 불명, Windows 소켓
  레벨의 포트 탈취로 추정) 실제 백그라운드 태스크를 돌리던 프로세스가
  아웃바운드 연결 없이 멈췄고(CPU 사용량도 0에 가까움), job 상태는
  DB에서 계속 RUNNING으로 남아 `resume`(PAUSED_QUOTA/FAILED만 허용)으로도
  복구할 수 없었다. 중복 프로세스를 모두 정리하고 **DB에서 job 상태를
  수동으로 FAILED로 되돌린 뒤** `resume`으로 재개해 완주시켰다 — B1
  백필 루프가 `rcept_no IS NULL` 조건으로 스코프돼 있어 이미 처리된
  23건을 건너뛰고 안전하게 이어졌다(멱등 확인됨). 이 우연한 사고가
  결과적으로 "orphaned RUNNING job을 우회 복구하는 절차"를 실전
  검증한 셈이다 — 별도 API/자동화는 추가하지 않았다(발생 빈도가 낮고
  수동 개입으로 충분).
- **삭제 범위**: `fsc_corp_index` 테이블(633,968행, `DROP TABLE` + `VACUUM`),
  `app/core/fsc_index.py`, `app/models/fsc_corp_index.py`,
  `tests/test_fsc_index.py`, `POST /api/meta/fsc-index/refresh` +
  `GET /api/meta/fsc-index/status`(`app/api/meta.py`),
  `Settings.fsc_index_ttl_days`, 프론트 `getFscIndexStatus()`/`FscIndexStatus`
  (M8 5단계 이후 이미 호출처 0이던 죽은 코드). `cache_meta`의 체크포인트
  키(`fsc_index_last_page`/`fsc_index_updated_at`)도 함께 정리했다.
  **`fsc_financial_stat`(M8 2단계, 매출액/총자산 참고값)은 이름이 비슷하지만
  완전히 별개 테이블이라 전혀 건드리지 않았다** — 혼동하기 쉬운 지점이라
  명시해 둔다.
- **검증**: `pytest tests/ -q` **249 passed**(회귀 없음 — 삭제로 줄어든
  `test_fsc_index.py`의 테스트 수만큼 감소, 다른 세션이 그 사이 커밋한
  `xml_parser.py`/`pipeline.py` 변경과도 충돌 없이 통과), `npm run build`/
  `npm run lint` 통과. 실제 DB에서 테이블을 드롭한 뒤 백엔드를 재기동해
  `GET /api/meta/fsc-index/status`가 404로 사라졌음을, `GET /api/meta/
  dart-index/status`는 정상 응답함을 확인했다.
- **동시 편집 주의**: 이 작업 도중 다른 세션이 같은 워킹트리에서 독립적으로
  커밋 2건(`e723b90` 매출총이익 금액 교체 + 현금흐름표 세부계정 펼치기,
  `260c7f9` "손실" 라벨 부호 반전 규칙 정교화)을 올렸다 — 파일 충돌은
  없었다(내가 건드린 파일은 `app/api/meta.py`/`app/core/fsc_index.py`/
  `app/models/*`/프론트 `api/meta.ts`·`types/index.ts`·
  `IndexStatusNote.tsx`뿐이고, 다른 세션은 `xml_parser.py`/
  `pipeline.py`/`account_detail.py`/`ResultDetailDrawer` 계열을 건드렸다).

**BS/IS 개별 항목 결측 실측 확인(코드 변경 없음) + 합계·최종값 항목 펼치기
버튼 제거(2026-07-21).** 사용자가 "유동부채/비유동부채/유동자산/비유동자산
등이 항상 금액이 있는 게 아니다 — 원본을 확인해서 그대로 정리하라"고 지적해,
실제 프로덕션 DB(`results` 테이블)를 직접 조회해 검증했다.
- **결측 처리는 이미 올바르게 동작하고 있었다(코드 버그 아님)** — 예: id=5613/
  5793/5942 등 여러 실제 회사가 `noncurrent_liab_cur IS NULL`이면서
  `total_liab_cur == current_liab_cur`(비유동부채가 없어 부채총계=유동부채)
  로 정확히 일치했다. `noncurrent_assets`만 결측인 사례도 3건 확인했다 — 원문
  자체에 그 구분이 없는 회사(전액 유동부채/유동자산만 보고)라는 뜻이고,
  `parse_won_amount`가 이를 0으로 지어내지 않고 `None`으로 정확히 남긴 뒤
  `parse_status=PARTIAL`로 검수 대상 표시하는 기존 설계(M3의 cogs/sga 구조적
  부재 정책과 동일 원리)가 BS 전 항목에 이미 일관 적용되고 있음을 실측으로
  재확인했을 뿐이다. 참고: 같은 조회에서 "`parse_status=OK`인데
  `gross_profit_cur IS NULL`"인 행이 863건 나왔는데, 이는 2026-07-20
  매출총이익율→매출총이익(금액) 교체 이전에 파싱된 레거시 행이라 정상이다
  (해당 세션이 이미 "소급 재파싱 없음, 신규 실행분부터만 채워짐"으로 명시).
- **펼치기(세부계정) 버튼은 실제로 문제였다** — `ResultDetailDrawer.tsx`의
  `canExpand()`가 무조건 `true`를 반환해, 원문 구조상 그 자체가 합계/최종값이라
  하위 항목이 있을 수 없는 항목(자산총계/부채총계/자본총계/매출총이익/
  영업이익/당기순이익/기말의현금)도 펼치기 버튼이 노출되고 클릭하면 항상
  "세부 내역이 원문에 없습니다"만 떴다. fixtures 20건 전체를
  `parse_account_detail()`로 스캔해 실측 검증했다 — 이 7개 항목은 **20건
  전부에서 children이 0건**이었던 반면, 유동자산/비유동자산/유동부채/
  비유동부채/매출액/매출원가/판관비/영업·투자·재무활동현금흐름은 대부분
  실제 하위계정을 갖고 있었다(비유동부채 19/20, 매출액 18/20처럼 일부 결측은
  있지만 구조적으로 하위계정이 있을 수 있는 항목이라 펼치기 버튼은 유지).
  `resultColumns.ts`의 `FinancialItem`에 `expandable?: boolean`을 추가해 이
  7개 항목만 `false`로 명시하고, `ResultDetailDrawer.tsx::canExpand(item)`이
  이를 참조하도록 고쳤다 — 나머지 항목은 기존처럼 펼칠 수 있고, 실제로
  하위계정이 없는 회사(드문 예외)는 여전히 "세부 내역이 원문에 없습니다"
  안내로 정상 처리된다.
- 백엔드 코드는 건드리지 않았다(순수 프론트 UI 수정) — `pytest tests/ -q`
  **249 passed**(회귀 없음, 애초에 무관), `npm run build`/`npm run lint` 통과.
  진단에 쓴 스크립트는 스캐치패드에만 남기고 커밋하지 않았다(기존 관행).

**→ 위 "결측 처리는 이미 올바르게 동작하고 있었다" 결론은 절반만 맞았다 —
사용자가 "유동자산/비유동자산도 항상 값이 있는 게 아니다"라고 재차 지적해
`noncurrent_assets` 결측 5건을 전부 원문과 직접 대조하니 실제 진짜 파싱
버그가 있었다(2026-07-21, 같은 세션 곧바로 후속).** 5건 중 3건은 위에서
확인한 대로 "current_assets == total_assets"(비유동자산이 정말로 없는
회사)로 정상이었지만, **2건은 MISMATCH**(id=4853 주식회사 신진팩: 유동자산
30억 vs 총자산 147억 — 117억 원 괴리, id=6722 (주)해동주택: 168억 vs 170억
— 2.7억 원 괴리)였다 — 이 괴리는 원문에 값이 없는 게 아니라 "Ⅱ.비유동자산"
대분류 행 자체가 인식되지 못해 그 아래 세부계정 전체가 통째로 버려진
것이었다. 로컬 문서 캐시에서 원문을 직접 열어 원인 2가지를 확정했다:
- **id=4853**: "Ⅱ **.** 비유동자산" — 로마숫자와 마침표 사이에 공백이 있다.
  `_PREFIX_RE`의 유니코드 로마숫자 분기 `[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.?`는 숫자와
  마침표가 붙어 있어야만 매치돼, "Ⅱ"만 제거되고 남은 ". 비유동자산"의 선행
  마침표가 지워지지 않아 `normalize_account_label` 결과가 "비유동자산"이
  아니라 ".비유동자산"이 되어 `ACCOUNT_NAME_ALIASES` 조회가 실패했다.
- **id=6722**: "**∥**.비유동자산" — 로마숫자 "Ⅱ"(U+2161) 자리에 모양이 비슷한
  수학 기호 "∥"(U+2225, PARALLEL TO)가 쓰여 있다(같은 문서의 "Ⅱ.비유동부채"/
  "Ⅱ.결손금"은 정상적으로 Ⅱ를 쓰고 있어, 이 회사가 비유동자산 행 하나만
  타이핑할 때 실수/폰트 치환으로 오타를 낸 것으로 추정). 로마숫자 문자
  집합에 없는 글자라 접두어 제거 자체가 전혀 일어나지 않았다.
- **수정**: `app/parsers/base.py`의 `_PREFIX_RE` 유니코드 로마숫자 분기를
  `[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ∥]+\s*\.?`로 확장해(문자 집합에 "∥" 추가 + 숫자와 마침표
  사이 공백 허용) 두 변형을 모두 흡수한다. 수정 후 두 실제 문서 모두
  `parse_status`가 PARTIAL→**OK**로 정정되고 `current_assets +
  noncurrent_assets == total_assets`가 정확히 맞아떨어짐을 확인했다.
  fixtures 30건 전체를 재스캔해 회귀 없음을 확인한 뒤, 이 두 실제 문서를
  `backend/tests/fixtures/20260402000767`·`20260408003380`으로 추가하고
  `manifest.json`에 등록했다(EUC-KR 원문 추가 전례와 동일한 방식) —
  `normalize_account_label` 파라미터 케이스 2건 + 전체 파싱 회귀 테스트
  2건(당기 유동자산+비유동자산=자산총계 항등식까지 검증) 신규.
  `pytest tests/ -q` **253 passed**(249 + 4).
- **소급 재파싱은 하지 않았다**(M7 CF/EUC-KR 전례와 동일) — 신규 Phase 2
  실행분부터만 적용된다. 다만 이 버그의 특성상(로컬 문서 캐시만 읽고 쿼터
  0건으로 재파싱 가능) 기존 PARTIAL 결과 중 이 두 패턴에 해당하는 행을
  일괄 재파싱해 OK로 교정하는 것도 언제든 안전하게 가능하다 — 다음 세션에서
  사용자가 원하면 처리할 것(auditor_name 소급 채움과 같은 성격의 보류).
  같은 원인(유사 유니코드 문자 오표기, 숫자-구두점 사이 공백)이 다른
  로마숫자(Ⅰ/Ⅲ/Ⅳ 등)나 다른 계정(유동자산/유동부채/비유동부채 대분류
  행)에도 있을 수 있는지는 이번엔 광범위하게 재조사하지 않았다 — 발견된
  2건만 근거로 정규식을 확장했다(과잉 일반화 방지, 실측 기반 확장이라는
  기존 관행 준수).
- **교훈**: "결측이 원본과 일치하니 정상"이라는 결론은 **일치하는 사례만
  확인하고 불일치(MISMATCH) 사례를 별도로 대조하지 않은 채** 내린
  성급한 일반화였다 — 사용자의 재질문이 아니었다면 이 파싱 버그를
  놓칠 뻔했다. 앞으로 "결측이 정상적인 구조적 부재인지"를 검증할 때는
  총계-부분값 항등식이 실제로 맞아떨어지는 사례뿐 아니라 **어긋나는
  사례가 있는지도 반드시 별도로 확인**할 것.

**→ 로컬 문서 캐시 4,922건 전수 스캔으로 확장 — "발견된 2건만 근거"였던
위 판단을 뒤집고 로마숫자 접두어 정규화를 근본적으로 다시 손봤다
(2026-07-21, 같은 세션 곧바로 후속).** 사용자가 "영업활동현금흐름/투자활동
현금흐름/재무활동현금흐름 등도 마찬가지야, 다른것도..."라고 재차 지적해,
위에서 "과잉 일반화 방지"를 이유로 미뤘던 광범위 재조사를 실제로 수행했다.
`data/documents/`(로컬 원문 캐시, 4,922건 — API 호출 0건) 전체를 스캔해
BS/IS/CF FINANCE 테이블의 **모든 행**(ALEVEL 무관)에서 "라벨은 표준 계정명
(유동자산/영업이익/영업활동으로인한현금흐름 등)처럼 보이는데 정규화 후
alias 매핑이 실패하는" 근접 불일치를 찾는 스크립트를 작성했다(정확한
서술형 라벨과의 오탐을 피하려 라벨에 2자리 이상 숫자가 섞이면 제외 — 예:
"미처분이익잉여금(당기순이익:192,230,184원...)"는 실제로 매핑되면 안 되는
서술형 각주다). 최초 스캔에서 92건의 진짜 후보가 나왔고, 원인을 4가지로
분류해 모두 고쳤다:
1. **아스키 로마숫자 접두어 목록이 X(10)까지만 있었다** — 항목이 11~12번째까지
   있는 손익계산서("XII.당기순손실", 오타 전혀 없음)가 접두어를 벗기지 못해
   그대로 실패하고 있었다. `_ASCII_ROMAN_NUMERALS_ORDERED`에 XI/XII를
   추가했다(길이 내림차순 순서 유지 — "XII"가 "X"로 잘못 잘리지 않도록).
2. **소문자 "l"/"i", 그리스 대문자 이오타 "Ι"(U+0399)가 로마숫자 I/II/III/IV/VI
   자리에 육안 구분 불가능한 형태로 쓰였다** — "l.유동자산", "ll.비유동부채",
   "lll.매출총이익", "lV.판매비와관리비", "Vl.기말의현금", "Vi.기말의현금",
   "Ι.유동부채", 심지어 아스키·유니코드 로마숫자가 한 접두어 안에 섞인
   "XⅠ.당기순이익(손실)"(X는 아스키, Ⅰ은 U+2160 유니코드)까지 확인했다.
   신규 함수 `_normalize_roman_lookalike_prefix()`(`app/parsers/base.py`)가
   접두어 자리의 `[IlivVXΙⅠ]` 문자열을 정본 대문자로 치환하되, 치환 결과가
   유효한 로마숫자(I~XII) 집합에 없으면 손대지 않는다(오탐 방지) —
   `normalize_account_label()`의 첫 단계로 호출한다.
3. **셀 안에서 라벨이 여러 줄로 렌더링돼 단어 중간에 개행이 섞였다**
   ("판매비와관리\n비", "매\n출액", "당기순\n이익(손실)", "기말의 \n현금") —
   최종 압축 단계가 일반 공백/전각 공백만 제거하고 있어 개행은 그대로
   남아 문자열이 어긋났다. `\n`/`\r`/`\t`도 함께 제거하도록 확장했다
   (`\s`가 개행도 포함하므로 로마숫자-마침표 사이에 개행이 낀 극단적 사례
   "XII\n.당기순손실"도 이 김에 함께 흡수됐다).
4. **현금흐름표 항목이 "영업활동으로 인한 현금흐름(I)"처럼 "+" 없이 항목번호만
   괄호로 병기하는 서식**이 있었다 — 기존 `_FORMULA_SUFFIX_RE`는 "산식"(예:
   "기말의현금(Ⅳ+Ⅴ)")을 의도해 "+"를 필수로 요구했는데, 정작 오탐을 막는
   실질적 안전장치는 "+" 요구가 아니라 문자 집합 자체가 한글을 포함하지
   않는다는 점이었다(그래서 "당기순이익(손실)"은 애초에 이 정규식과 무관).
   "+" 요구를 없애 로마숫자/숫자/공백만으로 이뤄진 괄호는 산식이든 단순
   항목번호든 모두 제거하도록 단순화했다.
5. **손실/이익 어순이 회사마다 다른 조합형 라벨 6종**도 함께 발견해
   `ACCOUNT_NAME_ALIASES`에 추가했다 — "매출총이익(총손실)"/"매출총손실(이익)"/
   "영업이익(영업손실)"/"영업손실(이익)"/"당기순이익(순손실)"/"당기순손실(이익)".
   `_apply_sign()`은 라벨에 "이익"/"손실" 존재 여부만으로 판정해 어순과
   무관하게 이미 정확히 동작하므로, alias 매핑 누락만 채우면 됐다.

수정 후 재스캔하니 92건 → **2건**만 남았고, 둘 다 의도적으로 매핑되지 않아야
하는 진짜 예외였다 — "XI. 당기순이익(손실)의 귀속"(연결재무제표에서 지배기업/
비지배지분 귀속분을 나누는 별도 분석 행, net_income 요약 행이 아님)과
"Ⅹ. 당기순이익(손손실)"(원문 자체의 오타 — "손"이 중복 — 단일 문서 1건뿐이라
alias로 흡수하지 않고 그대로 둔다, 과잉 일반화 방지 원칙 유지). 새 라벨
9종(로마숫자 lookalike 6종 + XII + 개행 + CF 항목번호 참조)을
`test_normalize_account_label`에, 조합형 라벨 6종을
`test_account_name_aliases_cover_reversed_order_combined_labels`에, "귀속"
행이 매핑되면 안 된다는 것을 `test_normalize_account_label_does_not_map_net_income_attribution_line`에
회귀 테스트로 남겼다. 실제 프로덕션 문서 3건을 새 fixtures로 추가해
end-to-end로도 검증했다 — 제이엠테크노(`20230405001652`, 소문자 l 계열이
BS/IS/CF 전 구간에 걸침) / 대한산업주식회사(`20220406002584`, XII) /
한미프랜트주식회사(`20230327000686`, CF 항목번호 참조). `pytest tests/ -q`
**271 passed**(기존 253 + 신규 18).

**실측 임팩트**: 기존 프로덕션 DB의 `parse_status=PARTIAL`이면서 `rcept_no`가
있는 229건 전부를 새 파서로 재파싱해 비교했다(DB는 갱신하지 않고 읽기 전용
비교만) — **36건(15.7%)이 OK로 정정될 것으로 확인**했다(FAILED로 새로
악화된 건 0건). 소급 재파싱은 하지 않았다(M7 CF/EUC-KR/직전 noncurrent_assets
수정과 동일 전례) — 신규 Phase 2 실행분부터 적용되고, 기존 PARTIAL 229건은
로컬 캐시만 읽으면 되므로(쿼터 0건) 사용자가 원하면 다음 세션에 일괄 재파싱할
수 있다. **교훈 갱신**: 앞서 "실측 2건만 근거로 확장, 과잉 일반화 방지"라고
적었던 판단은 스캔 범위 자체가 너무 좁았던 것이었다 — 사용자가 구체적으로
"현금흐름표도 마찬가지 아니냐"고 짚어준 덕분에 전수 스캔까지 갔고, 결과적으로
2건이 아니라 4가지 독립된 근본 원인·92건의 실제 라벨 변형을 찾아냈다. 라벨
정규화류 버그를 다룰 때는 fixtures 30건 표본이 아니라 **로컬 문서 캐시
전체(현재 약 4,900건, API 호출 0건으로 스캔 가능)를 대상으로 근접 불일치를
찾는 스크립트**를 먼저 돌리는 것이 개별 사례를 하나씩 쫓는 것보다 훨씬
효율적이라는 것을 확인했다 — 다음에 유사한 "라벨이 안 잡힌다" 계열 버그를
다룰 때 이 스캔 방법을 재사용할 것(스크립트는 스캐치패드에만 남기고
커밋하지 않음, 기존 관행).

작업을 시작하기 전에 반드시 아래 두 문서를 먼저 읽으세요 —
이 저장소의 유일한 진실 소스(source of truth)입니다.

- [PRD.md](PRD.md) — 제품 요구사항: 무엇을, 왜 만드는지, 확보 가능한 데이터 항목, 리스크
- [상세개발계획.md](상세개발계획.md) — 위 PRD를 웹앱으로 구현하기 위한 기술 설계:
  아키텍처, DB 스키마, API 설계, 파이프라인 단계, 마일스톤

코드를 작성하기 시작하면, 실제 구현이 두 문서와 달라지는 지점(설계 변경, 스파이크 결과 등)이
생길 수 있습니다. 그런 경우 이 CLAUDE.md와 상세개발계획.md를 함께 갱신해 다음 세션이
최신 상태를 참고할 수 있게 하세요.

## 제품 개요

지역 / 매출액 범위 / 업종 조건을 입력하면 OpenDART API 기반으로 **외부감사대상 비상장
법인**의 기본정보 + 요약 재무정보(당기·전기)를 자동 수집하는 도구. 세무회계사무소가
신규 거래처를 발굴하기 위한 용도이며, **데이터 수집기 + 결과 조회 웹앱까지가 이
프로젝트의 전체 범위다** (2026-07-17, 전단지/진단자료 생성 등 활용 단계는 범위에서
제외하기로 확정 — 아래 "Phase 2(전단지 생성) 범위 제외 확정" 참고).

## 핵심 아키텍처 (계획)

```
브라우저 (React SPA)
  └─ REST API (폴링 방식 진행률 조회)
      FastAPI 서버 ─── SQLite (corp_cache / corp_profiles / jobs / results)
        └─ 백그라운드 워커: 6단계 수집 파이프라인
            └─ OpenDART API (corpCode / list / company / document)
```

- 백엔드: Python 3.12 + FastAPI, 수집 작업은 `BackgroundTasks` 기반 Job으로 실행
  (수 분~수 시간 소요, 진행률은 프론트가 폴링)
- DB: SQLite (SQLAlchemy) — 단일 파일, 배포 시 PostgreSQL 전환 가능하게 설계
- 프론트: React 18 + Vite + TypeScript (Mantine 또는 shadcn/ui)
- HTTP: `httpx` 비동기 (OpenDART 병렬 호출, 타임아웃/재시도)
- 재무제표 파싱: XML 1순위(`lxml`) → PDF 2순위(`pdfplumber`) → HWP는 실패 기록만
- 계획된 디렉터리 구조와 각 모듈의 책임은 [상세개발계획.md §3](상세개발계획.md)의 트리를 그대로 따를 것

### 왜 이렇게 설계되었는가 — 반드시 알아야 할 구조적 제약

1. **OpenDART에는 지역 검색 파라미터가 없다.** 회사 주소는 기업개황(company.json)을
   회사별로 1건씩 조회해야만 알 수 있어, "김해만 검색"해도 후보 전체(연간 약 3~4만 개사)의
   기업개황이 필요하다. 이를 해결하기 위해 두 가지 대응을 계획해 두었다:
   - **대응 1(우선)**: 공공데이터포털 금융위원회_기업기본정보 API로 주소 DB를 일괄
     구축한 뒤 회사명 매칭으로 지역 후보를 먼저 추리고, 추려진 후보만 DART company.json으로
     확정한다. M1 마일스톤에서 소형 외감법인 커버리지를 스파이크로 검증해야 채택 확정.
   - **대응 2(폴백)**: `corp_profiles` 전역 캐시 테이블에 조회한 기업개황을 Job과 무관하게
     영구 저장해, 재검색 시 캐시만으로 즉시 필터링한다 (최초 1회는 일일 한도 20,000건
     제약으로 약 2일 소요).
   - 두 방식 다 인터페이스를 동일하게 유지해 교체 비용을 최소화하도록 설계됨
     ([상세개발계획.md §4-1](상세개발계획.md)).
2. **비상장 외감법인은 재무제표 API(fnlttSinglAcntAll 등)를 지원하지 않는다.** 상장법인/IFRS
   사업보고서 제출대상만 지원되므로, 재무정보는 **감사보고서 원문(document.xml)을
   다운로드해 직접 파싱**해야 한다. 이것이 파이프라인에서 가장 리스크가 큰 구간(M3,
   [상세개발계획.md §4-4](상세개발계획.md))이다.
3. **매출액은 구조적으로 사후 필터일 수밖에 없다.** 원문을 파싱하기 전에는 매출액을 알
   수 없기 때문. 대신 지역·업종 필터를 먼저 통과시켜 다운로드/파싱 대상을 최소화한다.
4. **일일 API 호출 한도 20,000건.** `dart_client.py`가 호출 카운터를 내장해 상한 도달 시
   Job을 `PAUSED_QUOTA`로 자동 전환, 다음 날 재개 가능해야 한다. 각 STEP은 DB에 체크포인트를
   남겨 **중단 후 이어하기(resume)**가 가능해야 함.
5. **파싱은 100% 자동화되지 않는다.** 회사마다 원문 서식이 달라 `parse_status`
   (OK/PARTIAL/FAILED)를 결과마다 남기고, 화면에서 검수 필요 건을 필터링해 재시도할 수
   있게 한다.

### DB 스키마 핵심 테이블

- `corp_cache`: corpCode.xml 전체 고유번호 목록 캐시
- `corp_profiles`: 기업개황 **전역** 캐시 (Job과 무관, 재검색 시 재사용 — 지역 필터 성능의 핵심)
- `jobs`: 검색 조건 + 진행 상태 (`PENDING/RUNNING/PAUSED_QUOTA/DONE/FAILED/CANCELLED`)
- `results`: 회사 1건 = 1행, 기본정보 + 당기(`_cur`)/전기(`_prv`) 재무 13항목 + `parse_status`
- `api_usage`: 일일 호출량 카운터

전체 컬럼 정의는 [상세개발계획.md §5](상세개발계획.md) 참고.

## 개발 시작 시 참고사항

- API 키(OpenDART, 공공데이터포털)는 `.env` + `pydantic-settings`로 관리, 코드 하드코딩 금지.
  프론트에는 절대 노출하지 않고 모든 DART 호출은 백엔드 경유.
- 마일스톤 순서(M1 기반 구축 → M2 파이프라인 → M3 파싱 → M4 프론트 → M5 검수)를 따르는 것을
  권장. 특히 **M1의 금융위 API 커버리지 스파이크**는 이후 지역 필터 구현 방식(대응 1 vs 2)을
  결정하므로 가장 먼저 검증해야 한다.
- `backend/tests/fixtures/`에 샘플 감사보고서 원문(10개사)을 두고 파서 단위 테스트를 작성하는
  구조로 계획되어 있다 — 실제 원문 파일이 없으면 M3 작업을 시작할 수 없으니 먼저 확보할 것.

### 백엔드 실행/테스트 명령 (M1 스캐폴딩 완료 후 실제 확인된 명령)

```
cd backend
python -m venv .venv            # Python 3.11 또는 3.12 권장 (아래 "Python 버전 주의" 참고)
source .venv/Scripts/activate   # Windows Git Bash 기준. PowerShell은 .venv\Scripts\Activate.ps1
pip install -r requirements.txt

cp .env.example .env            # 실제 키 발급 후 값 채워넣기 (커밋 금지)

uvicorn app.main:app --reload   # http://127.0.0.1:8000, 기동 시 SQLite 테이블 자동 생성
pytest tests/ -q                # 파서 fixtures 확보 전에는 placeholder 테스트만 존재
```

**Python 버전 주의**: 이 개발 환경의 기본 `python`이 3.14였는데, 3.14용 `pandas`/`lxml` 등의
사전 빌드 wheel이 아직 없어 `pip install -r requirements.txt`가 Meson/C 빌드 단계에서 실패했다
(Visual Studio 빌드 도구 필요). **Python 3.11 또는 3.12로 가상환경을 만들 것** — 이 저장소는
`py -3.11 -m venv .venv`로 확인 완료. frontend는 아직 없음(M4 착수 시 이 섹션에 `npm run dev`,
`npm run lint` 추가할 것).
