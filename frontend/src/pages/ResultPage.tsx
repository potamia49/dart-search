import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  CloseButton,
  Group,
  Loader,
  Menu,
  Pagination,
  Paper,
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
import { extractApiErrorMessage } from '../api/client'
import {
  SelectAllUnsupportedError,
  SelectionExportUnsupportedError,
  SelectionExportUnverifiableError,
  exportResults,
  generateReport,
  listAllResultIds,
  listResults,
} from '../api/results'
import type {
  GenerateReportResponse,
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
import ReportConfirmModal from '../components/ReportConfirmModal'
import ReportResultModal from '../components/ReportResultModal'
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

/** 이 개수를 넘게 고르면 파일 생성이 오래 걸릴 수 있다고 안내한다(§4-11 — 상한을 걸어
 * 막지는 않는다. 로컬 SQLite 조회뿐이라 수백~수천 건도 동작 자체는 가능하다). */
const LARGE_SELECTION_HINT = 500

/** 컬럼의 정렬 필드명 — `sortKey: false`면 정렬 불가 컬럼이다. */
function sortKeyOf(column: ResultColumn): string | null {
  if (column.sortKey === false) return null
  return column.sortKey ?? column.key
}

type ResultFilterParams = {
  parse_status?: ParseStatus
  excluded_by_revenue?: boolean
  excluded_by_assets?: boolean
  excluded_by_stale_disclosure?: boolean
  has_disclosure?: boolean
  auditor_changed?: boolean
}

function tabToParams(tab: FilterTab): ResultFilterParams {
  // "휴면·폐업 추정"(최근 1년 이내 DART 공시 없음) 건은 노이즈 성격이 강해
  // 전용 탭이 아닌 모든 화면(전체 탭 포함)에서 기본적으로 숨긴다 — 사용자가
  // 명시적으로 탭을 선택했을 때만 예외로 노출한다(2026-07-22 확정 UX).
  if (tab === 'STALE_DISCLOSURE') {
    return { excluded_by_stale_disclosure: true }
  }
  // 매출액/총자산 조건에 걸려 제외된 건도 같은 취급이다(2026-07-28) — 조건에
  // 맞지 않아 이미 탈락한 회사라 기본 목록에 섞이면 안 된다. 전용 탭에서만 본다.
  //
  // 단 전용 탭에서는 **자기 필드만** true로 덮어쓰고 다른 제외 사유는 아예 걸지
  // 않는다: B4의 매출액 필터와 총자산 필터는 서로 독립이라 한 회사가 두 플래그를
  // 동시에 1로 가질 수 있고, 그런 회사에 반대쪽 조건(`false`)까지 걸면 두 탭
  // 어디에도 나오지 않는 사각지대가 생긴다.
  if (tab === 'EXCLUDED_REVENUE') {
    return { excluded_by_stale_disclosure: false, excluded_by_revenue: true }
  }
  if (tab === 'EXCLUDED_ASSETS') {
    return { excluded_by_stale_disclosure: false, excluded_by_assets: true }
  }
  const baseline = {
    excluded_by_stale_disclosure: false,
    excluded_by_revenue: false,
    excluded_by_assets: false,
  }
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

/** 파싱비고 셀 (2026-08-03) — 파서가 남긴 검수 사유는 길이가 들쭉날쭉해("필수 항목 결측:
 * revenue" 한 줄부터 XML 파서 오류 로그까지) 그대로 흘리면 행 높이가 크게 튄다.
 * 두 줄로 잘라 보여주고 전체 문구는 툴팁으로 준다(값이 없으면 그냥 '-'). */
function ParseNoteCell({ text }: { text: string }) {
  if (text === '-') {
    return (
      <Text span size="sm" c="dimmed">
        -
      </Text>
    )
  }
  return (
    <Tooltip multiline w={360} label={text} withinPortal>
      <Text size="sm" lineClamp={2} maw={280} style={{ cursor: 'help' }}>
        {text}
      </Text>
    </Tooltip>
  )
}

/** phase='FINANCIALS'(Phase 2 완료/진행) Job의 "확정 결과" 뷰 — M2~M4 시점과 동일한
 * 결과 테이블/필터 탭/컬럼 토글/상세 Drawer/다운로드. §4-7-2로 "총자산 제외" 탭만 추가됐다.
 *
 * 다운로드 경로는 **선택 항목 다운로드(§4-11) 하나뿐이다** — 상단에 있던
 * "Excel 다운로드"/"CSV 다운로드"(현재 필터 전체를 당기·전기 wide 포맷으로 받던 버튼)는
 * 2026-08-03에 제거했다. "필터 전체 선택"이 생겨 체크 → 선택 항목 다운로드로 같은 일을
 * 할 수 있게 됐고, 전기 금액이 필요 없다는 사용자 확인으로 wide 포맷을 따로 남길 이유도
 * 없어졌기 때문이다(백엔드의 `ids` 없는 export 경로 자체는 그대로 살아 있다).
 *
 * `viewerOnly`는 뷰어 전용 배포 빌드(App.tsx VIEWER_JOB_ID)에서 true로 내려와 다운로드
 * 관련 UI(선택 체크박스 열, 선택 표시줄, 선택 항목 다운로드/보고서 생성)를 통째로
 * 숨긴다 — 조회·필터·정렬·상세보기는 그대로 동작한다. */
function FinancialsResultsView({ jobId, viewerOnly = false }: { jobId: number; viewerOnly?: boolean }) {
  const [tab, setTab] = useState<FilterTab>('ALL')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<ResultListResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [visibleKeys, setVisibleKeys] = useState<Set<keyof ResultResponse>>(
    new Set(DEFAULT_VISIBLE_KEYS),
  )
  const [selected, setSelected] = useState<ResultResponse | null>(null)
  // §4-11 다중 선택 다운로드 — 체크한 결과 id 집합. **페이지를 넘기거나 필터 탭을
  // 바꿔도 유지**하고(화면 탐색 수단일 뿐이므로), Job이 바뀔 때만 초기화한다.
  const [selectedIds, setSelectedIds] = useState<Set<number>>(() => new Set())
  const [selectionExporting, setSelectionExporting] = useState(false)
  // "현재 필터 전체 선택"(2026-08-03) 진행 중 표시 — 1,000건대에서도 수백 ms지만
  // 서버 왕복이라 버튼에 로딩을 건다.
  const [selectingAll, setSelectingAll] = useState(false)
  // §4-12 선택 항목 보고서 생성 — 선택 상태(`selectedIds`)는 다운로드와 그대로
  // 공유하고, 진행 중 표시와 결과 안내만 별도로 둔다.
  const [reportGenerating, setReportGenerating] = useState(false)
  const [reportResult, setReportResult] = useState<GenerateReportResponse | null>(null)
  // §4-12-A 생성 **전** 확인 모달(2026-08-03) — "선택 항목 보고서 생성"은 클릭 한 번으로
  // 수천 개 HTML을 로컬 폴더에 만들 수 있고, 선택이 탭을 넘나들며 합집합으로 누적돼
  // 조건에 안 맞는 회사가 섞이기 쉬워 무엇이 만들어지는지 먼저 되묻는다.
  const [reportConfirmOpen, setReportConfirmOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  // 다중 컬럼 정렬 — 배열 앞쪽이 1순위(주 정렬), 뒤쪽이 보조 정렬이다. 일반 클릭은
  // 단일 정렬로 초기화하고, Shift+클릭은 기존 기준에 이 컬럼을 보조 정렬로 추가/토글한다.
  const [sorts, setSorts] = useState<{ key: string; dir: SortDir }[]>([])
  // "휴면·폐업 추정"으로 기본 숨김 처리된 건수 — 무통보로 사라지지 않도록 탭
  // 뱃지와 "총 N건" 옆 안내 문구로 항상 고지한다(2026-07-22 디자인 리뷰 반영).
  const [staleCount, setStaleCount] = useState<number | null>(null)
  // 매출액/총자산 조건으로 기본 숨김 처리된 건수 — 위와 완전히 같은 이유·같은
  // 방식이다(2026-07-28). 각 탭이 실제로 쓰는 조건(tabToParams)을 그대로 재사용해
  // "N건 숨김"과 그 탭을 열었을 때의 "총 N건"이 어긋나지 않게 한다.
  const [excludedRevenueCount, setExcludedRevenueCount] = useState<number | null>(null)
  const [excludedAssetsCount, setExcludedAssetsCount] = useState<number | null>(null)

  useEffect(() => {
    listResults(jobId, { page: 1, page_size: 1, ...tabToParams('STALE_DISCLOSURE') })
      .then((res) => setStaleCount(res.total))
      .catch(() => setStaleCount(null))
    listResults(jobId, { page: 1, page_size: 1, ...tabToParams('EXCLUDED_REVENUE') })
      .then((res) => setExcludedRevenueCount(res.total))
      .catch(() => setExcludedRevenueCount(null))
    listResults(jobId, { page: 1, page_size: 1, ...tabToParams('EXCLUDED_ASSETS') })
      .then((res) => setExcludedAssetsCount(res.total))
      .catch(() => setExcludedAssetsCount(null))
  }, [jobId])

  // 다른 Job의 결과 id가 선택에 남아 있으면 백엔드가 400(스코프 검증)을 준다.
  useEffect(() => {
    setSelectedIds(new Set())
  }, [jobId])

  // 타이핑 중에 매 글자마다 요청하지 않도록 입력을 디바운스한다.
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search.trim())
      setPage(1)
    }, SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [search])

  // 현재 탭이 실제로 거는 필터. "N건 숨김" 안내를 탭 이름으로 하드코딩하지 않고
  // 이 값에서 파생시켜(그 탭이 정말 그 조건을 false로 걸 때만 고지) 어긋남을 막는다.
  const activeParams = useMemo(() => tabToParams(tab), [tab])

  // 목록 조회와 다운로드가 공유하는 필터/정렬 조건. 매 렌더마다 새 객체가 되면
  // 아래 useEffect가 무한 루프를 도므로 값이 바뀔 때만 다시 만든다.
  const query = useMemo(
    () => ({
      ...activeParams,
      q: debouncedSearch || undefined,
      // 다중 정렬은 콤마 구분 `field:dir` 목록으로 백엔드에 전달한다(서버 사이드 정렬).
      sort: sorts.length ? sorts.map((s) => `${s.key}:${s.dir}`).join(',') : undefined,
    }),
    [activeParams, debouncedSearch, sorts],
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

  /** §4-11 선택 항목 다운로드 — 체크한 회사만 내보낸다. 필터/정렬은 넘기지 않는다
   * (백엔드도 `ids`가 있으면 무시한다 — 이미 화면에서 골라 놓은 것이라 다운로드
   * 시점에 필터를 다시 태우면 "체크했는데 파일에 없다"가 된다).
   * `includeHistory`는 xlsx에서만 호출되게 메뉴 구성 자체로 차단해 둔다. */
  async function handleSelectionExport(format: 'xlsx' | 'csv', includeHistory: boolean) {
    const ids = [...selectedIds].sort((a, b) => a - b)
    if (ids.length === 0) return
    setSelectionExporting(true)
    try {
      await exportResults(jobId, format, {}, { ids, includeHistory })
    } catch (err) {
      // 구버전 백엔드가 `ids`를 무시하고 전체 결과를 내려준 경우는 "실패"와 다르게
      // 안내한다 — 파일이 받아지긴 하지만 체크하지 않은 회사까지 들어 있어, 그대로
      // 저장하면 잘못된 자료를 쓰게 된다(2026-07-28).
      if (err instanceof SelectionExportUnsupportedError) {
        // 고정 id로 띄워 재시도 때마다 같은 알림이 겹겹이 쌓이지 않게 한다
        // (autoClose: false라 수동으로 닫아야 하고, 목록 하단 페이지네이션을 가린다).
        notifications.show({
          id: 'selection-export-unsupported',
          color: 'red',
          title: '선택 항목 다운로드를 중단했습니다',
          message:
            '서버가 선택 항목만 내보내는 기능을 지원하지 않는 구버전이라, 체크하지 않은 회사까지 들어간 전체 파일이 돌아왔습니다. 받은 파일은 저장하지 않았습니다. 프로그램을 껐다 다시 켜 보고, 같은 안내가 계속 나오면 최신 버전 프로그램으로 교체가 필요합니다(담당자에게 문의하세요).',
          autoClose: false,
        })
      } else if (err instanceof SelectionExportUnverifiableError) {
        notifications.show({
          id: 'selection-export-unverifiable',
          color: 'red',
          title: '선택 항목 다운로드를 중단했습니다',
          message:
            '다운로드 응답 정보를 읽지 못해, 받은 파일이 체크한 회사만 담고 있는지 확인할 수 없었습니다. 받은 파일은 저장하지 않았습니다. 프로그램을 껐다 다시 켠 뒤 다시 시도하고, 같은 안내가 계속 나오면 담당자에게 문의하세요.',
          autoClose: false,
        })
      } else {
        notifications.show({ color: 'red', message: '선택 항목 다운로드에 실패했습니다.' })
      }
    } finally {
      setSelectionExporting(false)
    }
  }

  /** §4-12 선택 항목 보고서 생성 — 체크한 회사마다 감사 수임 제안서 HTML을 만들어
   * **서버(=이 프로그램이 돌고 있는 PC)의 로컬 폴더**에 저장한다. 브라우저로 파일이
   * 내려오지 않으므로, 성공하면 저장 폴더 경로를 복사할 수 있게 모달로 안내한다.
   * 선택 대상은 다운로드와 똑같이 `selectedIds` 전체다(현재 필터·탭과 무관). */
  async function handleGenerateReport() {
    const ids = [...selectedIds].sort((a, b) => a - b)
    if (ids.length === 0) return
    setReportGenerating(true)
    try {
      const result = await generateReport(jobId, ids)
      // 경고(warnings)가 있어도 실패가 아니다 — 파일은 만들어졌고 generated_count에
      // 포함된다. 반면 재무 이력이 없는 회사는 skipped로 빠져 **파일이 아예 생성되지
      // 않으므로**, 선택 전원이 스킵되면 generated_count가 0이 될 수 있다. 이때
      // 초록 토스트를 띄우면 "0건 생성 완료"라는 모순된 안내가 된다.
      const nothingGenerated = result.generated_count === 0
      notifications.show({
        id: 'report-generated',
        color: nothingGenerated ? 'yellow' : 'green',
        message: nothingGenerated
          ? '생성된 보고서가 없습니다 — 선택한 회사가 모두 제외되었습니다.'
          : `보고서 ${result.generated_count.toLocaleString()}건 생성 완료`,
      })
      setReportResult(result)
    } catch (err) {
      // 400(다른 Job의 id 혼입)·404(Job 없음)·507(저장 실패/템플릿 부재)은 백엔드가
      // 이미 사용자용 한국어 문장을 detail로 주므로 그대로 노출한다.
      notifications.show({
        color: 'red',
        title: '보고서 생성에 실패했습니다',
        message: extractApiErrorMessage(
          err,
          '보고서를 생성하지 못했습니다. 프로그램이 실행 중인지 확인한 뒤 다시 시도하세요.',
        ),
        autoClose: false,
      })
    } finally {
      setReportGenerating(false)
    }
  }

  /** 확인 모달(§4-12-A)의 "N건 생성" — 위 생성 로직을 **그대로** 실행하고 끝나면 모달을
   * 닫는다. 생성 중에는 모달이 열린 채 버튼만 로딩 상태가 돼 중복 클릭을 막는다
   * (실패해도 닫는다 — 원인은 autoClose:false 알림으로 남는다). */
  async function handleConfirmGenerateReport() {
    await handleGenerateReport()
    setReportConfirmOpen(false)
  }

  function toggleRowSelected(id: number, checked: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })
  }

  /** "현재 필터 전체 선택"(2026-08-03) — 지금 탭/검색/정렬 조건을 통과하는 결과를
   * **페이지를 넘기지 않고 한 번에** 선택한다.
   *
   * 헤더 체크박스는 보이는 50건만 잡아서 1,200건대 Job은 25번 페이지를 넘겨야 했다.
   * 목록 API에 `ids_only=true`만 얹어 id 배열을 받아온다(전체 행을 다시 받지 않는다).
   *
   * 기존 선택은 **지우지 않고 합집합**으로 더한다 — 다른 탭에서 골라 둔 회사가
   * 소리 없이 빠지면 다운로드/보고서 결과가 달라지기 때문이다. */
  async function handleSelectAllInFilter() {
    setSelectingAll(true)
    try {
      const ids = await listAllResultIds(jobId, query)
      setSelectedIds((prev) => new Set([...prev, ...ids]))
    } catch (err) {
      if (err instanceof SelectAllUnsupportedError) {
        // 구버전 서버는 `ids_only`를 무시하고 1페이지만 준다 — 그걸 전체로 착각해
        // 선택하면 "전체 선택했는데 50건만 담긴 파일"이 된다. 선택은 손대지 않는다.
        notifications.show({
          id: 'select-all-unsupported',
          color: 'red',
          title: '전체 선택을 적용하지 않았습니다',
          message:
            '서버가 필터 전체 선택을 지원하지 않는 구버전이라, 일부만 선택되는 것을 막기 위해 중단했습니다. 프로그램을 껐다 다시 켜 보고, 같은 안내가 계속 나오면 최신 버전 프로그램으로 교체가 필요합니다(담당자에게 문의하세요).',
          autoClose: false,
        })
      } else {
        notifications.show({ color: 'red', message: '전체 선택에 실패했습니다.' })
      }
    } finally {
      setSelectingAll(false)
    }
  }

  /** 헤더 체크박스 — **현재 페이지에 보이는 행만** 전체선택/해제한다
   * (다른 페이지에서 이미 고른 선택은 건드리지 않는다). */
  function togglePageSelected(checked: boolean) {
    const pageIds = data?.items.map((row) => row.id) ?? []
    setSelectedIds((prev) => {
      const next = new Set(prev)
      for (const id of pageIds) {
        if (checked) next.add(id)
        else next.delete(id)
      }
      return next
    })
  }

  // "휴면·폐업 추정" 탭에서는 판정 근거(최근 공시일자)를 항상 볼 수 있어야
  // 하므로 컬럼 토글 상태와 무관하게 표시한다(2026-07-22). ColumnToggle에도
  // 같은 집합을 넘겨 체크박스 상태가 실제 표시 상태와 어긋나지 않게 한다.
  //
  // 같은 이유로 검수 탭("파싱 실패"/"부분 성공")에서는 **파싱비고**를 항상 표시한다
  // (2026-08-03) — 그 탭에 있는 행은 전부 "왜 실패/부분인가"를 봐야 하는 행이고,
  // 상단 wide 다운로드 버튼을 없앤 뒤로 그 사유를 화면에서 볼 경로가 개별 회사
  // 상세 Drawer 하나뿐이었다. 다른 탭에서는 컬럼 토글로 켤 수 있다.
  const forcedVisibleKeys = useMemo<Set<keyof ResultResponse>>(() => {
    if (tab === 'STALE_DISCLOSURE') return new Set(['latest_disclosure_date'])
    if (tab === 'FAILED' || tab === 'PARTIAL') return new Set(['parse_note'])
    return new Set()
  }, [tab])
  // 확인 모달(§4-12-A)에 넘길 선택 id — 매 렌더 새 배열이면 모달이 요약 조회를 반복하므로
  // 선택이 실제로 바뀔 때만 다시 만든다(정렬 기준은 handleGenerateReport와 동일).
  const selectedIdList = useMemo(() => [...selectedIds].sort((a, b) => a - b), [selectedIds])
  const visibleColumns = ALL_COLUMNS.filter(
    (c) => visibleKeys.has(c.key) || forcedVisibleKeys.has(c.key),
  )
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
  const pageRows = data?.items ?? []
  const selectedOnPage = pageRows.filter((row) => selectedIds.has(row.id)).length
  const allPageSelected = pageRows.length > 0 && selectedOnPage === pageRows.length
  const hasSelection = selectedIds.size > 0
  // "현재 필터 전체 선택"은 지금 조건으로 잡히는 결과가 있을 때만 의미가 있다.
  const canSelectAll = !!data && data.total > 0

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
            <Tabs.Tab
              value="EXCLUDED_REVENUE"
              rightSection={
                excludedRevenueCount !== null ? (
                  <Badge size="xs" variant="light" color="gray">
                    {excludedRevenueCount}
                  </Badge>
                ) : undefined
              }
            >
              매출액 제외 건
            </Tabs.Tab>
            <Tabs.Tab
              value="EXCLUDED_ASSETS"
              rightSection={
                excludedAssetsCount !== null ? (
                  <Badge size="xs" variant="light" color="gray">
                    {excludedAssetsCount}
                  </Badge>
                ) : undefined
              }
            >
              총자산 제외 건
            </Tabs.Tab>
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
          {/* 다운로드 버튼은 여기에 두지 않는다 — 목록에서 대상을 고른 뒤
              아래 선택 표시줄에서 내려받는 경로 하나로 통일했다(2026-08-03). */}
          <ColumnToggle
            allColumns={ALL_COLUMNS}
            visibleKeys={visibleKeys}
            onToggle={toggleColumn}
            forcedVisibleKeys={forcedVisibleKeys}
          />
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

      {(tab === 'EXCLUDED_REVENUE' || tab === 'EXCLUDED_ASSETS') && (
        <Alert color="gray">
          감사보고서 원문에서 확인한 {tab === 'EXCLUDED_REVENUE' ? '매출액' : '총자산'}이 검색
          조건 범위를 벗어나 <b>제외된 회사</b>입니다 — 조건에 맞지 않는 회사이므로
          <b> "휴면·폐업 추정" 탭을 제외한 모든 탭(전체 포함)에서는 기본적으로 숨겨져
          있습니다</b>(휴면·폐업 추정 탭은 공시 여부만으로 걸러 이 회사들도 함께 보입니다).
          매출액·총자산 조건에 모두 걸린 회사는 두 탭 양쪽에 나옵니다.
        </Alert>
      )}

      {tab === 'STALE_DISCLOSURE' && (
        <Alert color="yellow">
          최근 1년 이내 DART 공시가 없는 회사입니다 — 폐업·휴면 상태일 가능성이 있어
          <b> 다른 모든 탭(전체 포함)에서는 기본적으로 숨겨져 있습니다</b>. 실제 영업
          여부는 이 목록만으로 단정할 수 없으니 필요 시 직접 확인하세요.
        </Alert>
      )}

      {/* §4-11 선택 항목 다운로드 + §4-12 보고서 생성 표시줄 — 두 기능은 같은 선택
          (selectedIds)을 쓰므로 활성 조건도 같다. 페이지 로딩 중에도 선택은 유지되므로
          테이블 바깥(로딩 조건 밖)에 둔다.

          **선택 0건이어도 숨기지 않는다**(2026-08-03): 상단 "Excel/CSV 다운로드" 버튼을
          없앤 뒤로 이 표시줄이 화면의 유일한 다운로드 입구라, 아무것도 고르지 않은 상태에서
          숨어 버리면 "내려받는 곳이 없다"가 된다. 대신 버튼을 비활성으로 두고 무엇을 해야
          하는지(체크 또는 "현재 필터 전체 선택") 그 자리에서 안내한다.

          뷰어 전용 빌드는 다운로드 자체가 목적이 없어 체크박스 열(아래)을 렌더링하지 않으므로
          이 표시줄도 통째로 숨긴다. */}
      {!viewerOnly && (
        <Paper
          withBorder
          p="xs"
          bg={hasSelection ? 'var(--mantine-color-blue-0)' : undefined}
        >
          <Group justify="space-between">
            <Group gap="xs">
              {hasSelection ? (
                <>
                  <Text size="sm" fw={600}>
                    선택 {selectedIds.size.toLocaleString()}건
                    {selectedIds.size > selectedOnPage && (
                      <Text component="span" size="xs" c="dimmed" fw={400}>
                        {' '}
                        (이 페이지 {selectedOnPage}건 · 현재 화면 밖{' '}
                        {(selectedIds.size - selectedOnPage).toLocaleString()}건)
                      </Text>
                    )}
                  </Text>
                  <Button
                    size="compact-xs"
                    variant="subtle"
                    onClick={() => setSelectedIds(new Set())}
                  >
                    선택 해제
                  </Button>
                </>
              ) : (
                <Text size="sm" c="dimmed">
                  왼쪽 체크박스로 회사를 고르면 그 회사만 내려받거나 보고서를 만들 수 있습니다.
                </Text>
              )}
              {/* "현재 필터 전체 선택"(2026-08-03) — 헤더 체크박스는 보이는 50건만 잡아
                  1,000건대 결과를 다 고르려면 페이지를 수십 번 넘겨야 했다. 지금 탭·검색·정렬
                  조건 그대로 서버에 id만 물어 한 번에 선택한다(기존 선택은 유지·합집합). */}
              {canSelectAll && (
                <Button
                  size="compact-xs"
                  variant={hasSelection ? 'subtle' : 'light'}
                  loading={selectingAll}
                  onClick={handleSelectAllInFilter}
                >
                  현재 필터 전체 선택 (총 {data!.total.toLocaleString()}건)
                </Button>
              )}
              {selectedIds.size > LARGE_SELECTION_HINT && (
                <Text size="xs" c="dimmed">
                  선택이 많아 파일 생성에 시간이 걸릴 수 있습니다.
                </Text>
              )}
            </Group>
            <Group gap="xs">
              <Menu position="bottom-end" withinPortal disabled={!hasSelection}>
                <Menu.Target>
                  <Button loading={selectionExporting} disabled={!hasSelection}>
                    선택 항목 다운로드
                  </Button>
                </Menu.Target>
                <Menu.Dropdown>
                  <Menu.Label>
                    선택한 {selectedIds.size.toLocaleString()}건만 내보내기 — 현재 필터·탭과
                    무관하게 지금까지 체크한 전체 건이 대상입니다
                  </Menu.Label>
                  <Menu.Item onClick={() => handleSelectionExport('xlsx', true)}>
                    Excel (기본정보 + 재무이력)
                  </Menu.Item>
                  <Menu.Item onClick={() => handleSelectionExport('xlsx', false)}>
                    Excel (기본정보만)
                  </Menu.Item>
                  {/* CSV는 시트가 하나뿐이라 재무이력을 담을 수 없다 — 조합 자체를 제공하지 않는다. */}
                  <Menu.Item onClick={() => handleSelectionExport('csv', false)}>
                    CSV (기본정보만)
                  </Menu.Item>
                  <Menu.Label>
                    재무 이력이 수집된 회사만 재무이력 시트에 실립니다(감사보고서 없음 등은
                    제외)
                  </Menu.Label>
                </Menu.Dropdown>
              </Menu>
              {/* §4-12 — 다운로드와 같은 선택(selectedIds)을 쓰지만 결과물이 다르다:
                  브라우저로 파일이 내려오는 게 아니라 이 PC의 로컬 폴더에 회사별 제안서
                  HTML이 저장된다. 클릭하면 바로 만들지 않고 **확인 모달**(§4-12-A)을
                  먼저 열어 무엇이 만들어지는지(건수·위험 신호·저장 위치) 보여준다. */}
              <Tooltip
                multiline
                w={280}
                label="선택한 회사마다 감사 수임 제안서(HTML)를 만들어 이 PC의 폴더에 저장합니다. 브라우저로 내려받는 것이 아니라 저장된 폴더 경로를 알려드립니다. 생성 전에 확인 창이 먼저 열립니다."
              >
                <Button
                  variant="light"
                  loading={reportGenerating}
                  disabled={!hasSelection}
                  onClick={() => setReportConfirmOpen(true)}
                >
                  선택 항목 보고서 생성
                </Button>
              </Tooltip>
            </Group>
          </Group>
        </Paper>
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
            {activeParams.excluded_by_stale_disclosure === false && !!staleCount && (
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
            {/* 매출액/총자산 숨김 건수는 **전체 탭에서만** 알린다(2026-07-28 디자인
                리뷰). 이 건수는 Job 전역 기준인데, `excluded_by_revenue=1`인 행은
                정의상 원문을 열어 값을 파싱한 행이라 "파싱 실패"·"감사보고서 없음"
                같은 세부 탭에서는 실제로 숨겨지는 행이 사실상 없다 — 그런 검수
                화면에 이 숫자가 뜨면 "검수할 게 더 있나?"라는 오해만 만든다. */}
            {tab === 'ALL' && activeParams.excluded_by_revenue === false && !!excludedRevenueCount && (
              <> · 매출액 조건 제외 {excludedRevenueCount.toLocaleString()}건 숨김 (
              <UnstyledButton
                component="span"
                td="underline"
                onClick={() => handleTabChange('EXCLUDED_REVENUE')}
              >
                보기
              </UnstyledButton>
              )</>
            )}
            {tab === 'ALL' && activeParams.excluded_by_assets === false && !!excludedAssetsCount && (
              <> · 총자산 조건 제외 {excludedAssetsCount.toLocaleString()}건 숨김 (
              <UnstyledButton
                component="span"
                td="underline"
                onClick={() => handleTabChange('EXCLUDED_ASSETS')}
              >
                보기
              </UnstyledButton>
              )</>
            )}
            {/* 두 건수는 서로 배타적이지 않다 — 매출액·총자산 조건에 동시에 걸린
                회사가 양쪽에 들어간다. 백엔드에 OR 필터가 없어 정확한 합집합
                건수를 낼 수 없으므로, 숫자를 나란히 보여줄 때만 문구로 명시해
                "합이 안 맞는다"는 오해를 막는다(2026-07-28 디자인 리뷰). */}
            {tab === 'ALL' &&
              activeParams.excluded_by_revenue === false &&
              activeParams.excluded_by_assets === false &&
              !!excludedRevenueCount &&
              !!excludedAssetsCount && <> (두 조건에 모두 걸린 회사는 두 건수에 중복 계상됩니다)</>}
          </Text>
          <Table.ScrollContainer minWidth={800}>
            <Table striped highlightOnHover withTableBorder>
              <Table.Thead>
                <Table.Tr>
                  {/* §4-11 선택 열 — 헤더 체크박스는 현재 페이지 행만 전체선택/해제한다.
                      가로 스크롤 중에도 왼쪽에 고정되도록 result-select-header가 sticky를 건다.
                      뷰어 전용 빌드는 다운로드가 없어 선택 자체가 무의미하므로 열을 통째로 뺀다. */}
                  {!viewerOnly && (
                    <Table.Th w={40} className="result-select-header">
                      <Checkbox
                        size="sm"
                        aria-label="현재 페이지 전체 선택"
                        checked={allPageSelected}
                        indeterminate={selectedOnPage > 0 && !allPageSelected}
                        disabled={pageRows.length === 0}
                        onChange={(event) => togglePageSelected(event.currentTarget.checked)}
                      />
                    </Table.Th>
                  )}
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
                    className={selectedIds.has(row.id) ? 'result-row-selected' : undefined}
                    style={{ cursor: 'pointer' }}
                    onClick={() => setSelected(row)}
                  >
                    {/* 체크박스 클릭이 행 클릭(상세 Drawer 열기)으로 번지지 않게 막는다. */}
                    {!viewerOnly && (
                      <Table.Td className="result-select-cell" onClick={(event) => event.stopPropagation()}>
                        <Checkbox
                          size="sm"
                          aria-label={`${row.corp_name ?? `결과 #${row.id}`} 선택`}
                          checked={selectedIds.has(row.id)}
                          onChange={(event) =>
                            toggleRowSelected(row.id, event.currentTarget.checked)
                          }
                        />
                      </Table.Td>
                    )}
                    {visibleColumns.map((col) => (
                      <Table.Td key={col.key}>
                        {col.key === 'auditor_changed' ? (
                          <AuditorChangedCell
                            value={row.auditor_changed}
                            label={formatCell(col, row)}
                          />
                        ) : col.key === 'parse_note' ? (
                          <ParseNoteCell text={formatCell(col, row)} />
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

      {/* 생성 **전** 확인(§4-12-A)과 생성 **후** 결과 안내(§4-12)는 별개 모달이다 —
          확인 모달은 생성이 끝나면 스스로 닫히고, 그 직후 결과 모달이 열린다. */}
      <ReportConfirmModal
        opened={reportConfirmOpen}
        jobId={jobId}
        ids={selectedIdList}
        generating={reportGenerating}
        largeSelectionThreshold={LARGE_SELECTION_HINT}
        onCancel={() => setReportConfirmOpen(false)}
        onConfirm={handleConfirmGenerateReport}
      />

      <ReportResultModal result={reportResult} onClose={() => setReportResult(null)} />
    </Stack>
  )
}

export default function ResultPage({ viewerOnly = false }: { viewerOnly?: boolean }) {
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
      {job && job.phase === 'FINANCIALS' && (
        <FinancialsResultsView jobId={jobId} viewerOnly={viewerOnly} />
      )}
    </Stack>
  )
}
