import { Alert, Text } from '@mantine/core'
import type { FscIndexStatus } from '../types'

/**
 * fsc_corp_index(§4-7 Phase 1 A1) 전역 인덱스의 마지막 완료 갱신 시각을
 * 보여준다. 백엔드(run_job_phase1)는 TTL(기본 180일)이 지나도 자동으로
 * 갱신하지 않고 로그에만 경고를 남기므로, 사용자가 매번 물어보지 않아도
 * 화면에서 바로 알 수 있게 2026-07-15 추가했다.
 */
export default function FscIndexStatusNote({ status }: { status: FscIndexStatus | null }) {
  if (!status) return null

  const formattedDate = status.last_completed_at
    ? status.last_completed_at.replace('T', ' ')
    : null

  if (status.row_count === 0) {
    return (
      <Alert color="yellow" variant="light">
        FSC 전역 인덱스가 아직 비어 있습니다 — 후보 확정(Phase 1) 작업이 즉시
        실패합니다. 관리자가 먼저 "금융위 전역 인덱스 갱신"을 실행해야 합니다.
      </Alert>
    )
  }

  if (status.crawl_in_progress) {
    return (
      <Text size="sm" c="dimmed">
        FSC 전역 인덱스 갱신 진행 중입니다 (기존 데이터로는 계속 검색 가능).
      </Text>
    )
  }

  if (status.is_stale) {
    return (
      <Alert color="yellow" variant="light">
        FSC 전역 인덱스 마지막 갱신: {formattedDate} (기준 {status.ttl_days}일 초과 —
        오래된 데이터로 동작 중입니다. 필요하면 관리자에게 갱신을 요청하세요.)
      </Alert>
    )
  }

  return (
    <Text size="sm" c="dimmed">
      FSC 전역 인덱스 마지막 갱신: {formattedDate} (최신, {status.row_count.toLocaleString()}개사)
    </Text>
  )
}
