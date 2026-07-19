import type { FinancialSnapshotResponse, ResultResponse } from '../types'

export interface ResultColumn {
  key: keyof ResultResponse
  label: string
  format?: (value: ResultResponse[keyof ResultResponse]) => string
}

/** 재무제표 구분 — 재무상태표(bs) / 손익계산서(is) / 현금흐름표(cf). */
export type StatementSection = 'bs' | 'is' | 'cf'

/** 재무 항목 1건 — results의 당기(_cur)/전기(_prv) 키와 financial_snapshots의
 * 접미어 없는 키를 함께 담아, 당기·전기 표와 재무이력 표가 같은 그룹 정의를 공유한다. */
export interface FinancialItem {
  label: string
  curKey: keyof ResultResponse
  prvKey: keyof ResultResponse
  snapKey: keyof FinancialSnapshotResponse
  format: (value: unknown) => string
}

/** 재무제표 구분별 항목 묶음. */
export interface FinancialGroup {
  section: StatementSection
  title: string
  items: FinancialItem[]
}

export function formatNumber(value: unknown): string {
  if (value === null || value === undefined) return '-'
  return Number(value).toLocaleString()
}

export function formatPercent(value: unknown): string {
  if (value === null || value === undefined) return '-'
  return `${Number(value).toFixed(2)}%`
}

// phone/ceo_name/induty_name은 Phase 2가 덮어쓰지 않아 Phase 1(FSC) 추정치가
// 그대로 남는다 — 실제 연락처로 쓰일 수 있어 라벨로 미확정임을 명시한다.
export const BASIC_COLUMNS: ResultColumn[] = [
  { key: 'corp_name', label: '회사명' },
  { key: 'address', label: '주소' },
  { key: 'phone', label: '전화번호 (미확정·FSC 기준)' },
  { key: 'ceo_name', label: '대표자 (미확정·FSC 기준)' },
  { key: 'induty_name', label: '업종 (미확정·FSC 기준)' },
  { key: 'induty_code', label: '업종코드' },
  { key: 'fiscal_date', label: '결산기준일' },
  { key: 'audit_opinion', label: '감사의견' },
]

// 재무 항목의 단일 소스 — 재무상태표/손익계산서/현금흐름표로 구분한다.
// 당기·전기 표(ResultResponse)와 재무이력 표(FinancialSnapshotResponse)가
// 이 그룹 정의를 공유하고, 아래 FINANCIAL_COLUMNS/CASH_FLOW_COLUMNS(컬럼 토글용)도
// 여기서 파생한다.
export const FINANCIAL_GROUPS: FinancialGroup[] = [
  {
    section: 'bs',
    title: '재무상태표',
    items: [
      { label: '유동자산', curKey: 'current_assets_cur', prvKey: 'current_assets_prv', snapKey: 'current_assets', format: formatNumber },
      { label: '비유동자산', curKey: 'noncurrent_assets_cur', prvKey: 'noncurrent_assets_prv', snapKey: 'noncurrent_assets', format: formatNumber },
      { label: '자산총계', curKey: 'total_assets_cur', prvKey: 'total_assets_prv', snapKey: 'total_assets', format: formatNumber },
      { label: '유동부채', curKey: 'current_liab_cur', prvKey: 'current_liab_prv', snapKey: 'current_liab', format: formatNumber },
      { label: '비유동부채', curKey: 'noncurrent_liab_cur', prvKey: 'noncurrent_liab_prv', snapKey: 'noncurrent_liab', format: formatNumber },
      { label: '부채총계', curKey: 'total_liab_cur', prvKey: 'total_liab_prv', snapKey: 'total_liab', format: formatNumber },
      { label: '자본총계', curKey: 'total_equity_cur', prvKey: 'total_equity_prv', snapKey: 'total_equity', format: formatNumber },
    ],
  },
  {
    section: 'is',
    title: '손익계산서',
    items: [
      { label: '매출액', curKey: 'revenue_cur', prvKey: 'revenue_prv', snapKey: 'revenue', format: formatNumber },
      { label: '매출원가', curKey: 'cogs_cur', prvKey: 'cogs_prv', snapKey: 'cogs', format: formatNumber },
      { label: '매출총이익율', curKey: 'gross_margin_cur', prvKey: 'gross_margin_prv', snapKey: 'gross_margin', format: formatPercent },
      { label: '판매비와관리비', curKey: 'sga_cur', prvKey: 'sga_prv', snapKey: 'sga', format: formatNumber },
      { label: '영업이익', curKey: 'operating_income_cur', prvKey: 'operating_income_prv', snapKey: 'operating_income', format: formatNumber },
      { label: '당기순이익', curKey: 'net_income_cur', prvKey: 'net_income_prv', snapKey: 'net_income', format: formatNumber },
    ],
  },
  {
    // 현금흐름표 4항목 (§4-8) — best-effort 항목이라 파싱상태가 OK여도 값이 없을 수 있다.
    section: 'cf',
    title: '현금흐름표',
    items: [
      { label: '영업활동현금흐름', curKey: 'cf_operating_cur', prvKey: 'cf_operating_prv', snapKey: 'cf_operating', format: formatNumber },
      { label: '투자활동현금흐름', curKey: 'cf_investing_cur', prvKey: 'cf_investing_prv', snapKey: 'cf_investing', format: formatNumber },
      { label: '재무활동현금흐름', curKey: 'cf_financing_cur', prvKey: 'cf_financing_prv', snapKey: 'cf_financing', format: formatNumber },
      { label: '기말의현금', curKey: 'cf_ending_cash_cur', prvKey: 'cf_ending_cash_prv', snapKey: 'cf_ending_cash', format: formatNumber },
    ],
  },
]

function itemsToColumns(items: FinancialItem[]): ResultColumn[] {
  return items.flatMap((item) => [
    { key: item.curKey, label: `${item.label}_당기`, format: item.format },
    { key: item.prvKey, label: `${item.label}_전기`, format: item.format },
  ])
}

// 재무상태표 + 손익계산서 13항목 (당기/전기 = 26컬럼).
export const FINANCIAL_COLUMNS: ResultColumn[] = FINANCIAL_GROUPS
  .filter((group) => group.section !== 'cf')
  .flatMap((group) => itemsToColumns(group.items))

// 현금흐름표 4항목 (§4-8) — 기본 숨김, 컬럼 토글로 노출.
export const CASH_FLOW_COLUMNS: ResultColumn[] = itemsToColumns(
  FINANCIAL_GROUPS.find((group) => group.section === 'cf')?.items ?? [],
)

export const STATUS_COLUMNS: ResultColumn[] = [
  { key: 'parse_status', label: '파싱상태' },
]

export const ALL_COLUMNS: ResultColumn[] = [
  ...BASIC_COLUMNS,
  ...FINANCIAL_COLUMNS,
  ...CASH_FLOW_COLUMNS,
  ...STATUS_COLUMNS,
]

/** 기본 표시 컬럼 (요구사항: 회사명/주소/업종/매출액_당기/영업이익_당기/당기순이익_당기/parse_status). */
export const DEFAULT_VISIBLE_KEYS: (keyof ResultResponse)[] = [
  'corp_name',
  'address',
  'induty_name',
  'revenue_cur',
  'operating_income_cur',
  'net_income_cur',
  'parse_status',
]

export function formatCell(column: ResultColumn, row: ResultResponse): string {
  const value = row[column.key]
  if (column.format) return column.format(value)
  if (value === null || value === undefined || value === '') return '-'
  return String(value)
}
