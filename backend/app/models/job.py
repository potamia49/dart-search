"""jobs 테이블 — 검색 조건 + 진행 상태.

상세개발계획.md §5:
    CREATE TABLE jobs (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at    TEXT, name TEXT,
      cond_region   TEXT,   -- JSON: {"sido":"경남","sigungu":["김해시","양산시"]}
      cond_revenue  TEXT,   -- JSON: {"min_krw":6000000000,"max_krw":15000000000}
      cond_total_assets TEXT, -- JSON: {"min_krw":...,"max_krw":...} (§4-7-2, 2026-07-15 추가,
                              -- cond_revenue와 동일 스키마, 선택 입력 — 미입력 시 무제한)
      cond_industry TEXT,   -- JSON: ["C25","C29"]
      cond_period   TEXT,   -- JSON: {"bgn_de":"20250101","end_de":"20251231"}
                             -- (M6 재설계 후에는 Phase 1에 미사용 — §4-7-1 참고.
                             --  구 파이프라인 호환을 위해 컬럼은 유지)
      status        TEXT,   -- PENDING/RUNNING/PAUSED_QUOTA/DONE/FAILED/CANCELLED
      phase         TEXT DEFAULT 'CANDIDATES', -- 'CANDIDATES'|'FINANCIALS' (§4-7-1, 2026-07-15 추가)
                             -- CANDIDATES: Phase1(A1~A4)까지만 실행하고 멈춘 상태.
                             -- FINANCIALS: 사용자가 POST /api/jobs/{id}/start-financials로
                             -- Phase2(B1~B5)를 트리거한 이후 상태.
      current_step  INTEGER, progress_done INTEGER, progress_total INTEGER,
      error_msg     TEXT,
      history_years INTEGER  -- STEP 7(최근 N년 재무이력)의 목표 연도수. 2/4/6/10만
                              -- 허용(app/api/jobs.py JobCreateRequest), 기본 4.
                              -- cond_* JSON들과 달리 "검색 필터"가 아니라 STEP7
                              -- 파이프라인 실행 파라미터라 별도 컬럼으로 둔다.
                              -- M6 재설계 후에는 Job 생성 시점이 아니라
                              -- start-financials 호출 시점에 값이 채워진다.
    );

`phase`/`cond_total_assets` 컬럼은 2026-07-15 M6 재설계로 추가됐다. 이미
실데이터가 든 dart_search.db에는 `app/core/db.py::run_schema_migrations()`가
`ALTER TABLE`로 컬럼을 추가한다(Alembic 미도입, CLAUDE.md 관행).
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
    cond_total_assets: Mapped[str | None] = mapped_column(String, nullable=True)
    cond_industry: Mapped[str | None] = mapped_column(String, nullable=True)
    cond_period: Mapped[str | None] = mapped_column(String, nullable=True)

    status: Mapped[str | None] = mapped_column(String, nullable=True)
    phase: Mapped[str | None] = mapped_column(String, nullable=True, default="CANDIDATES", server_default="CANDIDATES")
    current_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_done: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(String, nullable=True)
    history_years: Mapped[int | None] = mapped_column(Integer, nullable=True, default=4, server_default="4")


# 상세개발계획.md §5 status 값 (하드코딩 대신 상수로 참조)
class JobStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED_QUOTA = "PAUSED_QUOTA"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobPhase:
    """§4-7-1 (2026-07-15 추가) — Job이 지금 Phase 1(후보 확정)인지
    Phase 2(재무정보 수집)인지."""

    CANDIDATES = "CANDIDATES"
    FINANCIALS = "FINANCIALS"
