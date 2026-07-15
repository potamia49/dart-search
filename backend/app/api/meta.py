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

`/api/meta/regions`/`/api/meta/industries`는 M4에서 추가됐다. 두 엔드포인트
모두 정적 데이터(`app/core/region_data.py`/`app/core/industry_data.py`)를
그대로 직렬화해 반환할 뿐 DB/외부 API 호출이 없다.

`/api/meta/fsc-index/refresh`는 관리자 전용 — Job 실행(run_job_phase1)
안에서는 절대 트리거되지 않는 A1(전수 크롤, 약 12,821페이지/10시간 소요
추정)을 명시적으로 시작하는 유일한 경로다. 이번 세션에서는 실제로 호출되지
않았고 구현/테스트만 됐다(CLAUDE.md 참고).
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from app.core.dart_client import DartClient, FscCorpInfoClient
from app.core.db import get_session_factory
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


class IndustryChild(BaseModel):
    code: str
    name: str


class IndustryEntry(BaseModel):
    code: str
    name: str
    children: list[IndustryChild]


@router.get("/industries", response_model=list[IndustryEntry])
async def get_industries() -> list[IndustryEntry]:
    """KSIC 10차 대/중분류 트리. `app/core/industry_data.py`의 정적 데이터를 그대로 반환한다.

    중분류 `code`는 DART `induty_code` 체계와 동일한 2자리 코드이며,
    `app/core/filters.py::industry_matches()`가 prefix 매칭에 그대로 사용한다.
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

    async def _run_crawl() -> None:
        client = FscCorpInfoClient()
        try:
            result = await crawl_fsc_index(
                client,
                get_session_factory(),
                max_pages=payload.max_pages,
                force=payload.force,
            )
            logger.info("fsc_corp_index 갱신 완료: %s", result)
        except Exception:  # noqa: BLE001 - 백그라운드 작업은 여기서 반드시 흡수해야 한다
            logger.exception("fsc_corp_index 갱신 중 예외 발생")
        finally:
            await client.aclose()

    background_tasks.add_task(_run_crawl)
    return FscIndexRefreshResponse(
        started=True,
        message="fsc_corp_index 갱신을 백그라운드로 시작했습니다.",
    )


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
