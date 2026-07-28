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
    # 현금흐름표 4항목 (§4-8, 2026-07-19)
    "cf_operating_cur": "영업활동현금흐름(당기)",
    "cf_operating_prv": "영업활동현금흐름(전기)",
    "cf_investing_cur": "투자활동현금흐름(당기)",
    "cf_investing_prv": "투자활동현금흐름(전기)",
    "cf_financing_cur": "재무활동현금흐름(당기)",
    "cf_financing_prv": "재무활동현금흐름(전기)",
    "cf_ending_cash_cur": "기말의현금(당기)",
    "cf_ending_cash_prv": "기말의현금(전기)",
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
# 다중 선택 다운로드(§4-11, M9) — results + financial_history 2시트 xlsx
# ---------------------------------------------------------------------------

# `financial_snapshots`(회사×회계연도) 전용 한국어 헤더. `RESULT_COLUMN_LABELS`와
# 같은 명명 규칙을 따르되 이 테이블은 필드에 `_cur`/`_prv` 접미어가 없으므로
# "(당기)/(전기)" 표기를 빼고 그대로 쓴다(그래서 두 딕셔너리를 합칠 수 없다).
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
    # 표준 재무 13항목
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
    "sga": "판매비와관리비",
    "operating_income": "영업이익",
    "net_income": "당기순이익",
    # 현금흐름표 4항목
    "cf_operating": "영업활동현금흐름",
    "cf_investing": "투자활동현금흐름",
    "cf_financing": "재무활동현금흐름",
    "cf_ending_cash": "기말의현금",
    # 영업외수익/영업외비용 2항목
    "non_operating_income": "영업외수익",
    "non_operating_expense": "영업외비용",
    # 그 연도를 당기로 감사한 감사인 (2026-07-26) — 전기 열 유래 행은 항상 빈 값이다.
    "auditor_name": "감사인",
    "parse_status": "파싱상태",
}

FINANCIAL_SNAPSHOT_COLUMNS: list[str] = list(FINANCIAL_SNAPSHOT_COLUMN_LABELS.keys())


def snapshots_to_dataframe(
    snapshots: Sequence[FinancialSnapshot],
    corp_name_by_result_id: Mapping[int, str | None],
) -> pd.DataFrame:
    """`financial_snapshots` 레코드 목록을 DB 필드명 컬럼의 DataFrame으로 변환.

    스냅샷은 회사명을 직접 갖고 있지 않으므로(`result_id` FK만), 호출부가
    `results` 조회 결과로 만든 `{result_id: corp_name}` 매핑을 함께 넘겨 조인한다.
    매핑에 없는 `result_id`는 회사명을 빈 값(None)으로 둔다 — 파일 생성을 실패시키지
    않는다.

    행 순서는 **입력 순서를 그대로 보존**한다(호출부가 `ORDER BY result_id,
    fiscal_year`로 정렬해 넘긴다 — 회사별로 묶인 뒤 연도 오름차순).
    """
    rows = []
    for snapshot in snapshots:
        row = {col: getattr(snapshot, col, None) for col in FINANCIAL_SNAPSHOT_COLUMNS}
        row["corp_name"] = corp_name_by_result_id.get(snapshot.result_id)
        rows.append(row)
    return pd.DataFrame(rows, columns=FINANCIAL_SNAPSHOT_COLUMNS)


def export_results_with_history(
    results: Sequence[Result],
    snapshots: Sequence[FinancialSnapshot],
    corp_name_by_result_id: Mapping[int, str | None],
) -> bytes:
    """기본정보(`results`) + 재무이력(`financial_history`) 2시트 xlsx 바이트 생성.

    시트 ①`results`는 `export_results(..., "xlsx")`와 **완전히 같은 컬럼**이고
    (선택된 회사만 담긴다는 점만 다름), 시트 ②`financial_history`는 회사×회계연도
    그레인이라 행 수가 다르다 — 그래서 한 시트에 합치지 않고 시트를 나눈다.
    csv는 다중 시트를 표현할 수 없으므로 이 함수에 대응하는 csv 형식은 없다
    (API가 `format=csv`+`include_history=true` 조합을 400으로 거부한다).
    """
    results_df = results_to_dataframe(results).rename(columns=RESULT_COLUMN_LABELS)
    history_df = snapshots_to_dataframe(snapshots, corp_name_by_result_id).rename(
        columns=FINANCIAL_SNAPSHOT_COLUMN_LABELS
    )

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        results_df.to_excel(writer, sheet_name="results", index=False)
        history_df.to_excel(writer, sheet_name="financial_history", index=False)
    return buffer.getvalue()
