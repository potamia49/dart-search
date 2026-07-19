import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  Alert,
  Button,
  Group,
  Loader,
  Pagination,
  Stack,
  Table,
  Tabs,
  Text,
  Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { getJob } from '../api/jobs'
import { exportResults, listResults } from '../api/results'
import type { JobResponse, ParseStatus, ResultListResponse, ResultResponse } from '../types'
import { ALL_COLUMNS, DEFAULT_VISIBLE_KEYS, formatCell } from '../util/resultColumns'
import { summarizeJobConditions } from '../util/jobSummary'
import ColumnToggle from '../components/ColumnToggle'
import ResultDetailDrawer from '../components/ResultDetailDrawer'
import CandidatesView from '../components/CandidatesView'

type FilterTab =
  | 'ALL'
  | 'OK'
  | 'PARTIAL'
  | 'FAILED'
  | 'NO_DISCLOSURE'
  | 'EXCLUDED_REVENUE'
  | 'EXCLUDED_ASSETS'

const PAGE_SIZE = 50

function tabToParams(tab: FilterTab): {
  parse_status?: ParseStatus
  excluded_by_revenue?: boolean
  excluded_by_assets?: boolean
  has_disclosure?: boolean
} {
  switch (tab) {
    case 'OK':
      return { parse_status: 'OK' }
    case 'PARTIAL':
      return { parse_status: 'PARTIAL' }
    // FAILED 중에서도 원문을 실제로 열어본 건만 "검수 필요"다. 원문 자체가 없는
    // 건(rcept_no IS NULL)은 파서 문제가 아니라 DART에 감사보고서가 없는 것이라
    // 별도 탭으로 분리한다(2026-07-20).
    case 'FAILED':
      return { parse_status: 'FAILED', has_disclosure: true }
    case 'NO_DISCLOSURE':
      return { parse_status: 'FAILED', has_disclosure: false }
    case 'EXCLUDED_REVENUE':
      return { excluded_by_revenue: true }
    case 'EXCLUDED_ASSETS':
      return { excluded_by_assets: true }
    default:
      return {}
  }
}

/** phase='FINANCIALS'(Phase 2 완료/진행) Job의 "확정 결과" 뷰 — M2~M4 시점과 동일한
 * 결과 테이블/필터 탭/컬럼 토글/상세 Drawer/다운로드. §4-7-2로 "총자산 제외" 탭만 추가됐다. */
function FinancialsResultsView({ jobId }: { jobId: number }) {
  const [tab, setTab] = useState<FilterTab>('ALL')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<ResultListResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [visibleKeys, setVisibleKeys] = useState<Set<keyof ResultResponse>>(
    new Set(DEFAULT_VISIBLE_KEYS),
  )
  const [selected, setSelected] = useState<ResultResponse | null>(null)
  const [exporting, setExporting] = useState(false)

  useEffect(() => {
    setLoading(true)
    setError(null)
    listResults(jobId, { page, page_size: PAGE_SIZE, ...tabToParams(tab) })
      .then(setData)
      .catch(() => setError('결과를 불러오지 못했습니다. 백엔드 서버가 실행 중인지 확인하세요.'))
      .finally(() => setLoading(false))
  }, [jobId, tab, page])

  function handleTabChange(next: string | null) {
    if (!next) return
    setTab(next as FilterTab)
    setPage(1)
  }

  function toggleColumn(key: keyof ResultResponse, visible: boolean) {
    setVisibleKeys((prev) => {
      const next = new Set(prev)
      if (visible) next.add(key)
      else next.delete(key)
      return next
    })
  }

  async function handleExport(format: 'xlsx' | 'csv') {
    setExporting(true)
    try {
      await exportResults(jobId, format, tabToParams(tab))
    } catch {
      notifications.show({ color: 'red', message: '다운로드에 실패했습니다.' })
    } finally {
      setExporting(false)
    }
  }

  const visibleColumns = ALL_COLUMNS.filter((c) => visibleKeys.has(c.key))
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1

  return (
    <Stack>
      <Group justify="space-between">
        <Tabs value={tab} onChange={handleTabChange}>
          <Tabs.List>
            <Tabs.Tab value="ALL">전체</Tabs.Tab>
            <Tabs.Tab value="OK">파싱 성공</Tabs.Tab>
            <Tabs.Tab value="PARTIAL">부분 성공</Tabs.Tab>
            <Tabs.Tab value="FAILED">파싱 실패 (검수 필요)</Tabs.Tab>
            <Tabs.Tab value="NO_DISCLOSURE">감사보고서 없음</Tabs.Tab>
            <Tabs.Tab value="EXCLUDED_REVENUE">매출액 제외 건</Tabs.Tab>
            <Tabs.Tab value="EXCLUDED_ASSETS">총자산 제외 건</Tabs.Tab>
          </Tabs.List>
        </Tabs>

        <Group gap="xs">
          <ColumnToggle allColumns={ALL_COLUMNS} visibleKeys={visibleKeys} onToggle={toggleColumn} />
          <Button variant="default" loading={exporting} onClick={() => handleExport('xlsx')}>
            Excel 다운로드
          </Button>
          <Button variant="default" loading={exporting} onClick={() => handleExport('csv')}>
            CSV 다운로드
          </Button>
        </Group>
      </Group>

      {error && <Alert color="red">{error}</Alert>}

      {tab === 'NO_DISCLOSURE' && (
        <Alert color="gray">
          DART에서 감사보고서 공시를 찾지 못한 회사입니다 — 파싱 실패가 아니라 열어볼 원문이
          없는 경우로, <b>검수 대상이 아닙니다</b>. 외부감사 대상에서 빠졌거나(과거에만 제출),
          조회 기간(재무 이력 연수) 밖에 마지막 보고서가 있는 경우가 대부분입니다.
        </Alert>
      )}

      {loading && <Loader />}

      {!loading && data && (
        <>
          <Text size="sm" c="dimmed">
            총 {data.total.toLocaleString()}건
          </Text>
          <Table.ScrollContainer minWidth={800}>
            <Table striped highlightOnHover withTableBorder>
              <Table.Thead>
                <Table.Tr>
                  {visibleColumns.map((col) => (
                    <Table.Th key={col.key}>{col.label}</Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.items.map((row) => (
                  <Table.Tr
                    key={row.id}
                    style={{ cursor: 'pointer' }}
                    onClick={() => setSelected(row)}
                  >
                    {visibleColumns.map((col) => (
                      <Table.Td key={col.key}>{formatCell(col, row)}</Table.Td>
                    ))}
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>

          {data.items.length === 0 && <Text c="dimmed">표시할 결과가 없습니다.</Text>}

          <Group justify="center">
            <Pagination value={page} onChange={setPage} total={totalPages} />
          </Group>
        </>
      )}

      <ResultDetailDrawer jobId={jobId} result={selected} onClose={() => setSelected(null)} />
    </Stack>
  )
}

export default function ResultPage() {
  const { id } = useParams<{ id: string }>()
  const jobId = Number(id)

  const [job, setJob] = useState<JobResponse | null>(null)
  const [jobError, setJobError] = useState<string | null>(null)

  useEffect(() => {
    if (!Number.isFinite(jobId)) return
    getJob(jobId)
      .then(setJob)
      .catch(() => setJobError('작업 정보를 불러오지 못했습니다.'))
  }, [jobId])

  return (
    <Stack maw={1200} mx="auto">
      <div>
        <Title order={2}>결과 조회 — 작업 #{jobId}</Title>
        {job && (
          <Text size="sm" c="dimmed">
            {summarizeJobConditions(job)}
          </Text>
        )}
      </div>

      {jobError && <Alert color="red">{jobError}</Alert>}

      {!job && !jobError && <Loader />}

      {job && job.phase === 'CANDIDATES' && <CandidatesView job={job} />}
      {job && job.phase === 'FINANCIALS' && <FinancialsResultsView jobId={jobId} />}
    </Stack>
  )
}
