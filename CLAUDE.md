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
실제 OpenDART API로 end-to-end 스모크 테스트를 통과했다. `app/api/results.py`의
`/export`만 M4 TODO로 남아 있다. `exporters/excel.py`는 아직 M4에서 채울 골격만
있다. `frontend/`는 아직 스캐폴딩되지 않았다(M4 범위, 마일스톤 순서 준수).

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
