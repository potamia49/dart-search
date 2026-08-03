"""app/reports/audit_proposal.py + `POST /api/jobs/{id}/generate-report` 테스트.

보고서 생성은 **로컬 파일 시스템에 폴더/파일을 떨구는** 기능이라, 테스트는
`get_settings()`를 스텁으로 갈아끼워 산출물 경로를 `tmp_path`로 돌린다
(개발자 저장소의 실제 `backend/report/`를 더럽히지 않기 위함).

템플릿은 저장소 루트의 실제 `tamplate/audit_proposal_template.html`을 그대로
쓴다 — 치환 대상(`const EMBEDDED_DATA = {...};`)이 실제 템플릿에서도 정확히
잡히는지가 이 기능의 핵심 계약이기 때문이다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as app_main
from app.core.db import get_db
from app.exporters.excel import FINANCIAL_SNAPSHOT_ACCOUNT_LABELS
from app.models import Base
from app.models.financial_snapshot import FinancialSnapshot
from app.models.job import Job, JobStatus
from app.models.result import ParseStatus, Result
from app.reports import audit_proposal, firm_profile
from app.reports.audit_proposal import (
    LABEL_FILENAME,
    RATIO_REQUIRED_KEYS,
    SNAPSHOT_FIELD_TO_REPORT_KEY,
    ReportGenerationError,
    ReportInput,
    allocate_output_dir,
    build_financial_rows,
    build_report_payload,
    collect_warnings,
    excel_safe_text,
    find_embedded_data_block,
    generate_reports,
    missing_ratio_inputs,
    render_report_html,
    resolve_template_path,
    sanitize_filename_stem,
    select_financial_rows,
    unique_filename,
    write_label_workbook,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "tamplate" / "audit_proposal_template.html"


@pytest.fixture
def template_text() -> str:
    assert TEMPLATE_PATH.is_file(), f"보고서 템플릿이 없습니다: {TEMPLATE_PATH}"
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture
def report_settings(tmp_path, monkeypatch):
    """산출물 폴더를 tmp_path로, 템플릿을 저장소 실제 파일로 고정한다."""
    stub = SimpleNamespace(
        report_output_dir=str(tmp_path / "report"),
        report_template_path=str(TEMPLATE_PATH),
    )
    monkeypatch.setattr(audit_proposal, "get_settings", lambda: stub)
    return stub


def _extract_embedded_json(html: str) -> dict:
    """생성된 HTML에서 EMBEDDED_DATA 객체를 JSON으로 다시 읽어온다."""
    start, end = find_embedded_data_block(html)
    block = html[start:end]
    body = block.split("=", 1)[1].strip().rstrip(";")
    return json.loads(body)


# ---------------------------------------------------------------------------
# 매핑 드리프트 가드
# ---------------------------------------------------------------------------


def test_snapshot_field_mapping_matches_real_snapshot_columns():
    """보고서가 쓰는 재무 필드는 전부 `financial_snapshots`의 실제 계정 컬럼이어야 한다.

    엑셀 내보내기의 `FINANCIAL_SNAPSHOT_ACCOUNT_LABELS`(같은 테이블의 계정 컬럼
    정의)를 기준으로 대조한다 — 컬럼명을 오타로 적으면 조용히 전부 null인 보고서가
    나오므로 여기서 먼저 깨지게 한다.
    """
    unknown = set(SNAPSHOT_FIELD_TO_REPORT_KEY) - set(FINANCIAL_SNAPSHOT_ACCOUNT_LABELS)
    assert not unknown, f"스냅샷에 없는 컬럼을 매핑하고 있습니다: {sorted(unknown)}"
    # 템플릿이 요구하는 13항목(재무상태표 7 + 손익계산서 6)이 모두 있어야 한다.
    assert len(SNAPSHOT_FIELD_TO_REPORT_KEY) == 13


def test_template_is_resolvable_from_repo(report_settings):
    assert resolve_template_path() == TEMPLATE_PATH


# ---------------------------------------------------------------------------
# 템플릿 치환
# ---------------------------------------------------------------------------


def test_render_replaces_embedded_data_only(template_text):
    payload = {
        "firm": {"name": "금바다세무회계"},
        "company": {"name": "㈜테스트"},
        "financials": [],
        "peers": [],
        "industryAverage": None,
        "regionGroup": [],
        "opinionSummary": "",
    }
    html = render_report_html(template_text, payload)

    # EMBEDDED_DATA 블록은 딱 하나 남고, 그 안의 값이 교체돼야 한다.
    assert html.count("const EMBEDDED_DATA") == 1
    assert _extract_embedded_json(html) == payload
    # 템플릿 원문(HTML/CSS/렌더 로직)은 건드리지 않는다.
    assert "async function loadData()" in html
    assert "</html>" in html
    # 자리표시자 주석("예: ...")이 남은 옛 블록이 그대로 있지 않아야 한다.
    assert '"firm": {\n    "name": "",' not in html


def test_render_escapes_script_terminator_in_company_name(template_text):
    """회사명에 `</script>`가 들어가도 HTML이 조기 종료되면 안 된다."""
    payload = {
        "firm": {"name": "F"},
        "company": {"name": '㈜악의</script><script>alert("x")</script>'},
        "financials": [],
        "peers": [],
        "industryAverage": None,
        "regionGroup": [],
        "opinionSummary": "",
    }
    html = render_report_html(template_text, payload)

    start, end = find_embedded_data_block(html)
    block = html[start:end]
    assert "</script" not in block
    assert "\\u003c/script" in block
    # 이스케이프해도 JSON 값 자체는 원본 그대로 복원돼야 한다.
    assert _extract_embedded_json(html)["company"]["name"] == payload["company"]["name"]


def test_find_embedded_data_block_ignores_braces_in_comments_and_strings():
    text = (
        "prefix\n"
        "const EMBEDDED_DATA = {\n"
        '  // 주석 속 중괄호 { } 는 무시한다\n'
        '  "note": "문자열 속 } 도 무시",\n'
        "  /* 블록 주석 } */\n"
        '  "nested": { "a": 1 }\n'
        "};\n"
        "suffix"
    )
    start, end = find_embedded_data_block(text)
    assert text[:start] == "prefix\n"
    assert text[end:] == "\nsuffix"


def test_find_embedded_data_block_raises_when_marker_missing():
    with pytest.raises(ReportGenerationError):
        find_embedded_data_block("<html><script>var x = 1;</script></html>")


# ---------------------------------------------------------------------------
# 데이터 조립
# ---------------------------------------------------------------------------


def test_build_financial_rows_maps_korean_keys_in_year_order():
    snapshots = [
        SimpleNamespace(
            fiscal_year="2024",
            current_assets=11,
            noncurrent_assets=12,
            total_assets=23,
            current_liab=5,
            noncurrent_liab=3,
            total_liab=8,
            total_equity=15,
            revenue=100,
            cogs=60,
            gross_profit=40,
            sga=25,
            operating_income=15,
            net_income=10,
        ),
        SimpleNamespace(
            fiscal_year="2023",
            current_assets=1,
            noncurrent_assets=2,
            total_assets=3,
            current_liab=1,
            noncurrent_liab=1,
            total_liab=2,
            total_equity=1,
            revenue=50,
            cogs=None,
            gross_profit=20,
            sga=10,
            operating_income=10,
            net_income=5,
        ),
    ]
    rows = build_financial_rows(snapshots)

    assert [r["year"] for r in rows] == [2023, 2024]  # 연도 오름차순 + 숫자 변환
    assert rows[1]["매출원가"] == 60
    assert rows[1]["판관비"] == 25
    assert rows[0]["매출원가"] is None  # 결측은 null(템플릿이 "-"로 처리)
    assert set(rows[0]) == {"year", *SNAPSHOT_FIELD_TO_REPORT_KEY.values()}


def test_build_report_payload_leaves_unavailable_sections_empty():
    result = SimpleNamespace(
        id=1,
        corp_name="㈜테스트",
        induty_name="금속가공제품 제조업",
        address="경남 김해시 삼계로 1",
        ceo_name="홍길동",
        fiscal_date="20251231",
        audit_opinion="적정",
        auditor_name="안경회계법인",
    )
    payload = build_report_payload(ReportInput(result=result, snapshots=[]))

    assert payload["company"] == {
        "name": "㈜테스트",
        "industry": "금속가공제품 제조업",
        "address": "경남 김해시 삼계로 1",
        "ceo": "홍길동",
        "fiscalDate": "20251231",
        "opinion": "적정",
        "auditor": "안경회계법인",
    }
    assert payload["peers"] == []
    assert payload["industryAverage"] is None
    assert payload["regionGroup"] == []
    assert payload["opinionSummary"] == ""
    assert payload["firm"]["name"]  # 사무소 상수가 실려야 한다


# ---------------------------------------------------------------------------
# [H2/H3] 연도 선별 — 결측/0분모/FAILED는 제외, PARTIAL/전기유래는 싣되 경고
# ---------------------------------------------------------------------------


def test_missing_ratio_inputs_flags_nulls_and_zero_denominators():
    complete = {key: 10 for key in RATIO_REQUIRED_KEYS}
    complete["year"] = 2024
    assert missing_ratio_inputs(complete) == []

    # null 필수항목
    null_row = dict(complete, 매출총이익=None)
    assert missing_ratio_inputs(null_row) == ["매출총이익"]

    # 0 분모(Infinity 방지) — 분모가 아닌 항목의 0은 정상 값이라 걸리지 않는다.
    assert missing_ratio_inputs(dict(complete, 매출액=0)) == ["매출액"]
    assert missing_ratio_inputs(dict(complete, 자본총계=0)) == ["자본총계"]
    assert missing_ratio_inputs(dict(complete, 매출총이익=0)) == []
    # 결측/0이 아닌 항목(매출원가·판관비·비유동자산)은 애초에 필수가 아니다.
    assert "매출원가" not in RATIO_REQUIRED_KEYS
    assert "판관비" not in RATIO_REQUIRED_KEYS
    assert "비유동자산" not in RATIO_REQUIRED_KEYS


def test_select_financial_rows_excludes_failed_and_incomplete_years():
    snapshots = [
        _snapshot_stub("2021", parse_status=ParseStatus.FAILED),
        _snapshot_stub("2022", total_equity=None),  # 결측 → 제외
        _snapshot_stub("2023", parse_status=ParseStatus.PARTIAL),  # 실음 + 경고
        _snapshot_stub("2024", from_current_period=0),  # 실음 + 경고
    ]
    selection = select_financial_rows(snapshots)

    assert [row["year"] for row in selection.rows] == [2023, 2024]
    assert selection.total_years == 4
    assert selection.failed_years == ["2021"]
    assert selection.incomplete_years == [("2022", ["자본총계"])]
    assert selection.partial_years == ["2023"]
    assert selection.prior_period_years == ["2024"]
    # 실린 연도에는 비율 계산 필수항목이 하나도 비어 있지 않아야 한다.
    for row in selection.rows:
        assert missing_ratio_inputs(row) == []


def test_collect_warnings_reports_partial_prior_period_and_excluded_years():
    item = ReportInput(
        result=_result_stub(3, "㈜검수필요", "주소", parse_status=ParseStatus.PARTIAL),
        snapshots=[
            _snapshot_stub("2021", parse_status=ParseStatus.FAILED),
            _snapshot_stub("2022", revenue=None),
            _snapshot_stub("2023", parse_status=ParseStatus.PARTIAL),
            _snapshot_stub("2024", from_current_period=0),
        ],
    )
    messages = [w.message for w in collect_warnings(item, select_financial_rows(item.snapshots))]
    joined = " / ".join(messages)

    assert "파싱 실패(FAILED)한 회계연도를 보고서에서 제외" in joined and "2021" in joined
    assert "전체 4개년 중 1개년은 재무 항목이 결측" in joined and "2022년(매출액)" in joined
    assert "부분 파싱(PARTIAL)" in joined and "2023년" in joined
    assert "전기 항목에서 가져온 참고값" in joined and "2024년" in joined
    assert "이 회사의 파싱 상태가 PARTIAL입니다" in joined
    # 생성은 되므로 "생성하지 않았습니다"가 있으면 안 된다.
    assert "생성하지 않았습니다" not in joined


def test_collect_warnings_says_not_generated_when_all_years_unusable():
    item = ReportInput(
        result=_result_stub(4, "㈜전부실패", "주소"),
        snapshots=[
            _snapshot_stub("2023", parse_status=ParseStatus.FAILED),
            _snapshot_stub("2024", total_assets=None),
        ],
    )
    messages = [w.message for w in collect_warnings(item, select_financial_rows(item.snapshots))]

    assert messages[-1].endswith("이 회사는 보고서를 생성하지 않았습니다.")
    assert "전체 2개년이 모두" in messages[-1]


def test_collect_warnings_says_not_generated_when_history_is_empty():
    item = ReportInput(result=_result_stub(5, "㈜이력없음", "주소"), snapshots=[])
    messages = [w.message for w in collect_warnings(item, select_financial_rows([]))]

    assert messages == ["재무 이력이 없어 이 회사는 보고서를 생성하지 않았습니다."]


# ---------------------------------------------------------------------------
# 폴더/파일명
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("(주)홍성산업", "(주)홍성산업"),
        ('㈜a/b:c*d?e"f<g>h|i', "㈜a_b_c_d_e_f_g_h_i"),
        ("이름 끝 점...", "이름 끝 점"),
        ("   ", "회사명없음"),
        (None, "회사명없음"),
        ("con", "_con"),
    ],
)
def test_sanitize_filename_stem(raw, expected):
    assert sanitize_filename_stem(raw) == expected


def test_unique_filename_suffixes_duplicates_case_insensitively():
    taken: set[str] = set()
    assert unique_filename("㈜동명", ".html", taken) == "㈜동명.html"
    assert unique_filename("㈜동명", ".html", taken) == "㈜동명_2.html"
    assert unique_filename("㈜동명", ".html", taken) == "㈜동명_3.html"


def test_allocate_output_dir_never_overwrites(tmp_path):
    today = date(2026, 8, 3)
    first = allocate_output_dir(tmp_path, today)
    (first / "keep.txt").write_text("keep", encoding="utf-8")
    second = allocate_output_dir(tmp_path, today)

    assert first.name == "2026-08-03"
    assert second.name == "2026-08-03_2"
    assert (first / "keep.txt").read_text(encoding="utf-8") == "keep"


# ---------------------------------------------------------------------------
# generate_reports (파일 생성)
# ---------------------------------------------------------------------------


def _result_stub(
    result_id: int, name: str, address: str, parse_status: str = ParseStatus.OK
) -> SimpleNamespace:
    return SimpleNamespace(
        id=result_id,
        corp_name=name,
        induty_name="금속가공제품 제조업",
        address=address,
        ceo_name="홍길동",
        fiscal_date="20251231",
        audit_opinion="적정",
        auditor_name="안경회계법인",
        parse_status=parse_status,
    )


def _snapshot_stub(year: str, revenue: int | None = 100, **overrides) -> SimpleNamespace:
    fields = {
        "fiscal_year": year,
        "current_assets": 10,
        "noncurrent_assets": 10,
        "total_assets": 20,
        "current_liab": 5,
        "noncurrent_liab": 5,
        "total_liab": 10,
        "total_equity": 10,
        "revenue": revenue,
        "cogs": 60,
        "gross_profit": 40,
        "sga": 20,
        "operating_income": 20,
        "net_income": 10,
        "parse_status": ParseStatus.OK,
        "from_current_period": 1,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_generate_reports_writes_html_per_company_and_label_excel(report_settings, tmp_path):
    items = [
        ReportInput(
            result=_result_stub(1, "(주)이력있음", "경남 김해시 1"),
            snapshots=[_snapshot_stub("2023"), _snapshot_stub("2024")],
        ),
        ReportInput(result=_result_stub(2, "(주)이력없음", "경남 김해시 2"), snapshots=[]),
    ]

    outcome = generate_reports(items, today=date(2026, 8, 3))

    assert outcome.output_dir == tmp_path / "report" / "2026-08-03"
    # 재무이력이 없는 회사는 **파일을 만들지 않는다**(템플릿 렌더가 중단돼 연락처까지
    # 빈 반쪽 문서가 나오기 때문).
    assert [f.filename for f in outcome.files] == ["(주)이력있음.html"]
    assert not (outcome.output_dir / "(주)이력없음.html").exists()

    html = (outcome.output_dir / "(주)이력있음.html").read_text(encoding="utf-8")
    data = _extract_embedded_json(html)
    assert data["company"]["name"] == "(주)이력있음"
    assert [row["year"] for row in data["financials"]] == [2023, 2024]

    assert [(s.result_id, s.corp_name) for s in outcome.skipped] == [(2, "(주)이력없음")]
    warned = [w for w in outcome.warnings if w.result_id == 2]
    assert warned and "보고서를 생성하지 않았습니다" in warned[-1].message
    assert not [w for w in outcome.warnings if w.result_id == 1]

    # 발송처 라벨 엑셀 — 실제로 생성된 회사만(보고서 없는 회사에 우편을 보내면 안 된다).
    wb = openpyxl.load_workbook(outcome.output_dir / LABEL_FILENAME)
    rows = list(wb.active.iter_rows(values_only=True))
    assert rows == [
        ("회사명", "주소"),
        ("(주)이력있음", "경남 김해시 1"),
    ]


def test_generate_reports_skips_company_whose_only_year_is_incomplete(report_settings):
    """[H1/H2] 유일한 연도의 매출액이 없으면 → 실을 연도 0건 → 생성 자체를 건너뛴다."""
    items = [
        ReportInput(
            result=_result_stub(7, "(주)한해만", "경남 김해시 7"),
            snapshots=[_snapshot_stub("2024", revenue=None)],
        )
    ]
    outcome = generate_reports(items, today=date(2026, 8, 3))
    messages = " / ".join(w.message for w in outcome.warnings)

    assert outcome.files == []
    assert list(outcome.output_dir.glob("*.html")) == []
    assert [s.result_id for s in outcome.skipped] == [7]
    assert "2024년(매출액)" in messages  # 어떤 항목 때문에 제외됐는지
    assert "보고서를 생성하지 않았습니다" in messages


def test_generate_reports_disambiguates_same_company_names(report_settings):
    items = [
        ReportInput(
            result=_result_stub(1, "㈜동명", "주소1"),
            snapshots=[_snapshot_stub("2023"), _snapshot_stub("2024")],
        ),
        ReportInput(
            result=_result_stub(2, "㈜동명", "주소2"),
            snapshots=[_snapshot_stub("2023"), _snapshot_stub("2024")],
        ),
    ]
    outcome = generate_reports(items, today=date(2026, 8, 3))

    assert [f.filename for f in outcome.files] == ["㈜동명.html", "㈜동명_2.html"]
    assert len(list(outcome.output_dir.glob("*.html"))) == 2


def test_generate_reports_rejects_empty_selection(report_settings):
    with pytest.raises(ReportGenerationError):
        generate_reports([])


def test_generated_html_never_contains_unusable_financials(report_settings):
    """[H1/H2] 생성된 HTML의 `financials`는 항상 비어있지 않고 전부 완전한 연도다.

    템플릿 렌더 메인은 `financials[financials.length-1].매출액`을 무조건 읽고
    `calcRatios()`가 null-safe하지 않으므로, 이 두 조건이 깨지면 문서 뒷부분
    (사무소 소개·담당자·연락처 포함)이 통째로 빈 채 인쇄된다.
    """
    items = [
        ReportInput(result=_result_stub(1, "㈜정상", "주소1"), snapshots=[_snapshot_stub("2024")]),
        ReportInput(result=_result_stub(2, "㈜이력없음", "주소2"), snapshots=[]),
        ReportInput(
            result=_result_stub(3, "㈜일부결측", "주소3"),
            snapshots=[_snapshot_stub("2023", current_liab=None), _snapshot_stub("2024")],
        ),
        ReportInput(
            result=_result_stub(4, "㈜매출0", "주소4"),
            snapshots=[_snapshot_stub("2024", revenue=0)],
        ),
    ]
    outcome = generate_reports(items, today=date(2026, 8, 3))

    assert [f.result_id for f in outcome.files] == [1, 3]
    assert [s.result_id for s in outcome.skipped] == [2, 4]
    for entry in outcome.files:
        data = _extract_embedded_json(
            (outcome.output_dir / entry.filename).read_text(encoding="utf-8")
        )
        assert data["financials"], "빈 financials가 실리면 템플릿 렌더가 중단된다"
        for row in data["financials"]:
            assert missing_ratio_inputs(row) == []

    # 결측 연도를 제외한 회사는 그 사실이 경고에 남는다(내용은 있지만 1개년뿐).
    joined = " / ".join(w.message for w in outcome.warnings if w.result_id == 3)
    assert "전체 2개년 중 1개년은 재무 항목이 결측" in joined


def test_generate_reports_warns_once_when_firm_contact_is_placeholder(
    report_settings, monkeypatch
):
    """[M1] 자리표시자 연락처는 회사 수와 무관하게 요청당 1건만 경고한다."""
    items = [
        ReportInput(result=_result_stub(1, "㈜가", "주소1"), snapshots=[_snapshot_stub("2024")]),
        ReportInput(result=_result_stub(2, "㈜나", "주소2"), snapshots=[_snapshot_stub("2024")]),
    ]
    outcome = generate_reports(items, today=date(2026, 8, 3))
    contact_warnings = [w for w in outcome.warnings if "사무소 연락처" in w.message]

    assert len(contact_warnings) == 1
    assert contact_warnings[0].result_id is None

    # 실제 연락처를 채우면 경고가 사라진다.
    monkeypatch.setitem(audit_proposal.FIRM_PROFILE, "contact", "T. 055-000-0000")
    filled = generate_reports(items, today=date(2026, 8, 3))
    assert not [w for w in filled.warnings if "사무소 연락처" in w.message]


def test_firm_profile_placeholder_detection():
    assert firm_profile.is_placeholder_contact(firm_profile.FIRM_PROFILE["contact"]) is True
    assert firm_profile.is_placeholder_contact(None) is True
    assert firm_profile.is_placeholder_contact("   ") is True
    assert firm_profile.is_placeholder_contact("T. 055-000-0000") is False


# ---------------------------------------------------------------------------
# [H4] 자본잠식 회사의 재무안정성 등급 (템플릿 `scoreStability`)
# ---------------------------------------------------------------------------
#
# 템플릿은 원칙적으로 "수정 불필요 영역"이지만, 완전자본잠식(자본총계 < 0) 회사의
# 부채비율(=부채총계/자본총계)이 **음수**가 되어 `last.부채비율 < 0.5` 임계값 비교에
# 걸려 +30점을 받는 결함은 백엔드 필터로 막을 수 없어(음수는 null도 0도 NaN도 아니라
# `select_financial_rows()`의 결측/0분모 가드에 걸리지 않는다) 템플릿을 직접 고쳤다
# (2026-08-03, 사용자 승인). 아래 두 테스트가 그 수정이 되돌아가지 않게 잠근다.


def _extract_js_function_body(template: str, name: str) -> str:
    match = re.search(
        r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{(.*?)\n\}",
        template,
        re.S,
    )
    assert match, f"템플릿에서 {name}() 함수를 찾지 못했습니다"
    return match.group(1)


def _strip_js_comments(code: str) -> str:
    """JS 주석을 지운 코드만 남긴다.

    이 저장소의 템플릿 수정에는 **왜 고쳤는지를 적은 긴 주석**이 함께 들어가고, 그
    주석이 "수정 전 수식"을 그대로 인용하는 경우가 있다(예: `renderLineChart`의
    `cy = padT+plotH*(1-v/maxV)`). 주석까지 검사하면 "옛 수식이 남아 있으면 실패"
    같은 회귀 가드를 쓸 수 없으므로 코드만 남겨서 본다.
    """
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    return re.sub(r"^\s*//.*$", "", code, flags=re.M)


def _grade_d_threshold(template: str) -> int:
    """템플릿 `grade()`가 D를 주는 상한(=가장 낮은 임계값)을 템플릿에서 직접 읽는다."""
    body = _extract_js_function_body(template, "grade")
    thresholds = [int(v) for v in re.findall(r"score\s*>=\s*(\d+)", body)]
    assert thresholds, "템플릿 grade()의 점수 임계값을 찾지 못했습니다"
    return min(thresholds)


def test_score_stability_forces_low_score_on_capital_impairment(template_text):
    """[H4] 부채비율이 음수(자본잠식)면 D 등급이 확정되는 가드가 템플릿에 있어야 한다.

    가드가 없거나 임계값 비교(`< 0.5`)보다 뒤에 오면 음수가 "0.5 미만"으로 취급돼
    +30점을 받고, 자본이 전액 잠식된 회사가 A/B(우수·양호)로 인쇄된다.
    """
    body = _extract_js_function_body(template_text, "scoreStability")
    guard = re.search(r"if\s*\(\s*last\.부채비율\s*<\s*0\s*\)\s*return\s+(\d+)\s*;", body)
    assert guard, "자본잠식(부채비율 음수) 가드가 scoreStability()에 없습니다"

    # 반환 점수가 D 등급 구간(템플릿 grade()의 최저 임계값 미만)이어야 한다.
    assert int(guard.group(1)) < _grade_d_threshold(template_text)
    # 가드는 반드시 기존 임계값 비교보다 **먼저** 와야 한다.
    assert body.index(guard.group(0)) < body.index("부채비율 < 0.5")


# 템플릿 JS를 실제로 실행해 등급 문자열·리스크 문구·차트 도형까지 확인하는 end-to-end
# 검증용 최소 DOM 스텁. (jsdom을 새로 설치하지 않기 위해 Node 내장 `vm` + 스텁 document만
# 쓴다.) 출력은 다음 형태다:
#   {파일명: {"grades": {영역: 등급},
#             "risks": [[level, text], ...],
#             "kpi": kpiRow innerHTML,
#             "charts": {컨테이너id: {"nodes": [{tag, attrs, text, html}, ...], "html": ...}}}}
# `charts`의 `nodes`는 컨테이너 아래 SVG/legend 트리를 전부 펼친 것이라, 실제로 그려진
# 도형의 좌표(circle의 cy 등)와 범례 문구를 그대로 검사할 수 있다.
_NODE_RENDER_HARNESS = r"""
const fs = require('fs'), path = require('path'), vm = require('vm');
const dir = process.argv[2];
function makeEl(tag){
  return {
    tagName: tag, _text: '', _html: '', children: [], attrs: {},
    classList: { add(){}, remove(){}, contains(){ return false; } },
    setAttribute(k, v){ this.attrs[k] = v; },
    appendChild(c){ this.children.push(c); return c; },
    get textContent(){ return this._text; },
    set textContent(v){ this._text = String(v); },
    get innerHTML(){ return this._html; },
    set innerHTML(v){ this._html = String(v); },
  };
}
const result = {};
for(const file of fs.readdirSync(dir).filter(f => f.endsWith('.html'))){
  const html = fs.readFileSync(path.join(dir, file), 'utf8');
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  const els = new Map();
  const document = {
    getElementById(id){ if(!els.has(id)) els.set(id, makeEl(id)); return els.get(id); },
    querySelector(sel){ if(!els.has(sel)) els.set(sel, makeEl(sel)); return els.get(sel); },
    createElement(t){ return makeEl(t); },
    createElementNS(ns, t){ return makeEl(t); },
  };
  const sandbox = { document, console, Math, JSON, Date, Number, String, Array,
                    setTimeout, window: {} };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(scripts[scripts.length - 1], sandbox, { filename: file });
  setTimeout(() => {   // async IIFE의 마이크로태스크가 끝난 뒤 읽는다
    const cards = (els.get('gradeCards') || {})._html || '';
    const grades = {};
    for(const m of cards.matchAll(
        /<div class="label">(.*?)<\/div><div class="grade">(\w)<\/div>/g)){
      grades[m[1]] = m[2];
    }
    const riskHtml = (els.get('riskList') || {})._html || '';
    const risks = [...riskHtml.matchAll(
        /<div class="risk-item risk-(\w+)"><span class="dot"><\/span><span>(.*?)<\/span><\/div>/g)
      ].map(m => [m[1], m[2]]);
    const collect = (el, out) => {
      (el.children || []).forEach(c => {
        out.push({ tag: c.tagName, attrs: c.attrs, text: c._text, html: c._html });
        collect(c, out);
      });
      return out;
    };
    const charts = {};
    for(const id of ['chartStability', 'chartStructure', 'chartRevenue']){
      const el = els.get(id);
      charts[id] = el ? { nodes: collect(el, []), html: el._html } : null;
    }
    const kpi = (els.get('kpiRow') || {})._html || '';
    result[file] = { grades, risks, kpi, charts };
    if(Object.keys(result).length === fs.readdirSync(dir).filter(f => f.endsWith('.html')).length){
      console.log(JSON.stringify(result));
    }
  }, 0);
}
"""


def _render_reports_with_node(output_dir: Path, tmp_path: Path) -> dict:
    """생성된 HTML을 Node로 실제 렌더해 {파일명: {"grades":…, "risks":…}}를 돌려준다."""
    harness = tmp_path / "render_report.js"
    harness.write_text(_NODE_RENDER_HARNESS, encoding="utf-8")
    proc = subprocess.run(
        [shutil.which("node"), str(harness), str(output_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js가 없어 템플릿 렌더 검증 생략")
def test_rendered_report_grades_capital_impaired_company_as_d(report_settings, tmp_path):
    """[H4] 생성된 HTML을 실제로 렌더해 자본잠식 회사가 D(주의 필요)로 나오는지 확인.

    같은 요청에 정상(자본총계 양수) 회사를 함께 넣어, 그쪽 등급은 기존과 동일한지
    (회귀 없음) 함께 본다.
    """
    impaired = [  # 자본총계 < 0 → 부채비율 음수
        _snapshot_stub("2024", total_equity=-40, total_liab=60, total_assets=20),
        _snapshot_stub("2025", total_equity=-50, total_liab=70, total_assets=20),
    ]
    items = [
        ReportInput(result=_result_stub(1, "㈜자본잠식", "주소1"), snapshots=impaired),
        ReportInput(
            result=_result_stub(2, "㈜정상", "주소2"),
            snapshots=[_snapshot_stub("2024"), _snapshot_stub("2025")],
        ),
    ]
    outcome = generate_reports(items, today=date(2026, 8, 3))
    assert [f.result_id for f in outcome.files] == [1, 2]

    rendered = _render_reports_with_node(outcome.output_dir, tmp_path)

    # 자본잠식 회사 → 재무안정성 D(주의 필요). 부채비율 -1.4는 예전 로직에서 "0.5 미만"에
    # 걸려 +30점을 받아 B/A로 인쇄됐다.
    assert rendered["㈜자본잠식.html"]["grades"]["재무안정성"] == "D"
    # 정상 회사(부채비율 1.0 / 유동비율 2.0)는 기존과 같은 B.
    assert rendered["㈜정상.html"]["grades"]["재무안정성"] == "B"


# ---------------------------------------------------------------------------
# [H4-2] 자본잠식 회사의 리스크 문구 (템플릿 `buildRiskList`)
# ---------------------------------------------------------------------------
#
# 위 `scoreStability` 수정과 같은 뿌리의 결함이다 — 음수 부채비율이 단순 대소 비교
# `last.부채비율 < first.부채비율`도 통과해, 자본잠식으로 **악화**된 회사에
# "부채비율이 …로 지속 개선되어 재무안정성이 강화되었습니다"라는 초록 문구가 찍혔다
# (개발 DB 실측 81건). 같은 문서의 등급은 D(주의 필요)라 한 보고서 안에서 서술이
# 모순됐다. 2026-08-03에 사용자 승인 하에 템플릿을 두 번째로 고쳤고, 아래 두 테스트가
# 그 수정을 잠근다.

_RISK_IMPROVEMENT_TEXT = "지속 개선되어 재무안정성이 강화"
_RISK_IMPAIRMENT_TEXT = "완전자본잠식"


def test_risk_list_never_calls_capital_impairment_an_improvement(template_text):
    """[H4-2] `buildRiskList()`의 부채비율 개선 판정에 음수 가드가 있어야 한다."""
    body = _extract_js_function_body(template_text, "buildRiskList")

    guard = re.search(r"if\s*\(\s*last\.부채비율\s*<\s*0\s*\)", body)
    assert guard, "자본잠식(부채비율 음수) 가드가 buildRiskList()에 없습니다"

    improvement = re.search(
        r"(?:else\s+)?if\s*\(([^)]*last\.부채비율\s*<\s*first\.부채비율[^)]*)\)", body
    )
    assert improvement, "부채비율 개선 판정 분기를 찾지 못했습니다"
    # 개선 판정은 가드보다 뒤에 있어야 하고, 시작 연도가 음수인 경우(자본잠식 상태에서
    # 시작)도 배제해야 한다 — 음수는 항상 "더 낮은 값"이라 대소 비교가 뒤집힌다.
    assert body.index(guard.group(0)) < body.index(improvement.group(0))
    assert "first.부채비율 >= 0" in improvement.group(1)

    # 가드 분기는 초록(개선)이 아니라 빨강(위험) 항목을 넣어야 한다.
    guard_block = body[body.index(guard.group(0)) : body.index(improvement.group(0))]
    assert "level:'red'" in guard_block
    assert _RISK_IMPAIRMENT_TEXT in guard_block
    assert "level:'green'" not in guard_block


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js가 없어 템플릿 렌더 검증 생략")
def test_rendered_risk_list_warns_on_capital_impairment_instead_of_improvement(
    report_settings, tmp_path
):
    """[H4-2] 실제 렌더 결과에서 모순 문구가 사라지고 자본잠식 경고가 나오는지 확인.

    실사례(`results.id=6223`, 부채비율 903.1% → -336.8%)와 같은 형태의 회사,
    시작 연도부터 이미 자본잠식이면서 더 악화된 회사, 그리고 정상적으로 부채비율이
    개선된 회사(회귀 확인용)를 함께 넣는다.
    """
    items = [
        ReportInput(  # 903.1% → -336.8% (자본잠식 진입) — 실사례 6223과 같은 형태
            result=_result_stub(1, "㈜자본잠식진입", "주소1"),
            snapshots=[
                _snapshot_stub("2022", total_liab=903, total_equity=100, total_assets=1003),
                _snapshot_stub("2025", total_liab=337, total_equity=-100, total_assets=237),
            ],
        ),
        ReportInput(  # -600% → -1300% (이미 자본잠식 + 악화, 숫자상으론 "감소")
            result=_result_stub(2, "㈜자본잠식악화", "주소2"),
            snapshots=[
                _snapshot_stub("2022", total_liab=600, total_equity=-100, total_assets=500),
                _snapshot_stub("2025", total_liab=1300, total_equity=-100, total_assets=1200),
            ],
        ),
        ReportInput(  # 182.9% → 148.6% (실제 개선) — 기존 초록 문구가 유지돼야 한다
            result=_result_stub(3, "㈜정상개선", "주소3"),
            snapshots=[
                _snapshot_stub("2022", total_liab=1829, total_equity=1000, total_assets=2829),
                _snapshot_stub("2025", total_liab=1486, total_equity=1000, total_assets=2486),
            ],
        ),
    ]
    outcome = generate_reports(items, today=date(2026, 8, 3))
    assert [f.result_id for f in outcome.files] == [1, 2, 3]

    rendered = _render_reports_with_node(outcome.output_dir, tmp_path)

    for filename in ("㈜자본잠식진입.html", "㈜자본잠식악화.html"):
        risks = rendered[filename]["risks"]
        texts = " / ".join(text for _, text in risks)
        assert _RISK_IMPROVEMENT_TEXT not in texts, f"{filename}: 모순된 개선 문구가 남아 있다"
        impairment = [(level, text) for level, text in risks if _RISK_IMPAIRMENT_TEXT in text]
        assert len(impairment) == 1, f"{filename}: 자본잠식 경고가 없거나 중복된다"
        assert impairment[0][0] == "red"
        # 같은 문서의 등급과 서술이 일치해야 한다(등급 D + 빨강 경고).
        assert rendered[filename]["grades"]["재무안정성"] == "D"

    # 정상 개선 회사는 기존과 동일하게 초록 개선 문구가 그대로 나온다.
    normal = rendered["㈜정상개선.html"]["risks"]
    improvement = [(level, text) for level, text in normal if _RISK_IMPROVEMENT_TEXT in text]
    assert len(improvement) == 1
    assert improvement[0][0] == "green"
    assert "182.9%에서 148.6%" in improvement[0][1]
    assert not [text for _, text in normal if _RISK_IMPAIRMENT_TEXT in text]


# ---------------------------------------------------------------------------
# [M-1] 자본구성 도넛 차트 (템플릿 `renderDoughnut`) — 템플릿 무수정 관행의 예외 ③
# ---------------------------------------------------------------------------
#
# 위 H4/H4-2와 같은 뿌리다. 자본총계가 음수면 `total`(=유동부채+비유동부채+자본총계)이
# 부채 합보다 작아져 유동부채 조각의 `frac`이 1을 크게 넘고(원호가 여러 바퀴 겹침),
# 자본총계 조각은 음수 스윕이 되며, 범례에는 "유동부채 903.2% · 자본총계 -864.7%"처럼
# 말이 되지 않는 구성비가 인쇄됐다(실측 result_id=8583). 등급(D)·리스크 문구는 이미
# 자본잠식을 반영하고 있어 차트만 모순되던 상태였다.

_DOUGHNUT_FALLBACK_TEXT = "구성비를 표시할 수 없습니다"


def test_doughnut_chart_guards_negative_slices(template_text):
    """[M-1] 음수 조각이 있으면 도넛(원호·구성비 범례)을 아예 그리지 않아야 한다."""
    body = _strip_js_comments(_extract_js_function_body(template_text, "renderDoughnut"))

    guard = re.search(r"if\s*\(\s*data\.some\([^)]*v\s*<\s*0\s*\)[^{]*\)\s*\{", body)
    assert guard, "음수 조각 가드가 renderDoughnut()에 없습니다"

    guard_at = body.index(guard.group(0))
    # 가드는 원호 계산(frac)과 구성비 범례(pct)보다 **먼저** 오고, 조기 return으로
    # 그 둘에 아예 도달하지 않아야 한다.
    assert guard_at < body.index("frac*Math.PI*2")
    assert guard_at < body.index("pct(data[i]/total)")
    guard_block = body[guard_at : body.index("frac*Math.PI*2")]
    assert "return;" in guard_block
    # 대체 표시는 구성비(%)가 아니라 금액이어야 한다.
    assert "백만원" in guard_block
    assert "pct(" not in guard_block


def test_line_chart_scale_includes_negative_values(template_text):
    """[M-2] 선그래프 y축이 음수까지 포함해 잡히고, 양수 구간은 기존과 동일해야 한다."""
    body = _strip_js_comments(_extract_js_function_body(template_text, "renderLineChart"))

    # 예전 스케일식(0~maxV 고정)은 남아 있으면 안 된다.
    assert "padT+plotH*(1-v/maxV)" not in body
    # 최솟값이 음수면 minV가 음수가 되고, 좌표는 (maxV-v)/span으로 잡는다.
    assert re.search(r"const\s+minV\s*=\s*rawMin\s*<\s*0\s*\?", body), "음수 최솟값 처리가 없습니다"
    assert re.search(r"yOf\s*=\s*v\s*=>\s*padT\s*\+\s*plotH\*\(maxV-v\)/span", body)
    # 0선을 별도로 긋는다(renderGroupedBarChart의 zeroY 패턴과 동일).
    assert "zeroY" in body


def _chart_nodes(rendered_file: dict, container_id: str) -> list[dict]:
    chart = rendered_file["charts"][container_id]
    assert chart is not None, f"{container_id} 컨테이너가 렌더되지 않았습니다"
    return chart["nodes"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js가 없어 템플릿 렌더 검증 생략")
def test_rendered_charts_handle_capital_impairment(report_settings, tmp_path):
    """[M-1/M-2] 자본잠식 회사의 두 차트가 실제 렌더에서 정상적으로 보이는지 확인.

    - 자본구성 도넛: 원호(path)를 그리지 않고 금액 안내로 대체, 범례에 %가 없다.
    - 재무안정성 선그래프: 음수 부채비율 지점이 plot 영역 안에 있어 잘리지 않는다.
    정상(자본총계 양수) 회사도 함께 넣어 기존 렌더가 그대로인지 확인한다.
    """
    items = [
        ReportInput(  # 자본총계 < 0 → 부채비율 음수
            result=_result_stub(1, "㈜자본잠식", "주소1"),
            snapshots=[
                _snapshot_stub("2024", total_liab=60, total_equity=-40, total_assets=20),
                _snapshot_stub("2025", total_liab=70, total_equity=-50, total_assets=20),
            ],
        ),
        ReportInput(
            result=_result_stub(2, "㈜정상", "주소2"),
            snapshots=[_snapshot_stub("2024"), _snapshot_stub("2025")],
        ),
    ]
    outcome = generate_reports(items, today=date(2026, 8, 3))
    rendered = _render_reports_with_node(outcome.output_dir, tmp_path)

    impaired = rendered["㈜자본잠식.html"]
    normal = rendered["㈜정상.html"]

    # --- [M-1] 도넛: 자본잠식이면 원호를 그리지 않고 금액 안내로 대체 ---
    impaired_structure = _chart_nodes(impaired, "chartStructure")
    assert not [n for n in impaired_structure if n["tag"] == "path"], "음수 조각 원호가 그려졌다"
    structure_text = " ".join(
        (n["text"] or "") + " " + (n["html"] or "") for n in impaired_structure
    )
    assert _DOUGHNUT_FALLBACK_TEXT in structure_text
    assert "백만원" in structure_text
    assert "%" not in structure_text, f"무의미한 구성비가 인쇄됐다: {structure_text}"

    # 정상 회사는 기존과 동일하게 도넛 3조각 + 구성비 범례가 그려진다.
    normal_structure = _chart_nodes(normal, "chartStructure")
    assert len([n for n in normal_structure if n["tag"] == "path"]) == 3
    normal_structure_text = " ".join((n["html"] or "") for n in normal_structure)
    assert "%" in normal_structure_text
    assert "-" not in re.sub(r"[^0-9.%\-]", "", normal_structure_text).replace("%", "")

    # --- [M-2] 선그래프: 음수 지점도 plot 영역(padT=14 ~ H-padB=164) 안에 있어야 한다 ---
    for name, chart in (("자본잠식", impaired), ("정상", normal)):
        cys = [
            float(n["attrs"]["cy"])
            for n in _chart_nodes(chart, "chartStability")
            if n["tag"] == "circle"
        ]
        assert cys, f"{name}: 선그래프 데이터 점이 없습니다"
        assert all(14 <= cy <= 164 for cy in cys), f"{name}: 차트 밖으로 잘린 점이 있다 {cys}"

    # 자본잠식 회사에는 0선이 추가로 그려진다(음수 구간이 있다는 표시).
    impaired_lines = [n for n in _chart_nodes(impaired, "chartStability") if n["tag"] == "line"]
    normal_lines = [n for n in _chart_nodes(normal, "chartStability") if n["tag"] == "line"]
    assert len(impaired_lines) == len(normal_lines) + 1


# ---------------------------------------------------------------------------
# [M-3] 음수 분모(매출액 등)가 등급을 반전시키는 문제 — 백엔드에서 막는다
# ---------------------------------------------------------------------------
#
# 실측 result_id=6704: 2021년 매출액 -5,409,049,845 / 영업이익 -1,705,664,600 →
# 영업이익률이 **+31.5%** 로 계산돼 수익성 등급이 A로 반전됐다(KPI에는 "매출액(2021)
# -5,409백만원"이 함께 찍혀 한 장 안에서 모순). 음수 분모는 비율의 부호를 통째로
# 뒤집으므로 결측과 동일하게 그 연도를 제외한다 — **단 `자본총계`는 제외 대상이
# 아니다**(자본잠식은 실재하는 상태라 연도를 버리면 H4가 의도한 "자본잠식 회사도
# 보고서는 생성하되 등급만 D"가 무너진다).


def test_missing_ratio_inputs_flags_negative_denominators_except_equity():
    """[M-3] 매출액/자산총계/유동부채는 음수도 제외, 자본총계 음수는 그대로 싣는다."""
    complete = {key: 10 for key in RATIO_REQUIRED_KEYS}
    complete["year"] = 2021

    assert missing_ratio_inputs(dict(complete, 매출액=-5_409_049_845)) == ["매출액"]
    assert missing_ratio_inputs(dict(complete, 자산총계=-1)) == ["자산총계"]
    assert missing_ratio_inputs(dict(complete, 유동부채=-1)) == ["유동부채"]

    # 자본잠식(자본총계 음수)은 정상 값으로 통과해야 한다 — H4의 전제.
    assert missing_ratio_inputs(dict(complete, 자본총계=-40)) == []
    # 0은 기존대로 모든 분모에서 제외(Infinity 방지).
    assert missing_ratio_inputs(dict(complete, 자본총계=0)) == ["자본총계"]
    # 분모가 아닌 항목의 음수(영업손실 등)는 정상 값이다.
    assert missing_ratio_inputs(dict(complete, 영업이익=-1_705_664_600)) == []
    assert missing_ratio_inputs(dict(complete, 당기순이익=-1)) == []
    assert missing_ratio_inputs(dict(complete, 매출총이익=-1)) == []

    assert "자본총계" not in audit_proposal.RATIO_POSITIVE_DENOMINATOR_KEYS
    assert audit_proposal.RATIO_POSITIVE_DENOMINATOR_KEYS < audit_proposal.RATIO_DENOMINATOR_KEYS


def test_select_financial_rows_drops_negative_revenue_year_but_keeps_capital_impairment():
    """[M-3] 음수 매출액 연도만 빠지고, 자본잠식 연도는 그대로 실린다."""
    snapshots = [
        _snapshot_stub("2020", revenue=4000, operating_income=-400),
        _snapshot_stub("2021", revenue=-5409, operating_income=-1706),  # 제외 대상
        _snapshot_stub("2022", total_liab=70, total_equity=-50, total_assets=20),  # 자본잠식
    ]
    selection = select_financial_rows(snapshots)

    assert [row["year"] for row in selection.rows] == [2020, 2022]
    assert selection.incomplete_years == [("2021", ["매출액"])]
    # 자본잠식 연도는 값 그대로 실려야 한다(H4가 템플릿에서 D 등급을 매기는 근거).
    assert selection.rows[-1]["자본총계"] == -50


def test_collect_warnings_explains_abnormal_denominator_exclusion():
    """[M-3] 음수 때문에 뺀 연도도 "결측"이 아니라 "결측이거나 비정상"으로 안내한다."""
    item = ReportInput(
        result=_result_stub(6704, "㈜매출음수", "주소"),
        snapshots=[
            _snapshot_stub("2020", revenue=4000, operating_income=-400),
            _snapshot_stub("2021", revenue=-5409, operating_income=-1706),
        ],
    )
    joined = " / ".join(
        w.message for w in collect_warnings(item, select_financial_rows(item.snapshots))
    )
    assert "비정상(0/음수)" in joined
    assert "2021년(매출액)" in joined
    assert "생성하지 않았습니다" not in joined  # 남은 연도가 있으므로 생성은 된다


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js가 없어 템플릿 렌더 검증 생략")
def test_rendered_report_does_not_invert_profitability_on_negative_revenue(
    report_settings, tmp_path
):
    """[M-3] 음수 매출액 연도를 뺀 뒤 수익성 등급이 A로 반전되지 않는지 실제 렌더로 확인.

    같은 요청에 자본잠식 회사를 넣어 **H4의 "자본잠식도 보고서는 생성하되 D"** 가
    이 변경으로 깨지지 않는지 함께 본다(회귀 위험이 가장 큰 지점).
    """
    items = [
        ReportInput(  # 실측 6704과 같은 형태 — 마지막 연도의 매출액이 음수
            result=_result_stub(1, "㈜매출음수", "주소1"),
            snapshots=[
                _snapshot_stub("2020", revenue=4000, operating_income=-400),
                _snapshot_stub("2021", revenue=-5409, operating_income=-1706),
            ],
        ),
        ReportInput(  # 자본잠식(자본총계 음수) — 연도가 제외되지 않고 D로 나와야 한다
            result=_result_stub(2, "㈜자본잠식", "주소2"),
            snapshots=[
                _snapshot_stub("2024", total_liab=60, total_equity=-40, total_assets=20),
                _snapshot_stub("2025", total_liab=70, total_equity=-50, total_assets=20),
            ],
        ),
    ]
    outcome = generate_reports(items, today=date(2026, 8, 3))
    assert [f.result_id for f in outcome.files] == [1, 2]

    # 음수 매출액 연도는 아예 실리지 않는다.
    html = (outcome.output_dir / "㈜매출음수.html").read_text(encoding="utf-8")
    payload = _extract_embedded_json(html)
    assert [row["year"] for row in payload["financials"]] == [2020]

    rendered = _render_reports_with_node(outcome.output_dir, tmp_path)

    negative = rendered["㈜매출음수.html"]
    # 영업이익률 -400/4000 = -10% → 수익성 D. 수정 전에는 2021년(-1706/-5409=+31.5%)이
    # 마지막 연도로 남아 A가 찍혔다.
    assert negative["grades"]["수익성"] == "D"
    assert "-5,409" not in negative["kpi"]  # KPI에도 음수 매출액이 남지 않는다

    # --- H4 회귀 확인: 자본잠식 회사는 여전히 "생성되고" 등급만 D다 ---
    impaired_payload = _extract_embedded_json(
        (outcome.output_dir / "㈜자본잠식.html").read_text(encoding="utf-8")
    )
    assert [row["year"] for row in impaired_payload["financials"]] == [2024, 2025]
    assert impaired_payload["financials"][-1]["자본총계"] == -50
    assert rendered["㈜자본잠식.html"]["grades"]["재무안정성"] == "D"


# ---------------------------------------------------------------------------
# [M2] 라벨 엑셀 — 제어문자 방어
# ---------------------------------------------------------------------------


def test_excel_safe_text_strips_control_characters():
    assert excel_safe_text("㈜제어\x01문자\x1f") == "㈜제어문자"
    assert excel_safe_text(None) == ""
    assert excel_safe_text(" 여백 ") == "여백"
    assert excel_safe_text("줄\n바꿈") == "줄\n바꿈"  # 개행은 엑셀이 허용한다


def test_generate_reports_survives_control_characters_in_company_name(report_settings):
    items = [
        ReportInput(
            result=_result_stub(1, "㈜제어\x01문자", "경남 김해시\x07 1"),
            snapshots=[_snapshot_stub("2024")],
        )
    ]
    outcome = generate_reports(items, today=date(2026, 8, 3))

    wb = openpyxl.load_workbook(outcome.output_dir / LABEL_FILENAME)
    assert list(wb.active.iter_rows(values_only=True)) == [
        ("회사명", "주소"),
        ("㈜제어문자", "경남 김해시 1"),
    ]


def test_write_label_workbook_wraps_openpyxl_errors(tmp_path, monkeypatch):
    """openpyxl의 IllegalCharacterError/ValueError도 507용 예외로 통일한다."""
    from openpyxl.utils.exceptions import IllegalCharacterError

    class _BoomSheet:
        title = ""

        def append(self, *args, **kwargs):
            raise IllegalCharacterError("bad cell")

    class _BoomWorkbook:
        active = _BoomSheet()

    monkeypatch.setattr(audit_proposal, "Workbook", lambda: _BoomWorkbook())
    with pytest.raises(ReportGenerationError, match="발송처 목록 엑셀"):
        write_label_workbook([], tmp_path / "x.xlsx")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def client_with_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app_main.app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app_main.app), factory
    app_main.app.dependency_overrides.clear()


def _seed(factory) -> tuple[int, int, int]:
    """Job 1개 + 결과 2건(재무이력 있음/없음)을 넣고 (job_id, id_있음, id_없음)을 반환."""
    db = factory()
    try:
        job = Job(
            created_at="2026-08-03T00:00:00",
            name="보고서 테스트 Job",
            cond_region="{}",
            cond_revenue="{}",
            cond_industry="[]",
            cond_period="{}",
            status=JobStatus.DONE,
            current_step=6,
            progress_done=2,
            progress_total=2,
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        with_history = Result(
            job_id=job.id,
            corp_code="00100001",
            rcept_no="20260601000001",
            corp_name="(주)이력있음",
            address="경상남도 김해시 삼계로 1",
            ceo_name="홍길동",
            induty_code="25",
            induty_name="금속가공제품 제조업",
            fiscal_date="20251231",
            audit_opinion="적정",
            auditor_name="안경회계법인",
            parse_status=ParseStatus.OK,
        )
        without_history = Result(
            job_id=job.id,
            corp_code="00100002",
            rcept_no=None,
            corp_name="(주)이력없음",
            address="경상남도 김해시 분성로 2",
            ceo_name="김철수",
            induty_code="25",
            induty_name="금속가공제품 제조업",
            fiscal_date="20251231",
            parse_status=ParseStatus.FAILED,
        )
        db.add_all([with_history, without_history])
        db.commit()
        db.refresh(with_history)
        db.refresh(without_history)

        db.add_all(
            [
                FinancialSnapshot(
                    result_id=with_history.id,
                    rcept_no="20260601000001",
                    fiscal_year=str(year),
                    current_assets=10,
                    noncurrent_assets=10,
                    total_assets=20,
                    current_liab=5,
                    noncurrent_liab=5,
                    total_liab=10,
                    total_equity=10,
                    revenue=100 + year,
                    cogs=60,
                    gross_profit=40,
                    sga=20,
                    operating_income=20,
                    net_income=10,
                    parse_status=ParseStatus.OK,
                    from_current_period=1,
                )
                for year in (2023, 2024)
            ]
        )
        db.commit()
        return job.id, with_history.id, without_history.id
    finally:
        db.close()


def test_generate_report_endpoint_creates_folder_and_files(
    client_with_db, report_settings, tmp_path
):
    client, factory = client_with_db
    job_id, id_with, id_without = _seed(factory)

    resp = client.post(
        f"/api/jobs/{job_id}/generate-report", json={"ids": [id_without, id_with]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    output_dir = Path(body["output_dir"])
    assert output_dir.parent == tmp_path / "report"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}(_\d+)?", output_dir.name)
    # 재무이력이 없는 회사는 생성되지 않는다(선택 2건 중 1건).
    assert body["generated_count"] == 1
    assert body["label_file"] == LABEL_FILENAME
    assert (output_dir / LABEL_FILENAME).is_file()

    # id 오름차순으로 생성된다(선택 다운로드와 동일한 정렬 계약).
    assert [f["result_id"] for f in body["files"]] == [id_with]
    for entry in body["files"]:
        assert (output_dir / entry["filename"]).is_file()

    # 재무이력이 있는 회사는 실제 연도 데이터가 실린다.
    target = next(f for f in body["files"] if f["result_id"] == id_with)
    data = _extract_embedded_json(
        (output_dir / target["filename"]).read_text(encoding="utf-8")
    )
    assert [row["year"] for row in data["financials"]] == [2023, 2024]
    assert data["financials"][0]["매출액"] == 2123

    # 이력 없는 회사는 skipped + 경고로 알리고 요청 자체는 성공한다.
    assert [s["result_id"] for s in body["skipped"]] == [id_without]
    company_warnings = [w for w in body["warnings"] if w["result_id"] is not None]
    assert [w["result_id"] for w in company_warnings] == [id_without]
    assert "보고서를 생성하지 않았습니다" in company_warnings[0]["message"]


def test_generate_report_endpoint_rejects_foreign_and_invalid_ids(
    client_with_db, report_settings
):
    client, factory = client_with_db
    job_id, id_with, _ = _seed(factory)

    foreign = client.post(
        f"/api/jobs/{job_id}/generate-report", json={"ids": [id_with, 999_999]}
    )
    assert foreign.status_code == 400
    assert "999999" in foreign.json()["detail"]

    overflow = client.post(f"/api/jobs/{job_id}/generate-report", json={"ids": [2**63]})
    assert overflow.status_code == 400

    empty = client.post(f"/api/jobs/{job_id}/generate-report", json={"ids": []})
    assert empty.status_code == 422  # pydantic min_length=1

    missing_job = client.post("/api/jobs/999999/generate-report", json={"ids": [id_with]})
    assert missing_job.status_code == 404


def test_generate_report_endpoint_returns_friendly_error_on_io_failure(
    client_with_db, report_settings, monkeypatch
):
    """파일 저장 실패는 500 스택트레이스가 아니라 읽을 수 있는 메시지로 응답한다."""
    client, factory = client_with_db
    job_id, id_with, _ = _seed(factory)

    def _boom(*args, **kwargs):
        raise ReportGenerationError("보고서 폴더를 만들 수 없습니다: 디스크 공간 부족")

    monkeypatch.setattr(audit_proposal, "allocate_output_dir", _boom)

    resp = client.post(f"/api/jobs/{job_id}/generate-report", json={"ids": [id_with]})
    assert resp.status_code == 507
    assert "보고서 폴더를 만들 수 없습니다" in resp.json()["detail"]
