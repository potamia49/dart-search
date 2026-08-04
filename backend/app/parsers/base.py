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

# 세부계정(sub-account) 5항목. CF_FINANCIAL_FIELDS/NON_OPERATING_FINANCIAL_FIELDS와
# **완전히 동형인 best-effort 항목**이다 — 표준 13항목(STANDARD/DIRECT_FINANCIAL_FIELDS)에
# 절대 넣지 않으며 `determine_parse_status()` 판정에도 관여하지 않는다(결측이어도
# PARTIAL/FAILED로 떨어지지 않음). 이유는 CF/영업외손익과 동일하다: 이미 OK로 완료된
# Job이 새 필드 결측으로 재분류되면 검수 기준이 깨진다.
#
# **각 필드는 원천 재무제표가 하나로 고정돼 있다**(아래 세 alias 사전이 섹션별로
# 분리돼 있는 이유). 같은 라벨이 재무제표마다 다른 뜻이기 때문이다:
# - "매출채권"은 재무상태표에서는 잔액이지만 현금흐름표에서는 운전자본 증감액이다.
# - "이자비용"은 손익계산서에서는 발생주의 비용이지만 현금흐름표에서는 간접법 가산
#   조정액(또는 0)이다 — 로컬 캐시 실측상 두 값이 **92.3% 불일치**한다(299건 중 276건).
# - "감가상각비"는 손익계산서 본문에서는 판매비와관리비에 속한 몫뿐이고 현금흐름표
#   가산 조정에서는 제조원가 몫까지 포함한 **총액**이다 — 실측 7,355건 중 **75.4%가
#   불일치**했고(제조업은 최대 18배 차이) 그래서 두 출처를 폴백으로 섞지 않는다.
DETAIL_FINANCIAL_FIELDS: tuple[str, ...] = (
    "cash_and_equivalents",  # 현금및현금성자산 (재무상태표, 정부보조금 차감 후 순액)
    "trade_receivables",     # 매출채권 (재무상태표, 대손충당금 차감 후 순액)
    "interest_expense",      # 이자비용 (손익계산서)
    "depreciation",          # 감가상각비 (현금흐름표 = 제조원가 몫 포함 총액)
    "amortization",          # 무형자산상각비 (현금흐름표)
)

# 재무상태표 전용 세부계정 alias. 로컬 문서 캐시 8,748건 전수 스캔(2026-08-05)에서
# 실제로 관측된 표기만 등록한다.
#   현금: 현금및현금성자산 7,857(FINANCE)/453(첨부) · 현금및현금등가물 56 ·
#         현금및예치금 12/4 · 현금성자산 6/1 → 이 4종으로 FINANCE 99.7% / 첨부 98.9%.
#         (현금 행 바로 다음의 정부보조금 contra 행은 차감한다 — `CONTRA_ROW_SPECS` 참고.)
#         라벨이 그냥 "현금"뿐인 10건은 **등록하지 않는다** — 예금을 별도 행으로 적는
#         서식일 수 있어 현금성자산 전체를 과소계상할 위험이 있다(오매핑 방지).
#         "특정현금과예금"(26건, 사용이 제한된 예금)도 성격이 달라 제외한다.
#   매출채권: 매출채권 6,833/229 · 유동매출채권 5 · 단기매출채권 1.
#         **"매출채권및기타채권" 계열(첨부 193건 등)은 등록하지 않는다** — 매출채권에
#         기타채권(미수금·대여금 등)이 합쳐진 다른 집계라 그대로 쓰면 과대계상이다
#         (base.py가 "기타수익/기타비용"을 영업외수익에 억지로 합치지 않는 것과 같은
#         판단). 이 때문에 첨부 서식의 매출채권 커버리지는 50.9%에 머문다.
#         "장기매출채권"/"장기성매출채권"은 비유동 항목이라 별개로 두고 제외한다.
BS_DETAIL_ACCOUNT_ALIASES: dict[str, str] = {
    "현금및현금성자산": "cash_and_equivalents",
    "현금및현금등가물": "cash_and_equivalents",
    "현금및예치금": "cash_and_equivalents",
    "현금성자산": "cash_and_equivalents",
    "매출채권": "trade_receivables",
    "유동매출채권": "trade_receivables",
    "단기매출채권": "trade_receivables",
}

# 손익계산서 전용 세부계정 alias. "이자비용" 7,444건(FINANCE 손익계산서의 93.6%)이
# 지배적이고 "지급이자"(6건)는 같은 뜻의 구표기라 함께 등록한다. **IFRS "(첨부)재무제표"
# 서식의 "금융비용"(386건)/"금융원가"(71건)는 등록하지 않는다** — 이자비용에 외환차손·
# 파생상품평가손실 등이 합쳐진 다른 집계라 그대로 쓰면 과대계상이다. 그래서 첨부 서식의
# 손익계산서 이자비용 커버리지는 0.4%에 그친다(현금흐름표 폴백은 위 독스트링 참고로 금지).
IS_DETAIL_ACCOUNT_ALIASES: dict[str, str] = {
    "이자비용": "interest_expense",
    "지급이자": "interest_expense",
}

# 현금흐름표 전용 세부계정 alias(간접법 "현금의 유출이 없는 비용 등의 가산" 블록).
#   감가상각비 7,565(FINANCE)/101(첨부) · 유형자산감가상각비 19/4 → 95.7% / 21.9%.
#     "사용권자산감가상각비"/"투자부동산감가상각비" 등 자산 종류별 부분 상각액은 총액이
#     아니라 일부라 등록하지 않는다(감가상각비 행과 병존할 수 있고, 단독일 때 채우면
#     과소계상이다).
#   무형자산상각비 3,487/84 · 무형자산상각 54 · 무형고정자산상각 40 ·
#     무형자산감가상각비 23 · 무형고정자산상각비 13 → 45.8% / 18.4%.
#     ("무형자산감가상각비"는 "감가상각비"와 글자가 겹치지만 alias 조회가 **정규화 라벨
#      완전 일치**라 depreciation으로 새지 않는다.)
CF_DETAIL_ACCOUNT_ALIASES: dict[str, str] = {
    "감가상각비": "depreciation",
    "유형자산감가상각비": "depreciation",
    "무형자산상각비": "amortization",
    "무형자산상각": "amortization",
    "무형고정자산상각": "amortization",
    "무형고정자산상각비": "amortization",
    "무형자산감가상각비": "amortization",
}

# 세부계정 alias를 재무제표 섹션 키(walk_statement_tables의 "bs"/"is"/"cf")로 찾는다.
DETAIL_ALIASES_BY_SECTION: dict[str, dict[str, str]] = {
    "bs": BS_DETAIL_ACCOUNT_ALIASES,
    "is": IS_DETAIL_ACCOUNT_ALIASES,
    "cf": CF_DETAIL_ACCOUNT_ALIASES,
}

@dataclass(frozen=True)
class ContraRowSpec:
    """세부계정 총액(gross)에서 차감할 contra(차감) 행을 알아보는 규칙.

    재무상태표는 자산을 총액으로 적고 그 차감 항목을 **바로 다음 별도 행**에 음수로
    적는 서식이 표준이다. 이 행을 차감하지 않으면 저장값이 총액이 돼 실제 장부금액보다
    과대계상된다. 계정마다 차감 항목의 이름이 다르므로(매출채권↔대손충당금,
    현금↔정부보조금) 마커 집합을 계정별로 따로 둔다.

    - `markers`: 정규화 라벨에 **부분문자열로** 들어 있으면 contra 행으로 본다
      ("매출채권대손충당금"/"대손충당금(매출채권)"/"(-)정부보조금" 같은 변형을 흡수).
      **실측된 표기만 등록한다**(오탐 방지) — 이 프로젝트의 과잉 일반화 금지 원칙.
    - `owner_keyword`: 바로 다음 행이 아닌 위치에서 추가로 요구하는 소유 계정 표기.
      이름 없는 차감 행("대손충당금")은 **인접할 때만** 그 계정 몫으로 인정하고, 떨어져
      있으면 계정명이 함께 적힌 경우만 인정한다.
    """

    markers: tuple[str, ...]
    owner_keyword: str


# 세부계정별 contra 행 규칙. 로컬 문서 캐시 8,748건 전수 실측(2026-08-05) 결과만 담는다.
CONTRA_ROW_SPECS: dict[str, ContraRowSpec] = {
    # 매출채권 보유 6,833건 중 5,430건(**79.5%**)이 바로 다음 행에 대손충당금을 둔다.
    # 차감하지 않으면 최악 **6.38배** 과대계상(태진 20220415000297: 총액 2,237,045,099 /
    # 대손충당금 1,886,435,870 → 순액 350,609,229)이고, 전액 대손 설정으로 순액이 0인
    # 회사(78건)는 회수 불가능한 채권이 정상 채권으로 보인다.
    # 마커 4종은 전부 실측 표기다 — "대손충당금"(지배적) 외에 "대손충당부채"(라벨
    # 출현 21건 중 인접 적용 4건, 20230410000109 등) · "대손충담금"(오타, 적용 2건,
    # 20250410000819/20260403001550) · "대손충당"(끝 글자 누락, 적용 1건,
    # 20250404003165). 부분문자열 판정이라 "대손충당" 하나가 앞의 둘을 이미
    # 포섭하지만, 무엇을 실측했는지 남기려고 모두 적어 둔다(2026-08-05 dart-qa
    # 2차 재검증으로 건수 정정 — 최초 등록 시점의 "3건"은 라벨이 아니라
    # rcept_no 3건을 직접 대조한 표본 수였다).
    "trade_receivables": ContraRowSpec(
        markers=("대손충당금", "대손충당부채", "대손충담금", "대손충당"),
        owner_keyword="매출채권",
    ),
    # 현금 행 바로 다음에 정부보조금을 음수로 두는 서식이 112건 있다(현금이 채워진
    # 8,380건의 1.3%). 차감하지 않으면 중앙값 1.6% · 10% 이상 18건 · 최대 **89.2%**
    # 과대계상된다(20230412000095: 총액 22,415,189 / 보조금 20,000,000 → 순액
    # 2,415,189). 20230404001968은 현금 2,952,261,667이 유동자산 2,530,940,076을
    # **넘는** 불가능한 값이었는데, 보조금 1,720,000,000을 빼면 해소된다.
    # 마커 4종 역시 전부 실측 표기다 — "정부보조금"(인접 적용 108건) ·
    # "국고보조금"(인접 적용 52건) · 오타 "정부조보금"(2건) · "국고보고조금"(2건).
    # "(-)정부보조금"과 "현금및현금성자산_국고보조금"은 부분문자열 판정으로 이미
    # 잡힌다(2026-08-05 dart-qa 2차 재검증으로 건수 정정 — 최초 등록 시점의
    # "71"/"35"/"1"/"1"은 라벨 출현이 아니라 표본 문서 수였다).
    # **"특정예금차감"(1건)은 등록하지 않는다** — 보조금이 아니라 사용이 제한된 예금을
    # 현금성자산에서 빼는 별개 개념이고, 같은 이유로 "특정현금과예금"을 현금 alias에서
    # 제외한 판단과 일관되게 둔다.
    "cash_and_equivalents": ContraRowSpec(
        markers=("정부보조금", "국고보조금", "정부조보금", "국고보고조금"),
        owner_keyword="현금",
    ),
}


def is_contra_row_label(norm_label: str, spec: ContraRowSpec, *, adjacent: bool) -> bool:
    """정규화 라벨이 `spec`이 가리키는 계정의 contra 행인지 판정.

    `adjacent=True`(총액 행 바로 다음)면 마커만 있으면 그 계정 몫으로 인정하고, 떨어져
    있으면 `owner_keyword`가 함께 적힌 경우만 인정한다 — 총액 행 다음이 **다른 계정**이고
    그 아래에 이름 없는 차감 행이 오는 서식(매출채권 기준 실측 75건)에서 그 차감 행은
    그 다른 계정의 몫이기 때문이다. 실제로 흥한건설 20230404002046은 "매출채권
    2,244,097,579 / 단기대여금 / 대손충당금 (18,892,793,483)" 순서라, 인접 여부를 보지
    않고 빼면 매출채권이 **-166억**이 된다.
    """
    if not any(marker in norm_label for marker in spec.markers):
        return False
    if adjacent:
        return True
    return spec.owner_keyword in norm_label

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
    # IFRS "(첨부)재무제표"·연결 재무상태표는 "II. 유동자산"을 값 없는 섹션 헤더로
    # 두고 실제 소계 값은 "유동자산합계" 행에 적는다(씨이케이 rcept 20260330001497
    # "II.유동자산"(값 없음) → "유동자산합계" 52,141,919,553, 2026-07-23 dart-parser
    # 실측). 로컬 캐시 4,923건 전수 스캔상 "유동자산합계"류 소계 행은 17~18개 문서에
    # 나타난다. 헤더 행("유동자산")과 소계 행("유동자산합계")이 같은 필드로 매핑되며,
    # 값이 있는 행이 채우도록 _extract_attach_section이 빈 매칭으로는 필드를 잠그지
    # 않는다(아래 함수 참고). FINANCE 서식은 "유동자산" 헤더에 값이 인라인이라 그
    # 행이 먼저 채우고 "유동자산합계"는 첫 매칭 우선으로 건너뛴다(무변경·회귀 없음).
    "유동자산합계": "current_assets",
    "비유동자산합계": "noncurrent_assets",
    "유동부채합계": "current_liab",
    "비유동부채합계": "noncurrent_liab",
    # 같은 소계를 "합계"가 아니라 "계"로 줄여 적는 변형(상지해운 rcept 20260325000364
    # "유동자산계" 5,432,974,069 / "비유동자산계" / "유동부채계" / "비유동부채계",
    # 2026-07-23 dart-parser 실측). 로컬 캐시 전수 스캔상 3~4개 문서에 나타난다.
    "유동자산계": "current_assets",
    "비유동자산계": "noncurrent_assets",
    "유동부채계": "current_liab",
    "비유동부채계": "noncurrent_liab",
    "매출액": "revenue",
    "매출액및영업수익": "revenue",
    "영업수익": "revenue",
    "수익(매출액)": "revenue",
    # IFRS "(첨부)재무제표"·연결 포괄손익계산서는 매출 최상단 행을 "매출액"이 아니라
    # 그냥 "매출"로 적는 경우가 있다((주)엘엑스엠엠에이 rcept 20260326000129
    # "I. 매출" 748,580,488,436 → 정규화 "매출", 2026-07-23 dart-parser 실측).
    # 로컬 캐시 4,923건 전수 스캔상 정규화 라벨이 정확히 "매출"인 행은 465건/330개
    # 문서로 흔하고, alias 조회는 정규화 라벨 완전 일치 + "첫 매칭 우선"이라 이미
    # "매출액"으로 revenue가 채워진 문서에는 영향이 없다(회귀 안전).
    "매출": "revenue",
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
    # 손익계산서 최종 순손익 요약 행을 "당기순이익"/"당기순손실"이 아니라 이익·손실을
    # 아우르는 "당기순손익"으로 적는 서식이 흔하다(에스엠인더스트리 rcept
    # 20260407002731 "XI. 당기순손익", 진양에너지유틸리티 20260317000433
    # "Ⅶ. 당기순손익", 2026-07-23 dart-parser 실측). 로컬 캐시 4,923건 전수 스캔상
    # 정규화 라벨이 정확히 "당기순손익"인 행은 335건/286개 문서로 흔하다. alias 조회는
    # 완전 일치라 "법인세비용차감전순손익"(세전) 등에는 오매칭되지 않고, _apply_sign은
    # "손실"/"이익" 부분문자열이 모두 없어 원문 부호(괄호=음수)를 그대로 신뢰한다
    # (요약 순손익 행은 원문이 자연 부호로 적어 반전이 불필요 — 위 두 실측 모두 성립).
    "당기순손익": "net_income",
    # 연결 표기가 "당기순이익" 앞이 아니라 중간에 끼는 변형("당기연결순이익(손실)"
    # 등)도 실측했다((주)신신사 rcept 20260407001413 "당기연결순이익(손실)"
    # 6,412,374,853, 2026-07-23 dart-parser 실측). 로컬 캐시 전수 스캔상 실제로
    # 나타난 net_income 계열 요약 행 변형(당기연결순손실 4 / 당기연결순이익(손실) 3 /
    # 연결당기순손익 3 / 당기연결순이익 1)만 등록한다(귀속 분석 행·주당 행은 제외).
    # 부호는 normalize 기준으로 이미 정확하다("당기연결순손실"은 순수손실 반전,
    # "당기연결순이익(손실)"은 이익-primary라 원문 부호 신뢰, "연결당기순손익"은
    # 이익/손실 부분문자열이 없어 원문 부호 신뢰).
    "당기연결순이익": "net_income",
    "당기연결순손실": "net_income",
    "당기연결순이익(손실)": "net_income",
    "연결당기순손익": "net_income",
    # [알려진 한계] 중단영업을 분리해 적는 서식(예: "계속영업당기순손실"
    # + "중단영업손실" 두 행으로 나뉘고 "당기순이익"/"당기순손실" 합계 행 자체가
    # 없는 경우, (주)에스에이치엔지니어링 rcept 20240401002723 실측 —
    # 계속영업당기순손실 -2,176,456,086 + 중단영업손실 -3,189,710,477 =
    # -5,366,166,563)은 net_income이 두 행의 합이어야 하는데 이 두 라벨이
    # alias로 등록돼 있지 않아 매핑되지 않는다. parse_status는 PARTIAL로
    # 정직하게 남아 조용한 오류는 아니다 — 이 두 행을 합산하는 로직은 이번
    # 범위에서 구현하지 않았고 별도 과제로 남긴다(2026-07-28).
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
# 끝의 `\)+`: EPS 병기 괄호가 균형이 맞지 않게 닫는 괄호를 하나 더 붙여 적는 실측
# 오타를 흡수한다(대능주택개발 rcept 20220406000855 "X. 당기순이익(손실)(주석13)
# (주당순이익(손실):당기 (4,407원) 전기 60,420원))" — EPS 괄호 뒤 여분의 ")",
# 2026-07-23 dart-parser 실측). "(주당"으로 시작하는 접미어에만 적용되고 뒤에
# 의미 있는 텍스트가 오지 않으므로 여분 닫는 괄호를 더 소비해도 과잉 제거가 없다.
_EPS_SUFFIX_RE = re.compile(r"\(\s*주당[^()]*(?:\([^()]*\)[^()]*)*\)+\s*$")

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


# 로마숫자 항목번호로 시작하는지 판정용(유니코드/아스키 + 유사문자 오표기 흡수).
# "(첨부)재무제표" 서식에서 로마숫자 접두어는 **구역(대분류) 행의 표지**라,
# 들여쓰기가 없는 표에서 세부계정과 구역 행을 가르는 근거로 쓴다
# (account_detail.py `_collect_attach_table`의 "계속 행" 처리 참고).
_ROMAN_PREFIX_RE = re.compile(
    r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ∥]+|"
    rf"(?:{'|'.join(_ASCII_ROMAN_NUMERALS_ORDERED)}))\s*\."
)


def has_roman_numeral_prefix(label: str) -> bool:
    """라벨이 로마숫자 항목번호("Ⅳ." / "IV." / 유사문자 "Vl.")로 시작하는가."""
    text = _normalize_roman_lookalike_prefix((label or "").strip())
    return _ROMAN_PREFIX_RE.match(text) is not None


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
