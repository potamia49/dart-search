import type { ListResultsParams } from '../api/results'

/**
 * 결과 화면(§4-13-C) **컬럼 헤더 필터**의 상태 정의와 쿼리 파라미터 변환 —
 * 2026-08-05에 기존 필터 탭(FilterTab)을 통째로 대체했다.
 *
 * 탭이 표현하던 업무 규칙은 새 개념을 만들지 않고 **필터의 초기 선택값**으로 그대로
 * 이식했다(1:1 이관 원칙):
 * - "휴면·폐업 추정" 기본 숨김 → `stale`의 기본값이 `['ACTIVE']`(=아니오만).
 * - "파싱 실패(검수 필요)"와 "감사보고서 없음" 구분 → 파싱상태 4분류 체크박스
 *   (`FAILED_REVIEW` / `NO_DISCLOSURE`)로 분리.
 *
 * 반대로 **`excluded_by_revenue`/`excluded_by_assets`는 더 이상 화면에서 쓰지 않는다** —
 * 그 두 플래그는 "Job 생성 시점 조건과의 일치 여부"인데 §4-13-A로 그 조건 입력 자체가
 * 사라져 새 Job에서는 항상 0이다. 대신 **실측 파싱값**(`revenue_cur`/`total_assets_cur`)
 * 범위 필터를 쓴다.
 *
 * UI는 `components/ResultColumnFilters.tsx`에 있다.
 */

/** 1억원 = 100,000,000원. 화면은 억원 단위로 받고 백엔드에는 원 단위 정수로 보낸다. */
const EOK = 100_000_000

export const PARSE_STATUS_EXT_VALUES = ['OK', 'PARTIAL', 'FAILED_REVIEW', 'NO_DISCLOSURE'] as const
export const AUDITOR_CHANGED_EXT_VALUES = ['CHANGED', 'UNCHANGED', 'UNKNOWN'] as const
/** 휴면·폐업 추정 — `excluded_by_stale_disclosure` 1/0에 대응(신규 파라미터 없음). */
export const STALE_VALUES = ['STALE', 'ACTIVE'] as const

export type ParseStatusExt = (typeof PARSE_STATUS_EXT_VALUES)[number]
export type AuditorChangedExt = (typeof AUDITOR_CHANGED_EXT_VALUES)[number]
export type StaleExt = (typeof STALE_VALUES)[number]

/** 억원 단위 입력값 — 빈 문자열은 "제한 없음"이다(0과 구분해야 한다). */
export type EokInput = number | ''

// --- §4-14 텍스트/값 목록 필터(2026-08-05) -----------------------------------
// 회사명·주소는 자유 텍스트 부분일치, 업종·감사인·감사의견은 값 목록 다중 선택이고,
// **다섯 컬럼 모두 포함(그것만 보기)과 제외(그것만 빼기)를 지원한다**(사용자 요구).
// 한 컬럼 안에서 포함·제외를 동시에 거는 조합은 만들지 않는다 — 백엔드는 둘을 함께
// 받으면 교집합으로 처리하지만, 화면에서 "포함이면서 제외"는 읽기가 어렵고 실수로
// 0건을 만들기 쉽다. 대신 한 컬럼에 모드 하나를 고르게 한다.

/** 필터 방향 — 고른 조건만 보기(INCLUDE) / 고른 조건만 빼기(EXCLUDE). */
export type FilterMode = 'INCLUDE' | 'EXCLUDE'

export const FILTER_MODE_LABELS: Record<FilterMode, string> = {
  INCLUDE: '포함',
  EXCLUDE: '제외',
}

/** 회사명/주소 — 검색어 1개 + 방향. */
export interface TextFilterState {
  mode: FilterMode
  keyword: string
}

/** 업종/감사인/감사의견 — 고른 값 목록 + 방향. 값은 `distinct-values` 응답을 그대로
 * 되돌려 보내는 계약이라 **다듬지 않는다**(앞뒤 공백까지 원본 그대로). "값 없음"은
 * `BLANK_VALUE_TOKEN` 한 항목으로 표현한다. */
export interface ValueListFilterState {
  mode: FilterMode
  values: string[]
}

/** "값 없음"(NULL 또는 빈 문자열)을 값 목록에서 명시적으로 고르기 위한 예약 토큰.
 *
 * 실제로 필터에 실을 때는 **`distinct-values` 응답의 `blank_token`을 그대로** 쓰고
 * (백엔드가 토큰을 바꿔도 화면이 따라오도록), 이 상수는 화면 표기를 정할 때
 * ("(값 없음)"으로 보여줄지)만 쓴다. 감사인·감사의견은 값 없음이 실측 43~45%라
 * 이 항목이 없으면 목록 필터를 켜는 순간 절반이 사라진다. */
export const BLANK_VALUE_TOKEN = '__BLANK__'

/** 텍스트 필터 입력 상한 — 백엔드 `_MAX_TEXT_FILTER_LEN`(초과 시 400)과 같은 값이다. */
export const MAX_TEXT_FILTER_LEN = 200
/** 값 목록에서 한 번에 고를 수 있는 개수 상한 — 백엔드 `_MAX_VALUE_FILTER_ITEMS`와
 * 같은 값(초과 시 400). 화면은 여기 도달하면 더 못 고르게 막아 400을 미리 피한다. */
export const MAX_VALUE_FILTER_ITEMS = 500

const EMPTY_TEXT_FILTER: TextFilterState = { mode: 'INCLUDE', keyword: '' }
const EMPTY_VALUE_FILTER: ValueListFilterState = { mode: 'INCLUDE', values: [] }

export interface ResultColumnFilterState {
  revenueMinEok: EokInput
  revenueMaxEok: EokInput
  assetsMinEok: EokInput
  assetsMaxEok: EokInput
  parseStatus: ParseStatusExt[]
  auditorChanged: AuditorChangedExt[]
  stale: StaleExt[]
  // §4-14
  corpName: TextFilterState
  address: TextFilterState
  indutyName: ValueListFilterState
  auditorName: ValueListFilterState
  auditOpinion: ValueListFilterState
}

/** 화면 최초 진입 상태 = 예전 "전체" 탭과 같은 집합.
 * 휴면·폐업 추정만 기본 숨김(`['ACTIVE']`)이고 나머지는 전부 켠 상태다.
 * §4-14 필터 5종은 전부 **비어 있는 상태 = 필터 없음**이 기본이다. */
export const DEFAULT_RESULT_COLUMN_FILTERS: ResultColumnFilterState = {
  revenueMinEok: '',
  revenueMaxEok: '',
  assetsMinEok: '',
  assetsMaxEok: '',
  parseStatus: [...PARSE_STATUS_EXT_VALUES],
  auditorChanged: [...AUDITOR_CHANGED_EXT_VALUES],
  stale: ['ACTIVE'],
  corpName: EMPTY_TEXT_FILTER,
  address: EMPTY_TEXT_FILTER,
  indutyName: EMPTY_VALUE_FILTER,
  auditorName: EMPTY_VALUE_FILTER,
  auditOpinion: EMPTY_VALUE_FILTER,
}

export const PARSE_STATUS_LABELS: Record<ParseStatusExt, string> = {
  OK: '파싱 성공',
  PARTIAL: '부분 성공',
  FAILED_REVIEW: '검수 필요',
  NO_DISCLOSURE: '감사보고서 없음',
}

export const AUDITOR_CHANGED_LABELS: Record<AuditorChangedExt, string> = {
  CHANGED: '변동 있음',
  UNCHANGED: '변동 없음',
  UNKNOWN: '판정 불가',
}

export const STALE_LABELS: Record<StaleExt, string> = {
  STALE: '예',
  ACTIVE: '아니오',
}

function eokToKrw(value: EokInput): number | undefined {
  if (value === '' || !Number.isFinite(Number(value))) return undefined
  return Math.round(Number(value) * EOK)
}

/**
 * 최소값 > 최대값으로 뒤집어 입력한 상태인가.
 *
 * 뒤집힌 범위는 **무경고로 위험한 결과**를 만든다 — 값이 있는 회사는 전부 탈락하고
 * 값이 NULL인 회사(파싱 실패·감사보고서 없음 등)만 남는데(§4-13-B의 NULL 통과 규칙),
 * 상단 요약은 정상 범위를 건 것과 똑같이 보인다. 그 상태로 "현재 필터 전체 선택" →
 * 보고서 생성으로 이어지면 엉뚱한 회사에 제안서가 나간다.
 *
 * 표의 금액은 **원 단위**인데 입력칸은 **억원 단위**라, 표의 숫자를 그대로 복사해
 * 넣으면 하한이 천문학적으로 커져 이 상태가 쉽게 재현된다.
 *
 * 값을 자동으로 맞바꾸지 않는다 — 사용자가 알아채고 직접 고치게 한다.
 */
export function isRangeInverted(min: EokInput, max: EokInput): boolean {
  return min !== '' && max !== '' && Number(min) > Number(max)
}

export function isRevenueRangeInverted(filters: ResultColumnFilterState): boolean {
  return isRangeInverted(filters.revenueMinEok, filters.revenueMaxEok)
}

export function isAssetsRangeInverted(filters: ResultColumnFilterState): boolean {
  return isRangeInverted(filters.assetsMinEok, filters.assetsMaxEok)
}

/** 뒤집힌 범위가 걸린 컬럼 이름 목록(경고 문구용). 없으면 빈 배열. */
export function describeInvertedRanges(filters: ResultColumnFilterState): string[] {
  const parts: string[] = []
  if (isRevenueRangeInverted(filters)) parts.push('매출액')
  if (isAssetsRangeInverted(filters)) parts.push('총자산')
  return parts
}

/**
 * 필터 상태 → 목록 조회 쿼리 파라미터(§4-13-B 백엔드 계약).
 *
 * - 체크박스 그룹은 **전부 켠 상태면 파라미터 자체를 보내지 않는다** — 백엔드에서
 *   "4개 다 체크 == 파라미터 생략"이 정확히 같은 집합이라(상호배타 + 전수 포괄)
 *   URL과 쿼리를 불필요하게 키우지 않기 위함이다.
 * - 휴면·폐업 추정은 신규 파라미터 없이 기존 `excluded_by_stale_disclosure`를 쓴다:
 *   "예"만 = true / "아니오"만 = false / 둘 다 = 미지정(둘 다 보임).
 * - 금액 범위는 억원 → 원으로 환산해 보낸다. 값이 NULL인 회사는 백엔드가 항상
 *   통과시키므로(§4-13-B) 범위를 걸어도 목록에서 사라지지 않는다.
 *
 * 목록 조회(`listResults`)와 "현재 필터 전체 선택"(`listAllResultIds`, `ids_only=true`)이
 * **같은 이 결과**를 쓴다 — 두 경로의 조건이 갈리면 "화면에 보이는 것"과 "선택되는 것"이
 * 어긋난다.
 */
export function resultFiltersToParams(filters: ResultColumnFilterState): ListResultsParams {
  const params: ListResultsParams = {}

  // 최소>최대로 뒤집힌 범위는 **아예 보내지 않는다** — 보내면 "값이 있는 회사 전멸 +
  // NULL 회사만 남은 목록"이 정상 결과와 구분되지 않는다. 화면(팝오버 error + 상단
  // 경고)이 무엇이 잘못됐는지 알려 주고, 그동안 목록은 그 컬럼 범위가 없는 상태로 둔다.
  if (!isRevenueRangeInverted(filters)) {
    const revenueMin = eokToKrw(filters.revenueMinEok)
    const revenueMax = eokToKrw(filters.revenueMaxEok)
    if (revenueMin !== undefined) params.revenue_min = revenueMin
    if (revenueMax !== undefined) params.revenue_max = revenueMax
  }
  if (!isAssetsRangeInverted(filters)) {
    const assetsMin = eokToKrw(filters.assetsMinEok)
    const assetsMax = eokToKrw(filters.assetsMaxEok)
    if (assetsMin !== undefined) params.assets_min = assetsMin
    if (assetsMax !== undefined) params.assets_max = assetsMax
  }

  if (filters.parseStatus.length < PARSE_STATUS_EXT_VALUES.length) {
    params.parse_status_ext = filters.parseStatus.join(',')
  }
  if (filters.auditorChanged.length < AUDITOR_CHANGED_EXT_VALUES.length) {
    params.auditor_changed_ext = filters.auditorChanged.join(',')
  }
  if (filters.stale.length === 1) {
    params.excluded_by_stale_disclosure = filters.stale[0] === 'STALE'
  }

  // §4-14 — 텍스트 2종 + 값 목록 3종. 컬럼마다 방향(포함/제외)에 따라 파라미터 이름이
  // 갈리므로 필드별로 명시해 둔다(이름을 문자열로 조립하면 오타가 조용히 "필터 없음"이
  // 된다 — FastAPI는 모르는 쿼리 파라미터를 무시한다).
  const corpName = normalizeKeyword(filters.corpName)
  if (corpName) {
    if (filters.corpName.mode === 'INCLUDE') params.corp_name_contains = corpName
    else params.corp_name_not_contains = corpName
  }
  const address = normalizeKeyword(filters.address)
  if (address) {
    if (filters.address.mode === 'INCLUDE') params.address_contains = address
    else params.address_not_contains = address
  }
  // 고른 값이 0개면 **파라미터를 보내지 않는다**(= 필터 없음). 백엔드 규약상
  // `?induty_name_in=`(빈 값)은 정직하게 0건이지만, 화면에는 "체크를 다 풀었더니
  // 아무것도 없다"로만 보여 §4-13-C의 "최소 1개 강제"와 같은 혼란을 낳는다.
  if (filters.indutyName.values.length > 0) {
    if (filters.indutyName.mode === 'INCLUDE') params.induty_name_in = filters.indutyName.values
    else params.induty_name_not_in = filters.indutyName.values
  }
  if (filters.auditorName.values.length > 0) {
    if (filters.auditorName.mode === 'INCLUDE') params.auditor_name_in = filters.auditorName.values
    else params.auditor_name_not_in = filters.auditorName.values
  }
  if (filters.auditOpinion.values.length > 0) {
    if (filters.auditOpinion.mode === 'INCLUDE') {
      params.audit_opinion_in = filters.auditOpinion.values
    } else {
      params.audit_opinion_not_in = filters.auditOpinion.values
    }
  }
  return params
}

/** 텍스트 필터 입력값 정규화 — 공백뿐이면 "필터 없음"(백엔드 `_normalize_text_filter`와
 * 같은 방침). 상한을 넘는 입력은 애초에 화면에서 막지만(maxLength), 붙여넣기 등으로
 * 넘어오면 잘라서 보낸다 — 400으로 목록 전체가 안 뜨는 것보다는 낫다. */
function normalizeKeyword(filter: TextFilterState): string {
  return filter.keyword.trim().slice(0, MAX_TEXT_FILTER_LEN)
}

export function isRevenueFilterActive(filters: ResultColumnFilterState): boolean {
  return filters.revenueMinEok !== '' || filters.revenueMaxEok !== ''
}

export function isAssetsFilterActive(filters: ResultColumnFilterState): boolean {
  return filters.assetsMinEok !== '' || filters.assetsMaxEok !== ''
}

export function isParseStatusFilterActive(filters: ResultColumnFilterState): boolean {
  return filters.parseStatus.length !== PARSE_STATUS_EXT_VALUES.length
}

export function isAuditorChangedFilterActive(filters: ResultColumnFilterState): boolean {
  return filters.auditorChanged.length !== AUDITOR_CHANGED_EXT_VALUES.length
}

/** 휴면·폐업 추정은 **기본값 자체가 필터**(아니오만)라, "기본값과 다른가"로 강조 여부를
 * 판단한다 — 기본 상태에서 늘 강조돼 있으면 강조가 정보를 잃는다. */
export function isStaleFilterActive(filters: ResultColumnFilterState): boolean {
  return !(filters.stale.length === 1 && filters.stale[0] === 'ACTIVE')
}

/** §4-14 텍스트 필터가 실제로 걸려 있는가(공백뿐이면 안 걸린 것). */
export function isTextFilterActive(filter: TextFilterState): boolean {
  return filter.keyword.trim().length > 0
}

/** §4-14 값 목록 필터가 실제로 걸려 있는가(고른 값이 0개면 안 걸린 것). */
export function isValueFilterActive(filter: ValueListFilterState): boolean {
  return filter.values.length > 0
}

/** "모든 필터 지우기" 버튼 노출 여부 — 지우기는 **기본값으로 되돌리는 것**이다
 * (휴면·폐업 추정 기본 숨김까지 없애 버리면 지금까지의 기본 동작이 사라진다). */
export function hasNonDefaultFilters(filters: ResultColumnFilterState): boolean {
  return (
    isRevenueFilterActive(filters) ||
    isAssetsFilterActive(filters) ||
    isParseStatusFilterActive(filters) ||
    isAuditorChangedFilterActive(filters) ||
    isStaleFilterActive(filters) ||
    isTextFilterActive(filters.corpName) ||
    isTextFilterActive(filters.address) ||
    isValueFilterActive(filters.indutyName) ||
    isValueFilterActive(filters.auditorName) ||
    isValueFilterActive(filters.auditOpinion)
  )
}

function describeRange(min: EokInput, max: EokInput): string {
  if (min !== '' && max !== '') return `${min.toLocaleString()}~${max.toLocaleString()}억원`
  if (min !== '') return `${min.toLocaleString()}억원 이상`
  return `${(max as number).toLocaleString()}억원 이하`
}

/** 값 목록의 한 값을 화면에 보여줄 문구로 — "값 없음" 토큰만 사람 말로 바꾼다.
 * 그 외에는 원본 그대로다(다듬으면 되돌려 보낼 때 매칭되지 않는다). */
export function formatFilterValue(value: string): string {
  return value === BLANK_VALUE_TOKEN ? '(값 없음)' : value
}

/** "업종 전자부품 외 2개 포함"처럼 값 목록 필터를 한 줄로 요약한다. */
function describeValueFilter(label: string, filter: ValueListFilterState): string {
  const [first, ...rest] = filter.values
  const head = formatFilterValue(first)
  const body = rest.length > 0 ? `${head} 외 ${rest.length}개` : head
  return `${label} ${body} ${FILTER_MODE_LABELS[filter.mode]}`
}

/** 지금 걸린 필터를 사람이 읽을 수 있는 짧은 목록으로 — 필터가 걸린 컬럼을 컬럼
 * 토글로 숨겨 버리면 헤더 아이콘 강조가 보이지 않으므로, 상단에서도 한 번 알린다. */
export function describeActiveFilters(filters: ResultColumnFilterState): string[] {
  const parts: string[] = []
  // §4-14 5종은 컬럼 순서(회사명 → 주소 → 업종 → 감사인 → 감사의견)대로 먼저 적는다 —
  // 표의 왼쪽부터 훑는 순서와 같아야 "어느 컬럼 이야기인지" 찾기 쉽다.
  if (isTextFilterActive(filters.corpName)) {
    parts.push(
      `회사명 "${filters.corpName.keyword.trim()}" ${FILTER_MODE_LABELS[filters.corpName.mode]}`,
    )
  }
  if (isTextFilterActive(filters.address)) {
    parts.push(
      `주소 "${filters.address.keyword.trim()}" ${FILTER_MODE_LABELS[filters.address.mode]}`,
    )
  }
  if (isValueFilterActive(filters.indutyName)) parts.push(describeValueFilter('업종', filters.indutyName))
  if (isValueFilterActive(filters.auditorName)) {
    parts.push(describeValueFilter('감사인', filters.auditorName))
  }
  if (isValueFilterActive(filters.auditOpinion)) {
    parts.push(describeValueFilter('감사의견', filters.auditOpinion))
  }
  // 뒤집힌 범위는 실제로 적용하지 않으므로(위 resultFiltersToParams) 요약에서도 그렇게
  // 적는다 — "필터 매출액 1,000~10억원"이 정상 적용처럼 읽히면 안 된다.
  const invertedNote = ' (최소>최대 — 적용 안 함)'
  if (isRevenueFilterActive(filters)) {
    parts.push(
      `매출액 ${describeRange(filters.revenueMinEok, filters.revenueMaxEok)}${
        isRevenueRangeInverted(filters) ? invertedNote : ''
      }`,
    )
  }
  if (isAssetsFilterActive(filters)) {
    parts.push(
      `총자산 ${describeRange(filters.assetsMinEok, filters.assetsMaxEok)}${
        isAssetsRangeInverted(filters) ? invertedNote : ''
      }`,
    )
  }
  if (isParseStatusFilterActive(filters)) {
    parts.push(
      `파싱상태 ${filters.parseStatus.map((v) => PARSE_STATUS_LABELS[v]).join('·') || '없음'}`,
    )
  }
  if (isAuditorChangedFilterActive(filters)) {
    parts.push(
      `감사인변동 ${filters.auditorChanged.map((v) => AUDITOR_CHANGED_LABELS[v]).join('·') || '없음'}`,
    )
  }
  if (isStaleFilterActive(filters)) {
    // "예·아니오"를 둘 다 고른 상태는 사실상 필터가 없는 것이라 값을 나열하면 어색하다
    // (기본값이 "아니오만"이라 강조 자체는 유지해야 한다 — 기본 숨김이 풀린 상태이므로).
    parts.push(
      filters.stale.length === 2
        ? '휴면·폐업 추정 포함'
        : filters.stale[0] === 'STALE'
          ? // 상단 필터 버튼의 표기("휴면·폐업 추정: 이것만 보기")와 같은 말을 쓴다.
            '휴면·폐업 추정만 보기'
          : `휴면·폐업 추정 ${filters.stale.map((v) => STALE_LABELS[v]).join('·') || '없음'}`,
    )
  }
  return parts
}
