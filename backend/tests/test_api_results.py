"""app/api/results.py 라우터 테스트 (결과 조회 + M4 export).

test_api_jobs.py와 동일한 패턴으로 dependency_override + 인메모리 SQLite를
사용한다. export는 파이프라인을 타지 않으므로 Job/Result를 세션에 직접
삽입해 준비한다.
"""

from __future__ import annotations

import io

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as app_main
from app.core.db import get_db
from app.models import Base
from app.models.financial_snapshot import FinancialSnapshot
from app.models.job import Job, JobStatus
from app.models.result import ParseStatus, Result


@pytest.fixture
def client_with_db():
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

    yield TestClient(app_main.app), factory

    app_main.app.dependency_overrides.clear()


def _seed_job_with_results(factory) -> int:
    db = factory()
    try:
        job = Job(
            created_at="2026-07-15T00:00:00",
            name="테스트 Job",
            cond_region='{"sido": "경남", "sigungu": ["김해시"]}',
            cond_revenue="{}",
            cond_industry="[]",
            cond_period='{"bgn_de": "20260101", "end_de": "20260131"}',
            status=JobStatus.DONE,
            current_step=6,
            progress_done=2,
            progress_total=2,
            error_msg=None,
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        db.add_all(
            [
                Result(
                    job_id=job.id,
                    corp_code="00100001",
                    rcept_no="20260601000001",
                    corp_name="㈜성공테스트",
                    address="경상남도 김해시 삼계로 1",
                    phone="055-000-0000",
                    ceo_name="홍길동",
                    induty_code="25",
                    induty_name="금속가공제품 제조업",
                    fiscal_date="20251231",
                    audit_opinion="적정",
                    revenue_cur=10_000_000_000,
                    revenue_prv=9_000_000_000,
                    parse_status=ParseStatus.OK,
                    parse_note=None,
                    excluded_by_revenue=0,
                ),
                Result(
                    job_id=job.id,
                    corp_code="00100002",
                    rcept_no="20260601000002",
                    corp_name="㈜실패테스트",
                    address="경상남도 김해시 분성로 2",
                    phone=None,
                    ceo_name="김철수",
                    induty_code="25",
                    induty_name="금속가공제품 제조업",
                    fiscal_date="20251231",
                    audit_opinion=None,
                    revenue_cur=None,
                    revenue_prv=None,
                    parse_status=ParseStatus.FAILED,
                    parse_note="XML 파싱 실패",
                    excluded_by_revenue=0,
                ),
            ]
        )
        db.commit()
        return job.id
    finally:
        db.close()


def test_list_results_returns_seeded_rows(client_with_db):
    client, factory = client_with_db
    job_id = _seed_job_with_results(factory)

    resp = client.get(f"/api/jobs/{job_id}/results")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


def test_list_results_not_found_returns_404(client_with_db):
    client, _factory = client_with_db
    resp = client.get("/api/jobs/9999/results")
    assert resp.status_code == 404


def test_set_result_excluded_toggles_flag(client_with_db):
    """CandidatesView "선택 취소" — phase=CANDIDATES(기본값)에서는 자유롭게 토글 가능."""
    client, factory = client_with_db
    job_id = _seed_job_with_results(factory)

    db = factory()
    try:
        result_id = db.execute(select(Result.id).where(Result.job_id == job_id)).scalars().first()
    finally:
        db.close()

    resp = client.patch(f"/api/jobs/{job_id}/results/{result_id}/exclude", json={"excluded": True})
    assert resp.status_code == 200
    assert resp.json()["excluded_manually"] == 1

    resp = client.patch(f"/api/jobs/{job_id}/results/{result_id}/exclude", json={"excluded": False})
    assert resp.status_code == 200
    assert resp.json()["excluded_manually"] == 0


def test_set_result_excluded_rejects_when_phase_financials(client_with_db):
    client, factory = client_with_db
    job_id = _seed_job_with_results(factory)

    db = factory()
    try:
        job = db.get(Job, job_id)
        job.phase = "FINANCIALS"
        db.commit()
        result_id = db.execute(select(Result.id).where(Result.job_id == job_id)).scalars().first()
    finally:
        db.close()

    resp = client.patch(f"/api/jobs/{job_id}/results/{result_id}/exclude", json={"excluded": True})
    assert resp.status_code == 400


def test_set_result_excluded_not_found_returns_404(client_with_db):
    client, factory = client_with_db
    job_id = _seed_job_with_results(factory)

    resp = client.patch(f"/api/jobs/{job_id}/results/999999/exclude", json={"excluded": True})
    assert resp.status_code == 404

    resp = client.patch("/api/jobs/9999/results/1/exclude", json={"excluded": True})
    assert resp.status_code == 404


def test_export_xlsx_returns_valid_workbook_with_korean_headers(client_with_db):
    client, factory = client_with_db
    job_id = _seed_job_with_results(factory)

    resp = client.get(f"/api/jobs/{job_id}/export", params={"format": "xlsx"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "attachment" in resp.headers["content-disposition"]

    wb = openpyxl.load_workbook(io.BytesIO(resp.content))
    ws = wb["results"]
    header_row = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    assert "회사명" in header_row
    assert "매출액(당기)" in header_row
    # 데이터 행 2개(헤더 제외) 확인
    assert ws.max_row == 3


def test_export_csv_has_utf8_bom_and_korean_headers(client_with_db):
    client, factory = client_with_db
    job_id = _seed_job_with_results(factory)

    resp = client.get(f"/api/jobs/{job_id}/export", params={"format": "csv"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "utf-8-sig" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]

    # utf-8-sig BOM이 실제로 포함되어 있는지 확인
    assert resp.content.startswith(b"\xef\xbb\xbf")
    text = resp.content.decode("utf-8-sig")
    assert "회사명" in text.splitlines()[0]
    assert "㈜성공테스트" in text


def test_export_invalid_format_returns_400(client_with_db):
    client, factory = client_with_db
    job_id = _seed_job_with_results(factory)

    resp = client.get(f"/api/jobs/{job_id}/export", params={"format": "pdf"})
    assert resp.status_code == 400


def test_export_not_found_returns_404(client_with_db):
    client, _factory = client_with_db
    resp = client.get("/api/jobs/9999/export", params={"format": "xlsx"})
    assert resp.status_code == 404


def test_export_filters_by_parse_status(client_with_db):
    client, factory = client_with_db
    job_id = _seed_job_with_results(factory)

    resp = client.get(
        f"/api/jobs/{job_id}/export",
        params={"format": "csv", "parse_status": "OK"},
    )
    assert resp.status_code == 200
    text = resp.content.decode("utf-8-sig")
    assert "㈜성공테스트" in text
    assert "㈜실패테스트" not in text


# ---------------------------------------------------------------------------
# GET /api/jobs/{id}/results/{result_id}/history — STEP 7(2026-07-15 추가)
# ---------------------------------------------------------------------------


def _get_result_id(factory, job_id: int, corp_code: str) -> int:
    db = factory()
    try:
        result = db.execute(
            select(Result).where(Result.job_id == job_id, Result.corp_code == corp_code)
        ).scalar_one()
        return result.id
    finally:
        db.close()


def test_get_result_history_returns_oldest_first(client_with_db):
    client, factory = client_with_db
    job_id = _seed_job_with_results(factory)
    result_id = _get_result_id(factory, job_id, "00100001")

    db = factory()
    try:
        db.add_all(
            [
                FinancialSnapshot(result_id=result_id, rcept_no="R2", fiscal_year="2025", revenue=10_000),
                FinancialSnapshot(result_id=result_id, rcept_no="R1", fiscal_year="2023", revenue=8_000),
                FinancialSnapshot(result_id=result_id, rcept_no="R1", fiscal_year="2024", revenue=9_000),
            ]
        )
        db.commit()
    finally:
        db.close()

    resp = client.get(f"/api/jobs/{job_id}/results/{result_id}/history")
    assert resp.status_code == 200
    body = resp.json()
    assert [row["fiscal_year"] for row in body] == ["2023", "2024", "2025"]  # 오래된 -> 최신 순
    assert body[0]["revenue"] == 8_000


def test_get_result_history_empty_when_no_snapshots(client_with_db):
    client, factory = client_with_db
    job_id = _seed_job_with_results(factory)
    result_id = _get_result_id(factory, job_id, "00100001")

    resp = client.get(f"/api/jobs/{job_id}/results/{result_id}/history")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_result_history_job_not_found_returns_404(client_with_db):
    client, _factory = client_with_db
    resp = client.get("/api/jobs/9999/results/1/history")
    assert resp.status_code == 404


def test_get_result_history_result_not_found_returns_404(client_with_db):
    client, factory = client_with_db
    job_id = _seed_job_with_results(factory)

    resp = client.get(f"/api/jobs/{job_id}/results/9999/history")
    assert resp.status_code == 404


def test_get_result_history_rejects_result_from_other_job(client_with_db):
    """result_id는 존재하지만 다른 job에 속하면 404 (job_id-result_id 불일치)."""
    client, factory = client_with_db
    job_id_1 = _seed_job_with_results(factory)
    job_id_2 = _seed_job_with_results(factory)
    result_id_in_job1 = _get_result_id(factory, job_id_1, "00100001")

    resp = client.get(f"/api/jobs/{job_id_2}/results/{result_id_in_job1}/history")
    assert resp.status_code == 404
