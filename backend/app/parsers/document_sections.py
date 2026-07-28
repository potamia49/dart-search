"""감사보고서 원문 XML에서 특정 섹션(감사의견/재무상태표/손익계산서/현금흐름표/주석)을
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

**IFRS "(첨부)재무제표" 서식(2026-07-27 추가)**: 이 서식은 재무제표별 `<TITLE>`이
아예 없고 "(첨부)재 무 제 표" TITLE 하나 아래에 4개 재무제표가 들어가므로 위
규칙이 항상 실패한다(로컬 캐시 실측 391건 — 이래CS 20260401004343 등에서 사용자가
"재무이력에는 값이 있는데 원문보기는 못 찾는다"고 신고). TITLE을 못 찾았을 때에만
`xml_parser.walk_statement_tables`(파이프라인 파서와 **같은** 서식 인식 로직)로
제목(<P>/캡션 표)과 데이터 표를 찾아 조립한다 — 인식 기준을 이 파일에 다시
구현하지 않는 것이 핵심이다(그렇게 갈라져 있던 것을 고친 것이다).
"""

from __future__ import annotations

from html import escape

from lxml import etree

from app.parsers.xml_parser import _decode_raw_xml, _first_cell_text, walk_statement_tables

# 프론트 탭과 1:1 대응하는 섹션 키 → 원문 TITLE 매칭 문자열(공백 제거 기준).
#
# "audit"(감사보고서 본문 = 감사의견 문단)은 2026-07-20 추가했다. 실측(fixtures
# 30건 전부)상 서식이 두 가지인데 — 신서식 "독립된 감사인의 감사보고서",
# 2012년 구서식 "외부감사인의 감사보고서" — 공통 부분문자열 "감사보고서"로
# 잡으면 둘 다 매칭된다(30/30). 그 부모 `<SECTION-1>`이 의견 문단과 표를
# 통째로 담고 있어 다른 섹션과 동일한 "TITLE의 부모 컨테이너" 규칙이 그대로
# 통한다. 목차의 "목 차" TITLE에는 이 문자열이 없어 오탐하지 않는다.
SECTION_TITLE_MARKS: dict[str, str] = {
    "bs": "재무상태표",
    "is": "손익계산서",
    "cf": "현금흐름표",
    "notes": "주석",
    "audit": "감사보고서",
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


# "(첨부)재무제표" 첨부 서식에서 서버가 조립해 줄 수 있는 섹션(재무제표 3종).
# "주석"/"감사보고서"는 이 서식에서도 독립 <TITLE>로 나오므로 기존 경로가 그대로 처리한다.
_ATTACH_RENDERABLE_SECTIONS = ("bs", "is", "cf")


def _extract_attach_section_html(root: etree._Element, section: str) -> tuple[bool, str]:
    """IFRS "(첨부)재무제표" 서식 원문에서 `section` 구간을 HTML로 조립한다.

    이 서식은 본문에 "재무상태표"/"손익계산서"/"현금흐름표" `<TITLE>`이 아예 없고
    "(첨부)재 무 제 표" TITLE 하나 아래에 4개 재무제표가 들어가며, 각 재무제표의
    제목은 독립 `<P>` 또는 캡션 `<TABLE>`의 첫 셀로 나온다(xml_parser 모듈
    독스트링 참고). 서식 인식은 파이프라인 파서와 **같은** `walk_statement_tables`
    에 맡기고, 여기서는 그 워커가 알려주는 제목/표만 렌더링한다 — 인식 기준을
    여기에 다시 구현하면 서식 확장이 한쪽에만 반영돼 또 갈라진다(2026-07-27).

    표를 "소비"하지 않고(visit이 항상 False) 다음 재무제표 제목이 나올 때까지의
    표를 모두 모으므로, 데이터 표 뒤의 "별첨 주석은 본 재무제표의 일부입니다"
    같은 부속 표까지 원문 순서대로 함께 보여준다.
    """
    blocks: list[str] = []
    rendering = False

    def on_section_title(sec: str, el: etree._Element) -> None:
        nonlocal rendering
        rendering = sec == section
        if not rendering:
            return
        # 제목은 <P>(텍스트)일 수도 캡션 <TABLE>(첫 셀)일 수도 있다 — 어느 쪽이든
        # FINANCE 경로의 <TITLE>과 같은 모양(h4)으로 렌더링해 화면을 일관되게 한다.
        text = _first_cell_text(el) if _local(el) == "TABLE" else _text_of(el)
        if text:
            blocks.append(f'<h4 class="doc-section-title">{escape(text)}</h4>')

    def visit(fmt: str, sec: str, table: etree._Element) -> bool:
        if fmt == "attach" and rendering:
            _render_block(table, blocks)
        return False  # 표를 소비하지 않고 다음 제목까지 이어서 모은다

    walk_statement_tables(
        root, visit, on_section_title=on_section_title, detect_caption_while_pending=True
    )
    return bool(blocks), "".join(blocks)


def extract_section_html(raw_xml: bytes, section: str) -> tuple[bool, str]:
    """`raw_xml`에서 `section`(bs|is|cf|notes|audit)에 해당하는 구간을 HTML로 잘라 반환.

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
        # IFRS "(첨부)재무제표" 서식은 재무제표별 <TITLE>이 없어 위 경로가 항상
        # 실패한다(로컬 캐시 실측 391건) — 이 경우에만 첨부 서식 경로를 시도한다.
        # FINANCE 서식(TITLE을 찾은 원문)은 이 분기에 들어오지 않아 무변경이다.
        if section in _ATTACH_RENDERABLE_SECTIONS:
            return _extract_attach_section_html(root, section)
        return False, ""

    container = parent_map.get(title_el, title_el)
    blocks: list[str] = []
    _render_block(container, blocks)
    return True, "".join(blocks)
