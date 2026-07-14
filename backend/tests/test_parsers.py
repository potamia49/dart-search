"""파서 단위 테스트 (fixtures 기반).

상세개발계획.md: "backend/tests/fixtures/에 샘플 감사보고서 원문(10개사)을
두고 파서 단위 테스트를 작성" (M3 범위). 실제 원문 파일이 없으면 M3 작업을
시작할 수 없으므로, 표본 확보 후 이 파일을 채운다.

M1 시점에는 fixtures가 비어 있으므로 자리표시(placeholder) 테스트만 둔다.
"""

from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_fixtures_dir_exists():
    assert FIXTURES_DIR.is_dir()


# TODO(M3): fixtures/ 에 샘플 감사보고서 10개사 원문 확보 후 xml_parser/pdf_parser 단위 테스트 작성
