import type { ResultResponse } from '../types'

export interface ResultColumn {
  key: keyof ResultResponse
  label: string
  format?: (value: ResultResponse[keyof ResultResponse]) => string
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

// [당기 컬럼, 전기 컬럼, 표시 라벨] — 재무 13항목 x 당기/전기 = 26개.
const FINANCIAL_LABELS: [keyof ResultResponse, keyof ResultResponse, string][] = [
  ['current_assets_cur', 'current_assets_prv', '유동자산'],
  ['noncurrent_assets_cur', 'noncurrent_assets_prv', '비유동자산'],
  ['total_assets_cur', 'total_assets_prv', '자산총계'],
  ['current_liab_cur', 'current_liab_prv', '유동부채'],
  ['noncurrent_liab_cur', 'noncurrent_liab_prv', '비유동부채'],
  ['total_liab_cur', 'total_liab_prv', '부채총계'],
  ['total_equity_cur', 'total_equity_prv', '자본총계'],
  ['revenue_cur', 'revenue_prv', '매출액'],
  ['cogs_cur', 'cogs_prv', '매출원가'],
  ['gross_margin_cur', 'gross_margin_prv', '매출총이익율'],
  ['sga_cur', 'sga_prv', '판매비와관리비'],
  ['operating_income_cur', 'operating_income_prv', '영업이익'],
  ['net_income_cur', 'net_income_prv', '당기순이익'],
]

export const FINANCIAL_COLUMNS: ResultColumn[] = FINANCIAL_LABELS.flatMap(
  ([curKey, prvKey, label]) => {
    const format = curKey === 'gross_margin_cur' ? formatPercent : formatNumber
    return [
      { key: curKey, label: `${label}_당기`, format },
      { key: prvKey, label: `${label}_전기`, format },
    ]
  },
)

export const STATUS_COLUMNS: ResultColumn[] = [
  { key: 'parse_status', label: '파싱상태' },
]

export const ALL_COLUMNS: ResultColumn[] = [
  ...BASIC_COLUMNS,
  ...FINANCIAL_COLUMNS,
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
