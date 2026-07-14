"""corpCode.xml 다운로드/캐싱 (파이프라인 STEP 1).

상세개발계획.md §4 STEP 1: "corpCode 캐시 확인/갱신 (7일 경과 시 재다운로드)".

OpenDART의 고유번호 API는 전체 공시대상회사의 (corp_code, corp_name,
stock_code, modify_date) 목록을 zip으로 감싼 단일 XML로 반환한다. 이를
`corp_cache` 테이블에 upsert하고, 갱신 시각을 `cache_meta`에 기록해 다음
호출 시 TTL(기본 7일) 이내면 재다운로드를 건너뛴다.

실제 다운로드(`refresh_corp_cache`)는 유효한 DART_API_KEY가 있어야 동작하지만,
이 모듈 자체는 키 없이도 임포트/유닛테스트 가능해야 한다.
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import datetime, timedelta
from typing import Any

from lxml import etree
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.core.dart_client import DartClient
from app.core.db import get_session_factory
from app.models.corp_cache import CacheMeta, CorpCache

logger = logging.getLogger(__name__)

_META_KEY_UPDATED_AT = "corp_cache_updated_at"
_META_KEY_COUNT = "corp_cache_count"


def parse_corp_code_zip(zip_bytes: bytes) -> list[dict[str, str | None]]:
    """corpCode.xml 다운로드 API가 반환한 zip 바이너리를 파싱해 레코드 리스트로 변환.

    zip 내부에는 `CORPCODE.xml` 단일 파일이 들어 있고, 구조는:
        <result>
          <list>
            <corp_code>...</corp_code>
            <corp_name>...</corp_name>
            <stock_code>...</stock_code>   (비상장은 공백)
            <modify_date>...</modify_date>
          </list>
          ...
        </result>
    """
    records: list[dict[str, str | None]] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not xml_names:
            raise ValueError("corpCode zip 안에 XML 파일이 없습니다.")
        with zf.open(xml_names[0]) as f:
            tree = etree.parse(f)

    for node in tree.getroot().findall("list"):
        corp_code = (node.findtext("corp_code") or "").strip()
        if not corp_code:
            continue
        corp_name = (node.findtext("corp_name") or "").strip()
        stock_code = (node.findtext("stock_code") or "").strip() or None
        modify_date = (node.findtext("modify_date") or "").strip() or None
        records.append(
            {
                "corp_code": corp_code,
                "corp_name": corp_name,
                "stock_code": stock_code,
                "modify_date": modify_date,
            }
        )
    return records


def _get_meta(db: Session, key: str) -> str | None:
    row = db.execute(select(CacheMeta).where(CacheMeta.key == key)).scalar_one_or_none()
    return row.value if row else None


def _set_meta(db: Session, key: str, value: str) -> None:
    row = db.execute(select(CacheMeta).where(CacheMeta.key == key)).scalar_one_or_none()
    if row is None:
        db.add(CacheMeta(key=key, value=value))
    else:
        row.value = value


def is_cache_stale(
    session_factory: sessionmaker[Session] | None = None,
    ttl_days: int | None = None,
    settings: Settings | None = None,
) -> bool:
    """corp_cache가 비어있거나 TTL(기본 7일)이 지났으면 True."""
    settings = settings or get_settings()
    ttl_days = ttl_days if ttl_days is not None else settings.corp_cache_ttl_days
    session_factory = session_factory or get_session_factory()

    with session_factory() as db:
        updated_at_raw = _get_meta(db, _META_KEY_UPDATED_AT)
        has_rows = db.execute(select(CorpCache.corp_code).limit(1)).first() is not None

    if not has_rows or not updated_at_raw:
        return True

    try:
        updated_at = datetime.fromisoformat(updated_at_raw)
    except ValueError:
        return True

    return datetime.now() - updated_at > timedelta(days=ttl_days)


def upsert_corp_cache(
    records: list[dict[str, str | None]],
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    """레코드 리스트를 corp_cache에 upsert하고 cache_meta 갱신일을 기록.

    SQLite 특성상 대량 upsert는 corp_code PK 충돌 시 갱신하는 방식으로 처리한다.
    """
    session_factory = session_factory or get_session_factory()
    now_iso = datetime.now().isoformat(timespec="seconds")

    with session_factory() as db:
        existing_codes = {row[0] for row in db.execute(select(CorpCache.corp_code)).all()}
        for rec in records:
            code = rec["corp_code"]
            if code in existing_codes:
                obj = db.get(CorpCache, code)
                obj.corp_name = rec["corp_name"]
                obj.stock_code = rec["stock_code"]
                obj.modify_date = rec["modify_date"]
            else:
                db.add(
                    CorpCache(
                        corp_code=code,
                        corp_name=rec["corp_name"],
                        stock_code=rec["stock_code"],
                        modify_date=rec["modify_date"],
                    )
                )
        _set_meta(db, _META_KEY_UPDATED_AT, now_iso)
        _set_meta(db, _META_KEY_COUNT, str(len(records)))
        db.commit()

    return len(records)


async def refresh_corp_cache(
    dart_client: DartClient,
    session_factory: sessionmaker[Session] | None = None,
    settings: Settings | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """corp_cache 갱신 필요 여부를 판단 후, 필요 시 다운로드/파싱/upsert 수행.

    Returns:
        {"refreshed": bool, "count": int, "checked_at": str}
    """
    settings = settings or get_settings()
    session_factory = session_factory or get_session_factory()

    if not force and not is_cache_stale(session_factory, settings.corp_cache_ttl_days, settings):
        logger.info("corp_cache가 최신 상태입니다 (TTL %s일 이내). 다운로드 생략.",
                     settings.corp_cache_ttl_days)
        return {"refreshed": False, "count": 0, "checked_at": datetime.now().isoformat()}

    logger.info("corpCode.xml 다운로드 시작...")
    zip_bytes = await dart_client.download_corp_code_zip()
    records = parse_corp_code_zip(zip_bytes)
    count = upsert_corp_cache(records, session_factory)
    logger.info("corp_cache 갱신 완료: %s건", count)

    return {"refreshed": True, "count": count, "checked_at": datetime.now().isoformat()}
