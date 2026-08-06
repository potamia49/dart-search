// 백엔드 API 응답/요청 타입 정의.
// 참고: backend/app/api/jobs.py, results.py, meta.py (상세개발계획.md §5, §6)

export type JobStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'PAUSED_QUOTA'
  | 'DONE'
  | 'FAILED'
  | 'CANCELLED'

export type ParseStatus = 'OK' | 'PARTIAL' | 'FAILED'

/** M6 재설계(§4-7-1) — Job이 Phase 1(후보 확정)까지만 끝난 상태인지,
 * Phase 2(재무정보 수집)까지 트리거된 상태인지. */
export type JobPhase = 'CANDIDATES' | 'FINANCIALS'

export interface RegionCondition {
  /** 시도 다중 선택. */
  sido: string[]
  /** 시도별 시군구(업종 대분류→중분류와 동일 구조). 빈 배열이거나 키가 없으면 그 시도 전체. */
  sigungu_by_sido: Record<string, string[]>
}

export interface RevenueCondition {
  min_krw: number | null
  max_krw: number | null
}

/** cond_total_assets — RevenueCondition과 동일 스키마 (§4-7-2, 2026-07-15 추가). */
export type TotalAssetsCondition = RevenueCondition

export interface PeriodCondition {
  bgn_de: string
  end_de: string
}

/** STEP 7(최근 N년 재무이력)이 허용하는 조회 기간 — 감사보고서가 당기·전기
 * 비교식이라 짝수 연수만 가능하다 (backend/app/api/jobs.py JobCreateRequest 참고). */
export type HistoryYears = 2 | 4 | 6 | 10

export interface JobCreateRequest {
  name: string | null
  region: RegionCondition
  /** §4-13-A(2026-08-05)로 **화면에서 입력을 받지 않는다** — SearchPage는 이 두 키를
   * 아예 보내지 않고, 백엔드 `JobCreateRequest`의 `default_factory`가 무제한 조건
   * (min/max 모두 null)을 채운다. 필드 자체는 기존 Job 조회(`JobResponse.cond_*`)와
   * 하위호환을 위해 남겨 두되 optional이다. */
  revenue?: RevenueCondition
  total_assets?: TotalAssetsCondition
  industry: string[]
  // M6 재설계 이후 Phase 1(A2~A4)은 이 값을 쓰지 않는다 — 백엔드
  // JobCreateRequest.period도 optional로 정정됐으므로 아예 보내지 않는다
  // (SearchPage 화면에서도 이 입력을 노출하지 않는다 — §4-7-1).
  period?: PeriodCondition
  history_years: HistoryYears
}

export interface JobResponse {
  id: number
  created_at: string | null
  name: string | null
  cond_region: RegionCondition | null
  cond_revenue: RevenueCondition | null
  cond_total_assets: TotalAssetsCondition | null
  cond_industry: string[] | null
  cond_period: PeriodCondition | null
  history_years: number | null
  status: JobStatus | null
  phase: JobPhase | null
  current_step: number | null
  progress_done: number | null
  progress_total: number | null
  error_msg: string | null
}

/** POST /api/jobs/{id}/start-financials 요청 바디 (§4-7-1). */
export interface StartFinancialsRequest {
  history_years: HistoryYears
}

export interface ResultResponse {
  id: number
  job_id: number | null
  corp_code: string | null
  rcept_no: string | null

  corp_name: string | null
  address: string | null
  phone: string | null
  ceo_name: string | null
  induty_code: string | null
  induty_name: string | null
  fiscal_date: string | null
  audit_opinion: string | null
  /** 감사인(회계법인/감사반) 이름과 사무소 주소 — 원문 감사보고서에서 추출한다.
   * 서명란이 없는 원문은 이름만 채워지고 주소는 null이다. */
  auditor_name: string | null
  auditor_address: string | null
  /** 연도별 감사인 변동 여부 (2026-07-26) — 1: 재무 이력에 서로 다른 감사인이 2곳
   * 이상, 0: 이력의 감사인이 모두 동일, null: 판정 불가(감사인을 확보한 연도가
   * 1개 이하 — STEP 7 미수행이거나 이 기능 도입 이전에 수집된 기존 Job). */
  auditor_changed: number | null

  current_assets_cur: number | null
  current_assets_prv: number | null
  noncurrent_assets_cur: number | null
  noncurrent_assets_prv: number | null
  total_assets_cur: number | null
  total_assets_prv: number | null
  current_liab_cur: number | null
  current_liab_prv: number | null
  noncurrent_liab_cur: number | null
  noncurrent_liab_prv: number | null
  total_liab_cur: number | null
  total_liab_prv: number | null
  total_equity_cur: number | null
  total_equity_prv: number | null
  revenue_cur: number | null
  revenue_prv: number | null
  cogs_cur: number | null
  cogs_prv: number | null
  gross_profit_cur: number | null
  gross_profit_prv: number | null
  sga_cur: number | null
  sga_prv: number | null
  operating_income_cur: number | null
  operating_income_prv: number | null
  // 영업외수익/영업외비용 (2026-07-22) — 표준 13항목엔 없는 best-effort 추가
  // 항목이라 파싱상태가 OK여도 값이 없을 수 있다(CF 4항목과 동형).
  non_operating_income_cur: number | null
  non_operating_income_prv: number | null
  non_operating_expense_cur: number | null
  non_operating_expense_prv: number | null
  net_income_cur: number | null
  net_income_prv: number | null
  // 현금흐름표 4항목 (§4-8)
  cf_operating_cur: number | null
  cf_operating_prv: number | null
  cf_investing_cur: number | null
  cf_investing_prv: number | null
  cf_financing_cur: number | null
  cf_financing_prv: number | null
  cf_ending_cash_cur: number | null
  cf_ending_cash_prv: number | null

  // 금융위 요약재무 참고값 + 기준연도 (§4-10-C/D). 후보 검토용 표시 전용이고
  // 필터 판정에는 쓰이지 않는다 — 판정은 항상 원문 파싱값(_cur) 기준이다.
  ref_revenue: number | null
  ref_total_assets: number | null
  ref_fin_year: string | null

  parse_status: ParseStatus | null
  parse_note: string | null
  excluded_by_revenue: number
  excluded_by_assets: number
  excluded_manually: number

  /** 최신 DART 공시 접수일자(YYYYMMDD) — 폐업/휴면 추정 판정의 근거값. */
  latest_disclosure_date: string | null
  /** 1이면 최근 1년 이내 DART 공시가 없어(외부감사관련 공시 기준) 폐업/휴면으로
   * 추정되는 건. 결과 화면 기본 탭에서는 이 값이 1인 건을 기본 숨김 처리하고,
   * "휴면·폐업 추정" 전용 탭에서만 노출한다(2026-07-22). */
  excluded_by_stale_disclosure: number
}

export interface ResultListResponse {
  total: number
  page: number
  page_size: number
  items: ResultResponse[]
  /** `?ids_only=true`로 요청했을 때만 채워지는 경량 응답 — 현재 필터를 통과한
   * `results.id` 전체다(정렬 순서 그대로, 페이징 없음. 이때 `items`는 빈 배열).
   *
   * **그 외에는 항상 `null`/부재이며 빈 배열(`[]`)과 반드시 구분해야 한다** —
   * `ids_only`를 모르는 구버전 백엔드는 그 파라미터를 조용히 무시하고 200 +
   * 1페이지만 돌려주므로, 이 필드가 없으면 "서버가 필터 전체 선택을 지원하지
   * 않음"으로 판정한다(§4-11 `_selected` 파일명 접미어와 같은 양방향 계약). */
  ids?: number[] | null
}

export interface RegionMeta {
  sido: string
  sigungu: string[]
}

export interface IndustryMeta {
  code: string
  name: string
  children?: IndustryMeta[]
}

export interface QuotaResponse {
  date: string
  call_count: number
  limit: number
  remaining: number
}

/** GET /api/meta/dart-index/status (M8 1단계) — Phase 1이 후보를 찾는 정본
 * 인덱스(`dart_corp_index`). **이게 비어 있으면 후보 확정이 즉시 실패한다.**
 * fsc_corp_index를 대체했으므로 화면에서 먼저 보여야 하는 쪽이다. */
export interface DartIndexStatus {
  row_count: number
  last_completed_at: string | null
  crawl_in_progress: boolean
  checkpoint_industry: string | null
  /** 마지막으로 동명 회사 교정을 전부 마친 시각. 크롤은 이 교정을 자동으로
   * 이어서 실행하지만, 쿼터 소진 등으로 중단되면 아래 플래그가 남는다. */
  last_reconciled_at: string | null
  /** 크롤 이후 교정이 밀려 있음 — 동명 회사의 주소·업종이 교차돼 있을 수 있다. */
  reconcile_pending: boolean
}

/** GET /api/meta/fsc-financial/status (M8 2단계) — 매출액/총자산 **참고값**
 * 스냅샷(`fsc_financial_stat`). 비어 있어도 후보 확정·최종 결과에는 영향이
 * 없다(§4-10-C: 이 값으로 후보를 제외하지 않는다) — 후보 목록의 참고 표시와
 * Phase 2 처리 순서(밴드 근접도 정렬)에만 쓰인다. */
export interface FscFinancialStatus {
  row_count: number
  last_completed_at: string | null
  years: string[]
  crawl_in_progress: boolean
}

/** POST /api/meta/candidates-preview — 지역/업종 조건만으로 Phase 1 A2(로컬 DB
 * 필터, API 호출 없음)를 미리 실행한 후보 수.
 *
 * **기준이 바뀌었다(M8 5단계)**: A3(data.go.kr 건별 스크리닝)가 폐기돼 병목이
 * **DART 일일 한도**로 옮겨갔다. `daily_quota_assumed`는 하루에 재무정보를
 * 수집할 수 있는 후보 수(후보 1개사당 약 5회 호출)이고, `exceeds_daily_quota`가
 * true면 **재무정보 수집(2단계)** 이 여러 날에 걸쳐 나뉜다는 뜻이다 —
 * 후보 확정(1단계) 자체는 외부 호출이 0건이라 항상 즉시 끝난다. 한도에 걸리면
 * Job이 PAUSED_QUOTA로 멈췄다가 다음 날 이어서 진행되며 결과 정확도에는
 * 영향이 없다. */
export interface CandidatesPreviewRequest {
  region: RegionCondition
  industry: string[]
}

export interface CandidatesPreviewResponse {
  candidate_count: number
  daily_quota_assumed: number
  exceeds_daily_quota: boolean
  estimated_days: number
}

export interface KeyCheckResult {
  valid: boolean
  message: string
}

export interface ValidateKeyResponse {
  dart: KeyCheckResult | null
  data_go_kr: KeyCheckResult | null
}

export type ExportFormat = 'xlsx' | 'csv'

/** 결과 목록 정렬 방향 — 컬럼 헤더를 누를 때마다 오름차순 → 내림차순 → 해제 순으로 순환한다. */
export type SortDir = 'asc' | 'desc'

/** STEP 7(최근 N년 재무이력) — 회사×회계연도 단위 스냅샷 1건.
 * GET /api/jobs/{id}/results/{result_id}/history (backend/app/api/results.py). */
export interface FinancialSnapshotResponse {
  id: number
  result_id: number | null
  rcept_no: string | null
  fiscal_year: string

  current_assets: number | null
  noncurrent_assets: number | null
  total_assets: number | null
  current_liab: number | null
  noncurrent_liab: number | null
  total_liab: number | null
  total_equity: number | null
  revenue: number | null
  cogs: number | null
  gross_profit: number | null
  sga: number | null
  operating_income: number | null
  // 영업외수익/영업외비용 (2026-07-22) — best-effort, CF 4항목과 동형.
  non_operating_income: number | null
  non_operating_expense: number | null
  net_income: number | null
  // 현금흐름표 4항목 (§4-8)
  cf_operating: number | null
  cf_investing: number | null
  cf_financing: number | null
  cf_ending_cash: number | null

  /** 그 연도를 **당기**로 감사한 감사인 이름 (2026-07-26). 전기 열 유래 행
   * (`from_current_period=0`)과 이 기능 도입 이전에 수집된 기존 이력은 null이다 —
   * 화면은 null일 때 account-detail의 `auditor_name`(원문을 그 자리에서 다시 읽어
   * 채우므로 기존 Job에서도 값이 나온다)으로 보완한다. */
  auditor_name: string | null

  /** 이 연도의 감사인이 **직전에 이름이 확보된 연도**와 다른가 (2026-07-26).
   * true=변경 / false=동일 / null=판정 불가(이 연도 이름이 없거나 비교할 이전
   * 연도가 없음). 목록 컬럼 `auditor_changed`와 **동일한 정규화**로 서버가
   * 계산해 내려주는 값이다 — 화면에서 이름을 다시 비교하면 두 화면의 답이
   * 갈리므로(dart-qa 실측 3건: 대표이사 교체를 감사인 교체로 오판) 이 값만 읽는다. */
  auditor_changed_from_prev: boolean | null

  parse_status: ParseStatus | null
  parse_note: string | null
  /** 1이면 이 연도를 당기로 하는 감사보고서에서 나온 값(원문 보기 = 당기가 이 연도),
   *  0이면 다음 연도 공시의 전기 열에서 채워진 값이다(2026-07-20). */
  from_current_period: number
}

/** §4-8 원문 섹션 열람 — GET .../document-sections/{section}. */
export type DocumentSection = 'audit' | 'bs' | 'is' | 'cf' | 'notes'

export interface DocumentSectionResponse {
  section: string
  rcept_no: string
  available: boolean
  html: string
  notice: string | null
}

/** §4-12 선택 항목 보고서 생성 — POST /jobs/{id}/generate-report.
 *
 * "선택 항목 다운로드"와 달리 **파일을 브라우저로 내려주지 않는다** — 서버(=이 프로그램을
 * 실행 중인 바로 그 PC)의 로컬 폴더에 회사별 HTML + 발송처 라벨 엑셀을 저장하고, 그
 * 폴더 경로만 응답으로 알려준다. 화면은 그 경로를 사용자가 복사해 탐색기로 열 수 있게
 * 보여주는 것이 전부다. */
export interface GeneratedReportFile {
  result_id: number | null
  corp_name: string | null
  filename: string
}

/** **파일은 만들어졌지만** 일부 연도가 결측/PARTIAL이라 내용이 부실한 경우의 안내 —
 * 실패가 아니며 `generated_count`에 포함돼 있다. `result_id`가 `null`이면 특정 회사가
 * 아니라 **요청 전체에 대한 안내**(예: 사무소 연락처 미설정)다 — 회사별 경고와 섞어
 * 세지 말 것(한 회사가 여러 경고를 낼 수도 있다). */
export interface ReportWarning {
  result_id: number | null
  corp_name: string | null
  message: string
}

/** 재무 이력이 아예 없거나 전부 FAILED라 **파일 자체를 만들지 않은** 회사.
 * `warnings`와 달리 `generated_count`에 포함되지 않는다. */
export interface SkippedReport {
  result_id: number
  corp_name: string
  reason: string
}

export interface GenerateReportResponse {
  /** 생성된 폴더의 절대 경로(예: `C:\claude\dart-search\backend\report\2026-08-03`).
   * 같은 날 다시 생성하면 덮어쓰지 않고 `_2`, `_3` 폴더가 새로 생긴다. */
  output_dir: string
  generated_count: number
  files: GeneratedReportFile[]
  /** 같은 폴더에 함께 만들어진 우편 발송용 라벨 엑셀 파일명. */
  label_file: string
  warnings: ReportWarning[]
  /** 생성 대상에서 제외된 회사 목록(재무 이력 없음 등). 선택 전원이 여기 담기면
   * `generated_count`가 0이 될 수 있다. */
  skipped: SkippedReport[]
}

/** §4-12-A 선택 요약 — POST /jobs/{id}/results/selection-summary.
 *
 * "선택 항목 보고서 생성"의 **사전 확인 모달**이 쓰는 읽기 전용 집계다. 클릭 한 번으로
 * 수천 건이 선택될 수 있고(§4-11-A "현재 필터 전체 선택") 선택은 탭을 넘나들며 합집합으로
 * 누적되므로, 조건에 맞지 않는 회사가 우편 발송 대상에 섞였는지 생성 **전에** 알린다.
 *
 * 필드는 전부 **건수**다. 한 회사가 여러 조건에 동시에 걸릴 수 있어(매출액·총자산 필터는
 * 서로 독립 판정) **합이 `total`을 넘을 수 있다 — 화면에서 더해 "총 위험 N건"으로
 * 표시하지 말 것.**
 *
 * `failed`와 `no_disclosure`는 결과 화면의 탭 구분과 정확히 같은 조건이며 서로 배타적이다
 * (2026-08-03 백엔드 세분화) — `failed`는 "파싱 실패 (검수 필요)"(`parse_status='FAILED'`
 * **이면서** `rcept_no`가 있는 건)이고, `no_disclosure`는 "감사보고서 없음"(`rcept_no` 부재)
 * 이라 애초에 열어볼 원문이 없어 **검수 대상이 아니다**.
 *
 * `no_history`는 또 다른 축이다 — 재무 이력(financial_snapshots)이 0건이라 생성 시
 * **건너뛰어질** 회사 수다. 확인 모달의 "N건 생성" 문구는 `total`이 아니라
 * `total - no_history`(최대 생성 가능 건수)를 기준으로 쓴다.
 *
 * 입력·에러 계약은 `POST /generate-report`와 완전히 동일하다 — 같은 `ids`로 이 요약이
 * 200을 받았다면 생성도 같은 이유로 거부되지 않는다. */
export interface SelectionSummaryResponse {
  total: number
  stale_disclosure: number
  excluded_revenue: number
  excluded_assets: number
  /** `parse_status='FAILED'` **이면서** 원문(rcept_no)이 있는 건 = 진짜 검수 필요 */
  failed: number
  /** `rcept_no` 부재 = 감사보고서 자체가 없는 건(검수 대상 아님) */
  no_disclosure: number
  /** 재무 이력이 0건이라 보고서 생성이 건너뛰어질 건 */
  no_history: number
}

/** §4-14 값 목록 필터를 지원하는 컬럼 — 백엔드 `DISTINCT_VALUE_FIELDS`와 1:1이다.
 * 회사명/주소는 고유값이 행 수와 사실상 같아 **일부러 빠져 있다**(그쪽은 텍스트
 * 부분일치 필터가 담당한다). */
export type DistinctValueField = 'induty_name' | 'auditor_name' | 'audit_opinion'

/** 값 목록 한 항목 — 값과 그 값을 가진 결과 행 수. */
export interface DistinctValueItem {
  value: string
  count: number
}

/** `GET /api/jobs/{id}/results/distinct-values` 응답(§4-14, 2026-08-05).
 *
 * 엑셀 컬럼 필터의 "값 체크박스 목록"에 해당한다. **집계 범위는 그 Job의 결과
 * 전체이고 화면에 걸린 다른 필터를 반영하지 않는다** — 반영하면 지금 화면에서
 * 빠져 있는 값이 목록에서도 사라져 "안 보이는 값을 다시 켜기"가 불가능해진다. */
export interface DistinctValuesResponse {
  field: DistinctValueField
  /** 개수 내림차순 → 값 오름차순. "값 없음"은 여기 들어가지 않는다(아래 blank_*). */
  values: DistinctValueItem[]
  /** `limit` 적용 전 실제 고유값 개수. */
  total_distinct: number
  /** true면 목록이 잘렸다 — 화면은 "검색으로 좁히세요"를 안내해야 한다(잘린 줄
   * 모르고 "전체 선택"하면 안 보이는 값이 조용히 빠진다). */
  truncated: boolean
  /** 그 컬럼이 NULL이거나 빈 문자열인 행 수. 0이면 "(값 없음)" 항목을 그리지 않는다. */
  blank_count: number
  /** "(값 없음)"을 고를 때 필터 파라미터에 실을 예약 토큰(`__BLANK__`).
   * 백엔드가 토큰을 바꿔도 화면이 따라오도록 응답에 실려 온다 — 하드코딩하지 말 것. */
  blank_token: string
  /** 그 Job의 전체 결과 행 수(각 count·blank_count의 분모). */
  total_rows: number
}

/** 계정 상세 1행 — 원문 라벨(각주 포함)/상대 계층 레벨/당기·전기 값. */
export interface AccountRow {
  label: string
  level: number
  cur: number | null
  prv: number | null
}

/** 요약 표에서 대분류(유동자산 등)를 펼칠 때 쓰는 세부계정 상세.
 * `accounts`의 키는 요약 항목의 snapKey(current_assets 등)와 동일하다.
 * `fiscal_year_cur`는 그 원문의 당기 결산연도 — 재무이력 표가 연도별로
 * 당기/전기 중 어느 값을 써야 하는지 판정하는 데 쓴다. */
export interface AccountDetailResponse {
  rcept_no: string
  fiscal_year_cur: string | null
  accounts: Record<string, AccountRow[]>
  notice: string | null
  /** 이 원문의 감사의견(적정/한정/부적정/의견거절, 판정 불가 시 null). */
  audit_opinion: string | null
  /** 이 원문의 감사인 이름/사무소 주소 (2026-07-26). `results.auditor_name`(가장
   * 최근 1건 기준)과 달리 조회한 그 연도 원문에서 매번 새로 뽑으므로 기존 Job에도
   * 값이 있다(로컬 캐시만 읽어 쿼터 0건). 서명란이 없는 원문은 주소가 null. */
  auditor_name: string | null
  auditor_address: string | null
}
