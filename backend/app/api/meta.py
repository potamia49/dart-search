"""메타 정보 API.

상세개발계획.md §6:
    GET  /api/meta/quota          오늘 API 호출량 / 잔여량
    POST /api/meta/validate-key   .env의 DART API 키 유효성 확인

M1 범위에서는 이 두 엔드포인트만 구현한다. `/api/meta/regions`,
`/api/meta/industries`(KSIC 트리)는 M2/M4에서 지역·업종 필터와 함께 추가한다.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.dart_client import DartClient, FscCorpInfoClient

router = APIRouter(prefix="/api/meta", tags=["meta"])


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
