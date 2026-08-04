"""CSV/Excel 결과 산출.

상세개발계획.md §6 `GET /api/jobs/{id}/export?format=xlsx|csv`. `results`
테이블 레코드를 pandas DataFrame으로 변환해 xlsx(openpyxl 엔진)/csv 바이트로
직렬화한다.

DB 컬럼명(영문)은 `app/models/result.py`(§5 스키마)를 그대로 유지한다
(CLAUDE.md 관례 — 컬럼명을 임의로 바꾸지 않는다, Phase 2 재수집 방지). 한국어
헤더는 파일 출력 시에만 `RESULT_COLUMN_LABELS`로 매핑해 적용하고, DB/API
응답의 필드명 자체는 건드리지 않는다.

2026-07-28(§4-11, M9) 추가: 결과 목록에서 **체크박스로 고른 회사만** 내려받는
"다중 선택 다운로드"를 위해 `financial_snapshots`(회사×회계연도) 시트를 함께
쓰는 `export_results_with_history()`를 더했다. 기존 `RESULT_COLUMN_LABELS`/
`results_to_dataframe()`/`export_results()`는 **무변경**이고 전체 내보내기 동작도
그대로다 — 새 딕셔너리/함수를 옆에 추가했을 뿐이다.

2026-07-28 후속(§4-11): **선택 다운로드의 기본정보 표만** 계정과목을 세로로 푸는
long 포맷(`SELECTION_EXPORT_COLUMN_LABELS`/`results_to_selection_dataframe()`/
`export_selection_results()`)으로 교체했다. 필터 전체 내보내기(`ids` 없는 요청)는
여전히 기존 wide 포맷(`RESULT_COLUMN_LABELS`/`export_results()`)을 쓴다 — 두 경로가
쓰는 함수가 아예 갈라져 있다.

2026-07-29(§4-11): 2시트 xlsx의 ②`financial_history` 시트도 **long 포맷**으로
바꿨다(사용자 확정). 기존 "회사×회계연도 wide"(재무 19항목이 각각 컬럼 + 감사인 +
파싱상태)에서 **"회사×회계연도×계정과목"** 7컬럼(결과ID/회사명/회계연도/접수번호/
재무제표명/계정과목/금액)으로 교체했고, 감사인·파싱상태 컬럼은 제거했다. 신설
"재무제표명"은 그 계정과목이 속한 표(재무상태표/손익계산서/현금흐름표)를 담는다 —
분류·라벨·행 순서는 `FINANCIAL_SNAPSHOT_ACCOUNTS_BY_STATEMENT` 한 곳에서 정의한다.
시트 ①(기본정보)의 포맷/컬럼과 `export_results_with_history()`의 시그니처는 무변경.

포맷을 가르는 기준은 **`ids` 유무 하나뿐이다**(dart-qa 2026-07-28 리뷰 반영) —
`include_history`는 어느 포맷을 쓸지에 관여하지 않는다. `ids` 없이
`include_history=true`만 온 요청(필터 전체 + 재무이력)의 기본정보 시트는 wide다.
그래서 `export_results_with_history()`가 `use_selection_format` 인자를 받아
호출부(`export_job_results()`)가 넘긴 `selected_ids is not None`을 그대로 따른다.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from typing import Literal

import pandas as pd

from app.models.financial_snapshot import FinancialSnapshot
from app.models.result import Result

# DB 필드명 -> 한국어 컬럼 헤더 (PRD 3-1/3-2 항목 기준). 파일 출력 시에만 사용.
RESULT_COLUMN_LABELS: dict[str, str] = {
    "id": "결과ID",
    "job_id": "Job ID",
    "corp_code": "고유번호",
    "rcept_no": "접수번호",
    # 기본정보 (PRD 3-1)
    "corp_name": "회사명",
    "address": "주소",
    # M8 3단계(§4-10)부터 주소/대표자/업종은 DART 기업개황 원본이라 "미확정" 단서가
    # 필요 없다. 전화번호만은 기업개황 엑셀에 열 자체가 없어 항상 비어 있다.
    "phone": "전화번호(미수집)",
    "ceo_name": "대표자명",
    "induty_code": "업종코드",
    "induty_name": "업종명",
    "fiscal_date": "결산기준일",
    "audit_opinion": "감사의견",
    "auditor_name": "감사인",
    "auditor_address": "감사인주소",
    # 연도별 감사인 변동 여부 (2026-07-26) — 1/0/빈칸(판정 불가). 다른
    # excluded_by_* 플래그와 같이 원값(1/0)을 그대로 내보낸다.
    "auditor_changed": "감사인변동여부",
    # 요약 재무 (PRD 3-2) — 당기(_cur)/전기(_prv)
    "current_assets_cur": "유동자산(당기)",
    "current_assets_prv": "유동자산(전기)",
    "noncurrent_assets_cur": "비유동자산(당기)",
    "noncurrent_assets_prv": "비유동자산(전기)",
    "total_assets_cur": "자산총계(당기)",
    "total_assets_prv": "자산총계(전기)",
    "current_liab_cur": "유동부채(당기)",
    "current_liab_prv": "유동부채(전기)",
    "noncurrent_liab_cur": "비유동부채(당기)",
    "noncurrent_liab_prv": "비유동부채(전기)",
    "total_liab_cur": "부채총계(당기)",
    "total_liab_prv": "부채총계(전기)",
    "total_equity_cur": "자본총계(당기)",
    "total_equity_prv": "자본총계(전기)",
    # 재무상태표 세부계정 2항목 (2026-08-05, best-effort). **둘 다 contra 행을 차감한
    # 순액**이라 라벨에 그 사실을 박아 총액과 혼동되지 않게 한다 — 매출채권은
    # 대손충당금, 현금및현금성자산은 정부보조금·국고보조금 등을 뺀 값이다.
    "cash_and_equivalents_cur": "현금및현금성자산(순액)(당기)",
    "cash_and_equivalents_prv": "현금및현금성자산(순액)(전기)",
    "trade_receivables_cur": "매출채권(순액)(당기)",
    "trade_receivables_prv": "매출채권(순액)(전기)",
    "revenue_cur": "매출액(당기)",
    "revenue_prv": "매출액(전기)",
    "cogs_cur": "매출원가(당기)",
    "cogs_prv": "매출원가(전기)",
    "gross_profit_cur": "매출총이익(당기)",
    "gross_profit_prv": "매출총이익(전기)",
    "sga_cur": "판매비와관리비(당기)",
    "sga_prv": "판매비와관리비(전기)",
    "operating_income_cur": "영업이익(당기)",
    "operating_income_prv": "영업이익(전기)",
    "net_income_cur": "당기순이익(당기)",
    "net_income_prv": "당기순이익(전기)",
    # 손익계산서 세부계정 1항목 (2026-08-05, best-effort). 손익계산서의 발생주의
    # 이자비용이며 현금흐름표의 간접법 가산 조정액과 섞지 않는다(실측 92.3% 불일치).
    # 이 wide 포맷은 재무제표별로 묶지 않고 도입 시점 순으로 이어 붙이는 기존 관행이라
    # `FINANCIAL_SNAPSHOT_ACCOUNTS_BY_STATEMENT`(재무제표별 그룹, 손익계산서 안에서
    # 영업외수익/비용 뒤에 옴)와 이 항목의 상대 순서가 다르다 — 라벨·소속은 두 시트가
    # 일치하고(드리프트 가드 테스트가 잠금), 순서만 다른 것은 의도된 차이다.
    "interest_expense_cur": "이자비용(당기)",
    "interest_expense_prv": "이자비용(전기)",
    # 현금흐름표 4항목 (§4-8, 2026-07-19)
    "cf_operating_cur": "영업활동현금흐름(당기)",
    "cf_operating_prv": "영업활동현금흐름(전기)",
    "cf_investing_cur": "투자활동현금흐름(당기)",
    "cf_investing_prv": "투자활동현금흐름(전기)",
    "cf_financing_cur": "재무활동현금흐름(당기)",
    "cf_financing_prv": "재무활동현금흐름(전기)",
    "cf_ending_cash_cur": "기말의현금(당기)",
    "cf_ending_cash_prv": "기말의현금(전기)",
    # 현금흐름표 세부계정 2항목 (2026-08-05, best-effort). 감가상각비는 **현금흐름표
    # 기준 총액**(제조원가 몫 포함)이라 손익계산서 판관비의 감가상각비와 다른 숫자다
    # (실측 75.4% 불일치, 제조업은 최대 18배) — 라벨에 출처를 박아 혼동을 막는다.
    "depreciation_cur": "감가상각비(현금흐름표)(당기)",
    "depreciation_prv": "감가상각비(현금흐름표)(전기)",
    "amortization_cur": "무형자산상각비(당기)",
    "amortization_prv": "무형자산상각비(전기)",
    # 영업외수익/영업외비용 2항목 (2026-07-22)
    "non_operating_income_cur": "영업외수익(당기)",
    "non_operating_income_prv": "영업외수익(전기)",
    "non_operating_expense_cur": "영업외비용(당기)",
    "non_operating_expense_prv": "영업외비용(전기)",
    # 금융위 요약재무 참고값 (§4-10-C/D) — 필터 판정에 쓰이지 않는 참고 표시용이고
    # 기준연도가 회사마다 다르므로 라벨에 "참고"와 연도 컬럼을 함께 둔다.
    "ref_revenue": "매출액(참고)",
    "ref_total_assets": "자산총계(참고)",
    "ref_fin_year": "참고값기준연도",
    # 상태
    "parse_status": "파싱상태",
    "parse_note": "파싱비고",
    "excluded_by_revenue": "매출액조건제외여부",
    "excluded_by_assets": "총자산조건제외여부",  # §4-7-2, 2026-07-15 추가
    # "최근 1년 이내 DART 공시 없음" 배제 (2026-07-21 추가)
    "latest_disclosure_date": "최근공시일자",
    "excluded_by_stale_disclosure": "공시없음(1년초과)제외여부",
}

# DataFrame 컬럼 순서(=RESULT_COLUMN_LABELS 순서, Result 모델 필드 순서와 동일).
RESULT_COLUMNS: list[str] = list(RESULT_COLUMN_LABELS.keys())


def results_to_dataframe(results: Sequence[Result]) -> pd.DataFrame:
    """`results` 레코드 목록을 DB 필드명을 컬럼으로 갖는 DataFrame으로 변환.

    컬럼명은 영문 DB 필드명 그대로 유지한다 — 한국어 헤더 매핑은
    `export_results()`에서 파일 출력 직전에만 적용한다.
    """
    rows = [{col: getattr(r, col, None) for col in RESULT_COLUMNS} for r in results]
    return pd.DataFrame(rows, columns=RESULT_COLUMNS)


def export_results(results: Sequence[Result], fmt: Literal["xlsx", "csv"]) -> bytes:
    """`results` 레코드 목록을 xlsx 또는 csv 바이트로 직렬화.

    한국어 헤더는 여기서만 적용한다(DB 필드명 자체는 바꾸지 않음). csv는
    엑셀에서 한글이 깨지지 않도록 UTF-8 BOM(`utf-8-sig`)을 포함한다.
    """
    if fmt not in ("xlsx", "csv"):
        raise ValueError(f"지원하지 않는 형식입니다: {fmt}")

    df = results_to_dataframe(results).rename(columns=RESULT_COLUMN_LABELS)

    buffer = io.BytesIO()
    if fmt == "xlsx":
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="results", index=False)
    else:
        buffer.write(df.to_csv(index=False).encode("utf-8-sig"))

    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 다중 선택 다운로드(§4-11, M9) — 기본정보 long 포맷 (2026-07-28)
# ---------------------------------------------------------------------------
#
# 선택 다운로드(`ids` 지정)에서만 쓰는 컬럼 구성이다. 기본정보 컬럼은
# `RESULT_COLUMN_LABELS`와 **완전히 같은 필드·라벨**을 재사용하고, 그 뒤에
# 재무 항목을 "계정과목명/금액" 2컬럼으로 세로로 푼다(회사 1건 = 계정과목 수만큼의
# 행). 전기(`_prv`) 값은 싣지 않는다 — 사용자 확정 사항("전기 항목은 전년도 당기
# 항목이니깐"). 전기 포함 다년치가 필요하면 `include_history=true`의
# `financial_history` 시트를 쓴다.
#
# 필터 전체 내보내기(`ids` 없는 요청)는 이 포맷을 쓰지 않는다 — `include_history`
# 유무와 무관하게 기존 wide 포맷(`RESULT_COLUMN_LABELS`/`export_results()`)이다.

# long 포맷에서 각 행마다 반복되는 기본정보 필드. 앞 15개는 `RESULT_COLUMN_LABELS`의
# 앞 15개(필드·라벨·순서 동일)이고, `parse_status`는 사용자 확정(2026-07-28)에 따라
# **계정과목명/금액보다도 뒤**, 즉 최종 컬럼 순서의 맨 마지막에 놓는다 — 그 배치는
# 아래 `SELECTION_TRAILING_COLUMNS`가 담당한다(여기서는 "행마다 반복되는 필드"라는
# 의미만 갖는다).
SELECTION_BASE_COLUMNS: list[str] = [
    "id",
    "job_id",
    "corp_code",
    "rcept_no",
    "corp_name",
    "address",
    "phone",
    "ceo_name",
    "induty_code",
    "induty_name",
    "fiscal_date",
    "audit_opinion",
    "auditor_name",
    "auditor_address",
    "auditor_changed",
    "parse_status",
]

# 위 기본정보 필드 중 "계정과목명/금액" **뒤**에 배치할 것(2026-07-28, 사용자 확정).
SELECTION_TRAILING_COLUMNS: list[str] = ["parse_status"]

# 세로로 풀 당기(`_cur`) 재무 항목 24개(2026-08-05 세부계정 5항목 추가로 19 → 24).
# 순서가 곧 한 회사 안의 행 순서다(재무상태표 → 손익계산서 → 현금흐름표 →
# 영업외손익, `RESULT_COLUMN_LABELS`와 동일한 배열 순서). 세부계정은 각자의 원천
# 재무제표 블록 끝에 붙였다 — 값이 없어도 행은 남으므로(아래 함수 참고) 기존
# 항목의 상대 순서는 그대로다.
SELECTION_ACCOUNT_COLUMNS: list[str] = [
    "current_assets_cur",
    "noncurrent_assets_cur",
    "total_assets_cur",
    "current_liab_cur",
    "noncurrent_liab_cur",
    "total_liab_cur",
    "total_equity_cur",
    "cash_and_equivalents_cur",
    "trade_receivables_cur",
    "revenue_cur",
    "cogs_cur",
    "gross_profit_cur",
    "sga_cur",
    "operating_income_cur",
    "net_income_cur",
    "interest_expense_cur",
    "cf_operating_cur",
    "cf_investing_cur",
    "cf_financing_cur",
    "cf_ending_cash_cur",
    "depreciation_cur",
    "amortization_cur",
    "non_operating_income_cur",
    "non_operating_expense_cur",
]

# 계정과목명은 새로 짓지 않고 기존 wide 포맷 라벨에서 "(당기)" 접미어만 뗀다
# (예: "매출액(당기)" → "매출액"). 라벨이 두 곳에서 어긋날 여지를 없애기 위함.
SELECTION_ACCOUNT_LABELS: dict[str, str] = {
    col: RESULT_COLUMN_LABELS[col].removesuffix("(당기)") for col in SELECTION_ACCOUNT_COLUMNS
}

# DB 필드명(가상 필드 `account_name`/`amount` 포함) -> 한국어 헤더. dict 순서가 곧
# 파일의 컬럼 순서다 — `SELECTION_TRAILING_COLUMNS`(파싱상태)만 계정과목명/금액 뒤로
# 밀려난다.
SELECTION_EXPORT_COLUMN_LABELS: dict[str, str] = {
    **{
        col: RESULT_COLUMN_LABELS[col]
        for col in SELECTION_BASE_COLUMNS
        if col not in SELECTION_TRAILING_COLUMNS
    },
    "account_name": "계정과목명",
    "amount": "금액",
    **{col: RESULT_COLUMN_LABELS[col] for col in SELECTION_TRAILING_COLUMNS},
}

SELECTION_EXPORT_COLUMNS: list[str] = list(SELECTION_EXPORT_COLUMN_LABELS.keys())


def results_to_selection_dataframe(results: Sequence[Result]) -> pd.DataFrame:
    """`results` 레코드를 "회사 × 계정과목" long 포맷 DataFrame으로 변환.

    회사 1건이 `SELECTION_ACCOUNT_COLUMNS` 개수(24)만큼의 행이 되고, 기본정보
    컬럼(`SELECTION_BASE_COLUMNS`)은 그 행들에 그대로 반복된다. 입력 순서
    (호출부의 id 오름차순)를 보존한다. 컬럼 순서는 `SELECTION_EXPORT_COLUMNS`가
    정한다 — `parse_status`는 계정과목명/금액 뒤(맨 마지막)로 간다.

    값이 없는(`None`) 계정과목도 **행은 그대로 만들고 금액만 비운다** — 어떤
    계정과목이 결측인지 파일에서 알 수 있어야 하므로 스킵하지 않는다. 이를 위해
    "금액" 컬럼은 object dtype으로 고정한다(그대로 두면 pandas가 float64로 올려
    `10000000000.0`처럼 소수점이 붙고 결측이 `NaN`으로 출력된다).
    """
    rows: list[dict[str, object | None]] = []
    amounts: list[object | None] = []
    for result in results:
        base = {col: getattr(result, col, None) for col in SELECTION_BASE_COLUMNS}
        for col in SELECTION_ACCOUNT_COLUMNS:
            value = getattr(result, col, None)
            rows.append({**base, "account_name": SELECTION_ACCOUNT_LABELS[col], "amount": value})
            amounts.append(value)

    df = pd.DataFrame(rows, columns=SELECTION_EXPORT_COLUMNS)
    df["amount"] = pd.Series(amounts, dtype=object, index=df.index)
    return df


def export_selection_results(
    results: Sequence[Result], fmt: Literal["xlsx", "csv"]
) -> bytes:
    """선택 다운로드용 기본정보 파일(long 포맷)을 xlsx/csv 바이트로 직렬화.

    `export_results()`와 형식(시트명 `results`, csv의 UTF-8 BOM)은 같고 컬럼
    구성만 다르다 — 전체 내보내기는 계속 `export_results()`를 쓴다.
    """
    if fmt not in ("xlsx", "csv"):
        raise ValueError(f"지원하지 않는 형식입니다: {fmt}")

    df = results_to_selection_dataframe(results).rename(columns=SELECTION_EXPORT_COLUMN_LABELS)

    buffer = io.BytesIO()
    if fmt == "xlsx":
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="results", index=False)
    else:
        buffer.write(df.to_csv(index=False).encode("utf-8-sig"))

    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 다중 선택 다운로드(§4-11, M9) — results + financial_history 2시트 xlsx
# ---------------------------------------------------------------------------

# `financial_snapshots`(회사×회계연도) 시트는 2026-07-29부터 기본정보 시트와 같은
# **long 포맷**이다 — 스냅샷 1건(회사×회계연도)이 재무 항목 수(2026-08-05 세부계정
# 5항목 추가로 19 → 24)만큼의 행으로 풀리고,
# 재무 항목마다 그 항목이 속한 재무제표 이름("재무상태표"/"손익계산서"/"현금흐름표")을
# 함께 싣는다(사용자 확정). 감사인/파싱상태 컬럼은 이때 제거됐다.
#
# 계정과목 라벨·순서·재무제표 소속을 **한 곳에서** 정의해 셋이 어긋날 여지를 없앤다.
# dict 순서가 곧 한 스냅샷 안의 행 순서다(재무상태표 → 손익계산서 → 현금흐름표,
# 각 표 안에서는 아래 나열 순서 그대로). 영업외수익/영업외비용은 손익계산서 소속으로
# 분류한다.
FINANCIAL_SNAPSHOT_ACCOUNTS_BY_STATEMENT: dict[str, dict[str, str]] = {
    "재무상태표": {
        "current_assets": "유동자산",
        "noncurrent_assets": "비유동자산",
        "total_assets": "자산총계",
        "current_liab": "유동부채",
        "noncurrent_liab": "비유동부채",
        "total_liab": "부채총계",
        "total_equity": "자본총계",
        # 세부계정 2항목 (2026-08-05). 라벨은 시트 ①(`SELECTION_ACCOUNT_LABELS`)과
        # 글자까지 동일해야 한다 — 드리프트 가드 테스트가 이를 잠근다. 둘 다 contra
        # 행 차감 후 순액이라 "(순액)"을 붙인다(위 `RESULT_COLUMN_LABELS` 주석 참고).
        "cash_and_equivalents": "현금및현금성자산(순액)",
        "trade_receivables": "매출채권(순액)",
    },
    "손익계산서": {
        "revenue": "매출액",
        "cogs": "매출원가",
        "gross_profit": "매출총이익",
        "sga": "판매비와관리비",
        "operating_income": "영업이익",
        "net_income": "당기순이익",
        "non_operating_income": "영업외수익",
        "non_operating_expense": "영업외비용",
        "interest_expense": "이자비용",  # 세부계정 (2026-08-05)
    },
    "현금흐름표": {
        "cf_operating": "영업활동현금흐름",
        "cf_investing": "투자활동현금흐름",
        "cf_financing": "재무활동현금흐름",
        "cf_ending_cash": "기말의현금",
        # 세부계정 2항목 (2026-08-05). 감가상각비는 손익계산서 판관비 몫이 아니라
        # 현금흐름표 기준 총액이라 라벨에 출처를 남긴다(base.py 주석 참고).
        "depreciation": "감가상각비(현금흐름표)",
        "amortization": "무형자산상각비",
    },
}

# 세로로 풀 재무 항목 24개(위 정의 순서 = 행 순서)와 그 라벨/재무제표 소속.
FINANCIAL_SNAPSHOT_ACCOUNT_COLUMNS: list[str] = [
    col for accounts in FINANCIAL_SNAPSHOT_ACCOUNTS_BY_STATEMENT.values() for col in accounts
]
FINANCIAL_SNAPSHOT_ACCOUNT_LABELS: dict[str, str] = {
    col: label
    for accounts in FINANCIAL_SNAPSHOT_ACCOUNTS_BY_STATEMENT.values()
    for col, label in accounts.items()
}
FINANCIAL_SNAPSHOT_STATEMENT_BY_ACCOUNT: dict[str, str] = {
    col: statement
    for statement, accounts in FINANCIAL_SNAPSHOT_ACCOUNTS_BY_STATEMENT.items()
    for col in accounts
}

# DB 필드명(가상 필드 `statement_name`/`account_name`/`amount` 포함) -> 한국어 헤더.
# dict 순서가 곧 시트의 컬럼 순서다(2026-07-29 사용자 확정: 결과ID/회사명/회계연도/
# 접수번호/재무제표명/계정과목/금액 7컬럼).
#
# `corp_name`은 `financial_snapshots`에 없는 컬럼이다 — 스냅샷은 `result_id` FK만
# 가지므로 `snapshots_to_dataframe()`이 호출부가 넘긴 `{result_id: corp_name}`
# 매핑으로 채운다. `result_id`를 함께 실어 두 시트를 결과ID로 대조할 수 있게 한다
# (회사 고유번호 등 나머지 기본정보는 `results` 시트에서 같은 결과ID로 찾는다).
FINANCIAL_SNAPSHOT_COLUMN_LABELS: dict[str, str] = {
    "result_id": "결과ID",
    "corp_name": "회사명",
    "fiscal_year": "회계연도",
    "rcept_no": "접수번호",
    "statement_name": "재무제표명",
    "account_name": "계정과목",
    "amount": "금액",
}

FINANCIAL_SNAPSHOT_COLUMNS: list[str] = list(FINANCIAL_SNAPSHOT_COLUMN_LABELS.keys())

# 각 행마다 반복되는 스냅샷 식별 컬럼(계정과목/금액을 뺀 나머지).
FINANCIAL_SNAPSHOT_BASE_COLUMNS: list[str] = ["result_id", "corp_name", "fiscal_year", "rcept_no"]


def snapshots_to_dataframe(
    snapshots: Sequence[FinancialSnapshot],
    corp_name_by_result_id: Mapping[int, str | None],
) -> pd.DataFrame:
    """`financial_snapshots`를 "회사 × 회계연도 × 계정과목" long 포맷으로 변환.

    스냅샷 1건이 `FINANCIAL_SNAPSHOT_ACCOUNT_COLUMNS` 개수(24)만큼의 행이 되고,
    식별 컬럼(결과ID/회사명/회계연도/접수번호)은 그 행들에 그대로 반복된다.

    스냅샷은 회사명을 직접 갖고 있지 않으므로(`result_id` FK만), 호출부가
    `results` 조회 결과로 만든 `{result_id: corp_name}` 매핑을 함께 넘겨 조인한다.
    매핑에 없는 `result_id`는 회사명을 빈 값(None)으로 둔다 — 파일 생성을 실패시키지
    않는다.

    값이 없는(`None`) 계정과목도 **행은 그대로 만들고 금액만 비운다** — 기본정보
    시트(`results_to_selection_dataframe()`)와 동일한 방침이다(어떤 항목이 결측인지
    파일에서 알 수 있어야 하므로 스킵하지 않는다). "금액" 컬럼은 같은 이유로 object
    dtype으로 고정한다(그대로 두면 pandas가 float64로 올려 `5000000000.0`처럼
    소수점이 붙고 결측이 `NaN`으로 출력된다).

    행 순서는 **입력 순서를 그대로 보존**한다(호출부가 `ORDER BY result_id,
    fiscal_year`로 정렬해 넘긴다 — 회사별로 묶인 뒤 연도 오름차순).
    """
    rows: list[dict[str, object | None]] = []
    amounts: list[object | None] = []
    for snapshot in snapshots:
        base = {col: getattr(snapshot, col, None) for col in FINANCIAL_SNAPSHOT_BASE_COLUMNS}
        base["corp_name"] = corp_name_by_result_id.get(snapshot.result_id)
        for col in FINANCIAL_SNAPSHOT_ACCOUNT_COLUMNS:
            value = getattr(snapshot, col, None)
            rows.append(
                {
                    **base,
                    "statement_name": FINANCIAL_SNAPSHOT_STATEMENT_BY_ACCOUNT[col],
                    "account_name": FINANCIAL_SNAPSHOT_ACCOUNT_LABELS[col],
                    "amount": value,
                }
            )
            amounts.append(value)

    df = pd.DataFrame(rows, columns=FINANCIAL_SNAPSHOT_COLUMNS)
    df["amount"] = pd.Series(amounts, dtype=object, index=df.index)
    return df


def export_results_with_history(
    results: Sequence[Result],
    snapshots: Sequence[FinancialSnapshot],
    corp_name_by_result_id: Mapping[int, str | None],
    use_selection_format: bool = False,
) -> bytes:
    """기본정보(`results`) + 재무이력(`financial_history`) 2시트 xlsx 바이트 생성.

    시트 ②`financial_history`는 회사×회계연도×계정과목 그레인이라 시트 ①과 행 수가
    다르다 — 그래서 한 시트에 합치지 않고 시트를 나눈다. csv는 다중 시트를 표현할 수
    없으므로 이 함수에 대응하는 csv 형식은 없다(API가 `format=csv`+
    `include_history=true` 조합을 400으로 거부한다).

    시트 ①`results`의 컬럼 구성은 `use_selection_format`이 정한다 — True면
    `export_selection_results(..., "xlsx")`와 **완전히 같은 long 포맷**(회사 ×
    계정과목), False면 `export_results(..., "xlsx")`와 같은 기존 wide 포맷이다.
    호출부(`export_job_results()`)가 `selected_ids is not None`을 그대로 넘기므로
    **포맷은 오직 `ids` 유무로만 갈린다** — `include_history`만 붙인 필터 전체
    내보내기의 기본정보 시트는 wide 그대로다(dart-qa 2026-07-28 리뷰 반영).
    시트 ②는 어느 쪽이든 동일하다.
    """
    results_df = (
        results_to_selection_dataframe(results).rename(columns=SELECTION_EXPORT_COLUMN_LABELS)
        if use_selection_format
        else results_to_dataframe(results).rename(columns=RESULT_COLUMN_LABELS)
    )
    history_df = snapshots_to_dataframe(snapshots, corp_name_by_result_id).rename(
        columns=FINANCIAL_SNAPSHOT_COLUMN_LABELS
    )

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        results_df.to_excel(writer, sheet_name="results", index=False)
        history_df.to_excel(writer, sheet_name="financial_history", index=False)
    return buffer.getvalue()
