import { Badge } from '@mantine/core'
import type { JobStatus } from '../types'

const COLOR_BY_STATUS: Record<JobStatus, string> = {
  PENDING: 'gray',
  RUNNING: 'blue',
  PAUSED_QUOTA: 'yellow',
  DONE: 'green',
  FAILED: 'red',
  CANCELLED: 'dark',
}

const LABEL_BY_STATUS: Record<JobStatus, string> = {
  PENDING: '대기중',
  RUNNING: '실행중',
  PAUSED_QUOTA: '쿼터 초과 일시정지',
  DONE: '완료',
  FAILED: '실패',
  CANCELLED: '취소됨',
}

export default function JobStatusBadge({ status }: { status: JobStatus | null }) {
  if (!status) return <Badge color="gray">알 수 없음</Badge>
  return <Badge color={COLOR_BY_STATUS[status]}>{LABEL_BY_STATUS[status]}</Badge>
}
