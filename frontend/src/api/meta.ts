import { apiClient } from './client'
import type {
  CandidatesPreviewRequest,
  CandidatesPreviewResponse,
  DartIndexStatus,
  FscFinancialStatus,
  IndustryMeta,
  QuotaResponse,
  RegionMeta,
} from '../types'

export async function getRegions(): Promise<RegionMeta[]> {
  const { data } = await apiClient.get<RegionMeta[]>('/meta/regions')
  return data
}

export async function getIndustries(): Promise<IndustryMeta[]> {
  const { data } = await apiClient.get<IndustryMeta[]>('/meta/industries')
  return data
}

export async function getQuota(): Promise<QuotaResponse> {
  const { data } = await apiClient.get<QuotaResponse>('/meta/quota')
  return data
}

export async function getDartIndexStatus(): Promise<DartIndexStatus> {
  const { data } = await apiClient.get<DartIndexStatus>('/meta/dart-index/status')
  return data
}

export async function getFscFinancialStatus(): Promise<FscFinancialStatus> {
  const { data } = await apiClient.get<FscFinancialStatus>('/meta/fsc-financial/status')
  return data
}

export async function getCandidatesPreview(
  payload: CandidatesPreviewRequest,
): Promise<CandidatesPreviewResponse> {
  const { data } = await apiClient.post<CandidatesPreviewResponse>(
    '/meta/candidates-preview',
    payload,
  )
  return data
}
