import { apiClient } from './client'
import type {
  DocumentSection,
  DocumentSectionResponse,
  ExportFormat,
  FinancialSnapshotResponse,
  ParseStatus,
  ResultListResponse,
  ResultResponse,
} from '../types'

export interface ListResultsParams {
  page?: number
  page_size?: number
  parse_status?: ParseStatus
  excluded_by_revenue?: boolean
  excluded_by_assets?: boolean
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

/** CandidatesView "선택 취소" — 후보 목록에서 특정 회사를 재무정보 수집 대상에서
 * 제외/재포함한다(phase=CANDIDATES 동안만 가능, 실제 삭제는 start-financials
 * 호출 시점에 백엔드가 일괄 처리). */
export async function setResultExcluded(
  jobId: number,
  resultId: number,
  excluded: boolean,
): Promise<ResultResponse> {
  const { data } = await apiClient.patch<ResultResponse>(
    `/jobs/${jobId}/results/${resultId}/exclude`,
    { excluded },
  )
  return data
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

/** §4-8 원문 섹션 열람 — 감사보고서 원문의 재무상태표/손익계산서/현금흐름표/주석을
 * 서버 조립 HTML로 받아온다(추가 API 호출/쿼터 0건, 로컬 문서 캐시만 사용).
 * rcept_no를 지정하면 다년치 이력의 특정 연도 공시를 열람한다(이 결과 소속 공시만 허용). */
export async function getDocumentSection(
  jobId: number,
  resultId: number,
  section: DocumentSection,
  rceptNo?: string,
): Promise<DocumentSectionResponse> {
  const { data } = await apiClient.get<DocumentSectionResponse>(
    `/jobs/${jobId}/results/${resultId}/document-sections/${section}`,
    { params: rceptNo ? { rcept_no: rceptNo } : {} },
  )
  return data
}
