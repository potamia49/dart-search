"""M8 2단계 — 금융위 요약재무 스냅샷 단위 테스트. 상세개발계획.md §4-10-B/D.

실제 API는 타지 않는다(실측은 §4-10-B 스파이크가 담당). 여기서는 **연결/별도
혼재와 더미 crno를 걸러내는 규칙**, 그리고 **회사별 최신 연도 선택**에 집중한다 —
둘 다 틀리면 조용히 잘못된 값이 화면과 정렬에 쓰인다.
"""

from __future__ import annotations

from datetime import datetime

from app.core.fsc_financial_stat import (
    default_biz_years,
    extract_items,
    get_financial_stat_status,
    get_latest_stat_by_crno,
    to_row,
    upsert_financial_stats,
)


def _item(**over):
    base = {
        "crno": "1101110000086",
        "bizYear": "2024",
        "fnclDcd": "120",
        "enpSaleAmt": "68599701679",
        "enpTastAmt": "69029060428",
        "enpTdbtAmt": "1000",
        "enpTcptAmt": "2000",
        "enpBzopPft": "3000",
        "enpCrtmNpf": "4000",
    }
    base.update(over)
    return base


def test_default_biz_years_reaches_back_four_years():
    """회귀(2026-07-20): 3개년만 받으면 사실상 빈 최신 연도를 넣고 가장 알찬
    연도를 놓친다(실측 조인율 18.1% → 13.9%로 하락)."""
    assert default_biz_years(datetime(2026, 7, 20)) == ["2023", "2024", "2025", "2026"]


def test_extract_items_handles_empty_item_list():
    """결과 없음일 때도 items가 `{"item": []}`로 오므로 truthy 판정을 쓰면 안 된다."""
    payload = {"response": {"body": {"totalCount": "0", "items": {"item": []}}}}
    assert extract_items(payload) == (0, [])


def test_extract_items_wraps_single_dict_item():
    payload = {"response": {"body": {"totalCount": "1", "items": {"item": _item()}}}}
    total, items = extract_items(payload)
    assert total == 1 and len(items) == 1


def test_to_row_keeps_separate_statement():
    row = to_row(_item())
    assert row["crno"] == "1101110000086"
    assert row["biz_year"] == "2024"
    assert row["sale_amt"] == 68599701679
    assert row["tast_amt"] == 69029060428


def test_to_row_drops_consolidated_statement():
    """연결(110)을 함께 넣으면 같은 회사가 2행이 되고 매출이 실측 1.77배 부풀려진다."""
    assert to_row(_item(fnclDcd="110", enpSaleAmt="121267932711")) is None


def test_to_row_drops_na_and_dummy_crno():
    assert to_row(_item(fnclDcd="999")) is None
    assert to_row(_item(crno="0000000000000")) is None
    assert to_row(_item(crno="")) is None


def test_to_row_tolerates_missing_amounts():
    row = to_row(_item(enpBzopPft=None, enpCrtmNpf=""))
    assert row["bzop_pft"] is None and row["crtm_npf"] is None


def test_upsert_financial_stats_inserts_then_updates(db_session_factory):
    rows = [to_row(_item())]
    with db_session_factory() as db:
        assert upsert_financial_stats(db, rows) == (1, 0)
    with db_session_factory() as db:
        assert upsert_financial_stats(db, [to_row(_item(enpSaleAmt="999"))]) == (0, 1)
        latest = get_latest_stat_by_crno(db, ["1101110000086"])
        assert latest["1101110000086"].sale_amt == 999


def test_upsert_financial_stats_separates_years(db_session_factory):
    with db_session_factory() as db:
        upsert_financial_stats(
            db, [to_row(_item(bizYear="2023")), to_row(_item(bizYear="2024"))]
        )
        assert upsert_financial_stats(db, [to_row(_item(bizYear="2025"))]) == (1, 0)


def test_get_latest_stat_by_crno_picks_most_recent_year(db_session_factory):
    """최신 연도일수록 적재가 덜 돼 있어(2025년이 2024년의 57%) 회사별 최신을 골라야 한다."""
    with db_session_factory() as db:
        upsert_financial_stats(
            db,
            [
                to_row(_item(bizYear="2023", enpSaleAmt="100")),
                to_row(_item(bizYear="2025", enpSaleAmt="300")),
                to_row(_item(bizYear="2024", enpSaleAmt="200")),
                to_row(_item(crno="2222222222222", bizYear="2023", enpSaleAmt="10")),
            ],
        )
        latest = get_latest_stat_by_crno(db, ["1101110000086", "2222222222222", "9999999999999"])
    assert latest["1101110000086"].biz_year == "2025"
    assert latest["1101110000086"].sale_amt == 300
    # 다른 회사는 2023년만 있으므로 그것이 최신이다
    assert latest["2222222222222"].biz_year == "2023"
    # 데이터가 없는 회사는 아예 빠진다 — 호출부가 "미상"으로 처리한다(제외 아님)
    assert "9999999999999" not in latest


def test_get_latest_stat_by_crno_returns_empty_for_no_input(db_session_factory):
    with db_session_factory() as db:
        assert get_latest_stat_by_crno(db, []) == {}


def test_get_latest_stat_by_crno_handles_more_crnos_than_sql_variable_limit(db_session_factory):
    """회귀(2026-07-20): 후보 전체를 한 번에 `IN (...)`에 넣어 SQLite
    "too many SQL variables"로 터졌다 — Phase 1은 수천~수만 건으로 호출한다."""
    crnos = [str(1000000000000 + i) for i in range(2500)]
    with db_session_factory() as db:
        upsert_financial_stats(
            db, [to_row(_item(crno=c, enpSaleAmt="7")) for c in crnos[:1500]]
        )
        latest = get_latest_stat_by_crno(db, crnos)
    assert len(latest) == 1500
    assert latest[crnos[0]].sale_amt == 7


def test_get_financial_stat_status_reports_years_actually_stored(db_session_factory):
    """회귀(M8 5단계): 상태의 `years`는 마지막 크롤 인자가 아니라 **적재된 연도**다.

    한 해만 보강 크롤하면 메타에는 그 한 해만 남는데, 화면에는 "어떤 연도의
    참고값을 볼 수 있는지"가 보여야 한다 — 실제로 4개년이 들어 있는 DB가
    "2023년 기준"으로만 표시되던 것을 화면에 노출하며 발견했다.
    """
    with db_session_factory() as db:
        upsert_financial_stats(
            db,
            [
                to_row(_item(bizYear="2024")),
                to_row(_item(bizYear="2023")),
                to_row(_item(crno="2222222222222", bizYear="2025")),
            ],
        )
        db.commit()

    status = get_financial_stat_status(db_session_factory)
    assert status["years"] == ["2023", "2024", "2025"]
    assert status["row_count"] == 3
