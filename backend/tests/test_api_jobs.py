"""app/api/jobs.py 라우터 테스트.

실제 파이프라인(app/core/pipeline.run_job_phase1/run_job_phase2)은 네트워크를
타므로, 여기서는 BackgroundTasks가 트리거하는 함수들을 스텁으로 치환해
라우팅/상태검증/검증 로직만 확인한다(파이프라인 자체 로직은
tests/test_pipeline.py에서 검증).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as app_main
from app.api import jobs as jobs_api
from app.core.db import get_db
from app.models import Base
from app.models.financial_snapshot import FinancialSnapshot
from app.models.job import Job, JobPhase, JobStatus
from app.models.result import Result


def _build_test_client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app_main.app.dependency_overrides[get_db] = _override_get_db

    # run_job_phase1/run_job_phase2/retry_failed_parsing은 네트워크/파일 IO를
    # 타므로 호출 여부만 기록하는 스텁으로 치환.
    calls: list[int] = []
    phase2_calls: list[int] = []

    async def _fake_run_job_phase1(job_id: int) -> None:
        calls.append(job_id)

    async def _fake_run_job_phase2(job_id: int) -> None:
        phase2_calls.append(job_id)

    monkeypatch.setattr(jobs_api, "run_job_phase1", _fake_run_job_phase1)
    monkeypatch.setattr(jobs_api, "run_job_phase2", _fake_run_job_phase2)
    monkeypatch.setattr(jobs_api, "retry_failed_parsing", _fake_run_job_phase1)

    client = TestClient(app_main.app)
    return client, calls, phase2_calls


def _sample_payload():
    return {
        "name": "김해 건설업 테스트",
        "region": {"sido": "경남", "sigungu": ["김해시"]},
        "revenue": {"min_krw": 6000000000, "max_krw": 15000000000},
        "industry": ["C25"],
        "period": {"bgn_de": "20260101", "end_de": "20260131"},
    }


def test_create_job_returns_pending_and_triggers_phase1_background_task(monkeypatch):
    client, calls, phase2_calls = _build_test_client(monkeypatch)
    try:
        resp = client.post("/api/jobs", json=_sample_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == JobStatus.PENDING
        assert body["phase"] == JobPhase.CANDIDATES
        # 시도는 다중 선택(리스트) + 시도별 시군구로 저장된다 — 구 평면 형태
        # (단일 문자열 sido + 평면 sigungu)도 하위호환으로 표준 형태로 변환된다.
        assert body["cond_region"] == {
            "sido": ["경남"],
            "sigungu_by_sido": {"경남": ["김해시"]},
        }
        assert body["cond_industry"] == ["C25"]
        assert body["cond_total_assets"] == {"min_krw": None, "max_krw": None}
        assert body["history_years"] == 4  # 미지정 시 기본값
        assert calls == [body["id"]]  # run_job_phase1만 트리거, run_job_phase2는 아직 아님
        assert phase2_calls == []
    finally:
        app_main.app.dependency_overrides.clear()


def test_create_job_accepts_total_assets_condition(monkeypatch):
    client, _calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        payload = _sample_payload()
        payload["total_assets"] = {"min_krw": 10_000_000_000, "max_krw": 30_000_000_000}
        resp = client.post("/api/jobs", json=payload)
        assert resp.status_code == 200
        assert resp.json()["cond_total_assets"] == {
            "min_krw": 10_000_000_000,
            "max_krw": 30_000_000_000,
        }
    finally:
        app_main.app.dependency_overrides.clear()


def test_create_job_accepts_custom_history_years(monkeypatch):
    client, _calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        payload = _sample_payload()
        payload["history_years"] = 10
        resp = client.post("/api/jobs", json=payload)
        assert resp.status_code == 200
        assert resp.json()["history_years"] == 10
    finally:
        app_main.app.dependency_overrides.clear()


def test_create_job_rejects_invalid_history_years(monkeypatch):
    client, _calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        payload = _sample_payload()
        payload["history_years"] = 3  # 짝수 옵션(2/4/6/10)이 아님
        resp = client.post("/api/jobs", json=payload)
        assert resp.status_code == 422
    finally:
        app_main.app.dependency_overrides.clear()


def test_create_job_rejects_sigungu_for_unselected_sido(monkeypatch):
    """시군구를 지정한 시도가 선택된 시도 목록에 없으면 422 — filter_local_candidates가
    sido 선필터(SQL IN) 없이 fsc_corp_index 전체를 메모리로 로드하는 것을 막는다."""
    client, _calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        payload = _sample_payload()
        payload["region"] = {
            "sido": ["경상남도"],
            "sigungu_by_sido": {"부산광역시": ["해운대구"]},
        }
        resp = client.post("/api/jobs", json=payload)
        assert resp.status_code == 422
    finally:
        app_main.app.dependency_overrides.clear()


def test_create_job_accepts_multiple_sido_with_per_sido_sigungu(monkeypatch):
    """시도 다중 선택 + 시도별 시군구가 그대로 저장된다(업종 대분류→중분류 구조)."""
    client, _calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        payload = _sample_payload()
        payload["region"] = {
            "sido": ["경상남도", "부산광역시"],
            "sigungu_by_sido": {"경상남도": ["김해시"], "부산광역시": []},
        }
        resp = client.post("/api/jobs", json=payload)
        assert resp.status_code == 200
        assert resp.json()["cond_region"] == {
            "sido": ["경상남도", "부산광역시"],
            "sigungu_by_sido": {"경상남도": ["김해시"], "부산광역시": []},
        }
    finally:
        app_main.app.dependency_overrides.clear()


def test_get_job_not_found_returns_404(monkeypatch):
    client, _calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        resp = client.get("/api/jobs/9999")
        assert resp.status_code == 404
    finally:
        app_main.app.dependency_overrides.clear()


def test_list_jobs_returns_created_jobs(monkeypatch):
    client, _calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        client.post("/api/jobs", json=_sample_payload())
        client.post("/api/jobs", json=_sample_payload())
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        assert len(resp.json()) == 2
    finally:
        app_main.app.dependency_overrides.clear()


def test_cancel_job_marks_cancelled(monkeypatch):
    client, _calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        created = client.post("/api/jobs", json=_sample_payload()).json()
        resp = client.post(f"/api/jobs/{created['id']}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == JobStatus.CANCELLED
    finally:
        app_main.app.dependency_overrides.clear()


def test_resume_requires_paused_or_failed_status(monkeypatch):
    client, _calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        created = client.post("/api/jobs", json=_sample_payload()).json()
        # 방금 생성된 Job은 PENDING이라 resume 불가능해야 한다.
        resp = client.post(f"/api/jobs/{created['id']}/resume")
        assert resp.status_code == 400
    finally:
        app_main.app.dependency_overrides.clear()


def test_resume_candidates_phase_retriggers_phase1(monkeypatch):
    """phase=CANDIDATES인 Job을 resume하면 run_job_phase1이 다시 호출돼야 한다."""
    client, calls, phase2_calls = _build_test_client(monkeypatch)
    try:
        created = client.post("/api/jobs", json=_sample_payload()).json()
        job_id = created["id"]

        with _direct_session() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.PAUSED_QUOTA
            # phase는 기본값 CANDIDATES 그대로 둔다.
            db.commit()

        resp = client.post(f"/api/jobs/{job_id}/resume")
        assert resp.status_code == 200
        assert resp.json()["status"] == JobStatus.PENDING
        assert calls == [job_id, job_id]  # create_job 1회 + resume(phase1) 1회
        assert phase2_calls == []
    finally:
        app_main.app.dependency_overrides.clear()


def test_resume_financials_phase_retriggers_phase2(monkeypatch):
    """phase=FINANCIALS인 Job을 resume하면 run_job_phase2가 호출돼야 한다."""
    client, calls, phase2_calls = _build_test_client(monkeypatch)
    try:
        created = client.post("/api/jobs", json=_sample_payload()).json()
        job_id = created["id"]

        with _direct_session() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.PAUSED_QUOTA
            job.phase = JobPhase.FINANCIALS
            db.commit()

        resp = client.post(f"/api/jobs/{job_id}/resume")
        assert resp.status_code == 200
        assert resp.json()["status"] == JobStatus.PENDING
        assert phase2_calls == [job_id]
        assert calls == [job_id]  # create_job 1회만(resume은 phase2 경로라 phase1 추가 호출 없음)
    finally:
        app_main.app.dependency_overrides.clear()


def test_retry_failed_triggers_background_reparse(monkeypatch):
    client, calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        created = client.post("/api/jobs", json=_sample_payload()).json()
        resp = client.post(f"/api/jobs/{created['id']}/retry-failed")
        assert resp.status_code == 200
        assert calls == [created["id"], created["id"]]  # create_job 1회 + retry-failed 1회
    finally:
        app_main.app.dependency_overrides.clear()


def test_retry_failed_not_found_returns_404(monkeypatch):
    client, _calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        resp = client.post("/api/jobs/9999/retry-failed")
        assert resp.status_code == 404
    finally:
        app_main.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /api/jobs/{id}/start-financials (§4-7-1, 2026-07-15 추가)
# ---------------------------------------------------------------------------


def _direct_session():
    """`app_main.app.dependency_overrides[get_db]`가 바인딩한 세션 팩토리를 직접 연다.

    이 테스트 파일은 매 테스트마다 새 인메모리 엔진을 만들므로, 오버라이드된
    제너레이터를 그대로 호출해 같은 엔진의 세션을 얻는다.
    """
    return next(app_main.app.dependency_overrides[get_db]())


def test_start_financials_requires_candidates_done(monkeypatch):
    """phase=CANDIDATES + status=DONE이 아니면 400."""
    client, _calls, phase2_calls = _build_test_client(monkeypatch)
    try:
        created = client.post("/api/jobs", json=_sample_payload()).json()
        job_id = created["id"]
        # 방금 생성된 Job은 아직 PENDING(run_job_phase1이 스텁이라 DONE으로
        # 전환되지 않음) — start-financials는 거부돼야 한다.
        resp = client.post(f"/api/jobs/{job_id}/start-financials", json={"history_years": 4})
        assert resp.status_code == 400
        assert phase2_calls == []
    finally:
        app_main.app.dependency_overrides.clear()


def test_start_financials_succeeds_when_candidates_done(monkeypatch):
    client, _calls, phase2_calls = _build_test_client(monkeypatch)
    try:
        created = client.post("/api/jobs", json=_sample_payload()).json()
        job_id = created["id"]

        with _direct_session() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.DONE
            job.phase = JobPhase.CANDIDATES
            db.commit()

        resp = client.post(f"/api/jobs/{job_id}/start-financials", json={"history_years": 6})
        assert resp.status_code == 200
        body = resp.json()
        assert body["phase"] == JobPhase.FINANCIALS
        assert body["status"] == JobStatus.PENDING
        assert body["history_years"] == 6
        assert phase2_calls == [job_id]
    finally:
        app_main.app.dependency_overrides.clear()


def test_start_financials_deletes_manually_excluded_results(monkeypatch):
    """excluded_manually=1로 표시된 후보는 Phase 2 시작 시점에 삭제되고, 나머지는 남는다."""
    client, _calls, phase2_calls = _build_test_client(monkeypatch)
    try:
        created = client.post("/api/jobs", json=_sample_payload()).json()
        job_id = created["id"]

        with _direct_session() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.DONE
            job.phase = JobPhase.CANDIDATES
            keep = Result(job_id=job_id, corp_code="00164742", corp_name="유지회사")
            drop = Result(job_id=job_id, corp_code="00126380", corp_name="제외회사", excluded_manually=1)
            db.add_all([keep, drop])
            db.commit()
            db.refresh(keep)
            db.refresh(drop)
            keep_id, drop_id = keep.id, drop.id

        resp = client.post(f"/api/jobs/{job_id}/start-financials", json={"history_years": 4})
        assert resp.status_code == 200

        with _direct_session() as db:
            assert db.get(Result, keep_id) is not None
            assert db.get(Result, drop_id) is None
        assert phase2_calls == [job_id]
    finally:
        app_main.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# DELETE /api/jobs/{id} (2026-07-18 추가)
# ---------------------------------------------------------------------------


def test_delete_job_removes_job_and_cascades_results_and_snapshots(monkeypatch):
    client, _calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        created = client.post("/api/jobs", json=_sample_payload()).json()
        job_id = created["id"]

        with _direct_session() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.DONE
            result = Result(job_id=job_id, corp_code="00164742", corp_name="테스트회사")
            db.add(result)
            db.commit()
            db.refresh(result)
            snapshot = FinancialSnapshot(result_id=result.id, fiscal_year="2025")
            db.add(snapshot)
            db.commit()
            result_id = result.id

        resp = client.delete(f"/api/jobs/{job_id}")
        assert resp.status_code == 204

        with _direct_session() as db:
            assert db.get(Job, job_id) is None
            assert db.get(Result, result_id) is None
            assert (
                db.query(FinancialSnapshot).filter(FinancialSnapshot.result_id == result_id).first() is None
            )
    finally:
        app_main.app.dependency_overrides.clear()


def test_delete_job_rejects_running_status(monkeypatch):
    client, _calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        created = client.post("/api/jobs", json=_sample_payload()).json()
        job_id = created["id"]

        with _direct_session() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.RUNNING
            db.commit()

        resp = client.delete(f"/api/jobs/{job_id}")
        assert resp.status_code == 400

        with _direct_session() as db:
            assert db.get(Job, job_id) is not None
    finally:
        app_main.app.dependency_overrides.clear()


def test_delete_job_rejects_pending_status(monkeypatch):
    """생성 직후(run_job_phase1이 스텁이라 여전히 PENDING)에는 삭제가 거부돼야 한다."""
    client, _calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        created = client.post("/api/jobs", json=_sample_payload()).json()
        resp = client.delete(f"/api/jobs/{created['id']}")
        assert resp.status_code == 400
    finally:
        app_main.app.dependency_overrides.clear()


def test_delete_job_not_found_returns_404(monkeypatch):
    client, _calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        resp = client.delete("/api/jobs/9999")
        assert resp.status_code == 404
    finally:
        app_main.app.dependency_overrides.clear()


def test_start_financials_rejects_invalid_history_years(monkeypatch):
    client, _calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        created = client.post("/api/jobs", json=_sample_payload()).json()
        job_id = created["id"]
        resp = client.post(f"/api/jobs/{job_id}/start-financials", json={"history_years": 3})
        assert resp.status_code == 422
    finally:
        app_main.app.dependency_overrides.clear()


def test_start_financials_not_found_returns_404(monkeypatch):
    client, _calls, _phase2_calls = _build_test_client(monkeypatch)
    try:
        resp = client.post("/api/jobs/9999/start-financials", json={"history_years": 4})
        assert resp.status_code == 404
    finally:
        app_main.app.dependency_overrides.clear()
