import { apiClient } from './client'
import type { JobCreateRequest, JobResponse, StartFinancialsRequest } from '../types'

export async function createJob(payload: JobCreateRequest): Promise<JobResponse> {
  const { data } = await apiClient.post<JobResponse>('/jobs', payload)
  return data
}

export async function listJobs(): Promise<JobResponse[]> {
  const { data } = await apiClient.get<JobResponse[]>('/jobs')
  return data
}

export async function getJob(id: number): Promise<JobResponse> {
  const { data } = await apiClient.get<JobResponse>(`/jobs/${id}`)
  return data
}

export async function cancelJob(id: number): Promise<JobResponse> {
  const { data } = await apiClient.post<JobResponse>(`/jobs/${id}/cancel`)
  return data
}

export async function resumeJob(id: number): Promise<JobResponse> {
  const { data } = await apiClient.post<JobResponse>(`/jobs/${id}/resume`)
  return data
}

export async function retryFailedJob(id: number): Promise<JobResponse> {
  const { data } = await apiClient.post<JobResponse>(`/jobs/${id}/retry-failed`)
  return data
}

/** 과거 Job 기록 삭제 (results/financial_snapshots 포함). RUNNING/PENDING은 백엔드가 400으로 거부한다. */
export async function deleteJob(id: number): Promise<void> {
  await apiClient.delete(`/jobs/${id}`)
}

/** Phase 1이 확정한 후보(phase=CANDIDATES + status=DONE)에 대해 Phase 2(재무정보
 * 수집)를 시작한다 (§4-7-1). */
export async function startFinancials(
  id: number,
  payload: StartFinancialsRequest,
): Promise<JobResponse> {
  const { data } = await apiClient.post<JobResponse>(`/jobs/${id}/start-financials`, payload)
  return data
}
