"""파서 공통 인터페이스 + 계정과목명 정규화 사전.

상세개발계획.md §4-4 (M3 최대 리스크 구간). 감사보고서 원문(document.xml)
파싱 로직은 dart-parser 에이전트 영역이며, 백엔드는 원문 다운로드/저장/
체크포인트까지만 책임진다 (CLAUDE.md "하지 말 것"). 이 파일은 M1에서
디렉터리 구조만 맞추기 위한 골격이며, 실제 파싱 규칙/사전 보강은 M3에서
진행한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


# PRD 3-2절 표준 13항목 (당기/전기 각각) — results 테이블 컬럼과 1:1 대응
STANDARD_FINANCIAL_FIELDS: tuple[str, ...] = (
    "current_assets",
    "noncurrent_assets",
    "total_assets",
    "current_liab",
    "noncurrent_liab",
    "total_liab",
    "total_equity",
    "revenue",
    "cogs",
    "gross_margin",
    "sga",
    "operating_income",
    "net_income",
)

# 계정과목 표기 변형 → 표준 필드 매핑 사전 (v1, M3에서 검수하며 보강)
ACCOUNT_NAME_ALIASES: dict[str, str] = {
    "유동자산": "current_assets",
    "비유동자산": "noncurrent_assets",
    "자산총계": "total_assets",
    "유동부채": "current_liab",
    "비유동부채": "noncurrent_liab",
    "부채총계": "total_liab",
    "자본총계": "total_equity",
    "매출액": "revenue",
    "영업수익": "revenue",
    "수익(매출액)": "revenue",
    "매출원가": "cogs",
    "판매비와관리비": "sga",
    "판매비와 관리비": "sga",
    "영업이익": "operating_income",
    "영업손실": "operating_income",
    "당기순이익": "net_income",
    "당기순손실": "net_income",
}


@dataclass
class ParsedFinancials:
    """파서가 반환하는 결과 컨테이너 (results 테이블 적재 전 중간 표현)."""

    values_cur: dict[str, float | None]
    values_prv: dict[str, float | None]
    parse_status: str  # OK / PARTIAL / FAILED
    parse_note: str | None = None


class FinancialStatementParser(Protocol):
    """xml_parser.py / pdf_parser.py가 구현해야 하는 공통 인터페이스."""

    def parse(self, raw_bytes: bytes) -> ParsedFinancials: ...


# TODO(M3): 당기/전기 컬럼 판별 규칙("제 N 기" 헤더 비교) 구현
# TODO(M3): 검수 과정에서 ACCOUNT_NAME_ALIASES 지속 보강
