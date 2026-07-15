"""app/core/fsc_index.py(Phase 1 A1~A4) 단위 테스트.

CLAUDE.md 지침대로 실제 공공데이터포털 호출 없이 `FscCorpInfoClient`를
모킹해 검증한다. 응답 봉투 구조(`response.body.items.item`,
`response.body.totalCount`)는 §4-7 스파이크로 실측된 스키마를 그대로
반영한다(tests/test_pipeline.py의 `FakeFscClient`와 동일한 관례).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

from app.core import fsc_index
from app.models.corp_cache import CorpCache
from app.models.fsc_corp_index import FscCorpIndex


class FakeFscIndexClient:
    """FscCorpInfoClient 대체 테스트 더블.

    - `pages`: A1(`get_corp_basic_info`)용 — page_no(1-base) 순서의 응답 리스트.
    - `fina_stats`: A3(`get_summary_financial_stat`)용 — (crno, biz_year) -> item dict.
      키가 없으면 "데이터 없음"(totalCount=0)으로 응답한다.
    - `raise_for_crno`: 이 crno로 조회하면 예외를 던져 FSC 호출 실패를 시뮬레이션한다.
    """

    def __init__(
        self,
        pages: list[dict] | None = None,
        fina_stats: dict[tuple[str, str], dict] | None = None,
        raise_for_crno: set[str] | None = None,
    ) -> None:
        self.pages = pages or []
        self.fina_stats = fina_stats or {}
        self.raise_for_crno = raise_for_crno or set()
        self.finstat_calls: list[tuple[str, str]] = []
        self.closed = False

    async def get_corp_basic_info(
        self, *, page_no: int = 1, num_of_rows: int = 100, corp_nm: str | None = None
    ) -> dict:
        return self.pages[page_no - 1]

    async def get_summary_financial_stat(self, *, crno: str, biz_year: str) -> dict:
        self.finstat_calls.append((crno, biz_year))
        if crno in self.raise_for_crno:
            raise RuntimeError("FSC finstat 네트워크 오류 시뮬레이션")
        item = self.fina_stats.get((crno, biz_year))
        if item is None:
            return {"response": {"body": {"totalCount": 0, "items": {"item": []}}}}
        return {"response": {"body": {"totalCount": 1, "items": {"item": [item]}}}}

    async def aclose(self) -> None:
        self.closed = True


def _wrap_items(total_count: int, items: list[dict]) -> dict:
    return {"response": {"body": {"totalCount": total_count, "items": {"item": items}}}}


def _item(**overrides) -> dict:
    base = {
        "crno": "1000000000001",
        "fssCorpUnqNo": "",
        "corpNm": "테스트상사",
        "enpBsadr": "",
        "sicNm": "",
        "corpRegMrktDcd": "N",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# A1 — 전역 인덱스 구축/갱신
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crawl_fsc_index_merges_duplicate_records_by_crno_and_skips_foreign(
    db_session_factory,
):
    """동일 crno의 중복 레코드는 필드별로 병합되고, corpRegMrktDcd='E'(해외)는 저장되지 않는다."""
    page1 = _wrap_items(
        total_count=3,
        items=[
            _item(
                crno="1846110041115",
                fssCorpUnqNo="",
                corpNm="유진금속공업(주)",
                enpBsadr="경상남도 김해시 어딘가 1",
                sicNm="",
            ),
            _item(
                crno="1846110041115",
                fssCorpUnqNo="00567444",
                corpNm="유진금속공업(주)",
                enpBsadr="",
                sicNm="알루미늄주물 주조업",
            ),
            _item(
                crno="0000000000000",
                fssCorpUnqNo="",
                corpNm="LEHMAN BROTHERS INC",
                enpBsadr="",
                sicNm="",
                corpRegMrktDcd="E",
            ),
        ],
    )
    client = FakeFscIndexClient(pages=[page1])

    result = await fsc_index.crawl_fsc_index(client, db_session_factory, max_pages=1)

    with db_session_factory() as db:
        rows = db.execute(select(FscCorpIndex)).scalars().all()

    assert len(rows) == 1  # 두 중복 레코드가 crno 기준 1건으로 병합
    merged = rows[0]
    assert merged.crno == "1846110041115"
    assert merged.fss_corp_unq_no == "00567444"  # 두 번째 레코드에서 채워짐
    assert merged.sido == "경상남도"  # 첫 번째 레코드에서 채워지고 두 번째가 덮어쓰지 않음
    assert merged.sigungu == "김해시"
    assert merged.sic_name == "알루미늄주물 주조업"  # 두 번째 레코드에서 채워짐
    assert result["skipped_foreign"] == 1
    assert result["upserted"] == 2


@pytest.mark.asyncio
async def test_crawl_fsc_index_resumes_from_cache_meta_checkpoint(db_session_factory):
    """max_pages로 중단한 뒤 다시 호출하면 cache_meta 체크포인트로 이어서 진행한다."""
    page1 = _wrap_items(total_count=2, items=[_item(crno="1111111111111", corpNm="A사")])
    page2 = _wrap_items(total_count=2, items=[_item(crno="2222222222222", corpNm="B사")])
    client = FakeFscIndexClient(pages=[page1, page2])

    result1 = await fsc_index.crawl_fsc_index(client, db_session_factory, max_pages=1, num_of_rows=1)
    assert result1["start_page"] == 1
    result2 = await fsc_index.crawl_fsc_index(client, db_session_factory, max_pages=1, num_of_rows=1)
    assert result2["start_page"] == 2

    with db_session_factory() as db:
        crnos = {row.crno for row in db.execute(select(FscCorpIndex)).scalars().all()}
    assert crnos == {"1111111111111", "2222222222222"}


@pytest.mark.asyncio
async def test_crawl_fsc_index_force_restarts_from_page_one(db_session_factory):
    page1 = _wrap_items(total_count=2, items=[_item(crno="1111111111111", corpNm="A사")])
    client = FakeFscIndexClient(pages=[page1, page1])

    await fsc_index.crawl_fsc_index(client, db_session_factory, max_pages=1, num_of_rows=1)
    result = await fsc_index.crawl_fsc_index(
        client, db_session_factory, max_pages=1, num_of_rows=1, force=True
    )
    assert result["start_page"] == 1


def test_get_fsc_index_status_empty_index_is_stale(db_session_factory):
    """빈 인덱스는 row_count=0, is_stale=True, crawl_in_progress=False."""
    status = fsc_index.get_fsc_index_status(db_session_factory)
    assert status["row_count"] == 0
    assert status["is_stale"] is True
    assert status["crawl_in_progress"] is False
    assert status["last_completed_at"] is None


@pytest.mark.asyncio
async def test_get_fsc_index_status_after_full_crawl_not_stale(db_session_factory):
    """전수(1페이지짜리 total_count)를 다 돌면 last_completed_at이 채워지고 fresh."""
    page = _wrap_items(total_count=1, items=[_item(crno="1111111111111", corpNm="A사")])
    client = FakeFscIndexClient(pages=[page])

    await fsc_index.crawl_fsc_index(client, db_session_factory, num_of_rows=1)

    status = fsc_index.get_fsc_index_status(db_session_factory)
    assert status["row_count"] == 1
    assert status["is_stale"] is False
    assert status["crawl_in_progress"] is False
    assert status["last_completed_at"] is not None


@pytest.mark.asyncio
async def test_get_fsc_index_status_mid_crawl_reports_in_progress(db_session_factory):
    """max_pages로 중단된(아직 전수를 다 못 돈) 상태는 crawl_in_progress=True."""
    page1 = _wrap_items(total_count=2, items=[_item(crno="1111111111111", corpNm="A사")])
    client = FakeFscIndexClient(pages=[page1])

    await fsc_index.crawl_fsc_index(client, db_session_factory, max_pages=1, num_of_rows=1)

    status = fsc_index.get_fsc_index_status(db_session_factory)
    assert status["crawl_in_progress"] is True
    assert status["last_completed_at"] is None
    assert status["is_stale"] is True


@pytest.mark.asyncio
async def test_get_fsc_index_status_reports_in_progress_during_second_crawl(db_session_factory):
    """최초 완주 후 두 번째(증분) 크롤이 도는 중에도 crawl_in_progress=True여야 한다.

    회귀 테스트 — 이전에는 첫 완주 시 남은 last_completed_at 때문에 두 번째
    크롤부터는 실제로 몇 시간 동안 돌고 있어도 crawl_in_progress가 항상
    False로 보고되는 버그가 있었다.
    """
    page = _wrap_items(total_count=1, items=[_item(crno="1111111111111", corpNm="A사")])
    client = FakeFscIndexClient(pages=[page])
    await fsc_index.crawl_fsc_index(client, db_session_factory, num_of_rows=1)
    assert fsc_index.get_fsc_index_status(db_session_factory)["crawl_in_progress"] is False

    page2 = _wrap_items(total_count=2, items=[_item(crno="2222222222222", corpNm="B사")])
    client2 = FakeFscIndexClient(pages=[page2])
    await fsc_index.crawl_fsc_index(client2, db_session_factory, force=True, max_pages=1, num_of_rows=1)

    status = fsc_index.get_fsc_index_status(db_session_factory)
    assert status["crawl_in_progress"] is True
    assert status["last_completed_at"] is None


# ---------------------------------------------------------------------------
# A2 — 로컬 필터 (지역 + 업종, API 호출 없음)
# ---------------------------------------------------------------------------


def test_filter_local_candidates_applies_region_and_industry(db_session_factory):
    with db_session_factory() as db:
        db.add_all(
            [
                FscCorpIndex(
                    crno="1",
                    corp_name="김해기계",
                    sido="경상남도",
                    sigungu="김해시",
                    sic_name="금속가공제품 제조업(기계 및 가구 제외)",
                ),
                FscCorpIndex(
                    crno="2",
                    corp_name="양산화학",
                    sido="경상남도",
                    sigungu="양산시",
                    sic_name="화학물질 및 화학제품 제조업",
                ),
                FscCorpIndex(
                    crno="3",
                    corp_name="서울상사",
                    sido="서울특별시",
                    sigungu="강남구",
                    sic_name="금속가공제품 제조업(기계 및 가구 제외)",
                ),
            ]
        )
        db.commit()

    with db_session_factory() as db:
        candidates = fsc_index.filter_local_candidates(
            db,
            cond_region={"sido": "경남", "sigungu": ["김해시"]},
            cond_industry=["C25"],
        )

    assert [c.corp_name for c in candidates] == ["김해기계"]


def test_filter_local_candidates_no_conditions_returns_all(db_session_factory):
    with db_session_factory() as db:
        db.add_all(
            [
                FscCorpIndex(crno="1", corp_name="A사", sido="경상남도", sigungu="김해시"),
                FscCorpIndex(crno="2", corp_name="B사", sido="서울특별시", sigungu="강남구"),
            ]
        )
        db.commit()

    with db_session_factory() as db:
        candidates = fsc_index.filter_local_candidates(db, cond_region={}, cond_industry=[])

    assert {c.corp_name for c in candidates} == {"A사", "B사"}


# ---------------------------------------------------------------------------
# A3 — 매출액/총자산 보강 + 안전마진 스크리닝
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrich_and_screen_financials_screens_by_margin_and_passes_on_failure(
    db_session_factory,
):
    with db_session_factory() as db:
        c1 = FscCorpIndex(crno="1000000000001", corp_name="통과사")
        c2 = FscCorpIndex(crno="1000000000002", corp_name="제외사")
        c3 = FscCorpIndex(crno="1000000000003", corp_name="조회실패사")
        c4 = FscCorpIndex(crno=None, corp_name="crno없는사")
        db.add_all([c1, c2, c3, c4])
        db.commit()
        for c in (c1, c2, c3, c4):
            db.refresh(c)
            db.expunge(c)
        candidates = [c1, c2, c3, c4]

    this_year = str(datetime.now().year)
    client = FakeFscIndexClient(
        fina_stats={
            ("1000000000001", this_year): {
                "enpSaleAmt": "10000000000",
                "enpTastAmt": "20000000000",
                "bizYear": this_year,
            },
            ("1000000000002", this_year): {
                "enpSaleAmt": "1000000000",
                "enpTastAmt": "2000000000",
                "bizYear": this_year,
            },
        },
        raise_for_crno={"1000000000003"},
    )

    passed = await fsc_index.enrich_and_screen_financials(
        client,
        db_session_factory,
        candidates,
        cond_revenue={"min_krw": 6_000_000_000, "max_krw": 15_000_000_000},
        cond_total_assets=None,
    )

    assert {c.corp_name for c in passed} == {"통과사", "조회실패사", "crno없는사"}

    with db_session_factory() as db:
        stored_c1 = db.get(FscCorpIndex, c1.id)
        assert stored_c1.revenue_latest == 10_000_000_000
        assert stored_c1.total_assets_latest == 20_000_000_000
        assert stored_c1.revenue_biz_year == this_year


@pytest.mark.asyncio
async def test_enrich_and_screen_financials_retries_previous_year_when_no_data(
    db_session_factory,
):
    with db_session_factory() as db:
        candidate = FscCorpIndex(crno="1000000000009", corp_name="작년자료만")
        db.add(candidate)
        db.commit()
        db.refresh(candidate)
        db.expunge(candidate)

    this_year = datetime.now().year
    prev_year = str(this_year - 1)
    client = FakeFscIndexClient(
        fina_stats={
            ("1000000000009", prev_year): {"enpSaleAmt": "10000000000", "enpTastAmt": "20000000000"},
        }
    )

    passed = await fsc_index.enrich_and_screen_financials(
        client,
        db_session_factory,
        [candidate],
        cond_revenue=None,
        cond_total_assets=None,
    )

    assert len(passed) == 1
    assert (str(this_year), "") not in client.finstat_calls  # 참고용, 실제 확인은 아래
    assert ("1000000000009", str(this_year)) in client.finstat_calls
    assert ("1000000000009", prev_year) in client.finstat_calls


# ---------------------------------------------------------------------------
# A4 — 후보 확정 (corp_code 해석)
# ---------------------------------------------------------------------------


def test_resolve_candidates_prefers_fss_then_name_match_then_drops(db_session_factory):
    with db_session_factory() as db:
        db.add(CorpCache(corp_code="00112233", corp_name="이름매칭상사"))
        db.commit()

    candidates = [
        FscCorpIndex(corp_name="직접매칭상사", fss_corp_unq_no="00998877"),
        FscCorpIndex(corp_name="(주)이름매칭상사", fss_corp_unq_no=""),
        FscCorpIndex(corp_name="매칭불가상사", fss_corp_unq_no=""),
    ]

    with db_session_factory() as db:
        corp_codes = fsc_index.resolve_candidates(db, candidates)

    assert corp_codes == ["00998877", "00112233"]


def test_resolve_candidates_ignores_non_8_digit_fss_corp_unq_no(db_session_factory):
    """fss_corp_unq_no가 있어도 8자리 숫자가 아니면 이름 매칭 안전망을 탄다."""
    with db_session_factory() as db:
        db.add(CorpCache(corp_code="00445566", corp_name="이상한코드상사"))
        db.commit()

    candidates = [FscCorpIndex(corp_name="이상한코드상사", fss_corp_unq_no="ABC")]

    with db_session_factory() as db:
        corp_codes = fsc_index.resolve_candidates(db, candidates)

    assert corp_codes == ["00445566"]
