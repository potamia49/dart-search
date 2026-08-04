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

from app.parsers.account_detail import parse_account_detail
from app.parsers.audit_opinion import extract_audit_opinion
from app.parsers.document_sections import extract_section_html
from app.parsers.base import (
    ACCOUNT_NAME_ALIASES,
    BS_DETAIL_ACCOUNT_ALIASES,
    CF_DETAIL_ACCOUNT_ALIASES,
    CF_FINANCIAL_FIELDS,
    CONTRA_ROW_SPECS,
    DETAIL_ALIASES_BY_SECTION,
    DETAIL_FINANCIAL_FIELDS,
    DIRECT_FINANCIAL_FIELDS,
    IS_DETAIL_ACCOUNT_ALIASES,
    NON_OPERATING_FINANCIAL_FIELDS,
    STANDARD_FINANCIAL_FIELDS,
    determine_parse_status,
    is_contra_row_label,
    normalize_account_label,
    parse_won_amount,
)
from app.parsers.pdf_parser import parse_pdf_financials
from app.parsers.xml_parser import (
    _apply_sign,
    _build_period_column_plan,
    _classify_period,
    _decode_raw_xml,
    parse_xml_financials,
)

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
        ("Ⅱ. 매출원가(주석10과 13)", "매출원가"),  # 각주 번호를 한글 접속사 "과"로 이은 표기 (물맑은고기팜 실측, 2026-07-23)
        ("매출원가(주석3와 5)", "매출원가"),  # "와" 접속사도 마커가 있으면 각주로 인식
        ("Ⅷ. 당기순이익(손실)(주석10)", "당기순이익(손실)"),  # 각주만 제거, 의미있는 (손실)은 보존
        ("수익(매출액)", "수익(매출액)"),  # 괄호가 각주가 아니라 항목명 자체라 보존
        ("Ⅱ . 비유동자산", "비유동자산"),  # 로마숫자-마침표 사이 공백 (주식회사 신진팩 실측, 2026-07-21)
        ("∥.비유동자산", "비유동자산"),  # "Ⅱ" 대신 U+2225(∥) 오표기 ((주)해동주택 실측, 2026-07-21)
        ("l.유동자산", "유동자산"),  # 소문자 l을 로마숫자 I 대신 사용 (제이엠테크노 실측, 2026-07-21)
        ("ll.비유동부채", "비유동부채"),  # 소문자 ll을 로마숫자 II 대신 사용
        ("Vl.기말의현금", "기말의현금"),  # 소문자 l을 VI의 두번째 글자로 사용
        ("Ι.유동부채", "유동부채"),  # 그리스 대문자 이오타(U+0399)를 라틴 I 대신 사용
        ("XⅠ.당기순이익(손실)", "당기순이익(손실)"),  # 아스키 X + 유니코드 로마숫자 Ⅰ(U+2160) 혼용
        ("XII.당기순손실", "당기순손실"),  # X(10)를 넘는 항목 번호(오타 없음)
        ("Ⅳ.판매비와관리\n비", "판매비와관리비"),  # 셀 내 줄바꿈이 단어 중간에 낀 표기
        ("영업활동으로 인한 현금흐름(I)", "영업활동으로인한현금흐름"),  # "+" 없이 항목번호만 괄호 병기
    ],
)
def test_normalize_account_label(label, expected):
    assert normalize_account_label(label) == expected


@pytest.mark.parametrize(
    "label, field",
    [
        ("Ⅲ.매출총이익(총손실)", "gross_profit"),
        ("매출총손실(이익)", "gross_profit"),
        ("V.영업이익(영업손실)", "operating_income"),
        ("Ⅴ.영업손실(이익)", "operating_income"),
        ("Ⅹ.당기순이익(순손실)", "net_income"),
        ("X.당기순손실(이익)", "net_income"),
    ],
)
def test_account_name_aliases_cover_reversed_order_combined_labels(label, field):
    """손실/이익 어느 쪽을 앞에 적는지 회사마다 다른 조합형 라벨도 매핑돼야
    한다(2026-07-21, 로컬 캐시 4,922건 전수 스캔으로 발견)."""
    assert ACCOUNT_NAME_ALIASES[normalize_account_label(label)] == field


def test_normalize_account_label_does_not_map_net_income_attribution_line():
    """"당기순이익(손실)의 귀속"은 연결재무제표에서 지배기업/비지배지분 귀속분을
    나누는 별도 분석 행이라 net_income 요약 행과 다르다 — 매핑되면 안 된다."""
    norm = normalize_account_label("XI. 당기순이익(손실)의 귀속")
    assert norm not in ACCOUNT_NAME_ALIASES


def test_account_name_aliases_cover_combined_loss_labels():
    """(손실) 접미가 붙은 라벨도 정규화 후 표준 필드로 매핑돼야 한다."""
    assert ACCOUNT_NAME_ALIASES[normalize_account_label("Ⅷ. 당기순이익(손실)(주석10)")] == "net_income"


@pytest.mark.parametrize(
    "label",
    [
        "X. 연결당기순이익(주석 15)",  # (주)한미프렉시블 실측 (2026-07-23)
        "연결당기순손실",
        "연결당기순이익(손실)",
        "연결당기순이익(순손실)",
    ],
)
def test_account_name_aliases_cover_consolidated_net_income_labels(label):
    """연결재무제표의 당기순이익 요약 행("연결당기순이익" 계열)도 net_income으로
    매핑돼야 한다(2026-07-23, 로컬 캐시 4,922건 전수 스캔 — "연결" 접두어가 표준
    필드로 매핑되는 것은 net_income 계열 369건뿐이라 실측된 4종만 등록)."""
    assert ACCOUNT_NAME_ALIASES[normalize_account_label(label)] == "net_income"


def test_consolidated_net_income_attribution_line_not_mapped():
    """"연결당기순이익(손실)의 귀속"은 지배기업/비지배지분 귀속분을 나누는 분석
    행이라 요약 행과 달라 매핑되면 안 된다 — "연결" alias 추가가 이 보호를 깨지
    않는지 고정한다(비-연결 귀속 행 테스트와 동일 취지)."""
    norm = normalize_account_label("XII. 연결당기순이익(손실)의 귀속")
    assert norm not in ACCOUNT_NAME_ALIASES


def test_account_name_aliases_cover_gross_profit_and_loss_labels():
    """"매출총이익"/"매출총손실"(2026-07-20 신설 alias)도 gross_profit로 매핑돼야 한다."""
    assert ACCOUNT_NAME_ALIASES[normalize_account_label("Ⅲ.매출총이익")] == "gross_profit"
    assert ACCOUNT_NAME_ALIASES[normalize_account_label("Ⅲ. 매출총손실")] == "gross_profit"
    assert ACCOUNT_NAME_ALIASES[normalize_account_label("Ⅲ. 매출총이익(손실)(주석5)")] == "gross_profit"


@pytest.mark.parametrize(
    "raw_label, value, expected",
    [
        # "손실"만 명시된(="이익"이 없는) 행: 원문 부호와 무관하게 항상 반전한다
        # (2026-07-20 수정).
        ("Ⅴ. 영업손실", 2_264_996_073, -2_264_996_073),  # 실측 다수 사례: 양수로 찍힘 → 음수로
        ("Ⅲ. 매출총손실", -1_000_000, 1_000_000),  # 이미 음수(괄호 표기)인 "손실" = 음의 손실 = 이익
        # "영업이익(손실)" 등 흑자·적자 공용 조합형 라벨: 원문 부호를 그대로
        # 신뢰한다(뒤집지 않는다) — 실측(EUC-KR 원문 20220127000408) "영업이익(손실)"
        # 행이 "(6,308,961,098)"로 이미 음수 표기돼 있는 실제 사례.
        ("Ⅴ. 영업이익(손실)", -6_308_961_098, -6_308_961_098),
        ("Ⅹ.당기순이익(손실)", 172_184_056, 172_184_056),  # 조합형 라벨 + 실제 흑자(양수)도 그대로
        # 순수 "이익" 행(조합형도 아니고 "손실"도 없음): 원문 부호를 그대로 신뢰한다.
        ("Ⅴ. 영업이익", 1_843_858_188, 1_843_858_188),
        ("Ⅴ. 영업이익", -500_000, -500_000),  # 음수 "이익" = 손실이 이미 정확히 반영됨
        (None, None, None),
        # 라벨 글자 사이에 공백을 넣어 쓰는 회사(2026-07-21 dart-qa 실측 버그 —
        # 부호 판정을 raw 라벨 그대로 하면 "손실"/"이익" 부분문자열 매칭이 깨져
        # 오분류됐다. alias 조회와 동일하게 normalize_account_label로 공백을 제거한
        # 뒤 판정하도록 고쳐 두 오류 모드를 모두 잡는다).
        # B형: 순수손실인데 공백 때문에 "손실"이 안 잡혀 미반전됐던 케이스 →
        # 이제 반전된다(실측 20260413003038 "영    업    손    실").
        ("Ⅴ.영    업    손    실", 3_340_597_574, -3_340_597_574),
        ("V.영  업  손 실", 145_517_403, -145_517_403),  # 실측 20250324000071
        # A형: 조합형인데 공백 때문에 "이익"이 안 잡혀 순수손실로 오인·반전됐던
        # 케이스 → 이제 원문 부호를 그대로 신뢰한다(뒤집지 않는다).
        # 실측 20230404002324: 원문 괄호 음수(손실) → 그대로 음수 저장.
        ("Ⅲ.매  출 총 이 익(손실)", -2_146_389_859, -2_146_389_859),
        # 실측 20260406000276: 원문 양수(흑자) → 그대로 양수 저장(예전엔 손실로 반전).
        ("Ⅹ.당 기 순 이 익(손실)", 1_516_598_502, 1_516_598_502),
    ],
)
def test_apply_sign_flips_loss_only_rows_regardless_of_raw_sign(raw_label, value, expected):
    assert _apply_sign(raw_label or "", value) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("12,426,276,421", 12426276421.0),
        ("(393,502,380)", -393502380.0),  # 괄호 표기 = 음수
        ("-", 0.0),  # 명시적 0 표기 (부채/자산 항목이 실제로 0원인 경우)
        ("", None),  # 이 그룹에서 안 쓰는 열(당기/전기 상세·합계 중 미사용)
        ("  ", None),
        ("16,507,429,508 ===============", 16507429508.0),  # 총계 행 밑줄 괘선 제거(2012 실측)
        ("(393,502,380)===", -393502380.0),  # 음수 총계에 괘선이 붙어도 부호 유지
        ("===============", None),  # 괘선만 있는 셀은 값 없음(None)
    ],
)
def test_parse_won_amount(text, expected):
    assert parse_won_amount(text) == expected


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
    매출액/매출원가/매출총이익/영업이익/당기순이익 TE 셀 값을 그대로 옮겨 왔다.
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
    # 원문 "Ⅲ.매출총이익" 행(TE 셀) 값을 그대로 옮겨 왔다 — 매출액-매출원가와
    # 일치하지만, 계산이 아니라 다른 항목과 동일하게 원문에서 직접 파싱한다.
    assert parsed.values_cur["gross_profit"] == 15_242_639_160
    assert parsed.values_prv["gross_profit"] == 15_541_193_733
    assert parsed.values_cur["operating_income"] == 1_843_858_188
    assert parsed.values_prv["operating_income"] == 1_716_581_763
    assert parsed.values_cur["net_income"] == 172_184_056
    assert parsed.values_prv["net_income"] == 138_144_741


@pytest.mark.parametrize(
    "label, expected",
    [
        ("기말의 현금(Ⅳ+Ⅴ)", "기말의현금"),  # 유니코드 로마숫자 산식 접미어 (20260630000665 실측)
        ("현금의증가(감소)(Ⅰ+Ⅱ+Ⅲ)", "현금의증가(감소)"),  # 한글 괄호는 보존, 산식 괄호만 제거
        ("기말의 현금(I+II+III)", "기말의현금"),  # 아스키 로마숫자 산식
        ("당기순이익(손실)", "당기순이익(손실)"),  # +가 없는 한글 괄호는 산식이 아니므로 보존
    ],
)
def test_normalize_account_label_formula_suffix(label, expected):
    """현금흐름표 소계 행의 산식 접미어 "(Ⅳ+Ⅴ)"를 제거해도 의미있는 괄호는 보존한다(§4-8)."""
    assert normalize_account_label(label) == expected


def test_parse_xml_financials_extracts_cash_flow():
    """한국학술정보(20260630000641)의 현금흐름표 4항목 당기·전기 실측값(§4-8).

    원문 현금흐름표 TABLE-GROUP의 "영업활동으로 인한 현금흐름"/"투자활동..."/
    "재무활동..."/"기말의 현금" TE 셀 값을 그대로 옮겨 왔다.
    """
    parsed = parse_xml_financials(_read_fixture("20260630000641"))

    assert parsed.values_cur["cf_operating"] == 7_541_679_518
    assert parsed.values_prv["cf_operating"] == 3_744_531_358
    assert parsed.values_cur["cf_investing"] == -3_260_098_592
    assert parsed.values_prv["cf_investing"] == -4_301_670_215
    assert parsed.values_cur["cf_financing"] == -4_482_513_557
    assert parsed.values_prv["cf_financing"] == 1_455_116_279
    assert parsed.values_cur["cf_ending_cash"] == 1_749_296_461
    assert parsed.values_prv["cf_ending_cash"] == 1_950_229_092
    # CF는 best-effort — 정상 추출됐어도 parse_status/parse_note는 오염되지 않는다.
    assert parsed.parse_status == "OK"
    assert parsed.parse_note is None


def test_parse_xml_financials_cash_flow_formula_suffix_label():
    """기말현금 라벨이 "기말의 현금(Ⅳ+Ⅴ)"처럼 산식 접미어를 달고 있어도 매핑된다(20260630000665)."""
    parsed = parse_xml_financials(_read_fixture("20260630000665"))
    assert parsed.values_cur["cf_ending_cash"] == 86_743_556
    assert parsed.values_cur["cf_operating"] == -6_343_925_554


def test_cash_flow_absence_does_not_change_parse_status_but_notes():
    """CF 미첨부 원문은 CF 4항목이 None이지만 parse_status는 CF와 무관하게 판정된다(§4-8).

    20260630000634는 재무제표 자체가 미첨부(의견거절)라 PARTIAL이며, CF도 당연히
    없다 — 이 경우 이미 "미첨부" 안내가 있으므로 CF 부기를 중복하지 않는다.
    """
    parsed = parse_xml_financials(_read_fixture("20260630000634"))
    assert parsed.parse_status == "PARTIAL"
    assert parsed.values_cur.get("cf_operating") is None
    assert "현금흐름표 미확보" not in (parsed.parse_note or "")


def test_account_name_aliases_cover_non_operating_labels():
    """영업외수익/영업외비용은 로마숫자 접두어·글자 사이 공백·유사문자 변형을
    normalize한 뒤 정확히 "영업외수익"/"영업외비용"으로 매핑돼야 한다(2026-07-22,
    로컬 캐시 4,922건 전수 스캔 — 두 라벨이 각각 4,531건으로 지배적)."""
    assert ACCOUNT_NAME_ALIASES[normalize_account_label("Ⅵ. 영업외수익")] == "non_operating_income"
    assert ACCOUNT_NAME_ALIASES[normalize_account_label("Ⅶ.영업외비용")] == "non_operating_expense"
    # 글자 사이 공백(20230404002324 실측)과 유사문자 Vl/Vll(20230405001652 실측)도 흡수.
    assert ACCOUNT_NAME_ALIASES[normalize_account_label("Ⅵ.영  업  외  수  익")] == "non_operating_income"
    assert ACCOUNT_NAME_ALIASES[normalize_account_label("Vll.영업외비용")] == "non_operating_expense"


def test_non_operating_fields_excluded_from_standard_and_status():
    """영업외수익/영업외비용은 best-effort 항목이라 표준 13항목·parse_status 판정에서
    완전히 제외된다(CF와 동일 원칙) — 결측이어도 PARTIAL/FAILED로 떨어지면 안 된다."""
    assert "non_operating_income" not in STANDARD_FINANCIAL_FIELDS
    assert "non_operating_expense" not in STANDARD_FINANCIAL_FIELDS
    assert "non_operating_income" not in DIRECT_FINANCIAL_FIELDS
    assert set(NON_OPERATING_FINANCIAL_FIELDS) == {"non_operating_income", "non_operating_expense"}
    # 표준 13항목은 다 채우고 영업외 2항목만 비워도 OK여야 한다.
    values = {f: 1.0 for f in DIRECT_FINANCIAL_FIELDS}
    status, note = determine_parse_status(values, values, found_any_table=True)
    assert status == "OK"
    assert note is None


def test_parse_xml_financials_extracts_non_operating_items():
    """한국학술정보(20260630000641)의 영업외수익/영업외비용 당기·전기 실측값.

    원문 손익계산서 FINANCE 테이블의 "Ⅵ.영업외수익"/"Ⅶ.영업외비용" TE 셀 값을
    그대로 옮겨 왔다. 순수 수익/비용 항목이라 부호 반전 대상이 아니며, best-effort
    라 정상 추출돼도 parse_status는 오염되지 않는다.
    """
    parsed = parse_xml_financials(_read_fixture("20260630000641"))
    assert parsed.values_cur["non_operating_income"] == 235_085_178
    assert parsed.values_prv["non_operating_income"] == 526_475_539
    assert parsed.values_cur["non_operating_expense"] == 1_866_659_699
    assert parsed.values_prv["non_operating_expense"] == 2_092_546_996
    assert parsed.parse_status == "OK"
    assert parsed.parse_note is None


def test_parse_xml_financials_non_operating_letter_spaced_label():
    """라벨 글자 사이 공백("영   업   외   수   익")도 normalize로 흡수해 매핑된다
    (20260413003038 실측). 순수 수익/비용이라 원문 양수를 그대로 신뢰한다."""
    parsed = parse_xml_financials(_read_fixture("20260413003038"))
    assert parsed.values_cur["non_operating_income"] == 129_804_230
    assert parsed.values_cur["non_operating_expense"] == 659_246_052


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


def test_determine_parse_status_missing_gross_profit_subtotal_is_partial():
    """매출원가는 있지만 매출총이익 소계 행이 생략된 서식은 PARTIAL이어야 한다.

    위 test_..._missing_cogs_sga(cogs 자체가 없는 케이스)와 달리, cogs는 원문에
    정상 존재하고 "매출총이익" 소계 행만 생략된 서식을 고정한다 — 이때
    gross_profit만 None으로 남고 나머지 12항목은 모두 채워진다.

    회귀 배경(의도된 승격): 커밋 e723b90에서 계산값 gross_margin을 원문 직접
    파싱값 gross_profit으로 교체하면서 DIRECT_FINANCIAL_FIELDS가 표준 13항목과
    동일해졌고, gross_profit이 determine_parse_status의 필수 판정 항목으로 자동
    승격됐다. 이 승격은 의도된 동작이다(표준 13항목 정합성 기준으로 엄격하게
    검수 대상을 남긴다 — 사용자 확정 2026-07-21). 이 테스트는 그 판정이 우발적
    회귀가 아니라 의도임을 고정한다.
    """
    values_cur = {f: 1.0 for f in DIRECT_FINANCIAL_FIELDS}
    values_cur["gross_profit"] = None  # 매출총이익 소계 행이 원문에 없음
    assert values_cur["cogs"] is not None  # 매출원가는 정상 존재(cogs 결측 케이스와 구분)
    values_prv = dict(values_cur)

    status, note = determine_parse_status(values_cur, values_prv, found_any_table=True)

    assert status == "PARTIAL"
    assert "gross_profit" in note


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


def test_parse_xml_financials_2012_manufacturing_full_values():
    """2012년 원문(rcept_no=20120110000138), 적정의견, 제조업 완전 재무제표.

    M5 수동 검수(2026-07-15)로 확보한 2012년 구서식 ground truth. 원문 TE 셀:
    자산총계 "154,984,976,730"(당기, 글자 사이 공백 라벨 "자      산      총 계"),
    비유동부채 전기값이 "-"(명시적 0), 손익계산서 매출액(Ⅰ.매출액(주석 2))/
    매출원가(Ⅱ.매출원가)/판관비(Ⅳ.판매비와관리비(주석 19))/영업이익(Ⅴ.영업이익)/
    당기순이익("X.당기순이익" — 아스키 로마숫자 X 접두어)이 모두 채워져 있다.
    """
    raw = _read_fixture("20120110000138")
    parsed = parse_xml_financials(raw)

    assert parsed.parse_status == "OK"
    assert parsed.parse_note is None
    assert parsed.values_cur["current_assets"] == 102_648_344_396
    assert parsed.values_cur["noncurrent_assets"] == 52_336_632_334
    assert parsed.values_cur["total_assets"] == 154_984_976_730
    assert parsed.values_prv["total_assets"] == 128_803_193_386
    assert parsed.values_cur["noncurrent_liab"] == 1_200_000_000
    assert parsed.values_prv["noncurrent_liab"] == 0.0  # 원문 표기 "-"
    assert parsed.values_cur["total_liab"] == 65_072_747_576
    assert parsed.values_cur["total_equity"] == 89_912_229_154
    assert parsed.values_cur["revenue"] == 325_582_993_892
    assert parsed.values_cur["cogs"] == 279_317_000_150
    assert parsed.values_cur["sga"] == 26_272_969_092
    assert parsed.values_cur["operating_income"] == 19_993_024_650
    assert parsed.values_cur["net_income"] == 15_401_175_106  # "X.당기순이익"(아스키 X)


def test_parse_xml_financials_recovers_underlined_total_cell():
    """2012년 원문(rcept_no=20120110000471) — 총계 행 밑줄이 "===" 괘선으로
    금액 셀에 섞여 들어온 케이스(자산총계 "16,507,429,508 ===============").

    M5 수동 검수(2026-07-15)에서 발견: 이 괘선을 제거하지 않으면 float 변환이
    실패해 total_assets가 None으로 누락됐다(유동/비유동자산은 정상이라 더 눈에
    띔). parse_won_amount가 괘선을 제거하도록 고쳐 총계가 복구되는지 확인한다.
    이 회사는 "영업수익/영업비용" 서비스업 서식이라 cogs/sga는 구조적으로
    없어 전체는 PARTIAL이 맞다(태보산업 케이스와 동일 원리).
    """
    raw = _read_fixture("20120110000471")
    parsed = parse_xml_financials(raw)

    assert parsed.values_cur["total_assets"] == 16_507_429_508  # 괘선 제거 후 복구
    assert parsed.values_prv["total_assets"] == 12_788_614_554
    assert parsed.values_cur["current_assets"] == 15_062_356_917
    assert parsed.values_cur["revenue"] == 34_546_090_293  # Ⅰ.영업수익
    assert parsed.values_cur.get("cogs") is None  # 서비스업 서식 — 구조적 부재
    assert parsed.values_cur.get("sga") is None
    assert parsed.parse_status == "PARTIAL"


def test_decode_raw_xml_passes_through_utf8_unchanged():
    """정상 UTF-8 원문은 바이트를 그대로 반환해 기존 파싱 경로에 영향이 없어야 한다."""
    raw = "<DOC><TE>자산총계</TE></DOC>".encode("utf-8")
    assert _decode_raw_xml(raw) is raw


def test_decode_raw_xml_recovers_euckr_declared_as_utf8():
    """실제 EUC-KR 원문(rcept_no=20220127000408, 남경산업)은 선언부에
    encoding="utf-8"이라고 적혀 있지만 실제 바이트는 CP949다 — UTF-8 디코딩이
    실패해야 하고, _decode_raw_xml이 CP949로 폴백해 UTF-8 bytes로 정규화해야 한다."""
    raw = _read_fixture("20220127000408")
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")  # 원문이 실제로 비UTF-8임을 전제 확인

    normalized = _decode_raw_xml(raw)
    text = normalized.decode("utf-8")  # 정규화 후에는 UTF-8로 디코딩돼야 한다
    assert "남경산업" in text  # 한글이 깨지지 않고 정상 디코딩됨
    assert "재무상태표" in text
    assert not text.lstrip().startswith("<?xml")  # 거짓 인코딩 선언부 제거됨


def test_parse_xml_financials_recovers_euckr_encoded_document():
    """Job #14를 통째로 죽였던 EUC-KR 인코딩 원문(20220127000408)이 이제
    parse_status=OK로 재무 13항목까지 복구되는지 확인한다.

    실측(2026-07-19): 로컬 캐시 1,453건 중 64건(4.4%)이 encoding="utf-8"로
    거짓 선언된 EUC-KR/CP949 원문이었고, 인코딩 폴백 이전에는 recover=True로도
    복구되지 않아 XMLSyntaxError로 Job 전체가 FAILED로 죽었다.
    """
    raw = _read_fixture("20220127000408")
    parsed = parse_xml_financials(raw)

    assert parsed.parse_status == "OK"
    assert parsed.values_cur["total_assets"] == 11_342_787_789
    assert parsed.values_cur["revenue"] == 23_795_704_614
    assert parsed.values_cur["total_equity"] == -5_415_433_951  # 자본잠식(음수)
    assert parsed.values_cur["operating_income"] == -6_308_961_098  # 영업손실
    assert parsed.values_cur["net_income"] == -7_040_301_441  # 당기순손실


@pytest.mark.parametrize(
    "rcept_no, current_assets, noncurrent_assets, total_assets",
    [
        # "Ⅱ . 비유동자산"(로마숫자-마침표 사이 공백) — 이전에는 alias 조회가
        # 실패해 noncurrent_assets 전체가 None으로 누락되고 parse_status=PARTIAL로
        # 잘못 판정됐다(2026-07-21 사용자 지적으로 프로덕션 DB 역추적 발견).
        ("20260402000767", 3_094_933_423, 11_690_924_003, 14_785_857_426),
        # "∥.비유동자산"(U+2225로 Ⅱ 오표기) — 같은 종류의 누락, 다른 원인.
        ("20260408003380", 16_812_778_721, 272_745_550, 17_085_524_271),
    ],
)
def test_parse_xml_financials_recovers_noncurrent_assets_prefix_variants(
    rcept_no, current_assets, noncurrent_assets, total_assets
):
    raw = _read_fixture(rcept_no)
    parsed = parse_xml_financials(raw)

    assert parsed.parse_status == "OK"
    assert parsed.values_cur["current_assets"] == current_assets
    assert parsed.values_cur["noncurrent_assets"] == noncurrent_assets
    assert parsed.values_cur["total_assets"] == total_assets
    assert parsed.values_cur["current_assets"] + parsed.values_cur["noncurrent_assets"] == total_assets


def test_parse_xml_financials_recovers_lowercase_roman_numeral_lookalikes():
    """"l."/"ll."/"lll."/"Vl." 등 소문자 l을 로마숫자로 잘못 쓴 원문(제이엠테크노)이
    BS/IS/CF 전 구간에서 복구되는지 확인한다 — 사용자가 "현금흐름표도 마찬가지
    아니냐"고 지적해 로컬 문서 캐시 4,922건을 전수 스캔하며 발견(2026-07-21).
    """
    raw = _read_fixture("20230405001652")
    parsed = parse_xml_financials(raw)

    assert parsed.parse_status == "OK"
    assert parsed.values_cur["current_assets"] == 23_173_972_906
    assert parsed.values_cur["noncurrent_assets"] == 1_218_308_915
    assert parsed.values_cur["current_liab"] == 7_082_665_499
    assert parsed.values_cur["noncurrent_liab"] == 150_000_000
    assert parsed.values_cur["revenue"] == 17_897_652_129
    assert parsed.values_cur["cf_operating"] == -5_999_840_718.0
    assert parsed.values_cur["cf_investing"] == 8_995_230_124.0
    assert parsed.values_cur["cf_ending_cash"] == 148_899_665.0


def test_parse_xml_financials_recovers_xii_prefix_net_income():
    """"XII.당기순손실"(오타 없이 항목이 11~12번째까지 있는 손익계산서)이
    복구되는지 확인한다 — 기존 _PREFIX_RE의 아스키 로마숫자 목록이 X(10)까지만
    있어 XI/XII는 애초에 접두어 제거 대상이 아니었다(2026-07-21).
    """
    raw = _read_fixture("20220406002584")
    parsed = parse_xml_financials(raw)

    assert parsed.parse_status == "OK"
    assert parsed.values_cur["net_income"] == -493_762_268


def test_parse_xml_financials_recovers_cf_item_reference_suffix():
    """현금흐름표 항목이 "영업활동으로 인한 현금흐름(I)"처럼 "+" 없이 항목번호만
    괄호로 병기하는 서식(한미프랜트)이 복구되는지 확인한다 — 기존
    _FORMULA_SUFFIX_RE는 "+"를 요구해 이 단순 참조 표기를 못 벗겼다(2026-07-21).
    """
    raw = _read_fixture("20230327000686")
    parsed = parse_xml_financials(raw)

    assert parsed.parse_status == "OK"
    assert parsed.values_cur["cf_operating"] == 2_911_566_054
    assert parsed.values_cur["cf_investing"] == -2_346_232_906
    assert parsed.values_cur["cf_financing"] == 493_727_628


def test_parse_xml_financials_spaced_label_sign_type_a_combined():
    """A형 부호 버그(2026-07-21 dart-qa 실측): 라벨 글자 사이 공백이 들어간
    조합형 "매  출 총 이 익(손실)"(이익-primary)은 raw 라벨로는 "이익"이 안 잡혀
    순수손실로 오인·반전됐다. 정규화 라벨로 판정하면 원문 부호를 그대로 신뢰해야
    한다 — 당기 원문은 전부 괄호(손실)이므로 음수로 저장돼야 한다(태영에스티엠).
    """
    parsed = parse_xml_financials(_read_fixture("20230404002324"))
    assert parsed.parse_status == "OK"
    # 당기: 매출 74.6억 < 매출원가 96.1억 → 매출총손실. 조합형 원문 부호(괄호)를 신뢰.
    assert parsed.values_cur["gross_profit"] == -2_146_389_859
    assert parsed.values_cur["operating_income"] == -3_041_861_373
    assert parsed.values_cur["net_income"] == -3_914_280_052
    # 전기는 흑자(양수)로 그대로 유지.
    assert parsed.values_prv["gross_profit"] == 1_942_965_125
    assert parsed.values_prv["net_income"] == 699_523_641
    # 회계 항등식: 매출총이익 == 매출액 - 매출원가.
    assert parsed.values_cur["gross_profit"] == parsed.values_cur["revenue"] - parsed.values_cur["cogs"]


def test_parse_xml_financials_spaced_label_sign_type_b_pure_loss():
    """B형 부호 버그(2026-07-21 dart-qa 실측): 라벨 글자 사이 공백이 들어간
    순수손실 "영    업    손    실"은 raw 라벨로는 "손실"이 안 잡혀 미반전됐다.
    정규화 라벨로 판정하면 반전돼 음수(영업손실)로 저장돼야 한다. 이 문서는
    손실-primary 조합형 "매출총손실(이익)"도 함께 담고 있다(원프렌지).
    """
    parsed = parse_xml_financials(_read_fixture("20260413003038"))
    assert parsed.parse_status == "OK"
    # 순수손실 "영업손실" 당기 원문 3,340,597,574(양수) → 반전 → 음수.
    assert parsed.values_cur["operating_income"] == -3_340_597_574
    # 손실-primary "매출총손실(이익)" 당기 원문 2,574,766,473(양수=손실) → 반전 → 음수.
    assert parsed.values_cur["gross_profit"] == -2_574_766_473
    assert parsed.values_cur["gross_profit"] == parsed.values_cur["revenue"] - parsed.values_cur["cogs"]


def test_parse_xml_financials_loss_primary_combined_labels_are_reversed():
    """손실-primary 조합형 라벨("매출총손실(이익)"/"영업손실(이익)"/"당기순손실
    (이익)")은 이익-primary와 원문 부호 의미가 반대다 — 양수=손실, 괄호=이익이라
    반드시 반전해야 한다(2026-07-21 dart-qa 실측 검증 중 회계 항등식으로 발견,
    공백 없는 깔끔한 표기의 템스코로 whitespace 교란 없이 규칙 자체를 잠근다).
    """
    parsed = parse_xml_financials(_read_fixture("20250414000612"))
    assert parsed.parse_status == "OK"
    # 당기 전부 손실(원문 양수) → 반전 → 음수.
    assert parsed.values_cur["gross_profit"] == -166_892_583
    assert parsed.values_cur["operating_income"] == -1_682_630_773
    assert parsed.values_cur["net_income"] == -2_986_414_346
    # 전기는 원문 괄호(=이익) → 반전 → 양수.
    assert parsed.values_prv["net_income"] == 367_054_034
    assert parsed.values_cur["gross_profit"] == parsed.values_cur["revenue"] - parsed.values_cur["cogs"]


def test_parse_xml_financials_hangul_pseudo_tags_do_not_collapse_table():
    """미이스케이프 한글 유령 태그 회귀(2026-07-21, 제이에스원 rcept
    20260413001757). 셀 안의 "1. 현금및현금성자산 <주석3>", "<당기>"/"<전기>" 등을
    recover 파서가 유효한 시작 태그로 인식해, 그 지점 이후 재무상태표/손익계산서
    전체를 한 셀로 붕괴시켰다. 그 결과 자산총계/부채총계/자본총계/영업이익/
    당기순이익 등이 통째로 유실돼 값이 원문에 멀쩡히 있는데도 PARTIAL로 떨어졌다.
    _HANGUL_PSEUDO_TAG_RE가 유령 태그만 제거해 트리 붕괴를 막는다 — 13항목 전부
    복구되고 OK가 돼야 한다. 금액은 원문 TE 셀 값을 그대로 옮겨 왔다.
    """
    parsed = parse_xml_financials(_read_fixture("20260413001757"))

    assert parsed.parse_status == "OK"
    assert parsed.parse_note is None

    # 붕괴 지점 이후로 유실됐던 항목들 — 이제 전부 복구된다.
    assert parsed.values_cur["noncurrent_assets"] == 15_386_629_551
    assert parsed.values_cur["total_assets"] == 23_159_224_723
    assert parsed.values_prv["total_assets"] == 20_866_654_242
    assert parsed.values_cur["current_liab"] == 2_095_940_030
    assert parsed.values_cur["noncurrent_liab"] == 6_991_107
    assert parsed.values_cur["total_liab"] == 2_102_931_137
    assert parsed.values_cur["total_equity"] == 21_056_293_586
    assert parsed.values_cur["operating_income"] == 1_884_679_027
    assert parsed.values_cur["net_income"] == 1_743_952_060
    # 붕괴 전에 이미 잡히던 항목도 그대로 유지.
    assert parsed.values_cur["current_assets"] == 7_772_595_172
    assert parsed.values_cur["revenue"] == 19_165_612_869
    # 회계 항등식으로 교차 검증: 자산총계 == 부채총계 + 자본총계.
    assert (
        parsed.values_cur["total_assets"]
        == parsed.values_cur["total_liab"] + parsed.values_cur["total_equity"]
    )


def test_parse_xml_financials_ifrs_attached_statements_p_caption():
    """IFRS "(첨부)재무제표" 첨부문서 구조(롯데미쓰이화학 rcept 20250324000776,
    2026-07-22 사용자 실측 지적). 본문에 "재무상태표"/"손익계산서"/"현금흐름표"
    TITLE이 아예 없고, "(첨부)재 무 제 표" TITLE 아래에 각 재무제표 제목이 독립
    <P>("재 무 상 태 표" 등)로, 실제 데이터 표는 ACLASS="FINANCE"가 아니라
    ACLASS="NORMAL" + "과목|주석|당기|전기" 처럼 값 사이에 "주석" 열이 낀 구조로
    들어 있어 기존 파서가 통째로 놓쳐 PARTIAL(전 항목 None)로 떨어지던 케이스다.

    첨부문서 경로가 이 구조를 인식해 표준 13항목 + 현금흐름표 4항목을 모두
    복구해야 한다. 이 회사는 흑자라 부호가 명확하며(매출총이익==매출액-매출원가,
    매출원가 양수) 부호 규칙 논쟁과 무관하게 섹션 탐지 자체를 잠근다.
    """
    parsed = parse_xml_financials(_read_fixture("20250324000776"))
    assert parsed.parse_status == "OK"
    # 재무상태표 (독립 <P> 제목 + NORMAL 표 + 주석 열).
    assert parsed.values_cur["current_assets"] == 6_492_841_537
    assert parsed.values_cur["noncurrent_assets"] == 3_926_457_439
    assert parsed.values_cur["total_assets"] == 10_419_298_976
    assert parsed.values_cur["total_liab"] == 2_835_310_015
    assert parsed.values_cur["total_equity"] == 7_583_988_961
    # 포괄손익계산서 (제목 "포 괄 손 익 계 산 서" → is 섹션).
    assert parsed.values_cur["revenue"] == 10_230_248_259
    assert parsed.values_cur["cogs"] == 9_215_215_205
    assert parsed.values_cur["gross_profit"] == 1_015_033_054
    assert parsed.values_cur["operating_income"] == 393_563_767
    assert parsed.values_cur["net_income"] == 475_819_967
    # 현금흐름표 4항목(best-effort)도 복구.
    assert parsed.values_cur["cf_operating"] == 595_518_049
    assert parsed.values_cur["cf_ending_cash"] == 1_082_343_569
    # 전기 값도 같은 표에서 함께 파싱된다.
    assert parsed.values_prv["total_assets"] == 16_256_978_702
    assert parsed.values_prv["revenue"] == 12_043_789_612
    # 회계 항등식으로 교차 검증(부호 클린).
    assert parsed.values_cur["total_assets"] == (
        parsed.values_cur["total_liab"] + parsed.values_cur["total_equity"]
    )
    assert parsed.values_cur["gross_profit"] == (
        parsed.values_cur["revenue"] - parsed.values_cur["cogs"]
    )


def test_parse_xml_financials_ifrs_attached_statements_table_caption():
    """IFRS "(첨부)재무제표" 변형(하이에어코리아 rcept 20240329000968, 2026-07-22).
    롯데미쓰이와 달리 각 재무제표 제목이 독립 <P>가 아니라 **THEAD 없는 캡션
    <TABLE>의 첫 셀**("재 무 상 태 표")로 들어 있고, 현금흐름표 데이터 표는
    THEAD가 아예 없어 헤더 행이 첫 TBODY 행에 있다. 캡션-표 제목 감지 + THEAD가
    없을 때 첫 본문 행을 헤더로 삼는 열 계획을 모두 잠근다. 이 회사도 흑자다.
    """
    parsed = parse_xml_financials(_read_fixture("20240329000968"))
    assert parsed.parse_status == "OK"
    assert parsed.values_cur["total_assets"] == 433_843_751_101
    assert parsed.values_cur["total_liab"] == 115_262_286_587
    assert parsed.values_cur["total_equity"] == 318_581_464_514
    assert parsed.values_cur["revenue"] == 288_611_837_322
    assert parsed.values_cur["gross_profit"] == 38_632_641_869
    assert parsed.values_cur["operating_income"] == 15_229_753_225
    assert parsed.values_cur["net_income"] == 14_558_758_749
    # THEAD 없는 현금흐름표(헤더가 첫 TBODY 행)도 복구.
    assert parsed.values_cur["cf_operating"] == 46_262_163_859
    assert parsed.values_cur["cf_investing"] == -7_712_818_843
    assert parsed.values_cur["cf_ending_cash"] == 74_319_777_536
    assert parsed.values_cur["total_assets"] == (
        parsed.values_cur["total_liab"] + parsed.values_cur["total_equity"]
    )
    assert parsed.values_cur["gross_profit"] == (
        parsed.values_cur["revenue"] - parsed.values_cur["cogs"]
    )


def test_parse_xml_financials_ifrs_attached_pure_loss_natural_sign():
    """IFRS "(첨부)재무제표" 적자 문서 부호 회귀(ⓑ, rcept 20230322000842,
    2026-07-22 dart-qa 실증). IFRS 첨부 서식은 손실을 자연 부호(괄호=음수)로
    표기해 값에 이미 경제적 부호가 들어 있다 — 여기에 FINANCE 서식 전용
    `_apply_sign`(순수손실 라벨을 반전)을 재사용하면 "Ⅳ.영업손실 (15,641,046,221)"
    (실제 적자 -15.6억)이 +15.6억(흑자)로 뒤집혔다. 첨부 전용 `_apply_sign_ifrs`가
    원문 부호를 그대로 신뢰해 세 손익 소계가 모두 음수로 저장돼야 한다.

    이 회사는 매출액 248B < 매출원가 252B라 매출총손익부터 명백한 적자다
    (회계 항등식 gross_profit == revenue - cogs로 부호를 교차 검증).
    """
    parsed = parse_xml_financials(_read_fixture("20230322000842"))
    assert parsed.parse_status == "OK"
    # 순수손실 라벨 3종이 전부 음수(적자)로 저장돼야 한다.
    assert parsed.values_cur["gross_profit"] == -3_973_615_525
    assert parsed.values_cur["operating_income"] == -15_641_046_221
    assert parsed.values_cur["net_income"] == -31_896_630_865
    # 전기도 적자(음수) 유지.
    assert parsed.values_prv["operating_income"] == -22_417_251_485
    assert parsed.values_prv["net_income"] == -37_217_983_420
    # 비용 항목은 양수 크기(FINANCE 경로 관행과 일치).
    assert parsed.values_cur["revenue"] == 248_274_370_286
    assert parsed.values_cur["cogs"] == 252_247_985_811
    assert parsed.values_cur["sga"] == 11_667_430_696
    # 회계 항등식으로 부호를 자체 검증.
    assert parsed.values_cur["gross_profit"] == (
        parsed.values_cur["revenue"] - parsed.values_cur["cogs"]
    )
    assert parsed.values_cur["total_assets"] == (
        parsed.values_cur["total_liab"] + parsed.values_cur["total_equity"]
    )


def test_parse_xml_financials_ifrs_attached_contra_cogs_abs_normalized():
    """IFRS "(첨부)재무제표" contra 매출원가 부호 회귀(ⓐ, rcept 20230321000531,
    2026-07-22 dart-qa). 흑자 문서인데 포괄손익계산서가 매출원가를
    "Ⅱ.매출원가 (975,711,813,052)"처럼 괄호(contra)로 표기해 `parse_won_amount`가
    음수로 읽었다 — 비용은 정의상 음수가 될 수 없으므로 `_apply_sign_ifrs`가 abs로
    정규화해 FINANCE 경로의 양수 관행과 일치시켜야 한다.
    """
    parsed = parse_xml_financials(_read_fixture("20230321000531"))
    assert parsed.parse_status == "OK"
    # 괄호 표기 매출원가가 양수 크기로 정규화돼야 한다(음수 아님).
    assert parsed.values_cur["cogs"] == 975_711_813_052
    assert parsed.values_cur["sga"] == 7_331_407_030
    assert parsed.values_cur["revenue"] == 1_021_640_327_321
    # 흑자 소계는 그대로 양수.
    assert parsed.values_cur["gross_profit"] == 45_928_514_269
    assert parsed.values_cur["operating_income"] == 38_597_107_239
    assert parsed.values_cur["net_income"] == 15_694_757_080
    # abs 정규화 덕분에 회계 항등식이 성립한다(정규화 전에는 rev-cogs가 2배로 어긋났다).
    assert parsed.values_cur["gross_profit"] == (
        parsed.values_cur["revenue"] - parsed.values_cur["cogs"]
    )
    assert parsed.values_cur["total_assets"] == (
        parsed.values_cur["total_liab"] + parsed.values_cur["total_equity"]
    )


def test_parse_xml_financials_footnote_with_hangul_conjunction_recovers_cogs():
    """(주)물맑은고기팜농업회사법인(rcept 20260408002307) — 매출원가 각주 참조가
    "Ⅱ. 매출원가(주석10과 13)"처럼 한글 접속사 "과"를 끼고 있어, 각주를 못 벗기고
    "매출원가(주석10과13)"가 alias 조회에 실패해 cogs가 통째로 누락되던 케이스
    (2026-07-23 사용자 실측). _FOOTNOTE_SUFFIX_RE가 "주석/주" 마커가 있을 때 한해
    "과"/"와"를 허용하도록 확장해 cogs 당기·전기가 복구되고 OK가 돼야 한다.
    금액은 원문 TE 셀 값을 그대로 옮겨 왔다(gross_profit==revenue-cogs 자체검증).
    """
    parsed = parse_xml_financials(_read_fixture("20260408002307"))
    assert parsed.parse_status == "OK"
    assert parsed.parse_note is None
    assert parsed.values_cur["cogs"] == 15_764_916_952
    assert parsed.values_prv["cogs"] == 15_851_618_669
    assert parsed.values_cur["revenue"] == 17_140_576_742
    assert parsed.values_cur["gross_profit"] == 1_375_659_790
    # 회계 항등식으로 부호·값 교차 검증.
    assert parsed.values_cur["gross_profit"] == (
        parsed.values_cur["revenue"] - parsed.values_cur["cogs"]
    )
    assert parsed.values_cur["total_assets"] == (
        parsed.values_cur["total_liab"] + parsed.values_cur["total_equity"]
    )


def test_parse_xml_financials_consolidated_net_income_recovers():
    """(주)한미프렉시블(rcept 20260424000057) — 연결재무제표라 당기순이익 요약 행이
    "X. 연결당기순이익(주석 15)"(정규화 "연결당기순이익")로 표기돼 net_income alias
    매칭에 실패, net_income이 통째로 누락되던 케이스(2026-07-23 사용자 실측).
    "연결당기순이익" 계열 alias 추가로 net_income 당기·전기가 복구되고 OK가 돼야
    한다. 금액은 원문 TE 셀 값을 그대로 옮겨 왔다.
    """
    parsed = parse_xml_financials(_read_fixture("20260424000057"))
    assert parsed.parse_status == "OK"
    assert parsed.parse_note is None
    assert parsed.values_cur["net_income"] == 3_293_885_531
    assert parsed.values_prv["net_income"] == 2_194_667_989
    assert parsed.values_cur["revenue"] == 88_622_283_999
    assert parsed.values_cur["gross_profit"] == (
        parsed.values_cur["revenue"] - parsed.values_cur["cogs"]
    )
    assert parsed.values_cur["total_assets"] == (
        parsed.values_cur["total_liab"] + parsed.values_cur["total_equity"]
    )


def test_parse_xml_financials_eps_suffix_net_income_recovers():
    """주식회사 노바스(rcept 20260407001297) — 손익계산서 당기순이익 라벨 셀에
    EPS(주당손익)가 각주 뒤에 병기된 "X. 당기순이익(손실)(주석16)(주당손익 당기
    (14,770)원  전기  (11,169)원)" 서식(2026-07-23 사용자 실측). 괄호 안에 한글
    (당기/전기/원)과 중첩 괄호(EPS 금액)가 섞여 _FOOTNOTE_SUFFIX_RE/_FORMULA_SUFFIX_RE가
    모두 매치 실패, 정규화 라벨이 alias 키 "당기순이익(손실)"와 불일치해 net_income이
    통째로 누락(PARTIAL)되던 케이스. "주당"으로 시작하는 괄호에 한해 벗기는
    _EPS_SUFFIX_RE 신설로 복구되고 OK가 돼야 한다. FINANCE 서식·이익-primary
    조합형이라 원문 부호 그대로 신뢰: 당기 값 (855,942,067)/전기 (647,234,000)이
    괄호=음수로 net_income에 그대로 반영된다(적자). 금액은 원문 TE 셀(라인 1602~1606).
    """
    parsed = parse_xml_financials(_read_fixture("20260407001297"))
    assert parsed.parse_status == "OK"
    assert parsed.parse_note is None
    assert parsed.values_cur["net_income"] == -855_942_067
    assert parsed.values_prv["net_income"] == -647_234_000
    assert parsed.values_cur["gross_profit"] == (
        parsed.values_cur["revenue"] - parsed.values_cur["cogs"]
    )
    assert parsed.values_cur["total_assets"] == (
        parsed.values_cur["total_liab"] + parsed.values_cur["total_equity"]
    )


def test_normalize_account_label_strips_eps_suffix_only_when_per_share():
    """_EPS_SUFFIX_RE는 "주당"으로 시작하는 괄호 접미어만 벗기고, "(손실)"/"(매출액)"
    처럼 의미 있는 항목명 괄호는 절대 건드리지 않아야 한다(과잉 제거 방지)."""
    assert (
        normalize_account_label(
            "X. 당기순이익(손실)(주석16)(주당손익 당기 (14,770)원  전기  (11,169)원)"
        )
        == "당기순이익(손실)"
    )
    # EPS 접미어 없이 의미 있는 괄호만 있는 라벨은 그대로 보존.
    assert normalize_account_label("당기순이익(손실)") == "당기순이익(손실)"
    assert normalize_account_label("수익(매출액)") == "수익(매출액)"
    assert normalize_account_label("영업이익(손실)") == "영업이익(손실)"


def test_parse_xml_financials_net_income_summary_label_recovers():
    """진양에너지유틸리티(rcept 20260317000433) — 손익계산서 최종 순손익 요약 행을
    "Ⅶ. 당기순손익"으로 적어(정규화 "당기순손익") 기존 net_income alias와 불일치,
    net_income이 통째로 누락되던 케이스(2026-07-23 dart-parser 실측, PARTIAL). "당기순손익"
    alias 추가로 복구. 이익/손실 부분문자열이 없어 _apply_sign이 원문 부호(괄호=음수)를
    그대로 신뢰하므로 적자 값이 음수로 보존된다.
    """
    parsed = parse_xml_financials(_read_fixture("20260317000433"))
    assert parsed.parse_status == "OK"
    assert parsed.parse_note is None
    assert parsed.values_cur["net_income"] == -1_129_681_376
    assert parsed.values_prv["net_income"] == -1_836_759_384
    assert parsed.values_cur["gross_profit"] == (
        parsed.values_cur["revenue"] - parsed.values_cur["cogs"]
    )
    assert parsed.values_cur["total_assets"] == (
        parsed.values_cur["total_liab"] + parsed.values_cur["total_equity"]
    )
    assert normalize_account_label("Ⅶ. 당기순손익") == "당기순손익"
    assert ACCOUNT_NAME_ALIASES["당기순손익"] == "net_income"


def test_parse_xml_financials_mid_consolidated_net_income_recovers():
    """(주)신신사(rcept 20260407001413) — 연결 표기가 당기순이익 앞이 아니라 중간에
    끼는 변형 "당기연결순이익(손실)"로 net_income 매칭에 실패, 누락되던 케이스
    (2026-07-23 dart-parser 실측, PARTIAL). 이익-primary 조합형이라 원문 부호를 그대로
    신뢰: 당기 흑자(양수)/전기 적자(괄호=음수).
    """
    parsed = parse_xml_financials(_read_fixture("20260407001413"))
    assert parsed.parse_status == "OK"
    assert parsed.parse_note is None
    assert parsed.values_cur["net_income"] == 6_412_374_853
    assert parsed.values_prv["net_income"] == -792_865_908
    assert parsed.values_cur["gross_profit"] == (
        parsed.values_cur["revenue"] - parsed.values_cur["cogs"]
    )
    assert ACCOUNT_NAME_ALIASES["당기연결순이익(손실)"] == "net_income"


def test_parse_xml_financials_bare_maechul_revenue_recovers():
    """(주)엘엑스엠엠에이(rcept 20260326000129) — IFRS "(첨부)재무제표" 서식이라
    손익계산서 TITLE이 없고 매출 최상단 행을 "I. 매출"(정규화 "매출")로 적어 revenue가
    누락되던 케이스(2026-07-23 dart-parser 실측, PARTIAL). bare "매출" alias 추가로 복구.
    """
    parsed = parse_xml_financials(_read_fixture("20260326000129"))
    assert parsed.parse_status == "OK"
    assert parsed.values_cur["revenue"] == 748_580_488_436
    assert parsed.values_prv["revenue"] == 864_366_783_633
    assert parsed.values_cur["gross_profit"] == (
        parsed.values_cur["revenue"] - parsed.values_cur["cogs"]
    )
    assert ACCOUNT_NAME_ALIASES["매출"] == "revenue"


def test_parse_xml_financials_ifrs_subtotal_hap_recovers():
    """씨이케이(rcept 20260330001497) — IFRS "(첨부)연결재무제표"가 "II. 유동자산"을
    값 없는 섹션 헤더로 두고 실제 소계를 "유동자산합계" 행에 적는다. 값 없는 헤더가
    current_assets를 None으로 잠가 소계 값을 못 채우던 케이스(2026-07-23 dart-parser
    실측, PARTIAL). "...합계" alias + 빈 매칭이 필드를 잠그지 않도록 수정해 복구.
    자산총계==부채+자본은 성립하나 자산총계≠유동+비유동(매각예정자산 별도)이라
    항등식은 자산총계로만 검증한다.
    """
    parsed = parse_xml_financials(_read_fixture("20260330001497"))
    assert parsed.parse_status == "OK"
    assert parsed.values_cur["current_assets"] == 52_141_919_553
    assert parsed.values_cur["noncurrent_assets"] == 155_597_992_834
    assert parsed.values_cur["current_liab"] == 70_956_519_995
    assert parsed.values_cur["noncurrent_liab"] == 30_247_486_475
    assert parsed.values_cur["total_assets"] == (
        parsed.values_cur["total_liab"] + parsed.values_cur["total_equity"]
    )
    assert ACCOUNT_NAME_ALIASES["유동자산합계"] == "current_assets"


def test_parse_xml_financials_ifrs_subtotal_gye_recovers():
    """상지해운(rcept 20260325000364) — 위 씨이케이와 같은 IFRS 첨부 소계 서식이나
    소계 라벨이 "합계"가 아니라 "계"("유동자산계" 등)인 변형(2026-07-23 dart-parser
    실측, PARTIAL). "...계" alias 추가로 복구. 여기선 유동+비유동==자산총계도 성립한다.
    """
    parsed = parse_xml_financials(_read_fixture("20260325000364"))
    assert parsed.parse_status == "OK"
    assert parsed.values_cur["current_assets"] == 5_432_974_069
    assert parsed.values_cur["noncurrent_assets"] == 1_891_881_005
    assert parsed.values_cur["current_assets"] + parsed.values_cur["noncurrent_assets"] == (
        parsed.values_cur["total_assets"]
    )
    assert parsed.values_cur["total_assets"] == (
        parsed.values_cur["total_liab"] + parsed.values_cur["total_equity"]
    )
    assert ACCOUNT_NAME_ALIASES["유동부채계"] == "current_liab"


def test_parse_xml_financials_eps_suffix_extra_paren_recovers():
    """대능주택개발(rcept 20220406000855) — 당기순이익 라벨의 EPS 병기 괄호가 균형
    안 맞게 닫는 괄호를 하나 더 붙인 실측 오타 "...(주당순이익(손실):당기 (4,407원)
    전기 60,420원))"(끝에 여분 ")"). _EPS_SUFFIX_RE가 끝 앵커라 매치 실패해 net_income이
    누락되던 케이스(2026-07-23 dart-parser 실측, PARTIAL). 정규식 끝 ``\\)+``로 여분
    닫는 괄호를 흡수해 복구. 이익-primary라 원문 부호 신뢰(당기 적자/전기 흑자).
    """
    parsed = parse_xml_financials(_read_fixture("20220406000855"))
    assert parsed.parse_status == "OK"
    assert parsed.values_cur["net_income"] == -132_202_155
    assert parsed.values_prv["net_income"] == 1_812_601_601
    assert parsed.values_cur["gross_profit"] == (
        parsed.values_cur["revenue"] - parsed.values_cur["cogs"]
    )
    # 여분 닫는 괄호가 있어도 EPS 접미어가 벗겨져 alias 키와 일치해야 한다.
    assert (
        normalize_account_label(
            "X. 당기순이익(손실)(주석13)(주당순이익(손실):당기 (4,407원) 전기 60,420원))"
        )
        == "당기순이익(손실)"
    )


# ---------------------------------------------------------------------------
# IFRS "(첨부)재무제표" 서식 중 열 제목이 "당기"/"전기"가 아니라 **회계 차수**
# ("제6기 기말"/"제 6 기")로만 적힌 변형 (2026-07-28 실측).
#
# 로컬 문서 캐시 8,748건 전수 스캔: 첨부 서식 462건 중 63건(표 기준 184개)이
# 이 표기를 쓴다. 열 계획이 서지 않아 "재무상태표/손익계산서 테이블을 찾을 수
# 없음"으로 재무 데이터가 통째로 유실되던 회귀(전량 PARTIAL)를 잠근다.
# ---------------------------------------------------------------------------


def _header_cells(xml: str) -> list:
    from lxml import etree

    return list(etree.fromstring(xml))


def test_build_period_column_plan_ordinal_headers_use_document_order():
    """차수 표기 헤더는 **등장 순서**로 당기/전기를 정한다(차수 숫자 크기 아님).

    실측 184개 표 전부 큰 차수가 먼저 나오는 내림차순이었지만, 숫자 크기로
    판정하면 표기 방식이 달라질 때 조용히 뒤집힐 수 있어 순서 기준으로 고정한다.
    COLSPAN=2(상세/합계 2열)도 그대로 펼쳐져야 한다.
    """
    plan = _build_period_column_plan(
        _header_cells(
            '<THEAD><TH>과 목</TH><TH>주 석</TH>'
            '<TH COLSPAN="2">제6기 기말</TH><TH COLSPAN="2">제5기 기말</TH></THEAD>'
        )
    )
    assert plan == ([2, 3], [4, 5])
    # 글자 사이 공백("제 6 기")과 "(당)"/"(전)"이 낀 차수 표기도 같은 규칙.
    assert _build_period_column_plan(
        _header_cells("<THEAD><TH>과목</TH><TH>주석</TH><TH>제 6 기</TH><TH>제 5 기</TH></THEAD>")
    ) == ([2], [3])


def test_build_period_column_plan_explicit_labels_take_precedence():
    """"당기"/"전기" 명시 표기가 있으면 등장 순서와 무관하게 그 표기를 따른다.

    전기 열이 먼저 오는 표에서 차수 폴백이 우선하면 당기/전기가 통째로 뒤집힌다 —
    폴백은 명시 표기로 **둘 다** 잡지 못했을 때만 동작해야 한다.
    """
    plan = _build_period_column_plan(
        _header_cells(
            "<THEAD><TH>과목</TH><TH>주석</TH>"
            "<TH>제 5 기(전기)</TH><TH>제 6 기(당기)</TH></THEAD>"
        )
    )
    assert plan == ([3], [2])


def test_classify_period_ignores_embedded_linebreaks():
    """헤더 셀 텍스트 안에 개행/탭이 섞여도 "(당)"/"(전)" 명시를 잡아야 한다.

    실측(20240401002723)의 THEAD 헤더가 `'제\\n 8(\\n당\\n) \\n기'`처럼 셀 안에
    개행이 낀 채 저장돼 있다. 공백만 지우던 예전 `_classify_period`는 이를
    못 잡고 차수 순서 폴백으로 새 버렸다 — `_apply_sign`이 이미 겪은 "판정은
    반드시 정규화 라벨로" 교훈과 동일하다.
    """
    assert _classify_period("제\n 8(\n당\n) \n기") == "cur"
    assert _classify_period("제\t7(\t전\t)\t기") == "prv"


def test_build_period_column_plan_partial_explicit_label_is_respected():
    """헤더 한쪽만 "(당)"/"(전)"이 명시돼 있고 반대쪽은 순수 차수 표기뿐일 때,
    등장 순서 폴백이 그 명시를 뒤집으면 안 된다(dart-qa 2026-07-28 지적).

    "제 5 기(전) / 제 6 기"처럼 **전기 열이 먼저** 오면서 당기 열에만 표기가
    빠진 경우, 예전 폴백(cur/prv가 둘 다 안 잡혔을 때만 발동)은 명시된 "(전)"
    정보를 통째로 버리고 등장 순서로 재판정해 "제5기(전)"을 당기로 뒤집었다.
    """
    plan = _build_period_column_plan(
        _header_cells(
            "<THEAD><TH>과목</TH><TH>주석</TH>"
            "<TH>제 5 기(전)</TH><TH>제 6 기</TH></THEAD>"
        )
    )
    assert plan == ([3], [2])  # 당기=제6기(뒤), 전기=제5기(전)(앞) — 명시 존중

    # 대칭 케이스: 당기 열에만 "(당)"이 있고 전기 열은 순수 차수 표기.
    plan2 = _build_period_column_plan(
        _header_cells(
            "<THEAD><TH>과목</TH><TH>주석</TH>"
            "<TH>제 5 기</TH><TH>제 6 기(당)</TH></THEAD>"
        )
    )
    assert plan2 == ([3], [2])  # 당기=제6기(당)(명시), 전기=제5기(나머지)


def test_build_period_column_plan_single_ordinal_header_is_not_a_plan():
    """차수 표기가 1개뿐인 행(첨부 서식의 기간 캡션 표: "제6기 2025년 12월 31일
    현재"가 한 행에 하나씩)은 데이터 표가 아니므로 열 계획을 세우지 않는다."""
    assert (
        _build_period_column_plan(_header_cells("<TR><TE>제6기 2025년 12월 31일 현재</TE></TR>"))
        is None
    )


def test_parse_xml_financials_ifrs_attach_ordinal_period_headers():
    """합천수상태양광(rcept 20260331004483) — 사용자 실측 신고 케이스.

    "(첨부)재 무 제 표" 서식인데 재무상태표 THEAD가 `<TH COLSPAN="2">제6기 기말</TH>`
    / `<TH COLSPAN="2">제5기 기말</TH>`, 손익계산서가 `<TH>제 6 기</TH>` /
    `<TH>제 5 기</TH>`라 "당기"/"전기" 단서가 본문 3표에 전혀 없다(같은 문서의 주석
    표에는 "당기"/"전기"가 정상적으로 쓰인다). 열 계획이 서지 않아 4개 회계연도
    보고서가 전부 PARTIAL("재무상태표/손익계산서 테이블을 찾을 수 없음")이었다.

    당기/전기가 뒤집히지 않았음은 **직전 연도 보고서(20250328000382)의 당기
    유동자산 18,342,554,401과 이 보고서의 전기가 일치**하는 것으로 교차 확인했다.
    """
    parsed = parse_xml_financials(_read_fixture("20260331004483"))
    assert parsed.parse_status == "OK"
    assert parsed.values_cur["current_assets"] == 20_681_407_089
    assert parsed.values_prv["current_assets"] == 18_342_554_401  # 전년도 보고서의 당기값
    assert parsed.values_cur["noncurrent_assets"] == 55_257_773_260
    assert parsed.values_cur["total_assets"] == (
        parsed.values_cur["total_liab"] + parsed.values_cur["total_equity"]
    )
    assert parsed.values_cur["gross_profit"] == (
        parsed.values_cur["revenue"] - parsed.values_cur["cogs"]
    )
    assert parsed.values_cur["operating_income"] == 4_956_873_463
    assert parsed.values_cur["net_income"] == 2_423_036_118
    assert parsed.values_cur["cf_operating"] == 6_179_654_468


def test_parse_xml_financials_ifrs_attach_ordinal_headers_both_marked_current():
    """제일에프에이(rcept 20240329003157) — 위 차수 표기 서식의 변형.

    전기 열에도 "(당)"이 잘못 붙어 있다("제 9(당) 기말" / "제 8(당) 기말"). 명시
    표기만 보면 두 열이 모두 당기가 돼 전기 열을 못 찾고 열 계획이 무산됐다 —
    차수 폴백이 등장 순서로 이 원문도 흡수한다. 당기/전기가 뒤집히지 않았음은
    다음 연도 보고서(20250331003688)의 전기 유동자산 14,399,181,758과 이 보고서의
    당기가 일치하는 것으로 교차 확인했다.
    """
    parsed = parse_xml_financials(_read_fixture("20240329003157"))
    assert parsed.parse_status == "OK"
    assert parsed.values_cur["current_assets"] == 14_399_181_758  # 익년도 보고서의 전기값
    assert parsed.values_prv["current_assets"] == 10_664_342_536
    assert parsed.values_cur["total_assets"] == (
        parsed.values_cur["total_liab"] + parsed.values_cur["total_equity"]
    )
    assert parsed.values_cur["gross_profit"] == (
        parsed.values_cur["revenue"] - parsed.values_cur["cogs"]
    )
    assert parsed.values_prv["gross_profit"] == (
        parsed.values_prv["revenue"] - parsed.values_prv["cogs"]
    )


def test_parse_account_detail_ifrs_attach_ordinal_headers():
    """세부계정 펼치기도 같은 `attach_period_plan`을 쓰므로 함께 복구돼야 한다.

    (합천수상태양광은 재무상태표 전 행이 들여쓰기 0 + 로마숫자 대분류라 BS
    세부계정은 기존 계층 휴리스틱상 비어 있다 — 이 서식의 별개 한계이며, 수정
    전에는 손익계산서/현금흐름표까지 통째로 비어 있었다.)
    """
    raw = _read_fixture("20260331004483")
    detail = parse_account_detail(raw)
    summary = parse_xml_financials(raw)
    assert detail.fiscal_year_cur == "2025"

    op_rows = {r.label.strip(): r for r in detail.accounts["operating_income"]}
    assert op_rows["금융수익"].cur == 63_033_002
    assert op_rows["금융원가"].prv == 2_228_346_668

    cf_rows = detail.accounts["cf_operating"]
    assert cf_rows[0].label.strip() == "(1) 당기순이익"
    assert cf_rows[0].cur == summary.values_cur["net_income"]


def test_extract_section_html_ifrs_attach_ordinal_headers():
    """원문 섹션 열람은 열 계획을 쓰지 않아 이 서식도 원래 열렸다 — 무회귀 확인."""
    found, html = extract_section_html(_read_fixture("20260331004483"), "bs")
    assert found is True
    assert "제6기기말" in html.replace(" ", "")  # 차수 표기 헤더가 그대로 렌더링된다
    assert "20,681,407,089" in html


def test_parse_xml_financials_invalid_xml_returns_failed():
    parsed = parse_xml_financials(b"not xml at all &&&")
    assert parsed.parse_status == "FAILED"


# ---------------------------------------------------------------------------
# document_sections.py / account_detail.py — IFRS "(첨부)재무제표" 서식 인식
# (2026-07-27, 이래CS 20260401004343 사용자 실측 지적)
#
# 파이프라인 파서(xml_parser.py)는 2026-07-22에 이 서식을 지원해 재무이력 표에는
# 값이 나왔지만, UI 전용 두 모듈이 <TABLE-GROUP><TITLE> + ACLASS="FINANCE" 조합만
# 인식해 "원문을 찾을 수 없음"/"세부계정 없음"으로 비어 있었다. 세 모듈이 공용
# 워커(`walk_statement_tables`)로 같은 기준을 쓰게 고친 것을 잠근다.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rcept_no, section, expected_title_fragment",
    [
        # 캡션 <TABLE> 제목 + 연결재무제표 (이래CS — 사용자 실측 신고 케이스)
        ("20260401004343", "bs", "재 무 상 태 표"),
        ("20260401004343", "is", "손 익 계 산 서"),
        ("20260401004343", "cf", "현 금 흐 름 표"),
        # 독립 <P> 제목 (롯데미쓰이화학)
        ("20250324000776", "bs", "재 무 상 태 표"),
        ("20250324000776", "is", "손 익 계 산 서"),
        ("20250324000776", "cf", "현 금 흐 름 표"),
        # 캡션 <TABLE> 제목 + THEAD 없는 현금흐름표 (하이에어코리아)
        ("20240329000968", "bs", "재 무 상 태 표"),
        ("20240329000968", "cf", "현 금 흐 름 표"),
        # 적자 문서 / contra 매출원가 문서도 동일하게 열린다.
        ("20230322000842", "is", "손 익 계 산 서"),
        ("20230321000531", "bs", "재 무 상 태 표"),
    ],
)
def test_extract_section_html_ifrs_attach_format(rcept_no, section, expected_title_fragment):
    """IFRS "(첨부)재무제표" 서식 원문도 원문 섹션 열람이 가능해야 한다.

    이 서식은 재무제표별 `<TITLE>`이 없어 기존 경로가 항상 `available=false`를
    돌려줬다(사용자 화면: "해당 섹션을 원문에서 찾을 수 없습니다"). 제목(<P>/캡션
    표)이 h4로, 데이터 표가 <table>로 조립돼야 한다.
    """
    import re

    found, html = extract_section_html(_read_fixture(rcept_no), section)
    assert found is True
    assert expected_title_fragment in html
    assert '<h4 class="doc-section-title">' in html
    # 금액 셀(1,234,567 형태)이 실제로 담겨 있어야 한다(빈 표 렌더링 방지).
    assert len(re.findall(r"[0-9]{1,3}(?:,[0-9]{3})+", html)) > 10
    assert "<tr></tr>" not in html


def test_extract_section_html_finance_format_unchanged():
    """FINANCE 서식(기존 <TABLE-GROUP><TITLE> 경로)은 무변경이어야 한다 —
    첨부 서식 분기는 TITLE을 못 찾았을 때만 시도하므로 이 경로에 영향이 없다."""
    found, html = extract_section_html(_read_fixture("20260630000641"), "bs")
    assert found is True
    assert "자산총계" in html.replace(" ", "")


def test_extract_section_html_ifrs_attach_stops_at_next_statement():
    """첨부 서식의 손익계산서 구간은 다음 재무제표(자본변동표)에서 끊겨야 한다 —
    끊지 않으면 손익계산서 탭에 자본변동표가 통째로 딸려 들어간다(이래CS 실측)."""
    _, is_html = extract_section_html(_read_fixture("20260401004343"), "is")
    # 손익계산서 본문 표 + "별첨 주석은..." 부속 표까지 2개만 실린다.
    assert is_html.count("<table>") == 2
    # 자본변동표 고유 열("자본금"/"이익잉여금")이 손익계산서 구간에 없어야 한다.
    assert "자본금" not in is_html
    assert "이익잉여금" not in is_html
    # 손익계산서 자체 내용은 그대로 들어 있다.
    assert "매출원가" in is_html.replace(" ", "")


def test_parse_account_detail_ifrs_attach_children_sum_to_summary():
    """이래CS(rcept 20260401004343) — IFRS "(첨부)연결재무제표" 서식의 세부계정.

    이 서식의 데이터 표는 평범한 `<TD>`라 FINANCE 서식의 `ALEVEL` 속성이 없고
    하위계정은 선행 공백 4칸으로만 들여쓴다. 들여쓰기 기반 레벨 판정이 맞는지는
    **children 합계 == 요약 대분류 값** 항등식으로 자체 검증한다(부분값이 과다/
    과소로 붙으면 즉시 어긋난다). 당기·전기 양쪽에서 성립해야 한다.
    """
    raw = _read_fixture("20260401004343")
    detail = parse_account_detail(raw)
    summary = parse_xml_financials(raw)
    assert summary.parse_status == "OK"  # 파이프라인은 원래부터 정상이었다

    assert detail.fiscal_year_cur == "2025"
    assert detail.audit_opinion == "적정"

    first = detail.accounts["current_assets"][0]
    assert "현금및현금성자산" in first.label
    assert first.level == 1
    assert first.cur == 6_912_252_564
    assert first.prv == 3_709_701_041

    for field in ("current_assets", "noncurrent_assets", "current_liab", "noncurrent_liab"):
        rows = detail.accounts[field]
        assert rows, field
        assert sum(r.cur for r in rows if r.level == 1) == summary.values_cur[field]
        assert sum(r.prv for r in rows if r.level == 1) == summary.values_prv[field]

    # 총계 항목은 하위가 형제 대분류라 children이 비어 있다(FINANCE 경로와 동일).
    assert detail.accounts["total_assets"] == []
    # 현금흐름표도 같은 경로로 계층이 잡힌다(중첩 레벨 포함).
    cf_rows = detail.accounts["cf_operating"]
    assert [r.level for r in cf_rows] == [1, 2, 2, 2, 1]
    assert cf_rows[0].cur == 34_797_392_667


def test_parse_account_detail_ifrs_attach_keeps_natural_sign():
    """첨부 서식 세부계정의 부호는 `_apply_sign_ifrs`와 같은 규칙 — 손익 항목은
    원문 부호(괄호=음수)를 그대로 신뢰한다. 파이프라인이 저장한 요약값과 화면의
    세부계정이 다른 규칙을 쓰면 같은 표 안에서 부호가 엇갈려 보인다."""
    detail = parse_account_detail(_read_fixture("20260401004343"))
    rows = {r.label.strip(): r for r in detail.accounts["operating_income"]}
    assert rows["기타영업수익"].cur == 11_668_219_529  # 수익: 양수 그대로
    assert rows["기타영업비용"].cur == -10_339_682_121  # 괄호 표기 비용: 음수 유지
    assert rows["금융비용"].cur == -7_819_285_861


def test_parse_account_detail_ifrs_attach_summary_row_is_never_a_child():
    """요약 필드로 매핑되는 행은 들여쓰기와 무관하게 항상 대분류로 잡아야 한다.

    롯데미쓰이(20250324000776) 손익계산서는 "III. 매출총이익" 다음 줄의
    "판매비와관리비"를 한 칸 들여써 적는다 — 들여쓰기만 보면 판관비가 매출총이익의
    하위계정으로 붙어 화면에 엉뚱하게 표시된다."""
    detail = parse_account_detail(_read_fixture("20250324000776"))
    assert detail.accounts["gross_profit"] == []
    assert "sga" in detail.accounts  # 대분류로 인식(하위계정은 원문에 없어 빈 목록)
    assert [r.label.strip() for r in detail.accounts["operating_income"]] == [
        "금융수익",
        "금융비용",
        "기타수익",
        "기타비용",
    ]


def test_parse_account_detail_ifrs_attach_uses_numbering_when_no_indent():
    """들여쓰기가 없는 첨부 서식 표는 번호 접두어를 보조 단서로 써야 한다.

    세아엠앤에스(20230321000531)의 현금흐름표는 대분류("Ⅰ.영업활동으로 인한
    현금흐름")와 하위계정("1.당기순이익")이 **같은 열에서 시작**해 들여쓰기만으로는
    계층을 알 수 없다 — 보조 단서가 없으면 cf_* 세부계정이 통째로 비어 화면에서
    펼치기가 동작하지 않는다. children 합계 == 활동별 소계 항등식으로 자체 검증한다.
    """
    raw = _read_fixture("20230321000531")
    detail = parse_account_detail(raw)
    summary = parse_xml_financials(raw)

    for field in ("cf_operating", "cf_investing", "cf_financing"):
        rows = detail.accounts[field]
        assert rows, field
        top = min(r.level for r in rows)
        assert sum(r.cur for r in rows if r.level == top) == summary.values_cur[field]

    labels = [r.label.strip() for r in detail.accounts["cf_operating"]]
    assert labels[0].endswith("당기순이익")
    assert len(labels) == 6  # 1~6번 항목 전부


def test_parse_account_detail_ifrs_attach_numbering_does_not_invert_levels():
    """번호 접두어는 **대분류 경계 판정에만** 쓰고 레벨 순위에는 쓰지 않는다.

    한화컴파운드(20250328000629) 현금흐름표는 번호가 붙은 소계
    ("1. 재무활동으로 인한 현금유입액") 아래에 **번호 없는** 세부 항목("유상증자")을
    들여써 적는다 — 번호를 깊이 신호로 쓰면 부모가 자식보다 깊게 매겨져 계층이
    뒤집힌다. 부모는 자식보다 얕아야 한다.
    """
    rows = parse_account_detail(_read_fixture("20250328000629")).accounts["cf_financing"]
    by_label = {r.label.strip(): r for r in rows}
    parent = by_label["1. 재무활동으로 인한 현금유입액"]
    child = by_label["유상증자"]
    assert parent.level < child.level


def test_parse_account_detail_ifrs_attach_flat_table_keeps_all_rows():
    """들여쓰기가 전혀 없는 표에서는 번호 없는 세부 행도 같은 대분류에 이어 붙인다.

    한화컴파운드(20250328000629) 투자활동 구간은 번호 붙은 소계와 번호 없는 세부
    행이 모두 같은 열에서 시작한다 — 번호 있는 행만 잡으면 "합계가 맞지 않는 일부
    목록"이 화면에 나온다. 원문 구간의 행이 순서대로 모두 실려야 한다.
    """
    rows = parse_account_detail(_read_fixture("20250328000629")).accounts["cf_investing"]
    labels = [r.label.strip() for r in rows]
    assert labels[0] == "1. 투자활동으로 인한 현금유입액"
    assert "단기대여금의 감소" in labels  # 번호 없는 세부 행
    assert "2. 투자활동으로 인한 현금유출액" in labels  # 뒤따르는 소계도 유실되지 않는다
    assert len(labels) == 13


def test_parse_account_detail_finance_format_unchanged():
    """FINANCE 서식(ALEVEL 기반 경로)은 무변경 — 공용 워커로 바꾼 뒤에도 동일하게
    대분류별 children이 나와야 한다(위 §4-8 기존 테스트와 같은 원문)."""
    detail = parse_account_detail(_read_fixture("20260630000641"))
    assert len(detail.accounts["current_assets"]) > 5
    assert all(r.level >= 1 for r in detail.accounts["current_assets"])
    assert detail.accounts["total_assets"] == []
    assert detail.accounts["cf_ending_cash"] == []


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
        ("20120110000138", "적정"),  # 2012년 구서식(제조업) — M5 검수 추가
        ("20260630000634", "의견거절"),  # 아이알디앤씨(2026, 재무제표 미첨부) — M5 검수 추가
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
# 세부계정 5항목 (DETAIL_FINANCIAL_FIELDS, best-effort — 2026-08-05 신설)
#
# 로컬 문서 캐시 8,748건 전수 스캔으로 라벨 표기·커버리지·부호를 실측한 뒤 설계했다.
# 아래 테스트가 잠그는 핵심 규약은 세 가지다:
#   ① 필드마다 **원천 재무제표가 하나로 고정**돼 있고 다른 재무제표로 폴백하지 않는다
#      (같은 라벨이 재무제표마다 다른 뜻이라 섞으면 조용한 오류가 된다).
#   ② 재무상태표 잔액 2종은 **인접 contra 행을 차감한 순액**으로 저장한다 — 매출채권은
#      대손충당금(총액이면 최대 6.38배), 현금및현금성자산은 정부보조금(최대 89.2%).
#   ③ 이 다섯 필드는 `determine_parse_status()` 판정에 관여하지 않는다(결측이어도 OK).
# ---------------------------------------------------------------------------

_DETAIL_FIXTURE_EXPECTED = {
    # 실제 원문에서 눈으로 확인한 ground truth (당기 기준).
    "20260424000057": {  # (주)한미프렉시블 — FINANCE 서식, 5항목 모두 존재
        "cash_and_equivalents": 1_501_366_489.0,
        "trade_receivables": 9_871_514_894.0,
        "interest_expense": 946_271_041.0,
        "depreciation": 1_210_860_947.0,
        "amortization": 33_934_964.0,
    },
    "20260413003038": {  # FINANCE 서식(손실-primary 조합형 라벨을 쓰는 원문)
        "cash_and_equivalents": 227_872_140.0,
        "trade_receivables": 1_381_720_310.0,
        "interest_expense": 658_106_225.0,
        "depreciation": 444_073_163.0,
        "amortization": 1_730_000.0,
    },
    "20260330001497": {  # 씨이케이 — IFRS "(첨부)재무제표" 서식
        "cash_and_equivalents": 10_996_202_190.0,
        "trade_receivables": 8_479_688_212.0,
        "interest_expense": None,  # 첨부 서식 손익계산서는 "금융비용"만 있어 미매핑(아래 테스트 참고)
        "depreciation": 9_284_534_560.0,
        "amortization": 92_356_081.0,
    },
}


@pytest.mark.parametrize("rcept_no", sorted(_DETAIL_FIXTURE_EXPECTED))
def test_detail_fields_extracted_from_real_fixtures(rcept_no):
    """FINANCE/IFRS 첨부 두 서식 모두에서 세부계정 5항목이 실제 원문 수치로 나온다."""
    parsed = parse_xml_financials(_read_fixture(rcept_no))
    for field, expected in _DETAIL_FIXTURE_EXPECTED[rcept_no].items():
        assert parsed.values_cur.get(field) == expected, field
    # 전기(_prv)도 함께 채워지는 구조인지 확인(당기만 채우는 회귀 방지).
    assert parsed.values_prv.get("cash_and_equivalents") is not None


def test_detail_fields_are_best_effort_and_never_change_parse_status():
    """세부계정은 CF 4항목/영업외 2항목과 동형인 best-effort — 판정에 관여하지 않는다.

    `determine_parse_status`는 DIRECT_FINANCIAL_FIELDS만 보므로, 세부계정이 전부
    결측이어도 OK가 나와야 한다(이미 OK로 완료된 Job이 새 필드 때문에 PARTIAL로
    재분류되면 검수 기준이 깨진다 — CF·영업외손익 때와 같은 확정 사항).
    """
    for field in DETAIL_FINANCIAL_FIELDS:
        assert field not in STANDARD_FINANCIAL_FIELDS
        assert field not in DIRECT_FINANCIAL_FIELDS
        assert field not in CF_FINANCIAL_FIELDS
        assert field not in NON_OPERATING_FINANCIAL_FIELDS
    full = {f: 1.0 for f in DIRECT_FINANCIAL_FIELDS}
    status, note = determine_parse_status(dict(full), dict(full), found_any_table=True)
    assert (status, note) == ("OK", None)


def test_detail_alias_tables_are_section_scoped_and_disjoint():
    """세 alias 사전은 재무제표별로 분리돼 있고 서로 필드를 넘나들지 않는다."""
    assert set(DETAIL_ALIASES_BY_SECTION) == {"bs", "is", "cf"}
    assert DETAIL_ALIASES_BY_SECTION["bs"] is BS_DETAIL_ACCOUNT_ALIASES
    assert DETAIL_ALIASES_BY_SECTION["is"] is IS_DETAIL_ACCOUNT_ALIASES
    assert DETAIL_ALIASES_BY_SECTION["cf"] is CF_DETAIL_ACCOUNT_ALIASES
    # 모든 alias 값이 DETAIL_FINANCIAL_FIELDS 안에 있어야 한다(오타 방지).
    for aliases in DETAIL_ALIASES_BY_SECTION.values():
        assert set(aliases.values()) <= set(DETAIL_FINANCIAL_FIELDS)
    # 섹션끼리 같은 라벨을 공유하지 않는다 — 공유하면 "매출채권"/"이자비용"처럼
    # 재무제표마다 뜻이 다른 라벨이 엉뚱한 필드로 샌다.
    assert not set(BS_DETAIL_ACCOUNT_ALIASES) & set(CF_DETAIL_ACCOUNT_ALIASES)
    assert not set(IS_DETAIL_ACCOUNT_ALIASES) & set(CF_DETAIL_ACCOUNT_ALIASES)
    # "무형자산감가상각비"는 "감가상각비"를 부분문자열로 품지만 alias 조회가 완전
    # 일치라 amortization으로 정확히 간다(부분 일치로 바꾸면 즉시 깨진다).
    assert CF_DETAIL_ACCOUNT_ALIASES["무형자산감가상각비"] == "amortization"
    assert CF_DETAIL_ACCOUNT_ALIASES["감가상각비"] == "depreciation"


def test_trade_receivables_is_net_of_allowance_contra_row():
    """매출채권은 **대손충당금 차감 후 순액**으로 저장한다(주식회사 태진 20220415000297).

    원문 재무상태표: "매출채권 2,237,045,099" 바로 다음 행 "매출채권대손충당금
    (1,886,435,870)". 차감하지 않으면 실제 장부금액의 **6.38배**가 저장돼, 사실상
    회수 불가능한 채권을 정상 채권처럼 보이게 만든다.
    """
    parsed = parse_xml_financials(_read_fixture("20220415000297"))
    assert parsed.values_cur["trade_receivables"] == 350_609_229.0  # = 2,237,045,099 - 1,886,435,870
    # 유동자산을 넘지 않는다(총액이면 2,237,045,099 > 유동자산 1,450,098,065로 불가능한 값).
    assert parsed.values_cur["trade_receivables"] < parsed.values_cur["current_assets"]


def test_trade_receivables_bare_allowance_row_fully_provisioned_is_zero():
    """이름 없는 "대손충당금" 인접 행(실측 5,395건으로 지배적인 서식) + 전액 대손 설정.

    (주)명진기업 20220401000156: 매출채권 235,091,466 / 대손충당금 (235,091,466)
    → 순액 0원. "값이 0"과 "값이 없음(None)"은 다르므로 0.0으로 저장돼야 한다.
    """
    parsed = parse_xml_financials(_read_fixture("20220401000156"))
    assert parsed.values_cur["trade_receivables"] == 0.0
    assert parsed.values_prv["trade_receivables"] == 152_439_126.0


def test_trade_receivables_ignores_allowance_belonging_to_another_account():
    """인접하지 않은 이름 없는 대손충당금은 **다른 계정 몫**이라 빼지 않는다.

    흥한건설 20230404002046 재무상태표는 "매출채권 2,244,097,579 / 단기대여금 /
    대손충당금 (18,892,793,483)" 순서다(실측 75건이 이 구조). 인접 여부를 보지 않고
    빼면 매출채권이 -16,648,695,904원이라는 불가능한 값이 된다.
    """
    parsed = parse_xml_financials(_read_fixture("20230404002046"))
    assert parsed.values_cur["trade_receivables"] == 2_244_097_579.0  # 총액 그대로(차감 없음)


def test_is_contra_row_label_rules_for_trade_receivables():
    """차감 대상 판정 규칙 — 인접 행에서만 이름 없는 "대손충당금"을 인정한다."""
    spec = CONTRA_ROW_SPECS["trade_receivables"]
    # 인접(바로 다음 행): 이름 없는 것도, 계정명이 붙은 것도 매출채권 몫으로 본다.
    assert is_contra_row_label("대손충당금", spec, adjacent=True)
    assert is_contra_row_label("매출채권대손충당금", spec, adjacent=True)
    assert is_contra_row_label("대손충당금(매출채권)", spec, adjacent=True)
    # 그 뒤 행: "매출채권"이 함께 적힌 것만 인정한다.
    assert is_contra_row_label("매출채권대손충당금", spec, adjacent=False)
    assert not is_contra_row_label("대손충당금", spec, adjacent=False)
    # 애초에 대손충당금이 아닌 라벨은 어느 위치에서도 아니다.
    assert not is_contra_row_label("단기대여금", spec, adjacent=True)
    assert not is_contra_row_label("정부보조금", spec, adjacent=True)


def test_is_contra_row_label_accepts_measured_allowance_label_variants():
    """대손충당금 라벨의 실측 변형 3종도 contra로 인정한다(2026-08-05, dart-qa L1).

    "대손충당부채"(3건) · 오타 "대손충담금"(2건) · 끝 글자 누락 "대손충당"(1건). 이들은
    "대손충당금" 완전포함 판정에 걸리지 않아 약 1%씩 조용히 과대계상되고 있었다.
    """
    spec = CONTRA_ROW_SPECS["trade_receivables"]
    for label in ("대손충당부채", "대손충담금", "대손충당", "매출채권대손충당부채"):
        assert is_contra_row_label(label, spec, adjacent=True), label


def test_is_contra_row_label_rules_for_cash():
    """현금의 contra는 정부보조금 계열이고, 대손충당금과 마커가 섞이지 않는다."""
    spec = CONTRA_ROW_SPECS["cash_and_equivalents"]
    for label in ("정부보조금", "국고보조금", "(-)정부보조금", "정부조보금", "국고보고조금"):
        assert is_contra_row_label(label, spec, adjacent=True), label
    # 인접하지 않으면 "현금"이 라벨에 함께 적힌 것만 인정한다(다른 자산의 보조금 차단).
    assert is_contra_row_label("현금및현금성자산_국고보조금", spec, adjacent=False)
    assert not is_contra_row_label("정부보조금", spec, adjacent=False)
    # 보조금이 아닌 차감 표기는 등록하지 않았다(사용제한 예금은 별개 개념 — 과잉 일반화 금지).
    assert not is_contra_row_label("특정예금차감", spec, adjacent=True)
    assert not is_contra_row_label("대손충당금", spec, adjacent=True)


def test_cash_and_equivalents_is_net_of_government_grant_contra_row():
    """현금및현금성자산도 **인접 정부보조금 contra 행을 차감한 순액**으로 저장한다.

    영화여객자동차 20230404001968: "현금및현금성자산 2,952,261,667" 다음 행
    "정부보조금 (1,720,000,000)". 차감하지 않으면 현금이 **유동자산 2,530,940,076을
    넘는** 불가능한 값이 된다(실측 112건 중 유일하게 회계 항등식으로 검증되는 사례).
    """
    parsed = parse_xml_financials(_read_fixture("20230404001968"))
    assert parsed.values_cur["cash_and_equivalents"] == 1_232_261_667.0
    assert parsed.values_cur["cash_and_equivalents"] < parsed.values_cur["current_assets"]


def test_cash_and_equivalents_grant_contra_worst_case_overstatement():
    """정부보조금 차감의 과대계상 최대 사례(우성포마 20230412000095, 89.2%).

    현금 총액 22,415,189 / 정부보조금 (20,000,000) → 순액 2,415,189.
    """
    parsed = parse_xml_financials(_read_fixture("20230412000095"))
    assert parsed.values_cur["cash_and_equivalents"] == 2_415_189.0
    assert parsed.values_prv["cash_and_equivalents"] == 23_756_554.0


def test_trade_receivables_nets_allowance_label_variant_from_real_fixture():
    """대손충당금 변형 표기("대손충당부채")도 실제 원문에서 차감된다(한신기업 20230410000109).

    매출채권 1,956,182,102 / 대손충당부채 (19,561,821) → 순액 1,936,620,281.
    """
    parsed = parse_xml_financials(_read_fixture("20230410000109"))
    assert parsed.values_cur["trade_receivables"] == 1_936_620_281.0


def test_depreciation_comes_from_cash_flow_not_income_statement():
    """감가상각비는 **현금흐름표**(제조원가 몫 포함 총액)에서만 가져온다.

    손익계산서 본문의 "감가상각비"는 판매비와관리비에 속한 몫뿐이라 두 값이 다르다 —
    로컬 캐시 실측 7,355건 중 **75.4%가 불일치**했고 제조업은 최대 18배까지 벌어진다.
    한미프렉시블 20260424000057은 손익계산서 196,267,673 / 현금흐름표 1,210,860,947로
    6.2배 차이가 나는데, 저장값은 현금흐름표 쪽이어야 한다.
    """
    parsed = parse_xml_financials(_read_fixture("20260424000057"))
    assert parsed.values_cur["depreciation"] == 1_210_860_947.0
    assert parsed.values_cur["depreciation"] != 196_267_673.0  # 손익계산서 값이 아니다
    # 흥한건설도 같은 규약(손익계산서 31,010,839 / 현금흐름표 48,907,665).
    other = parse_xml_financials(_read_fixture("20230404002046"))
    assert other.values_cur["depreciation"] == 48_907_665.0


def test_interest_expense_comes_from_income_statement_not_cash_flow():
    """이자비용은 **손익계산서**(발생주의 비용)에서만 가져온다.

    현금흐름표에도 같은 이름의 간접법 조정 행이 있지만 실측상 **92.3% 불일치**하고
    (299건 중 276건) 0으로 적힌 경우도 흔하다. 한미프렉시블 20260424000057은
    손익계산서 946,271,041 / 현금흐름표 139,348,178로, 저장값은 손익계산서 쪽이다.
    """
    parsed = parse_xml_financials(_read_fixture("20260424000057"))
    assert parsed.values_cur["interest_expense"] == 946_271_041.0
    assert parsed.values_cur["interest_expense"] != 139_348_178.0  # 현금흐름표 값이 아니다


def test_interest_expense_does_not_fall_back_to_cash_flow_when_absent():
    """손익계산서에 이자비용이 없으면 현금흐름표로 **폴백하지 않고** 결측으로 둔다.

    IFRS "(첨부)재무제표" 서식의 포괄손익계산서는 이자비용 대신 "금융비용"(외환차손 등이
    합쳐진 다른 집계)만 적는다 — 롯데미쓰이화학 20230322000842는 현금흐름표에
    "이자비용 15,766,098,122"가 있지만 손익계산서에는 없으므로 None이어야 한다.
    폴백하면 뜻이 다른 값이 조용히 섞이고, 검수 화면에서 결측으로 드러나지도 않는다.
    """
    parsed = parse_xml_financials(_read_fixture("20230322000842"))
    # 미매핑 필드는 다른 best-effort 항목과 같은 규약으로 **키 자체가 없다**
    # (파이프라인 `_apply_parsed_result`가 `.get(f)`로 읽어 컬럼에 NULL을 넣는다).
    assert parsed.values_cur.get("interest_expense") is None
    # 같은 문서의 현금흐름표 유래 항목(감가상각비)은 정상적으로 채워진다.
    assert parsed.values_cur["depreciation"] == 27_342_599_978.0


def test_trade_receivables_not_polluted_by_cash_flow_working_capital_row():
    """현금흐름표의 "매출채권의 감소(증가)"(운전자본 증감액)가 매출채권 잔액을 덮지 않는다.

    한미프렉시블 20260424000057의 현금흐름표에는 매출채권 관련 행이 2,186,544,542로
    있지만, 저장되는 매출채권은 재무상태표 잔액 9,871,514,894여야 한다(섹션별 alias
    분리의 실증 — 사전을 하나로 합치면 이 테스트가 먼저 깨진다).
    """
    parsed = parse_xml_financials(_read_fixture("20260424000057"))
    assert parsed.values_cur["trade_receivables"] == 9_871_514_894.0


def test_detail_fields_do_not_disturb_standard_fields_on_ifrs_attach_format():
    """IFRS 첨부 서식에서 세부계정 채움이 표준 13항목 추출을 건드리지 않는다.

    첨부 경로는 "이 표에서 표준 항목이 하나라도 매칭됐는가"로 표를 소비할지 정하는데,
    세부계정을 먼저 채우면 캡션/각주 표까지 소비된 것으로 오판해 진짜 데이터 표를
    놓친다. 표준 항목(회계 항등식 포함)이 그대로인지로 확인한다.
    """
    parsed = parse_xml_financials(_read_fixture("20260330001497"))
    assert parsed.parse_status == "OK"
    assert parsed.values_cur["total_assets"] == pytest.approx(
        parsed.values_cur["total_liab"] + parsed.values_cur["total_equity"]
    )
    assert parsed.values_cur["cash_and_equivalents"] == 10_996_202_190.0


def test_detail_values_are_never_negative_in_fixtures():
    """다섯 항목은 실측상 항상 양수(또는 0)다 — 부호를 뒤집는 로직이 끼면 안 된다.

    라벨에 "이익"/"손실"이 없어 `_apply_sign`이 항등이고 첨부 경로의 abs 정규화 대상
    (cogs/sga)도 아니므로, 원문 부호가 그대로 나와야 한다.
    """
    for rcept_no in _DETAIL_FIXTURE_EXPECTED:
        parsed = parse_xml_financials(_read_fixture(rcept_no))
        for field in DETAIL_FINANCIAL_FIELDS:
            for values in (parsed.values_cur, parsed.values_prv):
                value = values.get(field)
                assert value is None or value >= 0, (rcept_no, field, value)


# ---------------------------------------------------------------------------
# pdf_parser.py — 실제 PDF 표본 없음(2026-07-15 기준 원문 30건 전부 XML).
# 손상/비-PDF 입력에 대한 방어 동작만 확인한다.
# ---------------------------------------------------------------------------


def test_parse_pdf_financials_invalid_bytes_returns_failed():
    parsed = parse_pdf_financials(b"this is not a pdf file")
    assert parsed.parse_status == "FAILED"
    assert parsed.parse_note is not None
