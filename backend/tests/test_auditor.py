"""app/parsers/auditor.py — 감사인(회계법인/감사반) 추출 테스트.

실제 원문 fixtures로 검증한다. 기대값은 원문 서명란/표지를 눈으로 대조해
확정한 것이며, 파서 규칙(글자 사이 공백 흡수/서명란 우선/직전 감사인 배제)이
각각 어떤 원문에서 필요한지 케이스마다 주석으로 남겼다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.parsers.auditor import extract_auditor, format_auditor

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _read_fixture(rcept_no: str) -> bytes:
    return (FIXTURES_DIR / rcept_no / f"{rcept_no}_00760.xml").read_bytes()


@pytest.mark.parametrize(
    "rcept_no, name, address",
    [
        # 서명란 이름이 "삼 일 회 계 법 인"처럼 글자 사이가 벌어져 있는 서식.
        ("20260630001111", "삼일회계법인", "서울특별시 용산구 한강대로 100"),
        # 이름 줄에 서명자가 붙는 서식("삼정회계법인 대표이사 김교태").
        ("20260630001108", "삼정회계법인", "서울특별시 강남구 테헤란로 152(역삼동, 강남파이낸스센터 27층)"),
        # 감사반 — "공인회계사"가 이름의 일부라 잘라내면 안 되고, 등록번호
        # 괄호("(제267호)")는 제거한다.
        ("20260630000731", "천일공인회계사감사반", "충청남도 천안시 서북구 서부대로 728, 302호"),
        ("20220127000408", "송림공인회계사감사반", "경기도 화성시 노작로 3길 35"),
        # 시도명이 단독 줄이고 나머지 주소가 다음 줄인 서식(줄 병합 필요).
        ("20260630000764", "다산회계법인", "서울특별시 강남구 영동대로 325(대치동, S-Tower 9층)"),
        # 2012년 구서식 + 주소 첫 토큰이 약칭("서울시")이라 표준 시도명으로 정규화된다.
        ("20120110000138", "서일회계법인", "서울특별시 서초구 잠원동 46-10 신영빌딩 3층"),
    ],
)
def test_extract_auditor_from_signature_block(rcept_no, name, address):
    info = extract_auditor(_read_fixture(rcept_no))
    assert info.name == name
    assert info.address == address


def test_extract_auditor_handles_name_after_suffix():
    """이름이 접미어 **뒤**에 오는 서식("회계법인 원지") — 캐시 250건 중 12%.

    fixtures에는 이 서식이 없어 실측 원문에서 확인한 최소 형태로 검증한다.
    """
    raw = (
        b"<DOCUMENT><P>\xea\xb0\x90\xec\x82\xac\xeb\xb3\xb4\xea\xb3\xa0\xec\x84\x9c</P>"
        b"<P>\xec\x84\x9c\xec\x9a\xb8\xec\x8b\x9c \xec\x84\x9c\xec\xb4\x88\xea\xb5\xac "
        b"\xed\x97\x8c\xeb\xa6\x89\xeb\xa1\x9c 1</P>"
        b"<P>\xed\x9a\x8c \xea\xb3\x84 \xeb\xb2\x95 \xec\x9d\xb8 \xec\x9b\x90 \xec\xa7\x80 "
        b"\xeb\x8c\x80\xed\x91\x9c\xec\x9d\xb4\xec\x82\xac \xed\x99\x8d\xea\xb8\xb8\xeb\x8f\x99</P>"
        b"</DOCUMENT>"
    )
    info = extract_auditor(raw)
    assert info.name == "회계법인 원지"  # 서명자("대표이사 홍길동")는 잘라낸다
    assert info.address == "서울특별시 서초구 헌릉로 1"  # "서울시" → 표준 시도명


@pytest.mark.parametrize("rcept_no, name", [("20260630000641", "정진세림회계법인"), ("20120110000508", "신한회계법인")])
def test_extract_auditor_falls_back_to_cover_when_no_signature_block(rcept_no, name):
    """서명란이 원문에 없는 서식(실측 31건 중 2건)은 표지 이름만 확보된다."""
    info = extract_auditor(_read_fixture(rcept_no))
    assert info.name == name
    assert info.address is None


def test_extract_auditor_ignores_prior_auditor_mentioned_in_body():
    """"기타사항" 문단의 **직전** 감사인("성문회계법인이 ... 감사하였으며")을
    현재 감사인으로 오인하지 않는다 — 서명란의 삼일회계법인이 채택돼야 한다."""
    raw = _read_fixture("20260630000634")
    assert b"\xec\x84\xb1\xeb\xac\xb8" in raw  # "성문"이 원문에 실제로 있는지 먼저 확인
    info = extract_auditor(raw)
    assert info.name == "삼일회계법인"


@pytest.mark.parametrize(
    "rcept_no, name",
    [
        # 주소와 이름이 한 줄에 공백 없이 이어지고("...한강대로 100삼 일 회 계 법 인")
        # 이름은 글자 사이가 벌어진 서식 — 잔여 주소 단위 글자 제거 조건이
        # "숫자 뒤 1글자 토큰"이던 시절 이름 첫 글자를 먹어 "일회계법인"이 됐다.
        ("20230504000483", "삼일회계법인"),
        # 같은 문제의 다른 변형 — 잔여 "층"이 이름 첫 글자와 한 토큰으로 붙어
        # ("8층신 한 회 계 법 인") 옛 조건은 발동조차 못 하고 "층신한회계법인"이 됐다.
        ("20250331002349", "신한회계법인"),
    ],
)
def test_extract_auditor_keeps_spaced_name_glued_to_address(rcept_no, name):
    assert extract_auditor(_read_fixture(rcept_no)).name == name


@pytest.mark.parametrize(
    "rcept_no, name",
    [
        # "...현대타워오피스텔 705호 동화공인회계사감사반" — 숫자에 붙은 "호"만
        # 떼어낸다. 이름이 "동"으로 시작하므로, 주소 단위라고 "동"까지 절단
        # 대상에 넣으면 안 된다는 근거 케이스이기도 하다.
        ("20250428000005", "동화공인회계사감사반"),
        # "...원방빌딩 14층 회 계 법 인 지 평" — "층" 제거 + 접미어 뒤 이름 결합.
        ("20230403002788", "회계법인 지평"),
    ],
)
def test_extract_auditor_strips_trailing_address_unit_char(rcept_no, name):
    assert extract_auditor(_read_fixture(rcept_no)).name == name


@pytest.mark.parametrize(
    "rcept_no, name",
    [
        # "...우동 1468번지 태흥공인회계사감사반" — 주소 단위가 두 글자인 지번
        # 주소. 1글자 집합만 보던 시절 "번지태흥공인회계사감사반"으로 오염돼,
        # 같은 감사반인데 연도별 표기가 갈려 감사인 변동으로 오판정됐다.
        ("20240828000306", "태흥공인회계사감사반"),
        # "...송파동 50번지 동산 공인회계사 감사반" — "번지"를 뗀 뒤 이름이 "동"으로
        # 시작한다. 글자 단위로 떼면 "산공인회계사감사반"이 되므로 통짜 2글자 매치여야 한다.
        ("20240403003225", "동산공인회계사감사반"),
    ],
)
def test_extract_auditor_strips_trailing_address_unit_word(rcept_no, name):
    assert extract_auditor(_read_fixture(rcept_no)).name == name


def test_extract_auditor_keeps_name_when_bunji_is_glued_to_it():
    """"번지" 뒤에 공백이 없으면 떼지 않는다 — "100번지성회계법인"은 "100번지"+
    "성회계법인"인지 "100번"+"지성회계법인"인지 구분할 수 없어(캐시 0건),
    이름을 조용히 잘라내는 대신 손대지 않는다("지성회계법인" 보호)."""
    raw = (
        "<DOCUMENT><P>감사보고서</P>"
        "<P>서울시 서초구 헌릉로 100번지지성회계법인</P></DOCUMENT>"
    ).encode("utf-8")
    assert extract_auditor(raw).name == "번지지성회계법인"


def test_format_auditor_uses_first_two_address_tokens():
    assert format_auditor("안경회계법인", "경상남도 창원시 중앙대로 1") == "안경회계법인(경상남도 창원시)"
    # 주소를 확보하지 못하면 이름만 표시한다(괄호 없이).
    assert format_auditor("영원감사반", None) == "영원감사반"
    assert format_auditor(None, "경상남도 창원시") is None
