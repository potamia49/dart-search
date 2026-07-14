"""api_usage 테이블 — 일일 OpenDART API 호출량 카운터.

상세개발계획.md §5, §4-5: `dart_client.py`가 이 테이블을 이용해 상한(기본
19,000) 도달 시 Job을 PAUSED_QUOTA로 전환한다.
"""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ApiUsage(Base):
    __tablename__ = "api_usage"

    date: Mapped[str] = mapped_column(String, primary_key=True)  # YYYY-MM-DD
    call_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
