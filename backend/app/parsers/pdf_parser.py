"""pdfplumber 기반 재무제표 파싱 (2순위, XML 파싱 실패/미제공 시).

상세개발계획.md §4-4. M3에서 구현 (dart-parser 에이전트 영역). 백엔드는
원문 다운로드/저장까지만 책임지므로, 이 모듈은 디렉터리 구조 스캐폴딩
목적의 골격만 둔다.
"""

from __future__ import annotations

from app.parsers.base import ParsedFinancials


def parse_pdf_financials(raw_pdf: bytes) -> ParsedFinancials:
    """감사보고서 원문 PDF에서 재무상태표/손익계산서를 파싱.

    M1 시점에는 미구현. 호출 시 NotImplementedError.
    """
    raise NotImplementedError("pdf_parser는 M3에서 구현 예정 (dart-parser 에이전트 영역)")
