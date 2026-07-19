"""감사보고서 원문 XML에서 특정 섹션(재무상태표/손익계산서/현금흐름표/주석)을
잘라 **서버에서 새로 조립한 안전한 HTML**로 변환한다 (§4-8, 2026-07-19).

DART document.xml의 실측 구조(backend/tests/fixtures 30건):
- 재무상태표/손익계산서/현금흐름표 각각은 `<TABLE-GROUP>` 컨테이너 안에
  `<TITLE>`(예: "재 무 상 태 표") + 표(들)로 들어 있다.
- 주석은 `<SECTION-1>`/`<SECTION-2>` 컨테이너 안에 `<TITLE>`("주석") + 다수의
  표/문단으로 들어 있다(주석 미제시 원문은 이 컨테이너에 표가 0개다).

따라서 "섹션 마크에 해당하는 첫 `<TITLE>`을 찾아 그 부모 컨테이너를 통째로
렌더링"하면 해당 섹션만 정확히 잘라낼 수 있다. 렌더링은 원문 마크업을 그대로
통과시키지 않고(XSS 안전) TABLE/TR/TD/TH/TITLE/P만 화이트리스트로 다시
조립하며, 모든 텍스트 노드는 이스케이프하고 셀 속성은 COLSPAN/ROWSPAN만
통과시킨다.
"""

from __future__ import annotations

from html import escape

from lxml import etree

from app.parsers.xml_parser import _decode_raw_xml

# 프론트 버튼 4개와 1:1 대응하는 섹션 키 → 원문 TITLE 매칭 문자열(공백 제거 기준).
SECTION_TITLE_MARKS: dict[str, str] = {
    "bs": "재무상태표",
    "is": "손익계산서",
    "cf": "현금흐름표",
    "notes": "주석",
}

# DART 원문 표의 셀 태그. 실측상 커버 페이지는 TD/TH를 쓰지만 **재무제표 데이터
# 행은 `<TE>`(헤더성 셀은 `<TU>`)** 를 쓴다 — xml_parser._row_values()가
# `list(tr)`로 태그 무관하게 셀을 잡는 것과 달리, 렌더러는 화이트리스트라
# 여기에 TE/TU를 빠뜨리면 데이터 행이 전부 빈 <tr></tr>로 렌더된다(§4-8 회귀).
_DATA_CELL_TAGS = ("TD", "TE")
_HEADER_CELL_TAGS = ("TH", "TU")
_CELL_TAGS = _DATA_CELL_TAGS + _HEADER_CELL_TAGS


def _local(el: etree._Element) -> str:
    tag = el.tag
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text_of(el: etree._Element) -> str:
    return "".join(el.itertext()).strip()


def _render_table(table: etree._Element) -> str:
    rows_html: list[str] = []
    for tr in table.findall(".//TR"):
        cells: list[str] = []
        for cell in tr:
            cl = _local(cell)
            if cl not in _CELL_TAGS:
                continue
            tag = "th" if cl in _HEADER_CELL_TAGS else "td"
            attrs = ""
            for attr in ("COLSPAN", "ROWSPAN"):
                val = cell.get(attr)
                if val and val.isdigit() and val != "1":
                    attrs += f' {attr.lower()}="{escape(val)}"'
            cells.append(f"<{tag}{attrs}>{escape(_text_of(cell))}</{tag}>")
        rows_html.append("<tr>" + "".join(cells) + "</tr>")
    return "<table>" + "".join(rows_html) + "</table>"


def _render_block(el: etree._Element, out: list[str]) -> None:
    """컨테이너를 문서 순서대로 순회하며 화이트리스트 블록만 HTML로 조립.

    TABLE은 원자적으로 렌더링하고 그 하위는 다시 순회하지 않는다. TITLE/P는
    텍스트 블록으로 렌더링한다. 그 외(SECTION/TABLE-GROUP 등 구조 태그)는
    자식으로 재귀한다.
    """
    local = _local(el)
    if local == "TABLE":
        out.append(_render_table(el))
        return
    if local == "TITLE":
        text = _text_of(el)
        if text:
            out.append(f'<h4 class="doc-section-title">{escape(text)}</h4>')
        return
    if local == "P":
        text = _text_of(el)
        if text:
            out.append(f"<p>{escape(text)}</p>")
        return
    for child in el:
        _render_block(child, out)


def extract_section_html(raw_xml: bytes, section: str) -> tuple[bool, str]:
    """`raw_xml`에서 `section`(bs|is|cf|notes)에 해당하는 구간을 HTML로 잘라 반환.

    반환값 `(found, html)` — 해당 섹션 TITLE을 원문에서 찾지 못하면
    `(False, "")`(재무제표/주석 미첨부 등, 에러가 아니라 안내 대상).

    xml_parser와 동일하게 `recover=True`로 파싱한다 — 원문 뒷부분(주석)이
    손상돼 일부가 잘려도 앞부분 구조는 그대로 활용한다(§4-8 열린 질문 3:
    잘린 그대로 보여주되 상위에서 "일부 손상" 안내를 붙이는 방향).
    """
    mark = SECTION_TITLE_MARKS.get(section)
    if mark is None:
        raise ValueError(f"알 수 없는 섹션: {section!r}")

    # 파서와 동일하게 인코딩을 UTF-8로 정규화한다 — 선언부는 utf-8이라 적고
    # 실제 바이트는 EUC-KR/CP949인 원문(실측 약 4.4%)의 원문 열람도 살린다.
    raw_xml = _decode_raw_xml(raw_xml)
    root = etree.fromstring(raw_xml, parser=etree.XMLParser(recover=True))
    if root is None:
        return False, ""

    parent_map = {child: parent for parent in root.iter() for child in parent}

    title_el: etree._Element | None = None
    for el in root.iter():
        if _local(el) == "TITLE" and mark in _text_of(el).replace(" ", "").replace("　", ""):
            title_el = el
            break
    if title_el is None:
        return False, ""

    container = parent_map.get(title_el, title_el)
    blocks: list[str] = []
    _render_block(container, blocks)
    return True, "".join(blocks)
