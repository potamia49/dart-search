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

2026-08-06(§4-15): 선택 다운로드 ① 시트에 **원문 그대로의 전체 세부계정**을 함께
싣는다. 기존 요약 24항목 행은 그대로 두고, 각 요약 항목 **바로 아래**에 그 대분류에
속한 원문 계정과목 행을 이어 붙인다(`SelectionAccountDetail`). 세부계정 데이터는
이 모듈이 직접 만들지 않고 **호출부가 넘긴다** — 원문 캐시 경로/파싱은 API 계층
(`app/api/results.py::_load_selection_account_details()`)의 일이고 이 모듈은 순수
직렬화만 담당한다는 기존 구조(`corp_name_by_result_id`를 넘겨받는 방식)를 따른다.

2026-08-06 후속(§4-15, dart-qa Medium-2): xlsx 직렬화를 `DataFrame.to_excel`에서
**openpyxl write-only 스트리밍**(`_write_xlsx()`)으로 바꿔 피크 메모리를 줄였다.
셀 값·헤더 서식은 pandas가 쓰던 것과 동일하며(드리프트 가드 테스트가 두 방식의
출력을 직접 대조한다), csv 경로와 DataFrame 생성 로직은 무변경이다.

2026-08-06 후속(dart-qa Low): 그 write-only 전환이 `%TEMP%` 임시파일 누수를 새로
만들었던 것을 고쳤다 — 이제 **정상 종료든 예외든 임시파일이 반드시 정리된다**
(`_discard_write_only_temp_files()`). 함께 `_xlsx_cell_value()`가 모든 문자열 셀의
제어문자를 제거하도록 해, 기본정보 컬럼(회사명/주소/감사인명)에 제어문자가 섞였을 때
다운로드 전체가 실패하던 **선행 이슈**(pandas 경로에도 있던 문제)도 막는다.
"""

from __future__ import annotations

import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Border, Font, Side

from app.models.financial_snapshot import FinancialSnapshot
from app.models.result import Result
from app.parsers.account_detail import AccountRow

# ---------------------------------------------------------------------------
# xlsx 직렬화 (2026-08-06, dart-qa Medium-2) — openpyxl write-only 스트리밍
# ---------------------------------------------------------------------------
#
# 기존에는 `DataFrame.to_excel(engine="openpyxl")`을 그대로 썼는데, 그 방식은
# **모든 셀을 `Cell` 객체로 메모리에 쌓아 둔 뒤** 마지막에 한 번에 직렬화한다.
# §4-15로 선택 다운로드 ① 시트의 행 수가 105,192행 → 380,992행(개발 DB Job 27
# 전량)으로 늘면서 피크 메모리가 946MB → 3,349MB로 뛰었고, 배포 대상이 브라우저·
# 엑셀이 함께 떠 있는 사무실 PC(exe)라 실사용에서 감당하기 어려운 수치였다.
#
# write-only 모드는 `ws.append()` 때마다 행을 임시 파일로 흘려보내 셀 객체를 남기지
# 않는다. **출력물은 pandas 방식과 동일하다** — 셀 값/타입, 헤더 서식(굵게 + 얇은
# 테두리 + 가운데·위 정렬), 시트 이름·순서가 모두 같고, 다음 두 가지만 다르다:
#   ① 빈 값 셀을 pandas는 빈 inlineStr(`<c t="inlineStr"></c>`)로 쓰고 write-only는
#      아예 쓰지 않는다 — 엑셀에서도 openpyxl 재읽기에서도 똑같이 빈 칸(None)이다.
#   ② `<dimension>`(사용 범위 힌트)이 빠진다 — 엑셀이 열 때 스스로 계산하는 값이고
#      openpyxl 재읽기의 `max_row`/`max_column`도 동일하다.
# 이 동등성은 `tests/test_exporters.py`의 대조 테스트가 pandas 출력과 직접 비교해
# 잠근다(pandas가 기본 헤더 서식을 바꾸면 그 테스트가 먼저 깨진다).

# 엑셀 워크시트 1장의 물리적 최대 행 수(헤더 포함). pandas는 이 상한을 넘으면
# `ValueError: This sheet is too large!`를 던졌는데, write-only 모드에는 그 검사가
# 없어 **손상된 파일이 조용히 만들어진다** — 그래서 같은 성격의 검사를 여기서 한다.
# API 계층(`export_job_results`)은 직렬화에 들어가기 전에 예상 행 수를 먼저 세어
# 400으로 막는다(사용자에게 보이는 안내는 그쪽이고, 여기는 최후의 방어선이다).
XLSX_MAX_ROWS = 1_048_576

# pandas `ExcelFormatter.header_style`과 같은 헤더 서식(굵게 + 얇은 테두리 4면 +
# 가로 가운데/세로 위 정렬). 값을 바꾸면 기존 파일과 서식이 달라진다.
_XLSX_HEADER_FONT = Font(bold=True)
_XLSX_HEADER_SIDE = Side(style="thin")
_XLSX_HEADER_BORDER = Border(
    left=_XLSX_HEADER_SIDE,
    right=_XLSX_HEADER_SIDE,
    top=_XLSX_HEADER_SIDE,
    bottom=_XLSX_HEADER_SIDE,
)
_XLSX_HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="top")

# openpyxl이 셀 값으로 거부하는 제어문자(`openpyxl.cell.cell.ILLEGAL_CHARACTERS_RE`와
# 동일 집합. 줄바꿈 `\n`/`\r`/탭은 허용이라 제외 — 원문 라벨에 셀 안 개행이 실제로
# 흔하다). `app/reports/audit_proposal.py::_excel_safe()`와 같은 방어다.
_EXCEL_ILLEGAL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _xlsx_cell_value(value: object) -> object:
    """DataFrame 셀 값을 openpyxl이 쓸 수 있는 값으로 변환(pandas와 같은 규칙).

    pandas `ExcelFormatter`는 결측(NaN/NaT)을 빈 값으로, 무한대를 `inf`/`-inf`
    문자열로 바꿔서 넘긴다 — 그 두 가지를 재현한다. NaN을 그대로 넘기면 엑셀이
    읽지 못하는 `<v>nan</v>` 셀이 만들어진다.

    거기에 더해 **모든 문자열 셀에서 제어문자를 제거한다**(2026-08-06, dart-qa Low).
    이건 pandas 동작과 다른 유일한 지점이자 의도된 보정이다 — pandas 경로도 제어문자가
    섞이면 `IllegalCharacterError`로 **다운로드 전체가 500**이 됐고(선행 이슈, 실측
    재현), 기본정보 컬럼(회사명/주소/감사인명 등 DART 원문 유래 값)에는 방어가 없었다.
    세부계정 라벨만 막던 `_detail_label()`과 같은 규칙을 여기서 전 컬럼에 적용해
    xlsx 3경로(wide/선택 long/재무이력)를 한 곳에서 덮는다. 값 자체가 바뀌는 것은
    제어문자가 실제로 있을 때뿐이다(개발 DB `results` 5,204행 전 문자열 컬럼 실측 0건).
    """
    if value is None:
        return None
    if isinstance(value, str):
        return _EXCEL_ILLEGAL_CHARS.sub("", value)
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        if value == float("inf"):
            return "inf"
        if value == float("-inf"):
            return "-inf"
    elif value is pd.NaT:
        return None
    return value


def _discard_write_only_temp_files(workbook: Workbook) -> None:
    """write-only 워크북이 `%TEMP%`에 스풀링해 둔 시트 임시파일을 지운다.

    write-only 모드는 `ws.append()` 때마다 행을 `%TEMP%`의 임시파일(`openpyxl.*`)로
    흘려보내고, **`Workbook.save()`가** 그 파일을 zip에 넣은 뒤
    `WorksheetWriter.cleanup()`으로 지운다 — 즉 `save()`에 도달하지 못하면 임시파일이
    그대로 남는다(dart-qa 2026-08-06 Low, 제어문자 값 → `IllegalCharacterError`로
    실제 재현). 개발 DB Job 27 전량의 시트 XML이 압축 전 약 604MB라 누수 1건이 그
    크기이고, 사무실 exe는 서버가 며칠씩 떠 있어 재시도할 때마다 누적된다.

    **`Workbook.close()`로는 정리되지 않는다**(openpyxl 3.1.5 실측). docstring에는
    write-only에도 영향이 있다고 적혀 있지만 구현은 read-only 전용 `_archive`만 닫는다.
    openpyxl이 프로세스 종료 시 도는 `atexit` 정리(`ALL_TEMP_FILES`)도 Windows에서는
    파일 핸들이 열린 채라 `PermissionError`로 실패한다 — 그래서 **핸들을 먼저 닫고
    (`writer.close()`) 지운다(`writer.cleanup()`)**. `close()` 자체는 향후 openpyxl이
    여기서 정리하게 될 때를 위해 함께 호출해 둔다(지금은 무해한 no-op).

    정상 종료 뒤에 다시 불러도 안전하다(이미 지워진 파일은 `OSError`로 걸러진다).
    정리 과정의 실패가 원래 예외를 덮으면 안 되므로 예외는 전부 삼킨다.
    """
    workbook.close()
    for worksheet in workbook.worksheets:
        writer = getattr(worksheet, "_writer", None)
        if writer is None:  # 행을 한 줄도 안 쓴 시트 — 임시파일 자체가 없다
            continue
        try:
            writer.close()  # 열린 파일 핸들 해제(Windows는 열린 파일을 지울 수 없다)
        except Exception:  # noqa: BLE001 - best-effort 정리
            pass
        try:
            writer.cleanup()
        except Exception:  # noqa: BLE001 - 이미 지워졌거나(정상 경로) 지울 수 없는 경우
            pass


def _write_xlsx(sheets: Sequence[tuple[str, pd.DataFrame]]) -> bytes:
    """DataFrame 목록을 시트별로 담은 xlsx 바이트를 **행 단위 스트리밍**으로 만든다.

    `sheets`는 (시트명, DataFrame) 순서쌍이고 컬럼 헤더는 DataFrame의 컬럼명을
    그대로 쓴다(인덱스는 싣지 않는다 — 기존 `to_excel(index=False)`와 동일).

    쓰기 도중 예외가 나도 `%TEMP%`에 스풀링된 시트 임시파일을 남기지 않는다
    (`_discard_write_only_temp_files()` 참고).
    """
    for name, df in sheets:
        if len(df) + 1 > XLSX_MAX_ROWS:
            raise ValueError(
                f"엑셀 시트 최대 행 수({XLSX_MAX_ROWS:,}행)를 초과했습니다: "
                f"{name} 시트 {len(df) + 1:,}행"
            )

    workbook = Workbook(write_only=True)
    try:
        for name, df in sheets:
            worksheet = workbook.create_sheet(title=name)
            header = []
            for column in df.columns:
                cell = WriteOnlyCell(worksheet, value=column)
                cell.font = _XLSX_HEADER_FONT
                cell.border = _XLSX_HEADER_BORDER
                cell.alignment = _XLSX_HEADER_ALIGNMENT
                header.append(cell)
            worksheet.append(header)
            for row in df.itertuples(index=False, name=None):
                worksheet.append([_xlsx_cell_value(value) for value in row])

        buffer = io.BytesIO()
        workbook.save(buffer)
    finally:
        # 정상 경로에서는 save()가 이미 지운 뒤라 no-op이고, 중간에 예외가 나면
        # 여기서만 임시파일이 정리된다.
        _discard_write_only_temp_files(workbook)
    return buffer.getvalue()


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

    if fmt == "xlsx":
        return _write_xlsx([("results", df)])
    return df.to_csv(index=False).encode("utf-8-sig")


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

# --- 원문 세부계정(§4-15, 2026-08-06) -------------------------------------
#
# 요약 24항목만으로는 "유동자산 안에 무엇이 얼마나 있는지"를 알 수 없어, 감사보고서
# 원문에서 뽑은 계정과목 전체를 같은 시트에 이어 싣는다. 라벨은 **원문 그대로**이며
# 표준화하지 않는다(회사마다 다른 것이 정상 — 화면의 "세부계정 펼치기"와 동일한 값).
#
# 행 순서는 "요약 항목 → 그 대분류에 속한 원문 세부계정" 반복이다. 어느 대분류에
# 속하는지는 `상위계정`(요약 라벨) 컬럼이, 원문 계층 깊이는 `계정단계`(요약=0,
# 세부=원문 상대 레벨)가 담는다. `구분`(요약/세부)을 별도 컬럼으로 둔 것은 엑셀에서
# 한 번에 걸러 보기 위함이며, 세부계정 라벨이 요약 라벨과 같은 글자일 수 있어
# (예: 원문 "현금및현금성자산" vs 요약 "현금및현금성자산(순액)") 구분이 필요하다.
SELECTION_ROW_KIND_SUMMARY = "요약"
SELECTION_ROW_KIND_DETAIL = "세부"


@dataclass
class SelectionAccountDetail:
    """회사 1건의 원문 세부계정(선택 다운로드 ① 시트 전용 입력).

    `accounts`는 요약 필드명(`_cur` 접미어 **없음** — `current_assets` 등) → 그
    대분류에 속한 원문 계정 행 목록이며, `app/parsers/account_detail.py`의
    `AccountDetail.accounts`를 그대로 넘겨받는 형태다(새 파서를 만들지 않는다).

    `notice`는 세부계정을 싣지 못한 사유(원문 없음/캐시 없음/PDF/파싱 실패 등)이며
    그 회사의 **모든 행**에 "세부계정비고"로 반복된다. 값이 없는 계정과목의 행을
    남기는 것과 같은 취지다 — 세부계정 행을 그냥 빼 버리면 "원문에 세부계정이 없는
    회사"와 "원문을 못 읽은 회사"가 파일에서 구분되지 않는다.
    """

    accounts: Mapping[str, Sequence[AccountRow]] = field(default_factory=dict)
    notice: str | None = None


def _selection_statement_of(account_col: str) -> str:
    """선택 다운로드 ① 시트의 계정과목이 속한 재무제표명.

    ② `financial_history` 시트와 **같은 단일 정의**(`FINANCIAL_SNAPSHOT_ACCOUNTS_BY_
    STATEMENT`)를 그대로 쓴다 — 두 시트가 같은 계정을 다른 재무제표로 분류하면
    대조가 불가능해진다. `_cur` 접미어를 뗀 필드명이 그 dict의 키와 1:1이라는 것은
    `tests/test_exporters.py`의 드리프트 가드 테스트가 잠근다.
    """
    return FINANCIAL_SNAPSHOT_STATEMENT_BY_ACCOUNT[account_col.removesuffix("_cur")]


def _detail_label(label: str) -> str:
    """원문 세부계정 라벨을 엑셀에 쓸 수 있게 최소한만 다듬는다(표준화하지 않는다).

    라벨은 회사마다 다른 원문 표기 그대로 싣는 것이 원칙이라(각주 번호·번호 접두어·
    셀 안 개행 포함) 내용은 건드리지 않고, xlsx 저장 자체를 실패시키는 제어문자만
    제거한다 — 380,992행 중 한 라벨 때문에 다운로드 전체가 500이 되면 안 된다
    (로컬 캐시 600건 표본에서는 실제 검출 0건이지만 방어는 남긴다).

    2026-08-06부터 `_xlsx_cell_value()`가 **모든 xlsx 셀**에 같은 규칙을 적용하므로
    xlsx만 보면 중복이지만, 이 함수는 **csv 경로에도 걸린다**(그쪽은 `_write_xlsx`를
    거치지 않는다) — 라벨의 제어문자는 포맷과 무관하게 떼는 것이 맞아 그대로 둔다.
    """
    return _EXCEL_ILLEGAL_CHARS.sub("", label)


def _as_export_amount(value: float | int | None) -> object | None:
    """원문 파싱값(float)을 정수로 되돌린다 — csv에 `1234.0`이 찍히는 것을 막는다.

    `results` 컬럼은 Integer라 DB 유래 금액은 이미 int지만, 세부계정은 원문에서
    막 파싱한 `float`(`parse_won_amount`)이다. 원 단위 금액이라 소수부가 있는 값은
    사실상 없지만, 있으면 그대로 둔다(임의로 반올림해 값을 바꾸지 않는다).
    """
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


# DB 필드명(가상 필드 `statement_name`/`row_kind`/`parent_account`/`account_name`/
# `account_level`/`amount`/`detail_notice` 포함) -> 한국어 헤더. dict 순서가 곧
# 파일의 컬럼 순서다 — `SELECTION_TRAILING_COLUMNS`(파싱상태)만 계정과목명/금액 뒤로
# 밀려나고, 그보다도 뒤에 "세부계정비고"가 온다(둘 다 회사 단위 상태 컬럼이라 나란히
# 둔다). "재무제표명"은 ② `financial_history` 시트의 같은 이름 컬럼과 값 체계가
# 동일하다.
SELECTION_EXPORT_COLUMN_LABELS: dict[str, str] = {
    **{
        col: RESULT_COLUMN_LABELS[col]
        for col in SELECTION_BASE_COLUMNS
        if col not in SELECTION_TRAILING_COLUMNS
    },
    "statement_name": "재무제표명",
    "row_kind": "구분",
    "parent_account": "상위계정",
    "account_name": "계정과목명",
    "account_level": "계정단계",
    "amount": "금액",
    **{col: RESULT_COLUMN_LABELS[col] for col in SELECTION_TRAILING_COLUMNS},
    "detail_notice": "세부계정비고",
}

SELECTION_EXPORT_COLUMNS: list[str] = list(SELECTION_EXPORT_COLUMN_LABELS.keys())


def _append_selection_row(
    data: dict[str, list[object | None]],
    base: Mapping[str, object | None],
    *,
    statement: str,
    row_kind: str,
    parent: str | None,
    account_name: str,
    level: int,
    amount: object | None,
    notice: str | None,
) -> None:
    """선택 다운로드 ① 시트 1행을 **컬럼별 리스트**에 덧붙인다.

    행 dict를 쌓아 `pd.DataFrame(rows)`에 넘기지 않는 이유는 메모리다 — 세부계정을
    싣기 시작하면서 최대 규모(개발 DB Job 27, 결과 4,383건)의 행 수가 105,192행에서
    **380,992행**으로 늘었는데, 23키짜리 dict 하나가 832바이트라 그 방식이면 행 dict
    만으로 약 302MB다(컬럼별 리스트는 포인터 배열이라 같은 조건에서 약 64MB).
    ② 시트(`snapshots_to_dataframe`)는 행 수가 그대로라 기존 dict 방식을 유지한다.
    """
    for col in SELECTION_BASE_COLUMNS:
        data[col].append(base[col])
    data["statement_name"].append(statement)
    data["row_kind"].append(row_kind)
    data["parent_account"].append(parent)
    data["account_name"].append(account_name)
    data["account_level"].append(level)
    data["amount"].append(amount)
    data["detail_notice"].append(notice)


def results_to_selection_dataframe(
    results: Sequence[Result],
    detail_by_result_id: Mapping[int, SelectionAccountDetail] | None = None,
) -> pd.DataFrame:
    """`results` 레코드를 "회사 × 계정과목" long 포맷 DataFrame으로 변환.

    회사 1건은 요약 계정과목(`SELECTION_ACCOUNT_COLUMNS`, 24개) 행 + 각 요약 항목
    **바로 아래**에 이어지는 원문 세부계정 행으로 풀리고, 기본정보 컬럼
    (`SELECTION_BASE_COLUMNS`)은 그 행들에 그대로 반복된다. 입력 순서(호출부의 id
    오름차순)를 보존한다. 컬럼 순서는 `SELECTION_EXPORT_COLUMNS`가 정한다 —
    `parse_status`/`detail_notice`는 계정과목명/금액 뒤(맨 마지막)로 간다.

    `detail_by_result_id`가 없으면(또는 그 회사 항목이 없으면) 요약 24행만 나오고
    세부계정 컬럼은 비어 있다 — 2026-08-06 이전 동작과 행 구성이 같다.

    세부계정은 **당기(`AccountRow.cur`)만** 싣는다 — 요약 항목이 `_cur`만 싣는 기존
    방침("전기 항목은 전년도 당기 항목")과 같다. 어느 시점의 값인지는 각 행에 이미
    반복되는 기본정보의 `결산기준일`/`접수번호`가 말해 준다(세부계정은 그 접수번호
    원문의 당기 열에서 뽑은 값이라 요약 항목과 같은 회계기간이다).

    값이 없는(`None`) 계정과목도 **행은 그대로 만들고 금액만 비운다** — 어떤
    계정과목이 결측인지 파일에서 알 수 있어야 하므로 스킵하지 않는다. 이를 위해
    "금액" 컬럼은 object dtype으로 고정한다(그대로 두면 pandas가 float64로 올려
    `10000000000.0`처럼 소수점이 붙고 결측이 `NaN`으로 출력된다).
    """
    data: dict[str, list[object | None]] = {col: [] for col in SELECTION_EXPORT_COLUMNS}
    details = detail_by_result_id or {}
    for result in results:
        base = {col: getattr(result, col, None) for col in SELECTION_BASE_COLUMNS}
        detail = details.get(getattr(result, "id", None))  # type: ignore[arg-type]
        notice = detail.notice if detail is not None else None
        children_by_field: Mapping[str, Sequence[AccountRow]] = (
            detail.accounts if detail is not None else {}
        )
        for col in SELECTION_ACCOUNT_COLUMNS:
            statement = _selection_statement_of(col)
            label = SELECTION_ACCOUNT_LABELS[col]
            _append_selection_row(
                data,
                base,
                statement=statement,
                row_kind=SELECTION_ROW_KIND_SUMMARY,
                parent=None,
                account_name=label,
                level=0,
                amount=getattr(result, col, None),
                notice=notice,
            )
            for child in children_by_field.get(col.removesuffix("_cur"), ()):
                _append_selection_row(
                    data,
                    base,
                    statement=statement,
                    row_kind=SELECTION_ROW_KIND_DETAIL,
                    parent=label,
                    account_name=_detail_label(child.label),
                    level=child.level,
                    amount=_as_export_amount(child.cur),
                    notice=notice,
                )

    df = pd.DataFrame(data, columns=SELECTION_EXPORT_COLUMNS)
    df["amount"] = pd.Series(data["amount"], dtype=object, index=df.index)
    return df


def export_selection_results(
    results: Sequence[Result],
    fmt: Literal["xlsx", "csv"],
    detail_by_result_id: Mapping[int, SelectionAccountDetail] | None = None,
) -> bytes:
    """선택 다운로드용 기본정보 파일(long 포맷)을 xlsx/csv 바이트로 직렬화.

    `export_results()`와 형식(시트명 `results`, csv의 UTF-8 BOM)은 같고 컬럼
    구성만 다르다 — 전체 내보내기는 계속 `export_results()`를 쓴다.
    `detail_by_result_id`는 원문 세부계정으로, xlsx/csv 모두 동일하게 반영된다.
    """
    if fmt not in ("xlsx", "csv"):
        raise ValueError(f"지원하지 않는 형식입니다: {fmt}")

    df = results_to_selection_dataframe(results, detail_by_result_id).rename(
        columns=SELECTION_EXPORT_COLUMN_LABELS
    )

    if fmt == "xlsx":
        return _write_xlsx([("results", df)])
    return df.to_csv(index=False).encode("utf-8-sig")


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
    detail_by_result_id: Mapping[int, SelectionAccountDetail] | None = None,
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

    `detail_by_result_id`(원문 세부계정, §4-15)는 시트 ①이 long 포맷일 때만 쓰인다 —
    wide 포맷에는 계정과목 행 자체가 없다. 시트 ②(연도별 스냅샷)는 **무변경**이다:
    세부계정을 연도마다 실으려면 회사×연도 수만큼 원문을 다시 파싱해야 해 비용이
    수 배로 뛰고, 이 기능의 요구("선택 다운로드에 원문 전체 계정과목")는 당기 기준
    ① 시트로 충족된다.
    """
    results_df = (
        results_to_selection_dataframe(results, detail_by_result_id).rename(
            columns=SELECTION_EXPORT_COLUMN_LABELS
        )
        if use_selection_format
        else results_to_dataframe(results).rename(columns=RESULT_COLUMN_LABELS)
    )
    history_df = snapshots_to_dataframe(snapshots, corp_name_by_result_id).rename(
        columns=FINANCIAL_SNAPSHOT_COLUMN_LABELS
    )

    return _write_xlsx([("results", results_df), ("financial_history", history_df)])
