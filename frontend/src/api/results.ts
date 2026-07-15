import { apiClient } from './client'
import type { ExportFormat, FinancialSnapshotResponse, ParseStatus, ResultListResponse } from '../types'

export interface ListResultsParams {
  page?: number
  page_size?: number
  parse_status?: ParseStatus
  excluded_by_revenue?: boolean
}

export async function listResults(
  jobId: number,
  params: ListResultsParams = {},
): Promise<ResultListResponse> {
  const { data } = await apiClient.get<ResultListResponse>(`/jobs/${jobId}/results`, {
    params,
  })
  return data
}

/** 현재 필터를 그대로 export 쿼리에 반영해 파일을 내려받는다 (blob 다운로드). */
export async function exportResults(
  jobId: number,
  format: ExportFormat,
  filters: Omit<ListResultsParams, 'page' | 'page_size'> = {},
): Promise<void> {
  const response = await apiClient.get(`/jobs/${jobId}/export`, {
    params: { format, ...filters },
    responseType: 'blob',
  })

  const disposition = response.headers['content-disposition'] as string | undefined
  const filenameMatch = disposition?.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
  const filename = filenameMatch ? decodeURIComponent(filenameMatch[1]) : `job_${jobId}_results.${format}`

  const blobUrl = window.URL.createObjectURL(response.data as Blob)
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(blobUrl)
}

/** STEP 7(최근 N년 재무이력) — 회사 1건의 연도별 재무 이력(오래된 연도 → 최신 연도 순).
 * 매출액 필터로 제외된 결과는 이력이 애초에 없을 수 있다(에러가 아니라 빈 배열). */
export async function getResultHistory(
  jobId: number,
  resultId: number,
): Promise<FinancialSnapshotResponse[]> {
  const { data } = await apiClient.get<FinancialSnapshotResponse[]>(
    `/jobs/${jobId}/results/${resultId}/history`,
  )
  return data
}
