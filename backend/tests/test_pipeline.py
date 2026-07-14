"""app/core/pipeline.py 단위 테스트.

CLAUDE.md 지침대로 실제 DART 호출 없이 `DartClient`를 모킹해 STEP 2/3/4
로직과 Job 상태 전이(특히 QuotaExceededError -> PAUSED_QUOTA)를 검증한다.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import Settings
from app.core import pipeline
from app.core.dart_client import DartApiError, QuotaExceededError
from app.models.corp_profile import CorpProfile
from app.models.job import Job, JobStatus
from app.models.result import ParseStatus, Result

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# 테스트 더블
# ---------------------------------------------------------------------------


class FakeDartClient:
    """DartClient를 대체하는 테스트 더블.

    - `disclosure_pages`: STEP 2용 — list.json 페이지별 응답(dict) 리스트.
    - `companies`: STEP 3용 — corp_code -> company.json 응답(dict).
    - `documents`: STEP 4용 — rcept_no -> zip bytes.
    - `raise_quota_after`: N번째 호출(1-base) 이후부터 QuotaExceededError를 던진다.
    """

    def __init__(
        self,
        disclosure_pages: list[dict] | None = None,
        companies: dict[str, dict] | None = None,
        documents: dict[str, bytes] | None = None,
        raise_quota_after: int | None = None,
    ) -> None:
        self.disclosure_pages = disclosure_pages or []
        self.companies = companies or {}
        self.documents = documents or {}
        self.raise_quota_after = raise_quota_after
        self.call_count = 0
        self.company_calls: list[str] = []
        self.document_calls: list[str] = []
        self.closed = False

    async def _tick(self) -> None:
        self.call_count += 1
        if self.raise_quota_after is not None and self.call_count > self.raise_quota_after:
            raise QuotaExceededError(current_count=self.call_count, limit=self.raise_quota_after)

    async def get_disclosure_list(self, **params) -> dict:
        await self._tick()
        page_no = params["page_no"]
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


class FakeFscClient:
    """FscCorpInfoClient를 대체하는 테스트 더블 (금융위 기업기본정보 API, 대응 1).

    - `matches`: `get_corp_basic_info(corp_nm=...)`에 전달되는 이름(정규화된
      회사명) -> `enpBsadr` 주소 문자열. 키가 없으면 "매칭 없음"
      (`response.body.items.item = []`, 실측 스키마)으로 응답한다.
    - `raise_for`: 이 이름으로 조회하면 예외를 던져 FSC 호출 실패(네트워크
      오류 등)를 시뮬레이션한다 — 호출부는 company.json 폴백으로 흡수해야 한다.
    """

    def __init__(
        self,
        matches: dict[str, str] | None = None,
        raise_for: set[str] | None = None,
    ) -> None:
        self.matches = matches or {}
        self.raise_for = raise_for or set()
        self.calls: list[str | None] = []
        self.closed = False

    async def get_corp_basic_info(
        self, *, page_no: int = 1, num_of_rows: int = 5, corp_nm: str | None = None
    ) -> dict:
        self.calls.append(corp_nm)
        if corp_nm in self.raise_for:
            raise RuntimeError("FSC 네트워크 오류 시뮬레이션")
        address = self.matches.get(corp_nm or "")
        if address is None:
            return {"response": {"body": {"items": {"item": []}}}}
        return {"response": {"body": {"items": {"item": [{"enpBsadr": address}]}}}}

    async def aclose(self) -> None:
        self.closed = True


def _make_zip(file_name: str, content: bytes = b"<xml>dummy</xml>") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(file_name, content)
    return buf.getvalue()


def _make_job(
    session_factory,
    *,
    cond_region: dict | None = None,
    cond_revenue: dict | None = None,
    cond_industry: list[str] | None = None,
    cond_period: dict | None = None,
    status: str = JobStatus.PENDING,
) -> int:
    with session_factory() as db:
        job = Job(
            created_at=datetime.now().isoformat(timespec="seconds"),
            name="test job",
            cond_region=json.dumps(cond_region or {}, ensure_ascii=False),
            cond_revenue=json.dumps(cond_revenue or {}, ensure_ascii=False),
            cond_industry=json.dumps(cond_industry or [], ensure_ascii=False),
            cond_period=json.dumps(cond_period or {"bgn_de": "20260101", "end_de": "20260131"}),
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


# ---------------------------------------------------------------------------
# STEP 3 — 지역/업종 필터 + corp_profiles 캐시
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_candidate_profile_uses_fresh_cache_without_api_call(db_session_factory):
    settings = Settings(corp_profile_ttl_days=180)
    now_iso = datetime.now().isoformat(timespec="seconds")
    with db_session_factory() as db:
        db.add(
            CorpProfile(
                corp_code="A0000001",
                corp_name="캐시상사",
                address="경상남도 김해시 어딘가 1",
                sido="경상남도",
                sigungu="김해시",
                induty_code="C25110",
                fetched_at=now_iso,
            )
        )
        db.commit()

    client = FakeDartClient(companies={})  # company.json 응답 없음 -> 호출되면 실패해야 함
    fsc_client = FakeFscClient()  # 매칭 데이터 없음 -> 호출되면 미매칭으로 응답

    profile = await pipeline._resolve_candidate_profile(
        client, fsc_client, db_session_factory, settings, "A0000001", "캐시상사", {}
    )

    assert profile is not None
    assert profile.sido == "경상남도"
    assert profile.sigungu == "김해시"
    assert client.call_count == 0  # 캐시 히트라 API 호출 없음
    assert fsc_client.calls == []  # 캐시 히트라 FSC도 호출되지 않음


@pytest.mark.asyncio
async def test_resolve_candidate_profile_fetches_and_upserts_on_cache_miss(db_session_factory):
    settings = Settings(corp_profile_ttl_days=180)
    client = FakeDartClient(
        companies={
            "A0000002": {
                "corp_name": "신규상사",
                "adres": "경상남도 양산시 어딘가 2",
                "induty_code": "C29110",
                "phn_no": "055-000-0000",
                "ceo_nm": "홍길동",
            }
        }
    )

    # cond_region이 비어 있으면(지역 조건 없음) FSC 매칭 결과와 무관하게
    # region_matches가 항상 True라 곧바로 company.json으로 확정한다.
    fsc_client = FakeFscClient(matches={"신규상사": "경상남도 양산시 어딘가 2"})

    profile = await pipeline._resolve_candidate_profile(
        client, fsc_client, db_session_factory, settings, "A0000002", "신규상사", {}
    )

    assert profile is not None
    assert profile.sido == "경상남도"
    assert profile.sigungu == "양산시"
    assert client.call_count == 1
    assert fsc_client.calls == ["신규상사"]

    with db_session_factory() as db:
        stored = db.get(CorpProfile, "A0000002")
        assert stored is not None
        assert stored.sigungu == "양산시"
        assert stored.fetched_at is not None
        assert stored.phone == "055-000-0000"  # company.json으로 풀 데이터 확정됨


@pytest.mark.asyncio
async def test_resolve_candidate_profile_stale_cache_triggers_refetch(db_session_factory):
    settings = Settings(corp_profile_ttl_days=1)
    stale_iso = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    with db_session_factory() as db:
        db.add(
            CorpProfile(
                corp_code="A0000003",
                corp_name="옛날상사",
                address="경상북도 포항시 옛주소 1",
                sido="경상북도",
                sigungu="포항시",
                induty_code="C25110",
                fetched_at=stale_iso,
            )
        )
        db.commit()

    client = FakeDartClient(
        companies={
            "A0000003": {
                "corp_name": "이사간상사",
                "adres": "경상남도 창원시 새주소 1",
                "induty_code": "C25110",
                "phn_no": "055-111-1111",
                "ceo_nm": "김철수",
            }
        }
    )

    fsc_client = FakeFscClient()  # 미매칭 -> 보수적으로 company.json 직접 호출

    profile = await pipeline._resolve_candidate_profile(
        client, fsc_client, db_session_factory, settings, "A0000003", "이사간상사", {}
    )

    assert client.call_count == 1  # TTL 만료라 재조회
    assert profile.sido == "경상남도"
    assert profile.sigungu == "창원시"


# ---------------------------------------------------------------------------
# STEP 3 대응 1 — FSC 사전 추림 3분기 (지역 일치 / 지역 불일치 / FSC 매칭 실패)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_candidate_profile_fsc_region_mismatch_skips_company_json(db_session_factory):
    """FSC로 지역이 명백히 다르다고 확인되면 company.json 호출을 생략해야 한다."""
    settings = Settings(corp_profile_ttl_days=180)
    fsc_client = FakeFscClient(matches={"먼지역상사": "경기도 성남시 어딘가 1"})
    # company.json 응답을 아예 주지 않는다 -> 호출되면 DartApiError로 실패해야 함
    client = FakeDartClient(companies={})

    cond_region = {"sido": "경남", "sigungu": ["김해시"]}
    profile = await pipeline._resolve_candidate_profile(
        client, fsc_client, db_session_factory, settings, "B0000001", "먼지역상사", cond_region
    )

    assert fsc_client.calls == ["먼지역상사"]
    assert client.call_count == 0  # DART 쿼터 절감 핵심 — company.json 호출 생략

    assert profile is not None
    assert profile.sido == "경기도"
    assert profile.sigungu == "성남시"
    assert profile.phone is None  # 부분 데이터만 upsert (전화/대표자/업종코드는 미확정)
    assert profile.induty_code is None
    assert profile.fetched_at is not None

    # region_matches는 이 프로필을 자연히 탈락시킨다 (별도 스킵 로직 불필요)
    from app.core.filters import region_matches

    assert region_matches(profile.sido, profile.sigungu, cond_region) is False


@pytest.mark.asyncio
async def test_resolve_candidate_profile_fsc_region_match_confirms_with_company_json(
    db_session_factory,
):
    """FSC로 지역이 맞다고 확인되면 company.json으로 전화/대표자/업종코드까지 확정한다."""
    settings = Settings(corp_profile_ttl_days=180)
    fsc_client = FakeFscClient(matches={"김해맞음상사": "경상남도 김해시 어딘가 1"})
    client = FakeDartClient(
        companies={
            "B0000002": {
                "corp_name": "김해맞음상사",
                "adres": "경상남도 김해시 어딘가 1",
                "induty_code": "C25110",
                "phn_no": "055-222-2222",
                "ceo_nm": "박영희",
            }
        }
    )

    cond_region = {"sido": "경남", "sigungu": ["김해시"]}
    profile = await pipeline._resolve_candidate_profile(
        client, fsc_client, db_session_factory, settings, "B0000002", "김해맞음상사", cond_region
    )

    assert fsc_client.calls == ["김해맞음상사"]
    assert client.call_count == 1  # 지역 일치 확인됐으므로 company.json으로 풀 데이터 확정
    assert profile is not None
    assert profile.sido == "경상남도"
    assert profile.sigungu == "김해시"
    assert profile.phone == "055-222-2222"
    assert profile.induty_code == "C25110"
    assert profile.ceo_name == "박영희"


@pytest.mark.asyncio
async def test_resolve_candidate_profile_fsc_no_match_falls_back_to_company_json(
    db_session_factory,
):
    """FSC 이름 검색 결과가 없으면(미매칭) 보수적으로 company.json을 직접 호출한다."""
    settings = Settings(corp_profile_ttl_days=180)
    fsc_client = FakeFscClient()  # 매칭 데이터 없음 -> 항상 미매칭
    client = FakeDartClient(
        companies={
            "B0000003": {
                "corp_name": "미매칭상사",
                "adres": "경상남도 창원시 어딘가 1",
                "induty_code": "C25110",
                "phn_no": "055-333-3333",
                "ceo_nm": "최민수",
            }
        }
    )

    cond_region = {"sido": "경남", "sigungu": ["김해시"]}  # 실제로는 창원시라 결국 필터에서 탈락하지만, 확정 자체는 이뤄져야 함
    profile = await pipeline._resolve_candidate_profile(
        client, fsc_client, db_session_factory, settings, "B0000003", "미매칭상사", cond_region
    )

    assert fsc_client.calls == ["미매칭상사"]
    assert client.call_count == 1  # 미매칭이므로 놓치지 않기 위해 company.json 직접 호출
    assert profile is not None
    assert profile.sido == "경상남도"
    assert profile.sigungu == "창원시"
    assert profile.phone == "055-333-3333"


@pytest.mark.asyncio
async def test_resolve_candidate_profile_fsc_failure_falls_back_to_company_json(
    db_session_factory,
):
    """FSC 호출 자체가 실패(네트워크 오류 등)해도 Job을 죽이지 않고 company.json으로 폴백한다."""
    settings = Settings(corp_profile_ttl_days=180)
    fsc_client = FakeFscClient(raise_for={"오류상사"})
    client = FakeDartClient(
        companies={
            "B0000004": {
                "corp_name": "오류상사",
                "adres": "경상남도 진주시 어딘가 1",
                "induty_code": "C25110",
                "phn_no": "055-444-4444",
                "ceo_nm": "정다은",
            }
        }
    )

    profile = await pipeline._resolve_candidate_profile(
        client, fsc_client, db_session_factory, settings, "B0000004", "오류상사", {}
    )

    assert fsc_client.calls == ["오류상사"]
    assert client.call_count == 1  # FSC 실패 -> company.json 폴백
    assert profile is not None
    assert profile.sido == "경상남도"
    assert profile.sigungu == "진주시"


@pytest.mark.asyncio
async def test_run_region_industry_filter_creates_results_only_for_matches(db_session_factory):
    settings = Settings(corp_profile_ttl_days=180)
    job_id = _make_job(
        db_session_factory,
        cond_region={"sido": "경남", "sigungu": ["김해시"]},
        cond_industry=["C25"],
    )

    candidates = [
        {"corp_code": "A1", "corp_name": "김해맞음", "rcept_no": "20260101000001"},
        {"corp_code": "A2", "corp_name": "김해업종불일치", "rcept_no": "20260101000002"},
        {"corp_code": "A3", "corp_name": "다른지역", "rcept_no": "20260101000003"},
    ]
    companies = {
        "A1": {"corp_name": "김해맞음", "adres": "경상남도 김해시 어딘가 1", "induty_code": "C25110"},
        "A2": {"corp_name": "김해업종불일치", "adres": "경상남도 김해시 어딘가 2", "induty_code": "G46900"},
        "A3": {"corp_name": "다른지역", "adres": "경상남도 창원시 어딘가 3", "induty_code": "C25110"},
    }
    client = FakeDartClient(companies=companies)
    fsc_client = FakeFscClient()  # 매칭 데이터 없음 -> 전부 미매칭이라 기존 대응 2와 동일하게 동작

    await pipeline._run_region_industry_filter(
        client,
        fsc_client,
        db_session_factory,
        settings,
        job_id,
        candidates,
        {"sido": "경남", "sigungu": ["김해시"]},
        ["C25"],
    )

    with db_session_factory() as db:
        results = db.execute(select(Result).where(Result.job_id == job_id)).scalars().all()

    assert len(results) == 1
    assert results[0].corp_code == "A1"
    assert results[0].rcept_no == "20260101000001"

    job = _get_job(db_session_factory, job_id)
    assert job.current_step == pipeline.STEP_REGION_INDUSTRY_FILTER
    assert job.progress_done == 3
    assert job.progress_total == 3


@pytest.mark.asyncio
async def test_run_region_industry_filter_skips_already_inserted_results(db_session_factory):
    """resume 시 이미 results에 있는 corp_code는 중복 삽입하지 않는다."""
    settings = Settings(corp_profile_ttl_days=180)
    job_id = _make_job(db_session_factory)

    with db_session_factory() as db:
        db.add(Result(job_id=job_id, corp_code="A1", rcept_no="20260101000001"))
        db.commit()

    client = FakeDartClient(
        companies={"A1": {"corp_name": "김해맞음", "adres": "경상남도 김해시 1", "induty_code": "C25110"}}
    )
    fsc_client = FakeFscClient()

    await pipeline._run_region_industry_filter(
        client,
        fsc_client,
        db_session_factory,
        settings,
        job_id,
        [{"corp_code": "A1", "corp_name": "김해맞음", "rcept_no": "20260101000099"}],
        {},
        [],
    )

    with db_session_factory() as db:
        results = db.execute(select(Result).where(Result.job_id == job_id)).scalars().all()
    assert len(results) == 1  # 중복 삽입 없음
    assert client.call_count == 1  # 프로필 조회 자체는 여전히 수행(캐시 미스였으므로)


@pytest.mark.asyncio
async def test_run_region_industry_filter_fsc_reduces_company_json_calls(db_session_factory):
    """대응 1 도입 효과: FSC로 지역 불일치가 확인된 후보는 company.json을 호출하지 않는다."""
    settings = Settings(corp_profile_ttl_days=180)
    job_id = _make_job(
        db_session_factory,
        cond_region={"sido": "경남", "sigungu": ["김해시"]},
    )

    candidates = [
        {"corp_code": "A1", "corp_name": "김해상사", "rcept_no": "20260101000001"},
        {"corp_code": "A2", "corp_name": "서울상사", "rcept_no": "20260101000002"},
        {"corp_code": "A3", "corp_name": "경기상사", "rcept_no": "20260101000003"},
    ]
    # FSC가 A2/A3는 지역이 다름을 확인해준다 -> company.json 호출 생략 대상
    fsc_client = FakeFscClient(
        matches={
            "김해상사": "경상남도 김해시 어딘가 1",
            "서울상사": "서울특별시 강남구 어딘가 2",
            "경기상사": "경기도 성남시 어딘가 3",
        }
    )
    # company.json에는 A1 응답만 등록 — A2/A3가 호출되면 DartApiError로 즉시 드러난다.
    client = FakeDartClient(
        companies={"A1": {"corp_name": "김해상사", "adres": "경상남도 김해시 어딘가 1", "induty_code": "C25110"}}
    )

    await pipeline._run_region_industry_filter(
        client,
        fsc_client,
        db_session_factory,
        settings,
        job_id,
        candidates,
        {"sido": "경남", "sigungu": ["김해시"]},
        [],
    )

    # 이전(대응 2)이었다면 후보 3건 전부 company.json을 호출했겠지만,
    # 대응 1 도입 후에는 지역이 명백히 다른 A2/A3는 호출을 건너뛴다.
    assert client.call_count == 1
    assert client.company_calls == ["A1"]
    assert fsc_client.calls == ["김해상사", "서울상사", "경기상사"]

    with db_session_factory() as db:
        results = db.execute(select(Result).where(Result.job_id == job_id)).scalars().all()
        profiles = db.execute(select(CorpProfile)).scalars().all()

    assert {r.corp_code for r in results} == {"A1"}  # 지역 통과 건만 results에 삽입
    # A2/A3도 부분 데이터(sido/sigungu)로 corp_profiles에 upsert되어 재검색 시 재호출 방지
    profile_by_code = {p.corp_code: p for p in profiles}
    assert profile_by_code["A2"].sido == "서울특별시"
    assert profile_by_code["A2"].phone is None
    assert profile_by_code["A3"].sido == "경기도"


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
# run_job — Job 생명주기 / 체크포인트 / 쿼터 일시정지 / 취소
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_pipeline_env(monkeypatch, db_session_factory, tmp_path):
    """pipeline.run_job이 사용하는 get_session_factory/get_settings/refresh_corp_cache를
    테스트용으로 교체한다. DartClient는 각 테스트에서 별도로 패치한다.

    FscCorpInfoClient도 기본값으로는 항상 미매칭(FakeFscClient())으로 패치해
    company.json 폴백 경로(기존 대응 2와 동일한 동작)를 타게 한다 — 대응 1의
    지역 사전 추림 자체를 검증하려는 테스트는 이 값을 개별적으로 재패치한다.
    """
    settings = Settings(document_cache_dir=str(tmp_path / "documents"))

    monkeypatch.setattr(pipeline, "get_session_factory", lambda: db_session_factory)
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "FscCorpInfoClient", lambda **kwargs: FakeFscClient())

    async def _fake_refresh_corp_cache(*args, **kwargs):
        return {"refreshed": False, "count": 0}

    monkeypatch.setattr(pipeline, "refresh_corp_cache", _fake_refresh_corp_cache)
    return settings


@pytest.mark.asyncio
async def test_run_job_happy_path_marks_done(monkeypatch, db_session_factory, patch_pipeline_env):
    job_id = _make_job(
        db_session_factory,
        cond_region={},
        cond_industry=[],
        cond_period={"bgn_de": "20260101", "end_de": "20260131"},
    )

    disclosure_page = {
        "status": "000",
        "total_page": 1,
        "list": [{"corp_code": "A1", "corp_name": "테스트상사", "rcept_no": "20260101000001"}],
    }
    company = {"corp_name": "테스트상사", "adres": "경상남도 김해시 1", "induty_code": "C25110"}
    zip_bytes = _make_zip("20260101000001_00001.xml")

    fake_client = FakeDartClient(
        disclosure_pages=[disclosure_page],
        companies={"A1": company},
        documents={"20260101000001": zip_bytes},
    )
    monkeypatch.setattr(pipeline, "DartClient", lambda **kwargs: fake_client)

    await pipeline.run_job(job_id)

    job = _get_job(db_session_factory, job_id)
    assert job.status == JobStatus.DONE
    assert job.error_msg is None
    assert fake_client.closed is True

    with db_session_factory() as db:
        results = db.execute(select(Result).where(Result.job_id == job_id)).scalars().all()
    assert len(results) == 1
    assert results[0].corp_code == "A1"


@pytest.mark.asyncio
async def test_run_job_fsc_prefilter_reduces_dart_company_calls_end_to_end(
    monkeypatch, db_session_factory, patch_pipeline_env
):
    """run_job 전체 실행에서도 대응 1(FSC 사전 추림)이 DART company.json 호출을 줄이는지 확인.

    후보 3건 중 1건만 경남(cond_region)이고 나머지 2건은 FSC로 다른 지역임이
    확인되므로, STEP3에서 company.json이 1건만 호출되어야 한다(이전 대응 2였다면
    3건 모두 호출).
    """
    job_id = _make_job(
        db_session_factory,
        cond_region={"sido": "경남", "sigungu": ["김해시"]},
        cond_industry=[],
        cond_period={"bgn_de": "20260101", "end_de": "20260131"},
    )

    disclosure_page = {
        "status": "000",
        "total_page": 1,
        "list": [
            {"corp_code": "A1", "corp_name": "김해상사", "rcept_no": "20260101000001"},
            {"corp_code": "A2", "corp_name": "서울상사", "rcept_no": "20260101000002"},
            {"corp_code": "A3", "corp_name": "경기상사", "rcept_no": "20260101000003"},
        ],
    }
    zip_bytes = _make_zip("20260101000001_00001.xml")

    fake_client = FakeDartClient(
        disclosure_pages=[disclosure_page],
        companies={
            "A1": {"corp_name": "김해상사", "adres": "경상남도 김해시 1", "induty_code": "C25110"},
        },
        documents={"20260101000001": zip_bytes},
    )
    fake_fsc = FakeFscClient(
        matches={
            "김해상사": "경상남도 김해시 어딘가 1",
            "서울상사": "서울특별시 강남구 어딘가 2",
            "경기상사": "경기도 성남시 어딘가 3",
        }
    )
    monkeypatch.setattr(pipeline, "DartClient", lambda **kwargs: fake_client)
    monkeypatch.setattr(pipeline, "FscCorpInfoClient", lambda **kwargs: fake_fsc)

    await pipeline.run_job(job_id)

    job = _get_job(db_session_factory, job_id)
    assert job.status == JobStatus.DONE
    # STEP3: list.json(1) + company.json(1, A1만) = 2. document.xml(1)은 STEP4.
    assert fake_client.company_calls == ["A1"]
    assert fake_fsc.calls == ["김해상사", "서울상사", "경기상사"]

    with db_session_factory() as db:
        results = db.execute(select(Result).where(Result.job_id == job_id)).scalars().all()
        profiles = db.execute(select(CorpProfile)).scalars().all()

    assert {r.corp_code for r in results} == {"A1"}
    profile_by_code = {p.corp_code: p for p in profiles}
    # A2/A3는 company.json 없이도 sido/sigungu만 채워진 채 캐시에 남아
    # 다음 재검색 시 FSC/DART를 다시 호출하지 않는다.
    assert profile_by_code["A2"].sido == "서울특별시"
    assert profile_by_code["A3"].sido == "경기도"


@pytest.mark.asyncio
async def test_run_job_quota_exceeded_pauses_job(monkeypatch, db_session_factory, patch_pipeline_env):
    job_id = _make_job(db_session_factory, cond_period={"bgn_de": "20260101", "end_de": "20260131"})

    disclosure_page = {
        "status": "000",
        "total_page": 1,
        "list": [{"corp_code": "A1", "corp_name": "테스트상사", "rcept_no": "20260101000001"}],
    }
    # 첫 호출(list.json)까지만 허용하고 그 다음(company.json)부터 쿼터 초과
    fake_client = FakeDartClient(disclosure_pages=[disclosure_page], raise_quota_after=1)
    monkeypatch.setattr(pipeline, "DartClient", lambda **kwargs: fake_client)

    await pipeline.run_job(job_id)  # 예외를 던지지 않고 정상 반환해야 한다

    job = _get_job(db_session_factory, job_id)
    assert job.status == JobStatus.PAUSED_QUOTA
    assert job.error_msg is not None
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_run_job_unexpected_exception_marks_failed(
    monkeypatch, db_session_factory, patch_pipeline_env
):
    job_id = _make_job(db_session_factory, cond_period={"bgn_de": "20260101", "end_de": "20260131"})

    class ExplodingClient(FakeDartClient):
        async def get_disclosure_list(self, **params):
            raise RuntimeError("네트워크가 이상해요")

    fake_client = ExplodingClient()
    monkeypatch.setattr(pipeline, "DartClient", lambda **kwargs: fake_client)

    await pipeline.run_job(job_id)

    job = _get_job(db_session_factory, job_id)
    assert job.status == JobStatus.FAILED
    assert "네트워크가 이상해요" in (job.error_msg or "")


@pytest.mark.asyncio
async def test_run_job_skips_when_already_cancelled(
    monkeypatch, db_session_factory, patch_pipeline_env
):
    job_id = _make_job(db_session_factory, status=JobStatus.CANCELLED)

    fake_client = FakeDartClient()
    monkeypatch.setattr(pipeline, "DartClient", lambda **kwargs: fake_client)

    await pipeline.run_job(job_id)

    job = _get_job(db_session_factory, job_id)
    assert job.status == JobStatus.CANCELLED  # 변경 없이 그대로
    assert fake_client.call_count == 0  # 아무 API도 호출되지 않음


@pytest.mark.asyncio
async def test_run_job_cancelled_mid_step3_stops_without_overwriting_status(
    monkeypatch, db_session_factory, patch_pipeline_env
):
    job_id = _make_job(
        db_session_factory,
        cond_period={"bgn_de": "20260101", "end_de": "20260131"},
    )

    disclosure_page = {
        "status": "000",
        "total_page": 1,
        "list": [
            {"corp_code": "A1", "corp_name": "가", "rcept_no": "20260101000001"},
        ],
    }

    class CancellingClient(FakeDartClient):
        """company.json 첫 호출 직후 Job을 CANCELLED로 바꿔, 다음 체크포인트에서 중단되는지 확인."""

        async def get_company(self, corp_code: str) -> dict:
            with db_session_factory() as db:
                job = db.get(Job, job_id)
                job.status = JobStatus.CANCELLED
                db.commit()
            raise DartApiError("취소 유도용 — 이 후보는 실패 처리")

    fake_client = CancellingClient(disclosure_pages=[disclosure_page])
    monkeypatch.setattr(pipeline, "DartClient", lambda **kwargs: fake_client)

    await pipeline.run_job(job_id)

    job = _get_job(db_session_factory, job_id)
    assert job.status == JobStatus.CANCELLED
    # STEP 4(문서 다운로드)까지 진행되지 않았어야 한다.
    assert job.current_step in (pipeline.STEP_DISCLOSURE_LIST, pipeline.STEP_REGION_INDUSTRY_FILTER)


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
