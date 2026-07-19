"""Phase 1(공공데이터 발굴) A1~A4 — 상세개발계획.md §4-7.

M6 아키텍처 재설계(2026-07-15)의 핵심 모듈. DART에는 지역 검색이 없어 STEP
2(전국 후보 수집)가 STEP 3(company.json 지역 필터)보다 먼저 실행돼야 했던
기존 파이프라인의 병목(§8 "M5에서 발견된 성능 병목")을, 공공데이터포털
금융위원회 API 2종(기업기본정보/기업재무정보)으로 DART를 거치기 전에
지역·업종·매출액·총자산을 먼저 걸러 대체한다.

- **A1** `crawl_fsc_index`: `getCorpOutline_V2`를 `corp_nm` 없이 전수
  페이징해 `fsc_corp_index`(Job과 무관한 전역 캐시)를 구축/갱신한다.
- **A2** `filter_local_candidates`: `fsc_corp_index`에서 지역/업종을 API
  호출 없이 DB 쿼리만으로 1차 스크리닝한다.
- **A3** `enrich_and_screen_financials`: A2 통과 후보만
  `getSummFinaStat_V2`로 매출액·총자산을 보강하고 안전마진을 두고 스크리닝한다.
- **A4** `resolve_candidates`: A3 통과 후보의 corp_code(DART 8자리)를 확정한다.

기존 `app/core/pipeline.py`/`app/core/filters.py`의 헬퍼(`parse_address`,
`region_matches`, `normalize_corp_name`)와 동시성 제한 패턴(기존 STEP3의
동시 5건 제한 세마포어)을 그대로 재사용한다 — 새 주소 파서/새 필터
로직을 만들지 않는다.

**A1 전수 크롤(약 12,821페이지, 실측 약 10.2시간 예상)은 이 모듈의 함수를
호출하는 쪽(관리자용 `POST /api/meta/fsc-index/refresh`, 아래 참고)이 명시적으로
트리거해야 한다** — `app/core/pipeline.py::run_job_phase1()`은 이미 채워진
`fsc_corp_index`를 재사용할 뿐, Job 실행 안에서 전수 크롤을 시작하지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.core.dart_client import FscCorpInfoClient
from app.core.db import get_session_factory
from app.core.filters import cond_sido_list, normalize_corp_name, parse_address, region_matches
from app.core.industry_data import INDUSTRIES
from app.models.corp_cache import CacheMeta, CorpCache
from app.models.fsc_corp_index import FscCorpIndex

logger = logging.getLogger(__name__)

# A3 안전마진 — Phase 1 스크리닝은 "확정치가 아닌" 최신연도 추정값(천원 단위
# 절삭 오차 포함)이라 조건 범위 경계에서 실제로는 통과해야 할 후보를 섣불리
# 떨어뜨리지 않기 위해 ±10% 여유를 둔다. 최종 확정은 항상 Phase 2 B4(원문
# 파싱값 기준 사후 필터)가 담당한다(상세개발계획.md §4-7-2).
_ASSET_SCREEN_MARGIN = 0.10

# STEP 3(company.json)와 동일한 동시 호출 제한 패턴 재사용.
_FSC_CONCURRENCY_LIMIT = 5

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


# ---------------------------------------------------------------------------
# A2 — 로컬 필터 (지역 + 업종 텍스트, API 호출 없음)
# ---------------------------------------------------------------------------


def _industry_labels_for_codes(codes: list[str]) -> list[str]:
    """`cond_industry`(DART `induty_code` prefix 목록, 예: ["C25"])를
    `industry_data.py`의 KSIC 대/중분류 라벨 텍스트로 변환한다.

    FSC는 업종을 코드 없는 자유 텍스트(`sicNm`)로만 제공해 기존 `induty_code`
    prefix 매칭(§4-2)과 스킴이 다르다 — 여기서는 완벽한 매칭이 아니라 1차
    스크리닝 용도의 느슨한 텍스트 포함 매칭 라벨만 뽑는다(정밀 확정은 Phase 2
    B1의 DART `company.json`이 담당, §4-7 열린 질문 5).

    실측(2026-07-18) 결과 FSC `sic_name`은 KSIC 세분류 수준의 매우 구체적인
    텍스트("곡물 도정업", "배합 사료 제조업" 등)라 중분류 라벨("식료품
    제조업")과 그대로는 거의 겹치지 않는다 — 중분류 코드만 선택하면 실제
    회사가 있어도 0건으로 걸러지는 회귀가 있었다. 자식(중분류) 코드가
    매칭되면 그 대분류(letter) 라벨도 함께 추가해, 대분류를 직접 선택했을
    때와 동일하게 "해당 대분류 전체"를 느슨하게 통과시킨다 — 이 A2 단계는
    docstring에 이미 명시된 대로 정밀 확정이 아닌 1차 스크리닝이므로,
    중분류 단위의 정밀도를 보장하지 못하더라도 대분류 단위로는 일관되게
    동작하는 쪽을 택했다.
    """
    labels: list[str] = []
    for raw_code in codes:
        code = (raw_code or "").strip().upper()
        if not code:
            continue
        for entry in INDUSTRIES:
            if entry["code"] == code:
                labels.append(entry["name"])
            for child in entry.get("children", []):
                if code.endswith(child["code"]):
                    labels.append(child["name"])
                    labels.append(entry["name"])
    return labels


def _sic_name_matches(sic_name: str | None, labels: list[str]) -> bool:
    if not sic_name:
        return False
    return any(label in sic_name or sic_name in label for label in labels)


def filter_local_candidates(
    db: Session,
    *,
    cond_region: dict[str, Any] | None,
    cond_industry: list[str] | None,
) -> list[FscCorpIndex]:
    """A2: `fsc_corp_index`에서 지역/업종을 API 호출 없이 DB 쿼리만으로 1차 스크리닝.

    시도는 SQL WHERE로 먼저 좁히고(전역 인덱스가 최대 수십만 건 규모라
    시도 단위 선필터 없이는 매 Job마다 전체를 스캔하게 되므로 성능상 필요),
    최종 판정은 `app/core/filters.py::region_matches()`를 그대로 재사용한다
    (시군구 목록 매칭 등 STEP3와 동일한 판정 로직을 새로 만들지 않기 위함).
    업종은 `sic_name` 자유 텍스트라 완벽할 필요 없이 느슨하게 매칭한다
    (§4-7 A2 설명 — 정밀 확정은 Phase 2 B1의 DART `company.json` 몫).
    """
    stmt = select(FscCorpIndex)

    cond_region = cond_region or {}
    cond_sidos = cond_sido_list(cond_region)
    if cond_sidos:
        stmt = stmt.where(FscCorpIndex.sido.in_(cond_sidos))

    rows = db.execute(stmt).scalars().all()
    industry_labels = _industry_labels_for_codes(cond_industry or [])

    filtered: list[FscCorpIndex] = []
    for row in rows:
        if not region_matches(row.sido, row.sigungu, cond_region):
            continue
        if industry_labels and not _sic_name_matches(row.sic_name, industry_labels):
            continue
        filtered.append(row)
    return filtered


# ---------------------------------------------------------------------------
# A3 — 매출액·총자산 보강 + 안전마진 스크리닝
# ---------------------------------------------------------------------------


def _extract_fina_stat_item(data: dict[str, Any]) -> dict[str, Any] | None:
    """`getSummFinaStat_V2` 응답에서 item 1건을 뽑는다. 결과 없으면 None.

    `getCorpOutline_V2`와 동일한 공공데이터포털 응답 봉투(response.body.items.item)
    구조를 따른다(§4-7 스파이크 실측). `totalCount=0`이면 그 연도 데이터가
    없다는 뜻(전기 조회 시 이 케이스가 발생 — §4-7 스파이크 결과 2번).
    """
    body = data.get("response", {}).get("body", {})
    total_count = int(body.get("totalCount") or 0)
    if total_count == 0:
        return None
    item_list = body.get("items", {}).get("item") or []
    if isinstance(item_list, dict):
        item_list = [item_list]
    if not item_list:
        return None
    return item_list[0]


async def _fetch_financial_stat_with_retry(
    client: FscCorpInfoClient, crno: str
) -> dict[str, Any] | None:
    """당해 연도로 먼저 조회하고, 데이터가 없으면(totalCount=0) 전년도로 1회 재시도.

    FSC 호출 자체가 실패(네트워크 오류 등)하면 재시도하지 않고 즉시 None을
    반환한다 — 호출부(`enrich_and_screen_financials`)가 이 경우를 "조회 실패,
    안전하게 통과"로 처리한다(상세개발계획.md §4-7-2).
    """
    this_year = datetime.now().year
    for biz_year in (str(this_year), str(this_year - 1)):
        try:
            data = await client.get_summary_financial_stat(crno=crno, biz_year=biz_year)
        except Exception as exc:  # noqa: BLE001 - FSC 실패는 여기서 흡수하고 통과시킨다
            logger.warning(
                "getSummFinaStat_V2 조회 실패 crno=%s biz_year=%s: %s", crno, biz_year, exc
            )
            return None
        item = _extract_fina_stat_item(data)
        if item is not None:
            return item
    return None


def _persist_financial_stat(
    session_factory: sessionmaker[Session], candidate: FscCorpIndex, item: dict[str, Any]
) -> None:
    """`item`으로 DB의 `fsc_corp_index` 행을 갱신하고, 호출부가 들고 있는 `candidate`
    (detached 객체) 자체도 함께 갱신한다.

    `candidate`는 A2에서 세션 밖으로 expunge된 detached 인스턴스라, DB만 갱신하고
    끝내면 `run_job_phase1()`이 이후 `resolve_candidate_pairs`/results 선삽입에
    쓰는 바로 그 객체에는 반영되지 않는다(별도 세션의 별도 객체이기 때문) —
    그래서 여기서 두 곳을 함께 갱신한다.
    """
    revenue_latest = _to_optional_int(item.get("enpSaleAmt"))
    revenue_biz_year = item.get("bizYear")
    total_assets_latest = _to_optional_int(item.get("enpTastAmt"))
    total_liab_latest = _to_optional_int(item.get("enpTdbtAmt"))
    total_equity_latest = _to_optional_int(item.get("enpTcptAmt"))
    fetched_at = datetime.now().isoformat(timespec="seconds")

    with session_factory() as db:
        row = db.get(FscCorpIndex, candidate.id)
        if row is not None:
            row.revenue_latest = revenue_latest
            row.revenue_biz_year = revenue_biz_year
            row.total_assets_latest = total_assets_latest
            row.total_liab_latest = total_liab_latest
            row.total_equity_latest = total_equity_latest
            row.fetched_at = fetched_at
            db.commit()

    candidate.revenue_latest = revenue_latest
    candidate.revenue_biz_year = revenue_biz_year
    candidate.total_assets_latest = total_assets_latest
    candidate.total_liab_latest = total_liab_latest
    candidate.total_equity_latest = total_equity_latest
    candidate.fetched_at = fetched_at


def _in_range_with_margin(value: int | None, cond: dict[str, Any] | None, margin: float) -> bool:
    """value가 cond(min_krw/max_krw) 범위에서 ±margin 여유를 두고 벗어나면 False.

    value가 없으면(조회 실패 등) 무조건 통과 — 최종 판정은 Phase 2 B4가 담당.
    """
    if value is None:
        return True
    cond = cond or {}
    min_krw = cond.get("min_krw")
    max_krw = cond.get("max_krw")
    if min_krw is not None and value < min_krw * (1 - margin):
        return False
    if max_krw is not None and value > max_krw * (1 + margin):
        return False
    return True


def _passes_screen(
    item: dict[str, Any],
    cond_revenue: dict[str, Any] | None,
    cond_total_assets: dict[str, Any] | None,
) -> bool:
    revenue = _to_optional_int(item.get("enpSaleAmt"))
    total_assets = _to_optional_int(item.get("enpTastAmt"))
    if not _in_range_with_margin(revenue, cond_revenue, _ASSET_SCREEN_MARGIN):
        return False
    if not _in_range_with_margin(total_assets, cond_total_assets, _ASSET_SCREEN_MARGIN):
        return False
    return True


async def enrich_and_screen_financials(
    client: FscCorpInfoClient,
    session_factory: sessionmaker[Session] | None,
    candidates: list[FscCorpIndex],
    *,
    cond_revenue: dict[str, Any] | None,
    cond_total_assets: dict[str, Any] | None,
) -> list[FscCorpIndex]:
    """A3: A2 통과 후보만 `getSummFinaStat_V2`로 매출액/총자산을 보강하고 스크리닝.

    STEP 3(company.json)와 동일한 동시 호출 제한(5건, `_FSC_CONCURRENCY_LIMIT`)을
    재사용한다. `crno`가 없는 후보(이론상 A1에서 이미 걸러졌어야 하지만 방어적으로),
    조회 실패, 또는 데이터가 없는 후보는 **안전하게 통과**시킨다(최종 판정은
    Phase 2 B4가 원문 파싱값으로 항상 정확히 수행) — 입력 순서를 그대로 유지한다.
    """
    session_factory = session_factory or get_session_factory()
    semaphore = asyncio.Semaphore(_FSC_CONCURRENCY_LIMIT)
    keep_flags: dict[int, bool] = {}

    async def _process(candidate: FscCorpIndex) -> None:
        if not candidate.crno:
            keep_flags[candidate.id] = True
            return
        async with semaphore:
            item = await _fetch_financial_stat_with_retry(client, candidate.crno)
        if item is None:
            keep_flags[candidate.id] = True
            return
        _persist_financial_stat(session_factory, candidate, item)
        keep_flags[candidate.id] = _passes_screen(item, cond_revenue, cond_total_assets)

    await asyncio.gather(*(_process(c) for c in candidates))
    return [c for c in candidates if keep_flags.get(c.id, True)]


# ---------------------------------------------------------------------------
# A4 — 후보 확정 (corp_code 해석)
# ---------------------------------------------------------------------------


def _build_corp_cache_name_index(db: Session) -> dict[str, str]:
    rows = db.execute(select(CorpCache.corp_code, CorpCache.corp_name)).all()
    index: dict[str, str] = {}
    for corp_code, corp_name in rows:
        norm = normalize_corp_name(corp_name or "")
        if norm and norm not in index:
            index[norm] = corp_code
    return index


def resolve_candidate_pairs(
    db: Session, candidates: list[FscCorpIndex]
) -> list[tuple[FscCorpIndex, str]]:
    """A4 내부 구현 — corp_code 확정과 원본 `FscCorpIndex` 레코드를 짝지어 반환.

    `resolve_candidates()`(공개 인터페이스, corp_code 리스트만 반환)와
    `app/core/pipeline.py::run_job_phase1()`(results 선삽입 시 회사명/주소 등
    메타데이터가 함께 필요)가 함께 재사용한다. 입력 순서를 유지한다.

    1. `fss_corp_unq_no`가 8자리 숫자로 채워져 있으면 그대로 corp_code로 채택
       (§4-7 스파이크로 확인된 DART corp_code와의 직접 조인 키).
    2. 없으면 안전망으로 `corp_cache`(corpCode.xml 캐시)에서
       `normalize_corp_name()` 이름 매칭을 시도한다.
    3. 그래도 안 되면 그 후보는 버린다(company.json 직접 확정 안전망은
       이번 스코프 밖 — §4-7-1 관련 지시사항 참고).
    """
    name_index: dict[str, str] | None = None
    pairs: list[tuple[FscCorpIndex, str]] = []

    for candidate in candidates:
        fss = (candidate.fss_corp_unq_no or "").strip()
        if fss and len(fss) == 8 and fss.isdigit():
            pairs.append((candidate, fss))
            continue

        if name_index is None:
            name_index = _build_corp_cache_name_index(db)

        norm_name = normalize_corp_name(candidate.corp_name or "")
        corp_code = name_index.get(norm_name) if norm_name else None
        if corp_code:
            pairs.append((candidate, corp_code))
        # 매칭 실패 시 조용히 버린다 — Job 전체를 실패시키지 않는다.

    return pairs


def resolve_candidates(db: Session, candidates: list[FscCorpIndex]) -> list[str]:
    """A4: A3 통과 후보의 corp_code(DART 8자리) 리스트를 확정한다."""
    return [corp_code for _candidate, corp_code in resolve_candidate_pairs(db, candidates)]
