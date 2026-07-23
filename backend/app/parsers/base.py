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
- "영업손실"/"매출총손실"/"당기순손실"처럼 **손실만** 명시된(=="이익"이 없는)
  행은 원문 부호와 무관하게 항상 반전해 저장한다(2026-07-20 수정) — 대부분
  금액이 양수로 찍혀 있어(부호 없음) 뒤집으면 음수(손실)가 되지만, 드물게
  이미 괄호로 음수 표기된 "손실" 행은 "음의 손실 = 이익"이라는 뜻이라 다시
  뒤집어 양수(이익)로 저장한다. 반면 "영업이익(손실)"처럼 흑자·적자 공용으로
  쓰는 **조합형** 라벨은 원문 부호가 이미 정확히 반영돼 있어 그대로 신뢰한다
  (뒤집지 않는다) — 두 갈래 판정 근거는 xml_parser.py의 `_apply_sign` 참고.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol


# PRD 3-2절 표준 13항목 (당기/전기 각각) — results 테이블 컬럼과 1:1 대응.
# "gross_profit"(매출총이익, 손실이면 음수)은 원문의 "매출총이익"/"매출총손실"
# 행을 다른 항목과 동일하게 직접 파싱한다(2026-07-20 변경 — 이전에는 매출액/
# 매출원가로 계산한 매출총이익율(%)을 저장했었다, ACCOUNT_NAME_ALIASES 참고).
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
    "gross_profit",
    "sga",
    "operating_income",
    "net_income",
)

# xml_parser.py가 원문에서 직접 채우는 필드 — 이제 전부 표준 13항목과 같다
# (계산 항목이었던 gross_margin을 없앤 뒤로는 별도로 뺄 필드가 없다).
DIRECT_FINANCIAL_FIELDS: tuple[str, ...] = STANDARD_FINANCIAL_FIELDS

# 현금흐름표 4항목 (§4-8, 2026-07-19). 위 13항목과 달리 best-effort 항목이며
# `determine_parse_status()` 판정에는 절대 포함하지 않는다 — CF 누락으로 기존
# OK 건이 PARTIAL로 재분류되면 이미 완료된 Job의 검수 기준과 충돌하기 때문
# (설계 확정: CF 미확보는 parse_note에만 부기). "현금의 증가(감소)"는 세 활동의
# 합으로 파생 가능해 저장하지 않고, "기초의 현금"은 전기 cf_ending_cash와
# 중복이라 제외한다.
CF_FINANCIAL_FIELDS: tuple[str, ...] = (
    "cf_operating",    # 영업활동현금흐름
    "cf_investing",    # 투자활동현금흐름
    "cf_financing",    # 재무활동현금흐름
    "cf_ending_cash",  # 기말의 현금
)

# 영업외수익/영업외비용 2항목 (2026-07-22). CF_FINANCIAL_FIELDS와 완전히 동형인
# best-effort 항목이다 — 표준 13항목(STANDARD/DIRECT_FINANCIAL_FIELDS)에 절대
# 넣지 않으며 `determine_parse_status()` 판정에도 관여하지 않는다(결측이어도
# PARTIAL/FAILED로 떨어지지 않음). 이유는 CF와 동일하다: 이미 OK로 완료된 Job이
# 새 필드 결측으로 재분류되면 검수 기준이 깨진다. 손익계산서 세부계정 펼치기에서
# "영업외수익"/"영업외비용" 대분류(L0)와 그 하위 세부계정(이자수익/외환차익 등)이
# 유실되던 것을 복구하기 위해 신설했다. 순수 수익/비용 항목이라 "이익(손실)"
# 조합형 부호 반전 대상이 아니고, 실측(로컬 캐시 4,922건 전수 스캔)상 FINANCE
# 서식은 둘 다 양수 크기로 표기하므로 원문 부호를 그대로 신뢰한다(영업외수익
# 4,529건 양수/1건 음수, 영업외비용 4,531건 전부 양수).
NON_OPERATING_FINANCIAL_FIELDS: tuple[str, ...] = (
    "non_operating_income",   # 영업외수익
    "non_operating_expense",  # 영업외비용
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
    "매출총이익": "gross_profit",
    "매출총손실": "gross_profit",
    "매출총이익(손실)": "gross_profit",
    # 회사마다 손실/이익 어느 쪽을 앞에 적는지, "총"/"영업"/"순" 같은 수식어를
    # 괄호 안쪽에도 반복하는지가 제각각이라(2026-07-21, 로컬 캐시 4,922건
    # 전수 스캔으로 확인) 실측된 조합을 그대로 등록한다 — _apply_sign()은
    # "손실"/"이익" 존재 여부만으로 판정해 순서와 무관하게 이미 올바르게
    # 동작하므로, 여기서는 alias 매핑 누락만 채우면 된다.
    "매출총이익(총손실)": "gross_profit",
    "매출총손실(이익)": "gross_profit",
    "판매비와관리비": "sga",
    "영업이익": "operating_income",
    "영업손실": "operating_income",
    "영업이익(손실)": "operating_income",
    "영업이익(영업손실)": "operating_income",
    "영업손실(이익)": "operating_income",
    "당기순이익": "net_income",
    "당기순손실": "net_income",
    "당기순이익(손실)": "net_income",
    "당기순이익(순손실)": "net_income",
    "당기순손실(이익)": "net_income",
    # 연결재무제표는 당기순이익 요약 행을 "연결당기순이익"으로 적는다((주)한미프렉시블
    # rcept 20260424000057 "X. 연결당기순이익(주석 15)" → 정규화 "연결당기순이익",
    # 2026-07-23 사용자 실측 지적). 로컬 캐시 4,922건 전수 스캔 결과 "연결" 접두어가
    # 붙은 라벨 중 표준 필드로 매핑되는 것은 오직 net_income 계열뿐이었다(연결당기순이익
    # 289 / 연결당기순이익(손실) 65 / 연결당기순손실 12 / 연결당기순이익(순손실) 3 =
    # 369건 — 연결매출액/연결영업이익/연결자본총계 등은 캐시에 아예 존재하지 않는다).
    # 그래서 normalize에서 "연결"을 일반적으로 벗기는 대신(모든 라벨에 영향을 주는
    # 전역 변경 + 과잉 일반화) 실측된 net_income 계열만 alias로 등록한다. 부호 처리는
    # normalize_account_label 기준으로 이미 올바르게 동작한다("연결당기순손실"은 "손실"만
    # 있어 순수손실로 반전, "연결당기순이익(손실)"은 이익-primary라 원문 부호 신뢰).
    # 귀속 분석 행("연결당기순이익(손실)의 귀속")은 여기 없어 매핑되지 않는다(요약 행과 구분).
    "연결당기순이익": "net_income",
    "연결당기순손실": "net_income",
    "연결당기순이익(손실)": "net_income",
    "연결당기순이익(순손실)": "net_income",
    # 영업외수익/영업외비용 (best-effort, NON_OPERATING_FINANCIAL_FIELDS 참고).
    # 로컬 캐시 4,922건 전수 스캔 결과 정규화 라벨은 정확히 "영업외수익"(4,531건)/
    # "영업외비용"(4,531건)이 지배적이고, 로마숫자 접두어·글자 사이 공백·유사문자
    # (Vl/Vll 등) 변형은 전부 normalize_account_label이 이미 흡수한다. "기타수익"/
    # "기타비용"/"기타영업외수익" 등은 회사마다 계정 체계가 달라 영업외수익과
    # 동일 개념이 아닐 수 있어 억지로 합치지 않는다(오매핑 방지).
    "영업외수익": "non_operating_income",
    "영업외비용": "non_operating_expense",
}

# 현금흐름표 전용 계정과목 alias (§4-8). fixtures 30건 중 CF 섹션 보유 19건을
# 실측한 결과, 간접법 구서식의 "영업활동으로 인한 현금흐름" 계열이 19/19로
# 지배적이었다("기말의 현금(Ⅳ+Ⅴ)"처럼 산식 접미어가 붙은 표기는
# normalize_account_label의 산식 접미어 제거로 "기말의현금"으로 정규화된다).
# 신서식(K-IFRS 직접법 등)의 "영업활동현금흐름" 계열은 실측 표본엔 없었으나
# 흔한 표준 표기라 방어적으로 함께 등록한다. BS/IS 라벨과 겹치지 않으므로
# xml_parser는 CF 섹션에서만 이 사전을 사용한다.
CF_ACCOUNT_NAME_ALIASES: dict[str, str] = {
    "영업활동으로인한현금흐름": "cf_operating",
    "영업활동현금흐름": "cf_operating",
    "영업활동으로부터의현금흐름": "cf_operating",
    "영업활동순현금흐름": "cf_operating",
    "투자활동으로인한현금흐름": "cf_investing",
    "투자활동현금흐름": "cf_investing",
    "투자활동으로부터의현금흐름": "cf_investing",
    "투자활동순현금흐름": "cf_investing",
    "재무활동으로인한현금흐름": "cf_financing",
    "재무활동현금흐름": "cf_financing",
    "재무활동으로부터의현금흐름": "cf_financing",
    "재무활동순현금흐름": "cf_financing",
    "기말의현금": "cf_ending_cash",
    "기말현금": "cf_ending_cash",
    "기말의현금및현금성자산": "cf_ending_cash",
    "기말현금및현금성자산": "cf_ending_cash",
}

# 아스키 로마숫자 접두어의 정본 표기(길이 내림차순 — 접두어 일치 순서상 긴
# 표기를 짧은 표기보다 먼저 시도해야 "XII"가 "X"로 잘못 잘리지 않는다).
# 원래 X(10)까지만 있었는데, 실제 원문에서 항목이 11~12번째까지 있는 손익계산서
# (XI/XII로 시작하는 당기순이익 등)를 발견해(2026-07-21, 로컬 캐시 4,922건 전수
# 스캔) XI/XII를 추가했다 — 이게 없으면 "XII.당기순이익"처럼 오타가 전혀 없는
# 정상 표기조차 접두어를 벗기지 못해 alias 조회가 실패했다.
_ASCII_ROMAN_NUMERALS_ORDERED = (
    "VIII", "XII", "III", "VII", "XI", "IV", "VI", "IX", "II", "I", "V", "X",
)
_ASCII_ROMAN_NUMERALS = frozenset(_ASCII_ROMAN_NUMERALS_ORDERED)

# 과목명 앞에 붙는 번호/기호 접두어 제거용 (실측: "Ⅰ.매출액"(유니코드 로마숫자),
# "I. 유동자산"(아스키 알파벳 로마숫자 — 회사마다 서식이 다르다), "1.현금및
# 현금성자산", "(1)당좌자산", "가.기초상품재고액" 등). [가-힣] 단일 글자 분기와
# 아스키 로마숫자 분기는 반드시 "."을 요구해야 "자산총계"의 "자"나 평범한
# 영단어 앞글자를 오삭제하지 않는다.
# 유니코드 로마숫자 뒤에 "."과의 사이에 공백이 낀 표기("Ⅱ . 비유동자산")와
# "Ⅱ" 대신 모양이 비슷한 "∥"(U+2225, PARALLEL TO — Ⅱ 오타/폰트 치환으로
# 추정)를 쓴 표기("∥.비유동자산")를 실제 원문에서 확인해(2026-07-21, 프로덕션
# DB의 noncurrent_assets 결측 사례 역추적) 두 변형을 모두 흡수하도록 확장했다
# — 이 두 변형은 원문에 값이 없는 게 아니라 접두어를 못 벗겨내 alias 조회가
# 통째로 실패해 있었을 뿐이라, 이번 확장 전에는 해당 대분류(비유동자산 등)
# 전체가 조용히 None으로 누락되고 있었다.
_PREFIX_RE = re.compile(
    r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ∥]+\s*\.?|"
    rf"(?:{'|'.join(_ASCII_ROMAN_NUMERALS_ORDERED)})\.|"
    r"\d+\.|\([0-9]+\)|[가-힣]\.)\s*"
)

# _PREFIX_RE의 아스키 로마숫자 분기는 정확한 대문자 표기("I.", "VI." 등)만
# 매칭한다. 실제 원문에는 육안으로 구분되지 않는 유사 문자로 오표기된 경우가
# 있다 — 소문자 "l"/"i"(로마숫자 I와 모양이 같은 알파벳), 그리스 대문자
# 이오타 "Ι"(U+0399, 라틴 대문자 I와 픽셀 단위로 동일), 유니코드 로마숫자
# "Ⅰ"(U+2160)이 아스키 "X"와 섞여 쓰인 "XⅠ."(=XI, 유니코드/아스키 혼용)를
# 실제 여러 회사·여러 계정(재무상태표/손익계산서/현금흐름표 전 구간)에서
# 확인했다(2026-07-21, 사용자가 "현금흐름표도 마찬가지 아니냐"고 재차
# 지적해 로컬 문서 캐시 4,922건을 전수 스캔하며 발견 — 예: "l.유동자산",
# "Vl.기말의현금", "Vi.기말의현금", "Ι.유동부채", "XⅠ.당기순이익(손실)").
# 유효한 로마숫자(I~XII)로 치환되는 경우에만 정규화하고, 아니면 원문을 그대로
# 둔다 — 실제 로마숫자 접두어가 아닌 텍스트를 잘못 건드리지 않기 위한
# 안전장치다. `\s`가 개행도 포함하므로 로마숫자와 마침표 사이에 줄바꿈이 낀
# 표기("XII\n.당기순손실")도 함께 흡수된다.
_ROMAN_LOOKALIKE_PREFIX_RE = re.compile(r"^\s*([IlivVXΙⅠ]+)\s*\.")
_ROMAN_LOOKALIKE_TRANSLATION = str.maketrans({"l": "I", "i": "I", "Ι": "I", "Ⅰ": "I"})


def _normalize_roman_lookalike_prefix(text: str) -> str:
    """로마숫자 접두어 자리의 유사 문자 오표기를 정본 아스키 로마숫자로 치환."""
    match = _ROMAN_LOOKALIKE_PREFIX_RE.match(text)
    if match is None:
        return text
    canonical = match.group(1).translate(_ROMAN_LOOKALIKE_TRANSLATION)
    if canonical not in _ASCII_ROMAN_NUMERALS:
        return text  # 유효한 로마숫자로 치환되지 않으면 오탐 방지를 위해 건드리지 않는다.
    return canonical + "." + text[match.end():]

# 과목명 뒤에 붙는 "(주석13)"/"(주6)"/"(주석 2,4)" 같은 각주 참조 제거용
# (실측: "Ⅳ. 판매비와관리비(주석13)", "Ⅱ.매출원가(주6)" — 같은 "주석" 표시가
# 회사마다 "주석"/"주"로 축약 방식이 다르다). 괄호 안이 순수 숫자/콤마/공백
# (+"주석" 또는 "주")일 때만 제거한다 — "당기순이익(손실)"/"수익(매출액)"처럼
# 괄호 안이 실제 항목명을 구성하는 경우까지 지워버리지 않기 위해서다.
# 여러 각주 번호를 한글 접속사로 잇는 표기("(주석10과 13)" — "주석 10과 13" =
# 주석 10 및 13)도 실측했다((주)물맑은고기팜농업회사법인 rcept 20260408002307
# "Ⅱ. 매출원가(주석10과 13)", 2026-07-23 사용자 실측 지적). 이 경우 "과"라는
# 한글이 괄호 안에 섞여 기존 순수 숫자/콤마/공백 패턴이 통째로 매치 실패해
# 각주 접미어가 안 벗겨졌고, "매출원가(주석10과13)"가 alias 조회를 못 해 cogs가
# 통째로 누락됐다. 그래서 "주석"/"주" 마커가 있는 경우에 한해 숫자를 잇는 한글
# 접속사 "과"/"와"도 허용한다 — 마커가 없는 순수 숫자형 브랜치(둘째 대안)는
# 옛 동작 그대로라, "(손실)"/"(매출액)"처럼 마커 없이 한글이 든 의미있는 괄호는
# 여전히 보존된다(과잉 제거 방지).
_FOOTNOTE_SUFFIX_RE = re.compile(r"\(\s*(?:(?:주석|주)[\s0-9,과와]*|[\s0-9,]*)\)\s*$")

# 과목명 뒤에 붙는 소계 "산식"/항목번호 참조 접미어 제거용 (실측: "기말의현금
# (Ⅳ+Ⅴ)", "현금의증가(감소)(Ⅰ+Ⅱ+Ⅲ)"처럼 계산식을 병기하는 서식뿐 아니라,
# "영업활동으로 인한 현금흐름(I)"처럼 "+" 없이 자신의 항목 번호만 괄호로 다시
# 적는 서식도 실측했다(2026-07-21, 로컬 캐시 전수 스캔). 괄호/대괄호 안이
# 로마숫자(유니코드/아스키 I·V·X)·숫자·공백·"+"로만 이뤄지면 "+" 유무와 무관하게
# 제거한다 — 애초에 "+"를 요구했던 이유는 이 문자 집합만으로도 "당기순이익(손실)"/
# "수익(매출액)"처럼 한글이 든 의미있는 괄호를 이미 걸러내고 있어 "+" 요구가
# 실질적인 안전장치가 아니었다(문자 집합 자체가 한글을 포함하지 않는다).
_FORMULA_SUFFIX_RE = re.compile(
    r"[\(\[][ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX0-9\s+＋]+[\)\]]\s*$"
)

# 과목명 뒤에 붙는 "주당손익(EPS)" 병기 괄호 접미어 제거용 (실측: 주식회사
# 노바스 rcept 20260407001297 손익계산서 당기순이익 라벨이
# "X. 당기순이익(손실)(주석16)(주당손익 당기 (14,770)원  전기  (11,169)원)"처럼
# 각주 참조 뒤에 EPS(주당손익) 값을 또 하나의 괄호로 병기했다(2026-07-23
# 사용자 실측). 괄호 안에 "당기"/"전기"/"원" 같은 한글과 자체 중첩 괄호
# (EPS 금액 "(14,770)")가 섞여 있어 _FOOTNOTE_SUFFIX_RE(숫자/콤마/공백+마커)와
# _FORMULA_SUFFIX_RE(로마숫자/숫자/공백/+)가 모두 매치 실패했고, 그 결과
# 정규화 라벨이 "...당기순이익(손실)(주석16)(주당손익...)"로 남아 alias 키
# "당기순이익(손실)"와 불일치 → net_income이 통째로 누락(PARTIAL)됐다.
# **괄호가 "주당"으로 시작할 때에 한해서만** 벗긴다 — "(손실)"/"(매출액)"처럼
# 의미 있는 항목명 괄호는 절대 건드리지 않는다(과잉 제거 방지). 안쪽 EPS 금액
# 괄호 한 겹의 중첩("(14,770)")까지 흡수하되, 반드시 문자열 끝($)에 붙은
# 접미어만 대상으로 한다. 이 접미어는 각주 참조 "(주석16)"보다 **뒤에** 오므로
# `normalize_account_label`에서 _FOOTNOTE_SUFFIX_RE보다 먼저 벗겨야 그다음
# 각주 제거가 "(주석16)"에 도달할 수 있다.
_EPS_SUFFIX_RE = re.compile(r"\(\s*주당[^()]*(?:\([^()]*\)[^()]*)*\)\s*$")

# 금액 문자열에서 콤마/공백 제거용
_AMOUNT_CLEAN_RE = re.compile(r"[,\s　]")

# 총계 행의 밑줄(이중선)이 "===============" 같은 ASCII 괘선으로 금액 셀에 그대로
# 섞여 들어오는 실측 사례가 있다(2012년 원문 20120110000471 자산총계
# "16,507,429,508 ==============="). 이 괘선을 제거하지 않으면 float 변환이 실패해
# 총계가 None으로 누락된다("=" 문자는 정상 금액에는 절대 나타나지 않으므로 안전).
_RULE_CHARS = "=＝"

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
    text = _normalize_roman_lookalike_prefix(text)  # "l."/"Vi."/"Ι." 등을 "I."/"VI."로 치환
    for _ in range(2):  # 접두어가 이중으로 붙는 경우는 실측상 없었지만 안전하게 2회 반복
        stripped = _PREFIX_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    for _ in range(2):  # "(주당손익 ... 원)" 같은 EPS 병기 접미어 제거(각주보다 뒤에 오므로 먼저)
        stripped = _EPS_SUFFIX_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    for _ in range(2):  # "(주석13)" 같은 각주 참조가 이어 붙는 경우 대비
        stripped = _FOOTNOTE_SUFFIX_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    for _ in range(2):  # "기말의현금(Ⅳ+Ⅴ)" 같은 산식 접미어 제거 (현금흐름표)
        stripped = _FORMULA_SUFFIX_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    # 셀 안에서 라벨이 여러 줄로 나뉘어 "판매비와관리\n비"처럼 단어 중간에
    # 개행이 섞이는 실측 사례가 있어(2026-07-21), 일반 공백/전각 공백과 함께
    # 개행·탭도 모두 제거한다.
    for ch in (" ", "　", "\n", "\r", "\t"):
        text = text.replace(ch, "")
    return text


def parse_won_amount(text: str) -> float | None:
    """원문 금액 셀 텍스트를 원(KRW) 단위 float로 변환.

    괄호 표기는 음수, "-"/빈 문자열은 값 없음(None)으로 처리한다.
    """
    raw = (text or "").strip()
    if _RULE_CHARS[0] in raw or _RULE_CHARS[1] in raw:
        # 총계 행 밑줄("16,507,429,508 ===============")의 괘선을 앞뒤에서 제거.
        raw = raw.strip(_RULE_CHARS).strip()
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
