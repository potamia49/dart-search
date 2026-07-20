"""금융위 기업기본정보 전역 인덱스(A1) — 상세개발계획.md §4-7 / §4-10.

M6 재설계(2026-07-15)에서 Phase 1의 A1~A4를 모두 담당하던 모듈이지만,
**M8 3단계(2026-07-20)에서 A2/A3/A4가 제거되어 A1(크롤/상태 조회)만 남았다.**

- A2(지역/업종 필터)는 `app/core/dart_corp_index.py`로 옮겨졌다 — FSC의
  자유 텍스트 `sic_name` 매칭 대신 DART가 부여한 `induty_code` prefix를 쓰므로
  업종 필터가 처음으로 정밀해졌다(2026-07-18 회귀의 근본 해결).
- A3(FSC 재무 사전 스크리닝)는 **폐기**됐다 — 1년 묵은 값으로 거르면 조건에
  맞는 회사의 25.3%를 조용히 놓친다(§4-10-C 실측). 매출액·총자산 판정 지점은
  Phase 2 B4 한 곳뿐이다.
- A4(이름 매칭 corp_code 해석)는 **불필요**해졌다 — `dart_corp_index`는
  `corp_code`가 PK라 동명이인 오매칭(실측 11.6%)이 구조적으로 사라졌다.

`fsc_corp_index` 테이블과 이 크롤러는 롤백 여지를 위해 남겨둔다(§4-10-E) —
새 파이프라인이 실전 Job으로 충분히 검증되면 별도 판단으로 정리한다.
그때까지 `POST /api/meta/fsc-index/refresh`로 계속 갱신할 수 있다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.core.dart_client import FscCorpInfoClient
from app.core.db import get_session_factory
from app.core.filters import parse_address
from app.models.corp_cache import CacheMeta
from app.models.fsc_corp_index import FscCorpIndex

logger = logging.getLogger(__name__)

_META_KEY_LAST_PAGE = "fsc_index_last_page"
_META_KEY_UPDATED_AT = "fsc_index_updated_at"


# ---------------------------------------------------------------------------
# A1 — FSC 전역 인덱스 구축/갱신
# ---------------------------------------------------------------------------


def _get_meta(db: Session, key: str) -> str | None:
    row = db.execute(select(CacheMeta).where(CacheMeta.key == key)).scalar_one_or_none()
    return row.value if row else None


def _set_meta(db: Session, key: str, value: str) -> None:
    row = db.execute(select(CacheMeta).where(CacheMeta.key == key)).scalar_one_or_none()
    if row is None:
        db.add(CacheMeta(key=key, value=value))
    else:
        row.value = value


def is_fsc_index_stale(
    session_factory: sessionmaker[Session] | None = None,
    ttl_days: int | None = None,
    settings: Settings | None = None,
) -> bool:
    """`fsc_corp_index`가 비어있거나 TTL(기본 180일)이 지났으면 True.

    `app/core/corp_cache.py::is_cache_stale`와 동일한 패턴.
    """
    settings = settings or get_settings()
    ttl_days = ttl_days if ttl_days is not None else settings.fsc_index_ttl_days
    session_factory = session_factory or get_session_factory()

    with session_factory() as db:
        updated_at_raw = _get_meta(db, _META_KEY_UPDATED_AT)
        has_rows = db.execute(select(FscCorpIndex.id).limit(1)).first() is not None

    if not has_rows or not updated_at_raw:
        return True
    try:
        updated_at = datetime.fromisoformat(updated_at_raw)
    except ValueError:
        return True
    return datetime.now() - updated_at > timedelta(days=ttl_days)


def get_fsc_index_status(
    session_factory: sessionmaker[Session] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """`GET /api/meta/fsc-index/status`(사용자 요청, 2026-07-15 추가)에서 사용.

    `is_fsc_index_stale()`과 같은 근거(`cache_meta`의 `_META_KEY_UPDATED_AT`)를
    쓰되, 화면에 그대로 보여줄 수 있게 행 수/완료 여부까지 함께 반환한다.
    `_META_KEY_LAST_PAGE`가 `"0"`이 아니면(A1이 마지막 페이지까지 못 돌고
    체크포인트만 남긴 상태) 전수 크롤이 아직 진행 중/미완료라는 뜻이라
    `last_completed_at`는 `None`으로 둔다 — "완료된 적 있는 갱신 시각"과
    "크롤이 진행 중"을 화면에서 구분해서 보여주기 위함이다.
    """
    settings = settings or get_settings()
    ttl_days = settings.fsc_index_ttl_days
    session_factory = session_factory or get_session_factory()

    with session_factory() as db:
        row_count = db.execute(select(func.count()).select_from(FscCorpIndex)).scalar_one()
        updated_at_raw = _get_meta(db, _META_KEY_UPDATED_AT)
        last_page_raw = _get_meta(db, _META_KEY_LAST_PAGE)

    # A1은 전수를 다 돌면 체크포인트를 "0"으로 리셋하고 그 순간에만
    # _META_KEY_UPDATED_AT을 기록한다(crawl_fsc_index 참고) — 즉
    # updated_at_raw가 있다는 것 자체가 "적어도 한 번은 완주했다"는 뜻이다.
    crawl_in_progress = bool(last_page_raw) and last_page_raw != "0" and not updated_at_raw

    is_stale = True
    if updated_at_raw:
        try:
            is_stale = datetime.now() - datetime.fromisoformat(updated_at_raw) > timedelta(
                days=ttl_days
            )
        except ValueError:
            is_stale = True

    return {
        "row_count": row_count,
        # crawl_fsc_index()가 새 크롤 시작 시 체크포인트를 ""(빈 문자열)로 리셋해
        # 두므로(위 crawl_in_progress 계산 참고), 완료 시각이 없는 상태는 항상
        # None으로 정규화해 응답한다 — "" 그대로 노출하면 API 계약("완료된 적
        # 없으면 null")과 어긋난다.
        "last_completed_at": updated_at_raw or None,
        "ttl_days": ttl_days,
        "is_stale": is_stale,
        "crawl_in_progress": crawl_in_progress,
    }


def _to_optional_int(raw: Any) -> int | None:
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _dedupe_batch_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 페이지(최대 100건) 안에서 crno/fss_corp_unq_no가 같은 item을 메모리에서
    미리 병합한다(§4-7 스파이크에서 관찰한 "유진금속공업 중복 3건"류 — 소스기관별
    중복 레코드가 같은 페이지에 나타나는 경우).

    2026-07-16: 페이지 단위 커밋 배칭 도입 전에는 item마다 별도 세션/커밋이라
    직전 item이 이미 DB에 반영돼 있어 중복 조회가 자연히 맞았지만, 배칭 이후
    같은 세션 안에서 처리하면 (autoflush=False라) 직전 item의 미반영 INSERT가
    다음 item의 조회에 안 보여 같은 키로 두 번 INSERT를 시도해 UNIQUE 위반이
    날 수 있다. item마다 `db.flush()`로 해결할 수도 있었지만 그러면 페이지당
    100번 DB 왕복이 남아 배칭 효과가 거의 사라졌다(실측 write 7~8초/페이지) —
    DB를 건드리기 전에 파이썬 dict로 먼저 병합해 페이지당 고유 키 수만큼만
    DB 왕복하도록 한다. 병합 규칙은 `_upsert_fsc_corp_index_item`과 동일하게
    "새 값이 있으면 나중 값으로 덮어쓴다"이며, crno/fss_corp_unq_no가 둘 다
    없는 item은 병합 키가 없어(중복 판별 불가) 그대로 통과시킨다.
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    standalone: list[dict[str, Any]] = []
    for item in items:
        crno_raw = (item.get("crno") or "").strip()
        fss_raw = (item.get("fssCorpUnqNo") or "").strip()
        valid_crno = crno_raw if crno_raw and crno_raw != "0000000000000" else None
        valid_fss = fss_raw or None
        key = valid_crno or valid_fss
        if key is None:
            standalone.append(item)
            continue
        if key not in merged:
            merged[key] = dict(item)
            order.append(key)
        else:
            for field_name, value in item.items():
                if value not in (None, ""):
                    merged[key][field_name] = value
    return [merged[key] for key in order] + standalone


def _upsert_fsc_corp_index_item(db: Session, item: dict[str, Any]) -> None:
    """FSC 응답 item 1건을 `crno` 기준으로 병합(merge) upsert(호출부가 커밋 책임을 진다).

    같은 회사의 소스기관별 중복 레코드는 필드별로 "새 값이 있으면 채택, 없으면
    기존 값 유지"하는 방식으로 병합한다(§4-7 스파이크 결과 4번 — 예: 유진금속공업
    중복 3건 중 일부만 `fssCorpUnqNo`/`sicNm`이 채워지는 식). `crno`가 없거나
    더미값("0000000000000")이면 `fss_corp_unq_no`로 재시도하고, 그마저 없으면
    새 행을 추가한다(중복 방지 불가 — 이런 레코드는 드물고 A2 지역 필터에서
    자연히 걸러진다).
    """
    crno_raw = (item.get("crno") or "").strip()
    fss_raw = (item.get("fssCorpUnqNo") or "").strip()
    valid_crno = crno_raw if crno_raw and crno_raw != "0000000000000" else None
    valid_fss = fss_raw or None

    address = (item.get("enpBsadr") or item.get("enpDtadr") or "").strip() or None
    sido, sigungu = parse_address(address)

    field_values: dict[str, Any] = {
        "corp_name": item.get("corpNm"),
        "corp_name_en": item.get("corpEnsnNm"),
        "ceo_name": item.get("enpRprFnm"),
        "bzno": item.get("bzno"),
        "address": address,
        "sido": sido,
        "sigungu": sigungu,
        "phone": item.get("enpTlno"),
        "sic_name": item.get("sicNm"),
        "est_date": item.get("enpEstbDt"),
        "fiscal_month": item.get("enpStacMm"),
    }
    employee_cnt = _to_optional_int(item.get("enpEmpeCnt"))

    # `crno`/`fss_corp_unq_no`는 부분 UNIQUE 인덱스(NULL/더미값 제외)라, SQLite
    # 쿼리 플래너가 `col = ?`만으로는 바인드 파라미터가 부분 인덱스 조건(NOT NULL/
    # 더미 아님)을 만족하는지 정적으로 증명할 수 없어 인덱스를 쓰지 않고 매번
    # 전체 테이블 스캔을 한다(실측 2026-07-16: 27만 행 기준 쿼리당 약 100ms,
    # A1 전수 크롤 병목의 실제 원인 — `EXPLAIN QUERY PLAN`으로 확인). `valid_crno`/
    # `valid_fss`는 이미 이 조건을 만족하도록 걸러진 값이므로, WHERE 절에 부분
    # 인덱스와 동일한 조건을 명시적으로 반복해 플래너가 인덱스를 쓰도록 유도한다
    # (같은 파일로 확인: 추가 후 `SEARCH ... USING INDEX`로 바뀜).
    existing = None
    if valid_crno:
        existing = db.execute(
            select(FscCorpIndex).where(
                FscCorpIndex.crno == valid_crno,
                FscCorpIndex.crno.isnot(None),
                FscCorpIndex.crno != "0000000000000",
            )
        ).scalar_one_or_none()
    if existing is None and valid_fss:
        existing = db.execute(
            select(FscCorpIndex).where(
                FscCorpIndex.fss_corp_unq_no == valid_fss,
                FscCorpIndex.fss_corp_unq_no.isnot(None),
                FscCorpIndex.fss_corp_unq_no != "",
            )
        ).scalar_one_or_none()

    if existing is None:
        existing = FscCorpIndex(crno=valid_crno, fss_corp_unq_no=valid_fss)
        db.add(existing)
    else:
        if existing.crno is None and valid_crno:
            existing.crno = valid_crno
        if not existing.fss_corp_unq_no and valid_fss:
            existing.fss_corp_unq_no = valid_fss

    for field_name, value in field_values.items():
        if value not in (None, ""):
            setattr(existing, field_name, value)
    if employee_cnt is not None:
        existing.employee_cnt = employee_cnt

    existing.fetched_at = datetime.now().isoformat(timespec="seconds")
    # 페이지 내 중복은 호출부(crawl_fsc_index)가 `_dedupe_batch_items()`로 미리
    # 병합해 두므로 여기서 flush할 필요가 없다 — 실제 커밋(디스크 fsync)은
    # 호출부가 페이지 단위로 한 번만 한다.


async def crawl_fsc_index(
    client: FscCorpInfoClient,
    session_factory: sessionmaker[Session] | None = None,
    *,
    max_pages: int | None = None,
    num_of_rows: int = 100,
    force: bool = False,
) -> dict[str, Any]:
    """A1: `getCorpOutline_V2`를 `corp_nm` 없이 전수 페이징해 `fsc_corp_index`를 upsert.

    - `corpRegMrktDcd == "E"`(해외/기타) 레코드는 저장하지 않는다(§4-7 —
      이런 레코드는 `crno`가 더미값이고 국내 주소 형식이 아니라 A2에서 자연히
      탈락하므로 저장 자체를 생략해도 기능상 문제가 없다).
    - 진행한 페이지 번호를 `cache_meta`(기존 corp_cache TTL 체크가 쓰는 그
      테이블)에 체크포인트로 저장해 중단 후 재개 가능하게 한다 — 전체 실행에
      약 10시간이 걸릴 것으로 실측됐으므로(§4-7) 필수적인 설계다.
    - `max_pages`는 **테스트/파일럿 전용**이다(전체 실행 시 None) — 지정하면
      그 페이지 수만큼만 처리하고 중단하며, 다음 호출 시 이어서 진행한다.
    - `force=True`면 체크포인트를 무시하고 1페이지부터 다시 시작한다(TTL이
      지나 전체를 다시 구축해야 할 때 관리자가 사용).
    - **주의**: 이 함수를 실제로 전체(약 12,821페이지) 실행하는 것은 이번
      작업 범위가 아니다 — `POST /api/meta/fsc-index/refresh`(관리자 전용
      엔드포인트)로 별도 트리거해야 한다.
    """
    session_factory = session_factory or get_session_factory()

    with session_factory() as db:
        last_page_raw = None if force else _get_meta(db, _META_KEY_LAST_PAGE)
    start_page = int(last_page_raw) + 1 if last_page_raw else 1

    # 첫 완주 이후 두 번째부터의 크롤(증분 재개/force 전면 재구축)에서도
    # `get_fsc_index_status()`의 `crawl_in_progress`가 이전 완주 시각이 남아있다는
    # 이유만으로 계속 False를 보고하지 않도록, 이번 실행이 완료되기 전까지는
    # "완료 시각 없음" 상태로 되돌려 둔다 — 실제 완료 시점(아래)에 다시 채워진다.
    with session_factory() as db:
        _set_meta(db, _META_KEY_UPDATED_AT, "")
        db.commit()

    page_no = start_page
    processed_pages = 0
    upserted = 0
    skipped_foreign = 0
    total_count: int | None = None

    while max_pages is None or processed_pages < max_pages:
        data = await client.get_corp_basic_info(page_no=page_no, num_of_rows=num_of_rows)
        body = data.get("response", {}).get("body", {})
        if total_count is None:
            total_count = int(body.get("totalCount") or 0)

        item_list = body.get("items", {}).get("item") or []
        if isinstance(item_list, dict):  # 단건 응답 시 dict로 오는 경우 대비
            item_list = [item_list]

        is_last_page = not item_list or bool(total_count and page_no * num_of_rows >= total_count)

        # 페이지(최대 100건) 전체를 세션 하나에 모아 커밋 1번으로 반영한다 —
        # 건별 커밋(2026-07-16 이전)은 SQLite fsync가 건마다 걸려 실측
        # 약 3.4행/초로 병목이었다(체크포인트도 같은 커밋에 실어 원자성 유지).
        with session_factory() as db:
            local_items = [item for item in item_list if item.get("corpRegMrktDcd") != "E"]
            skipped_foreign += len(item_list) - len(local_items)
            upserted += len(local_items)  # 페이지 내 병합 여부와 무관하게 "처리한 item 수"
            for item in _dedupe_batch_items(local_items):
                _upsert_fsc_corp_index_item(db, item)

            # 같은 키를 같은 세션에서 두 번 _set_meta하면(autoflush=False라 첫 호출의
            # pending insert가 두 번째 호출의 select에 보이지 않아) UNIQUE 위반이
            # 난다 — 최종값을 먼저 정해 한 번만 쓴다.
            if is_last_page and item_list:
                # 전수를 다 돌았으면 다음 크롤을 위해 체크포인트를 리셋해 둔다.
                _set_meta(db, _META_KEY_LAST_PAGE, "0")
                _set_meta(db, _META_KEY_UPDATED_AT, datetime.now().isoformat(timespec="seconds"))
            else:
                _set_meta(db, _META_KEY_LAST_PAGE, str(page_no))
            db.commit()

        processed_pages += 1
        page_no += 1

        if is_last_page:
            break

    return {
        "start_page": start_page,
        "processed_pages": processed_pages,
        "upserted": upserted,
        "skipped_foreign": skipped_foreign,
        "total_count": total_count,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
