"""app/api/jobs.py 라우터 테스트.

실제 파이프라인(app/core/pipeline.run_job)은 네트워크를 타므로, 여기서는
BackgroundTasks가 트리거하는 `run_job`을 스텁으로 치환해 라우팅/상태검증/
검증 로직만 확인한다 (파이프라인 자체 로직은 tests/test_pipeline.py에서 검증).
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
from app.models.job import JobStatus


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

    # run_job/retry_failed_parsing은 네트워크/파일 IO를 타므로 호출 여부만
    # 기록하는 스텁으로 치환.
    calls: list[int] = []

    async def _fake_run_job(job_id: int) -> None:
        calls.append(job_id)

    monkeypatch.setattr(jobs_api, "run_job", _fake_run_job)
    monkeypatch.setattr(jobs_api, "retry_failed_parsing", _fake_run_job)

    client = TestClient(app_main.app)
    return client, calls


def _sample_payload():
    return {
        "name": "김해 건설업 테스트",
        "region": {"sido": "경남", "sigungu": ["김해시"]},
        "revenue": {"min_krw": 6000000000, "max_krw": 15000000000},
        "industry": ["C25"],
        "period": {"bgn_de": "20260101", "end_de": "20260131"},
    }


def test_create_job_returns_pending_and_triggers_background_task(monkeypatch):
    client, calls = _build_test_client(monkeypatch)
    try:
        resp = client.post("/api/jobs", json=_sample_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == JobStatus.PENDING
        assert body["cond_region"] == {"sido": "경남", "sigungu": ["김해시"]}
        assert body["cond_industry"] == ["C25"]
        assert body["history_years"] == 4  # 미지정 시 기본값
        assert calls == [body["id"]]
    finally:
        app_main.app.dependency_overrides.clear()


def test_create_job_accepts_custom_history_years(monkeypatch):
    client, _calls = _build_test_client(monkeypatch)
    try:
        payload = _sample_payload()
        payload["history_years"] = 10
        resp = client.post("/api/jobs", json=payload)
        assert resp.status_code == 200
        assert resp.json()["history_years"] == 10
    finally:
        app_main.app.dependency_overrides.clear()


def test_create_job_rejects_invalid_history_years(monkeypatch):
    client, _calls = _build_test_client(monkeypatch)
    try:
        payload = _sample_payload()
        payload["history_years"] = 3  # 짝수 옵션(2/4/6/10)이 아님
        resp = client.post("/api/jobs", json=payload)
        assert resp.status_code == 422
    finally:
        app_main.app.dependency_overrides.clear()


def test_get_job_not_found_returns_404(monkeypatch):
    client, _calls = _build_test_client(monkeypatch)
    try:
        resp = client.get("/api/jobs/9999")
        assert resp.status_code == 404
    finally:
        app_main.app.dependency_overrides.clear()


def test_list_jobs_returns_created_jobs(monkeypatch):
    client, _calls = _build_test_client(monkeypatch)
    try:
        client.post("/api/jobs", json=_sample_payload())
        client.post("/api/jobs", json=_sample_payload())
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        assert len(resp.json()) == 2
    finally:
        app_main.app.dependency_overrides.clear()


def test_cancel_job_marks_cancelled(monkeypatch):
    client, _calls = _build_test_client(monkeypatch)
    try:
        created = client.post("/api/jobs", json=_sample_payload()).json()
        resp = client.post(f"/api/jobs/{created['id']}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == JobStatus.CANCELLED
    finally:
        app_main.app.dependency_overrides.clear()


def test_resume_requires_paused_or_failed_status(monkeypatch):
    client, calls = _build_test_client(monkeypatch)
    try:
        created = client.post("/api/jobs", json=_sample_payload()).json()
        # 방금 생성된 Job은 PENDING이라 resume 불가능해야 한다.
        resp = client.post(f"/api/jobs/{created['id']}/resume")
        assert resp.status_code == 400
    finally:
        app_main.app.dependency_overrides.clear()


def test_retry_failed_triggers_background_reparse(monkeypatch):
    client, calls = _build_test_client(monkeypatch)
    try:
        created = client.post("/api/jobs", json=_sample_payload()).json()
        resp = client.post(f"/api/jobs/{created['id']}/retry-failed")
        assert resp.status_code == 200
        assert calls == [created["id"], created["id"]]  # create_job 1회 + retry-failed 1회
    finally:
        app_main.app.dependency_overrides.clear()


def test_retry_failed_not_found_returns_404(monkeypatch):
    client, _calls = _build_test_client(monkeypatch)
    try:
        resp = client.post("/api/jobs/9999/retry-failed")
        assert resp.status_code == 404
    finally:
        app_main.app.dependency_overrides.clear()
