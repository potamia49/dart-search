"""app/exporters/excel.py 단위 테스트 — DB/HTTP 없이 export_results() 자체를 검증."""

from __future__ import annotations

import io

import openpyxl
import pytest

from app.exporters.excel import (
    FINANCIAL_SNAPSHOT_ACCOUNTS_BY_STATEMENT,
    FINANCIAL_SNAPSHOT_ACCOUNT_COLUMNS,
    FINANCIAL_SNAPSHOT_ACCOUNT_LABELS,
    FINANCIAL_SNAPSHOT_COLUMN_LABELS,
    FINANCIAL_SNAPSHOT_STATEMENT_BY_ACCOUNT,
    RESULT_COLUMN_LABELS,
    SELECTION_ACCOUNT_COLUMNS,
    SELECTION_ACCOUNT_LABELS,
    SELECTION_EXPORT_COLUMN_LABELS,
    export_results,
    export_results_with_history,
    export_selection_results,
    results_to_dataframe,
    results_to_selection_dataframe,
    snapshots_to_dataframe,
)
from app.models.financial_snapshot import FinancialSnapshot
from app.models.result import ParseStatus, Result
from app.parsers.base import DETAIL_FINANCIAL_FIELDS


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
# 선택 다운로드 long 포맷(§4-11, 2026-07-28) — results_to_selection_dataframe /
# export_selection_results
# ---------------------------------------------------------------------------


def test_selection_account_columns_cover_every_cur_field():
    """선택 다운로드 계정과목 목록이 wide 포맷의 `_cur` 필드 전체와 정확히 일치해야 한다.

    `SELECTION_ACCOUNT_COLUMNS`는 하드코딩 목록이라, 앞으로 누군가 새 `_cur`/`_prv`
    필드 쌍을 추가하면(CF 4항목·영업외손익 2항목이 그렇게 추가됐다) wide 전체
    내보내기에는 나오지만 선택 다운로드에서는 **에러 없이 조용히** 빠진다 — 그
    드리프트를 여기서 잡는다(dart-qa 2026-07-28 지적).
    """
    assert {c for c in RESULT_COLUMN_LABELS if c.endswith("_cur")} == set(
        SELECTION_ACCOUNT_COLUMNS
    )
    # 계정과목명은 wide 라벨에서 "(당기)"를 떼어 파생시킨다 — 접미어가 없는 라벨이
    # 섞이면 `removesuffix`가 조용히 통과해 "매출액(당기)" 같은 이름이 그대로 나간다.
    for col in SELECTION_ACCOUNT_COLUMNS:
        assert RESULT_COLUMN_LABELS[col].endswith("(당기)")
        assert "(당기)" not in SELECTION_ACCOUNT_LABELS[col]


def test_selection_dataframe_melts_one_company_into_account_rows():
    """회사 1건이 당기 계정과목 24행으로 풀리고 기본정보는 각 행에 반복된다."""
    df = results_to_selection_dataframe([_sample_result()])

    assert list(df.columns) == list(SELECTION_EXPORT_COLUMN_LABELS.keys())
    assert len(df) == len(SELECTION_ACCOUNT_COLUMNS) == 24  # 2026-08-05 세부계정 5항목 추가
    # 기본정보는 모든 행에 동일하게 반복
    assert set(df["corp_name"]) == {"㈜테스트"}
    assert set(df["corp_code"]) == {"00100001"}
    # 계정과목명은 RESULT_COLUMN_LABELS에서 "(당기)"만 뗀 이름
    assert df.loc[0, "account_name"] == "유동자산"
    assert list(df["account_name"])[:3] == ["유동자산", "비유동자산", "자산총계"]
    revenue_row = df[df["account_name"] == "매출액"].iloc[0]
    assert revenue_row["amount"] == 10_000_000_000


def test_selection_dataframe_keeps_rows_for_missing_amounts():
    """값이 None인 계정과목도 행은 남기고 금액만 비운다(어떤 항목이 결측인지 보여야 함)."""
    df = results_to_selection_dataframe([_sample_result()])

    assets_row = df[df["account_name"] == "자산총계"].iloc[0]
    assert assets_row["amount"] is None  # NaN(float)이 아니라 빈 값이어야 한다
    assert len(df[df["amount"].isna()]) == 23  # 매출액 1건만 값이 있다
    # 금액 컬럼이 float64로 승격되면 10000000000.0처럼 소수점이 붙는다.
    assert df["amount"].dtype == object


def test_selection_dataframe_excludes_previous_period_columns():
    """전기(_prv)는 싣지 않는다(사용자 확정 — 전기는 전년도 당기와 같다)."""
    df = results_to_selection_dataframe([_sample_result()])
    assert not any(c.endswith("_prv") for c in df.columns)
    assert not any("(전기)" in str(v) for v in df["account_name"])
    assert 9_000_000_000 not in list(df["amount"])  # revenue_prv


def test_selection_dataframe_multiple_companies_keep_input_order():
    first = _sample_result()
    second = _sample_result()
    second.id = 2
    second.corp_name = "㈜둘째"
    df = results_to_selection_dataframe([first, second])

    assert len(df) == 48
    assert list(df["corp_name"])[:24] == ["㈜테스트"] * 24
    assert list(df["corp_name"])[24:] == ["㈜둘째"] * 24


def test_export_selection_results_xlsx_uses_new_headers():
    content = export_selection_results([_sample_result()], "xlsx")
    wb = openpyxl.load_workbook(io.BytesIO(content))
    ws = wb["results"]
    header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]

    assert header == list(SELECTION_EXPORT_COLUMN_LABELS.values())
    assert header[:5] == ["결과ID", "Job ID", "고유번호", "접수번호", "회사명"]
    # 파싱상태는 계정과목명/금액보다도 뒤, 최종 컬럼 순서의 맨 마지막이다(2026-07-28).
    assert header[-3:] == ["계정과목명", "금액", "파싱상태"]
    assert ws.cell(row=2, column=header.index("파싱상태") + 1).value == "OK"
    assert ws.max_row == 25  # 헤더 + 계정과목 24행
    # 결측 금액은 빈 셀
    amount_col = header.index("금액") + 1
    account_col = header.index("계정과목명") + 1
    amounts = {
        ws.cell(row=r, column=account_col).value: ws.cell(row=r, column=amount_col).value
        for r in range(2, 26)
    }
    assert amounts["매출액"] == 10_000_000_000
    assert amounts["자산총계"] is None


def test_export_selection_results_csv_has_bom_and_blank_amount():
    content = export_selection_results([_sample_result()], "csv")
    assert content.startswith(b"\xef\xbb\xbf")
    lines = content.decode("utf-8-sig").splitlines()

    assert lines[0].endswith("계정과목명,금액,파싱상태")
    assert len(lines) == 25
    assert "10000000000.0" not in content.decode("utf-8-sig")
    assert any(line.endswith("매출액,10000000000,OK") for line in lines[1:])
    assert any(line.endswith("자산총계,,OK") for line in lines[1:])  # 결측은 빈 값


def test_export_selection_results_empty_list_returns_header_only():
    content = export_selection_results([], "xlsx")
    wb = openpyxl.load_workbook(io.BytesIO(content))
    assert wb["results"].max_row == 1


def test_export_selection_results_invalid_format_raises():
    with pytest.raises(ValueError):
        export_selection_results([_sample_result()], "pdf")  # type: ignore[arg-type]


def test_full_export_still_uses_wide_format():
    """필터 전체 내보내기는 long 포맷 교체와 무관하게 기존 wide 포맷 그대로다."""
    content = export_results([_sample_result()], "xlsx")
    wb = openpyxl.load_workbook(io.BytesIO(content))
    ws = wb["results"]
    header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]

    assert header == list(RESULT_COLUMN_LABELS.values())
    assert "매출액(전기)" in header
    assert "계정과목명" not in header
    assert ws.max_row == 2  # 회사 1건 = 1행


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
    """스냅샷 1건이 계정과목 24행으로 풀리고 식별 컬럼은 각 행에 반복된다(2026-07-29 long)."""
    df = snapshots_to_dataframe(_sample_snapshots(), {1: "㈜테스트"})

    assert list(df.columns) == list(FINANCIAL_SNAPSHOT_COLUMN_LABELS.keys())
    assert len(df) == 2 * len(FINANCIAL_SNAPSHOT_ACCOUNT_COLUMNS) == 48
    assert set(df["corp_name"]) == {"㈜테스트"}
    # 입력 순서(result_id → fiscal_year 오름차순) 보존: 앞 24행이 2024, 뒤 24행이 2025
    assert list(df["fiscal_year"])[:24] == ["2024"] * 24
    assert list(df["fiscal_year"])[24:] == ["2025"] * 24
    assert list(df["rcept_no"])[:24] == ["20250401000001"] * 24

    y2024 = df[df["fiscal_year"] == "2024"]
    assert y2024[y2024["account_name"] == "자산총계"].iloc[0]["amount"] == 5_000_000_000
    assert y2024[y2024["account_name"] == "영업활동현금흐름"].iloc[0]["amount"] == 1_000_000
    assert y2024[y2024["account_name"] == "영업외수익"].iloc[0]["amount"] == 2_000_000


def test_snapshots_to_dataframe_column_order_and_statement_classification():
    """컬럼 순서(7개)와 재무제표명 분류·행 순서를 사용자 확정안대로 잠근다(2026-07-29)."""
    df = snapshots_to_dataframe(_sample_snapshots()[:1], {1: "㈜테스트"})

    assert list(FINANCIAL_SNAPSHOT_COLUMN_LABELS.values()) == [
        "결과ID",
        "회사명",
        "회계연도",
        "접수번호",
        "재무제표명",
        "계정과목",
        "금액",
    ]
    # 감사인/파싱상태는 완전히 제거됐다.
    assert "감사인" not in FINANCIAL_SNAPSHOT_COLUMN_LABELS.values()
    assert "파싱상태" not in FINANCIAL_SNAPSHOT_COLUMN_LABELS.values()
    assert "auditor_name" not in df.columns and "parse_status" not in df.columns

    # 재무제표명 → 계정과목 순서: 재무상태표 9 → 손익계산서 9(영업외손익 포함) →
    # 현금흐름표 6 (2026-08-05 세부계정 5항목이 각 표 끝에 붙어 7/8/4 → 9/9/6).
    assert list(df["statement_name"]) == ["재무상태표"] * 9 + ["손익계산서"] * 9 + ["현금흐름표"] * 6
    assert list(df["account_name"]) == [
        "유동자산",
        "비유동자산",
        "자산총계",
        "유동부채",
        "비유동부채",
        "부채총계",
        "자본총계",
        "현금및현금성자산(순액)",
        "매출채권(순액)",
        "매출액",
        "매출원가",
        "매출총이익",
        "판매비와관리비",
        "영업이익",
        "당기순이익",
        "영업외수익",
        "영업외비용",
        "이자비용",
        "영업활동현금흐름",
        "투자활동현금흐름",
        "재무활동현금흐름",
        "기말의현금",
        "감가상각비(현금흐름표)",
        "무형자산상각비",
    ]


def test_snapshot_account_columns_match_selection_account_columns():
    """스냅샷 계정과목 24개가 `results`의 `_cur` 항목 전체와 1:1로 대응해야 한다.

    새 `_cur`/`_prv` 쌍이 추가되면 `financial_snapshots`에도 접미어 없는 같은
    필드가 생기는데(CF 4항목·영업외손익 2항목이 그랬다), 이 시트의 하드코딩 목록에
    반영하지 않으면 **에러 없이 조용히** 빠진다 — 그 드리프트를 여기서 잡는다.
    """
    assert {c.removesuffix("_cur") for c in SELECTION_ACCOUNT_COLUMNS} == set(
        FINANCIAL_SNAPSHOT_ACCOUNT_COLUMNS
    )
    # 라벨도 두 시트가 같아야 한다("매출액" 등 — 한쪽만 바뀌면 대조가 어려워진다).
    for col in SELECTION_ACCOUNT_COLUMNS:
        assert SELECTION_ACCOUNT_LABELS[col] == FINANCIAL_SNAPSHOT_ACCOUNT_LABELS[
            col.removesuffix("_cur")
        ]
    # 모든 계정과목이 정확히 하나의 재무제표에 속한다.
    assert set(FINANCIAL_SNAPSHOT_STATEMENT_BY_ACCOUNT) == set(FINANCIAL_SNAPSHOT_ACCOUNT_COLUMNS)
    assert set(FINANCIAL_SNAPSHOT_STATEMENT_BY_ACCOUNT.values()) == {
        "재무상태표",
        "손익계산서",
        "현금흐름표",
    }


def test_detail_account_fields_are_exported_in_both_sheets():
    """세부계정 5항목(2026-08-05)이 두 시트 모두에 원천 재무제표 기준으로 실린다.

    파서(`DETAIL_FINANCIAL_FIELDS`)가 뽑은 5항목이 DB 컬럼까지는 왔는데 export
    목록에 빠지면 **에러 없이 조용히** 안 나간다 — 필드 자체의 존재와 소속
    재무제표(원천 고정)를 함께 잠근다. 라벨의 출처 표기("(순액)"/"(현금흐름표)")는
    같은 이름의 다른 숫자와 혼동하지 않기 위한 것이라 문구까지 검증한다.
    """
    assert DETAIL_FINANCIAL_FIELDS == (
        "cash_and_equivalents",
        "trade_receivables",
        "interest_expense",
        "depreciation",
        "amortization",
    )
    # ① 시트: 당기(_cur) 컬럼으로 전부 실린다.
    for field in DETAIL_FINANCIAL_FIELDS:
        assert f"{field}_cur" in SELECTION_ACCOUNT_COLUMNS
        # wide 포맷에도 당기/전기 쌍으로 있다(전체 내보내기 경로).
        assert f"{field}_cur" in RESULT_COLUMN_LABELS
        assert f"{field}_prv" in RESULT_COLUMN_LABELS
    # ② 시트: 각 필드는 파서가 고정한 원천 재무제표에 속한다.
    assert FINANCIAL_SNAPSHOT_STATEMENT_BY_ACCOUNT["cash_and_equivalents"] == "재무상태표"
    assert FINANCIAL_SNAPSHOT_STATEMENT_BY_ACCOUNT["trade_receivables"] == "재무상태표"
    assert FINANCIAL_SNAPSHOT_STATEMENT_BY_ACCOUNT["interest_expense"] == "손익계산서"
    assert FINANCIAL_SNAPSHOT_STATEMENT_BY_ACCOUNT["depreciation"] == "현금흐름표"
    assert FINANCIAL_SNAPSHOT_STATEMENT_BY_ACCOUNT["amortization"] == "현금흐름표"
    # 라벨은 출처를 밝힌다(매출채권 총액/판관비 감가상각비와 혼동 방지).
    assert FINANCIAL_SNAPSHOT_ACCOUNT_LABELS["cash_and_equivalents"] == "현금및현금성자산(순액)"
    assert FINANCIAL_SNAPSHOT_ACCOUNT_LABELS["trade_receivables"] == "매출채권(순액)"
    assert FINANCIAL_SNAPSHOT_ACCOUNT_LABELS["depreciation"] == "감가상각비(현금흐름표)"
    # 각 표의 항목 수(7/8/4 → 9/9/6)
    assert [len(a) for a in FINANCIAL_SNAPSHOT_ACCOUNTS_BY_STATEMENT.values()] == [9, 9, 6]


def test_detail_account_values_reach_both_sheets():
    """세부계정 값이 실제로 두 시트의 "금액" 칸까지 흘러간다(결측이면 빈 값)."""
    result = _sample_result()
    result.cash_and_equivalents_cur = 1_500_000_000
    result.trade_receivables_cur = 2_500_000_000
    result.depreciation_cur = 300_000_000
    df = results_to_selection_dataframe([result])
    assert df[df["account_name"] == "현금및현금성자산(순액)"].iloc[0]["amount"] == 1_500_000_000
    assert df[df["account_name"] == "매출채권(순액)"].iloc[0]["amount"] == 2_500_000_000
    assert df[df["account_name"] == "감가상각비(현금흐름표)"].iloc[0]["amount"] == 300_000_000
    # best-effort라 결측이 정상 — 행은 남고 금액만 빈다.
    assert df[df["account_name"] == "무형자산상각비"].iloc[0]["amount"] is None
    assert df[df["account_name"] == "이자비용"].iloc[0]["amount"] is None

    snapshot = _sample_snapshots()[0]
    snapshot.interest_expense = 120_000_000
    snapshot.amortization = 45_000_000
    history = snapshots_to_dataframe([snapshot], {1: "㈜테스트"})
    assert history[history["account_name"] == "이자비용"].iloc[0]["amount"] == 120_000_000
    assert history[history["account_name"] == "무형자산상각비"].iloc[0]["amount"] == 45_000_000
    assert history[history["account_name"] == "현금및현금성자산(순액)"].iloc[0]["amount"] is None


def test_snapshots_to_dataframe_keeps_rows_for_missing_amounts():
    """값이 None인 계정과목도 행은 남기고 금액만 비운다(기본정보 시트와 동일 방침)."""
    df = snapshots_to_dataframe(_sample_snapshots()[:1], {1: "㈜테스트"})

    assert len(df) == 24
    assert df[df["account_name"] == "유동자산"].iloc[0]["amount"] is None
    # 값이 있는 것은 4개(자산총계/매출액/영업활동현금흐름/영업외수익)
    assert len(df[df["amount"].isna()]) == 20
    # float64로 승격되면 5000000000.0처럼 소수점이 붙는다.
    assert df["amount"].dtype == object


def test_snapshots_to_dataframe_unknown_result_id_leaves_corp_name_blank():
    """매핑에 없는 result_id는 회사명만 비고 파일 생성 자체는 실패하지 않는다."""
    df = snapshots_to_dataframe(_sample_snapshots(), {})
    assert df["corp_name"].isna().all()
    assert len(df) == 48


def test_export_results_with_history_writes_two_sheets():
    content = export_results_with_history(
        [_sample_result()], _sample_snapshots(), {1: "㈜테스트"}
    )
    wb = openpyxl.load_workbook(io.BytesIO(content))
    assert wb.sheetnames == ["results", "financial_history"]

    # 기본정보 시트는 기본값(=필터 전체 내보내기)에서 기존 wide 포맷이다 —
    # long 포맷은 `use_selection_format=True`(=`ids` 지정)일 때만 쓴다.
    results_ws = wb["results"]
    results_header = [c.value for c in next(results_ws.iter_rows(min_row=1, max_row=1))]
    assert results_header == list(RESULT_COLUMN_LABELS.values())
    assert results_ws.max_row == 2  # 헤더 + 회사 1건 = 1행

    history_ws = wb["financial_history"]
    history_header = [c.value for c in next(history_ws.iter_rows(min_row=1, max_row=1))]
    assert history_header == list(FINANCIAL_SNAPSHOT_COLUMN_LABELS.values())
    assert history_ws.max_row == 49  # 헤더 + 스냅샷 2건 × 계정과목 24행(2026-07-29 long)
    assert history_ws.cell(row=2, column=history_header.index("회사명") + 1).value == "㈜테스트"
    year_cell = history_ws.cell(row=2, column=history_header.index("회계연도") + 1).value
    assert str(year_cell) == "2024"

    stmt_col = history_header.index("재무제표명") + 1
    account_col = history_header.index("계정과목") + 1
    amount_col = history_header.index("금액") + 1
    assert history_ws.cell(row=2, column=stmt_col).value == "재무상태표"
    assert history_ws.cell(row=2, column=account_col).value == "유동자산"
    assert history_ws.cell(row=2, column=amount_col).value is None  # 결측은 빈 셀
    # 2024 스냅샷의 자산총계(재무상태표 3번째 행 = 시트 4행)
    assert history_ws.cell(row=4, column=account_col).value == "자산총계"
    assert history_ws.cell(row=4, column=amount_col).value == 5_000_000_000
    # 첫 스냅샷(2024)이 2~25행, 두 번째 스냅샷(2025)은 26행부터 시작한다.
    year_col = history_header.index("회계연도") + 1
    assert str(history_ws.cell(row=25, column=year_col).value) == "2024"
    assert str(history_ws.cell(row=26, column=year_col).value) == "2025"


def test_export_results_with_history_uses_long_format_only_when_asked():
    """기본정보 시트 포맷은 `use_selection_format`(=호출부의 `ids` 유무)만 따른다.

    `include_history`가 포맷을 바꾸면 `ids` 없이 이력만 요청한 전체 내보내기가
    조용히 long 포맷이 된다(dart-qa 2026-07-28 지적) — 두 경우를 함께 잠근다.
    """
    long_wb = openpyxl.load_workbook(
        io.BytesIO(
            export_results_with_history(
                [_sample_result()],
                _sample_snapshots(),
                {1: "㈜테스트"},
                use_selection_format=True,
            )
        )
    )
    long_ws = long_wb["results"]
    long_header = [c.value for c in next(long_ws.iter_rows(min_row=1, max_row=1))]
    assert long_header == list(SELECTION_EXPORT_COLUMN_LABELS.values())
    assert long_ws.max_row == 25  # 헤더 + 회사 1건 × 계정과목 24행
    # 시트 ②는 포맷과 무관하게 동일하다(스냅샷 2건 × 계정과목 24행 + 헤더).
    assert long_wb["financial_history"].max_row == 49

    wide_wb = openpyxl.load_workbook(
        io.BytesIO(
            export_results_with_history(
                [_sample_result()],
                _sample_snapshots(),
                {1: "㈜테스트"},
                use_selection_format=False,
            )
        )
    )
    wide_ws = wide_wb["results"]
    wide_header = [c.value for c in next(wide_ws.iter_rows(min_row=1, max_row=1))]
    assert wide_header == list(RESULT_COLUMN_LABELS.values())
    assert "매출액(전기)" in wide_header and "계정과목명" not in wide_header
    assert wide_ws.max_row == 2
    assert wide_wb["financial_history"].max_row == 49


def test_export_results_with_history_empty_inputs_return_header_only_sheets():
    content = export_results_with_history([], [], {})
    wb = openpyxl.load_workbook(io.BytesIO(content))
    assert wb.sheetnames == ["results", "financial_history"]
    assert wb["results"].max_row == 1
    assert wb["financial_history"].max_row == 1
