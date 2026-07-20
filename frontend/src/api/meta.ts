import { apiClient } from './client'
import type {
  CandidatesPreviewRequest,
  CandidatesPreviewResponse,
  DartIndexStatus,
  FscFinancialStatus,
  FscIndexStatus,
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

/** 구 fsc_corp_index(§4-7 A1) 상태. M8에서 후보 확정이 dart_corp_index 기준으로
 * 바뀌면서 화면에서는 더 이상 표시하지 않는다 — 테이블과 크롤러 자체는 롤백
 * 여지로 남겨둔 상태(§4-10-E)라 이 헬퍼도 함께 남긴다. */
export async function getFscIndexStatus(): Promise<FscIndexStatus> {
  const { data } = await apiClient.get<FscIndexStatus>('/meta/fsc-index/status')
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
