"""DART 원본 XML 재무제표 파싱 (1순위).

상세개발계획.md §4-4. 실제 DART document.xml 30건(backend/tests/fixtures,
2012~2026년 원문 포함)을 실측해 아래 구조를 확인한 뒤 작성했다:

- `<TITLE>` 텍스트로 "재무상태표"/"손익계산서" 구간을 찾고, 그 구간에서 처음
  만나는 `<TABLE ACLASS="FINANCE">` 1개를 그 재무제표의 본문 테이블로 본다
  (자본변동표/현금흐름표/주석도 ACLASS="FINANCE"를 쓰므로, TITLE을 만날
  때마다 상태를 리셋해 혼입을 막는다).
- 테이블 THEAD의 TH COLSPAN으로 당기/전기 열 개수를 판별하고, TBODY 각 행은
  과목명 셀 1개 + 값 셀 N개(당기 그룹 + 전기 그룹)로 구성된다. 실측 샘플
  전부 그룹당 2열(상세/합계, 자세한 설명은 base.py 참고)이었지만 오래된
  서식은 1열일 수도 있어 COLSPAN을 그대로 신뢰한다.
- 의견거절 등으로 재무제표 자체가 첨부되지 않은 원문(실측 25건 중 10건)은
  ACLASS="FINANCE" 테이블이 아예 없다 — 이 경우 파싱 실패가 아니라 "원문에
  없음"이므로 PARTIAL로 판정한다(base.py의 `determine_parse_status`).
"""

from __future__ import annotations

import logging

from lxml import etree

from app.parsers.base import (
    ACCOUNT_NAME_ALIASES,
    ParsedFinancials,
    compute_gross_margin,
    determine_parse_status,
    normalize_account_label,
    parse_won_amount,
)

logger = logging.getLogger(__name__)

_BS_TITLE_MARK = "재무상태표"
_IS_TITLE_MARK = "손익계산서"
# 이 마커들을 만나면 재무상태표/손익계산서 구간 추적을 멈춘다(자본변동표 등과 혼입 방지).
_OTHER_TITLE_MARKS = ("자본변동표", "결손금처리계산서", "이익잉여금처분계산서", "현금흐름표", "주석", "외부감사")


def _text_of(el: etree._Element) -> str:
    return "".join(el.itertext()).strip()


def _row_values(table: etree._Element) -> list[tuple[str, str, list[str]]]:
    """FINANCE 테이블 1개의 TBODY 행을 (원본라벨, 정규화라벨, 값셀텍스트리스트)로 변환."""
    tbody = table.find("TBODY")
    if tbody is None:
        return []
    rows: list[tuple[str, str, list[str]]] = []
    for tr in tbody.findall("TR"):
        cells = list(tr)
        if not cells:
            continue
        label = _text_of(cells[0])
        if not label:
            continue
        value_texts = [_text_of(c) for c in cells[1:]]
        rows.append((label, normalize_account_label(label), value_texts))
    return rows


def _period_spans(table: etree._Element) -> tuple[int, int]:
    """THEAD TH의 COLSPAN으로 (당기 열수, 전기 열수)를 판별. 실측 기본값은 (2, 2)."""
    thead = table.find("THEAD")
    if thead is None:
        return 2, 2
    ths = thead.findall(".//TH")
    period_ths = ths[1:]  # 첫 TH는 "과목" 라벨 컬럼
    spans = [int(th.get("COLSPAN", "1")) for th in period_ths]
    if len(spans) >= 2:
        return spans[0], spans[1]
    if len(spans) == 1:
        return spans[0], spans[0]
    return 2, 2


def _first_amount(cell_texts: list[str]) -> float | None:
    """그룹 내 여러 열(상세/합계) 중 값이 있는 첫 셀을 채택 (base.py 모듈독스트링 참고)."""
    for text in cell_texts:
        amount = parse_won_amount(text)
        if amount is not None:
            return amount
    return None


def _apply_sign(raw_label: str, value: float | None) -> float | None:
    """"영업손실"/"당기순손실"처럼 손실이 명시된 행은 원문상 양수라 부호를 뒤집는다."""
    if value is None:
        return None
    if "손실" in raw_label and value > 0:
        return -value
    return value


def _extract_section(table: etree._Element, values_cur: dict, values_prv: dict) -> None:
    cur_span, prv_span = _period_spans(table)
    for raw_label, norm_label, value_texts in _row_values(table):
        field = ACCOUNT_NAME_ALIASES.get(norm_label)
        if field is None or field in values_cur:
            continue  # 미매핑 과목이거나 이미 채워진 표준 필드(첫 매칭 우선)는 건너뜀
        cur_group = value_texts[:cur_span]
        prv_group = value_texts[cur_span : cur_span + prv_span]
        values_cur[field] = _apply_sign(raw_label, _first_amount(cur_group))
        values_prv[field] = _apply_sign(raw_label, _first_amount(prv_group))


def parse_xml_financials(raw_xml: bytes) -> ParsedFinancials:
    """감사보고서 원문 XML에서 재무상태표/손익계산서를 파싱해 표준 13항목을 채운다."""
    # 실측 샘플 30건 중 다수가 본문 텍스트에 "<"/"&"를 이스케이프하지 않은 채
    # 담고 있어(예: 서술형 문장 속 부등호, 앰퍼샌드) 엄격 모드로는 파싱 자체가
    # 실패한다. recover=True로 손상된 부분만 건너뛰고 나머지 구조는 그대로
    # 활용한다 — DART 원문은 표 구조가 앞부분(재무상태표/손익계산서)에 있고
    # 깨지는 지점은 대개 뒤쪽 서술형 주석이라 실사용에 지장이 없다.
    parser = etree.XMLParser(recover=True)
    root = etree.fromstring(raw_xml, parser=parser)
    if root is None:
        logger.warning("XML 파싱 실패(복구 불가): %s", parser.error_log)
        return ParsedFinancials(parse_status="FAILED", parse_note=f"XML 구문 오류(복구 불가): {parser.error_log}")

    values_cur: dict[str, float | None] = {}
    values_prv: dict[str, float | None] = {}
    found_any_table = False
    section: str | None = None

    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str):
            continue
        local_tag = tag.rsplit("}", 1)[-1]

        if local_tag == "TITLE":
            title_text = _text_of(el)
            compact = title_text.replace(" ", "").replace("　", "")
            if _BS_TITLE_MARK in compact:
                section = "bs"
            elif _IS_TITLE_MARK in compact:
                section = "is"
            elif any(mark in compact for mark in _OTHER_TITLE_MARKS):
                section = None
            continue

        if local_tag == "TABLE" and section in ("bs", "is") and el.get("ACLASS") == "FINANCE":
            found_any_table = True
            _extract_section(el, values_cur, values_prv)
            section = None  # 구간당 첫 FINANCE 테이블만 사용

    values_cur["gross_margin"] = compute_gross_margin(values_cur.get("revenue"), values_cur.get("cogs"))
    values_prv["gross_margin"] = compute_gross_margin(values_prv.get("revenue"), values_prv.get("cogs"))

    status, note = determine_parse_status(values_cur, values_prv, found_any_table=found_any_table)
    return ParsedFinancials(
        values_cur=values_cur, values_prv=values_prv, parse_status=status, parse_note=note
    )
