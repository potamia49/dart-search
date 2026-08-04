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

export interface ResultColumnFilterState {
  revenueMinEok: EokInput
  revenueMaxEok: EokInput
  assetsMinEok: EokInput
  assetsMaxEok: EokInput
  parseStatus: ParseStatusExt[]
  auditorChanged: AuditorChangedExt[]
  stale: StaleExt[]
}

/** 화면 최초 진입 상태 = 예전 "전체" 탭과 같은 집합.
 * 휴면·폐업 추정만 기본 숨김(`['ACTIVE']`)이고 나머지는 전부 켠 상태다. */
export const DEFAULT_RESULT_COLUMN_FILTERS: ResultColumnFilterState = {
  revenueMinEok: '',
  revenueMaxEok: '',
  assetsMinEok: '',
  assetsMaxEok: '',
  parseStatus: [...PARSE_STATUS_EXT_VALUES],
  auditorChanged: [...AUDITOR_CHANGED_EXT_VALUES],
  stale: ['ACTIVE'],
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
  return params
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

/** "모든 필터 지우기" 버튼 노출 여부 — 지우기는 **기본값으로 되돌리는 것**이다
 * (휴면·폐업 추정 기본 숨김까지 없애 버리면 지금까지의 기본 동작이 사라진다). */
export function hasNonDefaultFilters(filters: ResultColumnFilterState): boolean {
  return (
    isRevenueFilterActive(filters) ||
    isAssetsFilterActive(filters) ||
    isParseStatusFilterActive(filters) ||
    isAuditorChangedFilterActive(filters) ||
    isStaleFilterActive(filters)
  )
}

function describeRange(min: EokInput, max: EokInput): string {
  if (min !== '' && max !== '') return `${min.toLocaleString()}~${max.toLocaleString()}억원`
  if (min !== '') return `${min.toLocaleString()}억원 이상`
  return `${(max as number).toLocaleString()}억원 이하`
}

/** 지금 걸린 필터를 사람이 읽을 수 있는 짧은 목록으로 — 필터가 걸린 컬럼을 컬럼
 * 토글로 숨겨 버리면 헤더 아이콘 강조가 보이지 않으므로, 상단에서도 한 번 알린다. */
export function describeActiveFilters(filters: ResultColumnFilterState): string[] {
  const parts: string[] = []
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
