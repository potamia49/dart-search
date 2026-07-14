"""감사의견(적정/한정/부적정/의견거절) 추출.

상세개발계획.md §4-4. 실측 샘플(backend/tests/fixtures, 30건)에서 확인한
DART 감사보고서 서식 규칙:
- 비적정 의견(한정/부적정/의견거절)은 감사의견 문단 바로 앞에 굵은 글씨
  단독 문단으로 의견 종류가 그대로 적혀 있다(예: `<P USERMARK="B">한정의견</P>`,
  `<P USERMARK="B">의견거절</P>`). 실측 25건 중 10건이 "의견거절", 2건이
  "한정의견"이었다.
- 적정(무한정) 의견은 이런 표시가 없고 곧바로 `<SPAN USERMARK="B">감사의견</SPAN>`
  뒤에 "...중요성의 관점에서 공정하게 표시하고 있습니다." 문장이 온다
  (실측 13건 전부 동일 문구, 부정어 없음).
- 한정의견은 "...(사유)...를 제외하고는...공정하게 표시하고 있습니다"처럼
  "제외하고는"이 공정 표시 문장 앞에 낀다(홈마리나속초호텔 실측 확인).
- 부적정/의견거절은 이 저장소가 실제로 마주친 표본에는 "부적정"이 없었으나
  DART 표준 서식(회계감사기준 문안)상 "공정하게 표시하고 있지 않습니다"
  (부적정) / "의견을 표명하지 않습니다"(의견거절)로 고정 문구라 안전하게
  포함해 둔다.
- 2012년 원문(구서식)은 적정의견 문구 자체가 다르다 — "공정하게"가 아니라
  "**적정**하게 표시하고 있습니다"로 "적정"이라는 단어가 그대로 들어간다
  (2014년 전후 감사기준 개정으로 "공정하게" 문구로 바뀐 것으로 보인다).
  신서식(공정하게)과 구서식(적정하게)을 모두 처리한다.
"""

from __future__ import annotations

import re

AUDIT_OPINION_VALUES: tuple[str, ...] = ("적정", "한정", "부적정", "의견거절")

_TAG_RE = re.compile(r"<[^>]+>")
# 실측상 의견 종류 마커/문장은 원문 앞부분(감사보고서 커버+의견 문단)에 있다.
# 뒤쪽 주석(수만 자)까지 스캔하면 우연히 일치하는 문구를 오탐할 수 있어 범위를 제한한다.
_SEARCH_WINDOW = 4000

# 실측 결과 같은 문구도 회사/서식마다 띄어쓰기가 제각각이었다(예: "표시하고
# 있습니다" vs "표시하고있습니다"). 핵심 어절 사이에 \s*를 둬 띄어쓰기 변형을
# 흡수한다.
_DISCLAIMER_RE = re.compile(r"의견을\s*표명하지\s*않습니다")
_ADVERSE_RE = re.compile(r"(?:공정|적정)하게\s*표시하고\s*있지\s*않습니다")
_QUALIFIED_MARK_RE = re.compile(r"한정\s*의견")
_QUALIFIED_FAIR_RE = re.compile(r"제외하고는[\s\S]{0,120}?(?:공정|적정)하게\s*표시하고\s*있습니다")
# "공정하게"(신서식) / "적정하게"(2014년 이전 구서식) 둘 다 무한정(적정) 의견 문구다.
_UNQUALIFIED_FAIR_RE = re.compile(r"(?:공정|적정)하게\s*표시하고\s*있습니다")


def _plain_text(raw_text: str) -> str:
    return _TAG_RE.sub(" ", raw_text or "")


def extract_audit_opinion(raw_text: str) -> str | None:
    """감사보고서 원문(XML/PDF에서 추출한 텍스트)에서 감사의견을 판정.

    판정 우선순위(모두 원문 앞부분 `_SEARCH_WINDOW`자 이내에서 탐색):
    1. 의견거절: "의견거절" 마커 또는 "의견을 표명하지 않습니다"
    2. 부적정: "부적정" 마커 또는 "공정하게 표시하고 있지 않습니다"
    3. 한정: "한정의견" 마커 또는 "...제외하고는...공정하게 표시하고 있습니다"
    4. 적정: "공정하게 표시하고 있습니다" (위 조건에 해당 안 될 때)
    판정 불가 시 None (results.parse_note에서 확인 가능하도록 호출부가 기록).
    """
    text = _plain_text(raw_text)[:_SEARCH_WINDOW]

    if "의견거절" in text or _DISCLAIMER_RE.search(text):
        return "의견거절"
    if "부적정" in text or _ADVERSE_RE.search(text):
        return "부적정"
    if _QUALIFIED_MARK_RE.search(text) or _QUALIFIED_FAIR_RE.search(text):
        return "한정"
    if _UNQUALIFIED_FAIR_RE.search(text):
        return "적정"
    return None
