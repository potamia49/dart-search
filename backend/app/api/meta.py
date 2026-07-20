"""메타 정보 API.

상세개발계획.md §6:
    GET  /api/meta/regions               시도/시군구 목록
    GET  /api/meta/industries            KSIC 대/중분류 트리
    GET  /api/meta/quota                 오늘 API 호출량 / 잔여량
    POST /api/meta/validate-key          .env의 DART API 키 유효성 확인
    POST /api/meta/fsc-index/refresh     (§4-7 A1, 2026-07-15 추가) 금융위 전역
                                          인덱스(fsc_corp_index) 전수/부분 크롤
    GET  /api/meta/fsc-index/status      (2026-07-15 추가) 위 인덱스의 마지막
                                          완료 갱신 시각/행 수/TTL 초과 여부.
                                          사용자가 "다음 갱신은 내가 먼저
                                          물어봐야 아나?"라고 물어 화면에서
                                          바로 보이게 추가했다 — 백엔드가
                                          TTL 초과를 자동으로 알려주지 않고
                                          로그에만 남기던 것의 보완.
    POST /api/meta/dart-index/refresh    (§4-10 M8, 2026-07-20 추가) DART 기업개황
                                          전역 인덱스(dart_corp_index) 크롤.
                                          fsc_corp_index를 대체하며 실측 약 23분.
    GET  /api/meta/dart-index/status      (2026-07-20 추가) 위 인덱스의 행 수/
                                          마지막 완료 시각/진행 중 여부.
    POST /api/meta/candidates-preview    (2026-07-17 추가) 지역/업종 조건만으로
                                          Phase 1 A2(로컬 DB 필터, API 호출 없음)를
                                          미리 실행해 후보 수와 data.go.kr 일일
                                          쿼터 초과 가능성을 Job 생성 전에 보여준다.

`/api/meta/regions`/`/api/meta/industries`는 M4에서 추가됐다. 두 엔드포인트
모두 정적 데이터(`app/core/region_data.py`/`app/core/industry_data.py`)를
그대로 직렬화해 반환할 뿐 DB/외부 API 호출이 없다.

`/api/meta/fsc-index/refresh`는 관리자 전용 — Job 실행(run_job_phase1)
안에서는 절대 트리거되지 않는 A1(전수 크롤, 약 12,821페이지/10시간 소요
추정)을 명시적으로 시작하는 유일한 경로다. 이번 세션에서는 실제로 호출되지
않았고 구현/테스트만 됐다(CLAUDE.md 참고).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field

from app.api.jobs import RegionCondition
from app.config import get_settings
from app.core.dart_client import DartClient, FscCorpInfoClient
from app.core.dart_corp_index import (
    crawl_dart_corp_index,
    filter_local_candidates,
    find_ambiguous_corp_codes,
    get_dart_index_status,
    reconcile_ambiguous_rows,
)
from app.core.db import get_session_factory
from app.core.fsc_financial_stat import crawl_fsc_financial_stat, get_financial_stat_status
from app.core.fsc_index import crawl_fsc_index, get_fsc_index_status
from app.core.industry_data import INDUSTRIES
from app.core.region_data import REGIONS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meta", tags=["meta"])


class RegionEntry(BaseModel):
    sido: str
    sigungu: list[str]


@router.get("/regions", response_model=list[RegionEntry])
async def get_regions() -> list[RegionEntry]:
    """시도/시군구 목록. `app/core/region_data.py`의 정적 데이터를 그대로 반환한다.

    여기서 반환하는 `sido` 값은 `app/core/filters.py::SIDO_ALIASES`의 key(표준
    시도명)와 1:1 일치한다 — 프론트가 이 값을 그대로 Job 생성 시
    `cond_region.sido`에 넣어도 되도록 하기 위함.
    """
    return [RegionEntry(sido=sido, sigungu=sigungu) for sido, sigungu in REGIONS.items()]


class IndustryEntry(BaseModel):
    """업종 트리 노드 — 대분류/중분류/소분류가 같은 모양이라 재귀로 정의한다.

    `children`이 없는 노드(소분류)는 빈 리스트로 나간다 — 프론트가 레벨별로
    다른 타입을 다루지 않아도 되도록 모양을 통일한다.
    """

    code: str
    name: str
    children: list[IndustryEntry] = []


@router.get("/industries", response_model=list[IndustryEntry])
async def get_industries() -> list[IndustryEntry]:
    """KSIC 10차 대/중/소분류 트리. `app/core/industry_data.py`를 그대로 반환한다.

    중분류(2자리)/소분류(3자리) `code`는 DART `induty_code` 체계와 동일하며,
    `app/core/filters.py::industry_matches()`가 prefix 매칭에 그대로 사용한다.
    대분류만 알파벳(A~U)이라 `_expand_industry_prefixes()`가 소속 중분류로 펼친다.

    M8 5단계에서 소분류 한 층이 추가됐다(21/77/234) — 세분류·세세분류는
    누락률이 20.9%/41.3%라 **의도적으로 노출하지 않는다**(`industry_data.py` 참고).
    """
    return [IndustryEntry.model_validate(entry) for entry in INDUSTRIES]


class QuotaResponse(BaseModel):
    date: str
    call_count: int
    limit: int
    remaining: int


@router.get("/quota", response_model=QuotaResponse)
async def get_quota() -> QuotaResponse:
    """오늘자 OpenDART 호출량/잔여량. 키가 없어도 조회 가능(카운터는 로컬 DB 값)."""
    client = DartClient()
    try:
        status = client.get_quota_status()
    finally:
        await client.aclose()
    return QuotaResponse(**status)


class ValidateKeyRequest(BaseModel):
    target: Literal["dart", "data_go_kr", "both"] = "both"


class KeyCheckResult(BaseModel):
    valid: bool
    message: str


class ValidateKeyResponse(BaseModel):
    dart: KeyCheckResult | None = None
    data_go_kr: KeyCheckResult | None = None


@router.post("/validate-key", response_model=ValidateKeyResponse)
async def validate_key(payload: ValidateKeyRequest) -> ValidateKeyResponse:
    """DART / 공공데이터포털(금융위 기업기본정보) API 키를 최소 호출 1건으로 검증.

    키가 `.env`에 없으면 네트워크 호출 없이 즉시 invalid로 응답한다
    (불필요한 쿼터 소모 방지).
    """
    result = ValidateKeyResponse()

    if payload.target in ("dart", "both"):
        client = DartClient()
        try:
            valid, message = await client.validate_key()
        finally:
            await client.aclose()
        result.dart = KeyCheckResult(valid=valid, message=message)

    if payload.target in ("data_go_kr", "both"):
        fsc_client = FscCorpInfoClient()
        try:
            valid, message = await fsc_client.validate_key()
        finally:
            await fsc_client.aclose()
        result.data_go_kr = KeyCheckResult(valid=valid, message=message)

    return result


# 전수 크롤(A1) 감독 재시도 정책 — 페이지 단위 요청 자체의 재시도는
# `FscCorpInfoClient._get_with_retry`가 담당하고, 이 상수는 그 재시도까지
# 소진된 뒤 크롤 태스크 전체가 죽었을 때 체크포인트에서 다시 살리는 바깥쪽
# 루프(`_run_crawl`)용이다.
_FSC_CRAWL_OUTER_RETRIES = 100
_FSC_CRAWL_OUTER_BACKOFF_SEC = 30


class FscIndexRefreshRequest(BaseModel):
    """`max_pages`를 지정하지 않으면(None) 전체 페이징(약 12,821페이지, 실측
    약 10.2시간 예상)을 시도한다 — 신중히 호출할 것(§4-7)."""

    max_pages: int | None = None
    force: bool = False


class FscIndexRefreshResponse(BaseModel):
    started: bool
    message: str


@router.post("/fsc-index/refresh", response_model=FscIndexRefreshResponse)
async def refresh_fsc_index(
    payload: FscIndexRefreshRequest,
    background_tasks: BackgroundTasks,
) -> FscIndexRefreshResponse:
    """관리자용 — `fsc_corp_index`(§4-7 Phase 1 A1) 전역 인덱스 크롤을 백그라운드로 트리거.

    `app/core/pipeline.py::run_job_phase1()`은 이 크롤을 절대 직접 실행하지
    않는다(Job 하나의 실행 안에서 10시간짜리 작업을 트리거하면 안 되므로) —
    이 엔드포인트가 그 크롤을 시작하는 유일한 경로다. `max_pages`를 지정하면
    그 페이지 수만큼만 처리하고 체크포인트를 남긴 뒤 중단하며(파일럿/테스트용),
    다시 호출하면 이어서 진행한다. `force=True`면 체크포인트를 무시하고
    1페이지부터 다시 시작한다.
    """

    outer_attempts = _FSC_CRAWL_OUTER_RETRIES if payload.max_pages is None else 1

    async def _run_crawl() -> None:
        """전수 크롤(`max_pages=None`)은 실행에 수십 시간 걸리는 장시간 작업이라
        네트워크 일시 단절(DNS 실패, 타임아웃 등) 한 번에 통째로 죽으면 그때마다
        사람이 상태를 확인해 수동으로 재트리거해야 했다(2026-07-16 실측, 약 4시간
        사이 2회 발생). `crawl_fsc_index`가 체크포인트로 이어하기 가능하다는 점을
        이용해, 예외 발생 시 일정 대기 후 자동으로 다시 호출하는 감독 루프를
        추가했다. `max_pages`가 지정된 파일럿/테스트 호출은 의도적으로 부분
        실행이므로 재시도하지 않는다(기존 동작 그대로 1회만 시도).
        """
        for attempt in range(1, outer_attempts + 1):
            client = FscCorpInfoClient()
            try:
                result = await crawl_fsc_index(
                    client,
                    get_session_factory(),
                    max_pages=payload.max_pages,
                    # force는 최초 시도에만 적용한다 — 재시도는 이전 시도가 남긴
                    # 체크포인트를 그대로 이어서 진행해야 하므로 항상 force=False.
                    force=payload.force if attempt == 1 else False,
                )
                logger.info("fsc_corp_index 갱신 완료: %s", result)
                return
            except Exception:  # noqa: BLE001 - 백그라운드 작업은 여기서 반드시 흡수해야 한다
                logger.exception(
                    "fsc_corp_index 갱신 중 예외 발생(attempt=%s/%s)", attempt, outer_attempts
                )
                if attempt < outer_attempts:
                    await asyncio.sleep(_FSC_CRAWL_OUTER_BACKOFF_SEC)
            finally:
                await client.aclose()

    background_tasks.add_task(_run_crawl)
    return FscIndexRefreshResponse(
        started=True,
        message="fsc_corp_index 갱신을 백그라운드로 시작했습니다.",
    )


class DartIndexRefreshRequest(BaseModel):
    """`max_industries`를 지정하지 않으면(None) 중분류 77개 전체를 크롤한다
    (실측 약 23분 — A1의 10~16시간과 달리 짧아 부담이 적다, §4-10)."""

    max_industries: int | None = None
    force: bool = False


class DartIndexRefreshResponse(BaseModel):
    started: bool
    message: str


# DART 기업개황 크롤 감독 재시도 — 요청 단위 재시도는
# `dart_corp_index._post`가 담당하고, 이 상수는 그것까지 소진돼 태스크가
# 죽었을 때 중분류 체크포인트에서 되살리는 바깥쪽 루프용이다.
# 전체가 약 23분이라 A1(100회)보다 적게 잡는다.
_DART_CRAWL_OUTER_RETRIES = 20
_DART_CRAWL_OUTER_BACKOFF_SEC = 30


@router.post("/dart-index/refresh", response_model=DartIndexRefreshResponse)
async def refresh_dart_index(
    payload: DartIndexRefreshRequest,
    background_tasks: BackgroundTasks,
) -> DartIndexRefreshResponse:
    """관리자용 — `dart_corp_index`(§4-10 / M8 1단계) 전역 인덱스 크롤을 백그라운드로 트리거.

    `fsc-index/refresh`와 같은 원칙이다: Job 실행 안에서는 절대 트리거되지 않고
    이 엔드포인트가 유일한 시작 경로다. 중분류 단위로 체크포인트를 남기므로
    중단돼도 다시 호출하면 이어서 진행하고, `force=True`면 처음부터 다시 한다.
    `max_industries`를 지정한 파일럿/테스트 호출은 의도적 부분 실행이므로
    감독 재시도를 걸지 않는다(1회만 시도).
    """

    outer_attempts = _DART_CRAWL_OUTER_RETRIES if payload.max_industries is None else 1

    async def _run_crawl() -> None:
        for attempt in range(1, outer_attempts + 1):
            try:
                result = await crawl_dart_corp_index(
                    session_factory=get_session_factory(),
                    max_industries=payload.max_industries,
                    # force는 최초 시도에만 — 재시도는 항상 체크포인트를 이어간다.
                    force=payload.force if attempt == 1 else False,
                )
                logger.info("dart_corp_index 갱신 완료: %s", result)
                return
            except Exception:  # noqa: BLE001 - 백그라운드 작업은 여기서 반드시 흡수해야 한다
                logger.exception(
                    "dart_corp_index 갱신 중 예외 발생(attempt=%s/%s)", attempt, outer_attempts
                )
                if attempt < outer_attempts:
                    await asyncio.sleep(_DART_CRAWL_OUTER_BACKOFF_SEC)

    background_tasks.add_task(_run_crawl)
    return DartIndexRefreshResponse(
        started=True,
        message="dart_corp_index 갱신을 백그라운드로 시작했습니다.",
    )


class DartIndexReconcileRequest(BaseModel):
    """`max_groups`는 파일럿 확인용 — 미지정 시 위험 그룹 전체를 교정한다."""

    max_groups: int | None = None


class DartIndexReconcileResponse(BaseModel):
    started: bool
    message: str
    group_count: int


@router.post("/dart-index/reconcile", response_model=DartIndexReconcileResponse)
async def reconcile_dart_index(
    payload: DartIndexReconcileRequest,
    background_tasks: BackgroundTasks,
) -> DartIndexReconcileResponse:
    """관리자용 — 동명 회사끼리 교차된 인덱스 행을 `company.json` 기준으로 교정한다.

    `merge_by_position()`이 위치 결합의 어긋남을 회사명으로만 감지하기 때문에
    **같은 이름끼리 자리가 바뀌면 잡히지 않는다**(2026-07-20 M8 6단계 검증에서
    발견). 전수 재크롤 대신 위험 그룹(전체의 3.69%)만 DART 정본으로 되돌린다.

    크롤과 달리 **DART 일일 호출 한도를 소모**하므로(그룹 구성원 1건당
    company.json 1회) 크롤 완료 후 별도로 호출하는 단계로 분리했다.
    """
    with get_session_factory()() as db:
        groups = find_ambiguous_corp_codes(db)
    target = groups if payload.max_groups is None else groups[: payload.max_groups]
    call_estimate = sum(len(codes) for codes in target)

    async def _run_reconcile() -> None:
        try:
            async with DartClient() as client:
                stats = await reconcile_ambiguous_rows(
                    client,
                    get_session_factory(),
                    max_groups=payload.max_groups,
                )
            logger.info("dart_corp_index 동명 그룹 교정 완료: %s", stats)
        except Exception:  # noqa: BLE001 - 백그라운드 작업은 여기서 흡수한다
            logger.exception("dart_corp_index 동명 그룹 교정 중 예외 발생")

    background_tasks.add_task(_run_reconcile)
    return DartIndexReconcileResponse(
        started=True,
        message=(
            f"동명 그룹 {len(target)}개(약 {call_estimate}건 조회) 교정을 "
            "백그라운드로 시작했습니다."
        ),
        group_count=len(target),
    )


class DartIndexStatusResponse(BaseModel):
    row_count: int
    last_completed_at: str | None
    crawl_in_progress: bool
    checkpoint_industry: str | None


@router.get("/dart-index/status", response_model=DartIndexStatusResponse)
async def get_dart_index_status_endpoint() -> DartIndexStatusResponse:
    """`dart_corp_index`의 행 수 / 마지막 완료 시각 / 진행 중 여부."""
    return DartIndexStatusResponse(**get_dart_index_status())


class FscFinancialRefreshRequest(BaseModel):
    """수집 회계연도. 미지정 시 최근 3개년(실측 63요청 / 약 3분, §4-10-B)."""

    years: list[str] | None = None
    max_pages_per_year: int | None = None


class FscFinancialRefreshResponse(BaseModel):
    started: bool
    message: str


@router.post("/fsc-financial/refresh", response_model=FscFinancialRefreshResponse)
async def refresh_fsc_financial_stat(
    payload: FscFinancialRefreshRequest,
    background_tasks: BackgroundTasks,
) -> FscFinancialRefreshResponse:
    """관리자용 — `fsc_financial_stat`(§4-10-B / M8 2단계) 전수 크롤을 백그라운드로 트리거.

    여기 담기는 값은 **후보를 제외하는 데 쓰지 않는다**(§4-10-C) — 후보 목록의
    참고 표시와 Phase 2 처리 순서에만 쓴다. 전체가 약 3분이라 체크포인트 재개를
    두지 않았고, 재실행해도 `(crno, biz_year)` upsert라 멱등이다.
    """

    async def _run_crawl() -> None:
        client = FscCorpInfoClient()
        try:
            result = await crawl_fsc_financial_stat(
                client,
                get_session_factory(),
                years=payload.years,
                max_pages_per_year=payload.max_pages_per_year,
            )
            logger.info("fsc_financial_stat 갱신 완료: %s", result)
        except Exception:  # noqa: BLE001 - 백그라운드 작업은 여기서 반드시 흡수해야 한다
            logger.exception("fsc_financial_stat 갱신 중 예외 발생")
        finally:
            await client.aclose()

    background_tasks.add_task(_run_crawl)
    return FscFinancialRefreshResponse(
        started=True,
        message="fsc_financial_stat 갱신을 백그라운드로 시작했습니다.",
    )


class FscFinancialStatusResponse(BaseModel):
    row_count: int
    last_completed_at: str | None
    years: list[str]
    crawl_in_progress: bool


@router.get("/fsc-financial/status", response_model=FscFinancialStatusResponse)
async def get_fsc_financial_status_endpoint() -> FscFinancialStatusResponse:
    return FscFinancialStatusResponse(**get_financial_stat_status())


class FscIndexStatusResponse(BaseModel):
    row_count: int
    last_completed_at: str | None
    ttl_days: int
    is_stale: bool
    crawl_in_progress: bool


@router.get("/fsc-index/status", response_model=FscIndexStatusResponse)
async def get_fsc_index_status_endpoint() -> FscIndexStatusResponse:
    """`fsc_corp_index`의 마지막 완료 갱신 시각/행 수/TTL 초과 여부.

    Phase 1 Job(`run_job_phase1`)은 TTL이 지나도 자동으로 갱신하지 않고
    로그에만 경고를 남긴다 — 화면에서 이 상태를 바로 확인할 수 있게
    SearchPage/JobsPage가 이 엔드포인트를 호출한다.
    """
    return FscIndexStatusResponse(**get_fsc_index_status())


# data.go.kr GetFinaStatInfoService_V2(A3가 호출하는 금융위 재무정보 API)의
# 후보 1개사를 Phase 2에서 처리하는 데 드는 DART 호출 수(§4-10-C 실측 근거:
# 경남 4,538개사 × 약 5회 ≈ 22,690 호출). 최신 공시 조회 1회 + 원문 다운로드,
# 그리고 재무이력 연도 수만큼의 추가 원문이 더해진 값이다.
_DART_CALLS_PER_CANDIDATE = 5


class CandidatesPreviewRequest(BaseModel):
    region: RegionCondition = Field(default_factory=RegionCondition)
    industry: list[str] = Field(default_factory=list)


class CandidatesPreviewResponse(BaseModel):
    candidate_count: int
    daily_quota_assumed: int
    exceeds_daily_quota: bool
    estimated_days: int


@router.post("/candidates-preview", response_model=CandidatesPreviewResponse)
async def get_candidates_preview(payload: CandidatesPreviewRequest) -> CandidatesPreviewResponse:
    """검색 조건(지역/업종) 제출 전에 후보 수와 예상 소요일을 미리 계산한다.

    Phase 1 A2(`dart_corp_index.filter_local_candidates`)만 실행한다 — 로컬 DB
    쿼리뿐이라 외부 API 호출이 전혀 없고, Job을 만들지 않고도 즉시 응답할 수 있다.

    **기준이 바뀌었다(M8 3단계, §4-10-C)**: 예전에는 A3(`getSummFinaStat_V2`
    건별 스크리닝)의 data.go.kr 일일 쿼터가 병목이라 그 한도로 일수를 계산했다.
    A3가 폐기된 지금 병목은 **Phase 2가 쓰는 DART 일일 한도**다 — 후보 1개사당
    약 `_DART_CALLS_PER_CANDIDATE`회를 쓰므로 하루에 처리 가능한 후보 수는
    `daily_quota_limit / 5` 정도다. 한도를 넘으면 Job이 `PAUSED_QUOTA`로 멈췄다가
    다음 날 이어서 진행되며(결과 정확도에는 영향 없음), 밴드 근접도 정렬 덕에
    첫날에 실제 대상 대부분이 확보된다(§4-10-D).
    """
    with get_session_factory()() as db:
        candidates = filter_local_candidates(
            db,
            cond_region=payload.region.model_dump(),
            cond_industry=payload.industry,
        )
    count = len(candidates)
    daily_capacity = max(1, get_settings().daily_quota_limit // _DART_CALLS_PER_CANDIDATE)
    estimated_days = (count + daily_capacity - 1) // daily_capacity if count else 0
    return CandidatesPreviewResponse(
        candidate_count=count,
        daily_quota_assumed=daily_capacity,
        exceeds_daily_quota=count > daily_capacity,
        estimated_days=estimated_days,
    )
