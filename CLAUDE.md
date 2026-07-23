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

- 원문은 **XML이 절대다수**(HWP 미구현, PDF는 best-effort). XML 선언부의
  인코딩 표기를 신뢰하지 말 것 — 실측 약 4.4%가 선언과 달리 EUC-KR/CP949였다.
  `_decode_raw_xml()`이 UTF-8 실패 시 CP949로 자동 폴백한다.
- **계정과목 라벨 표기가 회사마다 크게 다르다**(로마숫자 유니코드/아스키/유사
  문자(l, i, Ι, ∥) 혼용, 셀 안 개행, 각주·산식 접미어 등). 새로운 "라벨이
  안 잡힌다" 계열 버그를 다룰 때는 fixtures 30건 표본이 아니라 **로컬 문서
  캐시 전체(API 호출 0건으로 스캔 가능, 현재 약 4,900건)를 대상으로 근접
  불일치를 찾는 스크립트**를 먼저 돌리는 것이 개별 사례 추적보다 훨씬
  효율적임을 확인했다(2026-07-21).
  · **각주 참조의 한글 접속사**(2026-07-23, 물맑은고기팜 rcept 20260408002307):
  각주 번호를 "과"/"와"로 잇는 표기("Ⅱ. 매출원가(주석10과 13)")는 기존
  `_FOOTNOTE_SUFFIX_RE`(괄호 안=숫자/콤마/공백 가정)가 매치 실패해 각주가 안
  벗겨지고 "매출원가(주석10과13)"가 alias 조회를 못 해 cogs가 통째로 누락됐다.
  정규식을 두 대안으로 재구성해 **"주석/주" 마커가 있을 때에 한해** "과"/"와"를
  허용(마커 없는 순수 숫자 브랜치는 옛 동작 유지 → "(손실)"/"(매출액)"은 여전히
  보존). 회귀 fixtures 20260408002307.
  · **기존 완료 Job 결과에 대한 소급 반영 완료(2026-07-23, 사용자 명시 승인
  일회성 예외, 아래 "연결당기순이익" 건과 한 번의 스크립트 실행으로 함께 처리)**:
  이 두 파서 수정 커밋(8a34c2d) 이후에도 물맑은고기팜(id=4823)·한미프렉시블
  (id=4976) 등 관련 기존 results 행은 컬럼 추가만으로는 값이 채워지지 않는
  원래 원칙 때문에 여전히 수정 전 PARTIAL 상태로 남아 있었다(사용자가 화면
  스크린샷으로 지적해 발견). `backend/scripts/reparse_local_cache.py`
  (무변경 재사용, `--dry-run` 지원, **API 호출 0건**)로 results 1,211건
  (rcept 보유+파싱완료, 캐시 결측 0) 재파싱 → **27행 순수 값 추가**
  (cogs/net_income/sga NULL→값, value→NULL 회귀 0), **PARTIAL→OK 26건**
  (1건 id=4946은 다른 결측 항목이 남아 PARTIAL 유지). `--verify` 항등식
  자체검증: 부호 오분류 0·DB드리프트 0(멱등), 크기 불일치 2건은 기존
  "-"→cogs=0 버그(20230410002954/20230406001585)로 이번 재파싱과 무관.
  실행 전 `dart_search.db.bak.pre_footnote_netincome_reparse.20260723_115602`
  로 백업.
  · **연결재무제표 "연결당기순이익"**(2026-07-23, 한미프렉시블 rcept
  20260424000057): 연결 원문은 순이익 요약 행을 "연결당기순이익"으로 적어
  net_income alias 매칭에 실패해 통째로 누락됐다. **로컬 캐시 4,922건 전수 스캔상
  "연결" 접두어가 표준 필드로 매핑되는 것은 net_income 계열뿐**(연결당기순이익
  289 / (손실) 65 / 연결당기순손실 12 / (순손실) 3 = 369건, 연결매출액·연결영업
  이익 등은 캐시에 아예 없음)이라 `normalize`에서 "연결"을 전역으로 벗기는 방안은
  이득 0·blast radius만 큼 → 배제하고 **실측된 net_income 계열 4종만
  `ACCOUNT_NAME_ALIASES`에 등록**. 부호는 `normalize` 기준으로 이미 정확히 동작해
  변경 불필요, 귀속 분석 행("연결당기순이익(손실)의 귀속")은 미매핑 유지. 회귀
  fixtures 20260424000057(원본 _00761→관례상 _00760 저장). 둘 다 gross_profit==
  revenue-cogs·자산총계==부채+자본 항등식으로 부호·값 자체검증. `pytest` 289 passed.
  · **기존 완료 Job 결과에 대한 소급 반영 완료(2026-07-23)**: 위 "각주 참조의
  한글 접속사" 항목과 같은 스크립트 실행 1회로 함께 처리. 한미프렉시블
  (id=4976, rcept 20260424000057)은 PARTIAL→OK 전환, net_income_cur/prv
  NULL→값(3,293,885,531 / 2,194,667,989) 확인. 상세 수치·검증 결과는 위
  "각주 참조의 한글 접속사" 항목의 소급 반영 기록 참고(중복 서술 방지).
  · **계정 라벨 셀에 EPS(주당손익) 병기 접미어**(2026-07-23, 주식회사 노바스 rcept
  20260407001297): 손익계산서 당기순이익 라벨이 "X. 당기순이익(손실)(주석16)(주당손익
  당기 (14,770)원  전기  (11,169)원)"처럼 각주 참조 **뒤에** EPS 값을 또 하나의
  괄호로 병기했다. 이 EPS 괄호는 안에 "당기"/"전기"/"원" 같은 한글과 자체 중첩 괄호
  (EPS 금액 "(14,770)")가 섞여 있어 `_FOOTNOTE_SUFFIX_RE`(숫자/콤마/공백+마커)와
  `_FORMULA_SUFFIX_RE`(로마숫자/숫자/공백/+)가 **모두** 매치 실패 → 정규화 라벨이
  "...당기순이익(손실)(주석16)(주당손익...)"로 남아 alias 키 "당기순이익(손실)"와
  불일치 → net_income이 당기·전기 모두 통째로 누락(PARTIAL)됐다. **괄호가 "주당"으로
  시작할 때에 한해서만** 벗기는 `_EPS_SUFFIX_RE`(`base.py`)를 신설하고,
  `normalize_account_label`에서 이 접미어는 각주 참조 "(주석16)"보다 **뒤에** 오므로
  `_FOOTNOTE_SUFFIX_RE`보다 **먼저** 벗겨야 그다음 각주 제거가 도달할 수 있어 순서를
  그렇게 배치했다. 안쪽 EPS 금액 괄호 한 겹의 중첩까지 흡수하되 문자열 끝($)에 붙은
  접미어만 대상으로 한다 — "(손실)"/"(매출액)" 등 의미 있는 항목명 괄호는 절대
  건드리지 않는다(과잉 제거 방지). FINANCE 서식·이익-primary 조합형이라 `_apply_sign`은
  원문 부호 그대로 신뢰: net_income_cur=-855,942,067 / _prv=-647,234,000(적자). 회귀
  fixtures 20260407001297. **로컬 캐시 4,922건 전수 스캔: 같은 EPS 병기 패턴으로
  net_income이 누락돼 있던 문서 9건 모두 이번 수정으로 복구**(8건 PARTIAL→OK, 1건
  20220414001761은 다른 결측 항목이 남아 PARTIAL 유지·net_income만 복구). 대상 rcept:
  20220414001761 / 20230403003125 / 20240408000864 / 20240408001105 / 20250331001113 /
  20250331001268 / 20260406002755 / 20260407001282 / 20260407001297. 이 9건에는
  콜론 "주당순손실:", 라벨 내 개행, "(-)5,767원" 중첩 괄호, "(손실)" 없는 순수
  "당기순이익" 라벨 등의 변형도 섞여 있었으나 모두 흡수 확인됨.
  **기존 완료 Job results 소급 재파싱은 미실행**(스캔·보고만,
  실행 여부는 사용자 별도 결정 — 이 프로젝트의 확립된 관행). `pytest` 291 passed.
  · **PARTIAL 원인 전수 조사 → 라벨/서식 변형 5종 + EPS 여분 괄호 1종 수정**
  (2026-07-23, dart-parser). `results`의 PARTIAL(rcept 보유) 113건을 전수 분류:
  **57건은 진성 결측**("(첨부)재무제표" TITLE 아래 목차·주석만 있고 실제 BS/IS 표가
  없는 의견거절·미첨부류 — `자산총계`/`매출액` 라벨 0), **cogs/gross_profit/sga
  3종 결측 다수는 진성**(골프장·호텔·케이블카·회계법인 등 `영업수익`/`영업비용`
  단일식 손익계산서라 매출원가·매출총이익 자체가 없음), **noncurrent_liab 단독
  결측은 진성**(BS에 `비유동부채` 라인 자체가 없는 무차입/유동부채만 회사),
  **금융업 무차입 BS**(대출채권 기반, 유동/비유동 미구분)도 진성. 나머지가 **파서
  버그**로 확인돼 아래를 최소 변경으로 수정(모두 `ACCOUNT_NAME_ALIASES` 실측 기반
  확장 + 회귀 fixtures, gross_profit==revenue-cogs·자산총계==부채+자본 자체검증):
  ① **net_income 요약행 "당기순손익"**(이익/손실 아우르는 표기, 335건/286개 문서 —
  진양에너지 20260317000433 "Ⅶ.당기순손익", 에스엠인더스트리 20260407002731
  "XI.당기순손익"). 완전 일치라 세전 "법인세비용차감전순손익"엔 오매칭 안 되고,
  이익/손실 부분문자열이 없어 `_apply_sign`이 원문 부호(괄호=음수) 그대로 신뢰.
  ② **연결 표기가 중간에 끼는 변형**(신신사 20260407001413 "당기연결순이익(손실)")
  — 실측된 4종만(당기연결순이익/당기연결순손실/당기연결순이익(손실)/연결당기순손익).
  ③ **bare "매출"**(매출액 아님, IFRS 첨부 손익계산서 — 엘엑스엠엠에이 20260326000129
  "I.매출", 465건/330개 문서). ④ **IFRS 연결 재무상태표의 값 없는 섹션 헤더 +
  "…합계"/"…계" 소계행**(씨이케이 20260330001497 "II.유동자산"(값없음)→"유동자산합계",
  상지해운 20260325000364 "유동자산계"): 값 없는 헤더가 필드를 None으로 잠가 소계값을
  못 채우던 것을 `_extract_attach_section`이 **빈 매칭(당기·전기 모두 None)으로는
  필드를 잠그지 않도록** 고치고 `유동자산합계`/`유동자산계`류 8종 alias 추가
  (**FINANCE 경로 `_extract_section`은 무변경** — 헤더에 값이 인라인이라 첫 매칭 우선이
  그대로 성립). ⑤ **EPS 병기 괄호 끝의 여분 닫는 괄호**(대능주택개발 20220406000855
  "…전기 60,420원))" — 불균형 오타)를 `_EPS_SUFFIX_RE` 끝 `\)+`로 흡수. **효과: 위
  PARTIAL 113건 중 12건이 OK로 전환 가능**(1건 윤솔회계법인은 net_income만 복구되고
  다른 진성 결측이 남아 PARTIAL 유지). 회귀 fixtures 6건 신설(20260317000433 /
  20260407001413 / 20260326000129 / 20260330001497 / 20260325000364 / 20220406000855).
  `pytest` 297 passed. **기존 완료 Job results 소급 재파싱은 미실행**(스캔·보고만 —
  `reparse_local_cache.py --dry-run`: 13행 변경 예정 전부 순수 값 추가(NULL→값),
  PARTIAL→OK 12건, value→NULL 회귀 0, `--verify` 부호 오분류 0. 실행 여부는 사용자
  별도 결정).
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
- **IFRS "(첨부)재무제표" 첨부문서 구조**(2026-07-22, 롯데미쓰이화학 rcept
  20250324000776 사용자 실측 지적 → 로컬 캐시 4,922건 전수 스캔). 이 서식은
  본문에 "재무상태표"/"손익계산서"/"현금흐름표" TITLE이 **아예 없고**,
  "(첨부)재 무 제 표"(또는 "(첨부)연 결 재 무 제 표") TITLE 하나 아래에 4개
  재무제표가 모두 들어간다. 세 가지가 겹쳐 기존 파서가 통째로 놓쳤다: ① 각
  재무제표 제목이 `<TITLE>`이 아니라 **독립 `<P>`(롯데미쓰이)** 또는 **THEAD
  없는 캡션 `<TABLE>`의 첫 셀(하이에어)** 로 나옴, ② 데이터 표가
  `ACLASS="FINANCE"`가 아니라 **`ACLASS="NORMAL"`**, ③ "과목|**주석**|당기|전기"
  처럼 값 사이에 "주석" 열이 끼고 현금흐름표는 THEAD가 없어 헤더가 첫 TBODY
  행에 있음. `xml_parser.py`가 `_ATTACH_TITLE_MARK` TITLE과 다음 TITLE 사이
  구간에서만(`in_attach` — 주석 본문의 "손익계산서" 언급 오탐 차단) `<P>`/캡션표
  제목을 감지하고, "(당)"/"(전)" 헤더로 열 계획을 세워 주석 열을 건너뛰는
  별도 경로(`_extract_attach_section`)로 처리한다. **기존 FINANCE 경로는 무변경.**
  회귀 테스트: fixtures 20250324000776(P-캡션·흑자) / 20240329000968(캡션표·
  THEAD 없는 CF) / 20230322000842(적자·순수손실 라벨) / 20230321000531(contra
  매출원가). 로컬 캐시 영향: 무FINANCE·무BS/IS-TITLE 391건 중 **228건이 신규로
  재무 데이터 복구(180건 OK)**, 나머지 163건은 진성 재무제표 미첨부(의견거절
  등)라 PARTIAL 유지가 정상.
  · **첨부 경로 전용 부호 처리 `_apply_sign_ifrs`**(2026-07-22, dart-qa 확정):
  FINANCE 서식과 IFRS 첨부 서식은 부호 규약이 **정반대**다. FINANCE는 손실을
  양수 크기로 적어 `_apply_sign`이 반전하지만, IFRS 첨부는 손실을 자연 부호
  (괄호=음수)로 그대로 적어 값에 이미 경제적 부호가 들어 있다. 첨부 경로가
  FINANCE용 `_apply_sign`을 재사용하던 것이 두 부호 버그의 근본 원인이었다 —
  ⓑ 순수손실 라벨("영업손실 (15,641,046,221)")이 흑자로 뒤집히고(적자→흑자),
  ⓐ 괄호 표기 매출원가/판관비가 음수로 저장. **`_extract_attach_section`이
  이제 `_apply_sign`이 아니라 `_apply_sign_ifrs`를 써서 손익은 원문 부호를 그대로
  신뢰하고, cogs/sga만 abs로 정규화(비용 크기)한다.** FINANCE 경로와
  `_apply_sign`(및 이를 잠그는 `test_parsers.py:118`)은 무변경. 전수 검증: 228건
  중 cogs 음수 0건·적자→흑자 뒤집힘 0건, gross_profit==revenue-cogs 성립 208건
  (나머지 ~20건은 매출원가가 없는 금융·수익형 업종이라 항등식 자체가 비적용).
  · **기존 완료 Job 결과에 대한 소급 반영 완료(2026-07-22, 사용자 명시 승인 일회성
  예외)**: `reparse_local_cache.py`(무변경 재사용, API 0건)로 results 테이블
  1,211건(rcept 보유+파싱완료, 캐시 결측 0) 재파싱 → **50행 값 변경, 전부 순수
  추가(NULL→값 채움 1,584필드, value→value 변경 0, value→NULL 회귀 0)**,
  **PARTIAL→OK 40건**(FAILED→ 전이 0 — 결과테이블의 FAILED 858건은 전부 rcept
  없는 "감사보고서 없음"이라 애초에 재파싱 대상이 아님), noncurrent NULL→값 47건.
  "228복구/180 OK"는 **문서캐시 4,922건 전수 기준**이고 results 테이블은 그 부분집합
  50행만 해당(2026-07-21과 동일한 캐시 대 결과테이블 구분). 검증: `--verify`로
  부호 오분류 0·DB드리프트 0(멱등 확인), 항등식 전수 대조 결과 **이번 재파싱이
  새로 만든 위반 0건**. (신규 OK 중 티케이지태광 20260330001165·씨이케이홀딩스
  20260331004697이 `자산총계==유동+비유동`을 위반하나, 둘 다 **"매각예정(비유동)
  자산"이라는 정당한 제3 자산 항목** 때문이며 유동/비유동/자산총계 각각 원문과
  정확 일치·`자산총계==부채+자본` 정확 성립 → 파서 정상, 오탐. gross_profit·
  total_liab 관련 잔여 위반 2~3건은 전부 이번 재파싱과 무관한 기존 이슈(대호
  20260430001104 total_liab=0, 선영축산 20230406001585 cogs=0 "-"오파싱 — 변경
  필드 0로 확인).)
- **현금흐름표 4항목(CF) + 세부계정 펼치기, 감사인명/사무소주소
  (`auditor_name`/`auditor_address`), 원문 섹션 열람 API**(재무상태표/
  손익계산서/현금흐름표/주석/감사의견 — 로컬 캐시만 읽어 쿼터 0건)가 모두
  구현돼 있다.
- **영업외수익/영업외비용 2항목**(2026-07-22, `non_operating_income`/
  `non_operating_expense`)이 CF 4항목과 완전히 동형인 best-effort 필드로
  추가됐다 — 파서(`app/parsers/base.py::NON_OPERATING_FINANCIAL_FIELDS`,
  `ACCOUNT_NAME_ALIASES`)와 세부계정 펼치기(`account_detail.py`)는
  dart-parser가, `results`/`financial_snapshots` 테이블 컬럼(`_cur`/`_prv`
  4개 + snapshot 2개, `app/core/db.py` ad-hoc `ALTER TABLE`로 기존 DB에도
  적용) · Phase 2 파이프라인 매핑(`app/core/pipeline.py`의 두 매핑 루프에
  `NON_OPERATING_FINANCIAL_FIELDS`를 추가하는 것만으로 충분— 필드명 순회
  구조라 CF 때처럼 별도 분기가 필요 없었다) · API 응답(`ResultResponse`/
  `FinancialSnapshotResponse`/`SORTABLE_COLUMNS`) · Excel/CSV 내보내기
  (`RESULT_COLUMN_LABELS`)는 dart-backend가 이어서 배선했다. CF와 동일하게
  `determine_parse_status()` 판정에는 관여하지 않고(결측이어도 PARTIAL/
  FAILED로 안 떨어짐), **기존 완료 Job은 소급 재파싱 없이 NULL로 남고
  신규 Phase 2 실행분부터만 채워진다.** `account-detail` 엔드포인트는
  `accounts` 응답이 애초에 `dict[str, list[...]]` 범용 구조라 코드 변경 없이
  새 키가 자동으로 실린다. pytest 298 passed(회귀 0).
  · **기존 완료 Job 결과에 대한 소급 반영 완료(2026-07-22, 사용자 명시 승인
  일회성 예외)**: CF·gross_profit·`_apply_sign` 부호수정 때와 동일하게
  `backend/scripts/reparse_local_cache.py`(이번에 `NON_OPERATING_FINANCIAL_FIELDS`
  를 대상 필드 목록에 추가하는 최소 변경만 함 — import/`_ALL_VALUE_FIELDS`/
  `new_values` 루프 3곳, `--dry-run`·멱등·**API 호출 0건** 구조 무변경)로
  results 1,211건(rcept 보유+파싱완료, 캐시 결측 0) 재파싱. **1,105행 변경,
  전부 순수 추가**: 4개 non_operating 컬럼 NULL→값 4,417필드 채움, value→NULL
  회귀 0·value→value 회귀 0(재파싱 전 non_operating 값이 non-NULL이던 행은
  0건이라 덮어쓸 값 자체가 없었음). **다른 숫자 값 컬럼은 백업 DB와 바이트
  단위로 전부 동일**(parse_status 전환 0, revenue/total_assets/noncurrent/
  auditor 변경 0), 유일한 부수 변경은 parse_note 9건이 현재 파서 기준
  missing 목록으로 텍스트만 갱신된 것(값 무관). `--verify` 항등식 자체검증:
  부호 오분류 0·DB드리프트 0(멱등), 크기 불일치 2건은 기존 "-"→cogs=0 버그
  (20230410002954/20230406001585)로 이번 재파싱과 무관. 채워진 결과: OK 행
  기준 non_operating_income_cur 0→1,032건. 실행 전 `dart_search.db.bak.
  pre_nonop_backfill.20260722222214`로 백업. `pytest` 298 passed(회귀 0).
  · **`financial_snapshots` 테이블도 별개 테이블이라 위 results 소급 반영에서
  빠져 있었다(2026-07-22, 사용자 명시 승인 일회성 예외, 뒤이어 처리)**: 결과
  화면의 "재무이력(최근 N년, 다년치)" 표는 `results`가 아니라 STEP7이 채우는
  `financial_snapshots`에서 값을 읽는데, 이 테이블의 기존 4,147행은 두 필드가
  생기기 전에 만들어져 전부 NULL이라 **합계 행이 "-"로 표시**됐다(세부계정은
  원문을 매번 새로 여는 `account_detail.py` 경로라 DB와 무관하게 정상이었음 —
  그래서 합계만 빈 게 드러났다). results 스크립트는 손대지 않고 **별도 스크립트
  `backend/scripts/reparse_financial_snapshots.py`**(PK·기간 판정 구조가 달라
  새로 작성, `rcept_no`로 로컬 캐시 재파싱, **API 0건**·`--dry-run`·멱등)로
  처리. 스냅샷 행이 원문의 당기/전기 어느 열에서 왔는지는 STEP7과 동일하게
  `_extract_fiscal_date`로 원문 당기 연도를 뽑아 `fiscal_year`와 비교해 판정
  (일치=values_cur, 한 해 앞=values_prv). **오직 `non_operating_income`/
  `non_operating_expense`가 NULL인 행만** 채우고 다른 컬럼은 무변경. 대상 4,147행
  (캐시 결측 0·파싱 예외 0·연도 불일치 0, 기간 판정은 4,147건 전부 결산기준일
  비교로 해결돼 from_current_period 폴백 미사용): **3,871행 채움**(income
  NULL→값 3,870·expense NULL→값 3,871), 나머지 276행은 원문에 영업외 항목이 없는
  업종(금융/수익형)이라 정상적으로 NULL 유지. 백업 DB와 전 컬럼 대조: **다른
  컬럼 드리프트 0행·value→변경 회귀 0**, 재실행 시 남은 277행 대상 0건 채움
  (멱등 확인). 실행 전 `dart_search.db.bak.pre_nonop_snapshots_backfill.
  20260722_225209`로 백업. `pytest` 298 passed.
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

### "최근 1년 이내 DART 공시 없음" 배제 (2026-07-21 추가)

실사례("주식회사 유진"류 — 폐업/휴면/합병소멸 등으로 실질적으로 활동을 멈춘
법인)를 걸러내기 위한 필터. `excluded_by_revenue`/`excluded_by_assets`와
완전히 같은 패턴이다 — Phase 2 **B2**(`_backfill_latest_rcept_no_for_job`,
`app/core/pipeline.py`)가 회사의 최신 rcept_no를 찾으려고 **이미 호출하는**
`list.json`(외부감사관련 F유형, 다년치 조회창) 응답을 그대로 재사용해 판정한다
— **추가 API 호출 0건**. rcept_no 앞 8자리(DART 접수번호 규격상 접수일자
YYYYMMDD)를 `results.latest_disclosure_date`에 남기고, 그 날짜가 365일보다
오래됐거나(또는 조회창 전체에서 공시가 0건이라 날짜 자체를 못 구했으면)
`results.excluded_by_stale_disclosure=1`로 표시한다. 다른 `excluded_by_*`와
마찬가지로 **행을 지우지 않는 순수 사후 필터 플래그**이고, `GET
/results`/`/export`가 동일한 tri-state(`true`/`false`/미지정=필터 없음)
쿼리 파라미터로 필터링을 지원한다. STEP7(다년치 이력 수집)만 이 플래그가
1인 회사를 건너뛰어 쿼터를 아낀다(B3까지는 이 판정과 무관하게 항상 최신
1건을 내려받아 파싱하므로 결과 행 자체는 그대로 남는다). **프론트엔드
미반영**: API/DB는 완료됐으나 결과 화면(`ResultPage.tsx`)에 새 필터 탭
추가와 기본 노출 여부 결정은 dart-frontend 몫으로 남아 있다.

**소급 반영 완료(2026-07-22)**: 이 기능 도입(2026-07-21) 이전에 이미
`phase=FINANCIALS`로 완료된 Job 4건(id 22/24/25/26, `rcept_no IS NOT
NULL AND parse_status IS NOT NULL`인 results 1,211행)은 컬럼 추가만으로는
값이 채워지지 않는 게 원래 원칙("컬럼 추가만, 소급 재파싱 없음")이지만,
사용자가 이번 건에 한해 명시적으로 소급 반영을 승인해 일회성 스크립트
`backend/scripts/backfill_stale_disclosure.py`(`--dry-run` 지원, 재실행
멱등)로 처리했다. **API 호출 0건** — 이미 저장된 `results.rcept_no`
앞 8자리(접수일자)로 `_disclosure_date_from_rcept_no`/`_is_disclosure_stale`
를 재계산했을 뿐, list.json을 다시 부르지 않았다(원 판정 로직 자체가
Phase 2 실행 당시 이미 확보한 rcept_no만으로 성립하는 순수 함수라 가능).
결과: 1,211행 중 204행(16.8%)이 `excluded_by_stale_disclosure`
0→1로 전환(실사례 "주식회사 유진", corp_code=00411525, id=6481,
latest_disclosure_date=20220406로 갱신 확인), 나머지 1,007행은 0→0
(날짜만 새로 채워짐). `rcept_no IS NULL`인 858행(감사보고서 원문을 아예
찾지 못한 행)은 이번 승인 범위 밖이라 건드리지 않았다 — 필요하면 별도
판단 후 처리.

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
