"""results 테이블 — 수집 결과 (회사 1건 = 1행).

상세개발계획.md §5 그대로 반영. 컬럼명/타입을 임의로 바꾸지 않는다.
"""

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Result(Base):
    __tablename__ = "results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    corp_code: Mapped[str | None] = mapped_column(String, nullable=True)
    rcept_no: Mapped[str | None] = mapped_column(String, nullable=True)

    # 기본정보 (PRD 3-1)
    corp_name: Mapped[str | None] = mapped_column(String, nullable=True)
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    ceo_name: Mapped[str | None] = mapped_column(String, nullable=True)
    induty_code: Mapped[str | None] = mapped_column(String, nullable=True)
    induty_name: Mapped[str | None] = mapped_column(String, nullable=True)
    fiscal_date: Mapped[str | None] = mapped_column(String, nullable=True)
    audit_opinion: Mapped[str | None] = mapped_column(String, nullable=True)

    # 요약 재무 (PRD 3-2): 당기(_cur) / 전기(_prv), 단위: 원
    current_assets_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_assets_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    noncurrent_assets_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    noncurrent_assets_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_assets_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_assets_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_liab_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_liab_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    noncurrent_liab_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    noncurrent_liab_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_liab_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_liab_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_equity_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_equity_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revenue_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revenue_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cogs_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cogs_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gross_margin_cur: Mapped[float | None] = mapped_column(Float, nullable=True)  # %
    gross_margin_prv: Mapped[float | None] = mapped_column(Float, nullable=True)  # %
    sga_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sga_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operating_income_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operating_income_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    net_income_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    net_income_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 상태
    parse_status: Mapped[str | None] = mapped_column(String, nullable=True)  # OK/PARTIAL/FAILED
    parse_note: Mapped[str | None] = mapped_column(String, nullable=True)
    excluded_by_revenue: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ParseStatus:
    OK = "OK"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
