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
import { listResults, setResultExcluded } from '../api/results'
import type { HistoryYears, JobResponse, ResultListResponse, ResultResponse } from '../types'
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
 * 화면 상단에 "재무정보 수집 시작" 버튼 + 수집기간 선택을 배치한다.
 *
 * **M8 5단계에서 매출액/총자산 표시의 의미가 바뀌었다.** 예전에는 A3(금융위
 * 건별 스크리닝)의 "추정치"였고 그 값으로 후보를 실제로 걸러내기까지 했다.
 * 지금은 A3가 폐기돼(§4-10-C: 1년 묵은 값으로 거르면 조건에 맞는 회사의
 * 25.3%를 조용히 놓친다) **어떤 후보도 이 값으로 제외되지 않는다** — 순수한
 * 참고 표시(`ref_revenue`/`ref_total_assets`)이며, 회사마다 확보된 회계연도가
 * 달라 `ref_fin_year`를 반드시 함께 보여준다. 매출액/총자산 조건 판정은
 * Phase 2가 DART 원문을 파싱한 뒤 한 곳(B4)에서만 일어난다.
 */
export default function CandidatesView({ job }: CandidatesViewProps) {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const [data, setData] = useState<ResultListResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [historyYears, setHistoryYears] = useState<HistoryYears>(4)
  const [starting, setStarting] = useState(false)
  const [updatingIds, setUpdatingIds] = useState<Set<number>>(new Set())

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

  async function handleToggleIncluded(row: ResultResponse, included: boolean) {
    setUpdatingIds((prev) => new Set(prev).add(row.id))
    try {
      const updated = await setResultExcluded(job.id, row.id, !included)
      setData((prev) =>
        prev
          ? { ...prev, items: prev.items.map((item) => (item.id === row.id ? updated : item)) }
          : prev,
      )
    } catch {
      notifications.show({ color: 'red', message: '선택 변경에 실패했습니다.' })
    } finally {
      setUpdatingIds((prev) => {
        const next = new Set(prev)
        next.delete(row.id)
        return next
      })
    }
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
  const excludedOnPage = data ? data.items.filter((row) => row.excluded_manually === 1).length : 0

  return (
    <Stack>
      <Alert color="blue" title="후보 확정 결과 (1단계)">
        회사명·주소·대표자·업종은 <b>DART 기업개황</b> 기준입니다. 매출액·총자산은
        금융위 요약재무 <b>참고값</b>일 뿐이며 — 회사마다 기준연도가 다르고 최신
        결산이 아직 반영되지 않았을 수 있습니다 — <b>이 값으로 후보를 제외하지
        않습니다.</b> 입력하신 매출액·총자산 조건은 재무정보 수집(2단계)에서
        감사보고서 원문을 파싱한 뒤에 판정합니다. 재무 13항목과 파싱 상태도 그때
        채워집니다.
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
              후보 수와 회사당 공시 건수에 따라 수 분~수십 분 이상 걸릴 수 있고, DART 일일 호출
              한도를 넘으면 자동으로 멈췄다가 다음 날 이어서 진행됩니다 — 위 참고값이 입력 조건에
              가까운 회사부터 먼저 처리하므로 중간에 멈춰도 대상일 가능성이 높은 회사가 먼저
              확보됩니다. 시작 후 작업 목록 화면에서 진행률을 확인하세요. 아래 표에서 "삭제"
              버튼을 누른 회사는 수집 대상에서 제외됩니다("복원" 버튼으로 다시 취소할 수 있습니다).
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
          <Group justify="space-between">
            <Text size="sm" c="dimmed">
              총 {data.total.toLocaleString()}건
            </Text>
            {excludedOnPage > 0 && (
              <Text size="sm" c="dimmed">
                이 페이지에서 제외 선택: {excludedOnPage}건 (체크박스를 다시 켜면 취소됩니다)
              </Text>
            )}
          </Group>
          <Table.ScrollContainer minWidth={800}>
            <Table striped highlightOnHover withTableBorder>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>관리</Table.Th>
                  <Table.Th>회사명</Table.Th>
                  <Table.Th>주소</Table.Th>
                  <Table.Th>업종</Table.Th>
                  <Table.Th>대표자</Table.Th>
                  <Table.Th>매출액 (참고)</Table.Th>
                  <Table.Th>총자산 (참고)</Table.Th>
                  <Table.Th>참고값 기준연도</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.items.map((row) => {
                  const excluded = row.excluded_manually === 1
                  return (
                    <Table.Tr key={row.id} style={excluded ? { opacity: 0.5 } : undefined}>
                      <Table.Td>
                        {excluded ? (
                          <Button
                            size="xs"
                            variant="outline"
                            loading={updatingIds.has(row.id)}
                            onClick={() => handleToggleIncluded(row, true)}
                          >
                            복원
                          </Button>
                        ) : (
                          <Button
                            size="xs"
                            color="red"
                            variant="outline"
                            loading={updatingIds.has(row.id)}
                            onClick={() => handleToggleIncluded(row, false)}
                          >
                            삭제
                          </Button>
                        )}
                      </Table.Td>
                      <Table.Td style={excluded ? { textDecoration: 'line-through' } : undefined}>
                        {row.corp_name ?? '-'}
                      </Table.Td>
                      <Table.Td>{row.address ?? '-'}</Table.Td>
                      <Table.Td>{row.induty_name ?? '-'}</Table.Td>
                      <Table.Td>{row.ceo_name ?? '-'}</Table.Td>
                      <Table.Td>{formatNumber(row.ref_revenue)}</Table.Td>
                      <Table.Td>{formatNumber(row.ref_total_assets)}</Table.Td>
                      <Table.Td>{row.ref_fin_year ? `${row.ref_fin_year}년` : '-'}</Table.Td>
                    </Table.Tr>
                  )
                })}
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
