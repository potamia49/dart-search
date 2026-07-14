"""jobs 테이블 — 검색 조건 + 진행 상태.

상세개발계획.md §5:
    CREATE TABLE jobs (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at    TEXT, name TEXT,
      cond_region   TEXT,   -- JSON: {"sido":"경남","sigungu":["김해시","양산시"]}
      cond_revenue  TEXT,   -- JSON: {"min_krw":6000000000,"max_krw":15000000000}
      cond_industry TEXT,   -- JSON: ["C25","C29"]
      cond_period   TEXT,   -- JSON: {"bgn_de":"20250101","end_de":"20251231"}
      status        TEXT,   -- PENDING/RUNNING/PAUSED_QUOTA/DONE/FAILED/CANCELLED
      current_step  INTEGER, progress_done INTEGER, progress_total INTEGER,
      error_msg     TEXT
    );
"""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)

    cond_region: Mapped[str | None] = mapped_column(String, nullable=True)
    cond_revenue: Mapped[str | None] = mapped_column(String, nullable=True)
    cond_industry: Mapped[str | None] = mapped_column(String, nullable=True)
    cond_period: Mapped[str | None] = mapped_column(String, nullable=True)

    status: Mapped[str | None] = mapped_column(String, nullable=True)
    current_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_done: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(String, nullable=True)


# 상세개발계획.md §5 status 값 (하드코딩 대신 상수로 참조)
class JobStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED_QUOTA = "PAUSED_QUOTA"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
