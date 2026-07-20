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

> **이 섹션은 요약이다.** 세션마다 쌓인 상세 조사·실측·버그 수정 경위(왜 이렇게
> 됐는지)는 전부 [개발이력.md](개발이력.md)에 시간순으로 보존돼 있다. 여기는
> "지금 무엇이 참인가"만 담는다 — 새 세션에서 상태가 바뀌면 여기부터 갱신하고,
> 경위는 개발이력.md 맨 아래에 이어서 적을 것.

**마일스톤 M1~M8 전체 완료(2026-07-21 기준).** 스캐폴딩(M1) → 수집 파이프라인
+ 재무제표 파싱(M2/M3) → 프론트엔드(M4) → 실전 검수(M5) → 아키텍처 재설계
설계·구현(M6/M7) → 재설계 파이프라인 전환 및 정합성 검증(M8)까지 마쳤다.
제품 범위는 **"데이터 수집기 + 결과 조회 웹앱"으로 확정**돼 있다 — 전단지/
진단자료 생성 등 활용 단계(구상 당시 "Phase 2"라 불렀던 것)는 범위에서
완전히 제외했다(2026-07-17).

### 현재 아키텍처 (M8 재설계 후 최종 상태)

Job은 `phase` 컬럼(`CANDIDATES`/`FINANCIALS`)으로 2단계로 나뉜다 —
`POST /api/jobs`는 후보 확정(Phase 1)까지만 실행하고 멈추며, 사용자가 후보
목록을 검토한 뒤 `POST /api/jobs/{id}/start-financials`를 명시적으로 호출해야
실제 DART 원문 크롤링(Phase 2)이 시작된다.

- **Phase 1(후보 확정)은 외부 API 호출이 0건이다.** `dart_corp_index`(DART
  corpCode 전수 인덱스, `corp_code`가 PK) 로컬 DB 쿼리만으로 지역/업종/상장
  여부를 확정한다. 주소·대표자·업종명은 DART 정본 데이터라 그 자리에서 확정치다.
- **금융위(FSC) API 기반의 두 메커니즘은 모두 제거됐다**:
  - "A3"(건별 재무 사전 스크리닝, `GetFinaStatInfoService_V2`로 매출액/총자산을
    미리 걸러 다운로드 대상을 줄이던 최적화)는 스냅샷이 최대 1년 묵어 있어
    조건에 맞는 회사의 **25.3%를 조용히 누락**시켰다 — 폐기.
  - "A4"(이름 매칭으로 corp_code를 추정하던 폴백)는 동명이인 corp_code
    오매칭(실측 11.6~12.7%)의 근본 원인이었다 — `dart_corp_index`가
    `corp_code` 자체를 PK로 가지므로 이름 매칭 자체가 불필요해져 제거됐다.
- **매출액/총자산 참고값**(`fsc_financial_stat` 테이블에서 가져온
  `results.ref_revenue`/`ref_total_assets`/`ref_fin_year`)은 **오직 Phase 2
  처리 순서 결정(조건 밴드 근접도順 정렬)에만 쓰이고, 후보를 제외하는 데는
  절대 쓰이지 않는다.** 최종 포함/제외 판정은 항상 B4(Phase 2, 실제 감사보고서
  원문을 파싱한 뒤의 사후 필터) 한 곳에서만 이뤄진다 — `results._cur`/`_prv`
  (확정치)와 `ref_*`(참고치)는 컬럼 자체가 분리돼 있어 섞일 위험이 없다.
- **`fsc_corp_index`(구 대응 1의 산물, data.go.kr `getCorpOutline_V2` 전수
  크롤, 633,968행)는 실전 3개 지역 완주 + 오매칭 0 검증 후 삭제 완료
  (2026-07-21)**. `fsc_financial_stat`(참고값 스냅샷)은 이름이 비슷하지만
  별개 테이블이며 계속 사용 중이다 — 혼동 주의.
- **업종 필터는 DART 자체 업종 트리(대분류 21 / 중분류 77 / 소분류 234)**를
  쓴다. 세분류·세세분류는 회사별 분류 깊이 편차로 prefix 매칭에서 조용한
  누락(20.9%/41.3%)이 발생해 화면에 노출하지 않는다.
- **동명 회사 위치 결합(merge) 정합성**: `dart_corp_index`를 갱신하는
  `merge_by_position()`은 회사명만으로 정합성을 검사해 동명 회사끼리 자리가
  바뀌면 주소·업종이 조용히 교차될 수 있다 — `reconcile_ambiguous_rows()`가
  위험 그룹만 DART 정본으로 재대조하며, `dart-index/refresh`가 크롤 완료 시
  자동으로 이어서 실행한다(끄려면 `reconcile: false`). 상태는
  `GET /api/meta/dart-index/status`의 `reconcile_pending`으로 확인 가능.

### 파서(dart-parser) 핵심 사실

- 원문은 **XML이 절대다수**(HWP 미구현, PDF는 best-effort). XML 선언부의
  인코딩 표기를 신뢰하지 말 것 — 실측 약 4.4%가 선언과 달리 EUC-KR/CP949였다.
  `_decode_raw_xml()`이 UTF-8 실패 시 CP949로 자동 폴백한다.
- **계정과목 라벨 표기가 회사마다 크게 다르다**(로마숫자 유니코드/아스키/유사
  문자(l, i, Ι, ∥) 혼용, 셀 안 개행, 각주·산식 접미어 등). 새로운 "라벨이
  안 잡힌다" 계열 버그를 다룰 때는 fixtures 30건 표본이 아니라 **로컬 문서
  캐시 전체(API 호출 0건으로 스캔 가능, 현재 약 4,900건)를 대상으로 근접
  불일치를 찾는 스크립트**를 먼저 돌리는 것이 개별 사례 추적보다 훨씬
  효율적임을 확인했다(2026-07-21).
- **"손실" 라벨 부호 처리**(`xml_parser.py::_apply_sign`): 판정은 **반드시
  `normalize_account_label`로 공백·개행을 제거한 라벨**로 한다(alias 조회와
  동일 기준). 규칙은 "정규화 라벨에서 **먼저 나오는** 이익/손실 키워드가 주
  계정":
  - 순수 손실("영업손실" — "이익" 없음): 원문이 부호 없이 양수라 항상 반전.
  - 이익-primary 조합형("영업이익(손실)" — 이익이 앞): 원문 부호가 곧 경제적
    부호라 그대로 신뢰(반전 안 함).
  - **손실-primary 조합형("매출총손실(이익)"/"영업손실(이익)"/"당기순손실(이익)"
    — 손실이 앞)**: 양수=손실, 괄호=이익이라 원문 부호가 경제적 부호와 **반대** →
    반드시 반전한다. "이익이 있으면 무조건 신뢰"로 묶으면 적자 기업이 흑자로
    뒤집힌다(2026-07-21 dart-qa 실측, gross_profit==revenue-cogs 항등식으로 확정).
  - 두 실측 버그(라벨 글자 사이 공백 "영    업    손    실"으로 부분문자열
    매칭이 깨지던 것 + 손실-primary 조합형 오처리)는 회귀 테스트(fixtures
    20230404002324/20260413003038/20250414000612)로 잠갔다.
- **매출총이익은 비율이 아니라 금액**(`gross_profit_cur/prv`)으로 원문에서
  직접 파싱한다(2026-07-20, 기존 `gross_margin`(%) 계산값 폐기).
- **현금흐름표 4항목(CF) + 세부계정 펼치기, 감사인명/사무소주소
  (`auditor_name`/`auditor_address`), 원문 섹션 열람 API**(재무상태표/
  손익계산서/현금흐름표/주석/감사의견 — 로컬 캐시만 읽어 쿼터 0건)가 모두
  구현돼 있다.
- **스키마 확장은 항상 "컬럼 추가 + 소급 재파싱 없음"** 패턴을 따른다(신규
  Phase 2 실행분부터만 채워짐). **소급 재파싱 대기 후보 4종은 2026-07-21
  일괄 처리 완료**(사용자 명시 요청에 의한 일회성 예외 — 쿼터 0건, 스크립트
  `backend/scripts/reparse_local_cache.py`, `--dry-run` 지원·재실행 멱등).
  대상 1,211건(rcept 보유+파싱완료분, 캐시 결측 0) 재파싱 결과: PARTIAL→OK
  36건, `noncurrent_assets` NULL→값 4건, EUC-KR 복구분은 이미 전부 OK/PARTIAL
  이라 신규 개선 없음(추정 51건은 결과테이블이 아닌 전체 문서캐시 기준
  수치였음), `auditor_*` NULL은 원문 서명란 부재에 의한 **진성 결측**으로
  확인돼 재파싱으로 채울 것 없음. **부수 효과로 조합형 라벨 부호 오류 668건**
  (2026-07-20 커밋 260c7f9 이전 파서가 "영업이익(손실)" 등을 순수 손실처럼
  잘못 반전시켰던 것)**과 `gross_profit` NULL→금액 약 939건도 함께 교정됨**
  (전 필드 전이 전수 분류로 value→NULL 회귀 0건 검증). OK 982→1,018,
  PARTIAL 229→193. **이어서 dart-qa 독립검증이 `_apply_sign` 부호 버그 2종
  (라벨 글자 사이 공백 + 손실-primary 조합형)을 발견해 62개 필드/20개 행을
  추가 교정**(전부 순수 부호 반전, gross_profit==revenue-cogs 항등식 자체검증
  `reparse_local_cache.py --verify`로 부호 오분류 0 확인). `pytest` 279 passed.
  · 미해결 후속: 상세열 "-"를 명시적 0으로 오파싱해 cogs=0이 되는 별개 버그
  2건(20230410002954/20230406001585) — gross_profit 부호는 정확, 부호 이슈
  아님. `_first_amount`/`parse_won_amount`의 "-" 의미 분리 필요(향후 과제).

### 결과 조회 화면

`parse_status`(OK/PARTIAL/FAILED) 필터 + "감사보고서 없음"(`rcept_no`
NULL, 검수 불필요)과 "파싱 실패"(검수 필요)를 구분 표시, 컬럼 정렬/검색,
Excel/CSV 내보내기(현재 필터·정렬 반영), 재무 이력(최근 N년) 표 + 원문
섹션 열람 모달, 후보 목록 화면에서 개별 후보 선택 제외(Phase 2 시작 전).

### 알려진 구조적 제약 (변하지 않음)

1. OpenDART에는 지역 검색이 없다 — 그래서 위 Phase 1/2 아키텍처가 필요하다.
2. 비상장 외감법인은 DART 재무제표 API를 지원하지 않는다 — 감사보고서
   원문을 직접 파싱해야 한다.
3. 매출액/총자산은 원문을 열기 전엔 알 수 없어 구조적으로 사후 필터다.
4. DART 일일 호출 한도 20,000건 — Job은 `PAUSED_QUOTA`로 자동 전환,
   체크포인트로 resume.
5. 파싱은 100% 자동화되지 않는다 — `parse_status`로 검수 대상을 남긴다.

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
제외하기로 확정 — 경위는 [개발이력.md](개발이력.md)의 "Phase 2(전단지 생성) 범위
제외 확정" 참고).

## 핵심 아키텍처 (M8 재설계 후 현재 구현 기준)

```
브라우저 (React SPA)
  └─ REST API (폴링 방식 진행률 조회)
      FastAPI 서버 ─── SQLite (dart_corp_index / fsc_financial_stat / jobs / results / financial_snapshots)
        └─ 백그라운드 워커: Phase 1(후보 확정, 로컬 쿼리만) → Phase 2(DART 원문 크롤링·파싱)
            └─ OpenDART API (corpCode / list / document) — Phase 2에서만 호출
```

- 백엔드: Python 3.12 + FastAPI, 수집 작업은 `BackgroundTasks` 기반 Job으로 실행
  (수 분~수 시간 소요, 진행률은 프론트가 폴링)
- DB: SQLite (SQLAlchemy) — 단일 파일, 배포 시 PostgreSQL 전환 가능하게 설계
- 프론트: React 18 + Vite + TypeScript (Mantine)
- HTTP: `httpx` 비동기 (OpenDART 병렬 호출, 타임아웃/재시도)
- 재무제표 파싱: XML 1순위(`lxml`) → PDF 2순위(`pdfplumber`) → HWP는 실패 기록만
- 디렉터리 구조와 각 모듈의 책임은 [상세개발계획.md §3](상세개발계획.md)의 트리를 참고할 것
  (최초 계획 트리 기준이며, M6~M8에서 추가된 모듈은 아래 "프로젝트 현황" 요약과
  [개발이력.md](개발이력.md) 참고)

### 왜 이렇게 설계되었는가 — 반드시 알아야 할 구조적 제약

1. **OpenDART에는 지역 검색 파라미터가 없다.** 회사 주소는 기업개황(company.json)을
   회사별로 1건씩 조회해야만 알 수 있어, "김해만 검색"해도 후보 전체(연간 약 3~4만 개사)의
   기업개황이 필요하다 — 이것이 Phase 1/Phase 2 분리 아키텍처의 근본 이유다.
   **최초 계획은 공공데이터포털 금융위 API로 이를 우회하는 것(대응 1/대응 2)이었으나,
   M8 재설계로 폐기하고 DART corpCode 전수 인덱스(`dart_corp_index`) 자체를 로컬에
   구축해 지역/업종/상장여부를 API 호출 없이 쿼리하는 방식으로 대체했다** — 금융위
   API 기반 방식은 최신성이 1년까지 뒤처져 조건에 맞는 회사를 조용히 놓치거나(사전
   재무 스크리닝) 이름 매칭 오류로 동명이인 회사를 잘못 연결하는(corp_code 추정)
   문제가 실측으로 확인됐기 때문이다. 상세 경위는 [개발이력.md](개발이력.md)의
   "M8" 관련 기록 참고, 최신 설계는 [상세개발계획.md §4-7~§4-10](상세개발계획.md) 참고.
2. **비상장 외감법인은 재무제표 API(fnlttSinglAcntAll 등)를 지원하지 않는다.** 상장법인/IFRS
   사업보고서 제출대상만 지원되므로, 재무정보는 **감사보고서 원문(document.xml)을
   다운로드해 직접 파싱**해야 한다. 이것이 파이프라인에서 가장 리스크가 큰 구간(M3,
   [상세개발계획.md §4-4](상세개발계획.md))이다.
3. **매출액/총자산은 구조적으로 사후 필터일 수밖에 없다.** 원문을 파싱하기 전에는 확정치를
   알 수 없기 때문. 금융위 API 참고값(`ref_*`)으로 Phase 2 처리 순서만 정할 뿐, 포함/제외
   판정은 항상 원문 파싱 이후에만 이뤄진다(위 "프로젝트 현황" 요약 참고).
4. **일일 API 호출 한도 20,000건.** `dart_client.py`가 호출 카운터를 내장해 상한 도달 시
   Job을 `PAUSED_QUOTA`로 자동 전환, 다음 날 재개 가능해야 한다. 각 단계는 DB에 체크포인트를
   남겨 **중단 후 이어하기(resume)**가 가능해야 함.
5. **파싱은 100% 자동화되지 않는다.** 회사마다 원문 서식이 달라 `parse_status`
   (OK/PARTIAL/FAILED)를 결과마다 남기고, 화면에서 검수 필요 건을 필터링해 재시도할 수
   있게 한다.

### DB 스키마 핵심 테이블

최초 계획(§5)의 기본 골격 + M6~M8에서 추가된 핵심 테이블:

- `dart_corp_index`: DART corpCode 전수 인덱스(`corp_code` PK) — Phase 1 지역/업종/
  상장여부 필터의 유일한 데이터 소스(M8, 외부 API 호출 0건)
- `fsc_financial_stat`: 금융위 API 매출액/총자산 참고값 스냅샷 — Phase 2 처리 순서
  결정에만 쓰이고 판정에는 쓰이지 않음(M8)
- `jobs`: 검색 조건 + 진행 상태(`PENDING/RUNNING/PAUSED_QUOTA/DONE/FAILED/CANCELLED`)
  + `phase`(`CANDIDATES`/`FINANCIALS`)
- `results`: 회사 1건 = 1행, 기본정보 + 당기(`_cur`)/전기(`_prv`) 재무 항목(현금흐름표
  포함) + `parse_status` + 참고값(`ref_*`) + `excluded_by_*` 필터 플래그
- `financial_snapshots`: 회사×회계연도 단위 다년치 재무 이력(M2 STEP 7)
- `corp_cache`: corpCode.xml 전체 고유번호 목록 캐시 (레거시, `dart_corp_index`와 역할 일부 중복)
- `api_usage`: 일일 호출량 카운터

전체 컬럼 정의는 [상세개발계획.md §5](상세개발계획.md) 참고. `fsc_corp_index`
테이블은 2026-07-21 삭제됐다 — `fsc_financial_stat`과 이름이 비슷하지만 별개였고
혼동하지 말 것(상세는 [개발이력.md](개발이력.md) 참고).

## 개발 시작 시 참고사항

- API 키(OpenDART, 공공데이터포털)는 `.env` + `pydantic-settings`로 관리, 코드 하드코딩 금지.
  프론트에는 절대 노출하지 않고 모든 DART 호출은 백엔드 경유.
- 마일스톤 M1~M8은 모두 완료된 상태다(위 "프로젝트 현황" 참고) — 이 저장소에서 새로
  작업할 때는 그 최종 상태를 기준으로 삼을 것. M1 시절 계획이었던 "금융위 API 커버리지
  스파이크로 대응 1 vs 2를 결정" 같은 초기 판단 과정은 M8 재설계로 이미 대체됐다(위
  "핵심 아키텍처" §1 참고) — 지금 시점에 다시 수행할 필요는 없다.
- `backend/tests/fixtures/`에 실제 감사보고서 원문 샘플(현재 20건 이상)과
  `manifest.json`이 있고, 파서 단위 테스트가 이를 근거로 작성돼 있다. 새 원문 서식
  변형을 발견하면 이 fixtures에 추가하는 기존 관행을 따를 것.

### 백엔드 실행/테스트 명령 (M1 스캐폴딩 완료 후 실제 확인된 명령)

```
cd backend
python -m venv .venv            # Python 3.11 또는 3.12 권장 (아래 "Python 버전 주의" 참고)
source .venv/Scripts/activate   # Windows Git Bash 기준. PowerShell은 .venv\Scripts\Activate.ps1
pip install -r requirements.txt

cp .env.example .env            # 실제 키 발급 후 값 채워넣기 (커밋 금지)

uvicorn app.main:app --reload   # http://127.0.0.1:8000, 기동 시 SQLite 테이블 자동 생성
pytest tests/ -q                # 2026-07-21 기준 279 passed
```

**Python 버전 주의**: 이 개발 환경의 기본 `python`이 3.14였는데, 3.14용 `pandas`/`lxml` 등의
사전 빌드 wheel이 아직 없어 `pip install -r requirements.txt`가 Meson/C 빌드 단계에서 실패했다
(Visual Studio 빌드 도구 필요). **Python 3.11 또는 3.12로 가상환경을 만들 것** — 이 저장소는
`py -3.11 -m venv .venv`로 확인 완료.

### 프론트엔드 실행 명령

```
cd frontend
npm install
npm run dev     # Vite dev 서버, /api는 vite.config.ts의 proxy로 백엔드(port 8000)에 전달
npm run build   # tsc 타입체크 포함
npm run lint    # oxlint
```
