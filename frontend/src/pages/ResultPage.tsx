import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  Alert,
  Badge,
  Box,
  Button,
  Checkbox,
  CloseButton,
  Group,
  Loader,
  LoadingOverlay,
  Menu,
  Pagination,
  Paper,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
  UnstyledButton,
} from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
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
import {
  ResultColumnFilterControl,
  StaleDisclosureFilterButton,
} from '../components/ResultColumnFilters'
import {
  DEFAULT_RESULT_COLUMN_FILTERS,
  describeActiveFilters,
  describeInvertedRanges,
  hasNonDefaultFilters,
  isAssetsFilterActive,
  isRevenueFilterActive,
  resultFiltersToParams,
} from '../util/resultFilters'
import type { ResultColumnFilterState } from '../util/resultFilters'

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

/** phase='FINANCIALS'(Phase 2 완료/진행) Job의 "확정 결과" 뷰 — 결과 테이블/컬럼
 * 헤더 필터/컬럼 토글/상세 Drawer/다운로드.
 *
 * **필터 탭(FilterTab)은 2026-08-05(§4-13-C)에 폐지됐다** — 전체/파싱 성공/부분 성공/
 * 파싱 실패/감사보고서 없음/매출액 제외/총자산 제외/감사인 변동/휴면·폐업 추정 9개 탭과
 * "…N건 숨김 (보기)" 안내 문구를 **엑셀식 컬럼 헤더 필터**로 옮겼다. 탭이 담고 있던
 * 업무 규칙(휴면·폐업 추정 기본 숨김, 검수 필요/감사보고서 없음 구분)은 새 필터의
 * **초기 선택값**으로 그대로 이식했다(`DEFAULT_RESULT_COLUMN_FILTERS`).
 * 매출액/총자산은 Job 생성 조건과의 일치 여부(`excluded_by_revenue`/`_assets`)가 아니라
 * **실측 파싱값**(`revenue_cur`/`total_assets_cur`) 범위로 거른다 — 프리셋 버튼도 두지
 * 않는다(사용자 확정).
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
  // §4-13-C 컬럼 헤더 필터 상태(구 FilterTab 대체). 여러 컬럼 필터는 전부 AND로
  // 결합되고, 백엔드도 그렇게 처리한다.
  const [filters, setFilters] = useState<ResultColumnFilterState>(DEFAULT_RESULT_COLUMN_FILTERS)
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

  // "…N건 숨김 (보기)" 안내를 위해 Job 전역 건수를 미리 세던 조회 3건은 §4-13-C로
  // 함께 사라졌다 — 지금은 헤더 아이콘 강조와 상단 "적용 중인 필터" 요약이 그 역할을
  // 대신하고, 무엇이 걸려 있는지는 필터 자체가 곧 답이다.

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

  // 금액 범위는 숫자를 한 글자씩 치는 동안 값이 계속 바뀌므로(50 → 5 → '') 조회를
  // 디바운스한다 — 검색어와 같은 이유·같은 지연이다. 헤더 아이콘 강조와 입력값 자체는
  // 디바운스 전 `filters`를 그대로 써서 타이핑이 즉시 반영된다.
  const [debouncedFilters] = useDebouncedValue(filters, SEARCH_DEBOUNCE_MS)
  // 컬럼 헤더 필터 → 쿼리 파라미터(§4-13-B 계약). 억원 입력의 원 단위 환산,
  // "전부 체크면 파라미터 생략" 같은 규칙은 전부 이 함수 한 곳에 있다.
  const columnParams = useMemo(() => resultFiltersToParams(debouncedFilters), [debouncedFilters])

  // 목록 조회와 "현재 필터 전체 선택"이 공유하는 필터/정렬 조건. 매 렌더마다 새 객체가
  // 되면 아래 useEffect가 무한 루프를 도므로 값이 바뀔 때만 다시 만든다.
  const query = useMemo(
    () => ({
      ...columnParams,
      q: debouncedSearch || undefined,
      // 다중 정렬은 콤마 구분 `field:dir` 목록으로 백엔드에 전달한다(서버 사이드 정렬).
      sort: sorts.length ? sorts.map((s) => `${s.key}:${s.dir}`).join(',') : undefined,
    }),
    [columnParams, debouncedSearch, sorts],
  )

  useEffect(() => {
    setLoading(true)
    setError(null)
    listResults(jobId, { page, page_size: PAGE_SIZE, ...query })
      .then(setData)
      .catch(() => setError('결과를 불러오지 못했습니다. 백엔드 서버가 실행 중인지 확인하세요.'))
      .finally(() => setLoading(false))
  }, [jobId, page, query])

  /** 컬럼 필터 변경 — 필터가 바뀌면 결과 집합 자체가 바뀌므로 항상 1페이지로 돌아간다
   * (3페이지를 보던 중 범위를 좁혀 결과가 1페이지뿐이면 빈 화면이 된다).
   * **선택(selectedIds)은 건드리지 않는다** — 필터는 화면 탐색 수단일 뿐이고, 다른
   * 조건에서 골라 둔 회사가 조용히 빠지면 다운로드/보고서 결과가 달라진다. */
  function updateFilters(patch: Partial<ResultColumnFilterState>) {
    setFilters((prev) => ({ ...prev, ...patch }))
    setPage(1)
  }

  function clearAllFilters() {
    setFilters(DEFAULT_RESULT_COLUMN_FILTERS)
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

  // 판정 근거 컬럼 강제 표시 — 탭이 하던 일을 **필터 상태**로 옮긴 것이다(§4-13-C).
  //
  // - 휴면·폐업 추정을 목록에 포함시켰다면 판정 근거인 "최근 공시일자"를 항상 보여준다
  //   (2026-07-22의 취지 그대로).
  // - 파싱상태를 검수 대상(부분 성공/검수 필요)으로만 좁혀 봤다면 **파싱비고**를 항상
  //   보여준다(2026-08-03) — 그 목록의 모든 행이 "왜 실패/부분인가"를 봐야 하는 행이고,
  //   상단 wide 다운로드를 없앤 뒤로 그 사유를 볼 경로가 상세 Drawer 하나뿐이었다.
  //   파싱 성공까지 함께 보는 상태에서는 강제하지 않는다(대부분 빈 값이라 자리만 차지).
  // ColumnToggle에도 같은 집합을 넘겨 체크박스 상태가 실제 표시 상태와 어긋나지 않게 한다.
  const forcedVisibleKeys = useMemo<Set<keyof ResultResponse>>(() => {
    const keys = new Set<keyof ResultResponse>()
    if (filters.stale.includes('STALE')) keys.add('latest_disclosure_date')
    const reviewOnly =
      filters.parseStatus.length > 0 &&
      filters.parseStatus.every((v) => v === 'PARTIAL' || v === 'FAILED_REVIEW')
    if (reviewOnly) keys.add('parse_note')
    return keys
  }, [filters])
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
  // 상단 요약용 — 필터가 걸린 컬럼을 컬럼 토글로 숨겨 두면 헤더 아이콘 강조가 보이지
  // 않으므로, 무엇이 걸려 있는지 한 줄로도 알려 준다(§4-13-C의 "총 N건 + 모든 필터
  // 지우기" 최소 구성에 이 한 줄만 더한 것이다).
  const activeFilterLabels = describeActiveFilters(filters)
  const filtersDirty = hasNonDefaultFilters(filters)
  const amountFilterActive = isRevenueFilterActive(filters) || isAssetsFilterActive(filters)
  // 최소>최대로 뒤집힌 금액 범위 — 그대로 적용하면 "값 있는 회사 전멸 + NULL 회사만"
  // 목록이 정상 결과처럼 보이고, 거기서 "현재 필터 전체 선택" → 보고서 생성으로 이어질
  // 수 있다. 범위는 적용하지 않고(util) 화면에서 크게 알린다.
  const invertedRanges = describeInvertedRanges(filters)

  return (
    <Stack>
      {/* 필터 탭은 폐지됐다(§4-13-C) — 상단에는 검색창, 전용 컬럼이 없는
          "휴면·폐업 추정" 필터, 컬럼 토글만 둔다. 나머지 필터는 전부 컬럼 헤더에 있다. */}
      <Group justify="space-between">
        <Group gap="xs">
          <StaleDisclosureFilterButton filters={filters} onChange={updateFilters} />
          {filtersDirty && (
            <Tooltip
              multiline
              w={260}
              label="컬럼 헤더에 걸어 둔 모든 필터를 화면 최초 상태로 되돌립니다(휴면·폐업 추정은 기본값인 '제외'로 돌아갑니다). 검색어와 정렬은 그대로 둡니다."
            >
              <Button variant="subtle" color="gray" onClick={clearAllFilters}>
                모든 필터 지우기
              </Button>
            </Tooltip>
          )}
        </Group>

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

      {/* 뒤집힌 금액 범위 경고 — 팝오버 안의 error 표시는 팝오버를 닫으면 보이지 않으므로,
          목록이 "왜 이런 회사만 남았지"로 읽히지 않게 화면 상단에도 남긴다. */}
      {invertedRanges.length > 0 && (
        <Alert color="red" title="금액 범위 입력을 확인하세요">
          {invertedRanges.join('·')} 필터의 <b>최소값이 최대값보다 큽니다</b> — 그 범위는{' '}
          <b>적용하지 않았습니다</b>. 해당 컬럼 헤더의 필터 아이콘을 열어 값을 고치세요. 입력
          단위는 <b>억원</b>입니다(표에 보이는 원 단위 금액을 그대로 옮겨 적으면 이 상태가
          됩니다).
        </Alert>
      )}

      {/* 탭별 안내 Alert 4종은 §4-13-C로 사라졌다 — 각 설명은 해당 컬럼 필터
          팝오버의 항목 설명(hint)으로 옮겨 필요한 자리에서만 보이게 했다.
          다만 "휴면·폐업 추정"을 목록에 **포함**시키는 것은 목록 전체의 의미를
          바꾸는 선택이라, 그때만 화면에 한 줄로 남긴다. */}
      {filters.stale.includes('STALE') && (
        <Alert color="yellow">
          최근 1년 이내 DART 공시가 없는 회사(<b>휴면·폐업 추정</b>)가 목록에 포함돼 있습니다 —
          폐업·휴면 상태일 가능성이 있어 기본적으로는 숨겨 둡니다. 실제 영업 여부는 이 목록만으로
          단정할 수 없으니 필요 시 직접 확인하세요.
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
                  1,000건대 결과를 다 고르려면 페이지를 수십 번 넘겨야 했다. 지금 필터·검색·정렬
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
                    선택한 {selectedIds.size.toLocaleString()}건만 내보내기 — 지금 걸려 있는
                    필터와 무관하게 지금까지 체크한 전체 건이 대상입니다
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

      {/* 첫 조회 때만 통짜 로더를 쓴다. **이미 목록이 있는 상태에서는 테이블을 절대
          언마운트하지 않는다**(§4-13-C) — 컬럼 헤더의 필터 팝오버가 테이블 안에 있어서,
          로딩마다 테이블을 걷어내면 최소값을 입력하는 순간 팝오버가 닫혀 최대값을 이어서
          칠 수 없다(실측 재현). 대신 반투명 오버레이만 덮는다. */}
      {loading && !data && <Loader />}

      {data && (
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
            {/* "…N건 숨김 (보기)" 안내 3종은 §4-13-C로 삭제됐다 — 무엇이 걸려 있는지는
                헤더 아이콘 강조와 아래 요약이 대신한다. 다만 필터가 걸린 컬럼을 숨겨 둔
                경우를 대비해 적용 중인 필터를 한 줄로 적어 준다. */}
            {activeFilterLabels.length > 0 && (
              <> · 필터 {activeFilterLabels.join(' / ')}</>
            )}
          </Text>
          {/* 금액 범위 필터의 NULL 통과 규칙(§4-13-B)은 "범위를 걸었는데 왜 빈 값이
              보이지"라는 오해를 부르므로, 범위가 실제로 걸려 있을 때만 한 줄로 알린다. */}
          {amountFilterActive && (
            <Text size="xs" c="dimmed">
              매출액·총자산 범위를 걸어도 <b>그 값을 얻지 못한 회사(파싱 실패·감사보고서 없음
              등)는 목록에 계속 표시됩니다</b> — 검수해야 할 회사가 범위 입력만으로 조용히
              사라지지 않게 한 것입니다.
            </Text>
          )}
          <Box pos="relative">
            <LoadingOverlay visible={loading} zIndex={100} overlayProps={{ blur: 1 }} />
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
                          {/* 정렬(라벨 클릭)과 필터(깔때기 아이콘)는 **별개 컨트롤**이다 —
                              아이콘은 정렬 버튼 바깥에 두어 필터를 열려다 정렬이 바뀌는 일이
                              없게 한다(§4-13-C). */}
                          {/* 필터 아이콘이 옆에 붙으면서 `파싱상태`/`감사인변동` 같은
                              3~4글자 헤더가 한 글자씩 세로로 접혔다 — 라벨에 nowrap을
                              걸어 컬럼 폭이 라벨을 온전히 담게 한다. */}
                          <Group gap={2} wrap="nowrap">
                            {key === null ? (
                              <span style={{ whiteSpace: 'nowrap' }}>{col.label}</span>
                            ) : (
                              <UnstyledButton
                                onClick={(event) => handleSort(col, event.shiftKey)}
                                style={{ fontWeight: 'inherit', fontSize: 'inherit' }}
                                aria-label={`${col.label} 기준 정렬 (클릭 시 이 컬럼 단독 정렬로 초기화, Shift+클릭 시 보조 정렬 기준으로 추가)`}
                              >
                                <Group component="span" gap={4} wrap="nowrap" display="inline-flex">
                                  <span style={{ whiteSpace: 'nowrap' }}>{col.label}</span>
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
                            {/* jobId는 §4-14 값 목록 필터(업종/감사인/감사의견)가 팝오버를
                                열 때 `distinct-values`를 조회하는 데 쓴다. */}
                            <ResultColumnFilterControl
                              jobId={jobId}
                              columnKey={col.key}
                              filters={filters}
                              onChange={updateFilters}
                            />
                          </Group>
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
          </Box>

          {data.items.length === 0 && !loading && (
            <Text c="dimmed">
              표시할 결과가 없습니다.
              {activeFilterLabels.length > 0 && ' 컬럼 헤더의 필터 조건을 넓혀 보세요.'}
            </Text>
          )}

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
