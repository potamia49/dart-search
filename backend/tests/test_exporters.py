"""app/exporters/excel.py 단위 테스트 — DB/HTTP 없이 export_results() 자체를 검증."""

from __future__ import annotations

import io

import openpyxl
import pytest

from app.exporters.excel import RESULT_COLUMN_LABELS, export_results, results_to_dataframe
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
