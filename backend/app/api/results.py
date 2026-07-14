"""결과 조회/다운로드 API.

상세개발계획.md §6 (M2~M4 범위):
    GET /api/jobs/{id}/results                  결과 목록 (페이징, parse_status/제외 여부 필터)
    GET /api/jobs/{id}/export?format=xlsx|csv    결과 파일 다운로드

STEP 5(파싱, M3)가 채워져 `parse_status`/재무 항목이 실제 값을 갖는다.
`/export`는 `app/exporters/excel.py`가 준비되는 M4에서 구현한다
(이 파일에서는 건드리지 않음).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.job import Job
from app.models.result import Result

router = APIRouter(prefix="/api/jobs", tags=["results"])


class ResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int | None
    corp_code: str | None
    rcept_no: str | None

    corp_name: str | None
    address: str | None
    phone: str | None
    ceo_name: str | None
    induty_code: str | None
    induty_name: str | None
    fiscal_date: str | None
    audit_opinion: str | None

    current_assets_cur: int | None
    current_assets_prv: int | None
    noncurrent_assets_cur: int | None
    noncurrent_assets_prv: int | None
    total_assets_cur: int | None
    total_assets_prv: int | None
    current_liab_cur: int | None
    current_liab_prv: int | None
    noncurrent_liab_cur: int | None
    noncurrent_liab_prv: int | None
    total_liab_cur: int | None
    total_liab_prv: int | None
    total_equity_cur: int | None
    total_equity_prv: int | None
    revenue_cur: int | None
    revenue_prv: int | None
    cogs_cur: int | None
    cogs_prv: int | None
    gross_margin_cur: float | None
    gross_margin_prv: float | None
    sga_cur: int | None
    sga_prv: int | None
    operating_income_cur: int | None
    operating_income_prv: int | None
    net_income_cur: int | None
    net_income_prv: int | None

    parse_status: str | None
    parse_note: str | None
    excluded_by_revenue: int


class ResultListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ResultResponse]


@router.get("/{job_id}/results", response_model=ResultListResponse)
async def list_results(
    job_id: int,
    page: int = 1,
    page_size: int = 50,
    parse_status: str | None = None,
    excluded_by_revenue: bool | None = None,
    db: Session = Depends(get_db),
) -> ResultListResponse:
    """결과 목록 페이징 조회.

    - `parse_status`: OK/PARTIAL/FAILED 중 하나로 필터.
    - `excluded_by_revenue`: true/false — 매출액 사후 필터로 제외된 건만/제외되지 않은 건만.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    stmt = select(Result).where(Result.job_id == job_id)
    if parse_status is not None:
        stmt = stmt.where(Result.parse_status == parse_status)
    if excluded_by_revenue is not None:
        stmt = stmt.where(Result.excluded_by_revenue == (1 if excluded_by_revenue else 0))

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()

    page = max(page, 1)
    page_size = max(min(page_size, 500), 1)
    rows = (
        db.execute(
            stmt.order_by(Result.id.asc()).offset((page - 1) * page_size).limit(page_size)
        )
        .scalars()
        .all()
    )

    return ResultListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[ResultResponse.model_validate(r) for r in rows],
    )


# TODO(M4): GET /api/jobs/{id}/export?format=xlsx|csv — app/exporters/excel.py 완성 후 구현
