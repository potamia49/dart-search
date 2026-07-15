"""Job 생성/조회/취소/재시도 API.

상세개발계획.md §6, §8 (M2 범위):
    POST /api/jobs                     조건 입력 → Job 생성 + 백그라운드 실행 시작
    GET  /api/jobs                     Job 목록 (상태/진행률 포함)
    GET  /api/jobs/{id}                Job 상세 (프론트가 2초 간격 폴링)
    POST /api/jobs/{id}/cancel         실행 취소
    POST /api/jobs/{id}/resume         중단(쿼터/오류) Job 이어하기
    POST /api/jobs/{id}/retry-failed   파싱 실패 건만 재시도

Job 실행 자체(STEP 1~7, STEP7은 2026-07-15 추가된 "최근 N년 재무이력"
수집 — CLAUDE.md 참고)는 `app/core/pipeline.py`의 `run_job()`이 담당하고,
이 라우터는 FastAPI `BackgroundTasks`로 그것을 트리거/조회/취소만 한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pipeline import retry_failed_parsing, run_job
from app.models.job import Job, JobStatus
from app.models.result import ParseStatus, Result

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


# ---------------------------------------------------------------------------
# 요청/응답 스키마 (상세개발계획.md §5 jobs.cond_* JSON 형태 그대로)
# ---------------------------------------------------------------------------


class RegionCondition(BaseModel):
    """cond_region: {"sido": "경남", "sigungu": ["김해시", "양산시"]}"""

    sido: str | None = None
    sigungu: list[str] = Field(default_factory=list)


class RevenueCondition(BaseModel):
    """cond_revenue: {"min_krw": 6000000000, "max_krw": 15000000000} — M3 매출액 필터에서 사용."""

    min_krw: int | None = None
    max_krw: int | None = None


class PeriodCondition(BaseModel):
    """cond_period: {"bgn_de": "20250101", "end_de": "20251231"} — STEP 2 list.json 파라미터."""

    bgn_de: str
    end_de: str


class JobCreateRequest(BaseModel):
    name: str | None = None
    region: RegionCondition = Field(default_factory=RegionCondition)
    revenue: RevenueCondition = Field(default_factory=RevenueCondition)
    industry: list[str] = Field(default_factory=list)
    period: PeriodCondition
    # STEP 7(최근 N년 재무이력, 2026-07-15 추가) 목표 연도수. 감사보고서 1건이
    # 당기·전기 2개년을 비교식으로 담기 때문에 짝수만 허용한다(상세개발계획.md
    # §4-6). 기본값 4는 사용자와 논의해 확정.
    history_years: Literal[2, 4, 6, 10] = 4


class JobResponse(BaseModel):
    id: int
    created_at: str | None
    name: str | None
    cond_region: dict[str, Any] | None
    cond_revenue: dict[str, Any] | None
    cond_industry: list[str] | None
    cond_period: dict[str, Any] | None
    history_years: int | None
    status: str | None
    current_step: int | None
    progress_done: int | None
    progress_total: int | None
    error_msg: str | None

    @classmethod
    def from_job(cls, job: Job) -> "JobResponse":
        return cls(
            id=job.id,
            created_at=job.created_at,
            name=job.name,
            cond_region=json.loads(job.cond_region) if job.cond_region else None,
            cond_revenue=json.loads(job.cond_revenue) if job.cond_revenue else None,
            cond_industry=json.loads(job.cond_industry) if job.cond_industry else None,
            cond_period=json.loads(job.cond_period) if job.cond_period else None,
            history_years=job.history_years,
            status=job.status,
            current_step=job.current_step,
            progress_done=job.progress_done,
            progress_total=job.progress_total,
            error_msg=job.error_msg,
        )


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------


@router.post("", response_model=JobResponse)
async def create_job(
    payload: JobCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> JobResponse:
    """조건 검증(Pydantic) → Job(PENDING) 생성 → 백그라운드로 STEP 1~6 실행 시작."""
    job = Job(
        created_at=datetime.now().isoformat(timespec="seconds"),
        name=payload.name,
        cond_region=json.dumps(payload.region.model_dump(), ensure_ascii=False),
        cond_revenue=json.dumps(payload.revenue.model_dump(), ensure_ascii=False),
        cond_industry=json.dumps(payload.industry, ensure_ascii=False),
        cond_period=json.dumps(payload.period.model_dump(), ensure_ascii=False),
        history_years=payload.history_years,
        status=JobStatus.PENDING,
        current_step=0,
        progress_done=0,
        progress_total=0,
        error_msg=None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(run_job, job.id)
    return JobResponse.from_job(job)


@router.get("", response_model=list[JobResponse])
async def list_jobs(db: Session = Depends(get_db)) -> list[JobResponse]:
    jobs = db.execute(select(Job).order_by(Job.id.desc())).scalars().all()
    return [JobResponse.from_job(j) for j in jobs]


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: int, db: Session = Depends(get_db)) -> JobResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")
    return JobResponse.from_job(job)


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(job_id: int, db: Session = Depends(get_db)) -> JobResponse:
    """실행 취소.

    이미 종료(DONE/CANCELLED)된 Job은 그대로 반환한다. 실행 중(RUNNING)인
    Job은 여기서 상태만 CANCELLED로 표시하고, 실제 중단은
    `pipeline.run_job()`이 다음 체크포인트에서 감지해 처리한다.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")
    if job.status not in (JobStatus.DONE, JobStatus.CANCELLED):
        job.status = JobStatus.CANCELLED
        db.commit()
        db.refresh(job)
    return JobResponse.from_job(job)


@router.post("/{job_id}/resume", response_model=JobResponse)
async def resume_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> JobResponse:
    """PAUSED_QUOTA(쿼터 초과) 또는 FAILED 상태 Job을 다시 실행한다."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")
    if job.status not in (JobStatus.PAUSED_QUOTA, JobStatus.FAILED):
        raise HTTPException(
            status_code=400,
            detail=f"resume 가능한 상태가 아닙니다 (현재 status={job.status}).",
        )
    job.status = JobStatus.PENDING
    db.commit()
    db.refresh(job)

    background_tasks.add_task(run_job, job.id)
    return JobResponse.from_job(job)


@router.post("/{job_id}/retry-failed", response_model=JobResponse)
async def retry_failed_results(
    job_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> JobResponse:
    """parse_status=FAILED인 results만 parse_status를 리셋하고 STEP 5를 재실행한다.

    STEP 1~4(후보 수집/필터/원문 다운로드)는 다시 하지 않는다 — 이미
    `results`/`DOCUMENT_CACHE_DIR`에 있는 데이터로 파싱만 재시도한다
    (`app/core/pipeline.py::retry_failed_parsing`).
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    failed_results = (
        db.execute(
            select(Result).where(Result.job_id == job_id, Result.parse_status == ParseStatus.FAILED)
        )
        .scalars()
        .all()
    )
    for result in failed_results:
        result.parse_status = None
        result.parse_note = None
    db.commit()

    background_tasks.add_task(retry_failed_parsing, job_id)
    return JobResponse.from_job(job)
