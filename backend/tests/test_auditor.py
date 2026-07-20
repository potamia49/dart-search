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


def test_format_auditor_uses_first_two_address_tokens():
    assert format_auditor("안경회계법인", "경상남도 창원시 중앙대로 1") == "안경회계법인(경상남도 창원시)"
    # 주소를 확보하지 못하면 이름만 표시한다(괄호 없이).
    assert format_auditor("영원감사반", None) == "영원감사반"
    assert format_auditor(None, "경상남도 창원시") is None
