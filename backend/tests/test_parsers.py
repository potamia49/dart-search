"""파서 단위 테스트 (fixtures 기반).

상세개발계획.md: "backend/tests/fixtures/에 샘플 감사보고서 원문을 두고 파서
단위 테스트를 작성" (M3 범위). 2026-07-15 실제 DART API로 원문 30건을
확보했다(2026년 4~6월 접수분 25건 + 2012년 초 접수분 5건,
backend/tests/fixtures/manifest.json). 이 파일은 그 실제 원문을 그대로
읽어 파서를 검증한다 — 합성(fake) XML이 아니라 실제 회사의 실제 수치를
ground truth로 사용한다(각 테스트 docstring에 근거 라인을 남겨둔다).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.parsers.audit_opinion import extract_audit_opinion
from app.parsers.base import (
    ACCOUNT_NAME_ALIASES,
    DIRECT_FINANCIAL_FIELDS,
    compute_gross_margin,
    determine_parse_status,
    normalize_account_label,
    parse_won_amount,
)
from app.parsers.pdf_parser import parse_pdf_financials
from app.parsers.xml_parser import parse_xml_financials

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_fixtures_dir_exists():
    assert FIXTURES_DIR.is_dir()


def _read_fixture(rcept_no: str) -> bytes:
    path = FIXTURES_DIR / rcept_no / f"{rcept_no}_00760.xml"
    return path.read_bytes()


# ---------------------------------------------------------------------------
# base.py — 계정과목 정규화 / 금액 파싱 유틸
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label, expected",
    [
        ("Ⅰ.매출액", "매출액"),  # 유니코드 로마숫자 접두어 (한국학술정보 실측)
        ("Ⅴ. 영업손실", "영업손실"),  # 로마숫자+마침표+공백
        ("I. 유동자산", "유동자산"),  # 아스키 로마숫자 접두어 (2012년 원문 실측)
        ("II. 영업비용(주11, 12)", "영업비용"),  # "주11, 12" 각주(숫자/콤마/공백만)도 제거됨
        ("(1) 당좌자산", "당좌자산"),  # 괄호 번호 접두어
        ("가.기초상품재고액", "기초상품재고액"),  # 가나다 접두어
        ("자      산      총      계", "자산총계"),  # 글자 사이 공백(패라매트릭코리아 실측)
        ("자　산　　총　계", "자산총계"),  # 전각 공백(2012년 원문 실측)
        ("Ⅳ. 판매비와관리비(주석13)", "판매비와관리비"),  # "주석13" 각주 제거
        ("Ⅱ.매출원가(주6)", "매출원가"),  # "주6" 축약 각주도 제거 (티디케이전자한국 실측)
        ("Ⅷ. 당기순이익(손실)(주석10)", "당기순이익(손실)"),  # 각주만 제거, 의미있는 (손실)은 보존
        ("수익(매출액)", "수익(매출액)"),  # 괄호가 각주가 아니라 항목명 자체라 보존
    ],
)
def test_normalize_account_label(label, expected):
    assert normalize_account_label(label) == expected


def test_account_name_aliases_cover_combined_loss_labels():
    """(손실) 접미가 붙은 라벨도 정규화 후 표준 필드로 매핑돼야 한다."""
    assert ACCOUNT_NAME_ALIASES[normalize_account_label("Ⅷ. 당기순이익(손실)(주석10)")] == "net_income"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("12,426,276,421", 12426276421.0),
        ("(393,502,380)", -393502380.0),  # 괄호 표기 = 음수
        ("-", 0.0),  # 명시적 0 표기 (부채/자산 항목이 실제로 0원인 경우)
        ("", None),  # 이 그룹에서 안 쓰는 열(당기/전기 상세·합계 중 미사용)
        ("  ", None),
    ],
)
def test_parse_won_amount(text, expected):
    assert parse_won_amount(text) == expected


def test_compute_gross_margin_handles_zero_revenue():
    assert compute_gross_margin(0, 100) is None
    assert compute_gross_margin(None, 100) is None
    assert compute_gross_margin(100, None) is None
    assert compute_gross_margin(100, 60) == pytest.approx(40.0)


def test_determine_parse_status_no_table_is_partial():
    status, note = determine_parse_status({}, {}, found_any_table=False)
    assert status == "PARTIAL"
    assert "찾을 수 없음" in note


def test_determine_parse_status_missing_fields_is_partial():
    values_cur = {f: 1.0 for f in DIRECT_FINANCIAL_FIELDS}
    values_cur["cogs"] = None
    values_prv = dict(values_cur)
    status, note = determine_parse_status(values_cur, values_prv, found_any_table=True)
    assert status == "PARTIAL"
    assert "cogs" in note


def test_determine_parse_status_all_fields_present_is_ok():
    values = {f: 1.0 for f in DIRECT_FINANCIAL_FIELDS}
    status, note = determine_parse_status(values, values, found_any_table=True)
    assert status == "OK"
    assert note is None


# ---------------------------------------------------------------------------
# xml_parser.py — 실제 원문 기반 통합 테스트
# ---------------------------------------------------------------------------


def test_parse_xml_financials_ok_unqualified_opinion():
    """한국학술정보(rcept_no=20260630000641), 적정의견, 완전한 재무제표 첨부.

    금액은 원문(tests/fixtures/20260630000641)의 자산총계/부채총계/자본총계/
    매출액/매출원가/영업이익/당기순이익 TE 셀 값을 그대로 옮겨 왔다.
    """
    raw = _read_fixture("20260630000641")
    parsed = parse_xml_financials(raw)

    assert parsed.parse_status == "OK"
    assert parsed.parse_note is None

    assert parsed.values_cur["total_assets"] == 46_609_006_893
    assert parsed.values_prv["total_assets"] == 50_320_613_406
    assert parsed.values_cur["total_liab"] == 33_128_073_764
    assert parsed.values_prv["total_liab"] == 37_011_864_333
    assert parsed.values_cur["total_equity"] == 13_480_933_129
    assert parsed.values_prv["total_equity"] == 13_308_749_073
    assert parsed.values_cur["revenue"] == 39_148_198_762
    assert parsed.values_prv["revenue"] == 40_045_263_359
    assert parsed.values_cur["cogs"] == 23_905_559_602
    assert parsed.values_prv["cogs"] == 24_504_069_626
    assert parsed.values_cur["operating_income"] == 1_843_858_188
    assert parsed.values_prv["operating_income"] == 1_716_581_763
    assert parsed.values_cur["net_income"] == 172_184_056
    assert parsed.values_prv["net_income"] == 138_144_741

    expected_gm_cur = round((39_148_198_762 - 23_905_559_602) / 39_148_198_762 * 100, 2)
    expected_gm_prv = round((40_045_263_359 - 24_504_069_626) / 40_045_263_359 * 100, 2)
    assert parsed.values_cur["gross_margin"] == pytest.approx(expected_gm_cur)
    assert parsed.values_prv["gross_margin"] == pytest.approx(expected_gm_prv)


def test_parse_xml_financials_qualified_opinion_flips_loss_sign():
    """홈마리나속초호텔(rcept_no=20260630000895), 한정의견, 영업손실/당기순손실.

    원문 TE 셀에는 손실 금액이 양수로 찍혀 있다(예: "영업손실" 행 당기값
    2,264,996,073) — 표준 필드에는 부호를 뒤집어 음수로 저장해야 한다.
    비유동부채 당기값은 원문에 "-"로 표기되어 있어(명시적 0) 0이어야 한다.
    """
    raw = _read_fixture("20260630000895")
    parsed = parse_xml_financials(raw)

    assert parsed.parse_status == "OK"
    assert parsed.values_cur["operating_income"] == -2_264_996_073
    assert parsed.values_prv["operating_income"] == -2_967_723_039
    assert parsed.values_cur["net_income"] == -4_643_612_253
    assert parsed.values_prv["net_income"] == -4_663_303_073
    assert parsed.values_cur["noncurrent_liab"] == 0.0  # 원문 표기 "-"
    assert parsed.values_prv["noncurrent_liab"] == 9_000_000_000
    assert parsed.values_cur["total_assets"] == 61_457_817_381
    assert parsed.values_cur["total_equity"] == 9_560_949_504


def test_parse_xml_financials_no_statements_attached_is_partial():
    """시대산업(rcept_no=20260630001111), 의견거절 — 재무제표 자체를 첨부하지 않음.

    원문에 "회사의 경영진은 ... 재무제표를 제시하지 아니함에 따라 동
    재무제표를 첨부하지 아니합니다"라고 명시되어 있어 ACLASS=FINANCE 테이블이
    아예 없다 — 파싱 실패(FAILED)가 아니라 "원문에 없음"(PARTIAL)으로
    판정해야 한다.
    """
    raw = _read_fixture("20260630001111")
    parsed = parse_xml_financials(raw)

    assert parsed.parse_status == "PARTIAL"
    assert all(parsed.values_cur.get(f) is None for f in DIRECT_FINANCIAL_FIELDS)
    assert all(parsed.values_prv.get(f) is None for f in DIRECT_FINANCIAL_FIELDS)
    assert "찾을 수 없음" in parsed.parse_note


def test_parse_xml_financials_service_format_missing_cogs_sga():
    """태보산업(rcept_no=20260630000859) — "영업수익/영업비용" 서비스업 서식.

    매출원가/판매비와관리비를 별도로 구분하지 않는 손익계산서 서식이라
    (부동산임대업 등 실측) revenue/operating_income은 채워지지만 cogs/sga는
    원문 자체에 해당 항목이 없어 구조적으로 None이어야 한다 — 파서 버그가
    아니라 실제 서식 차이이므로 PARTIAL로 정확히 반영되는지 확인한다.
    """
    raw = _read_fixture("20260630000859")
    parsed = parse_xml_financials(raw)

    assert parsed.parse_status == "PARTIAL"
    assert parsed.values_cur.get("cogs") is None
    assert parsed.values_cur.get("sga") is None
    assert parsed.values_cur["revenue"] is not None
    assert parsed.values_cur["operating_income"] is not None
    assert parsed.values_cur["total_assets"] is not None


def test_parse_xml_financials_recovers_from_malformed_entities():
    """2012년 원문(rcept_no=20120110000251)은 본문에 이스케이프 안 된 "&cr;"
    이 섞여 있어 엄격 XML 파싱은 실패한다 — recover 모드로 구조를 살려
    재무제표는 정상 추출되는지 확인한다(감사의견 문구도 구서식 "적정하게
    표시하고 있습니다"로 신서식과 다르다).
    """
    raw = _read_fixture("20120110000251")
    parsed = parse_xml_financials(raw)

    assert parsed.parse_status == "OK"
    assert parsed.values_cur["total_assets"] is not None
    assert parsed.values_cur["revenue"] is not None


def test_parse_xml_financials_invalid_xml_returns_failed():
    parsed = parse_xml_financials(b"not xml at all &&&")
    assert parsed.parse_status == "FAILED"


# ---------------------------------------------------------------------------
# audit_opinion.py — 실제 원문 + 서식 변형 테스트
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rcept_no, expected",
    [
        ("20260630000641", "적정"),  # 한국학술정보 — "공정하게 표시하고있습니다"(붙어씀)
        ("20260630000895", "한정"),  # 홈마리나속초호텔 — "...제외하고는...공정하게 표시하고 있습니다"
        ("20260630001111", "의견거절"),  # 시대산업 — 명시적 "의견거절" 마커 + 재무제표 미첨부
        ("20120110000251", "적정"),  # 2012년 구서식 — "적정하게 표시하고 있습니다"
    ],
)
def test_extract_audit_opinion_from_real_fixtures(rcept_no, expected):
    raw_text = _read_fixture(rcept_no).decode("utf-8", errors="ignore")
    assert extract_audit_opinion(raw_text) == expected


def test_extract_audit_opinion_adverse_synthetic():
    """실측 표본에 부적정 사례가 없어 DART 표준 문안 기준으로 합성 텍스트를 검증한다."""
    text = "<P>우리의 의견으로는 재무제표가 공정하게 표시하고 있지 않습니다.</P>"
    assert extract_audit_opinion(text) == "부적정"


def test_extract_audit_opinion_none_when_no_marker_found():
    assert extract_audit_opinion("<P>관련 없는 본문입니다.</P>") is None


# ---------------------------------------------------------------------------
# pdf_parser.py — 실제 PDF 표본 없음(2026-07-15 기준 원문 30건 전부 XML).
# 손상/비-PDF 입력에 대한 방어 동작만 확인한다.
# ---------------------------------------------------------------------------


def test_parse_pdf_financials_invalid_bytes_returns_failed():
    parsed = parse_pdf_financials(b"this is not a pdf file")
    assert parsed.parse_status == "FAILED"
    assert parsed.parse_note is not None
