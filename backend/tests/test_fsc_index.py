"""app/core/fsc_index.py(A1 — 금융위 전역 인덱스 크롤) 단위 테스트.

A2/A3/A4 테스트는 M8 3단계(2026-07-20)에서 함께 제거됐다 — A2는
`tests/test_dart_corp_index.py`로 옮겨졌고, A3(사전 스크리닝)와 A4(이름 매칭)는
파이프라인에서 폐기됐다(§4-10-C).

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
from app.models.fsc_corp_index import FscCorpIndex


class FakeFscIndexClient:
    """FscCorpInfoClient 대체 테스트 더블.

    `pages`는 A1(`get_corp_basic_info`)용 — page_no(1-base) 순서의 응답 리스트다.
    """

    def __init__(self, pages: list[dict] | None = None) -> None:
        self.pages = pages or []
        self.closed = False

    async def get_corp_basic_info(
        self, *, page_no: int = 1, num_of_rows: int = 100, corp_nm: str | None = None
    ) -> dict:
        return self.pages[page_no - 1]

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
