"""pdfplumber 기반 재무제표 파싱 (2순위, XML 미제공 시).

상세개발계획.md §4-4. M3에서 실제로 다운로드한 원문 30건(2012~2026년,
backend/tests/fixtures/manifest.json)은 **전부 XML**이었다 — 최근 분기는
물론 2012년 초 원문까지도 DART document.xml API가 XML로 반환했다(CLAUDE.md
"M3 실측 메모" 참고). 따라서 이 모듈은 실제 PDF 샘플로 검증하지 못한
best-effort 구현이다: STEP4에서 확장자가 PDF로 판별된 원문이 실제로
나타나면 검수(M5)에서 실측 후 보강해야 한다.

pdfplumber로 추출한 표 셀 텍스트를 xml_parser.py와 동일한
`ACCOUNT_NAME_ALIASES`/`parse_won_amount` 규칙에 태워 재사용한다 — 표
레이아웃만 다를 뿐 계정과목 표기/금액 표기 규칙은 XML 원문과 동일할
것이라는 전제(같은 DART 접수 데이터 소스)에 근거한다.
"""

from __future__ import annotations

import io
import logging

import pdfplumber

from app.parsers.base import (
    ACCOUNT_NAME_ALIASES,
    ParsedFinancials,
    compute_gross_margin,
    determine_parse_status,
    normalize_account_label,
    parse_won_amount,
)

logger = logging.getLogger(__name__)


def _extract_row(row: list[str | None]) -> tuple[str, list[str]] | None:
    cells = [c or "" for c in row]
    label = cells[0].strip()
    if not label:
        return None
    return label, [c.strip() for c in cells[1:]]


def parse_pdf_financials(raw_pdf: bytes) -> ParsedFinancials:
    """감사보고서 원문 PDF에서 재무상태표/손익계산서 표를 파싱.

    각 표의 컬럼 수가 페이지마다 다를 수 있어 XML처럼 당기/전기 그룹 폭을
    THEAD COLSPAN으로 판별할 수 없다 — 대신 "값 셀 중 파싱 가능한 첫 번째를
    당기, 그다음을 전기"로 단순화한다(표 헤더에 당기가 항상 먼저 오는 DART
    관행 전제). 실제 PDF 샘플 확보 전까지는 정확도를 보장할 수 없다.
    """
    try:
        values_cur: dict[str, float | None] = {}
        values_prv: dict[str, float | None] = {}
        found_any_table = False

        with pdfplumber.open(io.BytesIO(raw_pdf)) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    for row in table:
                        parsed = _extract_row(row)
                        if parsed is None:
                            continue
                        raw_label, value_cells = parsed
                        norm_label = normalize_account_label(raw_label)
                        field = ACCOUNT_NAME_ALIASES.get(norm_label)
                        if field is None or field in values_cur:
                            continue
                        amounts = [parse_won_amount(c) for c in value_cells]
                        amounts = [a for a in amounts if a is not None]
                        if not amounts:
                            continue
                        found_any_table = True
                        cur_val = amounts[0]
                        prv_val = amounts[1] if len(amounts) > 1 else None
                        if "손실" in raw_label:
                            cur_val = -abs(cur_val) if cur_val is not None else None
                            prv_val = -abs(prv_val) if prv_val is not None else None
                        values_cur[field] = cur_val
                        values_prv[field] = prv_val
    except Exception as exc:  # noqa: BLE001 - pdfplumber는 손상 PDF에서 다양한 예외를 던짐
        logger.warning("PDF 파싱 실패: %s", exc)
        return ParsedFinancials(parse_status="FAILED", parse_note=f"PDF 파싱 오류: {exc}")

    values_cur["gross_margin"] = compute_gross_margin(values_cur.get("revenue"), values_cur.get("cogs"))
    values_prv["gross_margin"] = compute_gross_margin(values_prv.get("revenue"), values_prv.get("cogs"))

    status, note = determine_parse_status(values_cur, values_prv, found_any_table=found_any_table)
    if found_any_table:
        note = f"{note} (PDF 파서는 실제 샘플로 검증되지 않음 — M5 검수 대상)" if note else (
            "PDF 파서는 실제 샘플로 검증되지 않음 — M5 검수 대상"
        )
    return ParsedFinancials(
        values_cur=values_cur, values_prv=values_prv, parse_status=status, parse_note=note
    )
