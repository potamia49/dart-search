"""파서 공통 인터페이스 + 계정과목명 정규화 사전 + 금액 파싱 유틸.

상세개발계획.md §4-4 (M3). 실제 DART 원문 25건(2026-04~06 수집분) +
2012년 원문 5건(총 30건, backend/tests/fixtures/manifest.json)을 실측해
계정과목 표기 변형과 금액 표기 규칙을 확인한 뒤 작성했다.

실측으로 확인한 원문 구조(DART XML, ACLASS="FINANCE" 테이블):
- 각 행(TR)은 과목명 셀 1개 + 값 셀 N개로 구성되고, 값 셀은 ACODE는 같고
  ADELIM(0=과목명, 1=당기 상세, 2=당기 합계, 3=전기 상세, 4=전기 합계)만
  다르다. 상세 항목은 ADELIM 1/3에, 소계/총계 항목은 ADELIM 2/4에 값이
  들어있고 나머지는 빈 문자열이라, "그룹 내 첫 번째로 비어있지 않은 셀"을
  취하면 당기/전기 값을 안정적으로 뽑을 수 있다 (xml_parser.py 참고).
- 금액은 원(KRW) 단위, 3자리 콤마 구분, 음수는 괄호 표기(예: "(393,502,380)"),
  값 없음은 "-" 또는 빈 문자열.
- "영업손실"/"당기순손실"처럼 과목명이 손실을 명시하는 행은 금액 자체는
  양수로 찍혀 있다(부호 없음) — 표준 필드에 저장할 때는 부호를 뒤집어야
  한다(xml_parser.py의 `_apply_sign` 참고).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol


# PRD 3-2절 표준 13항목 (당기/전기 각각) — results 테이블 컬럼과 1:1 대응.
# "gross_margin"은 원문에 직접 나오는 계정이 아니라 매출액/매출원가로부터
# 계산되는 매출총이익율(%)이다(PRD 3-2절, Result 모델 주석 참고) — 원문의
# "매출총이익"(금액) 행은 그대로 매핑하지 않고 계산에만 사용한다.
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

# xml_parser.py가 원문에서 직접 채우는 필드 (gross_margin 제외 — 계산값).
DIRECT_FINANCIAL_FIELDS: tuple[str, ...] = tuple(
    f for f in STANDARD_FINANCIAL_FIELDS if f != "gross_margin"
)

# 계정과목 표기 변형(공백 제거 후 기준) → 표준 필드 매핑 사전 (v1).
# 실측 샘플(한국학술정보/홈마리나속초호텔 등)에서 확인된 표기를 반영했다.
# 검수 과정(M5)에서 지속 보강한다.
ACCOUNT_NAME_ALIASES: dict[str, str] = {
    "유동자산": "current_assets",
    "비유동자산": "noncurrent_assets",
    "자산총계": "total_assets",
    "유동부채": "current_liab",
    "비유동부채": "noncurrent_liab",
    "부채총계": "total_liab",
    "자본총계": "total_equity",
    "매출액": "revenue",
    "매출액및영업수익": "revenue",
    "영업수익": "revenue",
    "수익(매출액)": "revenue",
    "매출원가": "cogs",
    "판매비와관리비": "sga",
    "영업이익": "operating_income",
    "영업손실": "operating_income",
    "영업이익(손실)": "operating_income",
    "당기순이익": "net_income",
    "당기순손실": "net_income",
    "당기순이익(손실)": "net_income",
}

# 과목명 앞에 붙는 번호/기호 접두어 제거용 (실측: "Ⅰ.매출액"(유니코드 로마숫자),
# "I. 유동자산"(아스키 알파벳 로마숫자 — 회사마다 서식이 다르다), "1.현금및
# 현금성자산", "(1)당좌자산", "가.기초상품재고액" 등). [가-힣] 단일 글자 분기와
# 아스키 로마숫자 분기는 반드시 "."을 요구해야 "자산총계"의 "자"나 평범한
# 영단어 앞글자를 오삭제하지 않는다. 아스키 로마숫자는 접두어 일치 순서상
# 긴 표기(VIII/III/VII)를 짧은 표기(I/V/X)보다 먼저 시도해야 한다.
_PREFIX_RE = re.compile(
    r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.?|(?:VIII|III|VII|IV|VI|IX|II|I|V|X)\.|\d+\.|\([0-9]+\)|[가-힣]\.)\s*"
)

# 과목명 뒤에 붙는 "(주석13)"/"(주6)"/"(주석 2,4)" 같은 각주 참조 제거용
# (실측: "Ⅳ. 판매비와관리비(주석13)", "Ⅱ.매출원가(주6)" — 같은 "주석" 표시가
# 회사마다 "주석"/"주"로 축약 방식이 다르다). 괄호 안이 순수 숫자/콤마/공백
# (+"주석" 또는 "주")일 때만 제거한다 — "당기순이익(손실)"/"수익(매출액)"처럼
# 괄호 안이 실제 항목명을 구성하는 경우까지 지워버리지 않기 위해서다.
_FOOTNOTE_SUFFIX_RE = re.compile(r"\(\s*(?:주석|주)?[\s0-9,]*\)\s*$")

# 금액 문자열에서 콤마/공백 제거용
_AMOUNT_CLEAN_RE = re.compile(r"[,\s　]")

# 빈 문자열: 당기/전기 그룹 내 "이 열은 안 쓰는 열"이라 값이 없음(None).
# "-"류: 원문이 명시적으로 0을 표기하는 관용 표기(예: 당기 비유동부채가 0원인
# 경우도 숫자 0 대신 "-"로 적는다) — None이 아니라 0.0으로 처리해야 한다.
_BLANK_AMOUNT_VALUES = {""}
_ZERO_AMOUNT_VALUES = {"-", "−", "‐", "–"}


def normalize_account_label(label: str) -> str:
    """과목명 표기를 정규화해 ACCOUNT_NAME_ALIASES 조회 키로 변환.

    "Ⅰ.매출액" -> "매출액", "Ⅴ. 영업손실" -> "영업손실",
    "판매비와 관리비" -> "판매비와관리비" 처럼 순번 접두어와 공백을 제거한다.
    """
    text = (label or "").strip()
    for _ in range(2):  # 접두어가 이중으로 붙는 경우는 실측상 없었지만 안전하게 2회 반복
        stripped = _PREFIX_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    for _ in range(2):  # "(주석13)" 같은 각주 참조가 이어 붙는 경우 대비
        stripped = _FOOTNOTE_SUFFIX_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    return text.replace(" ", "").replace("　", "")


def parse_won_amount(text: str) -> float | None:
    """원문 금액 셀 텍스트를 원(KRW) 단위 float로 변환.

    괄호 표기는 음수, "-"/빈 문자열은 값 없음(None)으로 처리한다.
    """
    raw = (text or "").strip()
    if raw in _BLANK_AMOUNT_VALUES:
        return None
    if raw in _ZERO_AMOUNT_VALUES:
        return 0.0
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1].strip()
    cleaned = _AMOUNT_CLEAN_RE.sub("", raw)
    if cleaned in _BLANK_AMOUNT_VALUES:
        return None
    if cleaned in _ZERO_AMOUNT_VALUES:
        return 0.0
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


@dataclass
class ParsedFinancials:
    """파서가 반환하는 결과 컨테이너 (results 테이블 적재 전 중간 표현)."""

    values_cur: dict[str, float | None] = field(default_factory=dict)
    values_prv: dict[str, float | None] = field(default_factory=dict)
    parse_status: str = "FAILED"  # OK / PARTIAL / FAILED
    parse_note: str | None = None


class FinancialStatementParser(Protocol):
    """xml_parser.py / pdf_parser.py가 구현해야 하는 공통 인터페이스."""

    def parse(self, raw_bytes: bytes) -> ParsedFinancials: ...


def compute_gross_margin(revenue: float | None, cogs: float | None) -> float | None:
    """매출총이익율(%) = (매출액-매출원가)/매출액*100. PRD 3-2절 정의.

    원문의 "매출총이익"(금액) 행을 그대로 쓰지 않고 매출액/매출원가로부터
    계산한다 — Result.gross_margin_cur/prv 컬럼이 REAL(%)이기 때문
    (results.py 모델 주석, ACCOUNT_NAME_ALIASES 상단 설명 참고).
    """
    if revenue is None or cogs is None or revenue == 0:
        return None
    return round((revenue - cogs) / revenue * 100, 2)


def determine_parse_status(
    values_cur: dict[str, float | None],
    values_prv: dict[str, float | None],
    *,
    found_any_table: bool,
) -> tuple[str, str | None]:
    """DIRECT_FINANCIAL_FIELDS 충족 여부로 parse_status/parse_note를 판정.

    xml_parser/pdf_parser가 공유하는 순수 판정 로직 (원문 형식과 무관).
    """
    if not found_any_table:
        return "PARTIAL", "재무상태표/손익계산서 테이블을 찾을 수 없음(재무제표 미첨부 등 - 감사의견 확인 필요)"

    missing_cur = [f for f in DIRECT_FINANCIAL_FIELDS if values_cur.get(f) is None]
    missing_prv = [f for f in DIRECT_FINANCIAL_FIELDS if values_prv.get(f) is None]
    if missing_cur or missing_prv:
        return (
            "PARTIAL",
            f"일부 항목 누락: 당기={missing_cur or '없음'} 전기={missing_prv or '없음'}",
        )
    return "OK", None
