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
                    auditor_name="안경회계법인",
                    auditor_address="경상남도 창원시 중앙대로 1",
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


def test_list_results_sorts_by_column_and_pushes_missing_values_last(client_with_db):
    """매출액 오름차순 정렬 — 값이 없는 행(파싱 실패)은 방향과 무관하게 항상 뒤로."""
    client, factory = client_with_db
    job_id = _seed_job_with_results(factory)

    asc = client.get(
        f"/api/jobs/{job_id}/results", params={"sort_by": "revenue_cur", "sort_dir": "asc"}
    ).json()
    assert [r["corp_name"] for r in asc["items"]] == ["㈜성공테스트", "㈜실패테스트"]

    desc = client.get(
        f"/api/jobs/{job_id}/results", params={"sort_by": "revenue_cur", "sort_dir": "desc"}
    ).json()
    # 내림차순이어도 revenue_cur=None인 ㈜실패테스트가 앞으로 오면 안 된다.
    assert [r["corp_name"] for r in desc["items"]] == ["㈜성공테스트", "㈜실패테스트"]


def test_list_results_rejects_unknown_sort_column(client_with_db):
    """화이트리스트 밖의 컬럼명은 무시하고 기본 정렬로 되돌린다(500이 아니라 200)."""
    client, factory = client_with_db
    job_id = _seed_job_with_results(factory)

    resp = client.get(f"/api/jobs/{job_id}/results", params={"sort_by": "id; DROP TABLE results"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


def test_list_results_filters_by_keyword_including_auditor(client_with_db):
    client, factory = client_with_db
    job_id = _seed_job_with_results(factory)

    by_name = client.get(f"/api/jobs/{job_id}/results", params={"q": "성공"}).json()
    assert [r["corp_name"] for r in by_name["items"]] == ["㈜성공테스트"]

    # 감사인명도 검색 대상이다 — "안경회계법인이 감사한 회사만" 추리는 용도.
    by_auditor = client.get(f"/api/jobs/{job_id}/results", params={"q": "안경회계"}).json()
    assert by_auditor["total"] == 1
    assert by_auditor["items"][0]["auditor_name"] == "안경회계법인"
    assert by_auditor["items"][0]["auditor_address"] == "경상남도 창원시 중앙대로 1"

    assert client.get(f"/api/jobs/{job_id}/results", params={"q": "없는회사"}).json()["total"] == 0


def test_list_results_splits_failed_by_has_disclosure(client_with_db):
    """FAILED 중 "파싱 실패"(rcept_no 있음)와 "감사보고서 없음"(rcept_no 없음)을
    `has_disclosure`로 구분할 수 있어야 한다(2026-07-20 추가)."""
    client, factory = client_with_db
    job_id = _seed_job_with_results(factory)

    db = factory()
    try:
        db.add(
            Result(
                job_id=job_id,
                corp_code="00100003",
                rcept_no=None,
                corp_name="㈜공시없음테스트",
                parse_status=ParseStatus.FAILED,
                parse_note="최근 감사보고서 공시를 찾을 수 없음(Phase 1 추정치만 존재)",
                excluded_by_revenue=0,
            )
        )
        db.commit()
    finally:
        db.close()

    all_failed = client.get(f"/api/jobs/{job_id}/results", params={"parse_status": "FAILED"})
    assert all_failed.json()["total"] == 2

    to_review = client.get(
        f"/api/jobs/{job_id}/results",
        params={"parse_status": "FAILED", "has_disclosure": True},
    )
    assert [r["corp_name"] for r in to_review.json()["items"]] == ["㈜실패테스트"]

    no_disclosure = client.get(
        f"/api/jobs/{job_id}/results",
        params={"parse_status": "FAILED", "has_disclosure": False},
    )
    assert [r["corp_name"] for r in no_disclosure.json()["items"]] == ["㈜공시없음테스트"]


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


# ---------------------------------------------------------------------------
# §4-8 원문 섹션 열람 API (document-sections)
# ---------------------------------------------------------------------------

import shutil  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _seed_result_with_rcept(factory, rcept_no: str) -> tuple[int, int]:
    """rcept_no를 가진 결과 1건을 seed하고 (job_id, result_id)를 반환."""
    db = factory()
    try:
        job = Job(
            created_at="2026-07-19T00:00:00",
            name="원문열람 테스트",
            cond_region="{}",
            cond_revenue="{}",
            cond_industry="[]",
            cond_period="{}",
            status=JobStatus.DONE,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        result = Result(job_id=job.id, corp_code="00100001", rcept_no=rcept_no, corp_name="㈜원문")
        db.add(result)
        db.commit()
        db.refresh(result)
        return job.id, result.id
    finally:
        db.close()


def _point_cache_at_tmp(monkeypatch, tmp_path, rcept_no: str, fixture_id: str) -> None:
    """DOCUMENT_CACHE_DIR을 tmp로 돌리고 fixture XML을 {tmp}/{rcept_no}/에 복사."""
    target = tmp_path / rcept_no
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(_FIXTURES_DIR / fixture_id / f"{fixture_id}_00760.xml", target / "document.xml")
    monkeypatch.setattr(
        "app.api.results.get_settings",
        lambda: SimpleNamespace(document_cache_dir=str(tmp_path)),
    )


def test_document_section_returns_assembled_html(client_with_db, monkeypatch, tmp_path):
    client, factory = client_with_db
    job_id, result_id = _seed_result_with_rcept(factory, "20260630000641")
    _point_cache_at_tmp(monkeypatch, tmp_path, "20260630000641", "20260630000641")

    resp = client.get(f"/api/jobs/{job_id}/results/{result_id}/document-sections/cf")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["notice"] is None
    assert "<table>" in body["html"]
    assert "현" in body["html"]  # 현금흐름표 제목/내용


@pytest.mark.parametrize(
    "fixture_id, expected_phrase",
    [
        # 신서식 — <TITLE>독립된 감사인의 감사보고서</TITLE>, 적정의견
        ("20260630000641", "공정하게"),
        # 2012년 구서식 — <TITLE>외부감사인의 감사보고서</TITLE>, "적정하게" 문구
        ("20120110000138", "적정하게"),
    ],
)
def test_document_section_audit_covers_both_report_title_formats(
    client_with_db, monkeypatch, tmp_path, fixture_id, expected_phrase
):
    """감사의견 탭(section=audit)은 신서식("독립된 감사인의...")과 2012년
    구서식("외부감사인의...")을 공통 부분문자열 "감사보고서"로 모두 잡는다."""
    client, factory = client_with_db
    job_id, result_id = _seed_result_with_rcept(factory, fixture_id)
    _point_cache_at_tmp(monkeypatch, tmp_path, fixture_id, fixture_id)

    resp = client.get(f"/api/jobs/{job_id}/results/{result_id}/document-sections/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert "감사보고서" in body["html"]
    assert expected_phrase in body["html"]


def test_document_section_renders_te_data_cells(client_with_db, monkeypatch, tmp_path):
    """재무제표 데이터 셀은 TD가 아니라 <TE> 태그다 — 이를 셀로 처리하지 않으면
    계정과목/금액이 전부 빈 <tr></tr>로 렌더된다(§4-8 회귀). 실제 금액 값과
    계정과목이 HTML에 담기는지, 빈 행이 없는지 검증한다."""
    import re

    client, factory = client_with_db
    job_id, result_id = _seed_result_with_rcept(factory, "20260630000641")
    _point_cache_at_tmp(monkeypatch, tmp_path, "20260630000641", "20260630000641")

    resp = client.get(f"/api/jobs/{job_id}/results/{result_id}/document-sections/bs")
    assert resp.status_code == 200
    html = resp.json()["html"]
    # 금액 셀(1,234,567 형태)이 실제로 담겨 있어야 한다.
    assert len(re.findall(r"[0-9]{1,3}(?:,[0-9]{3})+", html)) > 10
    # 데이터 행이 빈 <tr></tr>로 렌더되면 안 된다.
    assert "<tr></tr>" not in html
    assert "자산총계" in html.replace(" ", "")


def test_account_detail_returns_children_per_summary_field(client_with_db, monkeypatch, tmp_path):
    """요약 대분류(유동자산 등)별 세부계정이 계층/값과 함께 반환되는지 검증."""
    client, factory = client_with_db
    job_id, result_id = _seed_result_with_rcept(factory, "20260630000641")
    _point_cache_at_tmp(monkeypatch, tmp_path, "20260630000641", "20260630000641")

    resp = client.get(f"/api/jobs/{job_id}/results/{result_id}/account-detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["rcept_no"] == "20260630000641"
    assert body["fiscal_year_cur"] == "2026"  # 이력 표의 당기/전기 열 판정 근거

    rows = body["accounts"]["current_assets"]
    assert len(rows) > 5
    # 세부계정은 라벨/레벨/당기·전기 값을 갖는다.
    assert all(row["level"] >= 1 for row in rows)
    assert any(row["cur"] is not None for row in rows)
    # 총계 항목은 하위가 형제 대분류라 children이 비어 있다(토글 비활성 대상).
    assert body["accounts"]["total_assets"] == []


def test_account_detail_rejects_foreign_rcept_no(client_with_db, monkeypatch, tmp_path):
    client, factory = client_with_db
    job_id, result_id = _seed_result_with_rcept(factory, "20260630000641")
    _point_cache_at_tmp(monkeypatch, tmp_path, "20260630000641", "20260630000641")

    resp = client.get(
        f"/api/jobs/{job_id}/results/{result_id}/account-detail?rcept_no=19990101000001"
    )
    assert resp.status_code == 404


def test_account_detail_returns_cash_flow_children_and_audit_opinion(
    client_with_db, monkeypatch, tmp_path
):
    """현금흐름표 3항목(영업/투자/재무활동)도 재무상태표·손익계산서와 동일하게
    세부계정이 반환되고("기말의현금"은 총계라 children이 비어 있는 게 정상), 감사의견도
    함께 내려간다(재무상태표 위 안내 행에 쓴다)."""
    client, factory = client_with_db
    job_id, result_id = _seed_result_with_rcept(factory, "20260630000641")
    _point_cache_at_tmp(monkeypatch, tmp_path, "20260630000641", "20260630000641")

    resp = client.get(f"/api/jobs/{job_id}/results/{result_id}/account-detail")
    assert resp.status_code == 200
    body = resp.json()

    assert body["audit_opinion"] == "적정"

    operating_rows = body["accounts"]["cf_operating"]
    assert len(operating_rows) > 3
    assert all(row["level"] >= 1 for row in operating_rows)
    assert any(row["cur"] is not None for row in operating_rows)

    investing_rows = body["accounts"]["cf_investing"]
    assert len(investing_rows) > 3

    financing_rows = body["accounts"]["cf_financing"]
    assert len(financing_rows) > 3

    # 기말의현금은 그 자체가 총계라 하위 대분류가 없다(자산총계 등과 동일 패턴).
    assert body["accounts"].get("cf_ending_cash", []) == []


def test_document_section_invalid_section_returns_400(client_with_db, monkeypatch, tmp_path):
    client, factory = client_with_db
    job_id, result_id = _seed_result_with_rcept(factory, "20260630000641")
    _point_cache_at_tmp(monkeypatch, tmp_path, "20260630000641", "20260630000641")

    resp = client.get(f"/api/jobs/{job_id}/results/{result_id}/document-sections/xxx")
    assert resp.status_code == 400


def test_document_section_cache_missing_returns_404(client_with_db, monkeypatch, tmp_path):
    client, factory = client_with_db
    job_id, result_id = _seed_result_with_rcept(factory, "20260630000641")
    # 캐시 디렉터리를 비운 채(파일 복사 없이) 조회 → 404
    monkeypatch.setattr(
        "app.api.results.get_settings",
        lambda: SimpleNamespace(document_cache_dir=str(tmp_path)),
    )
    resp = client.get(f"/api/jobs/{job_id}/results/{result_id}/document-sections/cf")
    assert resp.status_code == 404


def test_document_section_absent_section_returns_notice(client_with_db, monkeypatch, tmp_path):
    """재무제표 미첨부(의견거절 계열) 원문의 cf는 에러가 아니라 available=false + 안내."""
    client, factory = client_with_db
    job_id, result_id = _seed_result_with_rcept(factory, "20260630001111")
    _point_cache_at_tmp(monkeypatch, tmp_path, "20260630001111", "20260630001111")

    resp = client.get(f"/api/jobs/{job_id}/results/{result_id}/document-sections/cf")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["notice"]  # 안내 문구 존재
    assert body["html"] == ""


def test_document_section_rejects_foreign_rcept_no(client_with_db, monkeypatch, tmp_path):
    """?rcept_no=가 이 결과에 속하지 않으면 404 (history 공시가 아닌 임의 값 거부)."""
    client, factory = client_with_db
    job_id, result_id = _seed_result_with_rcept(factory, "20260630000641")
    _point_cache_at_tmp(monkeypatch, tmp_path, "20260630000641", "20260630000641")

    resp = client.get(
        f"/api/jobs/{job_id}/results/{result_id}/document-sections/cf",
        params={"rcept_no": "99999999999999"},
    )
    assert resp.status_code == 404


def test_document_section_allows_history_rcept_no(client_with_db, monkeypatch, tmp_path):
    """?rcept_no=가 이 결과의 financial_snapshots 공시면 허용된다."""
    client, factory = client_with_db
    job_id, result_id = _seed_result_with_rcept(factory, "20260630000641")
    # 이력 공시로 다른 rcept_no를 등록하고 그 원문을 캐시에 둔다.
    db = factory()
    try:
        db.add(
            FinancialSnapshot(
                result_id=result_id, rcept_no="20260630000665", fiscal_year="2024", revenue=1
            )
        )
        db.commit()
    finally:
        db.close()
    tgt = tmp_path / "20260630000665"
    tgt.mkdir(parents=True)
    shutil.copy(
        _FIXTURES_DIR / "20260630000665" / "20260630000665_00760.xml", tgt / "document.xml"
    )
    monkeypatch.setattr(
        "app.api.results.get_settings",
        lambda: SimpleNamespace(document_cache_dir=str(tmp_path)),
    )

    resp = client.get(
        f"/api/jobs/{job_id}/results/{result_id}/document-sections/cf",
        params={"rcept_no": "20260630000665"},
    )
    assert resp.status_code == 200
    assert resp.json()["available"] is True
