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
import re
from collections.abc import Callable

from lxml import etree

from app.parsers.base import (
    ACCOUNT_NAME_ALIASES,
    CF_ACCOUNT_NAME_ALIASES,
    CF_FINANCIAL_FIELDS,
    ParsedFinancials,
    determine_parse_status,
    normalize_account_label,
    parse_won_amount,
)

logger = logging.getLogger(__name__)

_BS_TITLE_MARK = "재무상태표"
_IS_TITLE_MARK = "손익계산서"
_CF_TITLE_MARK = "현금흐름표"  # §4-8: 종료 마커에서 파싱 대상 섹션 "cf"로 승격
# 이 마커들을 만나면 재무상태표/손익계산서 구간 추적을 멈춘다(자본변동표 등과 혼입 방지).
# "현금흐름표"는 이제 별도 섹션으로 파싱하므로 여기서 제외한다.
_OTHER_TITLE_MARKS = ("자본변동표", "결손금처리계산서", "이익잉여금처분계산서", "주석", "외부감사")

# "(첨부)재무제표" 첨부문서 구조(IFRS 적용사에서 실측, 2026-07-22, 로컬 캐시
# 4,922건 중 29건 — 롯데미쓰이화학 20250324000776 외). 이 서식은 본문에
# "재무상태표"/"손익계산서"/"현금흐름표" TITLE이 아예 없고, 대신 "(첨부)재 무 제 표"
# (또는 "(첨부)연 결 재 무 제 표") 라는 TITLE 하나 아래에 4개 재무제표가 모두
# 들어간다. 그 안에서 각 재무제표의 제목("재 무 상 태 표" 등)은 <TITLE>이 아니라
# ① 독립 <P>(롯데미쓰이) 또는 ② THEAD 없는 캡션 <TABLE>의 첫 셀(하이에어)로
# 나타나고, 실제 데이터 표는 ACLASS="FINANCE"가 아니라 ACLASS="NORMAL"이며
# "과목 | 주석 | 당기 | 전기" 처럼 값 사이에 "주석" 열이 끼어 있다. 이 세 가지
# (섹션명이 P/캡션표에 있음, 표가 NORMAL, 주석 열 삽입)를 모두 흡수하기 위해
# 별도 경로(_extract_attach_section)로 파싱한다. 기존 FINANCE 경로는 무변경이다.
_ATTACH_TITLE_MARK = "재무제표"
# 재무제표 제목 앞에 붙는 수식어(연결/별도/개별/포괄) — 제거 후 정본 제목과 대조한다.
_ATTACH_CAPTION_PREFIXES = ("연결", "별도", "개별", "포괄")


_XML_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)

# 셀 안에 이스케이프되지 않은 한글 유령 태그(예: "1. 현금및현금성자산 <주석3>",
# "<당기>"/"<전기>"/"<당기말>"/"<전기말>")를 담은 원문이 실측된다
# (2026-07-21, 제이에스원 rcept 20260413001757). recover=True 파서는 단순한
# 부등호("a < b")는 텍스트로 흘려보내지만, "<주석3>"처럼 이름이 유효한
# (한글로 시작하는) 태그 꼴은 **실제 시작 태그**로 인식해 버린다. 이 유령
# 요소는 닫히지 않으므로 뒤따르는 형제 TE/TR 전체가 그 안으로 빨려 들어가
# 한 셀로 붕괴되고, 결과적으로 그 지점 이후의 자산총계/부채총계/자본총계/
# 영업이익/당기순이익 등이 통째로 유실돼(값은 원문에 멀쩡히 있는데) 진성
# 결측처럼 PARTIAL로 떨어졌다. 정상 DART 스키마의 태그명은 전부 아스키
# 대문자(TABLE/TR/TE/THEAD/…)라 "한글로 시작하는 태그"는 항상 이런 미이스케이프
# 마커이므로, 태그 마크업만 제거해 트리 붕괴를 막는다(셀 텍스트는 어차피
# normalize_account_label에서 각주 접미어·공백이 제거된다). 공백이 낀
# "< 주석3>"은 lxml도 태그로 보지 않아(태그명은 공백으로 시작 불가) 붕괴를
# 일으키지 않으므로 대상에서 제외한다(과잉 일반화 금지).
_HANGUL_PSEUDO_TAG_RE = re.compile(r"</?[가-힣][^<>]*>")


def _decode_raw_xml(raw_xml: bytes) -> bytes:
    """원문 bytes를 lxml이 파싱할 수 있는 UTF-8 bytes로 정규화한다.

    실측(로컬 캐시 1,453건, 2026-07-19): 약 4.4%(64건)가 XML 선언부에는
    `encoding="utf-8"`이라고 적어놓고 실제 바이트는 EUC-KR/CP949로 인코딩돼
    있다. 이런 원문은 bytes를 그대로 lxml에 넘기면 recover=True로도 복구되지
    않고(인코딩 오류는 XML 구조 오류가 아니라 파싱 진입 단계의 fatal error)
    `XMLSyntaxError: Input is not proper UTF-8`로 실패한다 — 실측 64건 전부
    CP949로 디코딩하면 정상 파싱되고(그 중 51건은 재무 테이블까지 복구),
    Job #14에서 이 인코딩 1건이 Job 전체를 죽였던 회귀의 근본 원인이다.

    정상 UTF-8 원문(약 95.6%)은 바이트를 그대로 반환해 기존 동작을 전혀
    건드리지 않는다. UTF-8 디코딩이 실패할 때만 CP949로 폴백해 디코딩하고,
    (이제는 거짓이 된) 인코딩 선언부를 제거한 뒤 UTF-8로 재인코딩한다.
    CP949마저 실패하면 최후 수단으로 UTF-8 errors="replace"로 복구를 시도한다.
    """
    try:
        raw_xml.decode("utf-8")
        return raw_xml  # 정상 UTF-8: 기존 경로 그대로 (바이트 무변경)
    except UnicodeDecodeError:
        pass
    try:
        text = raw_xml.decode("cp949")  # EUC-KR 상위호환
    except UnicodeDecodeError:
        logger.warning("원문 인코딩이 UTF-8/CP949 모두 아님 — errors='replace'로 복구 시도")
        text = raw_xml.decode("utf-8", errors="replace")
    # 디코딩을 우리가 이미 했으므로, 거짓 인코딩 선언부는 제거하고 UTF-8 bytes로 넘긴다.
    text = _XML_DECL_RE.sub("", text, count=1)
    return text.encode("utf-8")


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
    """"손실" 표기 라벨의 부호 규칙(2026-07-20 수정, 사용자 실측 지적).

    라벨은 실측상 두 갈래로 나뉜다:
    - **손실만 명시된 라벨**("영업손실"/"매출총손실"/"당기순손실" — "이익"이 없는
      순수 손실 행): 그 자체가 "손실 금액"이라는 뜻이라 원문 부호와 무관하게
      항상 반전한다. 실측 다수(예: 20260630000895 "영업손실" 당기값
      "2,264,996,073")는 양수로 찍혀 있어 뒤집으면 음의 영업손익이 된다.
      드물게 이미 괄호로 음수 표기된 경우는 "음의 손실 = 이익"이라는 뜻이므로
      다시 뒤집어 양수(이익)로 저장한다.
    - **조합형 라벨**(회사가 흑자든 적자든 같은 줄을 재사용하는 템플릿):
      **먼저 나오는(=주 계정) 키워드가 부호 기준**이다. 원문 부호는 그 주 계정
      기준으로 찍혀 있다.
      · "이익(손실)" 계열(이익이 앞 — 흑자 기업 서식): 양수=이익, 괄호=손실이라
        원문 부호가 곧 경제적 부호다 → 그대로 신뢰(반전 안 함). 실측(EUC-KR
        20220127000408) "영업이익(손실)"이 "(6,308,961,098)"로 음수 표기.
      · "손실(이익)" 계열(손실이 앞 — 적자 기업 서식): 양수=손실, 괄호=이익이라
        원문 부호가 경제적 부호와 **반대**다 → 반드시 반전한다. 이 갈래를
        "이익이 있으면 무조건 신뢰"로 잘못 묶으면 적자 기업의 매출총손실/
        영업손실/당기순손실이 흑자로 뒤집힌다(2026-07-21 dart-qa 실측 — 예:
        20260413003038 "매출총손실(이익)" 당기 "2,574,766,473"는 실제 매출총
        손실인데 revenue-cogs 항등식과 부호가 반대였다. 20250414000612/
        20260414000629/20240412000495도 동일). 같은 문서의 요약행
        "당기순이익(손실)"(이익 앞)과 대조하면 "손실(이익)"의 괄호가 이익(양수)
        임이 교차 확인된다.

    **"손실"/"이익"의 위치 판정은 반드시 `normalize_account_label`로 공백·개행을
    제거한 라벨에서 한다**(2026-07-21 dart-qa 실측 버그 수정). 회사가 셀 안에
    글자 사이 공백을 넣어("영    업    손    실", "매  출 총 이 익(손실)") 쓰면
    raw 라벨 그대로는 "손실"/"이익" 부분문자열 매칭이 깨져 갈래를 오분류한다 —
    alias 조회는 정규화 라벨로 하면서 부호 판정만 raw로 하던 불일치가 원인이라
    두 로직이 같은 정규화 기준을 쓰도록 일치시킨다.
    """
    if value is None:
        return None
    label = normalize_account_label(raw_label)  # alias 조회와 동일한 공백/개행 제거 기준
    idx_income = label.find("이익")
    idx_loss = label.find("손실")
    if idx_loss == -1:
        return value  # "손실" 없음(순수 이익 등): 원문 부호를 그대로 신뢰한다.
    if idx_income == -1:
        return -value  # 순수 손실 라벨: 부호와 무관하게 항상 반전한다.
    # 조합형: 먼저 나오는 키워드가 주 계정. "이익"이 앞이면 원문 부호가 곧
    # 경제적 부호라 신뢰하고, "손실"이 앞이면 원문 부호가 반대라 반전한다.
    return value if idx_income < idx_loss else -value


def _extract_section(
    table: etree._Element,
    values_cur: dict,
    values_prv: dict,
    aliases: dict[str, str] = ACCOUNT_NAME_ALIASES,
) -> None:
    cur_span, prv_span = _period_spans(table)
    for raw_label, norm_label, value_texts in _row_values(table):
        field = aliases.get(norm_label)
        if field is None or field in values_cur:
            continue  # 미매핑 과목이거나 이미 채워진 표준 필드(첫 매칭 우선)는 건너뜀
        cur_group = value_texts[:cur_span]
        prv_group = value_texts[cur_span : cur_span + prv_span]
        values_cur[field] = _apply_sign(raw_label, _first_amount(cur_group))
        values_prv[field] = _apply_sign(raw_label, _first_amount(prv_group))


# IFRS "(첨부)재무제표" 서식 전용 부호 처리 필드 집합 (2026-07-22, dart-qa 확정).
# 매출원가/판매비와관리비는 "비용 크기"라 정의상 음수가 될 수 없는데, 일부 IFRS
# 포괄손익계산서가 이를 괄호(contra 항목)로 표기해 parse_won_amount가 음수로
# 읽는다 — abs로 정규화해 FINANCE 경로(양수 관행)와 부호를 일치시킨다(ⓐ).
_IFRS_ABS_FIELDS = frozenset({"cogs", "sga"})


def _apply_sign_ifrs(field: str, value: float | None) -> float | None:
    """IFRS "(첨부)재무제표" 경로 전용 부호 처리 (2026-07-22, dart-qa 확정).

    FINANCE 서식과 IFRS 첨부 서식은 부호 규약이 **정반대**다:
    - FINANCE 서식: 손실 행을 양수 크기로 표기 → `_apply_sign`이 반전해 음수로
      저장(괄호=음수면 재반전해 이익으로 해석). 이 규약은 그대로 유지한다
      (test_parsers.py의 FINANCE 회귀가 이를 잠근다).
    - IFRS 첨부 서식: 손익을 자연 부호(괄호=음수)로 그대로 표기해 값에 이미
      경제적 부호가 들어 있다. 여기에 FINANCE식 손실-반전을 적용하면 "영업손실
      (15,641,046,221)"(=-15.6억, 실제 적자)이 +15.6억(흑자)로 뒤집힌다(ⓑ,
      20230322000842 실증 — 매출액 248B < 매출원가 252B인데 흑자로 저장됨).

    따라서 첨부 경로는 `_apply_sign`을 쓰지 않고 원문 부호를 그대로 신뢰한다.
    매출원가/판관비만 비용 크기이므로 abs로 정규화한다(ⓐ). 검증은 호출부의
    회계 항등식(gross_profit==revenue-cogs, total_assets==total_liab+total_equity)
    으로 자체 확인한다.
    """
    if value is None:
        return None
    if field in _IFRS_ABS_FIELDS:
        return abs(value)
    return value


def _attach_section_of(text: str) -> str | None:
    """재무제표 제목 문자열을 섹션 키(bs/is/cf)로 판정. 아니면 None.

    "(첨부)재무제표" 구조에서 각 표의 제목은 "재 무 상 태 표"/"포 괄 손 익 계 산 서"/
    "연 결 현 금 흐 름 표" 처럼 글자 사이 공백 + 연결/포괄 등의 수식어가 붙는다.
    공백을 제거하고 수식어 접두어를 벗긴 뒤 정본 제목으로 시작하는지로 판정한다
    (startswith — 캡션 표는 첫 셀에 "재 무 상 태 표"만, P는 제목만 담는 실측을
    반영. 판정은 항상 in_attach 구간 안에서만 호출돼 주석 본문 오탐이 없다)."""
    c = text.replace(" ", "").replace("　", "").replace("\n", "").replace("\r", "").replace("\t", "")
    for pref in _ATTACH_CAPTION_PREFIXES:
        if c.startswith(pref):
            c = c[len(pref) :]
    if c.startswith(_BS_TITLE_MARK):
        return "bs"
    if c.startswith(_IS_TITLE_MARK):
        return "is"
    if c.startswith(_CF_TITLE_MARK):
        return "cf"
    return None


def _attach_other_statement_title(text: str) -> bool:
    """첨부 구간의 제목이 **파싱 대상이 아닌 다른 재무제표**인지 판정.

    "자 본 변 동 표"/"이익잉여금처분계산서" 등은 bs/is/cf 어느 쪽도 아니라
    `_attach_section_of`가 None을 돌려주는데, 그 표들이 직전 재무제표 구간에
    딸려 들어가면(원문 섹션 열람에서 손익계산서 탭에 자본변동표가 붙는 식)
    화면이 지저분해진다. `detect_caption_while_pending` 소비자만 이 판정으로
    구간을 끊는다 — 값 추출 경로(파이프라인)의 동작에는 관여하지 않는다.
    """
    c = text.replace(" ", "").replace("　", "").replace("\n", "").replace("\r", "").replace("\t", "")
    for pref in _ATTACH_CAPTION_PREFIXES:
        if c.startswith(pref):
            c = c[len(pref) :]
    return any(c.startswith(mark) for mark in _OTHER_TITLE_MARKS)


def _first_cell_text(table: etree._Element) -> str:
    """표 첫 행의 첫 셀 텍스트(캡션 표의 제목 셀 판정용)."""
    tr = table.find(".//TR")
    if tr is None:
        return ""
    cells = list(tr)
    return _text_of(cells[0]) if cells else ""


def _classify_period(text: str) -> str | None:
    """헤더 셀 텍스트가 당기/전기 어느 쪽인지 판정. 아니면(과목/주석 등) None."""
    t = text.replace(" ", "").replace("　", "")
    has_cur = "(당)" in t or "당기" in t
    has_prv = "(전)" in t or "전기" in t
    if has_cur and not has_prv:
        return "cur"
    if has_prv and not has_cur:
        return "prv"
    return None


# 열 제목을 "당기"/"전기"가 아니라 **회계 차수**로만 적는 서식(2026-07-28 실측).
# 예: 재무상태표 THEAD `<TH COLSPAN="2">제6기 기말</TH>` / `<TH COLSPAN="2">제5기
# 기말</TH>`, 손익계산서 `<TH>제 6 기</TH>` / `<TH>제 5 기</TH>` (합천수상태양광
# 20260331004483). 이런 표는 "당기"/"전기" 단서가 헤더에 전혀 없어 `_classify_period`
# 만으로는 열 계획이 서지 않았고, 그 결과 재무상태표/손익계산서를 통째로 못 찾은
# 것으로 처리돼 PARTIAL로 떨어졌다(같은 문서의 주석 표에는 "당기"/"전기"가
# 정상적으로 쓰인다 — 본문 3표만 차수 표기를 쓰는 서식이다).
#
# 로컬 문서 캐시 8,748건 전수 스캔 실측: "(첨부)재무제표" 서식 462건 중 **63건**
# (표 기준 184개)이 이 표기를 쓴다. 184개 표 전부 큰 차수(당기)가 먼저 나오는
# 내림차순이었고 오름차순/동일 차수는 0건이었다. 그럼에도 판정은 **차수 숫자 크기가
# 아니라 등장 순서**로 한다 — 숫자 크기로 판정하면 서식이나 회계연도 표기 방식이
# 달라질 때 옳고 그름이 조용히 뒤집힐 수 있기 때문이다(첫 번째 차수 열=당기,
# 두 번째=전기, 그 뒤 열은 무시).
#
# "제 9(당) 기말" / "제 8(당) 기말"처럼 **전기 열에도 "(당)"이 잘못 붙은** 원문
# 3건(20240329003157/20250331003688/20250409002265)도 이 폴백이 흡수한다 —
# 명시 표기만 보면 두 열 모두 당기가 돼 열 계획이 서지 않던 사례다.
_ORDINAL_PERIOD_RE = re.compile(r"제\d+(?:\([당전]\))?기")
_ORDINAL_COMPACT = str.maketrans({" ": None, "　": None, "\n": None, "\r": None, "\t": None})


def _is_ordinal_period(text: str) -> bool:
    """헤더 셀 텍스트가 "제N기"/"제N기 기말" 같은 차수 기반 기간 제목인지 판정."""
    return bool(_ORDINAL_PERIOD_RE.search(text.translate(_ORDINAL_COMPACT)))


def _build_period_column_plan(header_cells: list[etree._Element]) -> tuple[list[int], list[int]] | None:
    """헤더 행의 셀들을 COLSPAN을 누적하며 순회해 당기/전기 열 인덱스를 계산.

    "(첨부)재무제표" 데이터 표는 "과목 | 주석 | 당기 | 전기" 구조라 값 사이에
    "주석" 열이 끼어 있고, 당기/전기가 각각 상세/합계 2열(COLSPAN=2)일 수도
    있다. 헤더 셀의 "(당)"/"(전)" 표기로 각 열을 분류해 라벨/주석 열은 건너뛰고
    당기·전기 값 열의 (본문 셀) 인덱스 목록만 돌려준다.

    명시 표기로 당기·전기를 **둘 다** 잡지 못했을 때만, 차수 기반 표기
    ("제6기 기말"/"제 6 기", `_ORDINAL_PERIOD_RE` 주석 참고)로 등장 순서에 따라
    첫 번째 차수 열=당기 / 두 번째=전기로 폴백한다. 명시 표기가 성립하는 기존
    표들은 이 폴백에 도달하지 않아 동작이 그대로다.

    둘 다 끝내 찾지 못하면 None(이 표는 재무제표 데이터 표가 아님)."""
    col = 0
    cur: list[int] = []
    prv: list[int] = []
    ordinals: list[tuple[int, int]] = []  # 차수 기반 헤더 셀의 (시작 열, 열 수)
    for cell in header_cells:
        raw_span = cell.get("COLSPAN", "1") or "1"
        try:
            span = int(raw_span)
        except ValueError:
            span = 1
        text = _text_of(cell)
        role = _classify_period(text)
        for c in range(col, col + span):
            if role == "cur":
                cur.append(c)
            elif role == "prv":
                prv.append(c)
        if _is_ordinal_period(text):
            ordinals.append((col, span))
        col += span
    if cur and prv:
        return cur, prv
    if len(ordinals) >= 2:
        (cur_col, cur_span), (prv_col, prv_span) = ordinals[0], ordinals[1]
        return (
            list(range(cur_col, cur_col + cur_span)),
            list(range(prv_col, prv_col + prv_span)),
        )
    return None


def attach_period_plan(
    table: etree._Element,
) -> tuple[tuple[list[int], list[int]], list[etree._Element]] | None:
    """"(첨부)재무제표" 데이터 표의 (당기/전기 열 계획, 본문 행 목록)을 판별.

    헤더(당기/전기 열 정의)는 THEAD에 있을 수도, 첫 TBODY 행에 있을 수도 있다
    (하이에어 현금흐름표는 THEAD가 없고 첫 본문 행이 헤더다). 두 경우를 모두
    시도하고, 당기·전기 열을 끝내 찾지 못하면 None(= 재무제표 데이터 표가 아님).

    `_extract_attach_section`(파이프라인 값 추출)과 `account_detail.py`(세부계정
    펼치기)가 **같은 열 판정 기준**을 쓰도록 공용 함수로 분리해 둔 것이다 —
    두 곳이 따로 열 계획을 세우면 화면과 저장값이 갈라진다(2026-07-27).
    """
    tbody = table.find("TBODY")
    rows = tbody.findall("TR") if tbody is not None else []
    plan: tuple[list[int], list[int]] | None = None
    data_rows = rows

    thead = table.find("THEAD")
    if thead is not None:
        plan = _build_period_column_plan(thead.findall(".//TH"))
    if plan is None:
        for idx, tr in enumerate(rows):
            cand = _build_period_column_plan(list(tr))
            if cand is not None:
                plan = cand
                data_rows = rows[idx + 1 :]
                break
    if plan is None:
        return None
    return plan, data_rows


# 첨부 서식 라벨 셀의 들여쓰기 계산 시 무시할 문자(셀 안 줄바꿈은 서식상 자동
# 개행일 뿐 계층 정보가 아니다 — 하이에어 20240329000968 실측: 같은 계층의 셀이
# "\n 매출채권\n"과 " 단기금융상품"으로 뒤섞여 있다).
_ATTACH_INDENT_IGNORED = str.maketrans({"\n": None, "\r": None, "\t": None})


def attach_label_indent(cell: etree._Element) -> int:
    """첨부 서식 라벨 셀의 선행 공백 수(계정 계층의 유일한 단서).

    FINANCE 서식은 셀에 `ALEVEL` 속성으로 계층을 명시하지만, "(첨부)재무제표"
    서식의 데이터 표는 평범한 `<TD>`라 ALEVEL이 아예 없다(실측 4건 + 이래CS
    20260401004343). 대신 대분류는 왼쪽 끝에서 시작하고("Ⅰ.유동자산") 하위
    계정은 선행 공백으로 들여쓴다("    1.현금및현금성자산") — 공백 폭 자체는
    회사마다 1~4칸으로 제각각이라 절대값이 아니라 **표 안에서의 상대 순위**로
    계층을 매긴다(account_detail.py `_attach_levels`).
    """
    text = "".join(cell.itertext()).translate(_ATTACH_INDENT_IGNORED)
    return len(text) - len(text.lstrip(" 　"))


def _extract_attach_section(
    table: etree._Element,
    values_cur: dict,
    values_prv: dict,
    aliases: dict[str, str] = ACCOUNT_NAME_ALIASES,
) -> None:
    """"(첨부)재무제표" 구조의 NORMAL 데이터 표 1개를 파싱해 표준 필드를 채운다.

    열 계획/본문 행 판별은 `attach_period_plan`이 담당하고, 여기서는 본문 행에서
    과목명→값을 추출한다. 값 선택(_first_amount)은 기존 경로와 공유하고, 부호는
    첨부 서식 전용 `_apply_sign_ifrs`를 쓴다(FINANCE 서식과 규약이 반대)."""
    planned = attach_period_plan(table)
    if planned is None:
        return
    (cur_cols, prv_cols), data_rows = planned
    for tr in data_rows:
        cells = list(tr)
        if not cells:
            continue
        label = _text_of(cells[0])
        if not label:
            continue
        field = aliases.get(normalize_account_label(label))
        # 이미 값(비-None)이 채워진 필드는 첫 매칭 우선으로 건너뛴다. 단 값이 아직
        # None(빈 매칭으로만 기록됐거나 미기록)이면 뒤 행이 채울 수 있게 막지 않는다 —
        # IFRS 연결 재무상태표는 "II.유동자산"(값 없는 섹션 헤더)이 "유동자산합계"
        # (실제 값)보다 먼저 나와, 헤더가 필드를 None으로 잠그면 소계 값을 영영 못
        # 채운다(씨이케이 20260330001497). FINANCE 경로(_extract_section)는 무변경.
        if field is None or values_cur.get(field) is not None:
            continue
        cur_texts = [_text_of(cells[c]) for c in cur_cols if c < len(cells)]
        prv_texts = [_text_of(cells[c]) for c in prv_cols if c < len(cells)]
        # IFRS 첨부 서식은 자연 부호 규약이라 FINANCE식 _apply_sign이 아니라
        # 첨부 전용 _apply_sign_ifrs를 쓴다(위 함수 독스트링 참고).
        cur_val = _apply_sign_ifrs(field, _first_amount(cur_texts))
        prv_val = _apply_sign_ifrs(field, _first_amount(prv_texts))
        # 당기·전기가 모두 빈 값이면 기록하지 않는다(필드를 None으로 잠그지 않고
        # 뒤따르는 소계 행이 채울 여지를 남긴다). 한쪽이라도 값이 있으면 기록한다.
        if cur_val is None and prv_val is None:
            continue
        values_cur[field] = cur_val
        values_prv[field] = prv_val


def walk_statement_tables(
    root: etree._Element,
    visit: Callable[[str, str, etree._Element], bool],
    *,
    on_section_title: Callable[[str, etree._Element], None] | None = None,
    detect_caption_while_pending: bool = False,
) -> None:
    """원문 트리에서 재무제표 데이터 표 후보를 문서 순서대로 찾아 `visit`에 넘긴다.

    **서식 인식 기준을 한 곳에 모아 두기 위한 공용 워커**다(2026-07-27 신설).
    파이프라인 파서(`parse_xml_financials`)·세부계정 펼치기(`account_detail.py`)·
    원문 섹션 열람(`document_sections.py`)이 모두 이 함수를 통해 섹션을 잡는다 —
    각자 TITLE 상태기계를 재구현하면 "(첨부)재무제표" 같은 서식 확장이 한쪽에만
    반영돼 화면과 저장값이 갈라진다(실제로 그렇게 갈라져 있던 것을 고친 것이다).

    두 서식을 모두 인식한다:
    - FINANCE 서식: `<TITLE>`("재무상태표"/"손익계산서"/"현금흐름표") 구간의
      `ACLASS="FINANCE"` 표.
    - "(첨부)재무제표" 서식: `_ATTACH_TITLE_MARK` TITLE과 다음 TITLE 사이
      (`in_attach`)에서만 `<P>`/캡션 표의 제목으로 섹션을 잡고, 그 뒤 표를
      데이터 표 후보로 넘긴다(주석 본문의 "손익계산서" 언급 오탐 차단).

    `visit(fmt, section, table)` — `fmt`는 "finance"/"attach", `section`은
    "bs"/"is"/"cf". 반환값 True는 "이 표를 그 섹션의 본문 표로 소비했다"는 뜻이라
    섹션 추적을 리셋한다(같은 섹션의 뒤따르는 표는 더 이상 넘기지 않는다).
    False면 섹션을 유지한 채 다음 표를 계속 넘긴다 — 첨부 서식의 기간/각주 표를
    건너뛰고 진짜 데이터 표에 도달하기 위한 기존 규칙이다.

    `on_section_title(section, element)`는 첨부 서식에서 재무제표 제목(<P> 또는
    캡션 표)을 감지했을 때 호출된다(원문 섹션 열람이 제목을 렌더링하는 데 쓴다).
    `detect_caption_while_pending=True`면 섹션이 이미 대기 중이어도 제목(<P>/캡션
    표)을 다시 확인해 섹션을 전환한다 — 표를 소비하지 않고 구간 전체를 훑는
    소비자(원문 섹션 열람)를 위한 opt-in이며, 기본값은 기존 동작(대기 중에는
    제목을 확인하지 않음) 그대로다.
    """
    section: str | None = None
    in_attach = False
    attach_section: str | None = None

    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str):
            continue
        local_tag = tag.rsplit("}", 1)[-1]

        if local_tag == "TITLE":
            compact = _text_of(el).replace(" ", "").replace("　", "")
            if _BS_TITLE_MARK in compact:
                section = "bs"
                in_attach = False
            elif _IS_TITLE_MARK in compact:
                section = "is"
                in_attach = False
            elif _CF_TITLE_MARK in compact:
                section = "cf"
                in_attach = False
            elif _ATTACH_TITLE_MARK in compact and "주석" not in compact:
                # "(첨부)재무제표"/"(첨부)연결재무제표" 첨부문서 시작 — 이 구간의
                # 표는 FINANCE가 아니라 NORMAL이라 별도 경로로 처리한다.
                in_attach = True
                section = None
                attach_section = None
            elif any(mark in compact for mark in _OTHER_TITLE_MARKS):
                section = None
                in_attach = False
            else:
                in_attach = False
            continue

        # "(첨부)재무제표" 구간에서 각 재무제표의 제목이 독립 <P>로 나오는 서식
        # (롯데미쓰이 등). 기본은 대기 중 섹션이 없을 때만 감지한다(기존 동작).
        if in_attach and local_tag == "P" and (attach_section is None or detect_caption_while_pending):
            text = _text_of(el)
            sec = _attach_section_of(text)
            if sec is not None:
                attach_section = sec
                if on_section_title is not None:
                    on_section_title(sec, el)
            elif detect_caption_while_pending and _attach_other_statement_title(text):
                attach_section = None  # 자본변동표 등 다른 재무제표에서 구간을 끊는다
            continue

        if local_tag != "TABLE":
            continue

        if section in ("bs", "is", "cf") and el.get("ACLASS") == "FINANCE":
            if visit("finance", section, el):
                section = None  # 구간당 첫 FINANCE 테이블만 사용
            continue

        if not in_attach:
            continue

        # "(첨부)재무제표" 구간의 표. 대기 섹션이 없으면 캡션 표(제목 셀)로
        # 보고 섹션을 잡고, 대기 섹션이 있으면 데이터 표로 보고 넘긴다.
        if attach_section is None or detect_caption_while_pending:
            caption = _first_cell_text(el)
            sec = _attach_section_of(caption)
            if sec is not None:
                attach_section = sec
                if on_section_title is not None:
                    on_section_title(sec, el)
                continue
            if detect_caption_while_pending and _attach_other_statement_title(caption):
                attach_section = None  # 자본변동표 등 다른 재무제표에서 구간을 끊는다
                continue
            if attach_section is None:
                continue
        if visit("attach", attach_section, el):
            attach_section = None


def parse_xml_financials(raw_xml: bytes) -> ParsedFinancials:
    """감사보고서 원문 XML에서 재무상태표/손익계산서를 파싱해 표준 13항목을 채운다."""
    # 실측 샘플 30건 중 다수가 본문 텍스트에 "<"/"&"를 이스케이프하지 않은 채
    # 담고 있어(예: 서술형 문장 속 부등호, 앰퍼샌드) 엄격 모드로는 파싱 자체가
    # 실패한다. recover=True로 손상된 부분만 건너뛰고 나머지 구조는 그대로
    # 활용한다 — DART 원문은 표 구조가 앞부분(재무상태표/손익계산서)에 있고
    # 깨지는 지점은 대개 뒤쪽 서술형 주석이라 실사용에 지장이 없다.
    # 인코딩을 UTF-8로 먼저 정규화한다(EUC-KR/CP949 원문 복구, _decode_raw_xml 참고).
    raw_xml = _decode_raw_xml(raw_xml)
    # 한글 유령 태그("<주석3>" 등)를 제거해 recover 파서가 표 구조를 통째로
    # 붕괴시키는 것을 막는다(_HANGUL_PSEUDO_TAG_RE 주석 참고). _decode_raw_xml이
    # UTF-8 bytes를 보장하므로 안전하게 디코딩→치환→재인코딩한다.
    raw_xml = _HANGUL_PSEUDO_TAG_RE.sub("", raw_xml.decode("utf-8")).encode("utf-8")
    parser = etree.XMLParser(recover=True)
    root = etree.fromstring(raw_xml, parser=parser)
    if root is None:
        logger.warning("XML 파싱 실패(복구 불가): %s", parser.error_log)
        return ParsedFinancials(parse_status="FAILED", parse_note=f"XML 구문 오류(복구 불가): {parser.error_log}")

    values_cur: dict[str, float | None] = {}
    values_prv: dict[str, float | None] = {}
    found_any_table = False

    def visit(fmt: str, sec: str, table: etree._Element) -> bool:
        """워커가 넘긴 표 1개를 추출한다(반환값=소비 여부, 워커 독스트링 참고)."""
        nonlocal found_any_table
        if fmt == "finance":
            if sec in ("bs", "is"):
                found_any_table = True
                _extract_section(table, values_cur, values_prv)
            else:
                # 현금흐름표 4항목(best-effort). found_any_table에는 반영하지 않아
                # parse_status 판정에 영향을 주지 않는다(§4-8 확정).
                _extract_section(table, values_cur, values_prv, CF_ACCOUNT_NAME_ALIASES)
            return True  # 구간당 첫 FINANCE 테이블만 사용

        attach_aliases = CF_ACCOUNT_NAME_ALIASES if sec == "cf" else ACCOUNT_NAME_ALIASES
        before = len(values_cur)
        _extract_attach_section(table, values_cur, values_prv, attach_aliases)
        if len(values_cur) == before:
            # 캡션/기간/각주 표는 매칭이 0이라 소비하지 않고 대기 섹션을 유지한다.
            return False
        # CF는 §4-8 규칙대로 found_any_table에 반영하지 않는다.
        if sec in ("bs", "is"):
            found_any_table = True
        return True

    walk_statement_tables(root, visit)

    status, note = determine_parse_status(values_cur, values_prv, found_any_table=found_any_table)

    # 현금흐름표 미확보는 parse_status를 바꾸지 않고 parse_note에만 부기한다(§4-8).
    # 재무제표 자체가 미첨부(found_any_table=False)인 경우는 이미 그 안내가
    # note에 있으므로 CF 부기를 생략한다(중복 방지).
    if found_any_table and values_cur.get("cf_operating") is None:
        cf_note = "현금흐름표 미확보(best-effort)"
        note = f"{note} / {cf_note}" if note else cf_note

    return ParsedFinancials(
        values_cur=values_cur, values_prv=values_prv, parse_status=status, parse_note=note
    )
