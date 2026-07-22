"""results 테이블 — 수집 결과 (회사 1건 = 1행).

상세개발계획.md §5 그대로 반영. 컬럼명/타입을 임의로 바꾸지 않는다.
"""

from sqlalchemy import ForeignKey, Integer, String
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
    # 감사인(회계법인/감사반) 이름과 사무소 주소 (2026-07-20 추가, app/parsers/auditor.py).
    # 주소는 원문 그대로이되 첫 토큰(시도)만 표준 시도명으로 정규화해 저장한다 —
    # 화면은 앞 두 토큰을 잘라 "안경회계법인(경상남도 창원시)"로 표시한다.
    # 서명란이 없는 원문(실측 31건 중 2건)은 이름만 채워지고 주소는 NULL이다.
    auditor_name: Mapped[str | None] = mapped_column(String, nullable=True)
    auditor_address: Mapped[str | None] = mapped_column(String, nullable=True)

    # 금융위 요약재무(`fsc_financial_stat`) 참고값 — Phase 1이 후보 목록 화면에
    # 보여주려고 채운다(§4-10-C/D). **필터 판정에 절대 쓰지 않는다** —
    # 매출액/총자산 조건은 원문에서 파싱한 `revenue_cur`/`total_assets_cur`로만
    # B4에서 판정한다. 두 값을 `_cur` 컬럼에 섞어 넣던 구 A3 방식(사전 스크리닝
    # 추정치를 확정치 자리에 임시 저장)을 이 3컬럼으로 분리한 것이다.
    # `ref_fin_year`는 그 참고값의 회계연도(회사마다 확보된 최신 연도가 다르다).
    ref_revenue: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ref_total_assets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ref_fin_year: Mapped[str | None] = mapped_column(String, nullable=True)

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
    # 2026-07-20 변경: 매출액/매출원가로 계산한 매출총이익율(%)이 아니라
    # 원문의 "매출총이익"/"매출총손실" 행을 그대로 파싱한 금액이다(손실이면
    # 음수). 이전 gross_margin_cur/prv(REAL, %) 컬럼은 DB에 물리적으로
    # 남아있을 수 있으나 더 이상 읽거나 쓰지 않는다(기존 관행 — 소급 없음).
    gross_profit_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gross_profit_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sga_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sga_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operating_income_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operating_income_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    net_income_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    net_income_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 현금흐름표 4항목 (§4-8, 2026-07-19) — best-effort. parse_status 판정에는
    # 반영하지 않으며(base.py CF_FINANCIAL_FIELDS 주석 참고), 누락 시 parse_note에만 부기.
    cf_operating_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cf_operating_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cf_investing_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cf_investing_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cf_financing_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cf_financing_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cf_ending_cash_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cf_ending_cash_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 영업외수익/영업외비용 2항목 (2026-07-22, base.py NON_OPERATING_FINANCIAL_FIELDS
    # 참고) — CF 4항목과 완전히 동형인 best-effort 컬럼이다. parse_status 판정에는
    # 반영하지 않으며, 누락 시 parse_note에도 부기하지 않는다(CF와 동일한 설계).
    non_operating_income_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    non_operating_income_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    non_operating_expense_cur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    non_operating_expense_prv: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 상태
    parse_status: Mapped[str | None] = mapped_column(String, nullable=True)  # OK/PARTIAL/FAILED
    parse_note: Mapped[str | None] = mapped_column(String, nullable=True)
    excluded_by_revenue: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    excluded_by_assets: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # §4-7-2 총자산 필터(2026-07-15 추가) — excluded_by_revenue와 완전히 동일한
    # 패턴. total_assets_cur(원문 파싱으로 확보되는 값) 기준 사후 확정 판정이며,
    # Phase 1의 사전 스크리닝(app/core/fsc_index.py A3) 성공 여부와 무관하게
    # 항상 정확히 동작한다.
    excluded_manually: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 2026-07-18 추가 — CandidatesView(Phase 1 후보 목록)에서 사용자가 자유롭게
    # 켰다 껐다 할 수 있는 "선택 취소" 토글(`PATCH .../results/{id}/exclude`).
    # excluded_by_revenue/assets(사후 확정 필터)와 달리 이 값은 확정 판정이
    # 아니라 사용자 의사 표시일 뿐이라 phase=CANDIDATES 동안은 계속 뒤집을 수
    # 있다 — 실제 제외(행 삭제)는 start-financials 호출 시점에 일괄 반영된다.

    # "최근 1년 이내 DART 공시 없음" 배제 (2026-07-21 추가, 실사례 "주식회사 유진").
    # Phase 2 B2(`_backfill_latest_rcept_no_for_job`)가 rcept_no를 찾으려고 이미
    # 호출하는 list.json(pblntf_ty=F, 다년치 조회창) 응답을 그대로 재사용해 채우므로
    # 추가 API 호출은 0건이다 — excluded_by_revenue/assets와 동일하게 "이미 확보한
    # 데이터로 사후 판정"하는 패턴을 따른다. latest_disclosure_date는 그 회사의
    # 가장 최근 외부감사관련(F) 공시 접수일자(YYYYMMDD, rcept_no 앞 8자리에서 뽑음),
    # 공시를 아예 못 찾았으면 None이다. excluded_by_stale_disclosure는 그 날짜가
    # 365일보다 오래됐거나(또는 애초에 공시가 없어 날짜를 못 구했으면) 1 — 폐업/휴면/
    # 합병소멸 등으로 실질적으로 활동을 멈췄을 가능성이 높다고 보고 표시만 해 둔다.
    # 다른 excluded_by_* 처럼 행을 지우지 않는 순수 필터 플래그이고, 기존 완료 Job의
    # 행은 이 컬럼 추가만으로는 소급 계산되지 않아 기본값 0(제외 안 됨)으로 남는다.
    latest_disclosure_date: Mapped[str | None] = mapped_column(String, nullable=True)
    excluded_by_stale_disclosure: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )


class ParseStatus:
    OK = "OK"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
