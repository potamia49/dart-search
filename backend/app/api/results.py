"""결과 조회/다운로드 API.

상세개발계획.md §6 (M2~M4 범위):
    GET /api/jobs/{id}/results                  결과 목록 (페이징, parse_status/제외 여부 필터)
    GET /api/jobs/{id}/export?format=xlsx|csv    결과 파일 다운로드
    POST /api/jobs/{id}/generate-report          선택 회사별 감사 수임 제안 보고서(HTML)
                                                 + 발송처 목록 엑셀을 로컬 폴더에 생성
                                                 (2026-08-03 추가)
    POST /api/jobs/{id}/results/selection-summary 선택한 id 목록의 요약 집계
                                                 (확인 모달용, 2026-08-03 추가.
                                                  `failed`/`no_disclosure`/
                                                  `no_history` 구분은 응답 모델
                                                  docstring 참고)
    GET /api/jobs/{id}/results/{result_id}/history  회사 1건의 연도별 재무 이력
                                                     (STEP 7, 2026-07-15 추가)

STEP 5(파싱, M3)가 채워져 `parse_status`/재무 항목이 실제 값을 갖는다.
`/export`는 M4에서 `app/exporters/excel.py`와 함께 구현했다 — 페이징 없이
필터를 통과한 결과 전체를 xlsx/csv로 내려준다.

`/export`는 2026-07-28(§4-11, M9)부터 `ids`(체크박스로 고른 `results.id` 목록)와
`include_history`(재무이력 시트 추가, xlsx 전용) 쿼리 파라미터를 함께 받는다 —
기존 "현재 필터·정렬 전체 내보내기" 동작은 두 파라미터가 없을 때 그대로다.

`/results`는 2026-08-03부터 `ids_only=true`를 받는다 — 페이징 없이 현재 필터를
통과한 `results.id`만 전부 돌려주는 경량 응답으로, 화면의 "현재 필터 전체 선택"이
페이지를 순회하지 않고 선택 목록(`/export?ids=` · `/generate-report`의 입력)을
한 번에 만들기 위한 것이다. 새 엔드포인트를 만들지 않고 기존 목록 조회에 옵션
하나만 얹은 형태이며, 필터·정렬 파라미터의 의미는 완전히 동일하다.

`/results/{result_id}/history`는 `financial_snapshots`(STEP 7)를 조회한다.
기존 `/results` 목록 응답은 무겁게 만들지 않기 위해 그대로 두고(이력은
포함하지 않음), 상세 조회에서만 lazy-load하게 별도 엔드포인트로 분리했다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from functools import partial
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, and_, distinct, false, func, or_, select, true
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.core.db import get_db
from app.core.pipeline import auditor_change_flags
from app.exporters.excel import (
    export_results,
    export_results_with_history,
    export_selection_results,
)
from app.models.financial_snapshot import FinancialSnapshot
from app.models.job import Job, JobPhase
from app.models.result import ParseStatus, Result
from app.reports.audit_proposal import (
    PeerPool,
    ReportGenerationError,
    ReportInput,
    build_peer_pool,
    generate_reports,
)
from app.parsers.account_detail import parse_account_detail
from app.parsers.auditor import extract_auditor
from app.parsers.document_sections import SECTION_TITLE_MARKS, extract_section_html

router = APIRouter(prefix="/api/jobs", tags=["results"])


# 정렬 허용 컬럼 화이트리스트 (2026-07-20) — 임의 컬럼명을 그대로 ORDER BY에
# 넣지 않기 위해 명시적으로 열거한다. 화면에 노출되는 컬럼과 1:1로 맞춘다.
SORTABLE_COLUMNS: tuple[str, ...] = (
    "corp_name",
    "address",
    "phone",
    "ceo_name",
    "induty_name",
    "induty_code",
    "fiscal_date",
    "audit_opinion",
    "auditor_name",
    "auditor_address",
    "auditor_changed",
    "parse_status",
    "current_assets_cur", "current_assets_prv",
    "noncurrent_assets_cur", "noncurrent_assets_prv",
    "total_assets_cur", "total_assets_prv",
    "current_liab_cur", "current_liab_prv",
    "noncurrent_liab_cur", "noncurrent_liab_prv",
    "total_liab_cur", "total_liab_prv",
    "total_equity_cur", "total_equity_prv",
    "revenue_cur", "revenue_prv",
    "cogs_cur", "cogs_prv",
    "gross_profit_cur", "gross_profit_prv",
    "sga_cur", "sga_prv",
    "operating_income_cur", "operating_income_prv",
    "net_income_cur", "net_income_prv",
    "cf_operating_cur", "cf_operating_prv",
    "cf_investing_cur", "cf_investing_prv",
    "cf_financing_cur", "cf_financing_prv",
    "cf_ending_cash_cur", "cf_ending_cash_prv",
    "non_operating_income_cur", "non_operating_income_prv",
    "non_operating_expense_cur", "non_operating_expense_prv",
    # 세부계정 5항목 (`DETAIL_FINANCIAL_FIELDS`, 2026-08-05). CF 4항목·영업외손익
    # 2항목과 동형인 best-effort 필드라 정렬 대상에도 같은 방식으로 넣는다 —
    # 값이 없는 행은 `_apply_sort()`가 방향과 무관하게 항상 뒤로 보낸다.
    "cash_and_equivalents_cur", "cash_and_equivalents_prv",
    "trade_receivables_cur", "trade_receivables_prv",
    "interest_expense_cur", "interest_expense_prv",
    "depreciation_cur", "depreciation_prv",
    "amortization_cur", "amortization_prv",
    "latest_disclosure_date",
)

# 키워드 검색(`q`) 대상 컬럼 — 회사명/주소/감사인으로 좁혀 찾는 용도.
_SEARCH_COLUMNS = ("corp_name", "address", "ceo_name", "induty_name", "auditor_name")


# ---------------------------------------------------------------------------
# 결과화면 컬럼 필터(§4-13-B/C, 2026-08-05)용 다중 선택 파라미터
#
# 기존 `parse_status`(단일값) + `has_disclosure`(단일값) 조합으로는 화면의 체크박스
# 4개를 **자유 조합**할 수 없다("검수 필요"만 켜고 "감사보고서 없음"은 끄기 등) —
# 둘 다 `parse_status=FAILED`이고 `rcept_no` 유무로만 갈리기 때문이다. 그래서
# 다중 정렬(`sort=field:dir,...`)과 같은 **콤마 구분 목록** 방식의 파생 enum
# 파라미터를 새로 둔다. 기존 두 파라미터는 다른 소비자를 위해 그대로 살아 있고,
# 함께 주면 AND로 결합된다(서로 모순되는 조합이면 0건이 나올 뿐이다).
# ---------------------------------------------------------------------------

# 파싱상태 4분류 — 화면 체크박스("파싱 성공/부분 성공/검수 필요/감사보고서 없음")와
# 1:1이다. **네 값이 그 Job의 모든 행을 빠짐없이·겹침없이 나눈다**(상호배타 + 전수
# 포괄): 넷을 다 켜면 필터를 안 건 것과 같은 집합이 나와야 한다는 뜻이다. 그래서
# `FAILED_REVIEW`를 "`parse_status='FAILED'`"가 아니라 "원문은 있는데 OK/PARTIAL이
# 아닌 것"이라는 **catch-all**로 정의했다 — 그렇게 하지 않으면 Phase 2 진행 중
# (rcept_no는 찾았고 아직 파싱 전이라 `parse_status IS NULL`)인 행이 네 값 어디에도
# 속하지 않아 **조용히 사라진다**(이 저장소가 가장 경계하는 실패 양식).
PARSE_STATUS_EXT_VALUES: tuple[str, ...] = (
    "OK",             # 원문 있음 + parse_status=OK
    "PARTIAL",        # 원문 있음 + parse_status=PARTIAL
    "FAILED_REVIEW",  # 원문 있음 + 그 외(FAILED/미파싱) = 검수 필요
    "NO_DISCLOSURE",  # rcept_no IS NULL = 열어볼 원문 자체가 없음(검수 대상 아님)
)

# 감사인 변동 tri-state(`results.auditor_changed` 1/0/NULL)의 다중 선택판 —
# 화면 체크박스 3종(변동 있음/변동 없음/판정 불가)과 1:1이다. 기존 불리언
# `auditor_changed`는 "1만"/"0만"/"전체"(미지정) 3가지만 표현할 수 있어
# "판정 불가만" · "변동 있음 + 판정 불가" 같은 조합이 안 된다.
AUDITOR_CHANGED_EXT_VALUES: tuple[str, ...] = ("CHANGED", "UNCHANGED", "UNKNOWN")

# SQLite INTEGER(부호 있는 64비트) 범위 — 금액 필터 경계값을 그대로 바인딩하면
# 범위 밖 정수에서 `OverflowError`로 500이 되므로 400으로 막는다(`_parse_export_ids`와
# 같은 이유). 매출액은 음수일 수 있어(실측 -5,409백만원) 하한을 0으로 두지 않는다.
_SQLITE_INT_MIN = -(2**63)
_SQLITE_INT_MAX = 2**63 - 1


def _parse_enum_list(
    raw: str | None, allowed: tuple[str, ...], param_name: str
) -> list[str] | None:
    """`?param=A,B` 콤마 구분 목록을 정규화한다(대소문자 무시, 중복 제거, 순서 보존).

    - 파라미터 자체가 없으면 `None` = **필터하지 않음**(기존 동작 그대로).
    - 값이 비어 있으면(`?param=` 또는 쉼표뿐) 빈 리스트 = **아무 것도 선택하지 않음**
      → 호출부가 0건으로 처리한다(체크박스를 전부 끈 상태의 자연스러운 결과다).
    - 목록에 없는 값은 **400**이다. 조용히 버리면 "체크했는데 목록에 없다"가 되어
      더 위험하다(`ids` 파싱과 같은 방침).
    """
    if raw is None:
        return None
    values: list[str] = []
    for token in (t.strip().upper() for t in raw.split(",")):
        if not token:
            continue
        if token not in allowed:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{param_name}에 알 수 없는 값이 있습니다: {token!r} "
                    f"(가능한 값: {', '.join(allowed)})"
                ),
            )
        if token not in values:
            values.append(token)
    return values


def _parse_status_ext_condition(value: str):
    """파싱상태 4분류 중 한 값에 해당하는 SQL 조건 — 위 상수 주석의 정의 그대로."""
    if value == "NO_DISCLOSURE":
        return Result.rcept_no.is_(None)
    if value == "OK":
        return and_(Result.rcept_no.is_not(None), Result.parse_status == ParseStatus.OK)
    if value == "PARTIAL":
        return and_(
            Result.rcept_no.is_not(None), Result.parse_status == ParseStatus.PARTIAL
        )
    # FAILED_REVIEW — 원문은 있는데 OK/PARTIAL이 아닌 전부(catch-all).
    # `NOT IN (...)`은 NULL에 대해 NULL(=거짓 취급)이라 미파싱 행을 놓치므로
    # `IS NULL` 분기를 명시적으로 함께 둔다.
    return and_(
        Result.rcept_no.is_not(None),
        or_(
            Result.parse_status.is_(None),
            Result.parse_status.not_in((ParseStatus.OK, ParseStatus.PARTIAL)),
        ),
    )


def _auditor_changed_ext_condition(value: str):
    """감사인 변동 3분류 중 한 값에 해당하는 SQL 조건(1/0/NULL 그대로 매핑)."""
    if value == "CHANGED":
        return Result.auditor_changed == 1
    if value == "UNCHANGED":
        return Result.auditor_changed == 0
    # UNKNOWN — 판정 불가. 이 컬럼 도입 이전 Job은 NULL이고, 1/0이 아닌 값이
    # 들어갈 일은 없지만 "CHANGED/UNCHANGED가 아닌 나머지"로 두어 전수 포괄을 지킨다.
    return or_(
        Result.auditor_changed.is_(None), Result.auditor_changed.not_in((0, 1))
    )


# ---------------------------------------------------------------------------
# 결과화면 컬럼 필터 2차 — 텍스트/값 목록 컬럼 (§4-14, 2026-08-05)
#
# §4-13-B가 다룬 4개 컬럼(매출액/총자산/파싱상태/감사인변동) 외에 회사명·주소·업종·
# 감사인·감사의견 5개 컬럼에도 헤더 필터를 붙이기 위한 파라미터다. 두 부류로 나뉘고,
# **각 컬럼마다 포함(선택)과 제외를 모두 지원**한다(사용자 요구):
#
#   (1) 자유 텍스트 부분일치 — 회사명(`corp_name_contains`/`corp_name_not_contains`),
#       주소(`address_contains`/`address_not_contains`). 고유값이 사실상 전부 달라
#       값 목록을 고르는 방식이 성립하지 않는다.
#   (2) 값 목록 다중 선택 — 업종명/감사인명/감사의견(`*_in`/`*_not_in`). 고를 값의
#       목록은 `GET /api/jobs/{id}/results/distinct-values`가 준다(엑셀 컬럼 필터의
#       "값 목록"에 해당. 이 조회가 없다는 이유로 §4-13-C에서 향후 과제로 미뤄 뒀던
#       바로 그 부분이다).
#
# **기존 콤마 구분 목록 규약(`parse_status_ext` 등)을 값 목록에는 쓰지 않는다** —
# 그쪽은 값이 코드 상수(`OK`/`CHANGED`…)라 안전하지만, 여기 값은 DB에 실제로 들어 있는
# 자유 텍스트(업종명·감사인명)라 값 자체에 쉼표가 들어가면 토큰이 조용히 쪼개져 **아무
# 것도 매칭되지 않는다**. 가정이 아니라 실측이다 — 개발 DB의 업종명에는 쉼표가 흔하다
# ("직물, 편조원단 및 의복류 염색 가공업" 등 Job 27 기준 330건/7.5%). 그래서 값 목록은
# **같은 이름을 여러 번 반복**하는 방식(`?induty_name_in=A&induty_name_in=B`)으로
# 받는다 — 이스케이프가 필요 없고 값을 그대로 왕복시킬 수 있다.
# 다만 기존 규약의 *정신*(파라미터 미지정 = 필터 없음 / 빈 값 = 아무 것도 선택 안 함
# = 0건 / 잘못된 입력은 조용히 버리지 않고 400)은 그대로 지킨다.
# ---------------------------------------------------------------------------

# 값 목록 조회(`distinct-values`)와 `*_in`/`*_not_in` 필터를 지원하는 컬럼.
# 회사명/주소는 **일부러 빼 뒀다** — 고유값이 행 수와 사실상 같아 목록을 뽑아 봐야
# 고를 수 없고, 그쪽은 (1)의 텍스트 부분일치가 맞는 도구다.
DISTINCT_VALUE_FIELDS: tuple[str, ...] = (
    "induty_name",
    "auditor_name",
    "audit_opinion",
)

# "값 없음"을 값 목록에서 **명시적으로 고르기 위한** 예약 토큰(엑셀 필터의
# "(필드 값 없음)" 항목과 같은 역할). `?audit_opinion_in=__BLANK__`이면 감사의견이
# NULL이거나 빈 문자열인 행만 남고, `?audit_opinion_not_in=__BLANK__`이면 그 행들만
# 빠진다. NULL/빈 문자열을 하나로 묶는 이유는 두 가지다 — ① 사용자에게는 둘 다 "빈
# 셀"이라 구분할 이유가 없고, ② 빈 문자열을 일반 값으로 두면 `?field_in=`(빈 쿼리
# 파라미터)이 "아무 것도 선택 안 함"인지 "빈 값을 골랐음"인지 구분되지 않는다.
# (개발 DB 실측으로는 빈 문자열이 0건이고 전부 NULL이지만, 파서/파이프라인이 어느 날
#  빈 문자열을 쓰기 시작해도 화면 동작이 갈라지지 않게 처음부터 묶어 둔다.)
#
# 이 토큰이 필요한 규모의 근거: 감사인명은 Job 27에서 4,383건 중 **1,886건(43.0%)**,
# 감사의견은 **1,990건(45.4%)** 이 값 없음이다(파싱 실패·감사보고서 없음). 값 없음을
# 고를 수단이 없으면 목록 필터를 켜는 순간 그 절반이 통째로 사라진다.
BLANK_VALUE_TOKEN = "__BLANK__"

# 입력 상한 — 자유 텍스트라 화이트리스트 검증이 불가능한 대신 크기만 막는다.
# (넘으면 400. 조용히 자르면 "고른 값 중 일부가 무시됐다"가 되어 더 위험하다.)
_MAX_TEXT_FILTER_LEN = 200
_MAX_VALUE_FILTER_ITEMS = 500
_MAX_VALUE_FILTER_LEN = 500

# 값 목록 조회 상한 — 개발 DB 실측 최대치는 업종명 738종(Job 27, 결과 4,383건)이라
# 기본값 1000이면 현실적인 Job은 한 번에 다 담긴다(감사인 247종 / 감사의견 4종).
# 그래도 넘치면 `truncated=true`로 알리고 화면이 `q`로 좁히게 한다.
_DISTINCT_VALUES_DEFAULT_LIMIT = 1000
_DISTINCT_VALUES_MAX_LIMIT = 2000

# LIKE 패턴에서 특수 의미를 갖는 문자 — 사용자가 친 그대로를 찾게 하려면 반드시
# 이스케이프해야 한다(회사명에 실제로 `_`가 들어가는 경우가 있고, `%`를 치면
# 이스케이프 없이는 "아무 글자나"가 되어 전 행이 매칭된다).
_LIKE_ESCAPE_CHAR = "\\"


def _is_blank(column):
    """"값 없음" 조건 — NULL 또는 빈 문자열(위 `BLANK_VALUE_TOKEN` 주석 참고)."""
    return or_(column.is_(None), column == "")


def _like_pattern(keyword: str) -> str:
    """부분일치용 LIKE 패턴(`%키워드%`) — 와일드카드/이스케이프 문자를 먼저 escape."""
    escaped = (
        keyword.replace(_LIKE_ESCAPE_CHAR, _LIKE_ESCAPE_CHAR * 2)
        .replace("%", _LIKE_ESCAPE_CHAR + "%")
        .replace("_", _LIKE_ESCAPE_CHAR + "_")
    )
    return f"%{escaped}%"


def _normalize_text_filter(raw: str | None, param_name: str) -> str | None:
    """텍스트 부분일치 파라미터 정규화 — 공백뿐이면 `None`(=필터 없음)."""
    if raw is None:
        return None
    keyword = raw.strip()
    if not keyword:
        # 검색어(`q`)와 같은 방침: 빈 입력은 "필터 없음"이다. 값 목록(`*_in`)의
        # 빈 값과 달리 0건으로 두지 않는 이유는, 텍스트 입력칸을 비우는 행위가
        # 화면에서 "이 필터를 끈다"는 뜻이기 때문이다(체크박스를 전부 끄는 것과 다르다).
        return None
    if len(keyword) > _MAX_TEXT_FILTER_LEN:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{param_name}이(가) 너무 깁니다: {len(keyword)}자 "
                f"(최대 {_MAX_TEXT_FILTER_LEN}자)"
            ),
        )
    return keyword


def _apply_text_filter(stmt: Select, column, keyword: str | None, *, exclude: bool) -> Select:
    """회사명/주소 부분일치(포함/제외) 조건을 건다(§4-14).

    - 포함(`exclude=False`): `컬럼 LIKE '%키워드%'`. 값이 NULL인 행은 "그 글자를
      포함한다"고 볼 수 없으므로 빠진다(NULL LIKE는 SQL에서 NULL = 거짓 취급).
    - 제외(`exclude=True`): `컬럼 IS NULL OR 컬럼 = '' OR NOT (컬럼 LIKE ...)`.
      **NULL/빈 값 행을 반드시 통과시켜야 한다** — 순진하게 `NOT LIKE`만 쓰면
      NULL이 NULL로 평가돼 조용히 사라진다(금액 범위 필터의 NULL 통과 방침과 같은
      이유다. 이 화면은 검수용이라 값 없는 회사가 사라지면 검수 경로가 끊긴다).
    """
    if keyword is None:
        return stmt
    matches = column.ilike(_like_pattern(keyword), escape=_LIKE_ESCAPE_CHAR)
    if not exclude:
        return stmt.where(matches)
    return stmt.where(or_(_is_blank(column), ~matches))


def _parse_value_filter(raw: list[str] | None, param_name: str) -> list[str] | None:
    """반복 쿼리 파라미터(`?f=A&f=B`)를 값 목록으로 정규화한다(§4-14).

    - 파라미터 자체가 없으면 `None` = **필터하지 않음**.
    - 빈 토큰(`?f=`)은 값이 아니라 "선택 없음"으로 보고 버린다 → 빈 리스트가 되며,
      호출부가 포함/제외에 따라 다르게 해석한다(포함=0건 / 제외=아무 것도 안 뺌).
      실제 "빈 값"을 고르려면 `__BLANK__` 토큰을 쓴다.
    - **값의 앞뒤 공백을 다듬지 않는다** — 이 값은 `distinct-values` 응답을 그대로
      되돌려 보내는 계약이라, 다듬으면 앞뒤 공백이 붙은 원본 값과 매칭되지 않는다.
    - 개수/길이 상한을 넘으면 400.
    """
    if raw is None:
        return None
    values: list[str] = []
    for token in raw:
        if token == "":
            continue
        if len(token) > _MAX_VALUE_FILTER_LEN:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{param_name}의 값이 너무 깁니다: {len(token)}자 "
                    f"(최대 {_MAX_VALUE_FILTER_LEN}자)"
                ),
            )
        if token not in values:
            values.append(token)
    if len(values) > _MAX_VALUE_FILTER_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{param_name}에 값이 너무 많습니다: {len(values)}개 "
                f"(최대 {_MAX_VALUE_FILTER_ITEMS}개)"
            ),
        )
    return values


def _value_list_condition(column, values: list[str]):
    """선택한 값 중 **하나라도** 일치하는 행 조건(`__BLANK__`는 값 없음)."""
    plain = [v for v in values if v != BLANK_VALUE_TOKEN]
    clauses = []
    if plain:
        clauses.append(column.in_(plain))
    if len(plain) != len(values):
        clauses.append(_is_blank(column))
    return or_(*clauses) if clauses else false()


def _value_list_exclude_condition(column, values: list[str]):
    """선택한 값을 **전부** 빼는 행 조건 — NULL 안전하게 직접 조립한다.

    `~_value_list_condition(...)`으로 뒤집으면 `NOT (컬럼 IN (...))`이 NULL 행에서
    NULL로 평가돼 **감사인명이 없는 회사가 "특정 감사인 제외"에서 조용히 사라진다**
    (텍스트 제외 필터와 같은 함정). 그래서 값 없는 행은 명시적으로 통과시키고,
    `__BLANK__`를 제외 목록에 넣었을 때만 뺀다.
    """
    plain = [v for v in values if v != BLANK_VALUE_TOKEN]
    clauses = []
    if plain:
        clauses.append(or_(_is_blank(column), column.not_in(plain)))
    if len(plain) != len(values):
        clauses.append(and_(column.is_not(None), column != ""))
    return and_(*clauses) if clauses else true()


def _apply_value_filters(
    stmt: Select, column, include: list[str] | None, exclude: list[str] | None
) -> Select:
    """한 컬럼의 포함/제외 목록을 함께 적용한다(둘 다 주면 AND)."""
    if include is not None:
        # 빈 목록 = 체크박스를 전부 끈 상태 = 0건(`parse_status_ext`와 같은 규약).
        stmt = stmt.where(_value_list_condition(column, include) if include else false())
    if exclude:
        # 제외는 빈 목록이 "뺄 것이 없음"이라 no-op이다(0건이 아니다).
        stmt = stmt.where(_value_list_exclude_condition(column, exclude))
    return stmt


def _check_amount_bound(value: int | None, param_name: str) -> None:
    if value is not None and not _SQLITE_INT_MIN <= value <= _SQLITE_INT_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"{param_name}이(가) 사용할 수 있는 금액 범위를 벗어났습니다: {value!r}",
        )


def _apply_amount_range(stmt: Select, column, min_value: int | None, max_value: int | None) -> Select:
    """실측 금액 컬럼(`revenue_cur`/`total_assets_cur`)에 범위 조건을 건다(§4-13-B).

    **값이 NULL인 행은 경계가 걸려 있어도 그대로 통과시킨다**(2026-08-05 사용자 확정,
    §4-13-E-1의 문서 원안 "엑셀처럼 빈 셀은 숨김"을 뒤집은 결정) — 이 화면은 검수용이라
    "매출액을 못 얻은 회사"(파싱 실패/부분 성공) 자체가 봐야 할 대상이고, 범위를 잡는
    순간 그 회사들이 조용히 사라지면 검수 경로가 끊기기 때문이다. 즉 조건은
    `컬럼 IS NULL OR (컬럼 >= min AND 컬럼 <= max)`이다.
    """
    bounds = []
    if min_value is not None:
        bounds.append(column >= min_value)
    if max_value is not None:
        bounds.append(column <= max_value)
    if not bounds:
        return stmt
    return stmt.where(or_(column.is_(None), and_(*bounds)))


def _build_results_query(
    job_id: int,
    parse_status: str | None = None,
    excluded_by_revenue: bool | None = None,
    excluded_by_assets: bool | None = None,
    excluded_by_stale_disclosure: bool | None = None,
    has_disclosure: bool | None = None,
    q: str | None = None,
    auditor_changed: bool | None = None,
    *,
    parse_status_ext: str | None = None,
    auditor_changed_ext: str | None = None,
    revenue_min: int | None = None,
    revenue_max: int | None = None,
    assets_min: int | None = None,
    assets_max: int | None = None,
    corp_name_contains: str | None = None,
    corp_name_not_contains: str | None = None,
    address_contains: str | None = None,
    address_not_contains: str | None = None,
    induty_name_in: list[str] | None = None,
    induty_name_not_in: list[str] | None = None,
    auditor_name_in: list[str] | None = None,
    auditor_name_not_in: list[str] | None = None,
    audit_opinion_in: list[str] | None = None,
    audit_opinion_not_in: list[str] | None = None,
) -> Select:
    """`results` 조회 쿼리 빌더 — `/results`(페이징)와 `/export`(전체)가 공유한다.

    2026-08-05(§4-13-B)에 결과화면 컬럼 필터용 파라미터 6개가 추가됐다
    (`parse_status_ext`/`auditor_changed_ext` 다중 선택 + 금액 범위 4개).
    같은 날 §4-14로 텍스트 부분일치 4개(회사명/주소 × 포함/제외)와 값 목록 6개
    (업종명/감사인명/감사의견 × 포함/제외)가 더해졌다.
    기존 파라미터는 하나도 바뀌지 않았고, 새 것과 함께 주면 전부 **AND**로 묶인다.
    """
    stmt = select(Result).where(Result.job_id == job_id)
    if q:
        keyword = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(*(getattr(Result, col).ilike(keyword) for col in _SEARCH_COLUMNS))
        )
    if parse_status is not None:
        stmt = stmt.where(Result.parse_status == parse_status)
    if has_disclosure is not None:
        # rcept_no IS NULL == Phase 2 B1이 감사보고서 공시를 못 찾은 건. 파싱 실패가
        # 아니라 "열어볼 원문이 애초에 없음"이라 검수 대상이 아니다(2026-07-20 추가).
        stmt = stmt.where(
            Result.rcept_no.is_not(None) if has_disclosure else Result.rcept_no.is_(None)
        )
    if excluded_by_revenue is not None:
        stmt = stmt.where(Result.excluded_by_revenue == (1 if excluded_by_revenue else 0))
    if excluded_by_assets is not None:
        stmt = stmt.where(Result.excluded_by_assets == (1 if excluded_by_assets else 0))
    if excluded_by_stale_disclosure is not None:
        # 최근 1년 이내 DART 공시가 없는(=폐업/휴면 추정) 건만/제외된 건만
        # (2026-07-21 추가). 지정하지 않으면 필터하지 않는다 — 기본 동작(기본
        # 탭에서 이 값을 명시적으로 false로 보내 숨기는지 여부)은 프론트엔드 책임이다.
        stmt = stmt.where(
            Result.excluded_by_stale_disclosure
            == (1 if excluded_by_stale_disclosure else 0)
        )
    if auditor_changed is not None:
        # 연도별 감사인이 바뀐 회사만(true) / 계속 같은 회사만(false) (2026-07-26).
        # NULL(판정 불가 — 감사인 이름을 확보한 연도가 1개 이하)은 어느 쪽에도
        # 속하지 않으므로 두 경우 모두에서 빠진다. 값을 주지 않으면 필터하지
        # 않는다(excluded_by_* 와 동일한 tri-state 패턴).
        stmt = stmt.where(Result.auditor_changed == (1 if auditor_changed else 0))

    # --- 결과화면 컬럼 필터(§4-13-B, 2026-08-05) ---------------------------
    # 선택 목록이 비면(빈 문자열로 넘어온 경우) "아무 것도 체크하지 않음"이므로
    # 0건이다 — 필터를 안 건 것(파라미터 미지정)과 명확히 구분한다.
    status_values = _parse_enum_list(
        parse_status_ext, PARSE_STATUS_EXT_VALUES, "parse_status_ext"
    )
    if status_values is not None:
        stmt = stmt.where(
            or_(*(_parse_status_ext_condition(v) for v in status_values))
            if status_values
            else false()
        )
    auditor_values = _parse_enum_list(
        auditor_changed_ext, AUDITOR_CHANGED_EXT_VALUES, "auditor_changed_ext"
    )
    if auditor_values is not None:
        stmt = stmt.where(
            or_(*(_auditor_changed_ext_condition(v) for v in auditor_values))
            if auditor_values
            else false()
        )

    _check_amount_bound(revenue_min, "revenue_min")
    _check_amount_bound(revenue_max, "revenue_max")
    _check_amount_bound(assets_min, "assets_min")
    _check_amount_bound(assets_max, "assets_max")
    stmt = _apply_amount_range(stmt, Result.revenue_cur, revenue_min, revenue_max)
    stmt = _apply_amount_range(stmt, Result.total_assets_cur, assets_min, assets_max)

    # --- 텍스트/값 목록 컬럼 필터(§4-14, 2026-08-05) ------------------------
    # 회사명·주소는 부분일치(포함/제외), 업종명·감사인명·감사의견은 값 목록
    # 다중 선택(포함/제외)이다. 같은 컬럼에 포함과 제외를 함께 주면 AND다
    # (예: 업종을 3개 고르고 그중 1개를 다시 제외 — 교집합이라 2개만 남는다).
    stmt = _apply_text_filter(
        stmt,
        Result.corp_name,
        _normalize_text_filter(corp_name_contains, "corp_name_contains"),
        exclude=False,
    )
    stmt = _apply_text_filter(
        stmt,
        Result.corp_name,
        _normalize_text_filter(corp_name_not_contains, "corp_name_not_contains"),
        exclude=True,
    )
    stmt = _apply_text_filter(
        stmt,
        Result.address,
        _normalize_text_filter(address_contains, "address_contains"),
        exclude=False,
    )
    stmt = _apply_text_filter(
        stmt,
        Result.address,
        _normalize_text_filter(address_not_contains, "address_not_contains"),
        exclude=True,
    )
    stmt = _apply_value_filters(
        stmt,
        Result.induty_name,
        _parse_value_filter(induty_name_in, "induty_name_in"),
        _parse_value_filter(induty_name_not_in, "induty_name_not_in"),
    )
    stmt = _apply_value_filters(
        stmt,
        Result.auditor_name,
        _parse_value_filter(auditor_name_in, "auditor_name_in"),
        _parse_value_filter(auditor_name_not_in, "auditor_name_not_in"),
    )
    stmt = _apply_value_filters(
        stmt,
        Result.audit_opinion,
        _parse_value_filter(audit_opinion_in, "audit_opinion_in"),
        _parse_value_filter(audit_opinion_not_in, "audit_opinion_not_in"),
    )
    return stmt


def _resolve_sort_specs(
    sort: str | None, sort_by: str | None, sort_dir: str
) -> list[tuple[str, str]]:
    """다중 정렬 스펙 `[(컬럼, 방향), ...]`을 정규화한다(앞선 항목이 1순위).

    `sort`(콤마 구분 `field:dir` 목록, 예: ``"corp_name:asc,induty_name:desc"``)가
    우선하며, 없으면 레거시 단일 ``sort_by``/``sort_dir``로 폴백한다(하위호환 — 기존
    호출·테스트는 그대로 동작). 화이트리스트(`SORTABLE_COLUMNS`) 밖의 컬럼과 형식
    오류(방향 값이 asc/desc 아님)는 조용히 버리고, 같은 컬럼이 중복되면 처음 것만 남긴다.
    """
    specs: list[tuple[str, str]] = []
    if sort:
        for pair in (p.strip() for p in sort.split(",")):
            if not pair:
                continue
            field, _, direction = pair.partition(":")
            field = field.strip()
            direction = direction.strip().lower() or "asc"
            if field not in SORTABLE_COLUMNS or direction not in ("asc", "desc"):
                continue
            if any(existing == field for existing, _ in specs):
                continue
            specs.append((field, direction))
    elif sort_by and sort_by in SORTABLE_COLUMNS:
        specs.append((sort_by, "desc" if sort_dir == "desc" else "asc"))
    return specs


def _apply_sort(stmt: Select, specs: list[tuple[str, str]]) -> Select:
    """다중 컬럼 정렬 절을 붙인다 — 스펙 앞쪽이 1순위, 뒤쪽이 보조 정렬 기준이다.

    값이 없는 행(파싱 실패/미확보)은 오름차순·내림차순 어느 쪽이든 각 컬럼마다
    **항상 뒤로** 보낸다 — SQLite 기본 동작(ASC일 때 NULL이 맨 앞)대로 두면 "매출액
    낮은 순"을 눌렀을 때 값 없는 행이 화면을 채워 정렬이 쓸모없어진다. 마지막에 id로
    안정 정렬해 페이지를 넘겨도 순서가 흔들리지 않게 한다. 스펙이 비면 기본(id 오름차순).
    """
    if not specs:
        return stmt.order_by(Result.id.asc())
    order_cols = []
    for field, direction in specs:
        column = getattr(Result, field)
        order_cols.append(column.is_(None))
        order_cols.append(column.desc() if direction == "desc" else column.asc())
    order_cols.append(Result.id.asc())
    return stmt.order_by(*order_cols)


class ResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int | None
    corp_code: str | None
    rcept_no: str | None

    corp_name: str | None
    address: str | None
    phone: str | None
    ceo_name: str | None
    induty_code: str | None
    induty_name: str | None
    fiscal_date: str | None
    audit_opinion: str | None
    auditor_name: str | None
    auditor_address: str | None
    # 연도별 감사인 변동 여부 (2026-07-26) — 1: 이력에 서로 다른 감사인 2곳 이상,
    # 0: 이력의 감사인이 모두 동일, null: 판정 불가(감사인을 확보한 연도가 1개 이하.
    # STEP 7 미수행이거나 이 기능 도입 이전에 수집된 기존 Job이면 항상 null이다).
    auditor_changed: int | None

    current_assets_cur: int | None
    current_assets_prv: int | None
    noncurrent_assets_cur: int | None
    noncurrent_assets_prv: int | None
    total_assets_cur: int | None
    total_assets_prv: int | None
    current_liab_cur: int | None
    current_liab_prv: int | None
    noncurrent_liab_cur: int | None
    noncurrent_liab_prv: int | None
    total_liab_cur: int | None
    total_liab_prv: int | None
    total_equity_cur: int | None
    total_equity_prv: int | None
    revenue_cur: int | None
    revenue_prv: int | None
    cogs_cur: int | None
    cogs_prv: int | None
    gross_profit_cur: int | None
    gross_profit_prv: int | None
    sga_cur: int | None
    sga_prv: int | None
    operating_income_cur: int | None
    operating_income_prv: int | None
    net_income_cur: int | None
    net_income_prv: int | None
    cf_operating_cur: int | None
    cf_operating_prv: int | None
    cf_investing_cur: int | None
    cf_investing_prv: int | None
    cf_financing_cur: int | None
    cf_financing_prv: int | None
    cf_ending_cash_cur: int | None
    cf_ending_cash_prv: int | None
    non_operating_income_cur: int | None
    non_operating_income_prv: int | None
    non_operating_expense_cur: int | None
    non_operating_expense_prv: int | None
    # 세부계정 5항목 (`DETAIL_FINANCIAL_FIELDS`, 2026-08-05) — CF 4항목·영업외손익
    # 2항목과 동형인 best-effort 필드다. `determine_parse_status()` 판정에 관여하지
    # 않으므로 **결측(null)이 정상**이고, "컬럼 추가만, 소급 재파싱 없음" 관행대로
    # 2026-08-05 이전에 수집된 행은 전부 null이다(신규 Phase 2 실행분부터 채워짐).
    #
    # **각 필드의 원천 재무제표가 하나로 고정돼 있다**(`parsers/base.py` 주석 참고):
    # 현금·매출채권은 재무상태표의 **contra 행 차감 후 순액**(총액이 아니다 — 매출채권
    # 총액은 순액의 최대 6.4배), 이자비용은 손익계산서(발생주의), 감가상각비·
    # 무형자산상각비는 현금흐름표(제조원가 몫 포함 총액 — 손익계산서 판관비 몫과
    # 실측 75.4% 불일치)다. 화면에 라벨을 붙일 때 이 출처를 지우면 같은 이름의
    # 다른 숫자와 섞인다(엑셀 라벨은 "현금및현금성자산(순액)"/"매출채권(순액)"/
    # "감가상각비(현금흐름표)").
    cash_and_equivalents_cur: int | None
    cash_and_equivalents_prv: int | None
    trade_receivables_cur: int | None
    trade_receivables_prv: int | None
    interest_expense_cur: int | None
    interest_expense_prv: int | None
    depreciation_cur: int | None
    depreciation_prv: int | None
    amortization_cur: int | None
    amortization_prv: int | None

    # 금융위 요약재무 참고값 + 기준연도 (§4-10-C/D) — 후보 목록에서 "재무정보 수집을
    # 시작할지" 판단할 근거로만 쓴다. 필터 판정은 항상 `_cur`(원문 파싱값) 기준이다.
    ref_revenue: int | None
    ref_total_assets: int | None
    ref_fin_year: str | None

    parse_status: str | None
    parse_note: str | None
    excluded_by_revenue: int
    excluded_by_assets: int
    excluded_manually: int
    latest_disclosure_date: str | None
    excluded_by_stale_disclosure: int


class ResultListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ResultResponse]
    # `?ids_only=true`일 때만 채워지는 경량 응답 필드 (2026-08-03, §4-11 "필터 전체
    # 선택"용). 그 외에는 항상 **null**이며(빈 배열이 아니다), 그 차이가 프론트의
    # fail-safe 근거다 — `ids`를 모르는 구버전 백엔드는 `ids_only`를 조용히 무시하고
    # 200 + 페이지 1쪽만 돌려주므로, 응답에 `ids` 키가 없으면(=null) "서버가 전체
    # 선택을 지원하지 않음"으로 판정해야 한다(`/export`의 `_selected` 파일명 접미어와
    # 같은 방식의 양방향 계약).
    ids: list[int] | None = None


@router.get("/{job_id}/results", response_model=ResultListResponse)
async def list_results(
    job_id: int,
    page: int = 1,
    page_size: int = 50,
    ids_only: bool = False,
    parse_status: str | None = None,
    excluded_by_revenue: bool | None = None,
    excluded_by_assets: bool | None = None,
    excluded_by_stale_disclosure: bool | None = None,
    has_disclosure: bool | None = None,
    q: str | None = None,
    auditor_changed: bool | None = None,
    parse_status_ext: str | None = None,
    auditor_changed_ext: str | None = None,
    revenue_min: int | None = None,
    revenue_max: int | None = None,
    assets_min: int | None = None,
    assets_max: int | None = None,
    corp_name_contains: str | None = None,
    corp_name_not_contains: str | None = None,
    address_contains: str | None = None,
    address_not_contains: str | None = None,
    induty_name_in: list[str] | None = Query(default=None),
    induty_name_not_in: list[str] | None = Query(default=None),
    auditor_name_in: list[str] | None = Query(default=None),
    auditor_name_not_in: list[str] | None = Query(default=None),
    audit_opinion_in: list[str] | None = Query(default=None),
    audit_opinion_not_in: list[str] | None = Query(default=None),
    sort: str | None = None,
    sort_by: str | None = None,
    sort_dir: str = "asc",
    db: Session = Depends(get_db),
) -> ResultListResponse:
    """결과 목록 페이징 조회.

    - `parse_status`: OK/PARTIAL/FAILED 중 하나로 필터.
    - `excluded_by_revenue`: true/false — 매출액 사후 필터로 제외된 건만/제외되지 않은 건만.
    - `excluded_by_assets`: true/false — 총자산 사후 필터로 제외된 건만/제외되지 않은 건만
      (§4-7-2, 2026-07-15 추가).
    - `excluded_by_stale_disclosure`: true/false — 최근 1년 이내 DART 공시(외부감사관련,
      F유형)가 없는(=폐업/휴면/합병소멸 추정) 건만/아닌 건만 (2026-07-21 추가). 값을
      주지 않으면 필터하지 않는다(다른 excluded_by_* 와 동일한 tri-state 패턴).
    - `has_disclosure`: true/false — 감사보고서 공시를 찾은 건만/못 찾은 건만
      (`rcept_no` 유무, 2026-07-20 추가). `parse_status=FAILED`와 함께 쓰면
      "실제 파싱 실패(검수 필요)"와 "원문 자체가 없음"을 구분할 수 있다.
    - `q`: 회사명/주소/대표자/업종/감사인명 부분일치 검색 (2026-07-20 추가).
    - `auditor_changed`: true/false — 연도별 감사인이 바뀐 건만/바뀌지 않은 건만
      (2026-07-26 추가). 판정 불가(null)인 건은 두 경우 모두에서 빠진다.

    **결과화면 컬럼 필터(§4-13-B, 2026-08-05 추가)** — 화면의 탭을 대체하는
    체크박스/범위 입력이 그대로 매핑되는 파라미터 6개다. 전부 선택이고, 기존
    파라미터와 함께 주면 AND로 결합된다:

    - `parse_status_ext`: 파싱상태 4분류를 **콤마 구분 목록**으로(예:
      `?parse_status_ext=OK,PARTIAL`). 값은 `OK`/`PARTIAL`/`FAILED_REVIEW`/
      `NO_DISCLOSURE`이며 대소문자를 가리지 않는다. 네 값은 그 Job의 행을
      겹침없이·빠짐없이 나누므로 **넷을 다 주면 파라미터를 안 준 것과 같은 집합**
      이다. 정의는 `PARSE_STATUS_EXT_VALUES` 주석 참고 — 특히 `FAILED_REVIEW`는
      "원문(rcept_no)은 있는데 OK/PARTIAL이 아닌 전부"(미파싱 포함)라 화면의
      "파싱 실패(검수 필요)" 탭보다 넓을 수 있다. 목록에 없는 값은 400,
      빈 값(`?parse_status_ext=`)은 "아무 것도 선택 안 함" = **0건**이다.
    - `auditor_changed_ext`: 감사인 변동 3분류 콤마 구분 목록 —
      `CHANGED`(=1) / `UNCHANGED`(=0) / `UNKNOWN`(=NULL, 판정 불가).
      기존 불리언 `auditor_changed`로는 "판정 불가만" 같은 조합을 표현할 수 없어
      추가했다(기존 파라미터는 그대로 살아 있다).
    - `revenue_min`/`revenue_max`: **실측 파싱값** `revenue_cur`(원 단위 정수)의
      범위. Job 생성 시점 조건(`excluded_by_revenue`)과 전혀 다른 축이다.
    - `assets_min`/`assets_max`: 같은 방식의 `total_assets_cur` 범위.
    - **금액 범위 4개는 값이 NULL인 행을 걸러내지 않는다** — 매출액/총자산을 못
      얻은 회사(파싱 실패·부분 성공)는 범위를 걸어도 계속 보인다(2026-08-05 사용자
      확정, `_apply_amount_range()` 주석 참고). 조건은 `IS NULL OR BETWEEN`이다.

    **텍스트/값 목록 컬럼 필터(§4-14, 2026-08-05 추가)** — 회사명·주소·업종·감사인·
    감사의견 5개 컬럼의 헤더 필터다. **모든 컬럼이 포함(선택)과 제외를 모두 지원**하며,
    서로 다른 컬럼끼리는 물론 같은 컬럼의 포함/제외끼리도 AND로 결합된다:

    - `corp_name_contains` / `corp_name_not_contains`: 회사명 **부분일치**(대소문자
      무시). 사용자가 친 글자는 그대로 찾는다 — `%`/`_`/`\\`는 LIKE 와일드카드가
      아니라 리터럴로 이스케이프된다. 값이 공백뿐이면 필터하지 않는다(검색어 `q`와
      같은 방침). 200자를 넘으면 400.
    - `address_contains` / `address_not_contains`: 주소에 같은 규칙.
    - `induty_name_in` / `induty_name_not_in`, `auditor_name_in` /
      `auditor_name_not_in`, `audit_opinion_in` / `audit_opinion_not_in`:
      **값 목록 다중 선택**. 값은 **완전 일치**이고, 같은 파라미터를 여러 번 반복해
      넘긴다(`?induty_name_in=A&induty_name_in=B`) — 값 자체에 쉼표가 들어갈 수 있어
      `parse_status_ext`류의 콤마 구분 목록을 쓰지 않았다. 고를 값의 목록은
      `GET /api/jobs/{id}/results/distinct-values`가 준다.
      값 개수 500개/값 길이 500자를 넘으면 400이다.
    - **"값 없음"은 `__BLANK__` 예약 토큰으로 명시적으로 고른다**(NULL 또는 빈 문자열).
      `?auditor_name_in=__BLANK__`면 감사인을 확보하지 못한 회사만,
      `?auditor_name_not_in=__BLANK__`면 그 회사들만 빠진다.
    - **NULL/빈 값 행의 기본 처리**: *제외* 조건에서는 항상 통과하고(`__BLANK__`를
      제외 목록에 넣었을 때만 빠진다), *포함* 조건에서는 빠진다(`__BLANK__`를
      포함 목록에 넣었을 때만 남는다). 금액 범위(항상 통과)와 규칙이 다른 이유는,
      값 목록에는 "값 없음"을 **직접 고를 수단**이 있어 조용히 사라지는 일이 없기
      때문이다(`distinct-values` 응답의 `blank_count`가 그 존재를 화면에 알린다).
    - 목록형 파라미터의 빈 값 규약: `?induty_name_in=`(빈 문자열)은 **아무 것도 선택
      안 함 = 0건**이고(체크박스를 전부 끈 상태), `?induty_name_not_in=`은 **뺄 것이
      없음 = 필터 없음**이다. 파라미터를 아예 안 주면 둘 다 필터 없음이다.

    - `sort`: 다중 컬럼 정렬 — 콤마 구분 `field:dir` 목록(예:
      `corp_name:asc,induty_name:desc`). 앞쪽이 1순위, 뒤쪽이 보조 정렬이다.
      화이트리스트(`SORTABLE_COLUMNS`) 밖 컬럼/형식 오류는 무시한다.
    - `sort_by`/`sort_dir`: 레거시 단일 정렬 컬럼과 방향(`asc`/`desc`, 기본
      오름차순). `sort`가 있으면 무시된다(하위호환). 값이 없는 행은 항상 뒤로 보낸다.
    - `ids_only`: true면 **페이징 없이** 위 필터를 통과한 `results.id` 전체를
      `ids`에 담아 돌려준다(아래 참고). 화면의 "현재 필터 전체 선택"(체크박스)이
      페이지를 넘나들지 않고 한 번에 선택 목록을 만들기 위한 옵션이다(2026-08-03).

    **`ids_only=true`의 응답 규약** (§4-11 "선택 항목 다운로드/보고서 생성"의 전제):

    - `items`는 **항상 빈 배열**이고 `ids`가 정렬 순서 그대로 전체 id를 담는다.
      `page`/`page_size` 파라미터는 무시하며(전체를 한 번에 준다), 응답의
      `page`는 1, `page_size`는 반환한 id 개수(=`total`)로 채운다.
    - 전체 필드를 담은 `items`(컬럼 73개 — 2026-08-05 세부계정 5항목 노출로 63개에서
      늘었다. 아래 크기 수치는 63개 시점 실측이라 지금은 그보다 크다)를 다시 내려받지 않기
      위해 SQL 자체가 `results.id` 한 컬럼만 SELECT한다 — 4,383건 기준 응답이
      약 8.5MB → 약 26KB로 줄고, `page_size` 상한(500) 때문에 필요했던 9회
      페이지 순회도 1회로 줄어든다.
    - `total`은 반환한 `ids`의 개수와 **항상 일치**한다(같은 쿼리에서 세므로).
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    stmt = _build_results_query(
        job_id,
        parse_status,
        excluded_by_revenue,
        excluded_by_assets,
        excluded_by_stale_disclosure,
        has_disclosure,
        q,
        auditor_changed,
        parse_status_ext=parse_status_ext,
        auditor_changed_ext=auditor_changed_ext,
        revenue_min=revenue_min,
        revenue_max=revenue_max,
        assets_min=assets_min,
        assets_max=assets_max,
        corp_name_contains=corp_name_contains,
        corp_name_not_contains=corp_name_not_contains,
        address_contains=address_contains,
        address_not_contains=address_not_contains,
        induty_name_in=induty_name_in,
        induty_name_not_in=induty_name_not_in,
        auditor_name_in=auditor_name_in,
        auditor_name_not_in=auditor_name_not_in,
        audit_opinion_in=audit_opinion_in,
        audit_opinion_not_in=audit_opinion_not_in,
    )

    sort_specs = _resolve_sort_specs(sort, sort_by, sort_dir)

    if ids_only:
        # id 한 컬럼만 SELECT한다(`with_only_columns`) — ORM 엔티티를 만들지 않으므로
        # 수천 건이어도 메모리/직렬화 비용이 무시할 수준이다. 정렬은 목록과 동일하게
        # 적용해 "화면에 보이는 순서 = 선택 순서"가 되게 한다(`_apply_sort`의 NULL
        # 후순위 규칙도 그대로 따라간다 — ORDER BY 컬럼이 SELECT 목록에 없어도 무방).
        ids = list(
            db.execute(_apply_sort(stmt.with_only_columns(Result.id), sort_specs))
            .scalars()
            .all()
        )
        return ResultListResponse(
            total=len(ids), page=1, page_size=len(ids), items=[], ids=ids
        )

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()

    page = max(page, 1)
    page_size = max(min(page_size, 500), 1)
    rows = (
        db.execute(
            _apply_sort(stmt, sort_specs).offset((page - 1) * page_size).limit(page_size)
        )
        .scalars()
        .all()
    )

    return ResultListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[ResultResponse.model_validate(r) for r in rows],
    )


class DistinctValueItem(BaseModel):
    """값 목록 한 항목 — 값과 그 값을 가진 결과 행 수."""

    value: str
    count: int


class DistinctValuesResponse(BaseModel):
    """컬럼별 "지금 이 Job 데이터의 값 목록"(§4-14, 2026-08-05).

    엑셀 컬럼 필터의 값 체크박스 목록에 해당한다. §4-13-C에서 "백엔드에
    distinct-value 조회가 없어 범위 밖(향후 과제)"이라고 남겨 뒀던 부분이다.
    """

    field: str
    # 개수 내림차순 → 값 오름차순. 화면이 가나다순으로 다시 정렬해도 무방하다.
    values: list[DistinctValueItem]
    # `values`에 담긴 개수와 `limit`을 적용하기 전 실제 고유값 개수. 둘이 다르면
    # `truncated=true`이며, 화면은 "검색으로 좁혀 주세요" 안내를 띄워야 한다 —
    # 잘린 줄 모르고 "전부 선택"하면 화면에 안 보이는 값이 조용히 빠진다.
    total_distinct: int
    truncated: bool
    # 그 컬럼이 NULL이거나 빈 문자열인 행 수("값 없음"). `values`에는 넣지 않고
    # 별도로 알려준다 — 화면은 이 값을 "(값 없음) N건" 항목으로 그리고, 선택하면
    # `?field_in=__BLANK__`(= `blank_token`)를 보낸다. 0이면 항목을 그리지 않는다.
    blank_count: int
    # 위 "값 없음" 항목이 쓸 예약 토큰. 화면이 문자열을 하드코딩하지 않도록 응답에
    # 실어 준다(백엔드가 토큰을 바꿔도 화면이 따라온다).
    blank_token: str = BLANK_VALUE_TOKEN
    # 그 Job의 전체 결과 행 수(참고용) — `blank_count`/각 `count`의 분모.
    total_rows: int


@router.get("/{job_id}/results/distinct-values", response_model=DistinctValuesResponse)
async def list_result_distinct_values(
    job_id: int,
    field: str,
    q: str | None = None,
    limit: int = _DISTINCT_VALUES_DEFAULT_LIMIT,
    db: Session = Depends(get_db),
) -> DistinctValuesResponse:
    """컬럼 헤더 필터가 보여줄 **값 목록**을 그 Job의 데이터에서 뽑아 준다(§4-14).

    - `field`: `induty_name`/`auditor_name`/`audit_opinion` 중 하나
      (`DISTINCT_VALUE_FIELDS`). 그 외 값은 **400**이다 — 임의 컬럼명을 그대로
      SELECT/GROUP BY에 넣지 않기 위한 화이트리스트로, 정렬(`SORTABLE_COLUMNS`)과
      같은 방식이다. 회사명/주소는 고유값이 행 수와 사실상 같아 목록으로 고를 수
      없으므로 일부러 빠져 있다(그쪽은 `*_contains` 텍스트 필터가 담당).
    - `q`: 값 자체를 부분일치로 좁힌다(대소문자 무시, 와일드카드는 리터럴 이스케이프).
      감사인처럼 값이 수백 개인 컬럼에서 화면이 "값 검색"을 제공하기 위한 것이다.
    - `limit`: 1~2000(기본 1000)으로 클램프한다. 잘렸는지는 `truncated`로 알린다.

    **집계 범위는 그 Job의 결과 전체이고, 화면에 걸려 있는 다른 필터는 반영하지
    않는다.** 다른 필터를 반영하면 지금 화면에서 빠져 있는 값이 목록에서도 사라져
    **"안 보이는 값을 다시 켜기"가 불가능**해진다(필터를 한 번 잘못 걸면 되돌릴 수
    없는 상태가 된다). 읽기 전용이고 DB 스키마 변경·외부 API 호출 0건이라 기존 완료
    Job에서도 그대로 동작한다.
    """
    if field not in DISTINCT_VALUE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"값 목록을 조회할 수 없는 컬럼입니다: {field!r} "
                f"(가능한 값: {', '.join(DISTINCT_VALUE_FIELDS)})"
            ),
        )

    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    column = getattr(Result, field)
    limit = max(min(limit, _DISTINCT_VALUES_MAX_LIMIT), 1)
    keyword = _normalize_text_filter(q, "q")

    # 값이 있는 행만 그룹화한다 — "값 없음"은 blank_count로 따로 센다.
    conditions = [Result.job_id == job_id, column.is_not(None), column != ""]
    if keyword is not None:
        conditions.append(column.ilike(_like_pattern(keyword), escape=_LIKE_ESCAPE_CHAR))

    count_col = func.count().label("cnt")
    rows = db.execute(
        select(column, count_col)
        .where(*conditions)
        .group_by(column)
        .order_by(count_col.desc(), column.asc())
        .limit(limit)
    ).all()
    total_distinct = db.execute(
        select(func.count(distinct(column))).where(*conditions)
    ).scalar_one()

    # blank_count/total_rows는 `q`(값 검색어)와 무관한 Job 전체 기준이다 — "값 없음"
    # 항목은 글자로 검색되는 대상이 아니라 목록 맨 아래 고정 항목이기 때문이다.
    blank_count = db.execute(
        select(func.count()).where(Result.job_id == job_id, _is_blank(column))
    ).scalar_one()
    total_rows = db.execute(
        select(func.count()).where(Result.job_id == job_id)
    ).scalar_one()

    return DistinctValuesResponse(
        field=field,
        values=[DistinctValueItem(value=value, count=count) for value, count in rows],
        total_distinct=total_distinct,
        truncated=total_distinct > len(rows),
        blank_count=blank_count,
        total_rows=total_rows,
    )


class ExcludeResultRequest(BaseModel):
    excluded: bool


@router.patch("/{job_id}/results/{result_id}/exclude", response_model=ResultResponse)
async def set_result_excluded(
    job_id: int,
    result_id: int,
    payload: ExcludeResultRequest,
    db: Session = Depends(get_db),
) -> ResultResponse:
    """후보 목록(Phase 1, CandidatesView)에서 특정 회사를 재무정보 수집 대상에서
    제외/재포함한다 — "선택 취소" 기능(2026-07-18 추가).

    `excluded_manually` 플래그만 토글하므로 phase=CANDIDATES인 동안은 자유롭게
    다시 켤 수 있다. 실제 삭제(행 제거)는 하지 않고, `POST
    /api/jobs/{id}/start-financials` 호출 시점에 제외 표시된 행을 일괄 삭제한다
    (`app/api/jobs.py::start_financials`) — Phase 2 파이프라인(B1~B5)은 전혀
    수정할 필요가 없다. phase=FINANCIALS로 전환된 뒤(이미 확정 처리에 들어간
    결과)에는 의미가 없으므로 400으로 거부한다.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")
    if job.phase != JobPhase.CANDIDATES:
        raise HTTPException(
            status_code=400,
            detail="후보 확정(Phase 1) 단계에서만 선택을 변경할 수 있습니다.",
        )

    result = db.get(Result, result_id)
    if result is None or result.job_id != job_id:
        raise HTTPException(status_code=404, detail="해당 Job의 결과를 찾을 수 없습니다.")

    result.excluded_manually = 1 if payload.excluded else 0
    db.commit()
    db.refresh(result)
    return ResultResponse.model_validate(result)


_EXPORT_CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8-sig",
}


def _parse_export_ids(ids: str | None) -> list[int] | None:
    """`?ids=101,102,105` 쿼리 문자열을 `results.id` 목록으로 파싱한다(§4-11).

    파라미터 자체가 없으면 `None`(=선택 다운로드가 아님, 기존 필터 기반 전체
    내보내기)을 돌려주고, 빈 문자열/쉼표만 있는 값은 빈 리스트를 돌려준다
    (=선택 0건 → 헤더만 있는 빈 파일). 정수가 아닌 토큰이 섞이면 400이다 —
    조용히 버리면 "체크했는데 파일에 없다"가 되어 오히려 위험하다.
    중복 id는 처음 것만 남기고 순서를 보존한다.

    파이썬 `int()`는 자릿수 제한이 없어 `2**63` 이상도 파싱에 성공하지만, 그 값을
    그대로 `IN (...)`에 바인딩하면 SQLite가 `OverflowError`를 내 500이 된다
    (dart-qa 2026-07-28 실측) — 그래서 **SQLite INTEGER 범위(양수 int64)를 벗어난
    값도 여기서 400으로 처리**한다. `results.id`는 AUTOINCREMENT 양수라 0/음수는
    애초에 존재할 수 없다.
    """
    if ids is None:
        return None
    parsed: list[int] = []
    seen: set[int] = set()
    for token in (t.strip() for t in ids.split(",")):
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"ids에 정수가 아닌 값이 있습니다: {token!r}",
            ) from None
        if not 0 < value <= 2**63 - 1:
            raise HTTPException(
                status_code=400,
                detail=f"ids에 결과 id로 쓸 수 없는 값이 있습니다: {token!r}",
            )
        if value in seen:
            continue
        seen.add(value)
        parsed.append(value)
    return parsed


@router.get("/{job_id}/export")
async def export_job_results(
    job_id: int,
    format: str = "xlsx",
    ids: str | None = None,
    include_history: bool = False,
    parse_status: str | None = None,
    excluded_by_revenue: bool | None = None,
    excluded_by_assets: bool | None = None,
    excluded_by_stale_disclosure: bool | None = None,
    has_disclosure: bool | None = None,
    q: str | None = None,
    auditor_changed: bool | None = None,
    parse_status_ext: str | None = None,
    auditor_changed_ext: str | None = None,
    revenue_min: int | None = None,
    revenue_max: int | None = None,
    assets_min: int | None = None,
    assets_max: int | None = None,
    corp_name_contains: str | None = None,
    corp_name_not_contains: str | None = None,
    address_contains: str | None = None,
    address_not_contains: str | None = None,
    induty_name_in: list[str] | None = Query(default=None),
    induty_name_not_in: list[str] | None = Query(default=None),
    auditor_name_in: list[str] | None = Query(default=None),
    auditor_name_not_in: list[str] | None = Query(default=None),
    audit_opinion_in: list[str] | None = Query(default=None),
    audit_opinion_not_in: list[str] | None = Query(default=None),
    sort: str | None = None,
    sort_by: str | None = None,
    sort_dir: str = "asc",
    db: Session = Depends(get_db),
) -> Response:
    """결과 파일 다운로드 (xlsx/csv, 페이징 없이 필터를 통과한 전체 결과).

    `parse_status`/`excluded_by_revenue`/`excluded_by_assets`/
    `excluded_by_stale_disclosure`/`has_disclosure`/`q`/`auditor_changed`/
    `parse_status_ext`/`auditor_changed_ext`/`revenue_min`/`revenue_max`/
    `assets_min`/`assets_max`/`corp_name_contains`/`corp_name_not_contains`/
    `address_contains`/`address_not_contains`/`induty_name_in`/`induty_name_not_in`/
    `auditor_name_in`/`auditor_name_not_in`/`audit_opinion_in`/`audit_opinion_not_in`/
    `sort`/`sort_by`/`sort_dir`는 `/results`와 동일한 의미다(다중 정렬 `sort`와
    §4-13-B 컬럼 필터 6개 + §4-14 텍스트/값 목록 필터 10개 포함) — 화면에서 걸러
    놓고 정렬한 그대로를 내려받게 된다. `format`이 xlsx/csv가 아니면 400.

    **다중 선택 다운로드(§4-11, 2026-07-28)** — 위 "필터 전체" 내보내기와 별개로,
    화면에서 체크박스로 고른 회사만 받는 두 파라미터를 지원한다:

    - `ids`: 쉼표 구분 `results.id` 목록(예: `?ids=101,102,105`). 지정되면 위
      필터·정렬 파라미터는 **전부 무시**하고 정확히 그 id들만 id 오름차순으로
      내보낸다 — 사용자는 이미 화면에서 필터로 찾아 체크한 뒤이므로 다운로드
      시점에 필터를 다시 태우면 "체크했는데 파일에 없다"가 된다. 다른 Job의
      결과 id가 섞여 있으면 400(`job_id` 스코프 검증). 빈 값이면 헤더만 있는
      빈 파일이다.
    - `include_history`: true면 `financial_snapshots`(회사×회계연도)를 담은
      `financial_history` 시트를 추가한 **2시트 xlsx**로 응답한다. csv는 다중
      시트를 표현할 수 없으므로 `format=csv`와 함께 오면 400이다. 이 시트는
      2026-07-29부터 **long 포맷**이다 — 결과ID/회사명/회계연도/접수번호/
      재무제표명/계정과목/금액 7컬럼으로, 스냅샷 1건(회사×회계연도)이 재무
      19항목만큼 19행으로 풀린다(값이 없는 계정과목도 금액만 빈 행으로 남는다).
      "재무제표명"은 재무상태표/손익계산서/현금흐름표 중 하나이며, 이전에 있던
      감사인·파싱상태 컬럼은 제거됐다. 정렬은 `result_id`→`fiscal_year`
      오름차순이고 각 스냅샷 안에서는 재무상태표→손익계산서→현금흐름표 순이다.

    **컬럼 구성(2026-07-28 변경)**: `ids`가 지정된 선택 다운로드의 기본정보는
    기본정보 15컬럼 + "계정과목명"/"금액" + "파싱상태" **long 포맷**(회사 1건 =
    당기 계정과목 19행, 값이 없는 계정과목도 금액만 비운 행으로 남는다)이다.
    `ids` 없는 필터 전체 내보내기는 `include_history` 여부와 **무관하게** 기존
    wide 포맷(당기/전기 한 행) 그대로다 — 포맷을 가르는 기준은 `ids` 유무 하나뿐이다.
    """
    if format not in _EXPORT_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 format입니다: {format!r} (xlsx 또는 csv만 가능)",
        )
    if include_history and format != "xlsx":
        raise HTTPException(
            status_code=400,
            detail="include_history=true는 format=xlsx에서만 지원합니다(csv는 다중 시트 불가).",
        )

    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    selected_ids = _parse_export_ids(ids)
    if selected_ids is not None:
        # 선택 다운로드 — 필터/정렬을 타지 않고 지정된 id만, 항상 id 오름차순으로.
        rows = (
            db.execute(
                select(Result)
                .where(Result.job_id == job_id, Result.id.in_(selected_ids))
                .order_by(Result.id.asc())
            )
            .scalars()
            .all()
            if selected_ids
            else []
        )
        _assert_ids_belong_to_job(selected_ids, {r.id for r in rows})
    else:
        stmt = _build_results_query(
            job_id,
            parse_status,
            excluded_by_revenue,
            excluded_by_assets,
            excluded_by_stale_disclosure,
            has_disclosure,
            q,
            auditor_changed,
            parse_status_ext=parse_status_ext,
            auditor_changed_ext=auditor_changed_ext,
            revenue_min=revenue_min,
            revenue_max=revenue_max,
            assets_min=assets_min,
            assets_max=assets_max,
            corp_name_contains=corp_name_contains,
            corp_name_not_contains=corp_name_not_contains,
            address_contains=address_contains,
            address_not_contains=address_not_contains,
            induty_name_in=induty_name_in,
            induty_name_not_in=induty_name_not_in,
            auditor_name_in=auditor_name_in,
            auditor_name_not_in=auditor_name_not_in,
            audit_opinion_in=audit_opinion_in,
            audit_opinion_not_in=audit_opinion_not_in,
        )
        sort_specs = _resolve_sort_specs(sort, sort_by, sort_dir)
        rows = db.execute(_apply_sort(stmt, sort_specs)).scalars().all()

    # 기본정보 시트/파일의 컬럼 구성은 **오직 `ids` 유무**로 갈린다 — 선택
    # 다운로드는 회사 × 계정과목 long 포맷, 필터 전체 내보내기는 기존 wide 포맷이다
    # (`include_history`는 `financial_history` 시트를 덧붙일 뿐 포맷에 관여하지
    # 않는다, dart-qa 2026-07-28 리뷰 반영).
    use_selection_format = selected_ids is not None

    # pandas/openpyxl 직렬화는 순수 동기 CPU 작업이라 `async def` 핸들러 안에서
    # 그대로 부르면 그 시간 동안 이벤트 루프 전체가 멈춘다 — Job 진행률 폴링을
    # 포함한 다른 모든 요청이 같이 대기한다. `financial_history` 시트를 long
    # 포맷으로 바꾼 뒤(2026-07-29) 개발 DB 최대 규모(결과 4,383건 × 4개 회계연도
    # = 333,108행)에서 약 45초가 걸리는 것이 실측돼(dart-qa), 아래 세 분기의
    # 직렬화 호출만 `run_in_threadpool`로 워커 스레드에 넘긴다. DB 조회는 동기
    # SQLAlchemy Session 그대로이며(세션을 스레드 간에 동시 사용하지 않는다 —
    # 위에서 이미 전부 로드해 둔 ORM 객체만 워커가 읽는다) 이 변경 범위 밖이다.
    if include_history:
        result_ids = [r.id for r in rows]
        snapshots = (
            db.execute(
                select(FinancialSnapshot)
                .where(FinancialSnapshot.result_id.in_(result_ids))
                .order_by(
                    FinancialSnapshot.result_id.asc(), FinancialSnapshot.fiscal_year.asc()
                )
            )
            .scalars()
            .all()
            if result_ids
            else []
        )
        content = await run_in_threadpool(
            export_results_with_history,
            rows,
            snapshots,
            {r.id: r.corp_name for r in rows},
            use_selection_format=use_selection_format,
        )
    elif use_selection_format:
        content = await run_in_threadpool(export_selection_results, rows, format)
    else:
        content = await run_in_threadpool(export_results, rows, format)

    # 주의: 프론트엔드(`frontend/src/api/results.ts`)가 이 `_selected` 접미어 유무로
    # 서버가 `ids`를 지원하는지(구버전 여부)를 판정하는 fail-safe 근거로 쓴다 —
    # 이 문자열을 바꾸면 프론트가 모든 선택 다운로드를 "서버 구버전"으로 오판해
    # 차단한다(양방향 잠금: tests/test_api_results.py의 `_selected` 접미어 테스트).
    suffix = "_selected" if selected_ids is not None else ""
    suffix += "_with_history" if include_history else ""
    filename = f"dart_search_job{job_id}_results{suffix}.{format}"
    return Response(
        content=content,
        media_type=_EXPORT_CONTENT_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class GenerateReportRequest(BaseModel):
    """선택 항목 보고서 생성 요청 — 체크박스로 고른 `results.id` 목록."""

    ids: list[int] = Field(..., min_length=1)


class GeneratedReportFileResponse(BaseModel):
    result_id: int | None
    corp_name: str | None
    filename: str


class ReportWarningResponse(BaseModel):
    result_id: int | None
    corp_name: str | None
    message: str


class SkippedReportResponse(BaseModel):
    """보고서를 만들지 않고 건너뛴 회사(2026-08-03 추가)."""

    result_id: int | None
    corp_name: str | None
    reason: str


class GenerateReportResponse(BaseModel):
    """생성 결과 — 화면에 "이 경로에 저장됐습니다"를 그대로 보여줄 수 있는 형태."""

    # 생성된 폴더의 절대 경로(예: `C:\\claude\\dart-search\\backend\\report\\2026-08-03`)
    output_dir: str
    generated_count: int
    files: list[GeneratedReportFileResponse]
    # 같은 폴더에 함께 만든 우편 발송용 라벨 엑셀 파일명
    label_file: str
    # 생성은 됐지만 검수가 필요한 회사에 대한 경고(부분 파싱 값 포함, 결측 연도
    # 제외 등) + 생성하지 않은 회사 안내. 요청 전체의 실패가 아니다.
    warnings: list[ReportWarningResponse]
    # 쓸 수 있는 재무 이력이 없어 **생성하지 않은** 회사 목록(2026-08-03).
    # 선택 건수 != generated_count인 이유가 여기에 있다.
    skipped: list[SkippedReportResponse] = []


def _validate_report_ids(ids: Sequence[int]) -> list[int]:
    """요청 바디의 `ids`를 정규화한다(중복 제거, 순서 보존, 범위 검증).

    `_parse_export_ids()`와 같은 이유로 SQLite INTEGER 범위(양수 int64)를 벗어난
    값을 400으로 막는다 — 그대로 `IN (...)`에 바인딩하면 `OverflowError`로 500이 된다.
    """
    normalized: list[int] = []
    seen: set[int] = set()
    for value in ids:
        if not 0 < value <= 2**63 - 1:
            raise HTTPException(
                status_code=400,
                detail=f"ids에 결과 id로 쓸 수 없는 값이 있습니다: {value!r}",
            )
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _assert_ids_belong_to_job(
    selected_ids: Sequence[int], found_ids: set[int]
) -> None:
    """선택 id 중 이 Job에 없는 것이 있으면 400 — 선택 기반 3개 경로의 공통 계약.

    `/export?ids=`(선택 다운로드) · `POST /generate-report`(보고서 생성) ·
    `POST /results/selection-summary`(선택 요약)가 **같은 메시지로** 거부해야
    한다 — 요약이 200을 주고 생성이 400을 주면 확인 모달이 거짓 안내를 하게 된다.
    """
    missing = [i for i in selected_ids if i not in found_ids]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"이 Job에 속하지 않는 결과 id가 있습니다: {missing}",
        )


def _load_job_peer_pool(db: Session, job_id: int) -> PeerPool:
    """보고서 비교군(peers/industryAverage/regionGroup)의 후보 풀을 **한 번에** 만든다.

    후보는 "같은 Job에서 수집한 다른 회사들"이라 선택 건수와 무관하게 Job 전체를
    봐야 한다 — 선택 회사마다 조회하면 수백 건 선택 시 그대로 N+1이 되므로,
    **요청당 쿼리 2건**(결과 목록 + 그 Job의 스냅샷 전체)으로 끝낸다.

    - 결과는 회사명/업종/의견만 있으면 되므로 필요한 5컬럼만 SELECT한다
      (`ResultResponse` 73필드를 수천 행 적재하지 않기 위함).
    - 휴면·폐업 추정(`excluded_by_stale_disclosure=1`)은 SQL에서 미리 뺀다.
      `excluded_by_revenue`/`excluded_by_assets`는 **빼지 않는다**(정상 영업 회사라
      비교군으로는 유효하다 — audit_proposal.py "비교군" 주석 참고).
    - 스냅샷은 result_id별로 묶어 넘기기만 하고, 실을 연도 선별/비율 계산은
      `build_peer_pool()`(순수 함수)이 한다.
    """
    rows = db.execute(
        select(
            Result.id,
            Result.corp_name,
            Result.induty_code,
            Result.induty_name,
            Result.audit_opinion,
        ).where(
            Result.job_id == job_id,
            func.coalesce(Result.excluded_by_stale_disclosure, 0) != 1,
        )
    ).all()
    if not rows:
        return PeerPool()

    snapshots_by_result: dict[int, list[FinancialSnapshot]] = defaultdict(list)
    for snapshot in (
        db.execute(
            select(FinancialSnapshot)
            .join(Result, FinancialSnapshot.result_id == Result.id)
            .where(Result.job_id == job_id)
            .order_by(FinancialSnapshot.result_id.asc(), FinancialSnapshot.fiscal_year.asc())
        )
        .scalars()
        .all()
    ):
        snapshots_by_result[snapshot.result_id].append(snapshot)

    return build_peer_pool(
        ReportInput(result=row, snapshots=snapshots_by_result.get(row.id, ()))
        for row in rows
        if snapshots_by_result.get(row.id)
    )


@router.post("/{job_id}/generate-report", response_model=GenerateReportResponse)
async def generate_selection_report(
    job_id: int,
    payload: GenerateReportRequest,
    db: Session = Depends(get_db),
) -> GenerateReportResponse:
    """선택한 회사들의 **감사 수임 제안 보고서(HTML)** 를 로컬 폴더에 생성한다.

    "선택 항목 다운로드"(`GET /export?ids=...`)가 파일을 브라우저로 내려보내는
    것과 달리, 이 엔드포인트는 **서버(=사용자 PC) 로컬 폴더에 파일을 떨궈 놓고
    그 경로를 알려준다** — 회사마다 HTML이 1개씩 나오고 우편 발송용 라벨 엑셀이
    함께 만들어지므로, 브라우저 다운로드보다 폴더로 받는 편이 쓰기 쉽기 때문이다.

        <REPORT_OUTPUT_DIR>/YYYY-MM-DD[_2, _3 ...]/
            ├─ (주)OO산업.html   ← 회사 1건 = 파일 1개(파일명은 회사명)
            └─ 발송처_목록.xlsx   ← 회사명/주소 2컬럼

    같은 날 다시 호출하면 기존 폴더를 덮어쓰지 않고 `_2`, `_3` ... 폴더를 새로 만든다.

    이 앱은 로컬 오피스 PC의 단일 사용자 도구라(CLAUDE.md) 선택 건수만큼을 그
    자리에서 동기 처리한다 — Job 크롤링처럼 백그라운드로 빼지 않는다. 다만 템플릿
    렌더링·파일 쓰기는 동기 I/O라 `run_in_threadpool`로 넘겨 진행률 폴링 등 다른
    요청을 막지 않는다(`/export`와 동일한 처리).

    - 다른 Job의 결과 id가 섞여 있으면 400이다(`/export?ids=`와 동일한 스코프 검증).
    - **쓸 수 있는 재무 이력이 한 연도도 없는 회사는 생성하지 않고** `skipped`와
      `warnings`에 남긴다(요청 전체는 성공이다). 빈 재무이력을 넣으면 템플릿 렌더가
      도중에 중단돼 연락처도 없는 반쪽 문서가 나오기 때문이다(§4-12 참고).
    - 파싱 실패(FAILED) 연도는 제외하고, 부분 파싱(PARTIAL)·전기 유래 연도가 실린
      경우 `warnings`로 알린다 — 검수되지 않은 값이 대외 문서에 섞였다는 사실이
      사용자에게 보여야 한다.
    - 파일 저장 실패(권한/디스크 공간 등)는 500 스택트레이스 대신 사용자가 읽을 수
      있는 한국어 메시지와 함께 **507**로 응답한다.
    - 동종업종/지역 비교군(`peers`/`industryAverage`/`regionGroup`, 2026-08-04)은
      **같은 Job에서 이미 수집한 다른 회사들**로 채운다 — 외부 API 호출도 새 스키마도
      없고, 후보 풀은 `_load_job_peer_pool()`이 요청당 한 번만 조회한다.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    selected_ids = _validate_report_ids(payload.ids)
    rows = (
        db.execute(
            select(Result)
            .where(Result.job_id == job_id, Result.id.in_(selected_ids))
            .order_by(Result.id.asc())
        )
        .scalars()
        .all()
    )
    _assert_ids_belong_to_job(selected_ids, {r.id for r in rows})

    # 재무이력은 새로 쿼리하지 않고 상세 Drawer가 쓰는 기존 조회 함수를 그대로
    # 재사용한다 — 연도 오름차순 정렬/소속 검증이 한 곳(`get_result_history`)에만
    # 있어야 화면과 보고서의 값이 갈리지 않는다.
    items = [
        ReportInput(result=row, snapshots=await get_result_history(job_id, row.id, db))
        for row in rows
    ]
    # 비교군 후보 풀은 선택 회사가 아니라 **Job 전체**로 만든다(요청당 1회).
    peer_pool = _load_job_peer_pool(db, job_id)

    try:
        outcome = await run_in_threadpool(partial(generate_reports, items, peer_pool=peer_pool))
    except ReportGenerationError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc

    return GenerateReportResponse(
        output_dir=str(outcome.output_dir),
        generated_count=len(outcome.files),
        files=[
            GeneratedReportFileResponse(
                result_id=f.result_id, corp_name=f.corp_name, filename=f.filename
            )
            for f in outcome.files
        ],
        label_file=outcome.label_filename,
        warnings=[
            ReportWarningResponse(
                result_id=w.result_id, corp_name=w.corp_name, message=w.message
            )
            for w in outcome.warnings
        ],
        skipped=[
            SkippedReportResponse(
                result_id=s.result_id, corp_name=s.corp_name, reason=s.reason
            )
            for s in outcome.skipped
        ],
    )


class SelectionSummaryRequest(BaseModel):
    """선택 요약 조회 요청 — `POST /generate-report`와 **완전히 같은 입력 계약**이다."""

    ids: list[int] = Field(..., min_length=1)


class SelectionSummaryResponse(BaseModel):
    """체크된 회사들의 "위험 신호" 집계 (2026-08-03, §4-12-A).

    화면이 확인 모달에 "선택 N건 중 휴면·폐업 추정 X건이 포함돼 있습니다"를 그대로
    찍을 수 있는 형태다. 필드는 전부 **건수**이며, 한 회사가 여러 조건에 동시에
    걸릴 수 있어(매출액·총자산 필터는 서로 독립 판정이다) **합계가 `total`을 넘을 수
    있다** — 화면에서 더하지 말 것.

    **`failed`와 `no_disclosure`는 반드시 분리해서 읽어야 한다** (2026-08-03
    dart-qa 실측 반영). Phase 2 B1이 감사보고서 공시를 못 찾은 건에
    `parse_status=FAILED`와 `excluded_by_stale_disclosure=1`을 **동시에** 쓰기
    때문에(`app/core/pipeline.py`), FAILED 전체를 세면 "검수 필요"가 실제보다
    수천 배 부풀려진다(개발 DB 실측: FAILED 2,215건 중 2,214건이 `rcept_no IS
    NULL`이고 진짜 검수 필요는 1건). 그래서 두 필드는 결과 화면의 탭 구분과
    **정확히 같은 조건**을 쓴다:

    - `failed`        = "파싱 실패 (검수 필요)" 탭 (`parse_status=FAILED` **AND**
                        `rcept_no IS NOT NULL`) — 원문은 있는데 못 읽은 건.
    - `no_disclosure` = "감사보고서 없음" 탭 (`rcept_no IS NULL`) — 열어볼 원문이
                        애초에 없어 **검수 대상이 아니다**. 경고로 취급할 필요는
                        없지만, 보고서에 실을 재무 이력이 없을 가능성이 높아
                        화면 안내에 쓸 수 있게 건수를 내려준다.

    `no_history`는 위 둘과 또 다른 축이다 — `financial_snapshots`가 0건인 회사는
    `generate_reports()`가 **생성하지 않고 건너뛰므로**(§4-12), 확인 모달의
    "N건 생성" 문구는 `total`이 아니라 `total - no_history`(=최대 생성 가능
    건수)를 기준으로 써야 한다. 실측(Job 27): 4,383건 중 3,149건이 스냅샷 0건이라
    실제 산출물은 최대 1,234건이었다. 스냅샷이 있어도 전 연도가 파싱 실패/결측이면
    추가로 건너뛸 수 있어 어디까지나 **상한**이다.
    """

    total: int
    stale_disclosure: int
    excluded_revenue: int
    excluded_assets: int
    # `parse_status=FAILED` **이면서** 원문(rcept_no)이 있는 건 = 진짜 검수 필요
    failed: int
    # `rcept_no IS NULL` = 감사보고서 자체가 없는 건(검수 대상 아님)
    no_disclosure: int
    # 재무 이력(financial_snapshots)이 0건이라 보고서 생성이 건너뛰어질 건
    no_history: int


@router.post("/{job_id}/results/selection-summary", response_model=SelectionSummaryResponse)
async def get_selection_summary(
    job_id: int,
    payload: SelectionSummaryRequest,
    db: Session = Depends(get_db),
) -> SelectionSummaryResponse:
    """체크된 `results.id` 목록에 대한 요약 집계 (§4-12-A, 2026-08-03).

    "선택 항목 보고서 생성"(`POST /generate-report`)의 **사전 확인 모달**이 쓰는
    읽기 전용 조회다 — 클릭 한 번으로 수천 건이 선택될 수 있고(§4-11-A "현재 필터
    전체 선택"), 선택은 여러 탭을 넘나들며 **합집합으로 누적**되므로 조건에 맞지
    않는 회사(휴면·폐업 추정 / 매출액·총자산 조건 제외 / 파싱 실패)가 조용히 우편
    발송 대상에 섞일 수 있다. 그 사실을 생성 **전에** 보여주기 위한 것이다.

    필터 조건이 아니라 **명시적 id 목록**에 대한 집계라 `GET /results`의 필터
    파라미터에 얹지 않고 별도 엔드포인트로 뒀다(설계 근거는 §4-12-A). 입력·검증·
    에러 계약은 `POST /generate-report`와 완전히 동일하다 — 같은 `ids`로 이 요약이
    200을 받았다면 생성도 같은 이유로 거부되지 않는다:

    - `ids`가 비면 422(pydantic `min_length=1`), 중복은 제거한다.
    - SQLite INTEGER 범위를 벗어난 값은 400(`_validate_report_ids`).
    - 다른 Job의 결과 id가 섞이면 400(`_assert_ids_belong_to_job` — 생성 시와 동일 메시지).

    **`failed` / `no_disclosure` / `no_history`의 의미 구분은
    `SelectionSummaryResponse`의 docstring에 정리돼 있다** — 특히 `failed`는
    화면의 "파싱 실패 (검수 필요)" 탭과 **정확히 같은 조건**(`rcept_no IS NOT
    NULL` 포함)이라, FAILED 전체 건수와 다르다는 점을 주의할 것.

    DB 스키마 변경·추가 API 호출은 0건이다. 집계에 필요한 6개 컬럼만 SELECT하고
    (ORM 엔티티를 만들지 않아 수천 건이어도 가볍다), 재무 이력 유무는 스냅샷의
    `result_id`만 DISTINCT로 한 번 더 조회해 판정한다 — 회사마다
    `get_result_history()`를 부르면 선택 건수만큼 쿼리가 나가고 이력 전체를
    직렬화하게 되는데, 여기서 필요한 건 "0건인가"뿐이다.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    selected_ids = _validate_report_ids(payload.ids)
    rows = db.execute(
        select(
            Result.id,
            Result.rcept_no,
            Result.excluded_by_stale_disclosure,
            Result.excluded_by_revenue,
            Result.excluded_by_assets,
            Result.parse_status,
        ).where(Result.job_id == job_id, Result.id.in_(selected_ids))
    ).all()
    _assert_ids_belong_to_job(selected_ids, {row.id for row in rows})

    # 재무 이력이 1건이라도 있는 result_id 집합 — 나머지가 `no_history`다.
    with_history = set(
        db.execute(
            select(FinancialSnapshot.result_id)
            .where(FinancialSnapshot.result_id.in_(selected_ids))
            .distinct()
        )
        .scalars()
        .all()
    )

    return SelectionSummaryResponse(
        total=len(rows),
        # 기존 완료 Job의 행은 이 플래그들이 NULL일 수 있어(컬럼 추가 시 소급 계산
        # 없음 관례) `== 1`로만 센다 — NULL/0은 모두 "해당 없음"이다.
        stale_disclosure=sum(1 for row in rows if row.excluded_by_stale_disclosure == 1),
        excluded_revenue=sum(1 for row in rows if row.excluded_by_revenue == 1),
        excluded_assets=sum(1 for row in rows if row.excluded_by_assets == 1),
        # 공시를 못 찾은 건에도 파이프라인이 FAILED를 쓰기 때문에(위 docstring),
        # `rcept_no` 유무로 "검수 필요"와 "원문 자체가 없음"을 갈라 센다.
        failed=sum(
            1
            for row in rows
            if row.parse_status == ParseStatus.FAILED and row.rcept_no is not None
        ),
        no_disclosure=sum(1 for row in rows if row.rcept_no is None),
        no_history=sum(1 for row in rows if row.id not in with_history),
    )


class FinancialSnapshotResponse(BaseModel):
    """STEP 7(최근 N년 재무이력)이 채우는 회사-회계연도 단위 스냅샷 1건."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    result_id: int | None
    rcept_no: str | None
    fiscal_year: str

    current_assets: int | None
    noncurrent_assets: int | None
    total_assets: int | None
    current_liab: int | None
    noncurrent_liab: int | None
    total_liab: int | None
    total_equity: int | None
    revenue: int | None
    cogs: int | None
    gross_profit: int | None
    sga: int | None
    operating_income: int | None
    net_income: int | None
    cf_operating: int | None
    cf_investing: int | None
    cf_financing: int | None
    cf_ending_cash: int | None
    non_operating_income: int | None
    non_operating_expense: int | None
    # 세부계정 5항목 (`DETAIL_FINANCIAL_FIELDS`, 2026-08-05) — `ResultResponse`의
    # 같은 이름 `_cur`/`_prv` 필드와 동일한 값의 연도별 판이다(결측 null이 정상,
    # 원천 재무제표 고정 — 위 `ResultResponse` 주석 참고).
    cash_and_equivalents: int | None
    trade_receivables: int | None
    interest_expense: int | None
    depreciation: int | None
    amortization: int | None

    # 그 연도를 **당기**로 감사한 감사인 이름 (2026-07-26). 전기 열 유래 행
    # (`from_current_period=0`)과 이 기능 도입 이전에 수집된 기존 이력은 null이다
    # — 연도별 감사인/주소를 항상 보여줘야 한다면 `account-detail`이 원문을 그
    # 자리에서 열어 돌려주는 `auditor_name`/`auditor_address`를 쓰면 된다.
    auditor_name: str | None
    # 이 연도의 감사인이 **직전에 이름이 확보된 연도**와 다른가 (2026-07-26).
    # true=변경, false=동일, null=판정 불가(이 연도 이름이 없거나 비교할 이전
    # 연도가 없음). 서버가 `results.auditor_changed`와 **동일한 정규화**
    # (`pipeline._auditor_key()`)로 계산해 내려주는 값이다 — 화면이 자체 비교
    # 로직을 다시 구현하면 목록 컬럼과 상세 뱃지의 답이 갈린다(dart-qa 실측 3건).
    auditor_changed_from_prev: bool | None = None

    parse_status: str | None
    parse_note: str | None
    # 1이면 이 연도를 **당기**로 하는 감사보고서에서 나온 값(= rcept_no를 열면
    # 당기가 이 연도), 0이면 다음 연도 공시의 전기 열에서 채워진 값이다.
    # 화면은 0인 연도의 "원문 보기"에 "전기 기준" 라벨을 붙인다(2026-07-20).
    from_current_period: int


@router.get(
    "/{job_id}/results/{result_id}/history",
    response_model=list[FinancialSnapshotResponse],
)
async def get_result_history(
    job_id: int,
    result_id: int,
    db: Session = Depends(get_db),
) -> list[FinancialSnapshotResponse]:
    """회사 1건(result_id)의 연도별 재무 이력을 오래된 연도 → 최신 연도 순으로 반환.

    STEP 7이 `excluded_by_revenue=0`인 결과만 대상으로 채우므로, 매출액
    필터로 제외된 결과는 이력이 비어 있을 수 있다(에러가 아니라 빈 배열).

    연도별 `auditor_changed_from_prev`(2026-07-26)는 오름차순으로 배열된 이
    목록 위에서 서버가 직접 계산한다 — 목록 컬럼(`results.auditor_changed`)과
    같은 `_auditor_key()` 정규화를 쓰므로 두 화면의 답이 어긋나지 않는다.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    result = db.get(Result, result_id)
    if result is None or result.job_id != job_id:
        raise HTTPException(status_code=404, detail="해당 Job의 결과를 찾을 수 없습니다.")

    rows = (
        db.execute(
            select(FinancialSnapshot)
            .where(FinancialSnapshot.result_id == result_id)
            .order_by(FinancialSnapshot.fiscal_year.asc())
        )
        .scalars()
        .all()
    )
    flags = auditor_change_flags([r.auditor_name for r in rows])
    items = []
    for row, flag in zip(rows, flags):
        item = FinancialSnapshotResponse.model_validate(row)
        item.auditor_changed_from_prev = flag
        items.append(item)
    return items


class DocumentSectionResponse(BaseModel):
    """감사보고서 원문 1개 섹션의 서버 조립 HTML (§4-8, 2026-07-19)."""

    section: str
    rcept_no: str
    available: bool  # 해당 섹션을 원문에서 실제로 찾았는지
    html: str  # 조립된 안전 HTML(찾지 못했으면 "")
    notice: str | None = None  # 안내 문구(미첨부/PDF 미지원 등)


def _pick_cached_xml(cache_dir: Path, rcept_no: str) -> Path | None:
    """DOCUMENT_CACHE_DIR/{rcept_no}/ 에서 파싱 대상 XML 1개를 고른다(없으면 None)."""
    target_dir = cache_dir / rcept_no
    if not target_dir.is_dir():
        return None
    xml_files = sorted(target_dir.rglob("*.xml"))
    return xml_files[0] if xml_files else None


def _resolve_target_rcept_no(
    db: Session, job_id: int, result_id: int, rcept_no: str | None
) -> str:
    """원문 열람 대상 rcept_no를 결정하고 소속을 검증한다(원문 섹션/계정 상세 공유).

    기본은 `results.rcept_no`(가장 최근 감사보고서)이고, `?rcept_no=`로 지정하면
    해당 result의 이력 공시(`financial_snapshots.rcept_no`)여야만 허용한다 —
    다른 회사의 원문을 임의 조회하지 못하게 막는다.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    result = db.get(Result, result_id)
    if result is None or result.job_id != job_id:
        raise HTTPException(status_code=404, detail="해당 Job의 결과를 찾을 수 없습니다.")

    if rcept_no is None:
        target_rcept_no = result.rcept_no
    else:
        allowed = {result.rcept_no} | {
            row[0]
            for row in db.execute(
                select(FinancialSnapshot.rcept_no).where(
                    FinancialSnapshot.result_id == result_id
                )
            ).all()
        }
        allowed.discard(None)
        if rcept_no not in allowed:
            raise HTTPException(
                status_code=404,
                detail="해당 결과에 속한 공시(rcept_no)가 아닙니다.",
            )
        target_rcept_no = rcept_no

    if not target_rcept_no:
        raise HTTPException(status_code=404, detail="이 결과에는 원문 공시(rcept_no)가 없습니다.")
    return target_rcept_no


@router.get(
    "/{job_id}/results/{result_id}/document-sections/{section}",
    response_model=DocumentSectionResponse,
)
async def get_document_section(
    job_id: int,
    result_id: int,
    section: str,
    rcept_no: str | None = None,
    db: Session = Depends(get_db),
) -> DocumentSectionResponse:
    """감사보고서 원문의 특정 섹션(bs|is|cf|notes)을 서버 조립 HTML로 반환 (§4-8).

    로컬 문서 캐시(`DOCUMENT_CACHE_DIR`)의 원문 XML을 열어 on-demand로 섹션을
    잘라내므로 추가 API 호출/쿼터가 0건이다. 대상 공시는 기본
    `results.rcept_no`(가장 최근 감사보고서)이고, `?rcept_no=`로 다년치 이력의
    특정 연도 공시(`financial_snapshots.rcept_no`)도 열람할 수 있다 — 단
    해당 result에 속한 rcept_no만 허용한다.
    """
    if section not in SECTION_TITLE_MARKS:
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 섹션입니다: {section!r} (bs|is|cf|notes|audit)",
        )

    target_rcept_no = _resolve_target_rcept_no(db, job_id, result_id, rcept_no)

    cache_dir = Path(get_settings().document_cache_dir)
    xml_path = _pick_cached_xml(cache_dir, target_rcept_no)
    if xml_path is None:
        # PDF만 있는 경우와 캐시 자체가 없는 경우를 구분해 안내한다.
        target_dir = cache_dir / target_rcept_no
        if target_dir.is_dir() and any(target_dir.rglob("*.pdf")):
            return DocumentSectionResponse(
                section=section,
                rcept_no=target_rcept_no,
                available=False,
                html="",
                notice="PDF 원문은 섹션 열람을 지원하지 않습니다(§4-8 — XML 원문만 지원).",
            )
        raise HTTPException(
            status_code=404,
            detail="원문 캐시가 없습니다 — 재수집이 필요합니다.",
        )

    found, html = extract_section_html(xml_path.read_bytes(), section)
    notice = None
    if not found:
        notice = "해당 섹션을 원문에서 찾을 수 없습니다(재무제표/주석 미첨부 등)."
    return DocumentSectionResponse(
        section=section,
        rcept_no=target_rcept_no,
        available=found,
        html=html,
        notice=notice,
    )


class AccountRowResponse(BaseModel):
    """세부계정 1행 — 라벨(원문 그대로)/상대 레벨/당기·전기 값."""

    label: str
    level: int
    cur: float | None = None
    prv: float | None = None


class AccountDetailResponse(BaseModel):
    """요약 필드(current_assets 등) → 그 대분류의 세부계정 목록."""

    rcept_no: str
    fiscal_year_cur: str | None = None
    accounts: dict[str, list[AccountRowResponse]] = {}
    notice: str | None = None
    # 이 원문의 감사의견(적정/한정/부적정/의견거절, 판정 불가 시 None) — 재무이력
    # 표가 연도(=원문)마다 다른 감사의견을 재무상태표 위 안내 행에 보여주는 데 쓴다.
    audit_opinion: str | None = None
    # 이 원문의 감사인 이름과 사무소 주소 (2026-07-26 추가) — 화면이 감사의견
    # **바로 다음 행**에 "누가 감사했는지"를 연도별로 보여주기 위한 값이다.
    # `results.auditor_name`(가장 최근 1건 기준)과 달리 조회한 그 연도 원문에서
    # 매번 새로 뽑으므로, 이 기능 도입 이전에 수집된 기존 Job에서도 값이 나온다
    # (로컬 문서 캐시만 읽어 추가 API 호출 0건). 서명란이 없는 원문은 null.
    auditor_name: str | None = None
    auditor_address: str | None = None


@router.get(
    "/{job_id}/results/{result_id}/account-detail",
    response_model=AccountDetailResponse,
)
async def get_account_detail(
    job_id: int,
    result_id: int,
    rcept_no: str | None = None,
    db: Session = Depends(get_db),
) -> AccountDetailResponse:
    """요약 13항목 대분류별 **세부계정 상세**를 반환한다.

    요약 표에서 "유동자산"을 클릭하면 그 아래 세부계정을 인라인으로 펼치기 위한
    데이터다. 원문 섹션 열람과 동일하게 로컬 문서 캐시만 읽으므로 추가 API
    호출/쿼터가 0건이고, `?rcept_no=`로 다년치 이력의 특정 연도 공시도 조회할
    수 있다. `fiscal_year_cur`는 그 원문의 당기 결산연도로, 재무이력 표가 어느
    열(당기/전기)의 값을 써야 하는지 판정하는 데 쓴다.
    """
    target_rcept_no = _resolve_target_rcept_no(db, job_id, result_id, rcept_no)

    cache_dir = Path(get_settings().document_cache_dir)
    xml_path = _pick_cached_xml(cache_dir, target_rcept_no)
    if xml_path is None:
        target_dir = cache_dir / target_rcept_no
        if target_dir.is_dir() and any(target_dir.rglob("*.pdf")):
            # PDF 원문은 계층 파싱을 지원하지 않는다 — 에러가 아니라 빈 상세로 안내한다.
            return AccountDetailResponse(
                rcept_no=target_rcept_no,
                notice="PDF 원문은 계정 상세를 지원하지 않습니다(XML 원문만 지원).",
            )
        raise HTTPException(
            status_code=404,
            detail="원문 캐시가 없습니다 — 재수집이 필요합니다.",
        )

    raw_bytes = xml_path.read_bytes()
    detail = parse_account_detail(raw_bytes)
    # 감사인은 같은 원문을 한 번 더 훑어 뽑는다(추가 API 호출 없음) — 연도별
    # 상세에서 감사의견 다음 행에 "누가 감사했는지"를 함께 보여주기 위함이다.
    auditor = extract_auditor(raw_bytes)
    return AccountDetailResponse(
        rcept_no=target_rcept_no,
        fiscal_year_cur=detail.fiscal_year_cur,
        accounts={
            field: [
                AccountRowResponse(label=row.label, level=row.level, cur=row.cur, prv=row.prv)
                for row in rows
            ]
            for field, rows in detail.accounts.items()
        },
        audit_opinion=detail.audit_opinion,
        auditor_name=auditor.name,
        auditor_address=auditor.address,
    )
