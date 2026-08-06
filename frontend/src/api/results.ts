import { apiClient } from './client'
import type {
  AccountDetailResponse,
  DistinctValueField,
  DistinctValuesResponse,
  DocumentSection,
  DocumentSectionResponse,
  ExportFormat,
  FinancialSnapshotResponse,
  GenerateReportResponse,
  ParseStatus,
  ResultListResponse,
  ResultResponse,
  SelectionSummaryResponse,
  SortDir,
} from '../types'

export interface ListResultsParams {
  page?: number
  page_size?: number
  parse_status?: ParseStatus
  excluded_by_revenue?: boolean
  excluded_by_assets?: boolean
  /** 최근 1년 이내 DART 공시 없음(폐업/휴면 추정) 건만(true)/제외한 나머지만(false).
   * 결과 화면은 "휴면·폐업 추정" 탭이 아닌 한 항상 false를 명시적으로 보내
   * 기본 숨김을 적용한다(2026-07-22). */
  excluded_by_stale_disclosure?: boolean
  /** 감사보고서 공시(rcept_no)를 찾은 건만(true)/못 찾은 건만(false).
   * parse_status='FAILED'와 함께 써서 "파싱 실패"와 "원문 없음"을 구분한다. */
  has_disclosure?: boolean
  /** 연도별 감사인이 바뀐 건만(true)/계속 같은 건만(false) (2026-07-26).
   * 판정 불가(auditor_changed IS NULL)인 건은 양쪽 모두에서 빠진다 —
   * excluded_by_* 와 동일한 tri-state 패턴이다.
   *
   * 결과 화면은 이 단일값 대신 아래 `auditor_changed_ext`(다중 선택)를 쓴다 —
   * 이 파라미터로는 "판정 불가만"·"변동 있음 + 판정 불가" 조합을 표현할 수 없다. */
  auditor_changed?: boolean

  // --- §4-13-B 결과화면 컬럼 필터(2026-08-05) ---------------------------------
  // 아래 6개는 기존 파라미터와 **AND**로 결합된다. 값 목록/의미는 백엔드
  // `app/api/results.py`의 `PARSE_STATUS_EXT_VALUES`/`_apply_amount_range` 참고.

  /** 파싱상태 4분류 다중 선택 — 콤마 구분(`OK`/`PARTIAL`/`FAILED_REVIEW`/`NO_DISCLOSURE`).
   * 네 값은 상호배타 + 전수 포괄이라 **4개를 다 주는 것은 파라미터 생략과 같다**
   * (그래서 프론트는 전부 선택이면 아예 보내지 않는다). 빈 문자열은 "아무 것도
   * 선택 안 함" = 0건이고, 목록에 없는 값은 400이다. */
  parse_status_ext?: string
  /** 감사인 변동 3분류 다중 선택 — 콤마 구분(`CHANGED`/`UNCHANGED`/`UNKNOWN`).
   * 규칙은 `parse_status_ext`와 동일하다. */
  auditor_changed_ext?: string
  /** **실측 파싱값** `revenue_cur`(원 단위 정수)의 하한/상한. 화면은 억원으로 입력받아
   * 환산해 보낸다. **값이 NULL인 회사는 범위를 걸어도 그대로 통과한다**(검수 대상이
   * 사라지지 않도록 한 백엔드 계약, §4-13-B). SQLite INTEGER 범위 밖이면 400. */
  revenue_min?: number
  revenue_max?: number
  /** 같은 방식의 `total_assets_cur`(실측 총자산) 범위. */
  assets_min?: number
  assets_max?: number

  // --- §4-14 결과화면 컬럼 필터 2차(2026-08-05) --------------------------------
  // 회사명/주소/업종/감사인/감사의견 5개 컬럼. 모든 컬럼이 **포함과 제외를 모두**
  // 지원하고, 기존 파라미터와는 전부 AND로 결합된다.

  /** 회사명 **부분일치**(대소문자 무시). LIKE 와일드카드는 백엔드가 리터럴로
   * 이스케이프한다. 공백뿐이면 필터 없음이고 200자 초과는 400.
   *
   * NULL/빈 값 행의 처리가 두 방향에서 다르다 — `_contains`(포함)에서는 빠지고,
   * `_not_contains`(제외)에서는 **항상 통과**한다(값이 없는 회사가 "○○ 제외"로
   * 조용히 사라지지 않게 한 백엔드 계약). */
  corp_name_contains?: string
  corp_name_not_contains?: string
  /** 같은 규칙의 주소 부분일치. */
  address_contains?: string
  address_not_contains?: string

  /** 업종명 **완전일치 다중 선택**(포함/제외). 콤마 구분이 아니라 **같은 파라미터를
   * 여러 번 반복**하는 형식이다(`?induty_name_in=A&induty_name_in=B`) — 업종명에
   * 쉼표가 실제로 흔해("직물, 편조원단 및 의복류 염색 가공업") 콤마 규약이면 값이
   * 조용히 쪼개진다. axios가 이 형식으로 직렬화하도록 `client.ts`에
   * `paramsSerializer: { indexes: null }`이 걸려 있다.
   *
   * 고를 값 목록은 `listDistinctValues()`가 준다. "값 없음"은 그 응답의
   * `blank_token`(`__BLANK__`)을 그대로 넣어 고른다.
   *
   * 빈 배열은 axios가 파라미터 자체를 만들지 않아 "필터 없음"이 된다 —
   * 백엔드 규약상 `?induty_name_in=`(빈 문자열 토큰)은 0건이지만, 화면은 그 상태를
   * 만들지 않는다(`resultFiltersToParams()`가 선택 0개면 아예 보내지 않는다). */
  induty_name_in?: string[]
  induty_name_not_in?: string[]
  /** 같은 규칙의 감사인명 목록 필터. 목록 셀은 "법인명(지역)"으로 합쳐 보이지만
   * 필터 대상은 `auditor_name` 하나뿐이다. */
  auditor_name_in?: string[]
  auditor_name_not_in?: string[]
  /** 같은 규칙의 감사의견 목록 필터(적정/한정/부적정/의견거절). */
  audit_opinion_in?: string[]
  audit_opinion_not_in?: string[]

  /** 회사명/주소/대표자/업종/감사인명 부분일치 검색. */
  q?: string
  /** 다중 컬럼 정렬 — 콤마 구분 `field:dir` 목록(예: `corp_name:asc,induty_name:desc`).
   * 앞쪽이 1순위, 뒤쪽이 보조 정렬이다. 백엔드 화이트리스트 밖 컬럼/형식 오류는 무시된다. */
  sort?: string
  /** 레거시 단일 정렬 컬럼과 방향 — `sort`가 있으면 백엔드가 무시한다(하위호환). */
  sort_by?: string
  sort_dir?: SortDir
  /** true면 페이징 없이 현재 필터를 통과한 `results.id` 전체만 받는 경량 응답
   * (`items`는 빈 배열, `ids`에 전체 id). "현재 필터 전체 선택"에서만 쓴다 —
   * 직접 호출하지 말고 아래 `listAllResultIds()`를 쓸 것(구버전 서버 판별 포함). */
  ids_only?: boolean
}

export async function listResults(
  jobId: number,
  params: ListResultsParams = {},
): Promise<ResultListResponse> {
  const { data } = await apiClient.get<ResultListResponse>(`/jobs/${jobId}/results`, {
    params,
  })
  return data
}

/** §4-14 값 목록 필터가 보여줄 **"지금 이 Job 데이터의 값 목록"**을 가져온다.
 *
 * **화면에 걸려 있는 다른 필터는 넘기지 않는다** — 백엔드도 Job 전체를 기준으로
 * 집계한다(다른 필터를 반영하면 지금 목록에서 빠져 있는 값이 선택지에서도 사라져
 * "안 보이는 값을 다시 켜기"가 불가능해진다). 그래서 이 함수는 `ListResultsParams`를
 * 받지 않는다 — 필터 상태를 여기에 섞지 말 것.
 *
 * `q`는 **값 자체**를 부분일치로 좁힌다(목록 검색창용, 목록의 행을 거르는 검색어 `q`와
 * 다르다). `limit`을 넘는 고유값은 응답의 `truncated=true`로 알려 온다. */
export async function listDistinctValues(
  jobId: number,
  field: DistinctValueField,
  options: { q?: string; limit?: number } = {},
): Promise<DistinctValuesResponse> {
  const { data } = await apiClient.get<DistinctValuesResponse>(
    `/jobs/${jobId}/results/distinct-values`,
    { params: { field, q: options.q || undefined, limit: options.limit } },
  )
  return data
}

/** "현재 필터 전체 선택"을 요청했는데 **서버가 `ids_only`를 모르는 구버전**인 경우.
 *
 * `ids_only`는 2026-08-03에 추가된 파라미터라, 그 이전 코드로 떠 있는 백엔드
 * (재시작하지 않은 uvicorn, 구버전 배포 exe)는 모르는 쿼리 파라미터를 조용히
 * 무시하고 200 + **1페이지(50건)만** 돌려준다. 그 응답에는 `ids` 키가 없으므로
 * (=`null`), 그대로 선택에 반영하면 "전체 선택했는데 50건만 들어간" 파일이 된다.
 * `ids: []`(조건에 맞는 결과가 진짜 0건)와 반드시 구분해야 한다 —
 * `SelectionExportUnsupportedError`와 같은 fail-safe 계약이다. */
export class SelectAllUnsupportedError extends Error {
  constructor() {
    super('서버가 필터 전체 선택(ids_only)을 지원하지 않습니다.')
    this.name = 'SelectAllUnsupportedError'
  }
}

/** "현재 필터 전체 선택" — 목록 화면과 **똑같은 필터·정렬**로 조회한 결과의 id를
 * 페이징 없이 전부 받아온다(2026-08-03).
 *
 * 목록 조회를 `page_size`만 키워 재사용할 수는 없다 — 백엔드가 `page_size`를 500으로
 * 조용히 상한 처리하고(에러 없이 잘림), 결과 1행이 73개 필드라 전체를 받으면
 * 무겁기 때문이다(실측 1,222건 약 2.4MB vs id만 12.4KB).
 *
 * 응답에 `ids`가 없으면(구버전 서버) 빈 배열로 뭉개지 말고 `SelectAllUnsupportedError`를
 * 던져 호출부가 "선택을 적용하지 않았다"고 안내하게 한다. */
export async function listAllResultIds(
  jobId: number,
  filters: Omit<ListResultsParams, 'page' | 'page_size' | 'ids_only'> = {},
): Promise<number[]> {
  const data = await listResults(jobId, { ...filters, ids_only: true })
  // `== null`은 null과 undefined(키 자체가 없는 구버전 응답)를 함께 잡는다.
  if (data.ids == null) throw new SelectAllUnsupportedError()
  return data.ids
}

/** §4-11 다중 선택 다운로드 옵션 — 백엔드의 "현재 필터 전체 내보내기"와 병행하는 별개 경로다
 * (전체 내보내기는 2026-08-03에 화면에서 버튼을 내렸고 백엔드 경로만 남아 있다). */
export interface ExportResultsOptions {
  /** 체크박스로 고른 `results.id` 목록. 지정되면 백엔드가 다른 필터·정렬을 **전부 무시**하고
   * 정확히 이 id들만 id 오름차순으로 내보낸다(타 Job의 id가 섞이거나 정수가 아니면 400). */
  ids?: number[]
  /** true면 `financial_history` 시트를 추가한 2시트 xlsx.
   * **xlsx 전용** — csv와 함께 보내면 백엔드가 400을 준다(메뉴에서 그 조합을 아예 없애 차단한다). */
  includeHistory?: boolean
}

/** 선택 항목 다운로드를 요청했는데 **서버가 `ids`를 무시하고 전체 결과를 내려준** 경우.
 *
 * `ids`/`include_history`는 2026-07-28에 추가된 파라미터라, 그 이전 코드로 떠 있는
 * 백엔드(예: 며칠째 재시작 없이 살아 있는 uvicorn, 구버전 배포 exe)에 요청하면
 * FastAPI가 **모르는 쿼리 파라미터를 조용히 무시**하고 200 + 전체 결과 파일을 준다 —
 * 사용자 눈에는 "체크하지 않은 회사까지 다운로드됨"으로 보인다(2026-07-28 실측 재현).
 * 이 경우 파일을 저장하지 않고 이 에러를 던져 호출부가 원인을 안내하게 한다. */
export class SelectionExportUnsupportedError extends Error {
  constructor() {
    super('서버가 선택 항목 다운로드(ids)를 지원하지 않습니다.')
    this.name = 'SelectionExportUnsupportedError'
  }
}

/** 선택 항목 다운로드를 요청했는데 **응답의 `Content-Disposition` 헤더 자체를 읽지 못한** 경우.
 *
 * 위 `SelectionExportUnsupportedError`와 달리 "서버가 구버전"이라고 단정할 수 없는 상황이다 —
 * 파일명을 못 읽으니 서버가 `ids`를 처리했는지 확인할 방법이 없을 뿐이다. 저장하면 전체 파일을
 * 선택 파일로 오인할 수 있으므로 똑같이 중단하되, 원인 안내는 다르게 한다. */
export class SelectionExportUnverifiableError extends Error {
  constructor() {
    super('다운로드 응답 정보를 읽지 못해 선택 항목 다운로드를 확인할 수 없습니다.')
    this.name = 'SelectionExportUnverifiableError'
  }
}

/** 현재 필터를 그대로 export 쿼리에 반영해 파일을 내려받는다 (blob 다운로드).
 * `options.ids`를 넘기면 필터 대신 그 결과들만 받는 선택 다운로드가 된다(§4-11). */
export async function exportResults(
  jobId: number,
  format: ExportFormat,
  filters: Omit<ListResultsParams, 'page' | 'page_size'> = {},
  options: ExportResultsOptions = {},
): Promise<void> {
  const response = await apiClient.get(`/jobs/${jobId}/export`, {
    params: {
      format,
      ...filters,
      // 백엔드는 쉼표 구분 문자열(`ids=1,2,3`)을 기대한다.
      ...(options.ids ? { ids: options.ids.join(',') } : {}),
      ...(options.includeHistory ? { include_history: true } : {}),
    },
    responseType: 'blob',
  })

  // `apiClient`는 상대 경로(`/api`)를 쓰는 동일 출처 요청이라 `Content-Disposition`이
  // 브라우저에 그대로 노출된다(교차 출처였다면 `Access-Control-Expose-Headers` 없이는
  // 이 헤더를 못 읽는다). 그래도 사내 프록시가 헤더를 떼는 드문 경우가 있어, 아래에서
  // "헤더 없음"과 "구버전 서버"를 구분해 처리한다.
  const disposition = response.headers['content-disposition'] as string | undefined
  const filenameMatch = disposition?.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
  const filename = filenameMatch ? decodeURIComponent(filenameMatch[1]) : `job_${jobId}_results.${format}`

  // 선택 다운로드는 **서버가 실제로 `ids`를 처리했는지 확인한 뒤에만** 저장한다.
  // 백엔드는 `ids`를 인식한 경우에만 파일명에 `_selected`를 붙이므로(§4-11,
  // `results.py::export_job_results`), 그 표식이 없으면 필터 기반 전체 내보내기가
  // 돌아온 것이다 — 저장해 버리면 "체크 안 한 회사까지 들어 있는 파일"이 된다.
  if (options.ids) {
    if (!filenameMatch) {
      // 파일명 자체를 못 읽었다 — 서버 버전 문제로 단정하지 말고 별도로 안내한다.
      throw new SelectionExportUnverifiableError()
    }
    if (!filename.includes('_selected')) {
      throw new SelectionExportUnsupportedError()
    }
  }

  const blobUrl = window.URL.createObjectURL(response.data as Blob)
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(blobUrl)
}

/** §4-12 선택 항목 보고서 생성 — 체크한 회사들의 감사 수임 제안서(HTML)를 **서버 로컬
 * 폴더에** 만들고 그 경로를 돌려받는다.
 *
 * `exportResults()`와 달리 blob 다운로드가 아니다 — 이 앱은 사무실 PC에서 단일
 * 사용자가 쓰는 데스크톱형 도구라(CLAUDE.md) 회사별 HTML + 발송처 라벨 엑셀을
 * 한 폴더에 떨궈 두는 편이 쓰기 쉽기 때문이다. 브라우저 다운로드를 흉내내지 말 것.
 *
 * `ids`는 최소 1개 필수다(빈 배열이면 백엔드가 422). 다른 Job의 결과 id가 섞이면 400,
 * 파일 저장/템플릿 부재 등 서버측 I/O 실패는 507 + 한국어 `detail`로 온다. */
export async function generateReport(
  jobId: number,
  ids: number[],
): Promise<GenerateReportResponse> {
  const { data } = await apiClient.post<GenerateReportResponse>(
    `/jobs/${jobId}/generate-report`,
    { ids },
  )
  return data
}

/** §4-12-A 선택 요약 — 보고서 생성 **전에** 확인 모달이 부르는 읽기 전용 집계.
 *
 * `generateReport()`와 **입력·에러 계약이 완전히 동일**하다(빈 배열 422, 타 Job의 id 혼입
 * 400, 없는 Job 404). 그래서 이 호출이 200이면 같은 `ids`로 생성을 눌러도 같은 이유로
 * 거부되지 않는다 — 확인 모달은 이 응답이 성공했을 때만 생성 버튼을 열어 준다.
 *
 * 응답 건수들은 서로 배타적이지 않다(한 회사가 매출액·총자산 조건에 동시에 걸릴 수
 * 있다) — 합산하지 말고 각각 따로 보여줄 것. 단 `failed`(파싱 실패, 검수 필요)와
 * `no_disclosure`(감사보고서 없음, 검수 대상 아님)는 결과 화면 탭과 같은 기준으로
 * 나뉜 배타적 집합이고, `no_history`(재무 이력 0건 → 생성 시 건너뜀)는 별개 축이다. */
export async function getSelectionSummary(
  jobId: number,
  ids: number[],
): Promise<SelectionSummaryResponse> {
  const { data } = await apiClient.post<SelectionSummaryResponse>(
    `/jobs/${jobId}/results/selection-summary`,
    { ids },
  )
  return data
}

/** CandidatesView "선택 취소" — 후보 목록에서 특정 회사를 재무정보 수집 대상에서
 * 제외/재포함한다(phase=CANDIDATES 동안만 가능, 실제 삭제는 start-financials
 * 호출 시점에 백엔드가 일괄 처리). */
export async function setResultExcluded(
  jobId: number,
  resultId: number,
  excluded: boolean,
): Promise<ResultResponse> {
  const { data } = await apiClient.patch<ResultResponse>(
    `/jobs/${jobId}/results/${resultId}/exclude`,
    { excluded },
  )
  return data
}

/** STEP 7(최근 N년 재무이력) — 회사 1건의 연도별 재무 이력(오래된 연도 → 최신 연도 순).
 * 매출액 필터로 제외된 결과는 이력이 애초에 없을 수 있다(에러가 아니라 빈 배열). */
export async function getResultHistory(
  jobId: number,
  resultId: number,
): Promise<FinancialSnapshotResponse[]> {
  const { data } = await apiClient.get<FinancialSnapshotResponse[]>(
    `/jobs/${jobId}/results/${resultId}/history`,
  )
  return data
}

/** §4-8 원문 섹션 열람 — 감사보고서 원문의 재무상태표/손익계산서/현금흐름표/주석을
 * 서버 조립 HTML로 받아온다(추가 API 호출/쿼터 0건, 로컬 문서 캐시만 사용).
 * rcept_no를 지정하면 다년치 이력의 특정 연도 공시를 열람한다(이 결과 소속 공시만 허용). */
export async function getDocumentSection(
  jobId: number,
  resultId: number,
  section: DocumentSection,
  rceptNo?: string,
): Promise<DocumentSectionResponse> {
  const { data } = await apiClient.get<DocumentSectionResponse>(
    `/jobs/${jobId}/results/${resultId}/document-sections/${section}`,
    { params: rceptNo ? { rcept_no: rceptNo } : {} },
  )
  return data
}

/** 요약 표의 대분류(유동자산 등)를 펼칠 때 쓰는 세부계정 상세.
 * 원문 섹션 열람과 마찬가지로 로컬 문서 캐시만 읽는다(추가 쿼터 0건).
 * rcept_no를 지정하면 다년치 이력의 특정 연도 공시를 파싱한다. */
export async function getAccountDetail(
  jobId: number,
  resultId: number,
  rceptNo?: string,
): Promise<AccountDetailResponse> {
  const { data } = await apiClient.get<AccountDetailResponse>(
    `/jobs/${jobId}/results/${resultId}/account-detail`,
    { params: rceptNo ? { rcept_no: rceptNo } : {} },
  )
  return data
}
