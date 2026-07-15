import { apiClient } from './client'
import type { JobCreateRequest, JobResponse } from '../types'

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
