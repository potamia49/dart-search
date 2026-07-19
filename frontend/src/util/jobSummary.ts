import type { JobResponse } from '../types'

const EOK = 100_000_000

function formatEok(krw: number | null | undefined): string {
  if (krw === null || krw === undefined) return '무제한'
  return `${(krw / EOK).toLocaleString()}억원`
}

/** Job 카드에 표시할 조건 요약 문자열. */
export function summarizeJobConditions(job: JobResponse): string {
  const parts: string[] = []

  const region = job.cond_region
  // 시도 다중 선택 + 시도별 시군구. 구 Job의 cond_region은 단일 문자열 sido +
  // 평면 sigungu라, 두 형태를 모두 흡수한다.
  const rawSido = region?.sido as string[] | string | null | undefined
  const sidoList = Array.isArray(rawSido) ? rawSido : rawSido ? [rawSido] : []
  const bySido = (region as { sigungu_by_sido?: Record<string, string[]> })?.sigungu_by_sido
  const flatSigungu = (region as { sigungu?: string[] })?.sigungu ?? []

  function sigunguOf(sidoName: string): string[] {
    if (bySido && bySido[sidoName]) return bySido[sidoName]
    // 구 평면 형태: 시도가 1개일 때만 그 시도에 매핑됐던 값.
    if (sidoList.length === 1) return flatSigungu
    return []
  }

  if (sidoList.length === 0) {
    parts.push('전국')
  } else if (sidoList.length === 1) {
    const sg = sigunguOf(sidoList[0])
    parts.push(`${sidoList[0]}${sg.length > 0 ? ` (${sg.join(', ')})` : ' 전체'}`)
  } else {
    const label = sidoList
      .map((s) => {
        const sg = sigunguOf(s)
        return sg.length > 0 ? `${s}(${sg.length})` : s
      })
      .join(', ')
    parts.push(`${label} · ${sidoList.length}개 시도`)
  }

  const revenue = job.cond_revenue
  if (revenue && (revenue.min_krw !== null || revenue.max_krw !== null)) {
    parts.push(`매출 ${formatEok(revenue.min_krw)}~${formatEok(revenue.max_krw)}`)
  }

  const totalAssets = job.cond_total_assets
  if (totalAssets && (totalAssets.min_krw !== null || totalAssets.max_krw !== null)) {
    parts.push(`총자산 ${formatEok(totalAssets.min_krw)}~${formatEok(totalAssets.max_krw)}`)
  }

  const industry = job.cond_industry
  if (industry && industry.length > 0) {
    parts.push(`업종 ${industry.length}개`)
  } else {
    parts.push('업종 전체')
  }

  return parts.join(' · ')
}
