"""app/exporters/excel.py 단위 테스트 — DB/HTTP 없이 export_results() 자체를 검증."""

from __future__ import annotations

import io

import openpyxl
import pytest

from app.exporters.excel import (
    FINANCIAL_SNAPSHOT_COLUMN_LABELS,
    RESULT_COLUMN_LABELS,
    export_results,
    export_results_with_history,
    results_to_dataframe,
    snapshots_to_dataframe,
)
from app.models.financial_snapshot import FinancialSnapshot
from app.models.result import ParseStatus, Result


def _sample_result() -> Result:
    return Result(
        id=1,
        job_id=1,
        corp_code="00100001",
        rcept_no="20260601000001",
        corp_name="㈜테스트",
        address="경상남도 김해시 삼계로 1",
        phone="055-000-0000",
        ceo_name="홍길동",
        induty_code="25",
        induty_name="금속가공제품 제조업",
        fiscal_date="20251231",
        audit_opinion="적정",
        revenue_cur=10_000_000_000,
        revenue_prv=9_000_000_000,
        parse_status=ParseStatus.OK,
        parse_note=None,
        excluded_by_revenue=0,
    )


def test_results_to_dataframe_keeps_db_field_names():
    df = results_to_dataframe([_sample_result()])
    assert list(df.columns) == list(RESULT_COLUMN_LABELS.keys())
    assert df.loc[0, "corp_name"] == "㈜테스트"
    assert df.loc[0, "revenue_cur"] == 10_000_000_000


def test_export_results_xlsx_uses_korean_headers():
    content = export_results([_sample_result()], "xlsx")
    wb = openpyxl.load_workbook(io.BytesIO(content))
    ws = wb["results"]
    header_row = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    assert header_row == list(RESULT_COLUMN_LABELS.values())


def test_export_results_csv_has_bom_and_korean_headers():
    content = export_results([_sample_result()], "csv")
    assert content.startswith(b"\xef\xbb\xbf")
    text = content.decode("utf-8-sig")
    first_line = text.splitlines()[0]
    assert "회사명" in first_line
    assert "corp_name" not in first_line


def test_export_results_empty_list_still_returns_header_only_file():
    content = export_results([], "xlsx")
    wb = openpyxl.load_workbook(io.BytesIO(content))
    ws = wb["results"]
    assert ws.max_row == 1  # 헤더만


def test_export_results_invalid_format_raises():
    with pytest.raises(ValueError):
        export_results([_sample_result()], "pdf")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 다중 선택 다운로드(§4-11, M9) — snapshots_to_dataframe / export_results_with_history
# ---------------------------------------------------------------------------


def _sample_snapshots() -> list[FinancialSnapshot]:
    return [
        FinancialSnapshot(
            id=11,
            result_id=1,
            rcept_no="20250401000001",
            fiscal_year="2024",
            total_assets=5_000_000_000,
            revenue=8_000_000_000,
            cf_operating=1_000_000,
            non_operating_income=2_000_000,
            auditor_name="안경회계법인",
            parse_status=ParseStatus.OK,
            from_current_period=1,
        ),
        FinancialSnapshot(
            id=12,
            result_id=1,
            rcept_no="20260401000001",
            fiscal_year="2025",
            total_assets=6_000_000_000,
            revenue=10_000_000_000,
            auditor_name="다른회계법인",
            parse_status=ParseStatus.OK,
            from_current_period=1,
        ),
    ]


def test_snapshots_to_dataframe_joins_corp_name_by_result_id():
    df = snapshots_to_dataframe(_sample_snapshots(), {1: "㈜테스트"})
    assert list(df.columns) == list(FINANCIAL_SNAPSHOT_COLUMN_LABELS.keys())
    assert list(df["corp_name"]) == ["㈜테스트", "㈜테스트"]
    assert list(df["fiscal_year"]) == ["2024", "2025"]
    assert df.loc[0, "total_assets"] == 5_000_000_000
    assert df.loc[0, "auditor_name"] == "안경회계법인"


def test_snapshots_to_dataframe_unknown_result_id_leaves_corp_name_blank():
    """매핑에 없는 result_id는 회사명만 비고 파일 생성 자체는 실패하지 않는다."""
    df = snapshots_to_dataframe(_sample_snapshots(), {})
    assert df["corp_name"].isna().all()
    assert len(df) == 2


def test_export_results_with_history_writes_two_sheets():
    content = export_results_with_history(
        [_sample_result()], _sample_snapshots(), {1: "㈜테스트"}
    )
    wb = openpyxl.load_workbook(io.BytesIO(content))
    assert wb.sheetnames == ["results", "financial_history"]

    results_header = [c.value for c in next(wb["results"].iter_rows(min_row=1, max_row=1))]
    assert results_header == list(RESULT_COLUMN_LABELS.values())

    history_ws = wb["financial_history"]
    history_header = [c.value for c in next(history_ws.iter_rows(min_row=1, max_row=1))]
    assert history_header == list(FINANCIAL_SNAPSHOT_COLUMN_LABELS.values())
    assert history_ws.max_row == 3  # 헤더 + 스냅샷 2행
    assert history_ws.cell(row=2, column=history_header.index("회사명") + 1).value == "㈜테스트"
    year_cell = history_ws.cell(row=2, column=history_header.index("회계연도") + 1).value
    assert str(year_cell) == "2024"


def test_export_results_with_history_empty_inputs_return_header_only_sheets():
    content = export_results_with_history([], [], {})
    wb = openpyxl.load_workbook(io.BytesIO(content))
    assert wb.sheetnames == ["results", "financial_history"]
    assert wb["results"].max_row == 1
    assert wb["financial_history"].max_row == 1
