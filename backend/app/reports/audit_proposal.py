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

이 "템플릿 무수정" 관행에는 **예외가 3건 있다**(전부 2026-08-03, dart-qa 리뷰,
사용자 승인). 셋 다 뿌리가 같다 — **완전자본잠식(자본총계 < 0)이면 부채비율이 음수**가
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

회귀 테스트는 전부 `tests/test_reports.py`에 있다(템플릿 원문 파싱 + Node `vm` 실제
렌더 검증 2중 구조).

데이터 소스:
  - `company`  : `results` 1행(회사명/업종명/주소/대표자/결산기준일/감사의견/감사인)
  - `financials`: `financial_snapshots`(회사×회계연도) — 호출부가 기존 재무이력
                  조회(`app/api/results.py::get_result_history`)를 재사용해 넘긴다.
                  여기서는 DB 쿼리를 하지 않는다.
  - `firm`     : `app/reports/firm_profile.py`의 `FIRM_PROFILE` 상수
  - `peers`/`industryAverage`/`regionGroup`/`opinionSummary`: **현재 데이터 소스가
    없어 빈 값**(`[]`/`null`/`[]`/`""`)으로 채운다. 나중에 소스가 생기면 이
    모듈의 `build_report_payload()`만 고치면 된다.

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
from collections.abc import Sequence
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
# 템플릿이 쓰는 14개 키(year + 13항목)만 싣는다 — 현금흐름표/영업외손익은 템플릿에
# 대응 항목이 없어 제외한다. 라벨은 템플릿의 계산식(`calcRatios`)이 참조하는 이름이라
# 임의로 바꾸면 안 된다("판관비"는 엑셀 내보내기의 "판매비와관리비"와 표기가 다른데,
# 이는 템플릿 쪽 이름을 따른 것이다).
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
}

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
    item: ReportInput, selection: FinancialSelection | None = None
) -> dict[str, Any]:
    """회사 1건의 `EMBEDDED_DATA` 전체를 만든다.

    `financials`에는 `select_financial_rows()`가 고른 **완전한 연도만** 실린다
    (모듈 docstring 참고). `selection`을 넘기지 않으면 여기서 계산한다.

    `peers`/`industryAverage`/`regionGroup`/`opinionSummary`는 현재 데이터 소스가
    없어 빈 값으로 둔다 — 템플릿이 빈 값을 "데이터 없음"으로 안전하게 렌더한다.
    """
    chosen = selection if selection is not None else select_financial_rows(item.snapshots)
    return {
        "firm": dict(FIRM_PROFILE),
        "company": build_company_payload(item.result),
        "financials": chosen.rows,
        "peers": [],
        "industryAverage": None,
        "regionGroup": [],
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
) -> ReportGenerationOutcome:
    """선택된 회사들의 보고서 HTML + 발송처 엑셀을 새 폴더에 생성한다.

    - `base_dir`: 기본값은 `Settings.report_output_dir`(= `BACKEND_DIR/report`).
    - `template_text`: 기본값은 템플릿 파일에서 읽은 원문(테스트용 주입 지점).
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

        payload = build_report_payload(item, selection)
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
