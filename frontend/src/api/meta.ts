import { apiClient } from './client'
import type { FscIndexStatus, IndustryMeta, QuotaResponse, RegionMeta } from '../types'

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

export async function getFscIndexStatus(): Promise<FscIndexStatus> {
  const { data } = await apiClient.get<FscIndexStatus>('/meta/fsc-index/status')
  return data
}
