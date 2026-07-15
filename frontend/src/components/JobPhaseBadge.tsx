import { Badge } from '@mantine/core'
import type { JobPhase } from '../types'

const COLOR_BY_PHASE: Record<JobPhase, string> = {
  CANDIDATES: 'teal',
  FINANCIALS: 'indigo',
}

const LABEL_BY_PHASE: Record<JobPhase, string> = {
  CANDIDATES: '후보 확정',
  FINANCIALS: '재무정보 수집',
}

/** Job의 phase(§4-7-1) 배지 — CANDIDATES(Phase 1)/FINANCIALS(Phase 2). */
export default function JobPhaseBadge({ phase }: { phase: JobPhase | null }) {
  if (!phase) return null
  return (
    <Badge color={COLOR_BY_PHASE[phase]} variant="light">
      {LABEL_BY_PHASE[phase]}
    </Badge>
  )
}
