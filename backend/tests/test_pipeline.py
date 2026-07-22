"""app/core/pipeline.py 단위 테스트.

CLAUDE.md 지침대로 실제 DART 호출 없이 `DartClient`를 모킹해 STEP 2/3/4
로직과 Job 상태 전이(특히 QuotaExceededError -> PAUSED_QUOTA)를 검증한다.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import Settings
from app.core import pipeline
from app.core.dart_client import DartApiError, QuotaExceededError
from app.models.corp_cache import CorpCache
from app.models.financial_snapshot import FinancialSnapshot
from app.models.dart_corp_index import DartCorpIndex
from app.models.fsc_financial_stat import FscFinancialStat
from app.models.job import Job, JobStatus
from app.models.result import ParseStatus, Result
from app.parsers.base import ParsedFinancials

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# 테스트 더블
# ---------------------------------------------------------------------------


class FakeDartClient:
    """DartClient를 대체하는 테스트 더블.

    - `disclosure_pages`: STEP 2용 — list.json 페이지별 응답(dict) 리스트.
    - `companies`: STEP 3용 — corp_code -> company.json 응답(dict).
    - `documents`: STEP 4용 — rcept_no -> zip bytes.
    - `disclosure_pages_by_corp`: STEP 7용 — corp_code -> list.json 페이지별
      응답(dict) 리스트. STEP 7은 corp_code를 지정해 조회하므로 bgn_de/end_de가
      아니라 corp_code로 라우팅한다.
    - `raise_quota_after`: N번째 호출(1-base) 이후부터 QuotaExceededError를 던진다.
    """

    def __init__(
        self,
        disclosure_pages: list[dict] | None = None,
        disclosure_pages_by_window: dict[tuple[str, str], list[dict]] | None = None,
        disclosure_pages_by_corp: dict[str, list[dict]] | None = None,
        companies: dict[str, dict] | None = None,
        documents: dict[str, bytes] | None = None,
        raise_quota_after: int | None = None,
    ) -> None:
        self.disclosure_pages = disclosure_pages or []
        # STEP 2가 90일 초과 기간을 여러 구간으로 나눠 호출하게 되면서, 구간마다
        # page_no가 1부터 다시 시작한다. 구간별로 다른 응답을 줘야 하는 테스트는
        # (bgn_de, end_de) -> 페이지 리스트 로 키를 잡는 이 딕셔너리를 사용한다.
        # 지정하지 않으면 기존처럼 `disclosure_pages`를 page_no로만 인덱싱한다
        # (단일 구간짜리 기존 테스트와 호환).
        self.disclosure_pages_by_window = disclosure_pages_by_window or {}
        self.disclosure_pages_by_corp = disclosure_pages_by_corp or {}
        self.companies = companies or {}
        self.documents = documents or {}
        self.raise_quota_after = raise_quota_after
        self.call_count = 0
        self.company_calls: list[str] = []
        self.document_calls: list[str] = []
        self.disclosure_list_calls: list[dict] = []
        self.closed = False

    async def _tick(self) -> None:
        self.call_count += 1
        if self.raise_quota_after is not None and self.call_count > self.raise_quota_after:
            raise QuotaExceededError(current_count=self.call_count, limit=self.raise_quota_after)

    async def get_disclosure_list(self, **params) -> dict:
        await self._tick()
        self.disclosure_list_calls.append(dict(params))
        page_no = params["page_no"]
        if self.disclosure_pages_by_corp and params.get("corp_code") in self.disclosure_pages_by_corp:
            return self.disclosure_pages_by_corp[params["corp_code"]][page_no - 1]
        if self.disclosure_pages_by_window:
            key = (params.get("bgn_de"), params.get("end_de"))
            return self.disclosure_pages_by_window[key][page_no - 1]
        return self.disclosure_pages[page_no - 1]

    async def get_company(self, corp_code: str) -> dict:
        await self._tick()
        self.company_calls.append(corp_code)
        if corp_code not in self.companies:
            raise DartApiError(f"조회 실패: corp_code={corp_code}")
        return self.companies[corp_code]

    async def get_document(self, rcept_no: str) -> bytes:
        await self._tick()
        self.document_calls.append(rcept_no)
        if rcept_no not in self.documents:
            raise DartApiError(f"조회 실패: rcept_no={rcept_no}")
        return self.documents[rcept_no]

    async def aclose(self) -> None:
        self.closed = True


def _make_zip(file_name: str, content: bytes = b"<xml>dummy</xml>") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(file_name, content)
    return buf.getvalue()


def _history_doc_zip(period_to: str, revenue_cur: int, revenue_prv: int, file_name: str = "doc.xml") -> bytes:
    """STEP 7 테스트용 가짜 원문. `_extract_fiscal_date`가 읽는 PERIODTO 속성은
    실제 정규식과 맞게 넣어두고(진짜 파서를 태우지 않고 그대로 사용), 재무
    수치는 REV_CUR/REV_PRV 마커로 심어 `_fake_parse_xml_financials`가 그대로
    읽게 한다(xml_parser 자체 로직은 test_parsers.py에서 이미 검증했으므로
    여기서는 STEP 7 오케스트레이션만 확인한다)."""
    content = (
        f'<ROOT><P AUNIT="PERIODFROM" AUNITVALUE="20200101"/>'
        f'<P AUNIT="PERIODTO" AUNITVALUE="{period_to}"/>'
        f"REV_CUR={revenue_cur};REV_PRV={revenue_prv}</ROOT>"
    ).encode("utf-8")
    return _make_zip(file_name, content)


def _fake_parse_xml_financials(raw_bytes: bytes) -> ParsedFinancials:
    text = raw_bytes.decode("utf-8")
    match = re.search(r"REV_CUR=(\d+);REV_PRV=(\d+)", text)
    assert match is not None
    return ParsedFinancials(
        values_cur={"revenue": int(match.group(1))},
        values_prv={"revenue": int(match.group(2))},
        parse_status="OK",
        parse_note=None,
    )


def _make_job(
    session_factory,
    *,
    cond_region: dict | None = None,
    cond_revenue: dict | None = None,
    cond_total_assets: dict | None = None,
    cond_industry: list[str] | None = None,
    cond_period: dict | None = None,
    history_years: int = 4,
    status: str = JobStatus.PENDING,
) -> int:
    with session_factory() as db:
        job = Job(
            created_at=datetime.now().isoformat(timespec="seconds"),
            name="test job",
            cond_region=json.dumps(cond_region or {}, ensure_ascii=False),
            cond_revenue=json.dumps(cond_revenue or {}, ensure_ascii=False),
            cond_total_assets=json.dumps(cond_total_assets or {}, ensure_ascii=False),
            cond_industry=json.dumps(cond_industry or [], ensure_ascii=False),
            cond_period=json.dumps(cond_period or {"bgn_de": "20260101", "end_de": "20260131"}),
            history_years=history_years,
            status=status,
            current_step=0,
            progress_done=0,
            progress_total=0,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job.id


def _get_job(session_factory, job_id: int) -> Job:
    with session_factory() as db:
        job = db.get(Job, job_id)
        db.expunge(job)
        return job


# ---------------------------------------------------------------------------
# STEP 2 — 공시 목록 페이징 수집
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_candidates_paginates_and_dedupes(db_session_factory):
    job_id = _make_job(db_session_factory)

    page1 = {
        "status": "000",
        "total_page": 2,
        "list": [
            {"corp_code": "A0000001", "corp_name": "가나다상사", "rcept_no": "20260110000001"},
            {"corp_code": "A0000002", "corp_name": "라마바상사", "rcept_no": "20260110000002"},
        ],
    }
    page2 = {
        "status": "000",
        "total_page": 2,
        "list": [
            # A0000001의 정정 공시 — rcept_no가 더 크므로 대표 건이 이걸로 갱신되어야 함
            {"corp_code": "A0000001", "corp_name": "가나다상사", "rcept_no": "20260115000009"},
            {"corp_code": "A0000003", "corp_name": "사아자상사", "rcept_no": "20260112000003"},
        ],
    }
    client = FakeDartClient(disclosure_pages=[page1, page2])

    candidates = await pipeline._collect_candidates(
        client, db_session_factory, job_id, {"bgn_de": "20260101", "end_de": "20260131"}
    )

    by_corp = {c["corp_code"]: c for c in candidates}
    assert set(by_corp) == {"A0000001", "A0000002", "A0000003"}
    assert by_corp["A0000001"]["rcept_no"] == "20260115000009"  # 최신 건으로 dedup
    assert client.call_count == 2  # 페이지 2개만 호출

    job = _get_job(db_session_factory, job_id)
    assert job.current_step == pipeline.STEP_DISCLOSURE_LIST
    assert job.progress_done == 2
    assert job.progress_total == 2


@pytest.mark.asyncio
async def test_collect_candidates_requires_period():
    client = FakeDartClient(disclosure_pages=[])
    with pytest.raises(ValueError):
        await pipeline._collect_candidates(client, None, 1, {})


def test_split_period_into_windows_chunks_by_90_days():
    """실측(2026-07-15): corp_code 없이 날짜만으로 list.json을 조회하면 조회
    기간이 3개월(90일)을 넘을 수 없다 — 90일 고정 폭으로 분할하는지 검증."""
    windows = pipeline._split_period_into_windows("20260101", "20260410")

    assert windows == [("20260101", "20260331"), ("20260401", "20260410")]


def test_split_period_into_windows_single_window_when_within_90_days():
    windows = pipeline._split_period_into_windows("20260101", "20260131")

    assert windows == [("20260101", "20260131")]


@pytest.mark.asyncio
async def test_collect_candidates_splits_period_over_90_days_and_dedupes_across_windows(
    db_session_factory,
):
    """상세개발계획.md §7-1 기본값인 "최근 1년" 검색처럼 90일을 넘는 기간이
    들어와도 실패하지 않고 구간별로 나눠 호출해야 한다(2026-07-15 실측
    발견 — 3개월 초과 시 list.json이 status=100으로 즉시 실패)."""
    job_id = _make_job(db_session_factory)

    window1 = ("20260101", "20260331")
    window2 = ("20260401", "20260410")
    page_w1 = {
        "status": "000",
        "total_page": 1,
        "list": [{"corp_code": "A1", "corp_name": "가나다상사", "rcept_no": "20260201000001"}],
    }
    page_w2 = {
        "status": "000",
        "total_page": 1,
        "list": [
            # A1의 정정 공시(2구간에서 접수) — rcept_no가 더 크므로 대표 건이 갱신되어야 함
            {"corp_code": "A1", "corp_name": "가나다상사", "rcept_no": "20260405000002"},
            {"corp_code": "A2", "corp_name": "라마바상사", "rcept_no": "20260406000003"},
        ],
    }
    client = FakeDartClient(
        disclosure_pages_by_window={window1: [page_w1], window2: [page_w2]}
    )

    candidates = await pipeline._collect_candidates(
        client, db_session_factory, job_id, {"bgn_de": "20260101", "end_de": "20260410"}
    )

    by_corp = {c["corp_code"]: c for c in candidates}
    assert set(by_corp) == {"A1", "A2"}
    assert by_corp["A1"]["rcept_no"] == "20260405000002"  # 2구간의 최신 건으로 dedup
    assert client.call_count == 2  # 구간 2개 x 페이지 1개

    called_periods = {(c["bgn_de"], c["end_de"]) for c in client.disclosure_list_calls}
    assert called_periods == {window1, window2}
    for call in client.disclosure_list_calls:
        assert call["page_no"] == 1  # 각 구간은 page_no가 1부터 다시 시작

    job = _get_job(db_session_factory, job_id)
    assert job.current_step == pipeline.STEP_DISCLOSURE_LIST
    assert job.progress_done == 2
    assert job.progress_total == 2


# ---------------------------------------------------------------------------
# STEP 4 — 감사보고서 원본 다운로드
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_document_download_downloads_and_extracts(db_session_factory, tmp_path):
    settings = Settings(document_cache_dir=str(tmp_path / "documents"))
    job_id = _make_job(db_session_factory)
    with db_session_factory() as db:
        db.add(Result(job_id=job_id, corp_code="A1", rcept_no="20260101000001"))
        db.commit()

    zip_bytes = _make_zip("20260101000001_00001.xml")
    client = FakeDartClient(documents={"20260101000001": zip_bytes})

    await pipeline._run_document_download(client, db_session_factory, settings, job_id)

    extracted = tmp_path / "documents" / "20260101000001" / "20260101000001_00001.xml"
    assert extracted.is_file()
    assert client.document_calls == ["20260101000001"]

    job = _get_job(db_session_factory, job_id)
    assert job.current_step == pipeline.STEP_DOCUMENT_DOWNLOAD
    assert job.progress_done == 1
    assert job.progress_total == 1


@pytest.mark.asyncio
async def test_run_document_download_skips_existing_local_cache(db_session_factory, tmp_path):
    settings = Settings(document_cache_dir=str(tmp_path / "documents"))
    job_id = _make_job(db_session_factory)
    with db_session_factory() as db:
        db.add(Result(job_id=job_id, corp_code="A1", rcept_no="20260101000001"))
        db.commit()

    cached_dir = tmp_path / "documents" / "20260101000001"
    cached_dir.mkdir(parents=True)
    (cached_dir / "already_here.xml").write_text("<xml/>", encoding="utf-8")

    # documents 딕셔너리를 비워둬서, 만약 재다운로드를 시도하면 DartApiError로 실패하게 한다.
    client = FakeDartClient(documents={})

    await pipeline._run_document_download(client, db_session_factory, settings, job_id)

    assert client.document_calls == []  # 로컬 캐시가 있으므로 재다운로드하지 않음


# ---------------------------------------------------------------------------
# Phase 2 처리 순서 — 조건 밴드 근접도 정렬 (§4-10-D)
# ---------------------------------------------------------------------------


def test_band_proximity_scores_ranks_near_band_first_and_places_unknown_in_middle():
    cond_revenue = {"min_krw": 6_000_000_000, "max_krw": 15_000_000_000}
    # 밴드 중심(기하평균)은 약 94.9억. 참고값이 없는 후보는 제외되지 않고 중간 순위.
    refs = [
        (100_000_000, None),  # 1억 — 밴드에서 아주 멀다
        (9_000_000_000, None),  # 90억 — 중심에 가장 가깝다
        (None, None),  # 참고값 없음 → 중간
        (40_000_000_000, None),  # 400억 — 멀다
    ]

    scores = pipeline._band_proximity_scores(refs, cond_revenue, {})
    order = [i for _, i in sorted(zip(scores, range(len(refs))), key=lambda p: p[0])]

    assert order[0] == 1  # 가장 가까운 후보가 먼저
    assert order.index(2) not in (0, len(refs) - 1)  # 참고값 없는 후보는 중간


def test_band_proximity_scores_keep_original_order_without_conditions():
    refs = [(9_000_000_000, None), (100_000_000, None), (None, None)]

    scores = pipeline._band_proximity_scores(refs, {}, {})

    assert scores == [0.0, 0.0, 0.0]  # 조건이 없으면 동점 → 안정 정렬로 id順 유지


@pytest.mark.asyncio
async def test_run_document_download_processes_near_band_candidates_first(
    db_session_factory, tmp_path
):
    settings = Settings(document_cache_dir=str(tmp_path / "documents"))
    job_id = _make_job(
        db_session_factory,
        cond_revenue={"min_krw": 6_000_000_000, "max_krw": 15_000_000_000},
    )
    with db_session_factory() as db:
        # 삽입 순서는 "먼 후보 → 참고값 없음 → 가까운 후보" — 정렬이 없으면 이 순서 그대로 처리된다.
        db.add(Result(job_id=job_id, corp_code="A1", rcept_no="R_FAR", ref_revenue=100_000_000))
        db.add(Result(job_id=job_id, corp_code="A2", rcept_no="R_UNKNOWN"))
        db.add(Result(job_id=job_id, corp_code="A3", rcept_no="R_NEAR", ref_revenue=9_000_000_000))
        db.commit()

    client = FakeDartClient(
        documents={key: _make_zip(f"{key}.xml") for key in ("R_FAR", "R_UNKNOWN", "R_NEAR")}
    )

    await pipeline._run_document_download(client, db_session_factory, settings, job_id)

    assert client.document_calls == ["R_NEAR", "R_UNKNOWN", "R_FAR"]


# ---------------------------------------------------------------------------
# Phase 1/2 공용 테스트 환경 픽스처
# ---------------------------------------------------------------------------
#
# 구 `run_job`(STEP 1~7 전체)과 그 STEP 3(지역/업종 필터·CorpProfile 캐시) 테스트는
# 2026-07-22에 프로덕션 코드 삭제와 함께 제거됐다. 아래 픽스처는 Phase 1
# (`run_job_phase1`)/Phase 2(`run_job_phase2`) 테스트가 공유한다 — 두 함수 모두
# corp_cache 갱신/FSC 사전 추림을 쓰지 않으므로 세션/설정만 테스트용으로 교체한다.


@pytest.fixture
def patch_pipeline_env(monkeypatch, db_session_factory, tmp_path):
    """Phase 1/2가 사용하는 get_session_factory/get_settings를 테스트용으로
    교체한다. DartClient는 각 테스트에서 별도로 패치한다.
    """
    settings = Settings(document_cache_dir=str(tmp_path / "documents"))

    monkeypatch.setattr(pipeline, "get_session_factory", lambda: db_session_factory)
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    return settings


# ---------------------------------------------------------------------------
# STEP 5 — 재무제표 파싱 (M3). 실제 원문(tests/fixtures)을 DOCUMENT_CACHE_DIR에
# 배치해 xml_parser까지 실제로 태워본다 — parse_status 판정 로직 자체는
# test_parsers.py에서 이미 촘촘히 검증했으므로, 여기서는 pipeline이 그 결과를
# results 테이블에 올바르게 적재/재시도하는지(resume, retry-failed)에 집중한다.
# ---------------------------------------------------------------------------


def _copy_fixture_to_cache(cache_root: Path, rcept_no: str, fixture_name: str) -> None:
    target_dir = cache_root / rcept_no
    target_dir.mkdir(parents=True, exist_ok=True)
    src = FIXTURES_DIR / fixture_name / f"{fixture_name}_00760.xml"
    (target_dir / f"{rcept_no}_00760.xml").write_bytes(src.read_bytes())


@pytest.mark.asyncio
async def test_run_financial_parsing_populates_result_from_real_fixture(db_session_factory, tmp_path):
    """한국학술정보(20260630000641) 실제 원문으로 STEP5 전체 경로(파일 읽기 →
    xml_parser → 감사의견/결산기준일 추출 → Result 갱신)를 검증한다."""
    settings = Settings(document_cache_dir=str(tmp_path / "documents"))
    job_id = _make_job(db_session_factory)
    with db_session_factory() as db:
        db.add(Result(job_id=job_id, corp_code="A1", rcept_no="20260630000641"))
        db.commit()

    _copy_fixture_to_cache(tmp_path / "documents", "20260630000641", "20260630000641")

    await pipeline._run_financial_parsing(db_session_factory, settings, job_id)

    with db_session_factory() as db:
        result = db.execute(select(Result).where(Result.job_id == job_id)).scalar_one()

    assert result.parse_status == "OK"
    assert result.audit_opinion == "적정"
    assert result.fiscal_date == "2026-03-31"
    assert result.total_assets_cur == 46_609_006_893
    assert result.revenue_cur == 39_148_198_762

    job = _get_job(db_session_factory, job_id)
    assert job.current_step == pipeline.STEP_PARSE_FINANCIALS
    assert job.progress_done == 1


@pytest.mark.asyncio
async def test_run_financial_parsing_skips_already_parsed_results(db_session_factory, tmp_path):
    """parse_status가 이미 있는 results는 원문이 없어도 다시 열지 않는다(resume)."""
    settings = Settings(document_cache_dir=str(tmp_path / "documents"))
    job_id = _make_job(db_session_factory)
    with db_session_factory() as db:
        db.add(
            Result(
                job_id=job_id,
                corp_code="A1",
                rcept_no="20260101000001",
                parse_status=ParseStatus.OK,
                revenue_cur=123,
            )
        )
        db.commit()

    # 원문 파일을 전혀 만들지 않는다 — 만약 재파싱을 시도한다면 "원문 없음"으로
    # FAILED가 될 텐데, resume이 제대로 동작하면 애초에 손대지 않아야 한다.
    await pipeline._run_financial_parsing(db_session_factory, settings, job_id)

    with db_session_factory() as db:
        result = db.execute(select(Result).where(Result.job_id == job_id)).scalar_one()
    assert result.parse_status == ParseStatus.OK
    assert result.revenue_cur == 123


@pytest.mark.asyncio
async def test_run_financial_parsing_missing_document_marks_failed(db_session_factory, tmp_path):
    settings = Settings(document_cache_dir=str(tmp_path / "documents"))
    job_id = _make_job(db_session_factory)
    with db_session_factory() as db:
        db.add(Result(job_id=job_id, corp_code="A1", rcept_no="20260101000099"))
        db.commit()

    await pipeline._run_financial_parsing(db_session_factory, settings, job_id)

    with db_session_factory() as db:
        result = db.execute(select(Result).where(Result.job_id == job_id)).scalar_one()
    assert result.parse_status == ParseStatus.FAILED
    assert "원문 파일" in result.parse_note


@pytest.mark.asyncio
async def test_retry_failed_parsing_only_reparses_failed_rows(db_session_factory, tmp_path, monkeypatch):
    """retry-failed API가 parse_status=FAILED만 NULL로 리셋해 두면,
    retry_failed_parsing이 그 건만 다시 파싱하고 OK 건은 그대로 둔다."""
    settings = Settings(document_cache_dir=str(tmp_path / "documents"))
    job_id = _make_job(db_session_factory)
    with db_session_factory() as db:
        db.add(
            Result(
                job_id=job_id,
                corp_code="OK1",
                rcept_no="20260101000001",
                parse_status=ParseStatus.OK,
                revenue_cur=999,
            )
        )
        # retry-failed API가 이미 parse_status를 NULL로 리셋했다고 가정
        db.add(Result(job_id=job_id, corp_code="A1", rcept_no="20260630000641", parse_status=None))
        db.commit()

    _copy_fixture_to_cache(tmp_path / "documents", "20260630000641", "20260630000641")

    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "get_session_factory", lambda: db_session_factory)

    await pipeline.retry_failed_parsing(job_id)

    with db_session_factory() as db:
        results = {
            r.corp_code: r
            for r in db.execute(select(Result).where(Result.job_id == job_id)).scalars().all()
        }
    assert results["OK1"].revenue_cur == 999  # 손대지 않음
    assert results["A1"].parse_status == "OK"  # 재파싱되어 채워짐
    assert results["A1"].revenue_cur == 39_148_198_762


# ---------------------------------------------------------------------------
# STEP 6 — 매출액 범위 사후 필터
# ---------------------------------------------------------------------------


def test_run_revenue_filter_marks_excluded_outside_range(db_session_factory):
    job_id = _make_job(db_session_factory)
    with db_session_factory() as db:
        db.add(Result(job_id=job_id, corp_code="LOW", rcept_no="R1", revenue_cur=1_000))
        db.add(Result(job_id=job_id, corp_code="MID", rcept_no="R2", revenue_cur=10_000))
        db.add(Result(job_id=job_id, corp_code="HIGH", rcept_no="R3", revenue_cur=100_000))
        db.add(Result(job_id=job_id, corp_code="UNKNOWN", rcept_no="R4", revenue_cur=None))
        db.commit()

    pipeline._run_revenue_filter(db_session_factory, job_id, {"min_krw": 5_000, "max_krw": 50_000})

    with db_session_factory() as db:
        results = {
            r.corp_code: r.excluded_by_revenue
            for r in db.execute(select(Result).where(Result.job_id == job_id)).scalars().all()
        }
    assert results["LOW"] == 1
    assert results["MID"] == 0
    assert results["HIGH"] == 1
    assert results["UNKNOWN"] == 0  # 매출액 미상은 사후 필터로 제외하지 않는다


def test_run_revenue_filter_no_condition_leaves_untouched(db_session_factory):
    job_id = _make_job(db_session_factory)
    with db_session_factory() as db:
        db.add(Result(job_id=job_id, corp_code="A1", rcept_no="R1", revenue_cur=1_000))
        db.commit()

    pipeline._run_revenue_filter(db_session_factory, job_id, {})

    with db_session_factory() as db:
        result = db.execute(select(Result).where(Result.job_id == job_id)).scalar_one()
    assert result.excluded_by_revenue == 0


# ---------------------------------------------------------------------------
# STEP 7 — 최근 N년 재무 이력 수집 (2026-07-15 추가)
# ---------------------------------------------------------------------------


def test_history_window_looks_back_n_over_2_plus_2_years():
    bgn_de, end_de = pipeline._history_window(4)
    assert end_de == datetime.now().strftime("%Y%m%d")
    assert bgn_de == f"{datetime.now().year - 4}0101"  # 4//2+2 = 4

    bgn_de2, _ = pipeline._history_window(10)
    assert bgn_de2 == f"{datetime.now().year - 7}0101"  # 10//2+2 = 7


def _seed_result(session_factory, *, job_id: int, corp_code: str, excluded_by_revenue: int = 0) -> int:
    with session_factory() as db:
        result = Result(job_id=job_id, corp_code=corp_code, rcept_no="SEED", excluded_by_revenue=excluded_by_revenue)
        db.add(result)
        db.commit()
        db.refresh(result)
        return result.id


@pytest.mark.asyncio
async def test_collect_history_for_result_stops_once_target_years_reached(
    db_session_factory, tmp_path, monkeypatch
):
    """목표 4개 연도가 각각 **자기 공시(당기)** 로 확정되면 더 오래된 공시는
    다운로드하지 않아야 한다(2026-07-20 규칙 변경 — 조기 중단은 유지하되
    "연도 수를 채우면 중단"이 아니라 "연도마다 당기 원문을 확보하면 중단").

    각 연도 값도 그 연도를 당기로 하는 공시에서 나와야 한다 — 전기 열로 먼저
    채워졌더라도 자기 공시를 열면 덮어쓴다."""
    monkeypatch.setattr(pipeline, "parse_xml_financials", _fake_parse_xml_financials)
    settings = Settings(document_cache_dir=str(tmp_path / "documents"))
    job_id = _make_job(db_session_factory)
    result_id = _seed_result(db_session_factory, job_id=job_id, corp_code="H1")

    r1, r2, r3, r4, r5 = (
        "20260601000001",
        "20250601000002",
        "20240601000003",
        "20230601000004",
        "20220601000005",
    )
    disclosure_page = {
        "status": "000",
        "total_page": 1,
        "list": [
            {"corp_code": "H1", "corp_name": "이력회사", "rcept_no": r1},
            {"corp_code": "H1", "corp_name": "이력회사", "rcept_no": r2},
            {"corp_code": "H1", "corp_name": "이력회사", "rcept_no": r3},
            {"corp_code": "H1", "corp_name": "이력회사", "rcept_no": r4},
            {"corp_code": "H1", "corp_name": "이력회사", "rcept_no": r5},
        ],
    }
    documents = {
        r1: _history_doc_zip("20251231", revenue_cur=25_000_000, revenue_prv=24_000_000, file_name=f"{r1}.xml"),
        r2: _history_doc_zip("20241231", revenue_cur=24_999_999, revenue_prv=23_000_000, file_name=f"{r2}.xml"),
        r3: _history_doc_zip("20231231", revenue_cur=22_999_999, revenue_prv=22_000_000, file_name=f"{r3}.xml"),
        r4: _history_doc_zip("20221231", revenue_cur=21_999_999, revenue_prv=21_000_000, file_name=f"{r4}.xml"),
        # r5(2021년 당기)는 documents에 아예 없음 -> 호출되면 DartApiError로 드러난다.
    }
    client = FakeDartClient(disclosure_pages_by_corp={"H1": [disclosure_page]}, documents=documents)

    await pipeline._collect_history_for_result(client, db_session_factory, settings, result_id, "H1", 4)

    # 목표 4개 연도(2025~2022)의 당기 원문 4건까지만 — r5는 조기 중단으로 미다운로드.
    assert client.document_calls == [r1, r2, r3, r4]

    with db_session_factory() as db:
        snapshots = {
            s.fiscal_year: s
            for s in db.execute(
                select(FinancialSnapshot).where(FinancialSnapshot.result_id == result_id)
            ).scalars().all()
        }
    assert set(snapshots) == {"2025", "2024", "2023", "2022"}  # r4의 전기(2021)는 추가하지 않는다
    # 각 연도 값·rcept_no는 그 연도를 당기로 하는 공시에서 나온다.
    assert (snapshots["2025"].revenue, snapshots["2025"].rcept_no) == (25_000_000, r1)
    assert (snapshots["2024"].revenue, snapshots["2024"].rcept_no) == (24_999_999, r2)
    assert (snapshots["2023"].revenue, snapshots["2023"].rcept_no) == (22_999_999, r3)
    assert (snapshots["2022"].revenue, snapshots["2022"].rcept_no) == (21_999_999, r4)
    assert all(s.from_current_period == 1 for s in snapshots.values())


@pytest.mark.asyncio
async def test_collect_history_marks_oldest_year_as_previous_period_when_own_report_missing(
    db_session_factory, tmp_path, monkeypatch
):
    """자기 공시를 못 찾은 연도(대개 가장 오래된 연도)는 다음 연도 공시의 전기
    열 값이 그대로 남고 `from_current_period=0`으로 표시된다 — 화면은 그 연도
    버튼에 "전기 기준"을 붙여 당기 연도가 어긋난다는 걸 알린다."""
    monkeypatch.setattr(pipeline, "parse_xml_financials", _fake_parse_xml_financials)
    settings = Settings(document_cache_dir=str(tmp_path / "documents"))
    job_id = _make_job(db_session_factory)
    result_id = _seed_result(db_session_factory, job_id=job_id, corp_code="H4")

    r1, r2 = "20260601000001", "20250601000002"
    disclosure_page = {
        "status": "000",
        "total_page": 1,
        "list": [
            {"corp_code": "H4", "corp_name": "이력회사", "rcept_no": r1},
            {"corp_code": "H4", "corp_name": "이력회사", "rcept_no": r2},
        ],
    }
    documents = {
        r1: _history_doc_zip("20251231", revenue_cur=5_000, revenue_prv=4_000, file_name=f"{r1}.xml"),
        r2: _history_doc_zip("20241231", revenue_cur=4_500, revenue_prv=3_000, file_name=f"{r2}.xml"),
    }
    client = FakeDartClient(disclosure_pages_by_corp={"H4": [disclosure_page]}, documents=documents)

    await pipeline._collect_history_for_result(client, db_session_factory, settings, result_id, "H4", 3)

    with db_session_factory() as db:
        snapshots = {
            s.fiscal_year: s
            for s in db.execute(
                select(FinancialSnapshot).where(FinancialSnapshot.result_id == result_id)
            ).scalars().all()
        }
    assert set(snapshots) == {"2025", "2024", "2023"}
    assert snapshots["2025"].from_current_period == 1
    assert snapshots["2024"].from_current_period == 1  # r1 전기로 채웠다가 r2 당기로 교정
    assert snapshots["2024"].revenue == 4_500
    # 2023년을 당기로 하는 공시는 없다 -> r2의 전기 값이 그대로 남는다.
    assert snapshots["2023"].from_current_period == 0
    assert (snapshots["2023"].revenue, snapshots["2023"].rcept_no) == (3_000, r2)


@pytest.mark.asyncio
async def test_collect_history_for_result_insufficient_disclosures_does_not_fail(
    db_session_factory, tmp_path, monkeypatch
):
    """목표 연도수(4년)를 채울 만큼 공시가 없어도 에러 없이 찾은 만큼만 채운다."""
    monkeypatch.setattr(pipeline, "parse_xml_financials", _fake_parse_xml_financials)
    settings = Settings(document_cache_dir=str(tmp_path / "documents"))
    job_id = _make_job(db_session_factory)
    result_id = _seed_result(db_session_factory, job_id=job_id, corp_code="H2")

    r1 = "20260601000001"
    disclosure_page = {
        "status": "000",
        "total_page": 1,
        "list": [{"corp_code": "H2", "corp_name": "짧은이력회사", "rcept_no": r1}],
    }
    documents = {r1: _history_doc_zip("20251231", revenue_cur=1_000, revenue_prv=900, file_name=f"{r1}.xml")}
    client = FakeDartClient(disclosure_pages_by_corp={"H2": [disclosure_page]}, documents=documents)

    await pipeline._collect_history_for_result(client, db_session_factory, settings, result_id, "H2", 4)

    with db_session_factory() as db:
        snapshots = db.execute(
            select(FinancialSnapshot).where(FinancialSnapshot.result_id == result_id)
        ).scalars().all()
    assert {s.fiscal_year for s in snapshots} == {"2025", "2024"}  # 목표 4년에 못 미쳐도 정상 종료


@pytest.mark.asyncio
async def test_collect_history_for_result_skips_api_when_already_sufficient(db_session_factory, tmp_path):
    """이미 목표 연도수만큼 financial_snapshots가 있고 (가장 오래된 연도를 뺀)
    나머지가 당기 유래로 확정돼 있으면 list.json조차 호출하지 않는다(resume 핵심).

    가장 오래된 연도(여기서는 2024)는 자기 공시가 조회 기간 밖이라 전기 유래로
    남는 것이 정상이므로, 그 연도만 0이어도 재조회하지 않는다."""
    settings = Settings(document_cache_dir=str(tmp_path / "documents"))
    job_id = _make_job(db_session_factory)
    result_id = _seed_result(db_session_factory, job_id=job_id, corp_code="H3")
    with db_session_factory() as db:
        db.add(
            FinancialSnapshot(
                result_id=result_id, fiscal_year="2025", rcept_no="X", from_current_period=1
            )
        )
        db.add(
            FinancialSnapshot(
                result_id=result_id, fiscal_year="2024", rcept_no="X", from_current_period=0
            )
        )
        db.commit()

    client = FakeDartClient()  # 응답을 아무것도 안 줌 -> 호출되면 즉시 실패

    await pipeline._collect_history_for_result(client, db_session_factory, settings, result_id, "H3", 2)

    assert client.call_count == 0
    assert client.disclosure_list_calls == []


@pytest.mark.asyncio
async def test_run_history_collection_only_processes_final_included_results(
    db_session_factory, tmp_path, monkeypatch
):
    """excluded_by_revenue=1인 회사는 STEP 7 대상에서 제외된다."""
    monkeypatch.setattr(pipeline, "parse_xml_financials", _fake_parse_xml_financials)
    settings = Settings(document_cache_dir=str(tmp_path / "documents"))
    job_id = _make_job(db_session_factory)
    included_id = _seed_result(db_session_factory, job_id=job_id, corp_code="I1", excluded_by_revenue=0)
    _seed_result(db_session_factory, job_id=job_id, corp_code="EXCLUDED", excluded_by_revenue=1)

    r1 = "20260601000001"
    disclosure_page = {
        "status": "000",
        "total_page": 1,
        "list": [{"corp_code": "I1", "corp_name": "포함회사", "rcept_no": r1}],
    }
    documents = {r1: _history_doc_zip("20251231", revenue_cur=1_000, revenue_prv=900, file_name=f"{r1}.xml")}
    client = FakeDartClient(disclosure_pages_by_corp={"I1": [disclosure_page]}, documents=documents)

    await pipeline._run_history_collection(client, db_session_factory, settings, job_id, 2)

    # EXCLUDED가 조회됐다면 corp_code로 라우팅되는 disclosure_pages_by_corp에 없어
    # KeyError가 났을 것이다 — 그런 예외 없이 통과했다는 것 자체가 스킵되었다는 증거.
    with db_session_factory() as db:
        snapshots = db.execute(select(FinancialSnapshot)).scalars().all()
    assert {s.result_id for s in snapshots} == {included_id}

    job = _get_job(db_session_factory, job_id)
    assert job.current_step == pipeline.STEP_HISTORY_COLLECTION
    assert job.progress_done == 1  # EXCLUDED는 애초에 대상 목록에서 제외되어 카운트되지 않음
    assert job.progress_total == 1


# ---------------------------------------------------------------------------
# run_job_phase1 / run_job_phase2 — M6 아키텍처 재설계(§4-7/§4-7-1, 2026-07-15)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_job_phase1_inserts_candidates_and_marks_done(
    db_session_factory, patch_pipeline_env
):
    """A2(로컬 필터)만으로 후보가 results에 선삽입되고 phase=CANDIDATES/status=DONE으로
    멈추는지 확인. M8 3단계 이후 Phase 1은 외부 API를 전혀 호출하지 않는다."""
    job_id = _make_job(
        db_session_factory,
        cond_region={"sido": "경남", "sigungu": ["김해시"]},
        cond_revenue={"min_krw": 6_000_000_000, "max_krw": 15_000_000_000},
        cond_industry=["C25"],
    )

    with db_session_factory() as db:
        db.add(
            DartCorpIndex(
                corp_code="00099001",
                corp_name="김해기계",
                address="경상남도 김해시 어딘가 1",
                sido="경상남도",
                sigungu="김해시",
                ceo_name="홍길동",
                jurir_no="1000000000001",
                induty_code="25",
                induty_name="금속가공제품 제조업",
                corp_cls="기타법인",
            )
        )
        # 금융위 요약재무는 참고 표시용으로만 쓰인다(§4-10-C) — 후보를 제외하지 않는다.
        db.add(
            FscFinancialStat(
                crno="1000000000001",
                biz_year="2024",
                sale_amt=10_000_000_000,
                tast_amt=20_000_000_000,
            )
        )
        db.commit()

    await pipeline.run_job_phase1(job_id)

    job = _get_job(db_session_factory, job_id)
    assert job.status == JobStatus.DONE
    assert job.phase == "CANDIDATES"

    with db_session_factory() as db:
        results = db.execute(select(Result).where(Result.job_id == job_id)).scalars().all()
    assert len(results) == 1
    result = results[0]
    assert result.corp_code == "00099001"
    assert result.corp_name == "김해기계"
    assert result.ceo_name == "홍길동"
    assert result.induty_code == "25"
    # 참고값은 ref_* 컬럼에 기준연도와 함께 들어가고, 확정치(_cur)는 비어 있어야 한다 —
    # B4가 참고값으로 판정하는 일이 없도록 컬럼 자체를 분리했다.
    assert result.ref_revenue == 10_000_000_000
    assert result.ref_total_assets == 20_000_000_000
    assert result.ref_fin_year == "2024"
    assert result.revenue_cur is None
    assert result.total_assets_cur is None
    assert result.parse_status is None  # 원문 파싱은 Phase 2 몫 — 아직 NULL


@pytest.mark.asyncio
async def test_run_job_phase1_keeps_candidate_without_financial_stat(
    db_session_factory, patch_pipeline_env
):
    """금융위 재무 스냅샷이 없는 후보(실측 커버리지 18.1%)도 그대로 후보로 남아야 한다 —
    참고값 부재는 제외 사유가 아니다(§4-10-C 사전 제외 전면 폐기)."""
    job_id = _make_job(
        db_session_factory,
        cond_region={"sido": "경남", "sigungu": ["김해시"]},
        cond_revenue={"min_krw": 6_000_000_000, "max_krw": 15_000_000_000},
    )

    with db_session_factory() as db:
        db.add(
            DartCorpIndex(
                corp_code="00099002",
                corp_name="재무자료없는사",
                sido="경상남도",
                sigungu="김해시",
                jurir_no="3000000000003",
            )
        )
        db.commit()

    await pipeline.run_job_phase1(job_id)

    with db_session_factory() as db:
        results = db.execute(select(Result).where(Result.job_id == job_id)).scalars().all()
    assert [r.corp_code for r in results] == ["00099002"]
    assert results[0].ref_revenue is None
    assert results[0].ref_fin_year is None


@pytest.mark.asyncio
async def test_run_job_phase1_excludes_candidates_outside_local_filter(
    db_session_factory, patch_pipeline_env
):
    """A2(지역 필터)를 통과하지 못한 후보는 results에 들어가지 않아야 한다."""
    job_id = _make_job(db_session_factory, cond_region={"sido": "경남", "sigungu": ["김해시"]})

    with db_session_factory() as db:
        db.add(
            DartCorpIndex(
                corp_code="00099003",
                corp_name="서울상사",
                sido="서울특별시",
                sigungu="강남구",
            )
        )
        db.commit()

    await pipeline.run_job_phase1(job_id)

    job = _get_job(db_session_factory, job_id)
    assert job.status == JobStatus.DONE
    with db_session_factory() as db:
        results = db.execute(select(Result).where(Result.job_id == job_id)).scalars().all()
    assert results == []


@pytest.mark.asyncio
async def test_run_job_phase1_fails_when_dart_index_empty(
    db_session_factory, patch_pipeline_env
):
    """dart_corp_index가 아직 한 번도 크롤되지 않았으면 Job은 FAILED로 안내 메시지와
    함께 종료돼야 한다 — Job 실행 중 전수 크롤을 트리거하지 않는다."""
    job_id = _make_job(db_session_factory)

    await pipeline.run_job_phase1(job_id)

    job = _get_job(db_session_factory, job_id)
    assert job.status == JobStatus.FAILED
    assert "dart_corp_index" in (job.error_msg or "")


@pytest.mark.asyncio
async def test_run_job_phase2_reuses_step4_7_for_existing_results_only(
    monkeypatch, db_session_factory, patch_pipeline_env
):
    """Phase 2는 STEP2/3(전국 후보 수집/company.json)을 전혀 호출하지 않고,
    이미 results에 있는 corp_code만 대상으로 기존 STEP4~7을 재사용해야 한다."""
    monkeypatch.setattr(pipeline, "parse_xml_financials", _fake_parse_xml_financials)

    job_id = _make_job(
        db_session_factory,
        cond_revenue={"min_krw": 1_000, "max_krw": 10_000},
        history_years=2,
    )
    with db_session_factory() as db:
        db.add(Result(job_id=job_id, corp_code="A1", corp_name="테스트상사"))
        db.commit()

    rcept_no = "20260601000001"
    disclosure_page = {
        "status": "000",
        "total_page": 1,
        "list": [{"corp_code": "A1", "corp_name": "테스트상사", "rcept_no": rcept_no}],
    }
    doc_zip = _history_doc_zip(
        "20251231", revenue_cur=5_000, revenue_prv=4_000, file_name=f"{rcept_no}.xml"
    )
    fake_client = FakeDartClient(
        disclosure_pages_by_corp={"A1": [disclosure_page]},
        documents={rcept_no: doc_zip},
    )
    monkeypatch.setattr(pipeline, "DartClient", lambda **kwargs: fake_client)

    await pipeline.run_job_phase2(job_id)

    job = _get_job(db_session_factory, job_id)
    assert job.status == JobStatus.DONE
    assert job.phase == "FINANCIALS"
    assert fake_client.company_calls == []  # STEP3(company.json)는 전혀 호출되지 않음

    with db_session_factory() as db:
        result = db.execute(select(Result).where(Result.job_id == job_id)).scalar_one()
        snapshots = {
            s.fiscal_year
            for s in db.execute(
                select(FinancialSnapshot).where(FinancialSnapshot.result_id == result.id)
            ).scalars().all()
        }
    assert result.rcept_no == rcept_no
    assert result.revenue_cur == 5_000
    assert result.excluded_by_revenue == 0
    assert snapshots == {"2025", "2024"}


@pytest.mark.asyncio
async def test_run_job_phase2_applies_assets_filter(
    monkeypatch, db_session_factory, patch_pipeline_env
):
    """B4가 매출액과 총자산을 나란히 최종 확정하는지 확인(§4-7-2)."""
    monkeypatch.setattr(pipeline, "parse_xml_financials", _fake_parse_xml_financials)

    job_id = _make_job(
        db_session_factory,
        cond_total_assets={"min_krw": 100_000, "max_krw": 200_000},
        history_years=2,
    )
    with db_session_factory() as db:
        db.add(Result(job_id=job_id, corp_code="A1", corp_name="테스트상사"))
        db.commit()

    rcept_no = "20260601000002"
    disclosure_page = {
        "status": "000",
        "total_page": 1,
        "list": [{"corp_code": "A1", "corp_name": "테스트상사", "rcept_no": rcept_no}],
    }
    # _fake_parse_xml_financials는 revenue만 채우므로 total_assets_cur는 None으로
    # 남는다 — 총자산을 파싱 못 한 건은 제외하지 않는다는 §4-3/§4-7-2 원칙 확인.
    doc_zip = _history_doc_zip(
        "20251231", revenue_cur=5_000, revenue_prv=4_000, file_name=f"{rcept_no}.xml"
    )
    fake_client = FakeDartClient(
        disclosure_pages_by_corp={"A1": [disclosure_page]},
        documents={rcept_no: doc_zip},
    )
    monkeypatch.setattr(pipeline, "DartClient", lambda **kwargs: fake_client)

    await pipeline.run_job_phase2(job_id)

    with db_session_factory() as db:
        result = db.execute(select(Result).where(Result.job_id == job_id)).scalar_one()
    assert result.total_assets_cur is None
    assert result.excluded_by_assets == 0  # 값을 모르면 제외하지 않는다


@pytest.mark.asyncio
async def test_backfill_marks_result_failed_when_no_disclosure_found(
    monkeypatch, db_session_factory, patch_pipeline_env
):
    """감사보고서 공시를 하나도 못 찾은 후보는 parse_status=FAILED로 명시하고,
    Phase 1의 A3 추정치(revenue_cur/total_assets_cur)를 지워야 한다 — 이 값이
    확정치인 것처럼 남아 B4 필터에 쓰이면 안 된다(회귀 테스트)."""
    job_id = _make_job(db_session_factory, cond_revenue={"min_krw": 0, "max_krw": 10**12})
    with db_session_factory() as db:
        db.add(
            Result(
                job_id=job_id,
                corp_code="A1",
                corp_name="테스트상사",
                revenue_cur=999_999,  # Phase 1 A3 추정치(공시를 못 찾으면 지워져야 함)
                total_assets_cur=888_888,
            )
        )
        db.commit()

    empty_page = {"status": "013", "list": [], "total_page": 1}
    fake_client = FakeDartClient(disclosure_pages_by_corp={"A1": [empty_page]})
    monkeypatch.setattr(pipeline, "DartClient", lambda **kwargs: fake_client)

    await pipeline.run_job_phase2(job_id)

    with db_session_factory() as db:
        result = db.execute(select(Result).where(Result.job_id == job_id)).scalar_one()
    assert result.rcept_no is None
    assert result.parse_status == ParseStatus.FAILED
    assert result.revenue_cur is None
    assert result.total_assets_cur is None
    assert result.excluded_by_revenue == 0  # 값을 모르면 제외하지 않는다(기존 원칙 그대로)
    # 다년치 조회창(§STEP7 설계메모, 여기서는 4년치) 전체에서도 공시가 0건이면
    # 최근 1년 이내에도 당연히 없다 — "최근 1년 이내 공시 없음" 배제(2026-07-21).
    assert result.latest_disclosure_date is None
    assert result.excluded_by_stale_disclosure == 1


@pytest.mark.asyncio
async def test_backfill_recovers_homonym_dead_corp_code_by_address(
    db_session_factory, patch_pipeline_env
):
    """A4 이름 매칭이 동명이인 중 '폐지된' corp_code(공시 0건)를 붙인 경우,
    B1이 같은 이름의 다른 corp_code 중 **주소가 일치하고 실제 공시가 있는** 것으로
    교체해야 한다.

    실측 회귀(2026-07-20): '유성정밀'이 corpCode.xml에 3개 있고, A4의 이름 인덱스가
    '먼저 만난 것 하나'만 보관해 2017년 이후 공시가 0건인 폐지 법인(00433989)을
    골라 Job #20에서 FAILED가 됐다. 실제 정답은 사천시 법인(01647297)이며, 단순히
    '최근 갱신 우선'으로 골랐다면 부산의 동명이인(00840383)을 잘못 골랐을 것 —
    그래서 주소 대조가 판정 기준이어야 한다.
    """
    job_id = _make_job(db_session_factory, cond_revenue={"min_krw": 0, "max_krw": 10**12})
    with db_session_factory() as db:
        # modify_date 내림차순으로 BUSAN001이 REAL0001보다 먼저 시도된다 —
        # 주소가 다르므로 반드시 탈락해야 한다.
        db.add(CorpCache(corp_code="DEAD0001", corp_name="유성정밀", modify_date="20170630"))
        db.add(CorpCache(corp_code="BUSAN001", corp_name="유성정밀", modify_date="20230220"))
        db.add(CorpCache(corp_code="REAL0001", corp_name="유성정밀", modify_date="20230208"))
        db.add(
            Result(
                job_id=job_id,
                corp_code="DEAD0001",
                corp_name="유성정밀",
                address="경상남도 사천시 사남면 외국기업로 21",
            )
        )
        db.commit()

    rcept_no = "20260331003150"
    fake_client = FakeDartClient(
        disclosure_pages_by_corp={
            "DEAD0001": [{"status": "013", "list": [], "total_page": 1}],
            "BUSAN001": [
                {"status": "000", "total_page": 1, "list": [{"rcept_no": "20260331001989"}]}
            ],
            "REAL0001": [{"status": "000", "total_page": 1, "list": [{"rcept_no": rcept_no}]}],
        },
        companies={
            "BUSAN001": {"adres": "부산광역시 사상구 낙동대로 856 (감전동)"},
            "REAL0001": {"adres": "경상남도 사천시 사남면 외국기업로 21 (유천리, 유성정밀)"},
        },
    )

    await pipeline._backfill_latest_rcept_no_for_job(fake_client, db_session_factory, job_id, 4)

    with db_session_factory() as db:
        result = db.execute(select(Result).where(Result.job_id == job_id)).scalar_one()
    assert result.corp_code == "REAL0001"  # 폐지 코드에서 교체됨(STEP 4/5/7이 이 값을 쓴다)
    assert result.rcept_no == rcept_no
    assert result.parse_status != ParseStatus.FAILED
    # 주소가 다른 동명이인도 실제로 검사했음을 확인(공시가 있는 후보에만 company.json 호출)
    assert "BUSAN001" in fake_client.company_calls


@pytest.mark.asyncio
async def test_backfill_homonym_falls_back_to_job_region_when_address_missing(
    db_session_factory, patch_pipeline_env
):
    """후보의 FSC 주소를 파싱할 수 없으면 Job의 지역 조건(`region_matches`)으로
    동명이인을 가려야 한다.

    `Job.cond_region`은 DB에 **JSON 문자열**로 저장되므로 dict로 파싱해서 넘겨야
    한다 — 원시 문자열을 넘기면 `region_matches`가 AttributeError로 터진다(회귀).
    """
    job_id = _make_job(db_session_factory, cond_region={"sido": ["경상남도"]})
    with db_session_factory() as db:
        db.add(CorpCache(corp_code="DEAD0001", corp_name="유성정밀", modify_date="20170630"))
        db.add(CorpCache(corp_code="BUSAN001", corp_name="유성정밀", modify_date="20230220"))
        db.add(CorpCache(corp_code="REAL0001", corp_name="유성정밀", modify_date="20230208"))
        # address=None -> want_sido를 못 구하므로 Job 지역 조건으로 폴백해야 한다.
        db.add(Result(job_id=job_id, corp_code="DEAD0001", corp_name="유성정밀", address=None))
        db.commit()

    rcept_no = "20260331003150"
    fake_client = FakeDartClient(
        disclosure_pages_by_corp={
            "DEAD0001": [{"status": "013", "list": [], "total_page": 1}],
            "BUSAN001": [
                {"status": "000", "total_page": 1, "list": [{"rcept_no": "20260331001989"}]}
            ],
            "REAL0001": [{"status": "000", "total_page": 1, "list": [{"rcept_no": rcept_no}]}],
        },
        companies={
            "BUSAN001": {"adres": "부산광역시 사상구 낙동대로 856 (감전동)"},
            "REAL0001": {"adres": "경상남도 사천시 사남면 외국기업로 21 (유천리, 유성정밀)"},
        },
    )

    await pipeline._backfill_latest_rcept_no_for_job(fake_client, db_session_factory, job_id, 4)

    with db_session_factory() as db:
        result = db.execute(select(Result).where(Result.job_id == job_id)).scalar_one()
    assert result.corp_code == "REAL0001"
    assert result.rcept_no == rcept_no


# ---------------------------------------------------------------------------
# "최근 1년 이내 DART 공시 없음" 배제 (2026-07-21 추가, 실사례 "주식회사 유진")
# ---------------------------------------------------------------------------


def test_disclosure_date_from_rcept_no_extracts_yyyymmdd_prefix():
    """rcept_no(14자리)의 앞 8자리가 접수일자다."""
    assert pipeline._disclosure_date_from_rcept_no("20260331003150") == "20260331"


def test_disclosure_date_from_rcept_no_defensive_for_bad_input():
    assert pipeline._disclosure_date_from_rcept_no(None) is None
    assert pipeline._disclosure_date_from_rcept_no("") is None
    assert pipeline._disclosure_date_from_rcept_no("abc") is None


def test_is_disclosure_stale_none_means_stale():
    """공시를 아예 못 찾았으면(날짜 자체가 없으면) 무조건 배제 대상이다."""
    assert pipeline._is_disclosure_stale(None) is True


def test_is_disclosure_stale_boundary_around_365_days():
    now = datetime.now()
    recent = (now - timedelta(days=10)).strftime("%Y%m%d")
    old = (now - timedelta(days=400)).strftime("%Y%m%d")
    assert pipeline._is_disclosure_stale(recent) is False
    assert pipeline._is_disclosure_stale(old) is True


@pytest.mark.asyncio
async def test_backfill_flags_stale_disclosure_when_latest_is_older_than_a_year(
    db_session_factory, patch_pipeline_env
):
    """실사례 "주식회사 유진"과 같은 패턴 — corp_code는 정상 배정됐고 공시도
    존재하지만(동명이인 폐지 케이스와 다름), 가장 최근 공시조차 1년(365일)보다
    오래됐다면 excluded_by_stale_disclosure=1로 표시해야 한다."""
    job_id = _make_job(db_session_factory, cond_revenue={"min_krw": 0, "max_krw": 10**12})
    with db_session_factory() as db:
        db.add(Result(job_id=job_id, corp_code="A1", corp_name="주식회사유진"))
        db.commit()

    old_date = (datetime.now() - timedelta(days=500)).strftime("%Y%m%d")
    old_rcept = f"{old_date}000001"
    page = {
        "status": "000",
        "total_page": 1,
        "list": [{"corp_code": "A1", "corp_name": "주식회사유진", "rcept_no": old_rcept}],
    }
    fake_client = FakeDartClient(disclosure_pages_by_corp={"A1": [page]})

    await pipeline._backfill_latest_rcept_no_for_job(fake_client, db_session_factory, job_id, 4)

    with db_session_factory() as db:
        result = db.execute(select(Result).where(Result.job_id == job_id)).scalar_one()
    assert result.rcept_no == old_rcept  # 공시 자체는 정상적으로 채워진다(FAILED 아님)
    assert result.latest_disclosure_date == old_date
    assert result.excluded_by_stale_disclosure == 1


@pytest.mark.asyncio
async def test_backfill_does_not_flag_recent_disclosure_as_stale(
    db_session_factory, patch_pipeline_env
):
    """최근 1년 이내 공시가 있으면 excluded_by_stale_disclosure=0이어야 한다."""
    job_id = _make_job(db_session_factory, cond_revenue={"min_krw": 0, "max_krw": 10**12})
    with db_session_factory() as db:
        db.add(Result(job_id=job_id, corp_code="A1", corp_name="정상활동회사"))
        db.commit()

    recent_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    recent_rcept = f"{recent_date}000001"
    page = {
        "status": "000",
        "total_page": 1,
        "list": [{"corp_code": "A1", "corp_name": "정상활동회사", "rcept_no": recent_rcept}],
    }
    fake_client = FakeDartClient(disclosure_pages_by_corp={"A1": [page]})

    await pipeline._backfill_latest_rcept_no_for_job(fake_client, db_session_factory, job_id, 4)

    with db_session_factory() as db:
        result = db.execute(select(Result).where(Result.job_id == job_id)).scalar_one()
    assert result.rcept_no == recent_rcept
    assert result.latest_disclosure_date == recent_date
    assert result.excluded_by_stale_disclosure == 0


@pytest.mark.asyncio
async def test_run_job_phase2_skips_history_collection_for_stale_disclosure(
    monkeypatch, db_session_factory, patch_pipeline_env
):
    """STEP7(다년치 이력 수집)은 excluded_by_stale_disclosure=1인 회사를 건너뛰어
    쿼터를 아껴야 한다 — 다만 B3(STEP4/5)는 이 판정과 무관하게 항상 최신 1건은
    내려받아 파싱하므로 결과 행 자체(당기 재무정보)는 그대로 남아야 한다."""
    monkeypatch.setattr(pipeline, "parse_xml_financials", _fake_parse_xml_financials)

    job_id = _make_job(
        db_session_factory,
        cond_revenue={"min_krw": 1_000, "max_krw": 10_000},
        history_years=2,
    )
    with db_session_factory() as db:
        db.add(Result(job_id=job_id, corp_code="A1", corp_name="오래된회사"))
        db.commit()

    old_date = (datetime.now() - timedelta(days=500)).strftime("%Y%m%d")
    old_rcept = f"{old_date}000001"
    disclosure_page = {
        "status": "000",
        "total_page": 1,
        "list": [{"corp_code": "A1", "corp_name": "오래된회사", "rcept_no": old_rcept}],
    }
    doc_zip = _history_doc_zip(
        "20251231", revenue_cur=5_000, revenue_prv=4_000, file_name=f"{old_rcept}.xml"
    )
    fake_client = FakeDartClient(
        disclosure_pages_by_corp={"A1": [disclosure_page]},
        documents={old_rcept: doc_zip},
    )
    monkeypatch.setattr(pipeline, "DartClient", lambda **kwargs: fake_client)

    await pipeline.run_job_phase2(job_id)

    job = _get_job(db_session_factory, job_id)
    assert job.status == JobStatus.DONE

    with db_session_factory() as db:
        result = db.execute(select(Result).where(Result.job_id == job_id)).scalar_one()
        snapshots = db.execute(
            select(FinancialSnapshot).where(FinancialSnapshot.result_id == result.id)
        ).scalars().all()

    assert result.excluded_by_stale_disclosure == 1
    assert result.rcept_no == old_rcept
    assert result.revenue_cur == 5_000  # B3(STEP4/5)는 정상 수행된다
    assert result.excluded_by_revenue == 0
    assert snapshots == []  # STEP7은 이 회사를 건너뛴다(쿼터 절약)
    # STEP7이 다년치 조회를 위해 corp_code를 다시 list.json으로 조회하지 않았어야 한다
    # (B2에서 이미 1회 호출한 것 외에 추가 호출이 없어야 함).
    assert [
        call for call in fake_client.disclosure_list_calls if call.get("corp_code") == "A1"
    ] == [
        {
            "corp_code": "A1",
            "bgn_de": fake_client.disclosure_list_calls[0]["bgn_de"],
            "end_de": fake_client.disclosure_list_calls[0]["end_de"],
            "pblntf_ty": "F",
            "page_no": 1,
            "page_count": pipeline._DISCLOSURE_PAGE_COUNT,
        }
    ]
