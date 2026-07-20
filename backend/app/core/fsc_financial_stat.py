"""M8 2단계 — 금융위 요약재무 스냅샷 전수 크롤. 상세개발계획.md §4-10-B/D.

`getSummFinaStat_V2`를 `crno` 없이 `bizYear`만으로 전수 페이징해
`fsc_financial_stat`을 채운다. 실측 **3개년 307,282건을 63요청 / 2분 42초**에
받았다 — Job마다 후보 수만큼(경남 4,538회) 호출하던 A3를 대체하므로
data.go.kr 일일 쿼터 소진 문제가 구조적으로 사라진다.

## 이 데이터로 후보를 제외하지 않는다 (§4-10-C)

매출액·총자산 조건은 **가장 최근 감사보고서 당기 값으로만** 판정한다(Phase 2 B4).
여기 담기는 값의 용도는 두 가지뿐이며 둘 다 제외 권한이 없다:

1. 후보 목록 화면의 **참고 표시** (기준연도를 함께 보여줄 것)
2. Phase 2 **처리 순서** — 조건 밴드에 가까운 회사부터 원문을 연다.
   실측 효과: 상위 10% 처리에 실제 대상의 48.5%, 20%에 81.5%, 30%에 92.2%.
   끝까지 돌리면 결과는 순서와 무관하게 같고, 쿼터로 중단돼도 손해가 적다.

## 커버리지 한계 (실측)

DART 인덱스 113,519개사 중 최근 3개년 데이터가 붙는 회사는 18.1%(경남 30.7%,
김해 38.4%)뿐이다. 이는 크롤 방식의 손실이 아니라 **API 자체의 한계**다 —
누락된 회사 30곳을 개별 조회로 교차 검증한 결과 11곳은 데이터가 있었으나
보유연도가 전부 2022년 이전이었고 19곳은 아예 없었다. 즉 기존 A3의 건별
호출도 같은 비율만 스크리닝하고 있었다.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.dart_client import FscCorpInfoClient
from app.core.db import get_session_factory
from app.models.corp_cache import CacheMeta
from app.models.fsc_financial_stat import FscFinancialStat

logger = logging.getLogger(__name__)

# 별도요약재무제표. 연결(110)/NA(999)는 적재하지 않는다 — DART 원문 파싱값과
# 짝이 맞는 쪽이 별도이고, 섞으면 같은 회사가 2행이 된다(§4-10-B).
SEPARATE_FNCL_DCD = "120"

# 해외 레코드 등에 채워지는 더미 법인등록번호 — 조인 불가라 버린다.
_DUMMY_CRNO = "0000000000000"

_PAGE_SIZE = 5000

# `IN (...)` 한 번에 넣을 최대 개수 — SQLite 바인드 파라미터 상한(기본 999) 대비 여유.
_IN_CLAUSE_CHUNK = 900
_META_KEY_UPDATED_AT = "fsc_financial_stat_updated_at"
_META_KEY_YEARS = "fsc_financial_stat_years"

_AMOUNT_FIELDS = {
    "sale_amt": "enpSaleAmt",
    "tast_amt": "enpTastAmt",
    "tdbt_amt": "enpTdbtAmt",
    "tcpt_amt": "enpTcptAmt",
    "bzop_pft": "enpBzopPft",
    "crtm_npf": "enpCrtmNpf",
}


def default_biz_years(today: datetime | None = None) -> list[str]:
    """수집 대상 회계연도 — 올해를 포함한 **최근 4개년**.

    이 데이터 소스는 **최신 연도가 거의 비어 있다.** 2026-07-20 실측
    totalCount: 2023 135,378 / 2024 109,130 / 2025 62,774 / 2026 124.
    결산·공시·FSC 적재가 순차로 일어나기 때문에 올해와 작년은 아직 채워지는 중이다.

    처음에 "올해 포함 최근 3년"(2024~2026)으로 잡았더니 사실상 빈 2026년을
    넣고 커버리지가 가장 좋은 2023년을 빠뜨려, DART 인덱스와의 조인율이
    기대치 18.1%에서 13.9%로 떨어졌다. 한 해를 더 거슬러 올라가 4개년을 받는다
    — 빈 연도는 1요청이면 끝나므로 비용이 사실상 없다.

    회사별로는 `get_latest_stat_by_crno()`가 확보된 연도 중 최신을 고른다.
    """
    year = (today or datetime.now()).year
    return [str(year - 3), str(year - 2), str(year - 1), str(year)]


def _to_int(raw: Any) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


def extract_items(payload: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    """응답에서 `(totalCount, item 리스트)`를 꺼낸다.

    `items`가 결과 없음일 때도 `{"item": []}`로 오는 스키마라 truthy 판정을
    쓰면 안 된다(M1 스파이크에서 커버리지율이 항상 100%로 잘못 계산됐던 버그).
    """
    body = (payload.get("response") or {}).get("body") or {}
    total = _to_int(body.get("totalCount")) or 0
    items = body.get("items") or {}
    raw = items.get("item") if isinstance(items, dict) else items
    if raw is None:
        return total, []
    return total, raw if isinstance(raw, list) else [raw]


def to_row(item: dict[str, Any]) -> dict[str, Any] | None:
    """API item 1건 -> 적재용 dict. 별도가 아니거나 조인 불가면 None."""
    if str(item.get("fnclDcd") or "").strip() != SEPARATE_FNCL_DCD:
        return None
    crno = str(item.get("crno") or "").strip()
    if not crno or crno == _DUMMY_CRNO:
        return None
    biz_year = str(item.get("bizYear") or "").strip()
    if not biz_year:
        return None
    row = {"crno": crno, "biz_year": biz_year}
    for column, field in _AMOUNT_FIELDS.items():
        row[column] = _to_int(item.get(field))
    row["updated_at"] = datetime.now().isoformat(timespec="seconds")
    return row


def upsert_financial_stats(db: Session, rows: list[dict[str, Any]]) -> tuple[int, int]:
    """페이지 단위 배치 upsert (건별 커밋은 A1에서 병목으로 확인됨)."""
    if not rows:
        return 0, 0
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        deduped[(row["crno"], row["biz_year"])] = row

    keys = list(deduped)
    existing = {
        (row.crno, row.biz_year): row
        for row in db.execute(
            select(FscFinancialStat).where(
                FscFinancialStat.crno.in_({k[0] for k in keys}),
                FscFinancialStat.biz_year.in_({k[1] for k in keys}),
            )
        ).scalars()
    }
    inserted = updated = 0
    for key, row in deduped.items():
        found = existing.get(key)
        if found is None:
            db.add(FscFinancialStat(**row))
            inserted += 1
        else:
            for column, value in row.items():
                if column not in ("crno", "biz_year"):
                    setattr(found, column, value)
            updated += 1
    db.commit()
    return inserted, updated


def _set_meta(db: Session, key: str, value: str) -> None:
    found = db.execute(select(CacheMeta).where(CacheMeta.key == key)).scalar_one_or_none()
    if found is None:
        db.add(CacheMeta(key=key, value=value))
    else:
        found.value = value


def _get_meta(db: Session, key: str) -> str | None:
    found = db.execute(select(CacheMeta).where(CacheMeta.key == key)).scalar_one_or_none()
    return found.value if found else None


async def crawl_fsc_financial_stat(
    client: FscCorpInfoClient,
    session_factory: sessionmaker[Session] | None = None,
    *,
    years: list[str] | None = None,
    max_pages_per_year: int | None = None,
) -> dict[str, Any]:
    """연도별 전수 페이징(실측 3개년 63요청 / 2분 42초).

    A1과 달리 체크포인트 재개를 두지 않는다 — 전체가 3분이라 실패 시 처음부터
    다시 도는 편이 단순하고, 재적재해도 `(crno, biz_year)` upsert라 멱등이다.
    """
    factory = session_factory or get_session_factory()
    target_years = years or default_biz_years()

    per_year: dict[str, int] = {}
    total_inserted = total_updated = skipped = 0

    with factory() as db:
        _set_meta(db, _META_KEY_UPDATED_AT, "")
        db.commit()

    for biz_year in target_years:
        first = await client.list_summary_financial_stats(
            biz_year=biz_year, page_no=1, num_of_rows=_PAGE_SIZE
        )
        total_count, items = extract_items(first)
        pages = (total_count + _PAGE_SIZE - 1) // _PAGE_SIZE
        if max_pages_per_year is not None:
            pages = min(pages, max_pages_per_year)

        year_rows = 0
        for page in range(1, pages + 1):
            if page > 1:
                payload = await client.list_summary_financial_stats(
                    biz_year=biz_year, page_no=page, num_of_rows=_PAGE_SIZE
                )
                _, items = extract_items(payload)
            rows = []
            for item in items:
                row = to_row(item)
                if row is None:
                    skipped += 1
                else:
                    rows.append(row)
            with factory() as db:
                inserted, updated = upsert_financial_stats(db, rows)
            total_inserted += inserted
            total_updated += updated
            year_rows += len(rows)

        per_year[biz_year] = year_rows
        logger.info(
            "fsc_financial_stat %s년 완료: totalCount=%s, 적재 %s행(%s페이지)",
            biz_year,
            total_count,
            year_rows,
            pages,
        )

    with factory() as db:
        _set_meta(db, _META_KEY_UPDATED_AT, datetime.now().isoformat(timespec="seconds"))
        _set_meta(db, _META_KEY_YEARS, ",".join(target_years))
        row_count = db.execute(select(func.count()).select_from(FscFinancialStat)).scalar_one()
        db.commit()

    return {
        "years": target_years,
        "rows_per_year": per_year,
        "inserted": total_inserted,
        "updated": total_updated,
        "skipped_non_separate_or_dummy": skipped,
        "row_count": row_count,
    }


def get_latest_stat_by_crno(
    db: Session, crnos: list[str]
) -> dict[str, FscFinancialStat]:
    """회사별로 **가장 최근 회계연도** 1건씩 반환한다.

    최신 연도일수록 적재가 덜 돼 있으므로(2025년이 2024년의 57%) 단일 연도로
    조회하면 커버리지가 크게 떨어진다 — 여러 해 중 최신을 고르는 것이 핵심.
    """
    if not crnos:
        return {}
    latest: dict[str, FscFinancialStat] = {}
    # SQLite는 바인드 파라미터 개수에 상한이 있어(기본 999, 빌드에 따라 32,766)
    # 후보 전체를 한 번에 `IN (...)`으로 넣으면 "too many SQL variables"로 터진다 —
    # Phase 1이 수천~수만 건으로 호출하는 함수라 반드시 나눠 조회해야 한다.
    unique = list(dict.fromkeys(crnos))
    for start in range(0, len(unique), _IN_CLAUSE_CHUNK):
        chunk = unique[start : start + _IN_CLAUSE_CHUNK]
        for row in db.execute(
            select(FscFinancialStat).where(FscFinancialStat.crno.in_(chunk))
        ).scalars():
            current = latest.get(row.crno)
            if current is None or row.biz_year > current.biz_year:
                latest[row.crno] = row
    return latest


def get_financial_stat_status(
    session_factory: sessionmaker[Session] | None = None,
) -> dict[str, Any]:
    factory = session_factory or get_session_factory()
    with factory() as db:
        row_count = db.execute(select(func.count()).select_from(FscFinancialStat)).scalar_one()
        updated_at = _get_meta(db, _META_KEY_UPDATED_AT) or None
        # `_META_KEY_YEARS`(마지막 크롤이 요청한 연도)가 아니라 **테이블에 실제로
        # 들어 있는 연도**를 센다. 한 해만 보강 크롤하면 메타에는 그 한 해만 남아
        # 실제로는 4개년이 적재돼 있는데 화면에는 "2023년 기준"으로만 보였다
        # (M8 5단계에서 이 값을 화면에 노출하며 발견). 사용자가 알고 싶은 것은
        # 어떤 연도의 참고값을 볼 수 있느냐이지 마지막 크롤 인자가 아니다.
        years = [
            str(year)
            for (year,) in db.execute(
                select(FscFinancialStat.biz_year)
                .distinct()
                .order_by(FscFinancialStat.biz_year)
            ).all()
            if year
        ]
    return {
        "row_count": row_count,
        "last_completed_at": updated_at,
        "years": years,
        "crawl_in_progress": updated_at is None and row_count > 0,
    }
