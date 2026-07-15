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
  if (region?.sido) {
    const sigungu = region.sigungu ?? []
    const sigunguPart = sigungu.length > 0 ? ` (${sigungu.join(', ')})` : ' 전체'
    parts.push(`${region.sido}${sigunguPart}`)
  } else {
    parts.push('전국')
  }

  const revenue = job.cond_revenue
  if (revenue && (revenue.min_krw !== null || revenue.max_krw !== null)) {
    parts.push(`매출 ${formatEok(revenue.min_krw)}~${formatEok(revenue.max_krw)}`)
  }

  const industry = job.cond_industry
  if (industry && industry.length > 0) {
    parts.push(`업종 ${industry.length}개`)
  } else {
    parts.push('업종 전체')
  }

  const period = job.cond_period
  if (period) {
    parts.push(`${period.bgn_de}~${period.end_de}`)
  }

  return parts.join(' · ')
}
