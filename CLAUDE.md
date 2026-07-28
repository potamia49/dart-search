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
| 2026-07-22 | 구 파이프라인 죽은 코드 물리 삭제 — `run_job()`(구 STEP 1~7 오케스트레이터), STEP 3 지역/업종 필터(`_run_region_industry_filter` + 하위 헬퍼), `CorpProfile` 모델/`corp_profiles` 정의, A3 `DartClient.get_summary_financial_stat()`, 관련 test_pipeline.py 18건 제거 | dart-backend | "M8에서 A3/A4가 제거됐다"는 기존 서술이 실제로는 **호출 경로만 끊기고 코드는 파일에 남아 있던** 상태였음이 dart-qa 2회 정밀 조사로 드러남(프로덕션 import 0건 확정). 죽은 코드를 실제 소스에서 삭제해 확정. 살아있는 `list_summary_financial_stats`/`FscCorpInfoClient`/Phase 2 B1 폴백(`_resolve_alternative_corp_code`/`CorpCache`)은 무변경 |

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
  - **주의(2026-07-22):** M8 3단계 당시의 위 "제거"는 실제로는 **호출 경로만
    끊고 코드는 파일에 남아 있던** 상태였다(구 오케스트레이터 `run_job()`(STEP
    1~7)이 프로덕션에서 import되지 않는 죽은 코드였고, A3 메서드
    `DartClient.get_summary_financial_stat()`·STEP 3 지역/업종 필터
    `_run_region_industry_filter()`·`CorpProfile` 모델도 그 죽은 경로에서만
    참조됐다). dart-qa 2회 정밀 조사로 프로덕션 호출 0건을 확정한 뒤
    2026-07-22에 이들을 **실제 소스에서 물리 삭제**했다(테스트 18건 포함) —
    "설계상 제거"가 이제 "코드에서도 부재"로 확정됐다. `list_summary_financial_stats`
    (이름이 비슷하나 `crno` 없이 연도 단위 전수 페이징하는 `ref_*` 적재용 별개
    메서드)·`FscCorpInfoClient`·Phase 2 B1 폴백(`_resolve_alternative_corp_code`/
    `CorpCache`)은 **살아있는 코드라 무변경**. 기존 DB의 `corp_profiles` 물리
    테이블은 무해하게 남겨 뒀다(모델 삭제로 이후 생성 안 됨).
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

> 아래는 "지금 무엇이 참인가"만 담은 요약이다. 각 항목의 실측 수치·정규식
> 설계·회귀 fixtures 등 상세 경위는 전부 [개발이력.md](개발이력.md)에 날짜순으로
> 있다.

- 원문은 **XML이 절대다수**(HWP 미구현, PDF는 best-effort). XML 선언부의
  인코딩 표기를 신뢰하지 말 것 — 실측 약 4.4%가 선언과 달리 EUC-KR/CP949였다.
  `_decode_raw_xml()`이 UTF-8 실패 시 CP949로 자동 폴백한다.
- **계정과목 라벨 표기가 회사마다 크게 다르다**(로마숫자 유니코드/아스키/유사
  문자 혼용, 셀 안 개행, 각주·산식·EPS 병기 접미어 등). 새로운 "라벨이 안
  잡힌다" 계열 버그를 다룰 때는 fixtures 표본이 아니라 **로컬 문서 캐시
  전체(API 호출 0건으로 스캔 가능, 현재 약 4,900건)를 대상으로 근접 불일치를
  찾는 스크립트**를 먼저 돌리는 것이 개별 사례 추적보다 훨씬 효율적임을
  확인했다. 이 관행으로 발견·수정된 라벨 매칭 버그들(각주 참조의 한글 접속사,
  연결당기순이익, EPS 병기 접미어, "당기순손익" 등 요약행 변형 5종, IFRS
  첨부 서식의 값 없는 소계 헤더 등)은 전부 개발이력.md에 기록돼 있다
  (2026-07-21~23).
- **"손실" 라벨 부호 처리**(`xml_parser.py::_apply_sign`): 판정은 반드시
  `normalize_account_label`로 공백·개행을 제거한 라벨로 한다(alias 조회와
  동일 기준). 규칙은 "정규화 라벨에서 **먼저 나오는** 이익/손실 키워드가 주
  계정" — 순수 손실은 항상 반전, 이익-primary 조합형("영업이익(손실)")은
  원문 부호 신뢰, **손실-primary 조합형**("매출총손실(이익)")은 경제적 부호가
  반대라 반드시 반전한다. 실측 버그 2종(라벨 글자 사이 공백, 손실-primary
  오처리)은 회귀 테스트로 잠갔다.
- **매출총이익은 비율이 아니라 금액**(`gross_profit_cur/prv`)으로 원문에서
  직접 파싱한다(2026-07-20, 기존 `gross_margin`(%) 계산값 폐기).
- **IFRS "(첨부)재무제표" 첨부문서 구조 지원 완료**(2026-07-22). "재무상태표"/
  "손익계산서"/"현금흐름표" TITLE 없이 "(첨부)재무제표" 하나 아래 4개 재무제표가
  모두 들어가는 서식 — 별도 부호 규약(`_apply_sign_ifrs`, FINANCE와 정반대라
  손익은 원문 부호 신뢰·cogs/sga만 abs)과 별도 추출 경로
  (`_extract_attach_section`)로 처리한다. **원문 섹션 열람(`document_sections.py`)
  과 세부계정 펼치기(`account_detail.py`)도 이 서식을 인식**하도록
  2026-07-27에 공용 워커(`xml_parser.walk_statement_tables`)로 통합돼, 세 모듈이
  서식 인식 기준을 공유한다(전에는 파이프라인만 지원해 UI 두 곳이 "원문을
  찾을 수 없음"으로 표시되는 drift가 있었다).
- **현금흐름표 4항목(CF) + 세부계정 펼치기, 감사인명/사무소주소
  (`auditor_name`/`auditor_address`), 원문 섹션 열람 API**(재무상태표/
  손익계산서/현금흐름표/주석/감사의견 — 로컬 캐시만 읽어 쿼터 0건)가 모두
  구현돼 있다.
- **영업외수익/영업외비용 2항목**(`non_operating_income`/`non_operating_expense`,
  2026-07-22)이 CF 4항목과 동형인 best-effort 필드로 구현돼 있다 — 파서/DB
  컬럼/파이프라인 매핑/API 응답/Excel·CSV 내보내기 전부 배선 완료.
  `determine_parse_status()` 판정에는 관여하지 않는다(결측이어도 PARTIAL/
  FAILED로 안 떨어짐).
- **연도별 감사인 추출 + "감사인 변동" tri-state 플래그**(`financial_snapshots.
  auditor_name` / `results.auditor_changed` 1/0/**NULL**, 2026-07-26)가
  백엔드·프론트 모두 구현 완료. 비교는 `_auditor_key()`(pipeline.py)로 표기
  차이(공백/접미어 순서/서명자 딸림)를 흡수한 뒤 한다 — dart-qa 리뷰로 이
  정규화 자체의 버그 1종과 파서 추출 단계의 별개 버그 1종을 발견해 수정했고
  (**[보정 1]/[보정 3]**), 연도별 변경 판정은 DB에 저장하지 않고 서버가 이력
  조회 시 순수 함수로 계산해 내려준다(**[보정 2]** — 프론트 자체 비교 로직
  제거).
- **`financial_snapshots`의 재무상태표/손익계산서/CF/영업외손익 값 자체가
  구버전 파서 결과에 고정돼 있던 문제를 발견해 소급 재파싱으로 해결**
  (2026-07-26/27). STEP7이 최초 파싱 시점의 결과를 영구 보관해 이후 파서
  개선이 자동 반영되지 않던 구조적 문제였다 — `results`는 여러 차례 소급
  반영됐지만 `financial_snapshots`는 그 대상에서 빠져 있었다.
- **스키마 확장은 항상 "컬럼 추가 + 소급 재파싱 없음"** 패턴을 따른다(신규
  Phase 2 실행분부터만 채워짐). 지금까지 사용자가 명시적으로 승인한 소급
  재파싱이 여러 차례 일회성으로 실행됐다(스크립트: `backend/scripts/
  reparse_local_cache.py`(results 전반, `--dry-run`/`--verify` 지원) /
  `reparse_financial_snapshot_values.py`(snapshots 값 전체) /
  `backfill_auditor_names.py` / `backfill_stale_disclosure.py` 등 — 각
  실행의 대상 건수·검증 결과는 개발이력.md 참고). 잔여 미해결: 상세열 "-"를
  명시적 0으로 오파싱해 cogs=0이 되는 별개 버그 2건(20230410002954/
  20230406001585) — gross_profit 부호는 정확, 부호 이슈 아님(향후 과제).

### 결과 조회 화면

`parse_status`(OK/PARTIAL/FAILED) 필터 + "감사보고서 없음"(`rcept_no`
NULL, 검수 불필요)과 "파싱 실패"(검수 필요)를 구분 표시, 컬럼 정렬/검색,
Excel/CSV 내보내기(현재 필터·정렬 반영), 재무 이력(최근 N년) 표 + 원문
섹션 열람 모달, 후보 목록 화면에서 개별 후보 선택 제외(Phase 2 시작 전).
"감사인 변동 여부" 컬럼(목록)과 상세 재무이력의 연도별 감사인 표시·강조는
**2026-07-26 구현 완료**(위 "연도별 감사인" 항목 참고).

**사후 필터 제외 건 기본 숨김(2026-07-28 확장)**: "휴면·폐업 추정"
(`excluded_by_stale_disclosure`, 2026-07-22)과 같은 패턴으로
`excluded_by_revenue`/`excluded_by_assets`(매출액·총자산 조건 제외 건)도
전용 탭(매출액 제외/총자산 제외) 외 모든 탭에서 기본적으로 숨긴다 — 검토
계기: 총자산 조건에 안 맞아 제외된 회사가 "전체" 탭에 그대로 노출돼
사용자가 재무 이력 부재를 오인한 사례. 한 회사가 매출액·총자산 두 조건에
동시에 걸릴 수 있어(두 필터는 독립 판정) `tabToParams()`는 baseline
스프레드 대신 STALE_DISCLOSURE와 같은 조기 반환 방식을 쓴다(안 그러면
반대쪽 필드가 `false`로 남아 두 탭 어디에도 안 뜨는 사각지대가 생김).
숨김 건수 안내는 ALL 탭에서만 노출한다(`excluded_by_revenue=1`이 서려면
원문 파싱이 선행돼야 해 FAILED/NO_DISCLOSURE 등 검수 탭에서는 실제 숨김이
0건에 가까워 안내가 오해를 유발하기 때문 — `staleCount`는 이 제약이 없어
세부 탭 안내를 유지). 상단 "전체 내보내기"도 화면 필터를 그대로 따라가
이 숨김이 다운로드 파일에도 적용된다(기존 stale과 동일 원칙, 사용자
확인 완료) — 선택 항목 다운로드(`ids` 지정)는 필터 무관이라 영향 없음.

**다중 선택 다운로드(§4-11/M9, 백엔드·프론트 모두 2026-07-28 완료)**: 목록 왼쪽
체크박스로 회사를 골라 그 회사들만 별도 파일로 받는다 — 기존 "전체 내보내기"를
**대체하지 않고 병행**한다. `GET /api/jobs/{id}/export`의 `ids`(쉼표구분
`results.id`) + `include_history`(=true면 `financial_history` 시트 추가,
xlsx 전용)를 쓴다. DB 스키마 변경·추가 API 호출 0건인 순수 조회 파라미터
확장이라 기존 완료 Job에서도 즉시 쓸 수 있다. 상세 설계(정렬 규칙, 입력값
검증, 프론트 선택 상태 관리, dart-design-review 반영 내역)는
[개발이력.md](개발이력.md) 참고.

**두 다운로드의 컬럼 구성이 다르다(2026-07-28 변경)** — 같은 엔드포인트지만
**`ids` 유무 하나로만** 포맷이 갈린다. `include_history`는 `financial_history`
시트를 덧붙일 뿐 기본정보 포맷에 관여하지 않는다:

- **선택 항목 다운로드(`ids` 지정, 프론트 메뉴 3종 전부)**: 기본정보 15컬럼
  (결과ID/Job ID/고유번호/접수번호/회사명/주소/전화번호(미수집)/대표자명/
  업종코드/업종명/결산기준일/감사의견/감사인/감사인주소/감사인변동여부) +
  **"계정과목명"/"금액" long(세로) 포맷** + 맨 마지막 "파싱상태"(2026-07-28
  사용자 확정 — 계정과목명/금액보다도 뒤). 회사 1건이 당기(`_cur`) 계정과목
  19행으로 풀리고 기본정보는 각 행에 반복된다. **전기(`_prv`)는 싣지 않는다**
  (사용자 확정: "전기 항목은 전년도 당기 항목이니깐"). 값이 없는 계정과목도
  **행은 남기고 금액만 빈 값**으로 둔다(어떤 항목이 결측인지 보여야 하므로
  스킵 금지). 계정과목명은 wide 포맷 라벨에서 "(당기)"만 뗀 이름을 코드에서
  파생시켜(`SELECTION_ACCOUNT_LABELS`) 두 포맷의 라벨이 어긋날 여지를 없앴다
  (`SELECTION_ACCOUNT_COLUMNS`가 wide의 `_cur` 필드 전체와 일치하는지는
  `tests/test_exporters.py`의 드리프트 가드 테스트가 잠근다 — 새 `_cur`/`_prv`
  쌍을 추가하면 이 테스트가 먼저 깨진다). `ids`와 함께 `include_history=true`를
  주면 2시트 xlsx의 ① 시트가 이 long 포맷이고, ② `financial_history`
  시트(회사×회계연도)는 **무변경**이다.
- **필터 전체 내보내기(`ids` 없음, 상단 [Excel/CSV 다운로드])**: `include_history`
  여부와 **무관하게** 기존 wide 포맷(`RESULT_COLUMN_LABELS`, 회사 1행에 당기·전기
  전 항목) **그대로 무변경**.

구현은 `app/exporters/excel.py`의 `SELECTION_EXPORT_COLUMN_LABELS`/
`results_to_selection_dataframe()`/`export_selection_results()`이며, wide 포맷
함수(`RESULT_COLUMN_LABELS`/`results_to_dataframe()`/`export_results()`)와 완전히
분리돼 있다. 2시트 xlsx를 만드는 `export_results_with_history()`는
`use_selection_format` 인자로 둘 중 하나를 골라 ① 시트를 쓴다 — 호출부가
`selected_ids is not None`을 그대로 넘긴다.

### "최근 1년 이내 DART 공시 없음" 배제 (2026-07-21 추가)

실사례("주식회사 유진"류 — 폐업/휴면/합병소멸 등으로 실질적으로 활동을 멈춘
법인)를 걸러내기 위한 필터. `excluded_by_revenue`/`excluded_by_assets`와 같은
패턴이다 — Phase 2 **B2**가 최신 rcept_no를 찾으려고 이미 호출하는 `list.json`
응답을 재사용해 판정한다(**추가 API 호출 0건**). rcept_no 앞 8자리(접수일자)를
`results.latest_disclosure_date`에 남기고, 365일보다 오래됐거나 공시 자체가
없으면 `results.excluded_by_stale_disclosure=1`로 표시하는 순수 사후 필터
플래그다. `GET /results`/`/export`가 동일한 tri-state 쿼리 파라미터로
필터링을 지원한다. **소급 반영 완료(2026-07-22)** — 기존 완료 Job 1,211행 중
204행(16.8%)이 0→1로 전환됨(경위·검증은 개발이력.md 참고). **프론트엔드
미반영**: 결과 화면에 새 필터 탭 추가와 기본 노출 여부 결정은 dart-frontend
몫으로 남아 있다.

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
- `financial_snapshots`: 회사×회계연도 단위 다년치 재무 이력(M2 STEP 7) +
  그 연도를 당기로 감사한 `auditor_name`(2026-07-26, `results.auditor_changed`의 원천)
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

### 배포용 실행파일(.exe) 빌드 (2026-07-23 추가, 2026-07-23 배포 위치 분리)

Python/Node가 설치되지 않은 사무실 PC에도 배포할 수 있도록, PyInstaller로
백엔드(FastAPI+uvicorn)와 프론트엔드 빌드 산출물을 단일 exe로 묶는다.
API 키는 exe에 하드코딩하지 않는다 — `backend/launcher.py`가 exe 옆에
`.env`가 없으면 `.env.example`을 복사해 메모장으로 열고 안내창을 띄운 뒤
종료하며, 사용자가 키를 채워 저장하고 재실행하면 정상 구동된다(최초 1회).

**배포 산출물은 개발 저장소(`c:\claude\dart-search`)와 분리해 `C:\claude\dart-search-배포`에
둔다** (2026-07-23, 사용자 명시 요청 — 개발용 소스/DB와 실제 사무실에서 쓰는
배포본을 물리적으로 구분하기 위함). `--distpath`로 그 경로를 직접 지정해
빌드 결과가 바로 그리로 나가게 한다.

```
cd frontend && npm run build          # frontend/dist 생성 (exe에 번들됨)
cd backend && source .venv/Scripts/activate
pip install pyinstaller
pyinstaller --noconfirm --onefile --name dart-search \
  --distpath "C:/claude/dart-search-배포" \
  --add-data "../frontend/dist;frontend_dist" \
  --add-data ".env.example;." \
  --collect-all fastapi --collect-all starlette --collect-all uvicorn \
  --collect-all pydantic --collect-all pydantic_core --collect-all sqlalchemy \
  --collect-all lxml --collect-all pdfplumber --collect-all pandas \
  --collect-all openpyxl --collect-all multipart --collect-all olefile \
  --collect-all httpx --collect-all anyio \
  --hidden-import app.api.jobs --hidden-import app.api.meta --hidden-import app.api.results \
  launcher.py
# 결과물: C:\claude\dart-search-배포\dart-search.exe (약 83MB)
# (재빌드해도 같은 폴더의 기존 .env/dart_search.db/data는 그대로 남고 exe만 갱신됨 —
#  onefile 모드는 exe 하나만 --distpath에 쓰고 나머지는 손대지 않기 때문)
```

구조: `app/config.py`의 `BACKEND_DIR`은 환경변수 `DART_SEARCH_APP_DIR`(exe가
놓인 폴더)을 우선 사용하도록 분기돼 있어, DB(`dart_search.db`)/캐시/`.env`가
exe 옆에 영구히 남는다(PyInstaller onefile의 임시 압축해제 폴더가 아님).
`app/main.py`는 `DART_SEARCH_RESOURCE_DIR`(PyInstaller 번들 임시폴더,
`sys._MEIPASS`) 아래 `frontend_dist`가 있으면 `/assets`를 정적 서빙하고
나머지 모든 경로를 `index.html`로 폴백하는 catch-all 라우트를 API 라우터들
**뒤에** 등록해 React Router(BrowserRouter) 클라이언트 라우팅을 지원한다 —
두 환경변수 모두 없으면(=일반 소스 실행) 기존 동작과 100% 동일하다.
`launcher.py`는 `DART_SEARCH_PORT`(기본 8000)로 uvicorn을 띄우고, 이미 그
포트가 쓰이는 중이면(중복 실행) 새 서버를 띄우지 않고 브라우저 창만 새로
연다. `backend/build/`·`backend/*.spec`은 PyInstaller 중간 산출물이라
`.gitignore`에 등록돼 있다(커밋 대상 아님) — 최종 배포본(`backend/dist/`가
아니라 `C:\claude\dart-search-배포\`)은 저장소 바깥이라 애초에 git 추적
대상이 아니다.
