"""결과 조회/다운로드 API.

상세개발계획.md §6 (M2~M4 범위):
    GET /api/jobs/{id}/results                  결과 목록 (페이징, parse_status/제외 여부 필터)
    GET /api/jobs/{id}/export?format=xlsx|csv    결과 파일 다운로드
    GET /api/jobs/{id}/results/{result_id}/history  회사 1건의 연도별 재무 이력
                                                     (STEP 7, 2026-07-15 추가)

STEP 5(파싱, M3)가 채워져 `parse_status`/재무 항목이 실제 값을 갖는다.
`/export`는 M4에서 `app/exporters/excel.py`와 함께 구현했다 — 페이징 없이
필터를 통과한 결과 전체를 xlsx/csv로 내려준다.

`/results/{result_id}/history`는 `financial_snapshots`(STEP 7)를 조회한다.
기존 `/results` 목록 응답은 무겁게 만들지 않기 위해 그대로 두고(이력은
포함하지 않음), 상세 조회에서만 lazy-load하게 별도 엔드포인트로 분리했다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.db import get_db
from app.exporters.excel import export_results
from app.models.financial_snapshot import FinancialSnapshot
from app.models.job import Job, JobPhase
from app.models.result import Result
from app.parsers.document_sections import SECTION_TITLE_MARKS, extract_section_html

router = APIRouter(prefix="/api/jobs", tags=["results"])


def _build_results_query(
    job_id: int,
    parse_status: str | None = None,
    excluded_by_revenue: bool | None = None,
    excluded_by_assets: bool | None = None,
) -> Select:
    """`results` 조회 쿼리 빌더 — `/results`(페이징)와 `/export`(전체)가 공유한다."""
    stmt = select(Result).where(Result.job_id == job_id)
    if parse_status is not None:
        stmt = stmt.where(Result.parse_status == parse_status)
    if excluded_by_revenue is not None:
        stmt = stmt.where(Result.excluded_by_revenue == (1 if excluded_by_revenue else 0))
    if excluded_by_assets is not None:
        stmt = stmt.where(Result.excluded_by_assets == (1 if excluded_by_assets else 0))
    return stmt


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
    cf_operating_cur: int | None
    cf_operating_prv: int | None
    cf_investing_cur: int | None
    cf_investing_prv: int | None
    cf_financing_cur: int | None
    cf_financing_prv: int | None
    cf_ending_cash_cur: int | None
    cf_ending_cash_prv: int | None

    parse_status: str | None
    parse_note: str | None
    excluded_by_revenue: int
    excluded_by_assets: int
    excluded_manually: int


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
    excluded_by_assets: bool | None = None,
    db: Session = Depends(get_db),
) -> ResultListResponse:
    """결과 목록 페이징 조회.

    - `parse_status`: OK/PARTIAL/FAILED 중 하나로 필터.
    - `excluded_by_revenue`: true/false — 매출액 사후 필터로 제외된 건만/제외되지 않은 건만.
    - `excluded_by_assets`: true/false — 총자산 사후 필터로 제외된 건만/제외되지 않은 건만
      (§4-7-2, 2026-07-15 추가).
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    stmt = _build_results_query(job_id, parse_status, excluded_by_revenue, excluded_by_assets)

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


class ExcludeResultRequest(BaseModel):
    excluded: bool


@router.patch("/{job_id}/results/{result_id}/exclude", response_model=ResultResponse)
async def set_result_excluded(
    job_id: int,
    result_id: int,
    payload: ExcludeResultRequest,
    db: Session = Depends(get_db),
) -> ResultResponse:
    """후보 목록(Phase 1, CandidatesView)에서 특정 회사를 재무정보 수집 대상에서
    제외/재포함한다 — "선택 취소" 기능(2026-07-18 추가).

    `excluded_manually` 플래그만 토글하므로 phase=CANDIDATES인 동안은 자유롭게
    다시 켤 수 있다. 실제 삭제(행 제거)는 하지 않고, `POST
    /api/jobs/{id}/start-financials` 호출 시점에 제외 표시된 행을 일괄 삭제한다
    (`app/api/jobs.py::start_financials`) — Phase 2 파이프라인(B1~B5)은 전혀
    수정할 필요가 없다. phase=FINANCIALS로 전환된 뒤(이미 확정 처리에 들어간
    결과)에는 의미가 없으므로 400으로 거부한다.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")
    if job.phase != JobPhase.CANDIDATES:
        raise HTTPException(
            status_code=400,
            detail="후보 확정(Phase 1) 단계에서만 선택을 변경할 수 있습니다.",
        )

    result = db.get(Result, result_id)
    if result is None or result.job_id != job_id:
        raise HTTPException(status_code=404, detail="해당 Job의 결과를 찾을 수 없습니다.")

    result.excluded_manually = 1 if payload.excluded else 0
    db.commit()
    db.refresh(result)
    return ResultResponse.model_validate(result)


_EXPORT_CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8-sig",
}


@router.get("/{job_id}/export")
async def export_job_results(
    job_id: int,
    format: str = "xlsx",
    parse_status: str | None = None,
    excluded_by_revenue: bool | None = None,
    excluded_by_assets: bool | None = None,
    db: Session = Depends(get_db),
) -> Response:
    """결과 파일 다운로드 (xlsx/csv, 페이징 없이 필터를 통과한 전체 결과).

    `parse_status`/`excluded_by_revenue`/`excluded_by_assets`는 `/results`와
    동일한 필터 의미다. `format`이 xlsx/csv가 아니면 400.
    """
    if format not in _EXPORT_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 format입니다: {format!r} (xlsx 또는 csv만 가능)",
        )

    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    stmt = _build_results_query(job_id, parse_status, excluded_by_revenue, excluded_by_assets)
    rows = db.execute(stmt.order_by(Result.id.asc())).scalars().all()

    content = export_results(rows, format)
    filename = f"dart_search_job{job_id}_results.{format}"
    return Response(
        content=content,
        media_type=_EXPORT_CONTENT_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class FinancialSnapshotResponse(BaseModel):
    """STEP 7(최근 N년 재무이력)이 채우는 회사-회계연도 단위 스냅샷 1건."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    result_id: int | None
    rcept_no: str | None
    fiscal_year: str

    current_assets: int | None
    noncurrent_assets: int | None
    total_assets: int | None
    current_liab: int | None
    noncurrent_liab: int | None
    total_liab: int | None
    total_equity: int | None
    revenue: int | None
    cogs: int | None
    gross_margin: float | None
    sga: int | None
    operating_income: int | None
    net_income: int | None
    cf_operating: int | None
    cf_investing: int | None
    cf_financing: int | None
    cf_ending_cash: int | None

    parse_status: str | None
    parse_note: str | None


@router.get(
    "/{job_id}/results/{result_id}/history",
    response_model=list[FinancialSnapshotResponse],
)
async def get_result_history(
    job_id: int,
    result_id: int,
    db: Session = Depends(get_db),
) -> list[FinancialSnapshotResponse]:
    """회사 1건(result_id)의 연도별 재무 이력을 오래된 연도 → 최신 연도 순으로 반환.

    STEP 7이 `excluded_by_revenue=0`인 결과만 대상으로 채우므로, 매출액
    필터로 제외된 결과는 이력이 비어 있을 수 있다(에러가 아니라 빈 배열).
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    result = db.get(Result, result_id)
    if result is None or result.job_id != job_id:
        raise HTTPException(status_code=404, detail="해당 Job의 결과를 찾을 수 없습니다.")

    rows = (
        db.execute(
            select(FinancialSnapshot)
            .where(FinancialSnapshot.result_id == result_id)
            .order_by(FinancialSnapshot.fiscal_year.asc())
        )
        .scalars()
        .all()
    )
    return [FinancialSnapshotResponse.model_validate(r) for r in rows]


class DocumentSectionResponse(BaseModel):
    """감사보고서 원문 1개 섹션의 서버 조립 HTML (§4-8, 2026-07-19)."""

    section: str
    rcept_no: str
    available: bool  # 해당 섹션을 원문에서 실제로 찾았는지
    html: str  # 조립된 안전 HTML(찾지 못했으면 "")
    notice: str | None = None  # 안내 문구(미첨부/PDF 미지원 등)


def _pick_cached_xml(cache_dir: Path, rcept_no: str) -> Path | None:
    """DOCUMENT_CACHE_DIR/{rcept_no}/ 에서 파싱 대상 XML 1개를 고른다(없으면 None)."""
    target_dir = cache_dir / rcept_no
    if not target_dir.is_dir():
        return None
    xml_files = sorted(target_dir.rglob("*.xml"))
    return xml_files[0] if xml_files else None


@router.get(
    "/{job_id}/results/{result_id}/document-sections/{section}",
    response_model=DocumentSectionResponse,
)
async def get_document_section(
    job_id: int,
    result_id: int,
    section: str,
    rcept_no: str | None = None,
    db: Session = Depends(get_db),
) -> DocumentSectionResponse:
    """감사보고서 원문의 특정 섹션(bs|is|cf|notes)을 서버 조립 HTML로 반환 (§4-8).

    로컬 문서 캐시(`DOCUMENT_CACHE_DIR`)의 원문 XML을 열어 on-demand로 섹션을
    잘라내므로 추가 API 호출/쿼터가 0건이다. 대상 공시는 기본
    `results.rcept_no`(가장 최근 감사보고서)이고, `?rcept_no=`로 다년치 이력의
    특정 연도 공시(`financial_snapshots.rcept_no`)도 열람할 수 있다 — 단
    해당 result에 속한 rcept_no만 허용한다.
    """
    if section not in SECTION_TITLE_MARKS:
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 섹션입니다: {section!r} (bs|is|cf|notes)",
        )

    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    result = db.get(Result, result_id)
    if result is None or result.job_id != job_id:
        raise HTTPException(status_code=404, detail="해당 Job의 결과를 찾을 수 없습니다.")

    # 대상 rcept_no 결정 + 소속 검증. 기본은 결과의 최신 감사보고서, ?rcept_no=로
    # 지정하면 이 result의 이력 공시(financial_snapshots) 중 하나여야 한다.
    if rcept_no is None:
        target_rcept_no = result.rcept_no
    else:
        allowed = {result.rcept_no} | {
            row[0]
            for row in db.execute(
                select(FinancialSnapshot.rcept_no).where(
                    FinancialSnapshot.result_id == result_id
                )
            ).all()
        }
        allowed.discard(None)
        if rcept_no not in allowed:
            raise HTTPException(
                status_code=404,
                detail="해당 결과에 속한 공시(rcept_no)가 아닙니다.",
            )
        target_rcept_no = rcept_no

    if not target_rcept_no:
        raise HTTPException(status_code=404, detail="이 결과에는 원문 공시(rcept_no)가 없습니다.")

    cache_dir = Path(get_settings().document_cache_dir)
    xml_path = _pick_cached_xml(cache_dir, target_rcept_no)
    if xml_path is None:
        # PDF만 있는 경우와 캐시 자체가 없는 경우를 구분해 안내한다.
        target_dir = cache_dir / target_rcept_no
        if target_dir.is_dir() and any(target_dir.rglob("*.pdf")):
            return DocumentSectionResponse(
                section=section,
                rcept_no=target_rcept_no,
                available=False,
                html="",
                notice="PDF 원문은 섹션 열람을 지원하지 않습니다(§4-8 — XML 원문만 지원).",
            )
        raise HTTPException(
            status_code=404,
            detail="원문 캐시가 없습니다 — 재수집이 필요합니다.",
        )

    found, html = extract_section_html(xml_path.read_bytes(), section)
    notice = None
    if not found:
        notice = "해당 섹션을 원문에서 찾을 수 없습니다(재무제표/주석 미첨부 등)."
    return DocumentSectionResponse(
        section=section,
        rcept_no=target_rcept_no,
        available=found,
        html=html,
        notice=notice,
    )
