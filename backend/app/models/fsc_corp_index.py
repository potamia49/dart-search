"""fsc_corp_index 테이블 — 금융위원회 기업기본정보 전역 인덱스.

상세개발계획.md §4-7 Phase 1 A1 / §5. Job과 무관한 전역 캐시로,
`getCorpOutline_V2`를 `corp_nm` 없이 전수 페이징해 구축한다(TTL 180일,
`Settings.fsc_index_ttl_days`). 실제 크롤은 `app/core/fsc_index.py::crawl_fsc_index`
(A1)가 담당하고, 이 모듈은 스키마만 정의한다.

⚠ PK 설계 (2026-07-15 실측으로 정정, CLAUDE.md 참고):
`fss_corp_unq_no`(=DART corp_code)는 무작위 표본에서 24%가 빈 문자열이라
PK로 쓸 수 없고, `crno`도 해외 레코드(`corpRegMrktDcd="E"`)는
"0000000000000" 더미값이 채워져 100% 신뢰할 수 없다. 그래서 실제 PK는
`id`(AUTOINCREMENT)로 두고, `crno`/`fss_corp_unq_no`는 각각 "더미/빈 값을
제외한" 부분(partial) UNIQUE 인덱스로만 둔다 — 동일 회사의 소스기관별
중복 레코드를 병합(merge)하는 기준 키는 `crno`다(§4-7 스파이크 결과 4번).
"""

from __future__ import annotations

from sqlalchemy import Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FscCorpIndex(Base):
    __tablename__ = "fsc_corp_index"
    __table_args__ = (
        Index(
            "ix_fsc_corp_index_crno",
            "crno",
            unique=True,
            sqlite_where=text("crno IS NOT NULL AND crno != '0000000000000'"),
        ),
        Index(
            "ix_fsc_corp_index_fss_corp_unq_no",
            "fss_corp_unq_no",
            unique=True,
            sqlite_where=text("fss_corp_unq_no IS NOT NULL AND fss_corp_unq_no != ''"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    crno: Mapped[str | None] = mapped_column(String, nullable=True)
    fss_corp_unq_no: Mapped[str | None] = mapped_column(String, nullable=True)

    corp_name: Mapped[str | None] = mapped_column(String, nullable=True)
    corp_name_en: Mapped[str | None] = mapped_column(String, nullable=True)
    ceo_name: Mapped[str | None] = mapped_column(String, nullable=True)
    bzno: Mapped[str | None] = mapped_column(String, nullable=True)
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    sido: Mapped[str | None] = mapped_column(String, nullable=True)
    sigungu: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    sic_name: Mapped[str | None] = mapped_column(String, nullable=True)
    est_date: Mapped[str | None] = mapped_column(String, nullable=True)
    fiscal_month: Mapped[str | None] = mapped_column(String, nullable=True)
    employee_cnt: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # A3(getSummFinaStat_V2)가 채우는 매출액/총자산/총부채/총자본 스크리닝 값 —
    # 최신 연도 1개년만 보유(다년치 불가), 최종 확정은 Phase 2 B4가 담당한다.
    revenue_latest: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revenue_biz_year: Mapped[str | None] = mapped_column(String, nullable=True)
    total_assets_latest: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_liab_latest: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_equity_latest: Mapped[int | None] = mapped_column(Integer, nullable=True)

    fetched_at: Mapped[str | None] = mapped_column(String, nullable=True)
