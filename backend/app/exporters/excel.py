"""CSV/Excel 결과 산출.

상세개발계획.md §6 `GET /api/jobs/{id}/export?format=xlsx|csv`. `results`
테이블 레코드를 pandas DataFrame으로 변환해 xlsx(openpyxl 엔진)/csv 바이트로
직렬화한다.

DB 컬럼명(영문)은 `app/models/result.py`(§5 스키마)를 그대로 유지한다
(CLAUDE.md 관례 — 컬럼명을 임의로 바꾸지 않는다, Phase 2 재수집 방지). 한국어
헤더는 파일 출력 시에만 `RESULT_COLUMN_LABELS`로 매핑해 적용하고, DB/API
응답의 필드명 자체는 건드리지 않는다.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from typing import Literal

import pandas as pd

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
    "phone": "전화번호(미확정,FSC기준)",
    "ceo_name": "대표자명(미확정,FSC기준)",
    "induty_code": "업종코드",
    "induty_name": "업종명(미확정,FSC기준)",
    "fiscal_date": "결산기준일",
    "audit_opinion": "감사의견",
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
    "gross_margin_cur": "매출총이익율(당기,%)",
    "gross_margin_prv": "매출총이익율(전기,%)",
    "sga_cur": "판매비와관리비(당기)",
    "sga_prv": "판매비와관리비(전기)",
    "operating_income_cur": "영업이익(당기)",
    "operating_income_prv": "영업이익(전기)",
    "net_income_cur": "당기순이익(당기)",
    "net_income_prv": "당기순이익(전기)",
    # 상태
    "parse_status": "파싱상태",
    "parse_note": "파싱비고",
    "excluded_by_revenue": "매출액조건제외여부",
    "excluded_by_assets": "총자산조건제외여부",  # §4-7-2, 2026-07-15 추가
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
