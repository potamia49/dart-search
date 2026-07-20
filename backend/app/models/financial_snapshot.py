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
      revenue             INTEGER, cogs                INTEGER, gross_profit INTEGER,
      sga                 INTEGER, operating_income     INTEGER, net_income   INTEGER,
      parse_status        TEXT,   -- OK / PARTIAL / FAILED (results.parse_status와 동일 의미)
      parse_note          TEXT,
      from_current_period INTEGER,-- 1: 이 연도를 당기로 하는 공시에서 나온 값
                                  -- 0: 다음 연도 공시의 전기 열에서 임시로 채운 값
      UNIQUE(result_id, fiscal_year)
    );

회계연도 판정: 한 감사보고서 원문에는 결산기준일(PERIODTO, 당기 말)만 명시돼
있고 전기 말 날짜는 별도로 파싱하지 않는다(app/core/pipeline.py의
`_extract_fiscal_date` 참고) — 이 테이블에서는 당기의 fiscal_year를
PERIODTO 연도로, 전기의 fiscal_year를 "당기 연도 - 1"로 계산한다(연 1회
정기 감사가 기본 전제라는 실무적 가정, 상세개발계획.md §4-6 참고).

`(result_id, fiscal_year)` 유니크 제약으로 같은 회사의 같은 회계연도는 한
행만 유지한다. 어느 공시의 값을 그 연도의 값으로 삼을지는
`from_current_period`가 결정한다(2026-07-20 변경) — **그 연도를 당기로 하는
공시(1차 자료)를 항상 우선**하고, 아직 그런 공시를 못 연 연도만 다음 연도
공시의 전기 열로 임시(`from_current_period=0`)로 채운 뒤 자기 공시를 열게
되면 덮어쓴다(pipeline.py `_collect_history_for_result`). 정정 공시가 있으면
newest-first 순회 덕분에 정정본이 먼저 그 연도를 확정한다.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
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
    # 2026-07-20 변경: 계산값(매출총이익율 %)이 아니라 원문 "매출총이익"/
    # "매출총손실" 행을 직접 파싱한 금액이다(손실이면 음수).
    gross_profit: Mapped[int | None] = mapped_column(Integer, nullable=True)
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

    # 이 행의 수치·rcept_no 출처가 "그 연도를 **당기**로 하는" 감사보고서인지(1),
    # 아니면 다음 연도 공시의 **전기** 열에서 임시로 채워진 값인지(0).
    # 2026-07-20 추가 — 화면의 연도별 "원문 보기" 버튼이 "당기가 그 연도인 원문"을
    # 열어야 한다는 요구에서 나왔다. STEP 7이 newest-first로 순회하며 전기 열로
    # 먼저 채운 연도를 나중에 그 연도의 자기 공시로 덮어쓰는데, 이 플래그가
    # "아직 전기 유래(임시)"인 연도를 구분해 준다(pipeline.py STEP 7 참고).
    # 이 컬럼 도입 이전에 수집된 기존 행은 전부 0이며(실제로는 당기 유래일 수도
    # 있다), 화면은 0인 연도의 버튼에 "전기 기준" 라벨을 붙인다.
    from_current_period: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
