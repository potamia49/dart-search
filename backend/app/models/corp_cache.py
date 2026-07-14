"""corp_cache / cache_meta 테이블.

상세개발계획.md §5:
    CREATE TABLE corp_cache (
      corp_code   TEXT PRIMARY KEY,   -- 8자리 고유번호
      corp_name   TEXT NOT NULL,
      stock_code  TEXT,               -- 상장사만 존재 (비상장 필터에 활용)
      modify_date TEXT
    );
    CREATE TABLE cache_meta (key TEXT PRIMARY KEY, value TEXT);  -- corp_cache 갱신일 등
"""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CorpCache(Base):
    """corpCode.xml 전체 고유번호 목록 캐시 (STEP 1 산출물)."""

    __tablename__ = "corp_cache"

    corp_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    corp_name: Mapped[str] = mapped_column(String, nullable=False)
    stock_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    modify_date: Mapped[str | None] = mapped_column(String, nullable=True)


class CacheMeta(Base):
    """corp_cache 갱신일 등 단순 key-value 메타 저장소."""

    __tablename__ = "cache_meta"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str | None] = mapped_column(String, nullable=True)
