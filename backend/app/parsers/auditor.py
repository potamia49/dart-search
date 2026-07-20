"""감사인(회계법인/감사반) 이름·주소 추출 (2026-07-20 추가).

결과 목록에 "안경회계법인(경상남도 창원시)"처럼 **누가 감사했는지**를 보여주기
위한 파서다. 감사의견(`audit_opinion.py`)과 마찬가지로 이미 내려받은 원문만
읽으므로 추가 API 호출/쿼터가 0건이다.

`backend/tests/fixtures` 31건 실측으로 확인한 서식 규칙:

1. **표지(커버)**: 기수/회계기간 표기 바로 뒤, "목 차" 앞에 감사인 이름이
   단독 줄로 온다(31/31 전부 존재). 여기에는 주소가 없다.
2. **서명란**: 감사보고서 본문 맨 끝, "이 감사보고서는 감사보고서일 ...
   유효한 것입니다" 문장 바로 앞에 `주소` → `감사인 이름` → `대표이사 ○○○`
   순으로 온다(31건 중 28건). 나머지 3건은 본문 서명란이 원문 XML에 아예
   없어(재무제표만 첨부된 서식) 표지 이름만 확보된다.
3. **이름은 글자 사이가 벌어져 있는 경우가 많다** — "삼 일 회 계 법 인",
   "서 일 회 계 법 인". `normalize_account_label()`이 계정과목에서 같은 문제를
   흡수하는 것과 동일하게, 공백을 모두 제거해 "삼일회계법인"으로 정규화한다.
4. **감사반은 "○○공인회계사감사반(제267호)" 형태**다 — "공인회계사"가 이름의
   일부라 이 토큰에서 잘라내면 안 된다(접미어 "감사반"까지 포함해야 완전한
   이름이 된다). 등록번호 괄호는 표시에 불필요해 제거한다.
5. **직전 감사인이 본문에 언급된다** — "기타사항" 문단에 "...는 성문회계법인이
   대한민국의 회계감사기준에 따라 감사하였으며..."처럼 **전기** 감사인이
   등장한다(실측 2건). 이걸 현재 감사인으로 오인하면 안 되므로 (a) 후보를
   짧은 줄(`_MAX_NAME_LINE_LEN`자 이하 = 단독 표기)로 제한하고 (b) 접미어
   뒤에 한글이 이어지면("회계법인이") 제외한다 — 서술 문장은 둘 다 걸러진다.

로컬 문서 캐시 250건 무작위 표본(2026-07-20) 기준 이름 248건(99%)·주소 199건
(80%)을 확보했다. 주소가 없는 건은 대부분 서명란이 원문에 없는 서식이고,
이름이 "공인회계사감사반"으로만 나오는 1건은 **원문 자체에 상호 없이
"제162호 공인회계사감사반"으로만 적혀 있는** 경우다(파서 한계가 아니다).

주소는 `app.core.filters.SIDO_ALIASES`를 그대로 재사용해 판별하고, 첫 토큰을
표준 시도명으로 정규화해 저장한다("서울시 서초구 ..." → "서울특별시 서초구
..."). 화면이 앞 두 토큰만 잘라 "(시도 시군구)"로 표시하므로, 정규화를 여기서
한 번 해두면 프론트가 시도 약칭 표를 따로 들고 있을 필요가 없다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.filters import SIDO_ALIASES, normalize_sido
from app.parsers.xml_parser import _decode_raw_xml

# 감사인 이름 접미어. 글자 사이 공백("감 사 반")을 흡수하고, 뒤에 한글이 이어지면
# 서술 문장("...회계법인이 감사하였으며") 이므로 후보에서 제외한다.
_SUFFIX_RE = re.compile(r"(?:회\s*계\s*법\s*인|감\s*사\s*반)(?![가-힣])")

# 이름 줄에 함께 오는 서명자 표기 — 접미어 **뒤**에서만 잘라낸다("공인회계사"가
# 이름 앞에 붙는 감사반 형태를 잘라먹지 않기 위함, 위 규칙 4).
_SIGNER_RE = re.compile(r"(대표\s*이사|대표자|공인\s*회계사|사원)")

# 단독 표기 줄만 후보로 삼기 위한 길이 상한(위 규칙 5). 실측 최장 후보는
# "천일공인회계사 감사반(제267호)"(21자)이라 넉넉하다.
_MAX_NAME_LINE_LEN = 40

# "회계담당 임원, 감사 및 회계법인 담당이사"처럼 감사인 이름이 아닌데 접미어를
# 포함하는 표 항목(실측 캐시 3건). 이 단어가 있으면 후보에서 제외한다.
_NOT_AUDITOR_LINE_RE = re.compile(r"담당|임원|위원회")

# 서명란에서 이름 줄 위로 주소를 찾을 때 거슬러 올라갈 최대 줄 수.
_ADDRESS_LOOKBACK_LINES = 6

_HANGUL_RE = re.compile(r"[가-힣]")
_TAG_RE = re.compile(r"<[^>]+>")

_ALL_SIDO_ALIASES = sorted(
    {alias for aliases in SIDO_ALIASES.values() for alias in aliases},
    key=len,
    reverse=True,
)
_SIDO_ALTERNATION = "|".join(re.escape(a) for a in _ALL_SIDO_ALIASES)
# "서울특별시 용산구 ..."처럼 시도로 시작하는 주소 줄.
_ADDRESS_LINE_RE = re.compile(rf"^(?:{_SIDO_ALTERNATION})\s")
# 시도명만 단독으로 한 줄인 경우(실측 1건: "서울특별시" / "강남구 영동대로 ...").
_SIDO_ONLY_RE = re.compile(rf"^(?:{_SIDO_ALTERNATION})$")


@dataclass(frozen=True)
class AuditorInfo:
    """감사인 이름과 사무소 주소(둘 다 확보 못 할 수 있다)."""

    name: str | None = None
    address: str | None = None


def _to_lines(raw_xml: bytes) -> list[str]:
    """원문 XML을 태그 경계 기준의 텍스트 줄 목록으로 변환한다.

    서명란은 주소/이름/서명자가 각각 별도 `<P>`에 담겨 있어, 태그를 공백이
    아니라 **줄바꿈**으로 치환해야 "주소 줄 바로 아래가 이름 줄"이라는 구조를
    쓸 수 있다(태그를 공백으로 뭉개면 본문 문장과 구분되지 않는다).
    """
    text = _decode_raw_xml(raw_xml).decode("utf-8", errors="replace")
    text = _TAG_RE.sub("\n", text).replace("&cr;", "\n")
    lines = []
    for raw_line in text.split("\n"):
        line = re.sub(r"[ \t\xa0　]+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return lines


def _leading_hangul_run(text: str) -> str:
    """문자열 앞쪽의 한글(+공백) 연속 구간만 잘라낸다."""
    end = 0
    while end < len(text) and (text[end].isspace() or _HANGUL_RE.match(text[end])):
        end += 1
    return text[:end]


def _clean_name(line: str, match: re.Match[str]) -> str | None:
    """이름 줄에서 감사인 이름만 잘라낸다.

    이름은 접미어 앞("삼일회계법인")뿐 아니라 **뒤**에 오기도 한다
    ("회계법인 원지", "회계법인 보명 대표이사 이 만 호") — 로컬 문서 캐시
    250건 표본에서 12%가 후자 서식이었다. 양쪽을 모두 이어붙이되, 접미어 뒤는
    서명자 표기(`_SIGNER_RE`)에서 끊는다.
    """
    head = line[: match.end()]

    # 접미어 앞의 한글(+공백) 연속 구간이 이름이다. 주소가 같은 줄에 붙어 있는
    # 서식이면 숫자/괄호에서 자연히 끊긴다("...325(대치동, 9층) 다산회계법인").
    start = match.start()
    while start > 0 and (head[start - 1].isspace() or _HANGUL_RE.match(head[start - 1])):
        start -= 1
    name_part = head[start:]

    # 주소가 "302호"처럼 한글 단위로 끝나면 그 한 글자가 앞에 딸려온다 — 앞
    # 글자가 숫자였을 때만 떼어낸다(정상 이름을 깎지 않기 위한 좁은 조건).
    tokens = name_part.split()
    if start > 0 and head[start - 1].isdigit() and len(tokens) > 1 and len(tokens[0]) == 1:
        name_part = " ".join(tokens[1:])

    name = re.sub(r"\s+", "", name_part)

    # 접미어 뒤에 이름이 오는 서식("회계법인 원지") — 서명자 표기 전까지만 취한다.
    tail = line[match.end() :]
    signer = _SIGNER_RE.search(tail)
    if signer:
        tail = tail[: signer.start()]
    tail_name = re.sub(r"\s+", "", _leading_hangul_run(tail))
    if tail_name:
        # "회계법인원지"로 붙이지 않고 원문 표기대로 띄어 쓴다.
        name = f"{name} {tail_name}"

    # 등록번호 괄호("(제267호)")는 표시에 불필요하다.
    name = re.sub(r"\(.*$", "", name).strip()
    return name or None


def _find_address(lines: list[str], index: int, name_start_text: str) -> str | None:
    """이름 줄(`index`) 기준으로 주소를 찾는다 — 같은 줄 앞부분 → 위쪽 줄 순."""
    if _ADDRESS_LINE_RE.match(name_start_text):
        return _normalize_address(name_start_text)

    for j in range(index - 1, max(-1, index - 1 - _ADDRESS_LOOKBACK_LINES), -1):
        line = lines[j]
        if _ADDRESS_LINE_RE.match(line):
            return _normalize_address(line)
        # 시도명만 단독 줄이면 다음 줄과 합쳐야 온전한 주소가 된다.
        if _SIDO_ONLY_RE.match(line) and j + 1 < len(lines):
            return _normalize_address(f"{line} {lines[j + 1]}")
    return None


def _normalize_address(address: str) -> str:
    """주소 첫 토큰(시도)을 표준 시도명으로 정규화한다 ("서울시 ..." → "서울특별시 ...")."""
    tokens = address.split()
    if not tokens:
        return address
    standard = normalize_sido(tokens[0])
    if standard:
        tokens[0] = standard
    return " ".join(tokens)


def extract_auditor(raw_xml: bytes) -> AuditorInfo:
    """감사보고서 원문 XML에서 감사인 이름과 사무소 주소를 추출한다.

    후보는 "회계법인"/"감사반"으로 끝나는 짧은 단독 줄이며, 그 중 **주소를 함께
    확보한 마지막 후보**(= 본문 끝 서명란)를 우선 채택한다. 서명란이 없는 원문은
    표지의 첫 후보(이름만)를 쓴다. 아무 후보도 없으면 빈 `AuditorInfo`를 반환한다
    — 감사보고서가 첨부되지 않은 원문(의견거절 등)에서 정상적으로 발생한다.
    """
    lines = _to_lines(raw_xml)

    first: AuditorInfo | None = None
    last_with_address: AuditorInfo | None = None

    for index, line in enumerate(lines):
        if len(line) > _MAX_NAME_LINE_LEN or _NOT_AUDITOR_LINE_RE.search(line):
            continue
        match = _SUFFIX_RE.search(line)
        if match is None:
            continue

        name = _clean_name(line, match)
        if not name:
            continue
        address = _find_address(lines, index, line[: match.start()].strip())

        info = AuditorInfo(name=name, address=address)
        if first is None:
            first = info
        if address:
            last_with_address = info

    return last_with_address or first or AuditorInfo()


def format_auditor(name: str | None, address: str | None) -> str | None:
    """표시용 문자열 — "안경회계법인(경상남도 창원시)". 주소를 모르면 이름만."""
    if not name:
        return None
    tokens = (address or "").split()
    region = " ".join(tokens[:2])
    return f"{name}({region})" if region else name
