import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Group,
  Loader,
  Pagination,
  Paper,
  SegmentedControl,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { startFinancials } from '../api/jobs'
import { listResults } from '../api/results'
import type { HistoryYears, JobResponse, ResultListResponse } from '../types'
import { formatNumber } from '../util/resultColumns'

const HISTORY_YEARS_OPTIONS: { label: string; value: HistoryYears }[] = [
  { label: '2년', value: 2 },
  { label: '4년', value: 4 },
  { label: '6년', value: 6 },
  { label: '10년', value: 10 },
]

const PAGE_SIZE = 50

interface CandidatesViewProps {
  job: JobResponse
}

/** phase='CANDIDATES' Job의 결과 화면 — §4-7-1/§7-3 "후보 목록" 뷰.
 *
 * 아직 원문을 열어보지 않았으므로 재무 13항목/parse_status는 의미가 없다 —
 * A3 스크리닝 추정치(매출액/총자산)만 "추정" 라벨과 함께 강조 표시하고,
 * 화면 상단에 "재무정보 수집 시작" 버튼 + 수집기간 선택을 배치한다.
 */
export default function CandidatesView({ job }: CandidatesViewProps) {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const [data, setData] = useState<ResultListResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [historyYears, setHistoryYears] = useState<HistoryYears>(4)
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    setLoading(true)
    setError(null)
    listResults(job.id, { page, page_size: PAGE_SIZE })
      .then(setData)
      .catch(() => setError('후보 목록을 불러오지 못했습니다. 백엔드 서버가 실행 중인지 확인하세요.'))
      .finally(() => setLoading(false))
  }, [job.id, page])

  const canStart = job.status === 'DONE'

  async function handleStart() {
    setStarting(true)
    try {
      await startFinancials(job.id, { history_years: historyYears })
      notifications.show({ color: 'green', message: `작업 #${job.id}의 재무정보 수집을 시작했습니다.` })
      navigate('/jobs')
    } catch {
      notifications.show({ color: 'red', message: '재무정보 수집 시작에 실패했습니다.' })
    } finally {
      setStarting(false)
    }
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1

  return (
    <Stack>
      <Alert color="blue" title="후보 확정 결과 (Phase 1)">
        아직 감사보고서 원문을 파싱하지 않았습니다. 아래 매출액/총자산은 공공데이터(금융위
        기업재무정보) 기준 <b>확정 전 추정치</b>입니다 — 재무 13항목 전체와 parse_status는
        재무정보 수집(Phase 2) 완료 후에 채워집니다.
      </Alert>

      {!canStart && (
        <Alert color="yellow">
          현재 작업 상태({job.status ?? '알 수 없음'})에서는 재무정보 수집을 시작할 수 없습니다.
          후보 확정(Phase 1)이 완료(DONE)된 뒤에 시작할 수 있습니다.
        </Alert>
      )}

      <Paper withBorder p="md">
        <Group justify="space-between" align="flex-end" wrap="wrap">
          <div>
            <Title order={4} mb="xs">
              재무정보 수집 시작
            </Title>
            <Text size="sm" c="dimmed" mb="xs">
              확정된 후보 회사에 대해 최근 N년치 재무정보(다년치 이력)를 DART 원문에서 수집합니다.
            </Text>
            <SegmentedControl
              value={String(historyYears)}
              onChange={(v) => setHistoryYears(Number(v) as HistoryYears)}
              data={HISTORY_YEARS_OPTIONS.map((opt) => ({
                label: opt.label,
                value: String(opt.value),
              }))}
            />
          </div>
          <Button size="md" loading={starting} disabled={!canStart} onClick={handleStart}>
            재무정보 수집 시작
          </Button>
        </Group>
      </Paper>

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
                  <Table.Th>회사명</Table.Th>
                  <Table.Th>주소</Table.Th>
                  <Table.Th>전화번호</Table.Th>
                  <Table.Th>대표자</Table.Th>
                  <Table.Th>매출액 (추정)</Table.Th>
                  <Table.Th>총자산 (추정)</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.items.map((row) => (
                  <Table.Tr key={row.id}>
                    <Table.Td>{row.corp_name ?? '-'}</Table.Td>
                    <Table.Td>{row.address ?? '-'}</Table.Td>
                    <Table.Td>{row.phone ?? '-'}</Table.Td>
                    <Table.Td>{row.ceo_name ?? '-'}</Table.Td>
                    <Table.Td>{formatNumber(row.revenue_cur)}</Table.Td>
                    <Table.Td>{formatNumber(row.total_assets_cur)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>

          {data.items.length === 0 && <Text c="dimmed">확정된 후보 회사가 없습니다.</Text>}

          <Group justify="center">
            <Pagination value={page} onChange={setPage} total={totalPages} />
          </Group>
        </>
      )}
    </Stack>
  )
}
