"""메타 정보 API.

상세개발계획.md §6:
    GET  /api/meta/regions        시도/시군구 목록
    GET  /api/meta/industries     KSIC 대/중분류 트리
    GET  /api/meta/quota          오늘 API 호출량 / 잔여량
    POST /api/meta/validate-key   .env의 DART API 키 유효성 확인

`/api/meta/regions`/`/api/meta/industries`는 M4에서 추가됐다. 두 엔드포인트
모두 정적 데이터(`app/core/region_data.py`/`app/core/industry_data.py`)를
그대로 직렬화해 반환할 뿐 DB/외부 API 호출이 없다.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.dart_client import DartClient, FscCorpInfoClient
from app.core.industry_data import INDUSTRIES
from app.core.region_data import REGIONS

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
