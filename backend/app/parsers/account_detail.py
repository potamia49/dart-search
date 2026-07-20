"""감사보고서 원문 XML에서 재무상태표/손익계산서/현금흐름표의 **계정 계층 상세**와
감사의견을 뽑는다.

요약 13항목 + 현금흐름표 4항목 표(당기·전기 표, 재무이력 표)에서 "유동자산" 같은
대분류를 클릭하면 그 아래 세부계정(현금및현금성자산·매출채권 …)을 인라인으로
펼쳐 보여주기 위한 데이터 소스다. 이미 로컬 캐시에 있는 원문 XML을 on-demand로
파싱하므로 추가 API 호출/쿼터가 0건이고, 기존 Job(이미 원문을 받아둔)에서도
즉시 동작한다.

계층 판정은 원문 TE 셀의 `ALEVEL` 속성을 그대로 신뢰한다(실측: L0=대분류
"I. 유동자산", L1=중분류 "(1) 당좌자산", L2=세부계정 "1.현금및현금등가물") —
현금흐름표도 동일한 ALEVEL 구조를 쓴다(실측: L0="Ⅰ.영업활동으로인한현금흐름",
L1="1.당기순이익", L2="감가상각비" 등). 대분류(L0)가 요약 필드(current_assets
등)에 매핑되면, 그 다음 L0 전까지의 하위 행들을 그 필드의 children으로 모은다.
"기말의현금"처럼 그 자체가 총계라 하위 행이 없는 L0 항목(자산총계 등과 동일한
패턴)은 children이 빈 목록으로 남는다 — 파싱 실패가 아니라 원문 구조상 정상이다.
xml_parser와 파싱 유틸(`_row_values` 계열)을 공유한다 — 새 파서를 만들지 않는다.

감사의견은 별도 계정 상세가 아니라 원문 전체에서 `audit_opinion.py`의 판정
로직을 그대로 재사용해 함께 반환한다 — 재무이력(다년치) 표에서 연도마다
서로 다른 감사보고서 원문의 감사의견을 보여주기 위함이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from app.parsers.audit_opinion import extract_audit_opinion
from app.parsers.base import (
    ACCOUNT_NAME_ALIASES,
    CF_ACCOUNT_NAME_ALIASES,
    CF_FINANCIAL_FIELDS,
    DIRECT_FINANCIAL_FIELDS,
    normalize_account_label,
    parse_won_amount,
)
from app.parsers.xml_parser import (
    _BS_TITLE_MARK,
    _CF_TITLE_MARK,
    _IS_TITLE_MARK,
    _OTHER_TITLE_MARKS,
    _decode_raw_xml,
    _period_spans,
    _text_of,
)

_FISCAL_DATE_RE = re.compile(r'AUNIT="PERIODTO"\s+AUNITVALUE="(\d{4})(\d{2})(\d{2})"')


@dataclass
class AccountRow:
    """세부계정 1행 — 라벨(원문 그대로, 각주 포함)/상대 레벨/당기·전기 값."""

    label: str
    level: int
    cur: float | None
    prv: float | None


@dataclass
class AccountDetail:
    """원문 1건에서 뽑은 계정 상세.

    `accounts`는 요약 필드명(current_assets 등) → 그 대분류의 children 리스트.
    `fiscal_year_cur`는 원문 당기 결산연도(YYYY) — 재무이력 표가 특정 연도 열에
    당기/전기 중 어느 값을 써야 하는지 판정하는 데 쓴다.
    `audit_opinion`은 이 원문의 감사의견(적정/한정/부적정/의견거절, 판정 불가 시
    None) — 재무상태표 위에 표시할 안내 행에 쓴다.
    """

    fiscal_year_cur: str | None
    accounts: dict[str, list[AccountRow]] = field(default_factory=dict)
    audit_opinion: str | None = None


def _first_amount(cell_texts: list[str]) -> float | None:
    """그룹 내 여러 열 중 값이 있는 첫 셀을 채택(xml_parser._first_amount와 동일 규칙)."""
    for text in cell_texts:
        amount = parse_won_amount(text)
        if amount is not None:
            return amount
    return None


def _collect_table(
    table: etree._Element,
    accounts: dict[str, list[AccountRow]],
    aliases: dict[str, str],
    valid_fields: tuple[str, ...],
) -> None:
    """FINANCE 테이블 1개를 순회하며 대분류(L0)별 children을 accounts에 채운다.

    `aliases`/`valid_fields`로 재무상태표·손익계산서(ACCOUNT_NAME_ALIASES/
    DIRECT_FINANCIAL_FIELDS)와 현금흐름표(CF_ACCOUNT_NAME_ALIASES/
    CF_FINANCIAL_FIELDS)를 같은 로직으로 처리한다(xml_parser._extract_section과
    동일한 파라미터화 방식).
    """
    cur_span, prv_span = _period_spans(table)
    tbody = table.find("TBODY")
    if tbody is None:
        return
    current_field: str | None = None
    base_level = 0
    for tr in tbody.findall("TR"):
        cells = list(tr)
        if not cells:
            continue
        label = _text_of(cells[0])
        if not label:
            continue
        try:
            level = int(cells[0].get("ALEVEL", "0") or 0)
        except ValueError:
            level = 0
        value_texts = [_text_of(c) for c in cells[1:]]
        cur = _first_amount(value_texts[:cur_span])
        prv = _first_amount(value_texts[cur_span : cur_span + prv_span])

        if level == 0:
            # 새 대분류. 요약 필드로 매핑되면 그 필드의 children 수집을 시작하고,
            # 아니면(자산/부채 헤더, 영업외수익 등) 현재 수집을 닫는다.
            mapped = aliases.get(normalize_account_label(label))
            if mapped in valid_fields:
                current_field = mapped
                base_level = level
                accounts.setdefault(mapped, [])
            else:
                current_field = None
            continue

        # level >= 1: 현재 대분류의 하위계정.
        if current_field is not None:
            accounts[current_field].append(
                AccountRow(label=label, level=level - base_level, cur=cur, prv=prv)
            )


def parse_account_detail(raw_xml: bytes) -> AccountDetail:
    """원문 XML에서 재무상태표/손익계산서/현금흐름표 대분류별 세부계정 + 감사의견을 파싱한다."""
    raw_xml = _decode_raw_xml(raw_xml)
    root = etree.fromstring(raw_xml, parser=etree.XMLParser(recover=True))
    if root is None:
        return AccountDetail(fiscal_year_cur=None)

    raw_text = raw_xml.decode("utf-8", errors="ignore")
    fiscal_year_cur: str | None = None
    m = _FISCAL_DATE_RE.search(raw_text)
    if m:
        fiscal_year_cur = m.group(1)
    audit_opinion = extract_audit_opinion(raw_text)

    accounts: dict[str, list[AccountRow]] = {}
    section: str | None = None
    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str):
            continue
        local_tag = tag.rsplit("}", 1)[-1]

        if local_tag == "TITLE":
            compact = _text_of(el).replace(" ", "").replace("　", "")
            if _BS_TITLE_MARK in compact:
                section = "bs"
            elif _IS_TITLE_MARK in compact:
                section = "is"
            elif _CF_TITLE_MARK in compact:
                section = "cf"
            elif any(mark in compact for mark in _OTHER_TITLE_MARKS):
                section = None
            continue

        if local_tag == "TABLE" and section in ("bs", "is") and el.get("ACLASS") == "FINANCE":
            _collect_table(el, accounts, ACCOUNT_NAME_ALIASES, DIRECT_FINANCIAL_FIELDS)
            section = None  # 구간당 첫 FINANCE 테이블만 사용(xml_parser와 동일)

        elif local_tag == "TABLE" and section == "cf" and el.get("ACLASS") == "FINANCE":
            _collect_table(el, accounts, CF_ACCOUNT_NAME_ALIASES, CF_FINANCIAL_FIELDS)
            section = None

    return AccountDetail(fiscal_year_cur=fiscal_year_cur, accounts=accounts, audit_opinion=audit_opinion)
