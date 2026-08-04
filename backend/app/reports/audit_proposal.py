"""선택 항목 보고서(감사 수임 제안서 HTML) 생성.

`POST /api/jobs/{job_id}/generate-report`(app/api/results.py)가 쓰는 순수 함수
모음이다. 결과 목록에서 체크한 회사들에 대해

    <기준 폴더>/report/YYYY-MM-DD[_2, _3 ...]/
        ├─ (주)OO산업.html      ← 회사 1건 = HTML 1개
        ├─ (주)XX전자.html
        └─ 발송처_목록.xlsx      ← 회사명/주소 2컬럼(우편 라벨 인쇄용)

를 만든다. "기준 폴더"는 `app/config.py`의 `BACKEND_DIR`
(=`DART_SEARCH_APP_DIR`가 있으면 exe 옆 폴더, 없으면 backend/)을 그대로 재사용한다 —
DB/문서 캐시와 같은 자리에 남아야 exe 배포본에서도 사용자가 찾을 수 있다.
경로 계산 로직을 여기서 새로 만들지 않는다(`Settings.report_output_dir`).

**HTML은 템플릿 파일(`tamplate/audit_proposal_template.html`)을 그대로 쓰고,
그 안의 `const EMBEDDED_DATA = {...};` 블록만 통째로 교체한다.** 템플릿의
HTML/CSS/차트 렌더 로직은 **원칙적으로** 건드리지 않는다(템플릿 주석의 "사용법" 참고).
치환은 정규식 자르기가 아니라 **중괄호 짝맞추기 스캐너**(`find_embedded_data_block`,
문자열/주석 안의 중괄호를 건너뛴다)로 블록 범위를 찾아 교체한다.

이 "템플릿 무수정" 관행에는 **예외가 5건 있다**(전부 dart-qa 리뷰 후 사용자 승인).
앞의 ①②③(2026-08-03)은 뿌리가 같다 — **완전자본잠식(자본총계 < 0)이면 부채비율이 음수**가
되는데, 음수는 `null`도 `0`도 `NaN`/`Infinity`도 아니라 아래 `select_financial_rows()`의
결측·0분모 가드를 **정상값으로 통과**하므로 이 모듈에서는 막을 수 없다(그리고 자본잠식
자체는 실재하는 상태라 연도를 버려서도 안 된다 — `RATIO_POSITIVE_DENOMINATOR_KEYS`
주석 참고). 새 결측 방어 로직을 여기에 덧대지 말 것:

  ① `scoreStability()` — `부채비율 < 0.5` 가점 때문에 자본잠식 회사가 재무안정성
     A/B로 인쇄됐다. 맨 앞에 `if(last.부채비율 < 0) return 10;` 가드를 넣어 D 확정.
  ② `buildRiskList()` — `last.부채비율 < first.부채비율`(초록 "개선" 문구) 비교도
     음수를 통과해 자본잠식으로 **악화**된 회사에 "지속 개선" 문구가 찍혔다.
     음수면 red "완전자본잠식" 항목을 넣고, 개선은 `first.부채비율 >= 0`일 때만.
  ③ `renderDoughnut()`/`renderLineChart()` — 자본구성 도넛은 음수 조각·100%를 넘는
     원호와 "유동부채 903.2% … 자본총계 -864.7%" 같은 범례를 인쇄했고, 재무안정성
     선그래프는 y축이 0부터 시작해 **음수 부채비율 지점이 차트 밖으로 잘려** 보이지
     않았다. 도넛은 음수가 섞이면 구성비 대신 금액 안내로 대체하고, 선그래프는
     음수까지 포함해 스케일을 잡고 0선을 긋는다(양수 구간 렌더는 수식상 동일).

뒤의 두 건은 자본잠식과 무관하고, **JS는 건드리지 않은 HTML/CSS 수정**이다:

  ④ `.opinion-tag` 배경색 fallback(2026-08-04 F1, CSS 1줄) — 전용 클래스가 없는
     `부적정`/`미상`이 흰 종이에 흰 글자로 찍혔다(`UNKNOWN_OPINION_LABEL` 주석 참고).
  ⑤ 비교군이 없을 때(`regionGroup=[]`)의 안내 문구(2026-08-05, HTML 2줄 + CSS 4줄) —
     업종 필터 도입으로 생긴 63건(4.2%)에서 2페이지의 비교 영역 3개가 **안내 없이
     통째로 백지**였다(peers 쪽 각주만 "동종업종 비교사 데이터 없음"을 찍고 지역
     쪽 두 섹션은 조용했다). `#regionTable tbody:empty`(= 렌더된 행 0건)를 조건으로
     쓰는 CSS만 얹어 순위 차트·상세 표 자리에 안내 문구를 보인다 — regionGroup이
     있는 문서(95.8%)는 렌더 결과가 한 글자도 바뀌지 않는다.

회귀 테스트는 전부 `tests/test_reports.py`에 있다(템플릿 원문 파싱 + Node `vm` 실제
렌더 검증 2중 구조).

데이터 소스:
  - `company`  : `results` 1행(회사명/업종명/주소/대표자/결산기준일/감사의견/감사인)
  - `financials`: `financial_snapshots`(회사×회계연도) — 호출부가 기존 재무이력
                  조회(`app/api/results.py::get_result_history`)를 재사용해 넘긴다.
                  여기서는 DB 쿼리를 하지 않는다.
  - `firm`     : `app/reports/firm_profile.py`의 `FIRM_PROFILE` 상수
  - `peers`/`industryAverage`/`regionGroup`: **같은 Job에서 이미 수집한 다른
    회사들**(`results` + 그 `financial_snapshots`)로 채운다(2026-08-04). 외부 API
    호출도, 새 DB 스키마도 없다 — 호출부가 Job 전체를 **요청당 한 번만** 벌크
    조회해 `PeerPool`로 만들어 넘기고(`build_peer_pool()`), 회사별 선별은 순수
    함수(`select_peers()`/`select_region_group()`)가 한다. 자세한 규칙은 아래
    "비교군" 섹션 주석 참고.
  - `opinionSummary`: 자유 서술 문단이라 자동 생성 대상이 아니다 — 계속 `""`.

**`financials`에는 "비율 계산에 필요한 항목이 모두 있는 연도"만 싣는다**
(2026-08-03, dart-qa 리뷰 반영). 이유는 템플릿의 렌더 로직 자체에 있다:

  - 템플릿 렌더 메인은 `financials[financials.length-1].매출액`을 무조건 읽어서,
    빈 배열을 넘기면 `TypeError`로 async IIFE가 통째로 중단된다 — 회사명/주소까지만
    찍히고 **KPI·차트·등급은 물론 마지막 장의 사무소 소개·담당자·연락처까지 전부
    빈 문서**가 인쇄된다(우편 발송 사고). 그래서 실을 연도가 하나도 없으면
    **아예 생성하지 않고** `warnings`에만 남긴다.
  - 템플릿의 `calcRatios()`는 null-safe가 아니라 결측 항목이 있으면 `NaN`/
    `Infinity`가 그대로 인쇄되고, 등급 산정(`scoreProfitability` 등)이 그 값을
    받아 **데이터가 없을수록 좋은 등급이 나오는** 방향으로 고장난다. 그래서
    결측/0분모 연도는 배열에서 제외하고 경고에 남긴다. **분모가 음수인 연도도
    같이 제외한다** — 비율의 부호가 통째로 뒤집혀 "매출액이 마이너스인데 수익성
    A등급" 같은 반전이 생기기 때문이다(단 `자본총계`는 예외, 위 ①~③ 참고).
  - `parse_status`가 FAILED인 연도도 같은 이유로 제외하고, PARTIAL이거나
    전기 유래(`from_current_period=0`)인 연도는 **싣되 경고로 알린다**
    (CLAUDE.md: "파싱은 100% 자동화되지 않는다 → 검수 대상을 남긴다"가 대외
    발송 문서 단계에서 끊기지 않게).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.utils.exceptions import IllegalCharacterError

from app.config import BACKEND_DIR, get_settings
from app.models.result import ParseStatus
from app.reports.firm_profile import FIRM_PROFILE, is_placeholder_contact

# 저장소의 템플릿 폴더 이름. "tamplate"는 오타처럼 보이지만 실제 폴더명이므로
# 그대로 쓴다(폴더를 rename하면 여기와 exe 빌드 `--add-data`를 함께 고쳐야 한다).
TEMPLATE_DIR_NAME = "tamplate"
TEMPLATE_FILENAME = "audit_proposal_template.html"

# 우편 발송용 라벨 엑셀 파일명 + 컬럼(회사명/주소 2개만 — 라벨지 인쇄가 목적이라
# 군더더기를 넣지 않는다).
LABEL_FILENAME = "발송처_목록.xlsx"
LABEL_COLUMNS: tuple[str, str] = ("회사명", "주소")

# `financial_snapshots` 컬럼 -> 템플릿 `financials[]` 키.
#
# **앞의 13항목은 템플릿이 실제로 읽는 키**다. 라벨은 템플릿의 계산식(`calcRatios`)이
# 참조하는 이름이라 임의로 바꾸면 안 된다("판관비"는 엑셀 내보내기의 "판매비와관리비"와
# 표기가 다른데, 이는 템플릿 쪽 이름을 따른 것이다).
#
# **뒤의 세부계정 5항목(2026-08-05)은 템플릿이 아직 읽지 않는다** — EMBEDDED_DATA의
# `financials[]`에 값만 실린다. 이 확장이 안전한 이유는 템플릿 JS가 `financials` 행을
# **명시 키로만** 읽기 때문이다(`f.매출액` 등. 유일한 `for...in`은 `svgEl()`의 SVG 속성
# 객체 순회이고 financials와 무관하다) — 모르는 키가 늘어도 렌더 결과가 한 글자도
# 바뀌지 않는다. 화면에 실제로 인쇄하려면 템플릿에 카드/표를 새로 넣어야 하고, 그건
# "템플릿은 원칙적으로 수정하지 않는다" 관행 때문에 **사용자 판단 사항으로 남겨 뒀다**.
#
# 세부계정 5항목의 라벨은 **엑셀 내보내기와 글자까지 동일**하게 둔다(위 "판관비"와 달리
# 템플릿이 요구하는 이름이 없으므로 굳이 다른 표기를 만들 이유가 없다). 특히
# "(순액)"/"(현금흐름표)" 접미어는 장식이 아니라 **출처 표시**다 — 매출채권 총액은
# 순액의 최대 6.4배이고, 감가상각비는 손익계산서 판관비 몫과 실측 75.4% 불일치한다
# (`parsers/base.py`의 `DETAIL_FINANCIAL_FIELDS` 주석 참고).
#
# 키(왼쪽)가 실제 스냅샷 컬럼인지는 tests/test_reports.py의 드리프트 가드가
# `FINANCIAL_SNAPSHOT_ACCOUNT_LABELS`(app/exporters/excel.py)와 대조해 잠근다.
SNAPSHOT_FIELD_TO_REPORT_KEY: dict[str, str] = {
    "current_assets": "유동자산",
    "noncurrent_assets": "비유동자산",
    "total_assets": "자산총계",
    "current_liab": "유동부채",
    "noncurrent_liab": "비유동부채",
    "total_liab": "부채총계",
    "total_equity": "자본총계",
    "revenue": "매출액",
    "cogs": "매출원가",
    "gross_profit": "매출총이익",
    "sga": "판관비",
    "operating_income": "영업이익",
    "net_income": "당기순이익",
    # --- 여기부터 세부계정 5항목 (2026-08-05). 템플릿 미사용(위 주석 참고). ---
    "cash_and_equivalents": "현금및현금성자산(순액)",
    "trade_receivables": "매출채권(순액)",
    "interest_expense": "이자비용",
    "depreciation": "감가상각비(현금흐름표)",
    "amortization": "무형자산상각비",
}

# 위 매핑 중 **템플릿이 실제로 읽는** 13항목의 컬럼명. 세부계정 5항목처럼 "값만 싣고
# 템플릿은 아직 안 쓰는" 키가 늘어날 때, 연도 선별(`RATIO_REQUIRED_KEYS`)이나 등급
# 계산이 그 키에 끌려가지 않는지 테스트가 대조하기 위한 기준선이다.
TEMPLATE_USED_SNAPSHOT_FIELDS: frozenset[str] = frozenset(
    {
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
    }
)

# 템플릿이 **계산에 쓰는** 항목(하나라도 null이면 그 연도를 통째로 제외한다).
#
# 근거는 템플릿 코드다 — `calcRatios()`(매출총이익률/영업이익률/순이익률/부채비율/
# 유동비율/자기자본비율)의 분자·분모 9항목 + 마지막 연도 자본구성 도넛차트가 쓰는
# `비유동부채`. 이 목록에 없는 3항목(`비유동자산`/`매출원가`/`판관비`)은 템플릿의
# 어떤 계산에도 쓰이지 않으므로 결측이어도 그 연도를 버리지 않는다.
RATIO_REQUIRED_KEYS: tuple[str, ...] = (
    "유동자산",
    "자산총계",
    "유동부채",
    "비유동부채",
    "부채총계",
    "자본총계",
    "매출액",
    "매출총이익",
    "영업이익",
    "당기순이익",
)

# 위 항목 중 **분모**로 쓰이는 것들 — 0이면 `Infinity`가 인쇄되므로 null과 동일하게
# "그 연도 제외" 사유로 본다(`매출액`은 증감률 계산의 분모이기도 하다).
RATIO_DENOMINATOR_KEYS: frozenset[str] = frozenset(
    {"매출액", "자산총계", "자본총계", "유동부채"}
)

# 위 분모 중 **음수도 0과 똑같이 "그 연도 제외" 사유**로 보는 것들
# (2026-08-03, dart-qa 3차 리뷰 Medium-3).
#
# 분모가 음수면 비율의 **부호가 통째로 뒤집혀** 등급 산정이 반대로 간다 — 실측
# `result_id=6704`의 2021년(매출액 -5,409,049,845 / 영업이익 -1,705,664,600)은
# 영업이익률이 **+31.5%** 로 계산돼 수익성 등급이 A로 반전됐고, 같은 문서의 KPI에는
# "매출액(2021) -5,409백만원"이 함께 인쇄돼 서술이 모순됐다. 음수 매출액/자산총계/
# 유동부채는 정상적인 재무 상태가 아니라 파싱 오류나 특수 회계처리이므로 결측과
# 동일하게 다룬다.
#
# **`자본총계`는 일부러 제외한다** — 완전자본잠식(자본총계 < 0)은 실제로 존재하는
# 정상적인(=파싱이 맞는) 상태이고, 그런 회사도 "보고서는 생성하되 등급만 정확히 D로"
# 인쇄하는 것이 H4/H4-2에서 확정한 방침이다. 여기에 자본총계를 넣었다면 개발 DB 실측
# 기준 스냅샷 469행이 사라지고 **전 연도가 자본잠식인 회사 66곳은 보고서 자체가
# 생성되지 않아**(쓸 수 있는 연도 0건 → skip) 그 방침과 정면으로 충돌한다.
# 자본총계 == 0(Infinity 방지)만 기존대로 걸러진다.
#
# 이 가드로 실제 제외되는 범위는 좁다 — 개발 DB 전체에서 음수 분모 스냅샷은 2행
# (result_id 6691/2024, 6704/2021)뿐이고, 그 때문에 보고서가 사라지는 회사는 0곳이다.
RATIO_POSITIVE_DENOMINATOR_KEYS: frozenset[str] = frozenset(
    {"매출액", "자산총계", "유동부채"}
)

# ---------------------------------------------------------------------------
# 비교군(peers / industryAverage / regionGroup) 파라미터 — 2026-08-04
# ---------------------------------------------------------------------------
#
# 비교군 후보는 **같은 Job에서 이미 수집한 다른 회사들**뿐이다(신규 API 호출 0건).
# 회사 1건이 비교군에 실을 수 있는 재무값은 그 회사 자신의 `select_financial_rows()`
# 결과 중 **가장 최근 연도 1개**(= 대상 회사가 KPI/차트에 쓰는 것과 같은 "최근 연도")다.

# 업종 매칭에 쓰는 `results.induty_code` prefix 길이 — 소분류(3자리) 우선, 표본이
# 모자라면 중분류(2자리)로 넓힌다. **더 넓히지 않는다**(대분류까지 가면 "동종업종"
# 이라는 말이 무의미해진다 — CLAUDE.md의 세분류 prefix 매칭 누락 경험과 같은 맥락).
INDUSTRY_PREFIX_LENGTHS: tuple[int, ...] = (3, 2)

# 자기 자신을 제외하고 이만큼도 안 모이면 비교군을 **아예 싣지 않는다**(빈 값).
# 1개사만 놓고 "동종업종 비교"라 부르면 대외 문서로서 오해를 준다.
MIN_COMPARISON_SAMPLE = 2

# 우편 발송용 3페이지 문서에 표/차트로 들어가므로 개수를 자른다. 자르는 기준은
# **대상 회사와 매출액 차이(절대값)가 작은 순** — 규모가 비슷한 회사가 먼저 남는다.
MAX_PEERS = 8
MAX_REGION_GROUP = 16  # 대상 회사 1건 포함(= 다른 회사 최대 15건)

# 재무데이터 신뢰성이 낮다고 보는 감사의견(2026-08-04, dart-qa 리뷰 M3, 사용자 확정).
#
# **`한정`은 일부러 넣지 않는다** — 실무상 흔한 의견이라 뺐다가는 표본만 크게 줄고,
# 한정의견의 재무수치는 "특정 사유를 제외하고는 공정하게 표시"라 비교군으로 쓸 수 있다.
# 반면 의견거절/부적정은 재무제표 전체의 신뢰성이 부정된 것이라 비교 대상이 못 된다
# (템플릿 250-252행의 자체 규약과 같은 취지).
#
# 두 비교군에서 **적용 방식이 다르다** — 템플릿의 null 처리 수준이 다르기 때문이다:
#   - `regionGroup`: 템플릿이 `regionGroup.filter(r=>r.매출총이익률!=null)`로 실제
#     걸러내므로 `_region_row()`에서 금액/비율을 `None`으로 두면 순위 차트에서 빠지고
#     표에는 "-"로 인쇄된다(= 회사는 목록에 남되 수치만 감춘다).
#   - `peers`: 템플릿 `chartPeer` 렌더가 **null 체크 없이** `p.매출총이익률*100`을
#     곱한다 — JS에서 `null*100`은 `0`이라 "0.0%"라는 **틀린 값이 조용히** 찍힌다
#     (제외가 아니라 왜곡). 그래서 필드 null이 아니라 `select_peers()`에서 후보를
#     통째로 뺀다(상한 절단보다 먼저 → `industryAverage` 가중평균에도 안 섞인다).
UNRELIABLE_OPINIONS: frozenset[str] = frozenset({"의견거절", "부적정"})

# 비교군 후보로 받아들일 비율의 절대값 상한(2026-08-04, dart-qa 리뷰 M2, 사용자 확정).
#
# 매출총이익률/영업이익률이 ±100%를 벗어난 회사는 **후보 풀 단계에서 통째로 뺀다**.
# 파싱은 맞는데(실측 `근하하이테크산업`: 매출 155백만 / 매출원가 3,035백만,
# `parse_status=OK`) 비율이 **-1857.9%** 라 지역 순위 차트(`renderHBarChart`)의 축이
# 그 한 건에 맞춰 늘어나면서 **대상 회사 막대가 7px로 뭉개지고 나머지는 전부 100%
# 동률처럼** 보였다. 정렬 기준(peers/regionGroup)마다 따로 거르지 않고 후보 풀에서
# 한 번만 거르는 이유는 두 비교군에 같은 기준이 적용돼야 하기 때문이다.
#
# **대상 회사 자신에게는 적용하지 않는다** — 대상 회사는 있는 그대로 보고서에 실린다
# (`build_peer_candidate()`는 풀을 거치지 않고 `build_report_payload()`가 직접 만든다).
# 경계값(정확히 ±100%)은 포함한다.
MAX_COMPARISON_ABS_RATIO = 1.0
MAX_COMPARISON_ABS_RATIO_KEYS: tuple[str, ...] = ("매출총이익률", "영업이익률")

# 감사의견을 확보하지 못한 회사가 지역 비교군 표에 실릴 때 쓰는 표기(2026-08-04, L5).
# 템플릿이 `<span class="opinion-tag ${r.opinion}">${r.opinion}</span>`로 찍어 빈
# 문자열이면 **빈 태그**만 남는다(실측 76개 대상 문서에 영향). 대상 회사에도 같은
# 규칙을 적용한다 — 이 표의 셀 값만 보정하는 것이고 `company.opinion`은 무변경이다.
UNKNOWN_OPINION_LABEL = "미상"

# 비교군 PARTIAL 경고에 이름을 몇 개까지 나열할지(2026-08-04, L2). 나머지는 "외 N곳".
# 경고 한 줄이 화면(모달)을 넘치게 만들면 정작 회사별 다른 경고가 묻힌다.
MAX_WARNING_NAME_SAMPLES = 3

# Windows 파일명에 쓸 수 없는 문자 + 제어문자.
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
# Windows 예약 파일명(대소문자 무관, 확장자가 붙어도 예약이다).
_RESERVED_FILENAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_MAX_STEM_LENGTH = 100


class ReportGenerationError(Exception):
    """파일 저장 실패/템플릿 부재 등, 사용자에게 그대로 보여줄 수 있는 오류.

    API 계층이 이 메시지를 그대로 응답 `detail`에 담는다(스택트레이스가 그대로
    500으로 새어 나가지 않게 하기 위함).
    """


@dataclass(frozen=True)
class ReportInput:
    """회사 1건 분량의 보고서 입력(결과 행 + 그 회사의 연도별 재무이력).

    `snapshots` 원소는 속성 접근(`getattr`)만 하므로 ORM(`FinancialSnapshot`)과
    API 응답 모델(`FinancialSnapshotResponse`) 어느 쪽이든 그대로 넘길 수 있다 —
    호출부가 기존 재무이력 조회 함수의 반환값을 변환 없이 재사용하기 위함이다.
    """

    result: Any  # app.models.result.Result
    snapshots: Sequence[Any] = ()  # 연도 오름차순


@dataclass(frozen=True)
class GeneratedReportFile:
    result_id: int | None
    corp_name: str | None
    filename: str


@dataclass(frozen=True)
class ReportWarning:
    result_id: int | None
    corp_name: str | None
    message: str


@dataclass(frozen=True)
class SkippedReport:
    """보고서를 만들지 않고 건너뛴 회사(현재 사유는 "쓸 수 있는 재무연도 0건" 하나뿐)."""

    result_id: int | None
    corp_name: str | None
    reason: str


@dataclass
class ReportGenerationOutcome:
    output_dir: Path
    files: list[GeneratedReportFile] = field(default_factory=list)
    label_filename: str = LABEL_FILENAME
    warnings: list[ReportWarning] = field(default_factory=list)
    skipped: list[SkippedReport] = field(default_factory=list)


@dataclass
class FinancialSelection:
    """`financials`에 실을 연도와, 실지 못했거나 검수가 필요한 연도의 분류 결과.

    `rows`만 템플릿에 들어가고 나머지 목록은 전부 `warnings` 문구의 재료다.
    """

    # 템플릿에 실제로 싣는 연도(연도 오름차순, 비율 계산 항목이 모두 있는 연도만)
    rows: list[dict[str, Any]] = field(default_factory=list)
    # 입력으로 받은 전체 연도 수(제외 몇 개년/전체 몇 개년을 안내하기 위함)
    total_years: int = 0
    # parse_status=FAILED라 제외한 연도
    failed_years: list[str] = field(default_factory=list)
    # 필수 항목 결측/0분모라 제외한 연도 -> (연도, 사유 항목들)
    incomplete_years: list[tuple[str, list[str]]] = field(default_factory=list)
    # 실었지만 parse_status=PARTIAL인 연도
    partial_years: list[str] = field(default_factory=list)
    # 실었지만 다음 연도 공시의 전기 열에서 온 값인 연도(from_current_period=0)
    prior_period_years: list[str] = field(default_factory=list)

    @property
    def excluded_years(self) -> list[str]:
        return [*self.failed_years, *(year for year, _ in self.incomplete_years)]


# ---------------------------------------------------------------------------
# 템플릿 로드
# ---------------------------------------------------------------------------


def resolve_template_path() -> Path:
    """보고서 템플릿 HTML의 실제 경로를 찾는다(찾지 못하면 예외).

    탐색 순서:
      1. `Settings.report_template_path` (명시 지정 시 그 경로만 본다)
      2. `DART_SEARCH_RESOURCE_DIR/tamplate/` — PyInstaller 번들에 동봉된 경우
      3. `BACKEND_DIR/tamplate/` — exe 옆에 사용자가 직접 둔 경우
      4. `BACKEND_DIR/../tamplate/` — 개발 저장소 루트(기본 위치)
    """
    settings = get_settings()
    configured = (settings.report_template_path or "").strip()
    if configured:
        path = Path(configured)
        if not path.is_file():
            raise ReportGenerationError(
                f"보고서 템플릿 파일을 찾을 수 없습니다: {path}"
                " (.env의 REPORT_TEMPLATE_PATH를 확인하세요)"
            )
        return path

    candidates: list[Path] = []
    resource_dir = os.environ.get("DART_SEARCH_RESOURCE_DIR")
    if resource_dir:
        candidates.append(Path(resource_dir) / TEMPLATE_DIR_NAME / TEMPLATE_FILENAME)
    candidates.append(BACKEND_DIR / TEMPLATE_DIR_NAME / TEMPLATE_FILENAME)
    candidates.append(BACKEND_DIR.parent / TEMPLATE_DIR_NAME / TEMPLATE_FILENAME)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ReportGenerationError(
        "보고서 템플릿 파일(" + TEMPLATE_FILENAME + ")을 찾을 수 없습니다. "
        "다음 위치를 확인하세요: " + ", ".join(str(c) for c in candidates)
    )


def load_template_text() -> str:
    """템플릿 HTML 원문을 문자열로 읽는다."""
    path = resolve_template_path()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # 권한/손상 등
        raise ReportGenerationError(
            f"보고서 템플릿 파일을 읽을 수 없습니다: {path} ({exc})"
        ) from exc


# ---------------------------------------------------------------------------
# EMBEDDED_DATA 치환
# ---------------------------------------------------------------------------

_EMBEDDED_MARKER = "const EMBEDDED_DATA"
# 실제 선언(줄 맨 앞의 `const EMBEDDED_DATA = {`)만 고르기 위한 패턴. 템플릿 상단
# 사용법 주석이 이 이름을 언급해도 그 쪽을 잡지 않게 한다(주석은 들여쓰기/앞말이
# 붙어 있어 줄 시작 위치에 오지 않는다). 못 찾으면 단순 문자열 탐색으로 폴백한다.
_EMBEDDED_DECL_RE = re.compile(r"^const\s+EMBEDDED_DATA\s*=\s*\{", re.MULTILINE)

# `<script>` 안에 그대로 넣으면 위험한 문자 -> JS/JSON 이스케이프.
#   `<`         : 값에 `</script>`가 섞이면 HTML 파서가 스크립트를 조기 종료시킨다.
#   U+2028/2029 : 구형 JS 파서가 문자열 리터럴 안의 실제 개행으로 취급해 SyntaxError.
# JSON 출력에서 이 문자들은 **문자열 리터럴 안에서만** 나올 수 있으므로 전역 치환이 안전하다.
_JS_UNSAFE_CHARS: dict[str, str] = {
    "<": "\\u003c",
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
}


def find_embedded_data_block(text: str) -> tuple[int, int]:
    """`const EMBEDDED_DATA = {...};` 블록의 `[시작, 끝)` 인덱스를 돌려준다.

    시작은 `const` 키워드 위치, 끝은 객체 리터럴을 닫는 `}` 다음(뒤따르는 `;`
    포함) 위치다. 문자열 리터럴('/"/`)과 주석(`//`, `/* */`) 안의 중괄호는
    건너뛰므로, 템플릿 주석에 `{`가 들어가도 잘못 끊기지 않는다.
    """
    declaration = _EMBEDDED_DECL_RE.search(text)
    start = declaration.start() if declaration else text.find(_EMBEDDED_MARKER)
    if start == -1:
        raise ReportGenerationError(
            "템플릿에서 EMBEDDED_DATA 블록을 찾지 못했습니다 — 템플릿 파일이 손상됐거나"
            " 다른 파일일 수 있습니다."
        )
    brace = text.find("{", start)
    if brace == -1:
        raise ReportGenerationError("템플릿의 EMBEDDED_DATA 블록이 비정상입니다(여는 중괄호 없음).")

    i = brace
    depth = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            newline = text.find("\n", i)
            i = n if newline == -1 else newline + 1
            continue
        if ch == "/" and nxt == "*":
            close = text.find("*/", i + 2)
            i = n if close == -1 else close + 2
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                # 뒤따르는 공백/세미콜론까지 블록에 포함시킨다.
                while end < n and text[end] in " \t":
                    end += 1
                if end < n and text[end] == ";":
                    end += 1
                return start, end
        i += 1

    raise ReportGenerationError("템플릿의 EMBEDDED_DATA 블록이 닫히지 않았습니다(중괄호 불일치).")


def serialize_embedded_data(payload: dict[str, Any]) -> str:
    """보고서 데이터를 `<script>` 안에 그대로 넣어도 안전한 JS 리터럴로 직렬화.

    JSON은 JS 객체 리터럴의 부분집합이라 그대로 쓸 수 있지만, 두 가지를 더 막는다:

    - `<`를 `\\u003c`로 escape — 회사명 등에 `</script>` 문자열이 섞이면 HTML
      파서가 스크립트를 조기 종료시켜 페이지 전체가 깨진다. JSON 출력에서 `<`는
      **문자열 리터럴 안에서만** 나올 수 있으므로 전역 치환이 안전하다.
    - U+2028/U+2029(줄 구분자)를 escape — 구형 JS 파서에서 문자열 리터럴 안의
      실제 개행으로 취급돼 SyntaxError가 난다.
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    for raw, escaped in _JS_UNSAFE_CHARS.items():
        text = text.replace(raw, escaped)
    return text


def render_report_html(template_text: str, payload: dict[str, Any]) -> str:
    """템플릿의 EMBEDDED_DATA 블록만 실제 데이터로 교체한 HTML 문자열을 만든다."""
    start, end = find_embedded_data_block(template_text)
    replacement = f"{_EMBEDDED_MARKER} = {serialize_embedded_data(payload)};"
    return template_text[:start] + replacement + template_text[end:]


# ---------------------------------------------------------------------------
# 보고서 데이터 조립
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    """템플릿이 문자열 보간으로 그대로 찍는 값 — None은 빈 문자열로."""
    return "" if value is None else str(value)


def _year_value(fiscal_year: Any) -> Any:
    """회계연도 문자열("2023")을 숫자로. 숫자가 아니면 원문 그대로 둔다."""
    text = _text(fiscal_year).strip()
    return int(text) if text.isdigit() else text


def _year_sort_key(year: Any) -> tuple[bool, Any]:
    """숫자 연도 먼저, 파싱 불가한 문자열 연도는 뒤로(둘을 직접 비교하면 TypeError)."""
    return (isinstance(year, str), year)


def _snapshot_to_row(snapshot: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"year": _year_value(getattr(snapshot, "fiscal_year", None))}
    for column, key in SNAPSHOT_FIELD_TO_REPORT_KEY.items():
        row[key] = getattr(snapshot, column, None)
    return row


def build_financial_rows(snapshots: Sequence[Any]) -> list[dict[str, Any]]:
    """`financial_snapshots` 목록을 템플릿 `financials[]`(연도 오름차순)로 변환.

    호출부가 이미 연도 오름차순으로 넘기지만, 여기서도 `year` 기준으로 한 번 더
    정렬해 둔다(템플릿이 "과거→최근" 순서를 전제로 증감률을 계산하기 때문).
    **결측 연도를 걸러내지 않는 순수 매핑**이다 — 실제 보고서에 실을 연도는
    `select_financial_rows()`가 고른다.
    """
    rows = [_snapshot_to_row(snapshot) for snapshot in snapshots]
    rows.sort(key=lambda r: _year_sort_key(r["year"]))
    return rows


def _parse_status_text(value: Any) -> str:
    """`parse_status`를 대문자 문자열로 정규화(ORM/응답모델/None 모두 수용)."""
    if value is None:
        return ""
    return str(getattr(value, "value", value)).strip().upper()


def missing_ratio_inputs(row: dict[str, Any]) -> list[str]:
    """그 연도를 보고서에 실을 수 없게 만드는 항목 목록(비면 실을 수 있다).

    - 필수 항목이 `null`   -> 템플릿 비율 계산이 `NaN`이 된다.
    - 분모 항목이 `0`      -> 템플릿 비율 계산이 `Infinity`가 된다.
    - 분모 항목이 **음수** -> 비율의 부호가 뒤집혀 등급이 반대로 나온다
      (`RATIO_POSITIVE_DENOMINATOR_KEYS`. **`자본총계`는 여기 없다** — 자본잠식은
      실재하는 상태라 연도를 버리지 않고 템플릿 가드로 D 등급을 확정한다).
    """
    missing: list[str] = []
    for key in RATIO_REQUIRED_KEYS:
        value = row.get(key)
        if value is None:
            missing.append(key)
        elif key in RATIO_POSITIVE_DENOMINATOR_KEYS and value <= 0:
            missing.append(key)
        elif key in RATIO_DENOMINATOR_KEYS and value == 0:
            missing.append(key)
    return missing


def select_financial_rows(snapshots: Sequence[Any]) -> FinancialSelection:
    """연도별 스냅샷에서 **보고서에 실을 수 있는 연도만** 골라낸다.

    제외 사유는 두 가지뿐이고, 어느 쪽이든 `FinancialSelection`에 연도가 남아
    경고 문구로 사용자에게 전달된다:

      1. `parse_status == FAILED` — 검수 대상 값을 대외 문서에 싣지 않는다.
      2. 비율 계산 필수 항목 결측/0분모 — 실으면 `NaN%`/`Infinity%`가 인쇄되고
         등급 산정이 "데이터가 없을수록 좋은 등급" 방향으로 고장난다.
    """
    pairs = [(_snapshot_to_row(snapshot), snapshot) for snapshot in snapshots]
    pairs.sort(key=lambda pair: _year_sort_key(pair[0]["year"]))

    selection = FinancialSelection(total_years=len(pairs))
    for row, snapshot in pairs:
        year_label = str(row["year"])
        status = _parse_status_text(getattr(snapshot, "parse_status", None))
        if status == ParseStatus.FAILED:
            selection.failed_years.append(year_label)
            continue
        missing = missing_ratio_inputs(row)
        if missing:
            selection.incomplete_years.append((year_label, missing))
            continue

        selection.rows.append(row)
        if status == ParseStatus.PARTIAL:
            selection.partial_years.append(year_label)
        # `from_current_period`가 없는 객체(구 스텁 등)는 판정하지 않는다.
        from_current = getattr(snapshot, "from_current_period", None)
        if from_current is not None and int(from_current) == 0:
            selection.prior_period_years.append(year_label)
    return selection


# ---------------------------------------------------------------------------
# 비교군 (peers / industryAverage / regionGroup) — 2026-08-04
# ---------------------------------------------------------------------------
#
# 소스는 **같은 Job의 다른 `results` 행**뿐이다. 후보가 되려면
#
#   1. `excluded_by_stale_disclosure != 1` — 휴면·폐업 추정 회사는 화면에서도 기본
#      숨김이라 비교군으로 쓰면 "동종업계 평균"이 죽은 회사로 오염된다.
#      (`excluded_by_revenue`/`excluded_by_assets`는 **제외하지 않는다** — 검색 조건인
#      규모 범위를 벗어났을 뿐 정상 영업 중인 회사라 비교군으로는 오히려 유효하다.)
#   2. `select_financial_rows()` 결과가 1개년 이상 — 대상 회사가 "쓸 수 있는 연도
#      0건이면 생성 스킵"인 것과 **같은 판단 기준**을 후보에도 그대로 적용한다.
#      결측/0·음수 분모 연도가 비교군 비율에 섞이면 NaN/부호 반전이 표에 인쇄된다.
#   3. 매출총이익률·영업이익률이 **±100% 이내**(2026-08-04) — 파싱은 맞지만 비율이
#      비현실적으로 극단적인 회사 한 건이 순위 차트의 축을 통째로 끌고 간다
#      (`MAX_COMPARISON_ABS_RATIO` 주석의 실측 사례 참고).
#
# 를 만족해야 한다. 여기에 더해 **감사의견이 의견거절/부적정인 회사**는 비교 수치로
# 쓰지 않는다 — 다만 적용 지점이 비교군마다 다르다(`UNRELIABLE_OPINIONS` 주석):
# peers는 후보에서 통째로 빼고(`select_peers()`), regionGroup은 행은 남기되 금액/비율만
# `None`으로 둔다(`_region_row()`). 한정의견은 제외 대상이 아니다.
#
# **업종 매칭(`match_by_industry_prefix()`)은 peers와 regionGroup 양쪽에 똑같이 적용된다**
# (2026-08-05 — regionGroup은 그전까지 "같은 Job이면 업종 무관"이었다). 규칙이 갈리지
# 않도록 함수 하나만 두고 두 곳이 같이 호출한다.
#
# 비율 계산식은 템플릿 `calcRatios()`와 **반드시 동일**해야 한다 —
# 같은 문서 안에서 대상 회사(템플릿이 계산)와 비교군(여기서 계산)의 수치가 다른 식으로
# 나오면 순위 차트가 조용히 어긋난다.


@dataclass(frozen=True)
class PeerCandidate:
    """비교군 후보 1건 = 회사 1곳의 **최근 연도** 요약(순수 값 객체).

    `year`는 그 회사 자신의 최근 사용 가능 연도라 **대상 회사와 다를 수 있다**
    (실측: peer 짝의 9.2%가 다르고 최대 4년 차) — 그래서 각주 문구에 기준 연도를
    명시한다(`build_comparison_year_note()`).

    `partial`은 그 연도 스냅샷이 `parse_status=PARTIAL`이었는지다(2026-08-04, L2).
    대상 회사 자신의 PARTIAL 연도는 `collect_warnings()`가 경고로 남기는데 비교군
    후보의 PARTIAL은 조용히 쓰이고 있었다 — `collect_comparison_warnings()`가 이
    플래그로 회사당 한 줄 경고를 남긴다(후보를 빼지는 않는다. 검수 대상일 뿐
    값 자체가 없는 것은 아니고, 빼면 표본이 더 줄어든다).
    """

    result_id: int | None
    name: str
    industry_code: str
    industry_name: str
    opinion: str
    year: Any
    revenue: float
    gross_profit: float
    ratios: dict[str, float]
    partial: bool = False


@dataclass(frozen=True)
class PeerPool:
    """한 Job 전체의 비교군 후보 목록.

    **요청당 한 번만** 만든다(`app/api/results.py`가 Job 전체를 벌크 조회해 생성) —
    선택 회사마다 후보를 다시 모으면 N+1 쿼리가 된다.
    """

    candidates: tuple[PeerCandidate, ...] = ()


EMPTY_PEER_POOL = PeerPool()


def compute_report_ratios(row: dict[str, Any]) -> dict[str, float] | None:
    """템플릿 `calcRatios()`와 **같은 식**으로 비교군에 쓸 비율 4종을 계산한다.

        매출총이익률 = 매출총이익 / 매출액
        영업이익률   = 영업이익  / 매출액
        부채비율     = 부채총계  / 자본총계
        자기자본비율 = 자본총계  / 자산총계

    분모 가드는 `select_financial_rows()`의 `RATIO_POSITIVE_DENOMINATOR_KEYS`와
    **같은 비대칭 규칙**이다(2026-08-04, dart-qa 리뷰 L3에서 강화):

      - `매출액`/`자산총계`: 결측·0 **및 음수**면 `None`. 분모가 음수면 비율의 부호가
        통째로 뒤집혀 "매출액이 마이너스인데 수익성 A"가 나온다.
      - `자본총계`: 결측·0이면 `None`이지만 **음수는 그대로 계산한다** — 완전자본잠식은
        실재하는 정상 파싱 결과이고, 그 연도를 버리면 자본잠식 회사가 보고서 자체를
        못 받는다(모듈 docstring 예외 ①~③의 방침).

    이전 구현은 `not revenue or not equity or not assets`(truthy 검사)라 **음수가
    그대로 통과**했다. 지금은 `select_financial_rows()`를 거친 행에만 쓰여 실 피해가
    없었지만, 이 함수를 다른 경로에서 부르면 그대로 재발하는 유형이라(이 프로젝트가
    자본잠식으로 세 번 겪은 바로 그 버그) 여기서도 명시적으로 막는다.
    """
    revenue = row.get("매출액")
    equity = row.get("자본총계")
    assets = row.get("자산총계")
    values = (
        revenue,
        equity,
        assets,
        row.get("매출총이익"),
        row.get("영업이익"),
        row.get("부채총계"),
    )
    if any(v is None for v in values):
        return None
    if revenue <= 0 or assets <= 0 or equity == 0:
        return None
    return {
        "매출총이익률": row["매출총이익"] / revenue,
        "영업이익률": row["영업이익"] / revenue,
        "부채비율": row["부채총계"] / equity,
        "자기자본비율": equity / assets,
    }


def _is_stale_disclosure(result: Any) -> bool:
    """휴면·폐업 추정(`excluded_by_stale_disclosure=1`) 여부. NULL/없음은 False."""
    value = getattr(result, "excluded_by_stale_disclosure", None)
    try:
        return int(value or 0) == 1
    except (TypeError, ValueError):
        return False


def is_unreliable_opinion(opinion: Any) -> bool:
    """감사의견이 의견거절/부적정인가(= 비교 수치로 쓰지 않는다).

    `한정`은 **포함하지 않는다**(`UNRELIABLE_OPINIONS` 주석 참고). 값 자체는
    `app/parsers/audit_opinion.py`의 `AUDIT_OPINION_VALUES`로 정규화돼 저장되지만,
    구 Job 행에 접미어("의견거절의견" 등)가 붙어 있을 수 있어 부분 문자열로 본다
    (`부적정`은 `적정`의 부분 문자열이 아니므로 이 방식이 안전하다).
    """
    text = _text(opinion).strip()
    if not text:
        return False
    return any(marker in text for marker in UNRELIABLE_OPINIONS)


def has_extreme_ratios(ratios: dict[str, float]) -> bool:
    """비율이 ±`MAX_COMPARISON_ABS_RATIO`(100%)를 **넘는가**(경계값은 정상으로 본다).

    비교군 후보 자격 판정 전용이다 — 대상 회사에는 쓰지 않는다.
    """
    return any(
        abs(ratios[key]) > MAX_COMPARISON_ABS_RATIO
        for key in MAX_COMPARISON_ABS_RATIO_KEYS
        if ratios.get(key) is not None
    )


def build_peer_candidate(result: Any, selection: FinancialSelection) -> PeerCandidate | None:
    """결과 1건 + 그 회사의 연도 선별 결과 -> 비교군 후보(못 쓰면 `None`).

    쓰는 연도는 **선별된 연도 중 가장 최근 1개**뿐이다 — 회사마다 최근 연도가 다를 수
    있어(파싱 실패/결측 연도를 걸러낸 뒤라 더욱 그렇다) 대상 회사와 시점이 어긋날 수
    있고, 그 사실은 각주(`build_comparison_year_note()`)로 문서에 남긴다.

    그 연도가 PARTIAL 파싱이었으면 `partial=True`로 표시해 둔다 — 후보에서 빼지는
    않고 경고(`collect_comparison_warnings()`)로만 알린다.
    """
    if not selection.rows:
        return None
    row = selection.rows[-1]
    ratios = compute_report_ratios(row)
    if ratios is None:
        return None
    return PeerCandidate(
        result_id=getattr(result, "id", None),
        name=_text(getattr(result, "corp_name", None)),
        industry_code=_text(getattr(result, "induty_code", None)).strip(),
        industry_name=_text(getattr(result, "induty_name", None)),
        opinion=_text(getattr(result, "audit_opinion", None)),
        year=row.get("year"),
        revenue=row["매출액"],
        gross_profit=row["매출총이익"],
        ratios=ratios,
        partial=str(row.get("year")) in set(selection.partial_years),
    )


def build_peer_pool(items: Iterable[ReportInput]) -> PeerPool:
    """Job 전체(또는 임의의 후보 집합)를 비교군 후보 풀로 만든다.

    호출부는 **선택된 회사가 아니라 Job 전체**를 넘겨야 한다 — 비교군은 사용자가
    체크한 회사가 아니라 "그 Job에서 수집한 회사들"이기 때문이다.
    """
    candidates: list[PeerCandidate] = []
    for item in items:
        if _is_stale_disclosure(item.result):
            continue
        candidate = build_peer_candidate(item.result, select_financial_rows(item.snapshots))
        if candidate is None:
            continue
        # 비율이 ±100%를 벗어난 후보는 peers/regionGroup **양쪽에서** 뺀다
        # (`MAX_COMPARISON_ABS_RATIO` 주석 — 차트 축이 한 건에 끌려가는 문제).
        if has_extreme_ratios(candidate.ratios):
            continue
        candidates.append(candidate)
    return PeerPool(candidates=tuple(candidates))


def _exclude_self(
    candidates: Iterable[PeerCandidate], target: PeerCandidate
) -> list[PeerCandidate]:
    """대상 회사 자신을 후보에서 뺀다(id가 없는 스텁은 객체 동일성으로도 본다)."""
    return [
        c
        for c in candidates
        if c is not target
        and not (target.result_id is not None and c.result_id == target.result_id)
    ]


def _closest_by_revenue(
    candidates: Sequence[PeerCandidate], revenue: float, limit: int
) -> list[PeerCandidate]:
    """대상 회사와 매출 규모가 가까운 순으로 `limit`건만 남긴다(동률은 id 오름차순)."""
    ordered = sorted(
        candidates,
        key=lambda c: (abs(c.revenue - revenue), c.result_id if c.result_id is not None else 0),
    )
    return ordered[: max(limit, 0)]


def match_by_industry_prefix(
    candidates: Sequence[PeerCandidate], industry_code: str
) -> list[PeerCandidate]:
    """`induty_code` prefix 매칭 — 소분류(3자리) → 중분류(2자리) **한 번만** 폴백.

    `peers`와 `regionGroup`이 **같은 함수**를 쓴다(2026-08-05). 두 곳에 같은 규칙을
    복사해 두면 한쪽만 고쳐져 "동종업종 표에는 있는데 지역 표에는 없는 회사"처럼
    같은 문서 안에서 기준이 갈린다 — 그래서 매칭 규칙은 여기 한 곳에만 둔다.

    **다만 "규칙 공유"가 "결과 일치"를 보장하지는 않는다**(2026-08-05 L3, 문서화만
    하고 로직은 그대로 둔다): 아래 두 호출부가 넘기는 **입력 목록이 다르므로**
    (peers는 의견거절/부적정을 먼저 뺀다) 소분류에서 표본 2건을 채우느냐가 갈려
    **선택되는 분류 단계(소분류 vs 중분류)가 서로 달라질 수 있다** — 그러면 peers
    차트에는 있는데 지역 표에는 없는 회사가 여전히 남는다. 개발 DB 실측(후보
    1,488건)으로 단계 불일치 6건, 그중 실제로 "peers에만 있는 회사"가 생기는 것은
    4건(0.27%)이다(업종 매칭 도입 전 1,421건에서 대폭 줄었지만 0은 아니다).

    소분류로 `MIN_COMPARISON_SAMPLE`건 미만이면 중분류로 한 번 넓히고, 그래도 모자라면
    **빈 목록**을 준다(에러가 아니라 정상 케이스다). **대분류까지는 넓히지 않는다** —
    거기까지 가면 "동종업종"이라는 말이 무의미해진다.

    `candidates`는 **호출부가 이미 자기 자신과 각 비교군의 자격 요건을 걸러 넘긴
    목록**이어야 한다. 최소 표본(2건) 판정이 그 목록 기준으로 이뤄지기 때문이다 —
    나중에 걸러낼 회사를 여기서 세면 "표본은 충분하다고 보고 폴백을 멈췄는데 정작
    표에는 한 곳만 남는" 상태가 된다. 두 호출부가 넘기는 목록이 다르다:
      - `select_peers()`      : 자기 자신 + **의견거절/부적정 제외** 후의 목록
      - `select_region_group()`: 자기 자신만 제외한 목록(의견거절/부적정은 행을 남기고
                                수치만 감추므로 표본으로 센다 — `_region_row()` 참고)

    **[알려진 제약, 2026-08-04 L4] `induty_code` 자릿수가 회사마다 2~5자리로 제각각이라
    이 prefix 매칭은 비대칭이다** — 2자리 회사는 소분류 매칭을 건너뛰고 곧장 중분류
    전체와 매칭되고(A는 B를 동종으로 보는데 B는 A를 안 보는 경우가 생긴다), 그래서
    "동종업종"의 해상도가 회사마다 다르다. CLAUDE.md의 "세분류 prefix 조용한 누락"과
    같은 성질의 구조적 제약이라 별도 업종 트리 재설계 없이는 해소되지 않는다 —
    자릿수를 임의로 맞추거나 대분류까지 넓히는 식으로 "고치지 말 것"(표본만 늘고
    동종업종이라는 말이 무의미해진다).
    """
    code = (industry_code or "").strip()
    if not code:
        return []
    for length in INDUSTRY_PREFIX_LENGTHS:
        if len(code) < length:
            continue
        prefix = code[:length]
        matched = [c for c in candidates if c.industry_code.startswith(prefix)]
        if len(matched) >= MIN_COMPARISON_SAMPLE:
            return matched
    return []


def select_peers(pool: PeerPool, target: PeerCandidate) -> list[PeerCandidate]:
    """동종업종 비교사 — `match_by_industry_prefix()`(소분류 → 중분류 폴백) + 매출 근접순.

    **의견거절/부적정 회사는 여기서 통째로 뺀다** — regionGroup처럼 값을 null로 두는
    방식이 peers에서는 통하지 않는다(`UNRELIABLE_OPINIONS` 주석 참고: 템플릿이
    `null*100 == 0`을 "0.0%"로 인쇄한다). 업종 매칭·상한 절단(`_closest_by_revenue`)
    보다 먼저 걸러야 `build_industry_average()`의 가중평균에도 섞이지 않고, 최소
    표본(2건) 판정도 "쓸 수 있는 후보" 기준으로 이뤄진다(부족하면 중분류 폴백이 정상
    작동).
    """
    others = [
        c for c in _exclude_self(pool.candidates, target) if not is_unreliable_opinion(c.opinion)
    ]
    matched = match_by_industry_prefix(others, target.industry_code)
    return _closest_by_revenue(matched, target.revenue, MAX_PEERS)


def build_peer_rows(peers: Sequence[PeerCandidate]) -> list[dict[str, Any]]:
    """템플릿 `peers[]` 형태로 변환(회사명은 DART 공시 기반 공개 정보라 실명 그대로)."""
    return [
        {
            "name": peer.name,
            "매출액": peer.revenue,
            "매출총이익률": peer.ratios["매출총이익률"],
            "영업이익률": peer.ratios["영업이익률"],
            "부채비율": peer.ratios["부채비율"],
            "자기자본비율": peer.ratios["자기자본비율"],
        }
        for peer in peers
    ]


def build_comparison_year_note(
    peers: Sequence[PeerCandidate], target: PeerCandidate
) -> str:
    """peers 각주에 붙일 **기준 연도** 문구(2026-08-04, dart-qa 리뷰 L1).

    비교군 후보의 재무값은 각 회사 자신의 "쓸 수 있는 최근 연도" 1개인데, 파싱 실패/
    결측 연도를 걸러낸 뒤라 회사마다 연도가 다를 수 있다 — 실측으로 peer 짝의 9.2%가
    대상 회사와 다른 회계연도였고 최대 4년까지 벌어졌다. 각주에 아무 표기가 없으면
    독자가 **같은 시점의 비교**로 읽는다.

    문구는 **템플릿을 고치지 않고** `industryAverage.설명`에 실어 보낸다 — 템플릿
    733행(`peerFootnote`)이 백엔드가 준 `설명` 문자열을 그대로 이어 붙여 찍기 때문이다
    (peer 이름 자체에 연도를 붙이면 차트 축 라벨까지 길어져 3페이지 문서가 깨진다).
    regionGroup 표에는 연도 컬럼이 없고 템플릿을 고쳐야만 넣을 수 있어 손대지 않았다.
    """
    labels = sorted({str(peer.year) for peer in peers if peer.year not in (None, "")})
    target_label = "" if target.year in (None, "") else str(target.year)
    if not labels:
        return "각 사 최근 결산연도 기준"
    span = labels[0] if len(labels) == 1 else f"{labels[0]}~{labels[-1]}"
    if target_label and set(labels) == {target_label}:
        return f"{target_label}년 기준"
    if not target_label:
        return f"비교사 최근 결산연도 {span}년 기준"
    return f"각 사 최근 결산연도 기준 · 대상 {target_label}년 / 비교사 {span}년"


def build_industry_average(
    peers: Sequence[PeerCandidate], target: PeerCandidate
) -> dict[str, Any] | None:
    """`peers` **만으로** 계산한 업종 평균 매출총이익률(대상 회사는 제외).

    비율끼리 단순 평균하지 않고 **매출액가중평균**(= 매출총이익 합 / 매출액 합)으로
    낸다 — 표본별 매출 규모 차이가 큰 비상장 외감법인 특성상 산술평균은 영세 회사
    한 곳의 극단적 비율에 통째로 끌려간다.

    이 값은 템플릿 `scoreProfitability()`의 가점/감점에도 쓰이므로, peers가 비면
    반드시 `None`이어야 한다(0으로 두면 "업종 평균이 0%"라는 잘못된 기준이 된다).

    `설명` 문구는 이 표본이 **업종 전체가 아니라 매출 규모가 비슷한 일부**임을 밝힌다
    (2026-08-04, dart-qa 리뷰 M1). peers는 `MAX_PEERS`(8건) 절단을 이미 거친 뒤라
    "동일 업종 8개사"라고만 쓰면 읽는 사람이 "그 업종에 8개사뿐"으로 오독한다.
    뒤에는 **기준 연도**(`build_comparison_year_note()`, L1)를 괄호로 덧붙인다 —
    회사마다 최근 연도가 달라 "동일 시점 비교"로 오독될 수 있기 때문이다.
    **계산 로직(가중평균 값)은 무변경이고 문구만 바꿨다.**

    `peers`가 비면 반드시 `None`이므로, 각주에 연도 표기가 필요한 경우
    (=peers가 실린 경우)는 항상 이 `설명`을 거친다.
    """
    if not peers:
        return None
    revenue_sum = sum(peer.revenue for peer in peers)
    if not revenue_sum:
        return None
    gross_sum = sum(peer.gross_profit for peer in peers)
    label = target.industry_name.strip() if target.industry_name else ""
    prefix = f"{label} 등 " if label else ""
    year_note = build_comparison_year_note(peers, target)
    description = (
        f"{prefix}매출 규모가 유사한 동종업종 {len(peers)}개사 매출액가중평균({year_note})"
    )
    return {"매출총이익률": gross_sum / revenue_sum, "설명": description}


def _region_row(candidate: PeerCandidate, *, is_target: bool) -> dict[str, Any]:
    """지역 비교군 1행. 의견거절/부적정 회사는 **금액/비율만 `None`** 으로 둔다.

    템플릿의 자체 규약이다(250-252행) — `regionGroup`은 렌더 시
    `filter(r=>r.매출총이익률!=null)`로 순위 차트에서 빠지고, 표에는 `fmtM`/`pct`가
    `null`을 `-`로 찍는다. 회사는 목록에 남되 신뢰할 수 없는 수치만 감추는 셈이다
    (peers는 이 방식이 통하지 않아 후보에서 통째로 뺀다 — `select_peers()` 참고).

    **대상 회사(`is_target`)에는 적용하지 않는다.** 대상 회사의 재무수치는 같은 문서의
    KPI·추이 차트·등급에 이미 그대로 인쇄되므로, 지역 표에서만 "-"로 가리면 한 장 안에서
    서술이 어긋나고(자본잠식 3건과 같은 유형의 모순) 순위 차트에서 대상 막대 강조가
    통째로 사라진다. M2의 비율 상하한 필터가 "대상 회사에는 적용하지 않는다"와 같은
    원칙이다 — 걸러내는 것은 **비교 상대**뿐이다.

    **감사의견이 비어 있으면 `UNKNOWN_OPINION_LABEL`("미상")로 채운다**(2026-08-04, L5)
    — 템플릿(751행)이 `<span class="opinion-tag ${r.opinion}">${r.opinion}</span>`로
    찍기 때문에 빈 문자열이면 **아무 글자도 없는 빈 태그**가 표에 남아 "데이터 없음"인지
    "의견 없음"인지 알 수 없다. 대상 회사(`is_target`)에도 같은 규칙을 적용한다 —
    이 표에 들어가는 값만 보정하는 것이고, `build_company_payload()`가 만드는
    `company.opinion`(문서 상단 회사 정보)은 **무변경**이다.
    """
    hide_values = not is_target and is_unreliable_opinion(candidate.opinion)
    return {
        "name": candidate.name,
        "industry": candidate.industry_name,
        "매출액": None if hide_values else candidate.revenue,
        "매출총이익률": None if hide_values else candidate.ratios["매출총이익률"],
        "opinion": _text(candidate.opinion).strip() or UNKNOWN_OPINION_LABEL,
        "isTarget": is_target,
    }


def select_region_candidates(pool: PeerPool, target: PeerCandidate) -> list[PeerCandidate]:
    """지역 비교군에 실을 **다른 회사들**(대상 회사 제외 + 동종업종만)을 고른다.

    `select_region_group()`이 표 행으로 변환하기 전 단계이며, 경고 집계
    (`collect_comparison_warnings()`)도 같은 목록을 봐야 하므로 함수로 분리해 뒀다 —
    두 곳이 각자 고르면 "경고에는 있는데 표에는 없는 회사"가 생긴다.

    필터 순서(2026-08-05에 ②가 추가됐다):

      ① 자기 자신 제외(`_exclude_self`)
      ② **업종 매칭**(`match_by_industry_prefix`, 소분류 → 중분류 1회 폴백)
      ③ 최소 표본(2건) 미달이면 빈 목록
      ④ 매출 근접순 상한 절단(`MAX_REGION_GROUP - 1`)

    ②를 ③④ **앞에** 두는 이유는 두 가지다. (a) 최소 표본과 소분류→중분류 폴백 판정은
    "실제로 표에 실릴 회사" 기준이어야 한다 — 뒤에 걸러낼 회사를 세면 소분류로 충분한
    줄 알고 폴백을 멈췄는데 정작 표에는 한 곳만 남는다. (b) 상한 15건을 매출 근접순으로
    먼저 자르면 그 15칸이 타업종으로 채워져 업종 필터가 사실상 무력해진다.

    반면 **감사의견 의견거절/부적정 회사는 여기서 빼지 않는다**(peers와 다른 점) —
    regionGroup은 그 회사들의 행을 남기고 금액/비율만 감추는 방식이라(`_region_row()`)
    표본으로도 그대로 센다. 기존 규칙(휴면·폐업 추정 제외·±100% 비율 상한은 풀 단계,
    "미상" 표기·대상 회사 첫머리·매출 근접순 상한)은 전부 무변경이고 업종 필터만
    얹었다.
    """
    others = _exclude_self(pool.candidates, target)
    matched = match_by_industry_prefix(others, target.industry_code)
    if len(matched) < MIN_COMPARISON_SAMPLE:
        return []
    return _closest_by_revenue(matched, target.revenue, MAX_REGION_GROUP - 1)


def select_region_group(pool: PeerPool, target: PeerCandidate) -> list[dict[str, Any]]:
    """지역 비교군 — 같은 Job(=같은 지역) 안에서 **업종까지 같은** 회사들만 싣는다.

    Job의 검색 조건 자체가 지역이라(§4-9) Job 전체가 곧 "같은 지역"이고, 여기에
    **업종 매칭이 2026-08-05에 추가됐다** — 그전에는 지역만 같으면 됐기 때문에 "비교군
    상세 현황" 표에 조선부품·시내버스운송·도장공사·상품중개가 한 회사의 문서에 나란히
    실려, 매출총이익률 순위를 나열해도 비교의 의미가 없다는 지적을 사용자가 실제 생성
    문서를 보고 제기했다. 매칭 규칙은 peers와 **완전히 같은 함수**를 쓴다
    (`match_by_industry_prefix()`: 소분류 3자리 → 중분류 2자리 1회 폴백).

    대상 회사는 규모와 무관하게 **항상 `isTarget: true`로 배열 첫머리**에 들어가고
    (템플릿이 순위 차트에서 이 항목을 강조 색으로 칠한다), 나머지는 매출 규모가 가까운
    순이다. 동종업종 후보가 `MIN_COMPARISON_SAMPLE`건 미만이면 **빈 목록**을 준다 —
    peers가 표본 부족일 때 빈 값을 주는 것과 같은 기준이고, 템플릿도 빈 배열에 안전하다
    (1개사만 놓고 "지역 내 순위"라고 인쇄하면 대외 문서로서 오해를 준다). 개발 DB
    실측으로 이 경우는 후보 1,488건 중 63건(4.2%)이고, 그때는 템플릿이 순위 차트·상세
    표 대신 안내 문구를 보인다(2026-08-05 템플릿 예외 ⑤ — HTML/CSS만 수정).

    **단, 이 "2건" 게이트가 곧 "비교 막대 2개"는 아니다**(2026-08-05 L2, 문서화만):
    의견거절/부적정 회사는 표본으로는 세지만 `_region_row()`가 금액/비율을 `None`으로
    두고 템플릿 735행이 `filter(r=>r.매출총이익률!=null)`로 걸러내므로, **표본 2건을
    채우고도 순위 차트에는 비교사가 1곳만 그려질 수 있다**(개발 DB 실측 6건, 0.4%).
    표에는 두 곳 다 남으므로(수치만 "-") 차트와 표의 건수가 다른 것은 정상이다.

    표에 **회계연도 컬럼이 없다** — 후보마다 기준 연도가 다를 수 있지만(L1) 컬럼을
    추가하려면 템플릿 `<thead>`와 행 렌더를 고쳐야 해 손대지 않았다. 연도 표기는
    peers 각주에만 있다.
    """
    chosen = select_region_candidates(pool, target)
    if not chosen:
        return []
    return [
        _region_row(target, is_target=True),
        *(_region_row(c, is_target=False) for c in chosen),
    ]


def build_company_payload(result: Any) -> dict[str, Any]:
    """`results` 1행을 템플릿 `company` 객체로 매핑."""
    return {
        "name": _text(getattr(result, "corp_name", None)),
        "industry": _text(getattr(result, "induty_name", None)),
        "address": _text(getattr(result, "address", None)),
        "ceo": _text(getattr(result, "ceo_name", None)),
        "fiscalDate": _text(getattr(result, "fiscal_date", None)),
        "opinion": _text(getattr(result, "audit_opinion", None)),
        "auditor": _text(getattr(result, "auditor_name", None)),
    }


def build_report_payload(
    item: ReportInput,
    selection: FinancialSelection | None = None,
    peer_pool: PeerPool | None = None,
) -> dict[str, Any]:
    """회사 1건의 `EMBEDDED_DATA` 전체를 만든다.

    `financials`에는 `select_financial_rows()`가 고른 **완전한 연도만** 실린다
    (모듈 docstring 참고). `selection`을 넘기지 않으면 여기서 계산한다.

    `peer_pool`(같은 Job 전체의 비교군 후보)을 넘기면 `peers`/`industryAverage`/
    `regionGroup`을 채운다 — **넘기지 않으면 예전과 똑같이 빈 값**이다(풀을 여기서
    새로 만들지 않는다. 선택 회사들만으로 만든 풀은 "같은 Job의 다른 회사"라는
    비교군 정의와 다르기 때문). 표본이 모자라면 빈 값으로 남는 것도 정상이다.

    `opinionSummary`는 자유 서술 문단이라 자동 생성 대상이 아니다(계속 `""`).
    """
    chosen = selection if selection is not None else select_financial_rows(item.snapshots)
    pool = peer_pool if peer_pool is not None else EMPTY_PEER_POOL
    target = build_peer_candidate(item.result, chosen)

    peers: list[PeerCandidate] = []
    industry_average: dict[str, Any] | None = None
    region_group: list[dict[str, Any]] = []
    if target is not None:
        peers = select_peers(pool, target)
        industry_average = build_industry_average(peers, target)
        region_group = select_region_group(pool, target)

    return {
        "firm": dict(FIRM_PROFILE),
        "company": build_company_payload(item.result),
        "financials": chosen.rows,
        "peers": build_peer_rows(peers),
        "industryAverage": industry_average,
        "regionGroup": region_group,
        "opinionSummary": "",
    }


def collect_warnings(item: ReportInput, selection: FinancialSelection) -> list[ReportWarning]:
    """사용자에게 알려야 할 문제를 모은다.

    두 종류가 섞여 있다:

      - **생성하지 않은 경우**(실을 재무연도가 0건) — 문구가 "생성하지 않았습니다"로
        끝나야 한다. 예전 문구("분석 영역이 비어 보입니다")는 실제 결과(문서 뒷부분
        전체 소실)를 축소 서술했다.
      - **생성했지만 검수가 필요한 경우** — PARTIAL 파싱/전기 유래 연도/제외된 연도.
    """
    result_id = getattr(item.result, "id", None)
    corp_name = getattr(item.result, "corp_name", None)
    warnings: list[ReportWarning] = []

    def add(message: str) -> None:
        warnings.append(ReportWarning(result_id=result_id, corp_name=corp_name, message=message))

    # --- 제외된 연도 안내 (생성 여부와 무관) ---
    if selection.failed_years:
        add(
            "파싱 실패(FAILED)한 회계연도를 보고서에서 제외했습니다: "
            + ", ".join(selection.failed_years)
        )
    if selection.incomplete_years:
        details = ", ".join(
            f"{year}년({'/'.join(missing)})" for year, missing in selection.incomplete_years
        )
        add(
            f"전체 {selection.total_years}개년 중 {len(selection.incomplete_years)}개년은"
            f" 재무 항목이 결측이거나 비정상(0/음수)이라 제외하고 보고서를 생성했습니다:"
            f" {details}"
        )

    # --- 생성 자체가 불가능한 경우 ---
    if not selection.rows:
        if selection.total_years == 0:
            add("재무 이력이 없어 이 회사는 보고서를 생성하지 않았습니다.")
        else:
            add(
                f"쓸 수 있는 재무 이력이 없어(전체 {selection.total_years}개년이 모두"
                " 파싱 실패/결측) 이 회사는 보고서를 생성하지 않았습니다."
            )
        return warnings

    # --- 생성은 했지만 검수가 필요한 경우 ---
    if len(selection.rows) < 2:
        add(
            f"재무 이력이 {len(selection.rows)}개년뿐이라 추세 분석(권장 4개년)이"
            " 제한된 보고서로 생성됐습니다."
        )
    if selection.partial_years:
        add(
            "부분 파싱(PARTIAL) 결과가 포함돼 있습니다 — 검수되지 않은 값일 수 있습니다: "
            + ", ".join(f"{year}년" for year in selection.partial_years)
        )
    if selection.prior_period_years:
        add(
            "다음 연도 공시의 전기 항목에서 가져온 참고값이 포함돼 있습니다: "
            + ", ".join(f"{year}년" for year in selection.prior_period_years)
        )

    status = _parse_status_text(getattr(item.result, "parse_status", None))
    if status in (ParseStatus.PARTIAL, ParseStatus.FAILED):
        add(f"이 회사의 파싱 상태가 {status}입니다 — 발송 전 값 검수를 권합니다.")
    if not _text(getattr(item.result, "corp_name", None)).strip():
        add("회사명이 비어 있어 파일명이 임시 이름으로 저장됐습니다.")
    return warnings


def collect_comparison_warnings(
    item: ReportInput,
    selection: FinancialSelection,
    peer_pool: PeerPool | None,
) -> list[ReportWarning]:
    """비교군에 **부분 파싱(PARTIAL) 연도**가 섞였음을 회사당 한 줄로 알린다(L2).

    대상 회사 자신의 PARTIAL 연도는 `collect_warnings()`가 이미 경고로 남기는데,
    비교군(peers/regionGroup) 후보의 PARTIAL 최근 연도는 그동안 조용히 쓰였다
    (실측 8건). 검수 상태가 대외 문서 단계에서 끊기지 않아야 한다는 원칙
    (CLAUDE.md)은 비교 수치에도 똑같이 적용된다.

    **후보를 빼지는 않는다** — PARTIAL은 "값이 없다"가 아니라 "검수가 덜 됐다"이고,
    빼면 가뜩이나 좁은 표본이 더 줄어든다. 대신 회사 이름을 몇 개만 보여 주고
    나머지는 "외 N곳"으로 줄인다(`MAX_WARNING_NAME_SAMPLES`).

    선별은 `build_report_payload()`와 **같은 순수 함수**(`select_peers()` /
    `select_region_candidates()`)를 다시 호출해 얻는다 — 결과를 주고받으려면
    공개 시그니처를 바꿔야 하는데, 두 호출 모두 DB/IO 없는 목록 필터라 그 대가가
    구조 변경보다 싸다. 여기서 고른 목록은 실제로 문서에 실리는 목록과 동일하다.
    """
    if peer_pool is None or not selection.rows:
        return []
    target = build_peer_candidate(item.result, selection)
    if target is None:
        return []

    partial_candidates: list[PeerCandidate] = []
    seen: set[Any] = set()
    for candidate in (
        *select_peers(peer_pool, target),
        *select_region_candidates(peer_pool, target),
    ):
        if not candidate.partial:
            continue
        key = candidate.result_id if candidate.result_id is not None else id(candidate)
        if key in seen:
            continue
        seen.add(key)
        partial_candidates.append(candidate)

    if not partial_candidates:
        return []

    names = [f"{c.name}({c.year}년)" for c in partial_candidates]
    shown = ", ".join(names[:MAX_WARNING_NAME_SAMPLES])
    rest = len(names) - MAX_WARNING_NAME_SAMPLES
    suffix = f" 외 {rest}곳" if rest > 0 else ""
    return [
        ReportWarning(
            result_id=getattr(item.result, "id", None),
            corp_name=getattr(item.result, "corp_name", None),
            message=(
                f"비교군에 부분 파싱(PARTIAL) 연도 데이터가 포함된 회사가"
                f" {len(names)}곳 있습니다: {shown}{suffix}"
            ),
        )
    ]


def collect_firm_profile_warnings() -> list[ReportWarning]:
    """사무소 소개 상수(`FIRM_PROFILE`)가 자리표시자 그대로인지 점검한다.

    회사별이 아니라 **한 번의 생성 요청당 1건**만 남긴다(`result_id=None`) —
    같은 문구가 회사 수만큼 반복되면 정작 회사별 경고가 묻힌다.
    """
    if not is_placeholder_contact(FIRM_PROFILE.get("contact")):
        return []
    return [
        ReportWarning(
            result_id=None,
            corp_name=None,
            message=(
                "사무소 연락처가 설정되지 않았습니다 — 자리표시자 문구가 그대로 인쇄됩니다."
                " backend/app/reports/firm_profile.py의 FIRM_PROFILE['contact']를 확인하세요."
            ),
        )
    ]


# ---------------------------------------------------------------------------
# 출력 폴더 / 파일명
# ---------------------------------------------------------------------------


def sanitize_filename_stem(name: str | None, fallback: str = "회사명없음") -> str:
    """회사명을 Windows에서 안전한 파일명(확장자 제외)으로 바꾼다.

    금지문자(`\\/:*?"<>|`)와 제어문자는 `_`로, 끝의 공백/마침표는 제거한다
    (Windows가 조용히 잘라내 다른 파일을 덮어쓸 수 있다). 예약어(CON/PRN/...)는
    `_`를 앞에 붙이고, 너무 긴 이름은 100자로 자른다. 결과가 비면 `fallback`.
    """
    text = (name or "").strip()
    text = _INVALID_FILENAME_CHARS.sub("_", text)
    text = text.rstrip(" .")
    if len(text) > _MAX_STEM_LENGTH:
        text = text[:_MAX_STEM_LENGTH].rstrip(" .")
    if not text:
        return fallback
    if text.upper() in _RESERVED_FILENAMES or text.split(".")[0].upper() in _RESERVED_FILENAMES:
        text = f"_{text}"
    return text


def unique_filename(stem: str, suffix: str, taken: set[str]) -> str:
    """이미 쓴 파일명이면 `_2`, `_3` ... 을 붙여 덮어쓰지 않게 한다.

    Windows 파일시스템은 대소문자를 구분하지 않으므로 소문자로 비교한다.
    `taken`에는 확정된 파일명(소문자)을 넣어 돌려준다.
    """
    candidate = f"{stem}{suffix}"
    index = 1
    while candidate.lower() in taken:
        index += 1
        candidate = f"{stem}_{index}{suffix}"
    taken.add(candidate.lower())
    return candidate


def allocate_output_dir(base_dir: Path, today: date | None = None) -> Path:
    """`base_dir/YYYY-MM-DD`(이미 있으면 `_2`, `_3` ...) 폴더를 새로 만든다.

    한 번의 호출로 만든 산출물 폴더는 그대로 보존한다 — 기존 폴더에 덧쓰지 않는다.
    """
    day = (today or date.today()).strftime("%Y-%m-%d")
    index = 1
    while True:
        name = day if index == 1 else f"{day}_{index}"
        candidate = base_dir / name
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            index += 1
            if index > 1000:  # 비정상 상황 방어(무한 루프 방지)
                raise ReportGenerationError(
                    f"보고서 폴더를 만들 수 없습니다 — {base_dir} 아래 {day} 폴더가 너무 많습니다."
                ) from None
        except OSError as exc:
            raise ReportGenerationError(
                f"보고서 폴더를 만들 수 없습니다: {candidate} ({exc})"
            ) from exc


# ---------------------------------------------------------------------------
# 라벨 엑셀
# ---------------------------------------------------------------------------


# openpyxl이 셀 값으로 거부하는 제어문자(`openpyxl.cell.cell.ILLEGAL_CHARACTERS_RE`와
# 동일 범위 — 탭/개행/캐리지리턴은 허용된다).
_EXCEL_ILLEGAL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def excel_safe_text(value: Any) -> str:
    """엑셀 셀에 그대로 넣어도 안전한 문자열로 정제한다.

    회사명/주소에 제어문자가 섞여 있으면 openpyxl이 `IllegalCharacterError`를
    던져 **엑셀만 실패하고 HTML은 이미 저장된** 반쪽 폴더가 남는다. 값을 버리지
    않고 제어문자만 제거한다.
    """
    return _EXCEL_ILLEGAL_CHARS.sub("", _text(value)).strip()


def write_label_workbook(items: Sequence[ReportInput], path: Path) -> None:
    """우편 발송용 라벨 엑셀(회사명/주소 2컬럼)을 저장한다.

    `openpyxl`은 값 대입 시점(`IllegalCharacterError`)과 저장 시점(`OSError` 등)
    양쪽에서 실패할 수 있어 조립 전체를 감싼다 — 어떤 실패든 500 스택트레이스가
    아니라 507 `ReportGenerationError`로 통일한다.
    """
    try:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "발송처"
        sheet.append(list(LABEL_COLUMNS))
        for item in items:
            sheet.append(
                [
                    excel_safe_text(getattr(item.result, "corp_name", None)),
                    excel_safe_text(getattr(item.result, "address", None)),
                ]
            )
        workbook.save(path)
    except (OSError, ValueError, IllegalCharacterError) as exc:
        raise ReportGenerationError(
            f"발송처 목록 엑셀을 저장할 수 없습니다: {path} ({exc})"
        ) from exc


# ---------------------------------------------------------------------------
# 엔트리포인트
# ---------------------------------------------------------------------------


def generate_reports(
    items: Sequence[ReportInput],
    *,
    base_dir: Path | None = None,
    template_text: str | None = None,
    today: date | None = None,
    peer_pool: PeerPool | None = None,
) -> ReportGenerationOutcome:
    """선택된 회사들의 보고서 HTML + 발송처 엑셀을 새 폴더에 생성한다.

    - `base_dir`: 기본값은 `Settings.report_output_dir`(= `BACKEND_DIR/report`).
    - `template_text`: 기본값은 템플릿 파일에서 읽은 원문(테스트용 주입 지점).
    - `peer_pool`: 같은 Job 전체로 만든 비교군 후보 풀(`build_peer_pool()`).
      호출부가 **요청당 한 번만** 만들어 넘긴다 — 넘기지 않으면 비교군은 빈 값이다.
    - 반환값의 `warnings`는 "생성은 했지만 검수가 필요한 회사" + "생성하지 않은
      회사" 목록이다 — 한 회사 때문에 전체 요청을 실패시키지 않는다.
    - **쓸 수 있는 재무연도가 0건인 회사는 생성하지 않는다**(`skipped`). 템플릿이
      빈 `financials`에서 렌더 도중 죽어 연락처도 없는 반쪽 문서가 나오기 때문
      (모듈 docstring 참고). 발송처 라벨 엑셀에도 **실제로 생성된 회사만** 싣는다 —
      보고서가 없는 회사에 우편을 보내게 되면 안 된다.
    """
    if not items:
        raise ReportGenerationError("보고서를 생성할 대상이 없습니다.")

    settings = get_settings()
    target_base = Path(base_dir) if base_dir is not None else Path(settings.report_output_dir)
    template = template_text if template_text is not None else load_template_text()

    output_dir = allocate_output_dir(target_base, today)
    outcome = ReportGenerationOutcome(output_dir=output_dir)
    outcome.warnings.extend(collect_firm_profile_warnings())

    taken: set[str] = {LABEL_FILENAME.lower()}
    generated_items: list[ReportInput] = []
    for item in items:
        selection = select_financial_rows(item.snapshots)
        outcome.warnings.extend(collect_warnings(item, selection))
        if not selection.rows:
            outcome.skipped.append(
                SkippedReport(
                    result_id=getattr(item.result, "id", None),
                    corp_name=getattr(item.result, "corp_name", None),
                    reason="쓸 수 있는 재무 이력이 없습니다.",
                )
            )
            continue

        # 비교군 경고는 **실제로 문서를 만드는 회사에 대해서만** 남긴다(생성하지 않은
        # 회사에는 비교군 자체가 인쇄되지 않으므로 알릴 이유가 없다).
        outcome.warnings.extend(collect_comparison_warnings(item, selection, peer_pool))

        payload = build_report_payload(item, selection, peer_pool)
        html = render_report_html(template, payload)
        stem = sanitize_filename_stem(getattr(item.result, "corp_name", None))
        filename = unique_filename(stem, ".html", taken)
        try:
            (output_dir / filename).write_text(html, encoding="utf-8")
        except OSError as exc:
            raise ReportGenerationError(
                f"보고서 파일을 저장할 수 없습니다: {output_dir / filename} ({exc})"
            ) from exc
        outcome.files.append(
            GeneratedReportFile(
                result_id=getattr(item.result, "id", None),
                corp_name=getattr(item.result, "corp_name", None),
                filename=filename,
            )
        )
        generated_items.append(item)

    write_label_workbook(generated_items, output_dir / LABEL_FILENAME)
    return outcome
