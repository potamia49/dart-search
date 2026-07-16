# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
신규 거래처를 발굴하기 위한 용도(Phase 2에서 조건에 맞는 회사에 보낼 전단지/진단자료 생성으로
이어짐)이며, **Phase 1(현재 범위)은 데이터 수집기 + 결과 조회 웹앱까지만** 다룬다.
전단지 생성은 별도 착수 예정(Phase 2)이지만, Phase 1에서 수집하는 데이터 필드는 Phase 2 요구사항까지
미리 고려해 설계되어 있다 (재수집 방지) — [상세개발계획.md §10](상세개발계획.md).

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
