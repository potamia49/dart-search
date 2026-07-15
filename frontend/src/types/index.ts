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

export interface RegionCondition {
  sido: string | null
  sigungu: string[]
}

export interface RevenueCondition {
  min_krw: number | null
  max_krw: number | null
}

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
  industry: string[]
  period: PeriodCondition
  history_years: HistoryYears
}

export interface JobResponse {
  id: number
  created_at: string | null
  name: string | null
  cond_region: RegionCondition | null
  cond_revenue: RevenueCondition | null
  cond_industry: string[] | null
  cond_period: PeriodCondition | null
  history_years: number | null
  status: JobStatus | null
  current_step: number | null
  progress_done: number | null
  progress_total: number | null
  error_msg: string | null
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

  parse_status: ParseStatus | null
  parse_note: string | null
  excluded_by_revenue: number
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

  parse_status: ParseStatus | null
  parse_note: string | null
}
