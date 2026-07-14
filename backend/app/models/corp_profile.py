"""corp_profiles 테이블 — 기업개황 전역 캐시.

상세개발계획.md §5, §4-1(대응 2): Job과 무관하게 영구 보존되며 재검색 시
재사용된다. 지역 필터의 성능 핵심 테이블.

    CREATE TABLE corp_profiles (
      corp_code   TEXT PRIMARY KEY,
      corp_name   TEXT, address TEXT,
      sido        TEXT, sigungu TEXT,   -- 주소에서 파싱해 저장 → 지역 필터는 컬럼 매칭
      induty_code TEXT, phone TEXT, ceo_name TEXT,
      fetched_at  TEXT                  -- 갱신 주기 판단용 (기본 180일)
    );
"""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CorpProfile(Base):
    __tablename__ = "corp_profiles"

    corp_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    corp_name: Mapped[str | None] = mapped_column(String, nullable=True)
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    sido: Mapped[str | None] = mapped_column(String, nullable=True)
    sigungu: Mapped[str | None] = mapped_column(String, nullable=True)
    induty_code: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    ceo_name: Mapped[str | None] = mapped_column(String, nullable=True)
    fetched_at: Mapped[str | None] = mapped_column(String, nullable=True)
