"""financial_snapshots 테이블 — 회사별 연도(회계연도) 단위 재무 이력.

이번 확장(2026-07-15, "최근 N년치 재무정보 이력") 배경은 CLAUDE.md/
상세개발계획.md §4-6, §5를 참고. `results` 테이블의 `_cur`/`_prv` 컬럼은
"가장 최근 감사보고서 1건의 당기·전기"라는 기존 의미를 그대로 유지하고
(하위 호환, 컬럼 변경 없음), 이 테이블이 그 회사의 "연도별 재무 이력"을
별도로 보관한다 — `results` 1건(회사)에 `financial_snapshots` 여러 건
(회계연도별 1행)이 매달린다.

    CREATE TABLE financial_snapshots (
      id                 INTEGER PRIMARY KEY AUTOINCREMENT,
      result_id           INTEGER REFERENCES results(id),
      rcept_no            TEXT,   -- 이 연도 수치가 어느 공시(감사보고서)에서 나왔는지
      fiscal_year         TEXT,   -- 회계연도, 예: "2023" (fiscal_date의 연도 4자리)
      -- 표준 재무 13항목 (results의 _cur/_prv와 동일한 필드셋, 접미어 없음)
      current_assets      INTEGER, noncurrent_assets   INTEGER, total_assets INTEGER,
      current_liab        INTEGER, noncurrent_liab     INTEGER, total_liab   INTEGER,
      total_equity        INTEGER,
      revenue             INTEGER, cogs                INTEGER, gross_margin REAL,
      sga                 INTEGER, operating_income     INTEGER, net_income   INTEGER,
      parse_status        TEXT,   -- OK / PARTIAL / FAILED (results.parse_status와 동일 의미)
      parse_note          TEXT,
      UNIQUE(result_id, fiscal_year)
    );

회계연도 판정: 한 감사보고서 원문에는 결산기준일(PERIODTO, 당기 말)만 명시돼
있고 전기 말 날짜는 별도로 파싱하지 않는다(app/core/pipeline.py의
`_extract_fiscal_date` 참고) — 이 테이블에서는 당기의 fiscal_year를
PERIODTO 연도로, 전기의 fiscal_year를 "당기 연도 - 1"로 계산한다(연 1회
정기 감사가 기본 전제라는 실무적 가정, 상세개발계획.md §4-6 참고).

`(result_id, fiscal_year)` 유니크 제약으로 같은 회사의 같은 회계연도는 한
행만 유지한다 — STEP 7이 최신 rcept_no(정정 포함)를 우선 처리하도록
설계되어 있어(더 최근에 접수된 공시가 그 연도 값을 먼저 채운다), 이후
더 오래된 공시에서 같은 연도가 다시 나와도 "이미 있으면 건너뜀"으로
자연히 최신 값이 유지된다(pipeline.py `_collect_history_for_result`).
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FinancialSnapshot(Base):
    __tablename__ = "financial_snapshots"
    __table_args__ = (UniqueConstraint("result_id", "fiscal_year", name="uq_financial_snapshot_result_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    result_id: Mapped[int | None] = mapped_column(ForeignKey("results.id"), nullable=True)
    rcept_no: Mapped[str | None] = mapped_column(String, nullable=True)
    fiscal_year: Mapped[str] = mapped_column(String, nullable=False)

    # 표준 재무 13항목 (results의 _cur/_prv와 동일 필드셋, 접미어 없음)
    current_assets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    noncurrent_assets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_assets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_liab: Mapped[int | None] = mapped_column(Integer, nullable=True)
    noncurrent_liab: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_liab: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_equity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revenue: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cogs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gross_margin: Mapped[float | None] = mapped_column(Float, nullable=True)  # %
    sga: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operating_income: Mapped[int | None] = mapped_column(Integer, nullable=True)
    net_income: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 현금흐름표 4항목 (§4-8, 2026-07-19) — results의 cf_*와 동일 필드셋, 접미어 없음
    cf_operating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cf_investing: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cf_financing: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cf_ending_cash: Mapped[int | None] = mapped_column(Integer, nullable=True)

    parse_status: Mapped[str | None] = mapped_column(String, nullable=True)  # OK/PARTIAL/FAILED
    parse_note: Mapped[str | None] = mapped_column(String, nullable=True)
