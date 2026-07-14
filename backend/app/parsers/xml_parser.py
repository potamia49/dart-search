"""DART 원본 XML 재무제표 파싱 (1순위).

상세개발계획.md §4-4. document.xml이 반환하는 zip 안의 DART 접수 XML에서
<TABLE> 구조로 재무상태표/손익계산서를 탐색한다. M3에서 구현 (dart-parser
에이전트 영역). 백엔드는 원문 다운로드/저장까지만 책임지므로, 이 모듈은
디렉터리 구조 스캐폴딩 목적의 골격만 둔다.
"""

from __future__ import annotations

from app.parsers.base import ParsedFinancials


def parse_xml_financials(raw_xml: bytes) -> ParsedFinancials:
    """감사보고서 원문 XML에서 재무상태표/손익계산서를 파싱.

    M1 시점에는 미구현. 호출 시 NotImplementedError.
    """
    raise NotImplementedError("xml_parser는 M3에서 구현 예정 (dart-parser 에이전트 영역)")
