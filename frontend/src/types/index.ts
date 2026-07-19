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
  sido: string | null
  sigungu: string[]
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
  revenue: RevenueCondition
  total_assets: TotalAssetsCondition
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
  gross_margin_cur: number | null
  gross_margin_prv: number | null
  sga_cur: number | null
  sga_prv: number | null
  operating_income_cur: number | null
  operating_income_prv: number | null
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

  parse_status: ParseStatus | null
  parse_note: string | null
  excluded_by_revenue: number
  excluded_by_assets: number
  excluded_manually: number
}

export interface ResultListResponse {
  total: number
  page: number
  page_size: number
  items: ResultResponse[]
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

/** GET /api/meta/fsc-index/status (2026-07-15 추가) — Phase 1이 쓰는
 * fsc_corp_index 전역 인덱스의 마지막 완료 갱신 시각/TTL 초과 여부.
 * 백엔드는 TTL이 지나도 자동 갱신하지 않고 로그에만 남기므로, 화면에서
 * 바로 확인할 수 있게 노출한다. */
export interface FscIndexStatus {
  row_count: number
  last_completed_at: string | null
  ttl_days: number
  is_stale: boolean
  crawl_in_progress: boolean
}

/** POST /api/meta/candidates-preview (2026-07-17 추가) — 지역/업종 조건만으로
 * Phase 1 A2(로컬 DB 필터, API 호출 없음)를 미리 실행한 후보 수. `exceeds_daily_quota`가
 * true면 A3(매출액/총자산 스크리닝)가 data.go.kr 일일 쿼터를 넘겨 하루 안에 끝나지
 * 않을 수 있다는 뜻이다(결과 정확도에는 영향 없음 — Phase 2가 항상 DART 원문으로
 * 최종 재검증). */
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
  gross_margin: number | null
  sga: number | null
  operating_income: number | null
  net_income: number | null
  // 현금흐름표 4항목 (§4-8)
  cf_operating: number | null
  cf_investing: number | null
  cf_financing: number | null
  cf_ending_cash: number | null

  parse_status: ParseStatus | null
  parse_note: string | null
}

/** §4-8 원문 섹션 열람 — GET .../document-sections/{section}. */
export type DocumentSection = 'bs' | 'is' | 'cf' | 'notes'

export interface DocumentSectionResponse {
  section: string
  rcept_no: string
  available: boolean
  html: string
  notice: string | null
}
