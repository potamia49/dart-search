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
    DELETE /api/jobs/{id}                 (2026-07-18 추가) 과거 Job 기록 삭제 (results/
                                           financial_snapshots 포함). RUNNING/PENDING은 거부.

**M6 재설계(2026-07-15)로 `POST /api/jobs`가 실행하는 백그라운드 작업이
구 `run_job()`(STEP 1~7 전체)에서 `run_job_phase1()`(후보 확정까지만)로
바뀌었다** (구 `run_job()`은 호출 경로가 끊긴 죽은 코드였고 2026-07-22에
물리 삭제됐다) — Job은 Phase 1 완료 시 `status=DONE`/`phase=CANDIDATES`로 멈추고,
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
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pipeline import retry_failed_parsing, run_job_phase1, run_job_phase2
from app.models.financial_snapshot import FinancialSnapshot
from app.models.job import Job, JobPhase, JobStatus
from app.models.result import ParseStatus, Result

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


# ---------------------------------------------------------------------------
# 요청/응답 스키마 (상세개발계획.md §5 jobs.cond_* JSON 형태 그대로)
# ---------------------------------------------------------------------------


class RegionCondition(BaseModel):
    """cond_region: 시도 다중 선택 + 시도별 시군구.

    표준 형태:
      {"sido": ["경상남도", "부산광역시"],
       "sigungu_by_sido": {"경상남도": ["김해시", "양산시"], "부산광역시": []}}
    시군구가 시도별로 그룹화되므로 여러 시도를 골라도 "중구"처럼 시도 간
    시군구명이 충돌하지 않는다(업종 대분류→중분류 선택과 동일한 구조).

    하위호환: 구 평면 형태(`{"sido": "경남", "sigungu": ["김해시"]}`,
    시도 1개)도 `_normalize_shape`가 표준 형태로 변환해 받아들인다 — 이미
    저장된 Job의 cond_region JSON과 문자열/평면 payload를 넘기는 기존 테스트가
    그대로 동작하도록 하기 위함.
    """

    sido: list[str] = Field(default_factory=list)
    sigungu_by_sido: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_shape(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        raw_sido = data.get("sido")
        if raw_sido is None:
            sido: list[str] = []
        elif isinstance(raw_sido, str):
            sido = [raw_sido] if raw_sido else []
        else:
            sido = list(raw_sido)
        sbs = data.get("sigungu_by_sido")
        if not (isinstance(sbs, dict) and sbs):
            # 구 평면 sigungu는 시도가 정확히 1개일 때만 그 시도로 흡수한다.
            flat = data.get("sigungu") or []
            sbs = {sido[0]: list(flat)} if flat and len(sido) == 1 else {}
        data["sido"] = sido
        data["sigungu_by_sido"] = sbs
        data.pop("sigungu", None)
        return data

    @model_validator(mode="after")
    def _sigungu_keys_subset_of_sido(self) -> "RegionCondition":
        # 시군구를 지정한 시도는 선택된 시도 목록에도 있어야 한다 — 시도 선필터
        # (`filter_local_candidates`의 SQL IN) 없이 시군구만 지정돼 fsc_corp_index
        # 전체(최대 약 63만 행)를 메모리로 로드하는 것을 막는다.
        for key in self.sigungu_by_sido:
            if key not in self.sido:
                raise ValueError(
                    "시군구를 지정한 시도는 시도 목록에도 포함되어야 합니다."
                )
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

    M6 재설계(§4-7-1) 이후 Phase 1(A2)은 dart_corp_index 로컬 인덱스 기반이라
    이 값을 사용하지 않는다 — 구 파이프라인(run_job, STEP 1~7)이 2026-07-22
    삭제되기 전 호환을 위해 optional로 남긴 컬럼이며, 지금은 아무도 쓰지 않지만
    요청 스키마 하위호환을 위해 필드 자체는 유지한다. Phase 1 전용
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
    Phase 1/2 오케스트레이터(`run_job_phase1`/`run_job_phase2`)가 다음
    체크포인트에서 감지해 처리한다.
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

    # "선택 취소"(excluded_manually)로 표시된 후보는 Phase 2 시작 시점에 일괄
    # 삭제한다 — 아직 원문을 연 적이 없어(phase=CANDIDATES) financial_snapshots가
    # 없으므로 results만 지우면 된다. Phase 2 파이프라인(B1~B5)은 남은 results만
    # 대상으로 그대로 동작하므로 수정할 필요가 없다.
    db.execute(delete(Result).where(Result.job_id == job_id, Result.excluded_manually == 1))
    db.commit()

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


@router.delete("/{job_id}", status_code=204, response_model=None)
async def delete_job(job_id: int, db: Session = Depends(get_db)) -> None:
    """과거 Job 기록을 삭제한다 (딸린 results/financial_snapshots까지 함께 삭제).

    RUNNING/PENDING인 Job은 백그라운드 태스크(run_job_phase1/2)가 여전히 이
    job_id를 참조하며 DB에 쓰는 중일 수 있어 삭제를 막는다 — 먼저 취소
    (`POST /api/jobs/{id}/cancel`)한 뒤 삭제해야 한다.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")
    if job.status in (JobStatus.RUNNING, JobStatus.PENDING):
        raise HTTPException(
            status_code=400,
            detail="실행 중인 작업은 삭제할 수 없습니다. 먼저 취소한 뒤 삭제하세요.",
        )

    result_ids = db.execute(select(Result.id).where(Result.job_id == job_id)).scalars().all()
    if result_ids:
        db.execute(delete(FinancialSnapshot).where(FinancialSnapshot.result_id.in_(result_ids)))
        db.execute(delete(Result).where(Result.job_id == job_id))
    db.delete(job)
    db.commit()
