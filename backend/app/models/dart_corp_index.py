"""dart_corp_index 테이블 — DART 기업개황 전역 인덱스.

상세개발계획.md §4-10 / M8 1단계. `fsc_corp_index`(금융위 `getCorpOutline_V2`
전수 크롤)를 대체하며, DART 전자공시 웹의 기업개황 화면(`dsae001`)이 쓰는
엔드포인트를 그대로 사용해 구축한다. 실제 크롤은
`app/core/dart_corp_index.py::crawl_dart_corp_index`가 담당하고 이 모듈은
스키마만 정의한다.

`fsc_corp_index`와의 결정적 차이(§4-10-A 실측):
- **`corp_code`가 PK다.** `search.ax` 응답에서 8자리 고유번호를 직접 얻으므로
  회사명 매칭이 필요 없다 — `fsc_corp_index`가 겪던 동명이인 오매칭
  (실측 11.6%, 김해시 24.8%)이 구조적으로 사라진다.
- `jurir_no`(법인등록번호, 하이픈 제거)가 100% 채워지며, 이것이 금융위
  요약재무 API(`getSummFinaStat_V2`)의 조회키 `crno`와 동일하다 →
  `fsc_financial_stat`과의 조인 키. 더미값 문제도 없다.
- `induty_code`에는 회사별 **정밀 코드(2~5자리)** 를 넣는다. 엑셀의 업종명을
  DART 업종 트리 코드로 역매핑해 얻으며(표본 100% 유일 매칭), 회사마다 부여
  깊이가 다르다(2자리 5.18% / 3자리 20.35% / 4자리 15.72% / 5자리 58.75%).
  화면 노출은 소분류(3자리)까지만 한다 — 더 깊이 노출하면 얕게 분류된 회사를
  조용히 누락시킨다(§4-10-G 열린 질문 2 결론).
"""

from __future__ import annotations

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DartCorpIndex(Base):
    __tablename__ = "dart_corp_index"
    __table_args__ = (
        Index("ix_dart_corp_index_region", "sido", "sigungu"),
        Index("ix_dart_corp_index_induty", "induty_code"),
        Index("ix_dart_corp_index_jurir_no", "jurir_no"),
    )

    # search.ax에서 직접 확보하는 DART 8자리 고유번호 (이름 매칭 불필요)
    corp_code: Mapped[str] = mapped_column(String, primary_key=True)

    corp_name: Mapped[str] = mapped_column(String, nullable=False)
    corp_name_norm: Mapped[str | None] = mapped_column(String, nullable=True)
    eng_name: Mapped[str | None] = mapped_column(String, nullable=True)
    disclosure_name: Mapped[str | None] = mapped_column(String, nullable=True)

    # 값이 있으면 상장사 → Phase 1 후보에서 제외 (감사보고서를 별도 공시하지 않음)
    stock_code: Mapped[str | None] = mapped_column(String, nullable=True)
    # 법인구분: 기타법인 / 유가증권시장 / 코스닥시장 / 코넥스시장
    corp_cls: Mapped[str | None] = mapped_column(String, nullable=True)

    ceo_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # 법인등록번호(하이픈 제거) = fsc_financial_stat.crno 조인 키
    jurir_no: Mapped[str | None] = mapped_column(String, nullable=True)
    bizr_no: Mapped[str | None] = mapped_column(String, nullable=True)

    address: Mapped[str | None] = mapped_column(String, nullable=True)
    sido: Mapped[str | None] = mapped_column(String, nullable=True)
    sigungu: Mapped[str | None] = mapped_column(String, nullable=True)
    homepage: Mapped[str | None] = mapped_column(String, nullable=True)

    # 회사별 정밀 코드(2~5자리). 노출 깊이(소분류)와 저장 깊이를 분리한다.
    induty_code: Mapped[str | None] = mapped_column(String, nullable=True)
    induty_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # 수집에 사용한 중분류(2자리) — 크롤 체크포인트/재수집 단위
    crawl_induty_code: Mapped[str | None] = mapped_column(String, nullable=True)

    est_date: Mapped[str | None] = mapped_column(String, nullable=True)
    acc_month: Mapped[str | None] = mapped_column(String, nullable=True)

    updated_at: Mapped[str | None] = mapped_column(String, nullable=True)
