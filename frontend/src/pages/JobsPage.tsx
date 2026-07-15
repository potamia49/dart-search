import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Group,
  Paper,
  Progress,
  Stack,
  Text,
  Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { cancelJob, listJobs, resumeJob, retryFailedJob } from '../api/jobs'
import { getQuota } from '../api/meta'
import type { JobResponse, QuotaResponse } from '../types'
import JobStatusBadge from '../components/JobStatusBadge'
import { summarizeJobConditions } from '../util/jobSummary'

const POLL_INTERVAL_MS = 2000

export default function JobsPage() {
  const navigate = useNavigate()
  const [jobs, setJobs] = useState<JobResponse[]>([])
  const [quota, setQuota] = useState<QuotaResponse | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const intervalRef = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [jobsData, quotaData] = await Promise.all([listJobs(), getQuota()])
      setJobs(jobsData)
      setQuota(quotaData)
      setLoadError(null)
    } catch {
      setLoadError('작업 목록을 불러오지 못했습니다. 백엔드 서버가 실행 중인지 확인하세요.')
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  // RUNNING 상태 Job이 하나라도 있으면 2초 간격 폴링 (상세개발계획.md §6 "진행률 전달은 폴링").
  useEffect(() => {
    const hasRunning = jobs.some((j) => j.status === 'RUNNING')
    if (hasRunning && intervalRef.current === null) {
      intervalRef.current = window.setInterval(refresh, POLL_INTERVAL_MS)
    } else if (!hasRunning && intervalRef.current !== null) {
      window.clearInterval(intervalRef.current)
      intervalRef.current = null
    }
    return () => {
      if (intervalRef.current !== null) {
        window.clearInterval(intervalRef.current)
        intervalRef.current = null
      }
    }
  }, [jobs, refresh])

  async function handleCancel(id: number) {
    try {
      await cancelJob(id)
      notifications.show({ message: `작업 #${id}을 취소했습니다.` })
      refresh()
    } catch {
      notifications.show({ color: 'red', message: '취소에 실패했습니다.' })
    }
  }

  async function handleResume(id: number) {
    try {
      await resumeJob(id)
      notifications.show({ message: `작업 #${id}을 이어서 진행합니다.` })
      refresh()
    } catch {
      notifications.show({ color: 'red', message: '이어하기에 실패했습니다.' })
    }
  }

  async function handleRetryFailed(id: number) {
    try {
      await retryFailedJob(id)
      notifications.show({ message: `작업 #${id}의 실패 건 재시도를 시작했습니다.` })
      refresh()
    } catch {
      notifications.show({ color: 'red', message: '재시도 요청에 실패했습니다.' })
    }
  }

  return (
    <Stack maw={960} mx="auto">
      <Group justify="space-between">
        <Title order={2}>작업 현황</Title>
        {quota && (
          <Text size="sm" c="dimmed">
            오늘 API 호출량: {quota.call_count.toLocaleString()} / {quota.limit.toLocaleString()}건
            (잔여 {quota.remaining.toLocaleString()}건)
          </Text>
        )}
      </Group>

      {loadError && <Alert color="red">{loadError}</Alert>}

      {jobs.length === 0 && !loadError && (
        <Text c="dimmed">아직 실행한 작업이 없습니다. 검색 페이지에서 조건을 입력해 시작하세요.</Text>
      )}

      <Stack gap="md">
        {jobs.map((job) => {
          const total = job.progress_total ?? 0
          const done = job.progress_done ?? 0
          const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
          return (
            <Paper
              key={job.id}
              withBorder
              p="md"
              style={{ cursor: 'pointer' }}
              onClick={() => navigate(`/jobs/${job.id}/results`)}
            >
              <Stack gap="xs">
                <Group justify="space-between" align="flex-start">
                  <div>
                    <Group gap="sm">
                      <Text fw={600}>#{job.id} {job.name ?? '(이름 없음)'}</Text>
                      <JobStatusBadge status={job.status} />
                    </Group>
                    <Text size="sm" c="dimmed">
                      {summarizeJobConditions(job)}
                    </Text>
                  </div>
                  <Button variant="light">결과 보기</Button>
                </Group>

                <div>
                  <Text size="xs" c="dimmed" mb={4}>
                    STEP {job.current_step ?? '-'} — {done}/{total}
                  </Text>
                  <Progress value={percent} animated={job.status === 'RUNNING'} />
                </div>

                {job.error_msg && (
                  <Text size="sm" c="red">
                    {job.error_msg}
                  </Text>
                )}

                <Group gap="xs" onClick={(e) => e.stopPropagation()}>
                  {job.status === 'PAUSED_QUOTA' && (
                    <Button size="xs" onClick={() => handleResume(job.id)}>
                      이어하기
                    </Button>
                  )}
                  {(job.status === 'RUNNING' || job.status === 'PENDING') && (
                    <Button size="xs" color="red" variant="outline" onClick={() => handleCancel(job.id)}>
                      취소
                    </Button>
                  )}
                  {job.status === 'FAILED' && (
                    <>
                      <Button size="xs" onClick={() => handleResume(job.id)}>
                        이어하기
                      </Button>
                      <Button size="xs" variant="outline" onClick={() => handleRetryFailed(job.id)}>
                        파싱 재시도
                      </Button>
                    </>
                  )}
                </Group>
              </Stack>
            </Paper>
          )
        })}
      </Stack>
    </Stack>
  )
}
