"""Job 생성/조회/취소/재시도 API.

상세개발계획.md §6, §8:
    POST /api/jobs                        조건 입력 → Job 생성 + 백그라운드로 Phase 1(A2~A4) 실행
    GET  /api/jobs                        Job 목록 (상태/진행률/phase 포함)
    GET  /api/jobs/{id}                   Job 상세 (프론트가 2초 간격 폴링)
    POST /api/jobs/{id}/cancel            실행 취소
    POST /api/jobs/{id}/resume            중단(쿼터/오류) Job 이어하기 (phase에 따라 Phase1/2 재실행)
    POST /api/jobs/{id}/retry-failed      파싱 실패 건만 재시도
    POST /api/jobs/{id}/start-financials  (§4-7-1, 2026-07-15 추가) Phase 1 확정 후보에 대해
                                           Phase 2(B1~B5, 재무정보 수집) 시작

**M6 재설계(2026-07-15)로 `POST /api/jobs`가 실행하는 백그라운드 작업이
`run_job()`(구 STEP 1~7 전체)에서 `run_job_phase1()`(A2~A4, 후보 확정까지만)로
바뀌었다** — Job은 Phase 1 완료 시 `status=DONE`/`phase=CANDIDATES`로 멈추고,
사용자가 `POST /api/jobs/{id}/start-financials`를 명시적으로 호출해야
Phase 2(`run_job_phase2`, 구 STEP 4~7 재사용)가 시작된다(§4-7-1). 이 라우터는
FastAPI `BackgroundTasks`로 `app/core/pipeline.py`의 이 함수들을 트리거/조회/
취소만 한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pipeline import retry_failed_parsing, run_job_phase1, run_job_phase2
from app.models.job import Job, JobPhase, JobStatus
from app.models.result import ParseStatus, Result

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


# ---------------------------------------------------------------------------
# 요청/응답 스키마 (상세개발계획.md §5 jobs.cond_* JSON 형태 그대로)
# ---------------------------------------------------------------------------


class RegionCondition(BaseModel):
    """cond_region: {"sido": "경남", "sigungu": ["김해시", "양산시"]}"""

    sido: str | None = None
    sigungu: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sigungu_requires_sido(self) -> "RegionCondition":
        # Phase 1 A2(`app/core/fsc_index.py::filter_local_candidates`)는 sido가
        # 있어야만 SQL WHERE로 fsc_corp_index(최대 약 128만 행)를 먼저 좁힌다 —
        # sigungu만 있고 sido가 없으면 전체 인덱스를 메모리로 로드하게 되어
        # OOM/장시간 정지 위험이 있다. 프론트는 항상 sido와 함께 보내지만
        # API를 직접 호출하는 경로까지 막기 위해 여기서 거부한다.
        if self.sigungu and not self.sido:
            raise ValueError("sigungu를 지정하려면 sido도 함께 지정해야 합니다.")
        return self


class RevenueCondition(BaseModel):
    """cond_revenue: {"min_krw": 6000000000, "max_krw": 15000000000} — M3 매출액 필터에서 사용."""

    min_krw: int | None = None
    max_krw: int | None = None


class TotalAssetsCondition(BaseModel):
    """cond_total_assets: {"min_krw": ..., "max_krw": ...} — §4-7-2(2026-07-15 추가)
    총자산 필터. RevenueCondition과 완전히 동일한 스키마이며, 미입력 시 무제한이다.
    """

    min_krw: int | None = None
    max_krw: int | None = None


class PeriodCondition(BaseModel):
    """cond_period: {"bgn_de": "20250101", "end_de": "20251231"} — STEP 2 list.json 파라미터.

    M6 재설계(§4-7-1) 이후 Phase 1(A2~A4)은 FSC 전역 인덱스 스냅샷 기반이라
    이 값을 사용하지 않는다 — 구 파이프라인(run_job, STEP 1~7, 현재 API에서는
    호출되지 않고 단위 테스트에서만 직접 호출됨) 호환을 위해 컬럼은 유지하되,
    2026-07-15 이 필드를 요청 스키마에서 optional로 바꿨다. Phase 1 전용
    엔드포인트에 강제로 채워야 할 이유가 없는 필드를 required로 두면 프론트가
    의미 없는 더미 날짜를 만들어 보내야 하는 문제가 있었다(SearchPage에서
    "공시 대상 기간" 입력 자체를 제거했으므로).
    """

    bgn_de: str
    end_de: str


class JobCreateRequest(BaseModel):
    name: str | None = None
    region: RegionCondition = Field(default_factory=RegionCondition)
    revenue: RevenueCondition = Field(default_factory=RevenueCondition)
    total_assets: TotalAssetsCondition = Field(default_factory=TotalAssetsCondition)
    industry: list[str] = Field(default_factory=list)
    period: PeriodCondition | None = None
    # STEP 7(최근 N년 재무이력, 2026-07-15 추가) 목표 연도수. 감사보고서 1건이
    # 당기·전기 2개년을 비교식으로 담기 때문에 짝수만 허용한다(상세개발계획.md
    # §4-6). M6 재설계 이후에는 Job 생성 시점이 아니라 start-financials 호출
    # 시점에 실제로 쓰인다(§4-7-1) — 다만 기존 계약을 유지하기 위해 요청
    # 스키마 필드 자체는 그대로 둔다.
    history_years: Literal[2, 4, 6, 10] = 4


class JobResponse(BaseModel):
    id: int
    created_at: str | None
    name: str | None
    cond_region: dict[str, Any] | None
    cond_revenue: dict[str, Any] | None
    cond_total_assets: dict[str, Any] | None
    cond_industry: list[str] | None
    cond_period: dict[str, Any] | None
    history_years: int | None
    status: str | None
    phase: str | None
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
            cond_total_assets=json.loads(job.cond_total_assets) if job.cond_total_assets else None,
            cond_industry=json.loads(job.cond_industry) if job.cond_industry else None,
            cond_period=json.loads(job.cond_period) if job.cond_period else None,
            history_years=job.history_years,
            status=job.status,
            phase=job.phase,
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
    """조건 검증(Pydantic) → Job(PENDING) 생성 → 백그라운드로 Phase 1(A2~A4) 실행 시작.

    M6 재설계(§4-7-1)로 여기서는 후보 확정(Phase 1)까지만 실행하고 멈춘다 —
    재무정보 수집(Phase 2)은 `POST /api/jobs/{id}/start-financials`를 사용자가
    별도로 호출해야 시작된다.
    """
    job = Job(
        created_at=datetime.now().isoformat(timespec="seconds"),
        name=payload.name,
        cond_region=json.dumps(payload.region.model_dump(), ensure_ascii=False),
        cond_revenue=json.dumps(payload.revenue.model_dump(), ensure_ascii=False),
        cond_total_assets=json.dumps(payload.total_assets.model_dump(), ensure_ascii=False),
        cond_industry=json.dumps(payload.industry, ensure_ascii=False),
        cond_period=json.dumps(payload.period.model_dump(), ensure_ascii=False) if payload.period else None,
        history_years=payload.history_years,
        status=JobStatus.PENDING,
        phase=JobPhase.CANDIDATES,
        current_step=0,
        progress_done=0,
        progress_total=0,
        error_msg=None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(run_job_phase1, job.id)
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
    """PAUSED_QUOTA(쿼터 초과) 또는 FAILED 상태 Job을 다시 실행한다.

    `job.phase`에 따라 이어서 실행할 함수가 다르다 — `FINANCIALS`면 Phase 2
    (`run_job_phase2`), 그 외(`CANDIDATES`)면 Phase 1(`run_job_phase1`)을
    재실행한다(§4-7-1).

    `start_financials`와 동일하게 상태 확인+전환을 조건부 `UPDATE` 하나로
    묶어 거의 동시에 들어온 중복 resume 요청 중 하나만 실제로 백그라운드
    태스크를 큐잉하도록 한다.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")
    phase = job.phase

    result = db.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status.in_((JobStatus.PAUSED_QUOTA, JobStatus.FAILED)),
        )
        .values(status=JobStatus.PENDING)
    )
    db.commit()

    job = db.get(Job, job_id)
    if result.rowcount == 0:
        raise HTTPException(
            status_code=400,
            detail=f"resume 가능한 상태가 아닙니다 (현재 status={job.status}).",
        )

    if phase == JobPhase.FINANCIALS:
        background_tasks.add_task(run_job_phase2, job.id)
    else:
        background_tasks.add_task(run_job_phase1, job.id)
    return JobResponse.from_job(job)


class StartFinancialsRequest(BaseModel):
    """POST /api/jobs/{id}/start-financials 요청 바디 (§4-7-1, 2026-07-15 추가)."""

    history_years: Literal[2, 4, 6, 10] = 4


@router.post("/{job_id}/start-financials", response_model=JobResponse)
async def start_financials(
    job_id: int,
    payload: StartFinancialsRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> JobResponse:
    """Phase 1이 확정한 후보(`phase=CANDIDATES`+`status=DONE`)에 대해 Phase 2(B1~B5)를 시작한다.

    사용자가 후보 목록(ResultPage "후보 목록" 뷰)을 검토한 뒤 수집기간(2/4/6/10년)을
    선택해 호출한다 — 그 외 상태의 Job은 400으로 거부한다(§4-7-1).

    상태 확인과 전환을 단일 조건부 `UPDATE`로 묶어 원자적으로 처리한다 —
    버튼 연타 등으로 거의 동시에 두 요청이 들어와도 `read(status==DONE 확인) →
    write(FINANCIALS로 전환)` 사이에 경쟁이 끼어들 여지가 없다(둘 중 하나만
    `rowcount==1`을 받고, 다른 하나는 이미 바뀐 상태를 보고 400을 받는다).
    """
    result = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.phase == JobPhase.CANDIDATES, Job.status == JobStatus.DONE)
        .values(
            history_years=payload.history_years,
            phase=JobPhase.FINANCIALS,
            status=JobStatus.PENDING,
        )
    )
    db.commit()

    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")
    if result.rowcount == 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "재무정보 수집을 시작할 수 없는 상태입니다 "
                f"(phase={job.phase}, status={job.status}) — "
                "phase=CANDIDATES이고 status=DONE인 Job만 가능합니다."
            ),
        )

    background_tasks.add_task(run_job_phase2, job.id)
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
    (`app/core/pipeline.py::retry_failed_parsing`). `rcept_no`가 없는 FAILED건
    (Phase 2 `_backfill_latest_rcept_no_for_job`이 감사보고서 공시 자체를 못
    찾은 경우 — 열어볼 원문이 아예 없음)은 리셋 대상에서 제외한다 — 포함하면
    `_run_financial_parsing`이 `rcept_no IS NOT NULL` 조건 때문에 그 건을
    다시 열지 않아 parse_status만 NULL로 되돌아간 채 영영 갇히게 된다.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    failed_results = (
        db.execute(
            select(Result).where(
                Result.job_id == job_id,
                Result.parse_status == ParseStatus.FAILED,
                Result.rcept_no.is_not(None),
            )
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
