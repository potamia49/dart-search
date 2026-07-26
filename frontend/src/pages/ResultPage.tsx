import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  Alert,
  Badge,
  Button,
  CloseButton,
  Group,
  Loader,
  Pagination,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
  Title,
  Tooltip,
  UnstyledButton,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { getJob } from '../api/jobs'
import { exportResults, listResults } from '../api/results'
import type {
  JobResponse,
  ParseStatus,
  ResultListResponse,
  ResultResponse,
  SortDir,
} from '../types'
import { ALL_COLUMNS, DEFAULT_VISIBLE_KEYS, formatCell } from '../util/resultColumns'
import type { ResultColumn } from '../util/resultColumns'
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
  | 'STALE_DISCLOSURE'
  | 'AUDITOR_CHANGED'

const PAGE_SIZE = 50

/** 검색어 입력마다 요청을 보내지 않도록 하는 디바운스 지연(ms) — SearchPage의
 * 후보 수 미리보기와 같은 값을 쓴다. */
const SEARCH_DEBOUNCE_MS = 400

/** 컬럼의 정렬 필드명 — `sortKey: false`면 정렬 불가 컬럼이다. */
function sortKeyOf(column: ResultColumn): string | null {
  if (column.sortKey === false) return null
  return column.sortKey ?? column.key
}

function tabToParams(tab: FilterTab): {
  parse_status?: ParseStatus
  excluded_by_revenue?: boolean
  excluded_by_assets?: boolean
  excluded_by_stale_disclosure?: boolean
  has_disclosure?: boolean
  auditor_changed?: boolean
} {
  // "휴면·폐업 추정"(최근 1년 이내 DART 공시 없음) 건은 노이즈 성격이 강해
  // 전용 탭이 아닌 모든 화면(전체 탭 포함)에서 기본적으로 숨긴다 — 사용자가
  // 명시적으로 탭을 선택했을 때만 예외로 노출한다(2026-07-22 확정 UX).
  if (tab === 'STALE_DISCLOSURE') {
    return { excluded_by_stale_disclosure: true }
  }
  const baseline = { excluded_by_stale_disclosure: false }
  switch (tab) {
    case 'OK':
      return { ...baseline, parse_status: 'OK' }
    case 'PARTIAL':
      return { ...baseline, parse_status: 'PARTIAL' }
    // FAILED 중에서도 원문을 실제로 열어본 건만 "검수 필요"다. 원문 자체가 없는
    // 건(rcept_no IS NULL)은 파서 문제가 아니라 DART에 감사보고서가 없는 것이라
    // 별도 탭으로 분리한다(2026-07-20).
    case 'FAILED':
      return { ...baseline, parse_status: 'FAILED', has_disclosure: true }
    case 'NO_DISCLOSURE':
      return { ...baseline, parse_status: 'FAILED', has_disclosure: false }
    case 'EXCLUDED_REVENUE':
      return { ...baseline, excluded_by_revenue: true }
    case 'EXCLUDED_ASSETS':
      return { ...baseline, excluded_by_assets: true }
    // 연도별 감사인이 바뀐 회사만(2026-07-26). 판정 불가(NULL — 감사인을 확인한
    // 연도가 1개 이하)인 건은 백엔드 tri-state 규칙상 이 탭에서 빠진다.
    case 'AUDITOR_CHANGED':
      return { ...baseline, auditor_changed: true }
    default:
      return baseline
  }
}

/** 감사인 변동 셀 (2026-07-26) — 목록의 다른 상태 컬럼(파싱상태)이 평문이라,
 * 눈에 띄어야 하는 "변동 있음"에만 뱃지를 써서 대비시킨다. 표기 문구 자체는
 * resultColumns의 formatRow(formatAuditorChanged)가 만든 것을 그대로 쓴다. */
function AuditorChangedCell({ value, label }: { value: number | null; label: string }) {
  if (value === 1) {
    return (
      <Tooltip
        multiline
        w={280}
        label="수집한 재무 이력 안에서 서로 다른 감사인이 2곳 이상 확인됐습니다. 어느 해에 바뀌었는지는 이 행을 클릭해 상세의 '감사인' 행에서 확인하세요."
      >
        <Badge color="orange" variant="light" style={{ cursor: 'help' }}>
          {label}
        </Badge>
      </Tooltip>
    )
  }
  if (value === 0) {
    return (
      <Text span size="sm" c="dimmed">
        {label}
      </Text>
    )
  }
  return (
    <Tooltip
      multiline
      w={280}
      label="감사인 이름을 확인한 연도가 1개 이하라 변동 여부를 판정할 수 없습니다(재무 이력 미수집·감사인 서명란 없음 등)."
    >
      <Text span size="sm" c="dimmed" style={{ cursor: 'help' }}>
        {label}
      </Text>
    </Tooltip>
  )
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
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  // 다중 컬럼 정렬 — 배열 앞쪽이 1순위(주 정렬), 뒤쪽이 보조 정렬이다. 일반 클릭은
  // 단일 정렬로 초기화하고, Shift+클릭은 기존 기준에 이 컬럼을 보조 정렬로 추가/토글한다.
  const [sorts, setSorts] = useState<{ key: string; dir: SortDir }[]>([])
  // "휴면·폐업 추정"으로 기본 숨김 처리된 건수 — 무통보로 사라지지 않도록 탭
  // 뱃지와 "총 N건" 옆 안내 문구로 항상 고지한다(2026-07-22 디자인 리뷰 반영).
  const [staleCount, setStaleCount] = useState<number | null>(null)

  useEffect(() => {
    listResults(jobId, { page: 1, page_size: 1, excluded_by_stale_disclosure: true })
      .then((res) => setStaleCount(res.total))
      .catch(() => setStaleCount(null))
  }, [jobId])

  // 타이핑 중에 매 글자마다 요청하지 않도록 입력을 디바운스한다.
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search.trim())
      setPage(1)
    }, SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [search])

  // 목록 조회와 다운로드가 공유하는 필터/정렬 조건. 매 렌더마다 새 객체가 되면
  // 아래 useEffect가 무한 루프를 도므로 값이 바뀔 때만 다시 만든다.
  const query = useMemo(
    () => ({
      ...tabToParams(tab),
      q: debouncedSearch || undefined,
      // 다중 정렬은 콤마 구분 `field:dir` 목록으로 백엔드에 전달한다(서버 사이드 정렬).
      sort: sorts.length ? sorts.map((s) => `${s.key}:${s.dir}`).join(',') : undefined,
    }),
    [tab, debouncedSearch, sorts],
  )

  useEffect(() => {
    setLoading(true)
    setError(null)
    listResults(jobId, { page, page_size: PAGE_SIZE, ...query })
      .then(setData)
      .catch(() => setError('결과를 불러오지 못했습니다. 백엔드 서버가 실행 중인지 확인하세요.'))
      .finally(() => setLoading(false))
  }, [jobId, page, query])

  function handleTabChange(next: string | null) {
    if (!next) return
    setTab(next as FilterTab)
    setPage(1)
  }

  /** 헤더 클릭 — 정렬 기준을 갱신한다.
   *
   * - 일반 클릭(`additive=false`): 이 컬럼 단독 정렬로 초기화한다. 이미 이 컬럼
   *   하나만 걸려 있으면 오름차순 → 내림차순 → 정렬 해제 순으로 순환한다(기존
   *   단일 정렬 UX 그대로 유지).
   * - Shift+클릭(`additive=true`): 이 컬럼을 정렬 우선순위에 추가/토글한다 —
   *   없으면 맨 뒤(가장 낮은 우선순위)에 오름차순으로 추가, 오름차순이면 내림차순으로,
   *   내림차순이면 이 기준을 제거한다(AG Grid/Excel 다중 정렬 관례). */
  function handleSort(column: ResultColumn, additive: boolean) {
    const key = sortKeyOf(column)
    if (!key) return
    setPage(1)
    setSorts((prev) => {
      const idx = prev.findIndex((s) => s.key === key)
      if (additive) {
        if (idx === -1) return [...prev, { key, dir: 'asc' }]
        if (prev[idx].dir === 'asc') {
          const next = [...prev]
          next[idx] = { key, dir: 'desc' }
          return next
        }
        return prev.filter((s) => s.key !== key)
      }
      if (prev.length === 1 && idx === 0) {
        return prev[0].dir === 'asc' ? [{ key, dir: 'desc' }] : []
      }
      return [{ key, dir: 'asc' }]
    })
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
      // 화면에서 걸러 놓고 정렬한 그대로를 내려받게 한다.
      await exportResults(jobId, format, query)
    } catch {
      notifications.show({ color: 'red', message: '다운로드에 실패했습니다.' })
    } finally {
      setExporting(false)
    }
  }

  // "휴면·폐업 추정" 탭에서는 판정 근거(최근 공시일자)를 항상 볼 수 있어야
  // 하므로 컬럼 토글 상태와 무관하게 표시한다(2026-07-22). ColumnToggle에도
  // 같은 집합을 넘겨 체크박스 상태가 실제 표시 상태와 어긋나지 않게 한다.
  const forcedVisibleKeys = useMemo<Set<keyof ResultResponse>>(
    () => (tab === 'STALE_DISCLOSURE' ? new Set(['latest_disclosure_date']) : new Set()),
    [tab],
  )
  const visibleColumns = ALL_COLUMNS.filter(
    (c) => visibleKeys.has(c.key) || forcedVisibleKeys.has(c.key),
  )
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
            <Tabs.Tab value="AUDITOR_CHANGED">감사인 변동</Tabs.Tab>
            <Tabs.Tab
              value="STALE_DISCLOSURE"
              rightSection={
                staleCount !== null ? (
                  <Badge size="xs" variant="light" color="yellow">
                    {staleCount}
                  </Badge>
                ) : undefined
              }
            >
              휴면·폐업 추정
            </Tabs.Tab>
          </Tabs.List>
        </Tabs>

        <Group gap="xs">
          <TextInput
            placeholder="회사명·주소·대표자·업종·감사인 검색"
            value={search}
            onChange={(event) => setSearch(event.currentTarget.value)}
            rightSection={
              search ? <CloseButton size="sm" onClick={() => setSearch('')} /> : null
            }
            w={260}
          />
          <ColumnToggle
            allColumns={ALL_COLUMNS}
            visibleKeys={visibleKeys}
            onToggle={toggleColumn}
            forcedVisibleKeys={forcedVisibleKeys}
          />
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

      {tab === 'AUDITOR_CHANGED' && (
        <Alert color="gray">
          수집한 재무 이력 안에서 <b>감사인(회계법인·감사반)이 한 번 이상 바뀐</b> 회사입니다.
          어느 해에 바뀌었는지는 행을 클릭해 상세의 재무 이력 표 <b>"감사인" 행</b>에서 확인할
          수 있습니다. 감사인을 확인한 연도가 1개 이하라 <b>판정할 수 없는 건은 이 탭과
          "변동 없음" 어느 쪽에도 포함되지 않습니다</b>. 이 기능이 추가되기 전에 수집된 작업은
          연도별 감사인이 저장돼 있지 않아 목록에서는 전부 판정 불가(-)로 보이지만,
          <b>상세의 "감사인" 행은 원문을 그때그때 읽어 채우므로 기존 작업에서도 연도별로
          확인할 수 있습니다</b>.
        </Alert>
      )}

      {tab === 'STALE_DISCLOSURE' && (
        <Alert color="yellow">
          최근 1년 이내 DART 공시가 없는 회사입니다 — 폐업·휴면 상태일 가능성이 있어
          <b> 다른 모든 탭(전체 포함)에서는 기본적으로 숨겨져 있습니다</b>. 실제 영업
          여부는 이 목록만으로 단정할 수 없으니 필요 시 직접 확인하세요.
        </Alert>
      )}

      {loading && <Loader />}

      {!loading && data && (
        <>
          <Text size="sm" c="dimmed">
            총 {data.total.toLocaleString()}건
            {sorts.length > 0 && (
              <>
                {' '}· 정렬 {sorts.length}개 기준 (헤더 클릭은 그 컬럼 단독 정렬, Shift+클릭은 보조
                정렬 추가){' '}
                <UnstyledButton
                  component="span"
                  td="underline"
                  onClick={() => {
                    setSorts([])
                    setPage(1)
                  }}
                >
                  정렬 초기화
                </UnstyledButton>
              </>
            )}
            {tab !== 'STALE_DISCLOSURE' && !!staleCount && (
              <> · 휴면·폐업 추정 {staleCount.toLocaleString()}건 숨김 (
              <UnstyledButton
                component="span"
                td="underline"
                onClick={() => handleTabChange('STALE_DISCLOSURE')}
              >
                보기
              </UnstyledButton>
              )</>
            )}
          </Text>
          <Table.ScrollContainer minWidth={800}>
            <Table striped highlightOnHover withTableBorder>
              <Table.Thead>
                <Table.Tr>
                  {visibleColumns.map((col) => {
                    const key = sortKeyOf(col)
                    const sortIndex = key === null ? -1 : sorts.findIndex((s) => s.key === key)
                    const active = sortIndex >= 0
                    const dir = active ? sorts[sortIndex].dir : null
                    return (
                      <Table.Th key={col.key}>
                        {key === null ? (
                          col.label
                        ) : (
                          <UnstyledButton
                            onClick={(event) => handleSort(col, event.shiftKey)}
                            style={{ fontWeight: 'inherit', fontSize: 'inherit' }}
                            aria-label={`${col.label} 기준 정렬 (클릭 시 이 컬럼 단독 정렬로 초기화, Shift+클릭 시 보조 정렬 기준으로 추가)`}
                          >
                            <Group component="span" gap={4} wrap="nowrap" display="inline-flex">
                              <span>{col.label}</span>
                              <Text component="span" c={active ? undefined : 'dimmed'}>
                                {active ? (dir === 'asc' ? '▲' : '▼') : '↕'}
                              </Text>
                              {/* 정렬 우선순위(1, 2, 3...) — 다중 정렬일 때만 뱃지로 표시해
                                  몇 번째 기준인지 알 수 있게 한다. */}
                              {active && sorts.length > 1 && (
                                <Badge size="xs" circle variant="filled" color="blue">
                                  {sortIndex + 1}
                                </Badge>
                              )}
                            </Group>
                          </UnstyledButton>
                        )}
                      </Table.Th>
                    )
                  })}
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
                      <Table.Td key={col.key}>
                        {col.key === 'auditor_changed' ? (
                          <AuditorChangedCell
                            value={row.auditor_changed}
                            label={formatCell(col, row)}
                          />
                        ) : (
                          formatCell(col, row)
                        )}
                      </Table.Td>
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
