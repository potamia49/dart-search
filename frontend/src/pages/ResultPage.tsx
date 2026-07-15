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

type FilterTab = 'ALL' | 'OK' | 'PARTIAL' | 'FAILED' | 'EXCLUDED'

const PAGE_SIZE = 50

function tabToParams(tab: FilterTab): { parse_status?: ParseStatus; excluded_by_revenue?: boolean } {
  switch (tab) {
    case 'OK':
      return { parse_status: 'OK' }
    case 'PARTIAL':
      return { parse_status: 'PARTIAL' }
    case 'FAILED':
      return { parse_status: 'FAILED' }
    case 'EXCLUDED':
      return { excluded_by_revenue: true }
    default:
      return {}
  }
}

export default function ResultPage() {
  const { id } = useParams<{ id: string }>()
  const jobId = Number(id)

  const [job, setJob] = useState<JobResponse | null>(null)
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
    if (!Number.isFinite(jobId)) return
    getJob(jobId)
      .then(setJob)
      .catch(() => setError('작업 정보를 불러오지 못했습니다.'))
  }, [jobId])

  useEffect(() => {
    if (!Number.isFinite(jobId)) return
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
    <Stack maw={1200} mx="auto">
      <div>
        <Title order={2}>결과 조회 — 작업 #{jobId}</Title>
        {job && (
          <Text size="sm" c="dimmed">
            {summarizeJobConditions(job)}
          </Text>
        )}
      </div>

      <Group justify="space-between">
        <Tabs value={tab} onChange={handleTabChange}>
          <Tabs.List>
            <Tabs.Tab value="ALL">전체</Tabs.Tab>
            <Tabs.Tab value="OK">파싱 성공</Tabs.Tab>
            <Tabs.Tab value="PARTIAL">부분 성공</Tabs.Tab>
            <Tabs.Tab value="FAILED">실패 (검수 필요)</Tabs.Tab>
            <Tabs.Tab value="EXCLUDED">매출액 제외 건</Tabs.Tab>
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
