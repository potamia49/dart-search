import type { FinancialSnapshotResponse, ResultResponse } from '../types'

export interface ResultColumn {
  key: keyof ResultResponse
  label: string
  format?: (value: ResultResponse[keyof ResultResponse]) => string
  /** 값 하나만으로 표기를 정할 수 없는 컬럼용(예: 파싱상태는 rcept_no 유무에 따라
   * "실패"와 "공시 없음"을 구분해야 한다). 지정되면 format보다 우선한다. */
  formatRow?: (row: ResultResponse) => string
  /** 이 컬럼으로 서버 정렬을 걸 때 쓸 필드명. 생략하면 `key`를 쓰고,
   * false면 정렬을 지원하지 않는 컬럼(헤더 클릭 비활성). */
  sortKey?: string | false
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
  /** 세부계정 펼치기 가능 여부. 생략하면 true(기본은 펼침 가능) — 원문 구조상
   * 그 자체가 합계/최종값이라 하위 항목이 있을 수 없는 항목만 false로 명시한다
   * (실측: fixtures 20건 전부에서 이 항목들의 children이 0건이었다, 2026-07-21). */
  expandable?: boolean
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

// 주소/대표자/업종은 M8 3단계(§4-10)부터 DART 기업개황 원본이라 "미확정" 단서가
// 필요 없다. 전화번호만은 기업개황 엑셀에 열이 없어 항상 비어 있다.
export const BASIC_COLUMNS: ResultColumn[] = [
  { key: 'corp_name', label: '회사명' },
  { key: 'address', label: '주소' },
  { key: 'phone', label: '전화번호 (미수집)' },
  { key: 'ceo_name', label: '대표자' },
  { key: 'induty_name', label: '업종' },
  { key: 'induty_code', label: '업종코드' },
  { key: 'fiscal_date', label: '결산기준일' },
  { key: 'audit_opinion', label: '감사의견' },
  // 감사인은 이름과 주소가 별도 컬럼으로 오지만, 화면에서는 "안경회계법인(경상남도
  // 창원시)" 한 칸으로 합쳐 보여준다(정렬은 이름 기준).
  {
    key: 'auditor_name',
    label: '감사인',
    formatRow: (row) => formatAuditor(row.auditor_name, row.auditor_address) ?? '-',
  },
]

/** "안경회계법인(경상남도 창원시)" — 주소는 앞 두 토큰(시도/시군구)만 쓴다.
 * 백엔드가 저장 시점에 시도를 표준명으로 정규화해 두므로 여기서 약칭을 펴지 않는다. */
export function formatAuditor(
  name: string | null | undefined,
  address: string | null | undefined,
): string | null {
  if (!name) return null
  const region = (address ?? '').split(/\s+/).filter(Boolean).slice(0, 2).join(' ')
  return region ? `${name}(${region})` : name
}

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
      { label: '자산총계', curKey: 'total_assets_cur', prvKey: 'total_assets_prv', snapKey: 'total_assets', format: formatNumber, expandable: false },
      { label: '유동부채', curKey: 'current_liab_cur', prvKey: 'current_liab_prv', snapKey: 'current_liab', format: formatNumber },
      { label: '비유동부채', curKey: 'noncurrent_liab_cur', prvKey: 'noncurrent_liab_prv', snapKey: 'noncurrent_liab', format: formatNumber },
      { label: '부채총계', curKey: 'total_liab_cur', prvKey: 'total_liab_prv', snapKey: 'total_liab', format: formatNumber, expandable: false },
      { label: '자본총계', curKey: 'total_equity_cur', prvKey: 'total_equity_prv', snapKey: 'total_equity', format: formatNumber, expandable: false },
    ],
  },
  {
    section: 'is',
    title: '손익계산서',
    items: [
      { label: '매출액', curKey: 'revenue_cur', prvKey: 'revenue_prv', snapKey: 'revenue', format: formatNumber },
      { label: '매출원가', curKey: 'cogs_cur', prvKey: 'cogs_prv', snapKey: 'cogs', format: formatNumber },
      { label: '매출총이익', curKey: 'gross_profit_cur', prvKey: 'gross_profit_prv', snapKey: 'gross_profit', format: formatNumber, expandable: false },
      { label: '판매비와관리비', curKey: 'sga_cur', prvKey: 'sga_prv', snapKey: 'sga', format: formatNumber },
      { label: '영업이익', curKey: 'operating_income_cur', prvKey: 'operating_income_prv', snapKey: 'operating_income', format: formatNumber, expandable: false },
      // 영업외수익/영업외비용 (2026-07-22) — 표준 13항목엔 없는 best-effort 추가
      // 항목이라 파싱상태가 OK여도 값이 없을 수 있다(CF 4항목과 동형 패턴). 세부계정
      // (이자수익/이자비용/외환차익/외환차손 등)이 있는 경우가 흔해 펼치기 가능.
      { label: '영업외수익', curKey: 'non_operating_income_cur', prvKey: 'non_operating_income_prv', snapKey: 'non_operating_income', format: formatNumber },
      { label: '영업외비용', curKey: 'non_operating_expense_cur', prvKey: 'non_operating_expense_prv', snapKey: 'non_operating_expense', format: formatNumber },
      { label: '당기순이익', curKey: 'net_income_cur', prvKey: 'net_income_prv', snapKey: 'net_income', format: formatNumber, expandable: false },
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
      { label: '기말의현금', curKey: 'cf_ending_cash_cur', prvKey: 'cf_ending_cash_prv', snapKey: 'cf_ending_cash', format: formatNumber, expandable: false },
    ],
  },
]

function itemsToColumns(items: FinancialItem[]): ResultColumn[] {
  return items.flatMap((item) => [
    { key: item.curKey, label: `${item.label}_당기`, format: item.format },
    { key: item.prvKey, label: `${item.label}_전기`, format: item.format },
  ])
}

// 재무상태표 + 손익계산서 (표준 13항목 + 영업외수익/영업외비용 best-effort 2항목, §4-8).
export const FINANCIAL_COLUMNS: ResultColumn[] = FINANCIAL_GROUPS
  .filter((group) => group.section !== 'cf')
  .flatMap((group) => itemsToColumns(group.items))

// 현금흐름표 4항목 (§4-8) — 기본 숨김, 컬럼 토글로 노출.
export const CASH_FLOW_COLUMNS: ResultColumn[] = itemsToColumns(
  FINANCIAL_GROUPS.find((group) => group.section === 'cf')?.items ?? [],
)

export const STATUS_COLUMNS: ResultColumn[] = [
  {
    key: 'parse_status',
    label: '파싱상태',
    // FAILED인데 rcept_no가 없으면 파서가 실패한 게 아니라 DART에 감사보고서
    // 공시 자체가 없는 것이다(상장사·외감 대상 제외 등) — 검수 대상이 아니므로
    // 다른 문구로 구분해 보여준다(2026-07-20).
    formatRow: (row) => {
      if (row.parse_status === 'FAILED' && !row.rcept_no) return '감사보고서 없음'
      return row.parse_status ?? '-'
    },
  },
  // 최신 DART 공시 접수일자 — "휴면·폐업 추정" 판정의 근거값(2026-07-22).
  // YYYYMMDD를 YYYY-MM-DD로 보기 좋게 바꿔 표시한다.
  {
    key: 'latest_disclosure_date',
    label: '최근 공시일자',
    format: (value) => {
      if (!value || typeof value !== 'string' || value.length !== 8) return '-'
      return `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`
    },
  },
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
  'auditor_name',
  'revenue_cur',
  'operating_income_cur',
  'net_income_cur',
  'parse_status',
]

export function formatCell(column: ResultColumn, row: ResultResponse): string {
  if (column.formatRow) return column.formatRow(row)
  const value = row[column.key]
  if (column.format) return column.format(value)
  if (value === null || value === undefined || value === '') return '-'
  return String(value)
}
