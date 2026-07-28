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

**IFRS "(첨부)재무제표" 서식(2026-07-27 추가)**: 이 서식은 재무제표별 `<TITLE>`이
없고 데이터 표가 ACLASS="NORMAL"이라, 서식 인식을 파이프라인 파서와 공유하는
공용 워커(`xml_parser.walk_statement_tables`)에 맡긴다. 표 **안**을 읽는 방식만
서식별로 갈린다 — 첨부 서식의 데이터 표는 평범한 `<TD>`라 `ALEVEL`이 아예 없어
계층을 선행 공백(들여쓰기)의 상대 순위로 판정하고(`_attach_levels`), "과목|주석|
당기|전기"처럼 값 사이에 낀 주석 열은 `xml_parser.attach_period_plan`의 열 계획으로
건너뛴다. 부호 함수는 파이프라인과 같은 `_apply_sign_ifrs`를 재사용하지만, 실제로
화면에 실리는 세부계정 행은 항상 원문 부호 그대로다 — 매출원가/판관비로 매핑되는
라벨은 대분류(요약) 행으로 소비돼 children에 들어가지 않으므로, abs 정규화 분기는
이 경로에서 실행되지 않는다(2026-07-27 dart-qa 지적으로 문구 정정).

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
    NON_OPERATING_FINANCIAL_FIELDS,
    has_roman_numeral_prefix,
    normalize_account_label,
    parse_won_amount,
)

# 손익계산서 세부계정 펼치기에서 대분류(L0)로 인식할 필드 = 표준 13항목 +
# 영업외수익/영업외비용(best-effort). 후자를 valid_fields에 넣지 않으면 원문의
# "영업외수익"/"영업외비용" L0 행에서 current_field가 닫혀 그 하위 세부계정
# (이자수익/외환차익/이자비용/외환차손 등)까지 통째로 유실된다(2026-07-22).
_BS_IS_VALID_FIELDS: tuple[str, ...] = DIRECT_FINANCIAL_FIELDS + NON_OPERATING_FINANCIAL_FIELDS
from app.parsers.xml_parser import (
    _apply_sign_ifrs,
    _decode_raw_xml,
    _period_spans,
    _text_of,
    attach_label_indent,
    attach_period_plan,
    walk_statement_tables,
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
            # 새 대분류. 요약 필드(valid_fields)로 매핑되면 그 필드의 children
            # 수집을 시작하고, 아니면(자산/부채 헤더 등 valid_fields 밖 항목)
            # 현재 수집을 닫는다.
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


# 하위계정 번호 접두어(로마숫자 **아닌** 번호 체계) 판정용. 실측상 첨부 서식은
# 대분류를 로마숫자("Ⅰ."/"I.")나 무번호("자산총계")로, 하위계정을 아라비아 숫자
# ("1.")·괄호숫자("(1)")·한글("가.")로 적는다. 들여쓰기가 계층을 구분하지 못하는
# 원문에서만 보조 단서로 쓴다(아래 `_attach_levels` 참고).
_SUBITEM_PREFIX_RE = re.compile(r"^(?:\d+\s*[.)]|\([0-9]+\)|[가-힣]\s*\.)")


def _label_depth_class(label: str) -> int:
    """라벨의 번호 접두어가 하위계정 체계면 1, 대분류 체계(로마숫자/무번호)면 0."""
    return 1 if _SUBITEM_PREFIX_RE.match(label.strip()) else 0


def _attach_levels(rows: list[etree._Element]) -> tuple[list[int], int]:
    """첨부 서식 데이터 표의 행별 계층 레벨을 판정한다(들여쓰기 우선, 번호 보조).

    반환값은 `(행별 레벨, 대분류 들여쓰기 폭)`이다.

    FINANCE 서식은 셀 `ALEVEL` 속성이 계층을 명시하지만 "(첨부)재무제표" 서식의
    데이터 표는 평범한 `<TD>`라 ALEVEL이 없다 — 대분류는 왼쪽 끝에서 시작하고
    ("Ⅰ.유동자산") 하위 계정은 공백으로 들여쓰는 것이 주 단서다. 들여쓰기 폭이
    회사마다 1~4칸으로 제각각이라(롯데미쓰이 1칸 / 세아엠앤에스 3칸 / 이래CS
    4칸, 2026-07-27 실측) 절대 폭이 아니라 **상대 순위**로 레벨을 매긴다:

    - **대분류(레벨 0) 판정**: 각 행의 판정 키는 `(들여쓰기 폭, 번호 체계)`이고,
      표 전체의 **최소 키**를 가진 행이 대분류다.
    - **하위계정의 레벨**: 나머지 행은 하위계정이며, **그 대분류 그룹 안에서**
      나타난 들여쓰기 폭만 정렬해 1,2,3…을 매긴다(FINANCE 경로의
      `level - base_level`과 같은 "대분류 기준 상대 레벨" 의미 유지 — 그룹마다
      들여쓰기 폭이 달라도 첫 하위 계정은 항상 레벨 1).

    키의 두 번째 요소(번호 체계)는 **들여쓰기가 계층을 구분하지 못하는 원문**을
    위한 보조 단서이며 **대분류 경계 판정에만** 쓴다: 하위계정이 들여쓰기를
    잃어버려 대분류와 같은 열에서 시작하는 실측 원문이 있다(20250411002450 /
    20250411003158 재무상태표의 "I. 유동부채" 아래 "6. 기타유동부채", 세아엠앤에스
    20230321000531 현금흐름표는 아예 전 행이 들여쓰기 0 — 이 경우 보조 단서가
    없으면 하위계정이 통째로 비어 버린다). 반대로 이 단서를 **레벨 순위**에까지
    쓰면 안 된다 — 번호가 붙은 소계 행이 번호 없는 세부 행의 **부모**인 원문
    (20250328000629 / 20260331002166 현금흐름표: "1. 투자활동으로 인한 현금유입액"
    아래에 번호 없는 세부 항목들이 같은 들여쓰기로 온다)에서 부모·자식 레벨이
    뒤집힌다. 번호는 "깊이"를 신뢰할 수 있는 신호가 아니다(실측 확인).

    계층 단서가 아예 없는 표(모든 행의 키가 같음)는 모든 행이 레벨 0이 돼 대분류만
    잡히고 하위계정은 비게 된다 — 잘못된 계층을 지어내는 것보다 안전하다.
    라벨이 빈 행은 레벨 0으로 두되 어차피 호출부에서 건너뛴다.
    """
    indents: list[int | None] = []
    keys: list[tuple[int, int] | None] = []
    for tr in rows:
        cells = list(tr)
        label = _text_of(cells[0]) if cells else ""
        if not label:
            indents.append(None)
            keys.append(None)
            continue
        indent = attach_label_indent(cells[0])
        indents.append(indent)
        keys.append((indent, _label_depth_class(label)))

    present = {k for k in keys if k is not None}
    if not present:
        return [0] * len(rows), 0
    base = min(present)

    levels = [0] * len(rows)
    group: list[int] = []  # 현재 대분류 그룹에 속한 하위 행 인덱스

    def close_group() -> None:
        # 레벨 순위는 들여쓰기만으로 매긴다(번호 체계는 경계 판정 전용, 위 참고).
        ranks = {w: n + 1 for n, w in enumerate(sorted({indents[i] for i in group}))}
        for i in group:
            levels[i] = ranks[indents[i]]
        group.clear()

    for idx, key in enumerate(keys):
        if key is None:
            continue  # 빈 라벨 행(수집 대상 아님)은 그룹을 끊지 않는다
        if key == base:
            close_group()  # 대분류 행에서 그룹을 끊는다
            continue
        group.append(idx)
    close_group()
    return levels, base[0]


def _collect_attach_table(
    table: etree._Element,
    accounts: dict[str, list[AccountRow]],
    aliases: dict[str, str],
    valid_fields: tuple[str, ...],
) -> bool:
    """IFRS "(첨부)재무제표" 서식의 데이터 표 1개에서 대분류별 children을 채운다.

    열 계획(주석 열 건너뛰기)은 `xml_parser.attach_period_plan`을, 부호 함수는 첨부
    전용 규칙 `_apply_sign_ifrs`를 그대로 쓴다. 단 children으로 append되는 행은
    매출원가/판관비 등 표준 필드로 매핑되는 라벨이 전부 대분류(요약) 행으로
    소비된 뒤라 sign_field가 항상 빈 문자열이고, 결과적으로 화면의 세부계정은
    항상 원문 부호 그대로 나온다 — abs 정규화 분기는 이 경로에서 실행되지 않는다.

    반환값은 "이 표에서 대분류를 하나라도 잡았는가" — 워커가 이 표를 그 섹션의
    본문 표로 소비할지 판단하는 데 쓴다(캡션/기간/각주 표는 False가 돼 건너뛴다).
    """
    planned = attach_period_plan(table)
    if planned is None:
        return False
    (cur_cols, prv_cols), data_rows = planned
    levels, base_indent = _attach_levels(data_rows)

    matched = False
    current_field: str | None = None
    # 현재 대분류 그룹이 "번호 접두어 단서로만" 하위계정을 잡고 있는지(=원문이
    # 들여쓰기를 주지 않는 표인지)와, 그때 하위계정에 매긴 레벨. 아래 "계속 행"
    # 처리에 쓴다.
    flat_child_level: int | None = None
    for level, tr in zip(levels, data_rows):
        cells = list(tr)
        if not cells:
            continue
        label = _text_of(cells[0])
        if not label:
            continue
        mapped = aliases.get(normalize_account_label(label))
        # `mapped`가 표준 필드(cogs/sga 포함)면 아래 valid_fields 분기에서 대분류로
        # continue되고 children에는 절대 append되지 않으므로, 여기 도달하는 행의
        # sign_field는 항상 ""(원문 부호 그대로) — abs 정규화가 실행될 일이 없다.
        sign_field = mapped or ""
        cur = _apply_sign_ifrs(
            sign_field, _first_amount([_text_of(cells[c]) for c in cur_cols if c < len(cells)])
        )
        prv = _apply_sign_ifrs(
            sign_field, _first_amount([_text_of(cells[c]) for c in prv_cols if c < len(cells)])
        )

        # 요약 필드로 매핑되는 행은 들여쓰기와 무관하게 항상 새 대분류로 본다.
        # 첨부 서식은 요약 항목을 들여써 적기도 하기 때문이다(롯데미쓰이
        # 20250324000776 손익계산서: "III. 매출총이익"(들여쓰기 0) 다음 줄의
        # "판매비와관리비"가 한 칸 들여쓰기 — 들여쓰기만 보면 매출총이익의
        # 하위계정으로 붙어 화면에 엉뚱하게 표시된다). 파이프라인
        # `_extract_attach_section`도 레벨과 무관하게 라벨만 보고 매핑한다.
        if mapped in valid_fields:
            current_field = mapped
            accounts.setdefault(mapped, [])
            matched = True
            flat_child_level = None
            continue

        if level == 0:
            # 들여쓰기·번호가 모두 대분류와 같은 행. 이 그룹이 이미 "번호 단서로만"
            # 하위계정을 잡고 있고(=원문에 들여쓰기가 없고) 이 행에 금액이 있으면
            # 같은 대분류의 하위계정으로 이어 붙인다 — 그러지 않으면 번호가 붙은
            # 소계 행만 잡히고 그 사이의 번호 없는 세부 행이 통째로 빠져, 화면에
            # "합계가 맞지 않는 일부 목록"이 나온다(20230321000429 현금흐름표 실측:
            # 전 행이 들여쓰기 0이라 "1./2."만 잡히고 "3./4."와 세부 행이 유실됐다).
            # 단 ① 금액이 없는 행("자산"/"부채" 같은 구역 헤더)과 ② 로마숫자 항목번호
            # 행("Ⅳ.현금의 증감" — 첨부 서식에서 로마숫자는 구역 표지다)은 세부계정이
            # 아니므로 대분류를 닫는다(세아엠앤에스 20230321000531 현금흐름표 실측:
            # 이 가드가 없으면 재무활동 구간에 "Ⅳ.현금의 증감"/"Ⅴ.기초의 현금"이
            # 하위계정으로 딸려 들어간다).
            if (
                current_field is not None
                and flat_child_level is not None
                and (cur is not None or prv is not None)
                and not has_roman_numeral_prefix(label)
            ):
                accounts[current_field].append(
                    AccountRow(label=label, level=flat_child_level, cur=cur, prv=prv)
                )
                continue
            current_field = None
            flat_child_level = None
            continue

        if current_field is None:
            continue
        if attach_label_indent(cells[0]) == base_indent:
            # 들여쓰기는 대분류와 같은데 번호 단서로 하위계정이 된 행 — 이 그룹은
            # 들여쓰기 신호가 없다는 뜻이라 위 "계속 행" 처리를 켠다.
            flat_child_level = level
        accounts[current_field].append(AccountRow(label=label, level=level, cur=cur, prv=prv))
    return matched


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

    def visit(fmt: str, section: str, table: etree._Element) -> bool:
        """워커가 넘긴 표에서 세부계정을 수집한다(반환값=소비 여부).

        FINANCE 서식과 IFRS "(첨부)재무제표" 서식을 **파이프라인 파서와 완전히
        동일한 기준**(`walk_statement_tables`)으로 인식하고, 표 안을 읽는 방식만
        서식별로 나눈다 — 첨부 서식은 ALEVEL이 없고 주석 열이 끼기 때문이다.
        """
        cf = section == "cf"
        aliases = CF_ACCOUNT_NAME_ALIASES if cf else ACCOUNT_NAME_ALIASES
        valid_fields = CF_FINANCIAL_FIELDS if cf else _BS_IS_VALID_FIELDS
        if fmt == "finance":
            _collect_table(table, accounts, aliases, valid_fields)
            return True  # 구간당 첫 FINANCE 테이블만 사용(xml_parser와 동일)
        return _collect_attach_table(table, accounts, aliases, valid_fields)

    walk_statement_tables(root, visit)

    return AccountDetail(fiscal_year_cur=fiscal_year_cur, accounts=accounts, audit_opinion=audit_opinion)
