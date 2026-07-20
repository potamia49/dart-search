"""fsc_financial_stat 테이블 — 금융위 요약재무 스냅샷 (M8 2단계, §4-10-B).

⚠ `financial_snapshots`(STEP 7이 DART 원문에서 파싱한 다년 재무이력)와 **다른
테이블**이다. 이름이 비슷하니 혼동하지 말 것:

| | 출처 | 용도 |
|---|---|---|
| `financial_snapshots` | DART 감사보고서 원문 파싱 | 화면에 보여주는 **확정** 재무이력 |
| `fsc_financial_stat`  | 금융위 `getSummFinaStat_V2` 전수 페이징 | **참고 표시 + Phase 2 처리 순서** |

**이 테이블의 값으로 후보를 제외해서는 안 된다**(§4-10-C 확정 정책).
매출액·총자산 조건은 그 회사의 가장 최근 감사보고서 당기 값
(`results.revenue_cur`/`total_assets_cur`)으로만 판정하며, 판정 지점은
Phase 2 B4 한 곳이다. 실측상 1년 묵은 값으로 거르면 조건에 맞는 회사의
25.3%를, 2년 묵은 값이면 34.3%를 놓친다(중소기업 매출 |변동| 30% 초과가 32.0%).

`fnclDcd='120'`(별도요약재무제표)만 적재하므로 `(crno, biz_year)`가 유일하다 —
연결(`110`)을 함께 넣으면 같은 회사가 2행이 되고 실측에서 매출이 1.77배까지
차이났다(우리는 DART 원문에서 별도 재무제표를 파싱하므로 별도가 맞는 짝이다).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FscFinancialStat(Base):
    __tablename__ = "fsc_financial_stat"

    # 법인등록번호 13자리 = dart_corp_index.jurir_no 조인 키
    crno: Mapped[str] = mapped_column(String, primary_key=True)
    biz_year: Mapped[str] = mapped_column(String, primary_key=True)

    sale_amt: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 매출액
    tast_amt: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 총자산
    tdbt_amt: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 총부채
    tcpt_amt: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 총자본
    bzop_pft: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 영업이익
    crtm_npf: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 당기순이익

    updated_at: Mapped[str | None] = mapped_column(String, nullable=True)
