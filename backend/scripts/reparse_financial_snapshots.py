"""financial_snapshots 테이블의 영업외수익/영업외비용 2컬럼을 로컬 캐시로 소급 채우는 일회성 유틸리티.

배경(2026-07-22): 영업외수익/영업외비용(`non_operating_income`/
`non_operating_expense`) 2필드를 신설한 뒤, `results` 테이블은
`reparse_local_cache.py`로 소급 반영했으나 **재무이력(다년치) 표가 값을 읽는
`financial_snapshots` 테이블은 별개 테이블이라 빠져 있었다** — 결과 화면의
"재무이력(최근 N년)" 표에서 영업외수익/영업외비용 **합계 행**이 여전히
"-"로 나오는 것으로 사용자가 발견. 세부계정(이자수익/외환차익 등)은
`account_detail.py`가 원문을 매번 새로 열어 표시하므로 DB와 무관하게 정상이었다.

이 테이블의 기존 4,147행은 두 필드가 생기기 전에 STEP7이 채운 것이라 전부
NULL이다. 각 행은 `rcept_no`로 자기 원문의 출처를 알고 있으므로, 그 원문을
로컬 캐시에서 다시 파싱하면 그 스냅샷 행의 값을 그대로 재현할 수 있다
(`_upsert_financial_snapshot`과 완전히 같은 원리, `results` 소급 반영과 동형).

원칙:
- **DART/FSC API 호출 0건.** 오직 DOCUMENT_CACHE_DIR 로컬 캐시만 읽는다.
  캐시가 없는 rcept_no는 스킵하고 집계에만 남긴다.
- 파싱 로직은 STEP7(`_collect_history_for_result`)과 **완전히 동일**한 경로
  (parse_xml_financials / parse_pdf_financials / _extract_fiscal_date)를
  재사용한다 — 새 파싱 규칙을 만들지 않는다.
- **오직 `non_operating_income`/`non_operating_expense` 두 컬럼만** NULL인 경우에
  한해 채운다. 다른 컬럼(revenue/gross_profit/parse_status/from_current_period
  등)은 절대 건드리지 않는다 — "이번에 새로 추가된 필드만" 범위를 좁게 유지.
- 스냅샷 행이 어느 기간(당기/전기) 열에서 왔는지는 STEP7 규칙 그대로 판정한다:
  원문의 결산기준일(`_extract_fiscal_date`)로 당기 연도를 뽑아
  `financial_snapshots.fiscal_year`와 비교 — 같으면 `values_cur`, 한 해 앞서면
  `values_prv`를 쓴다(`_collect_history_for_result`가 채운 것과 동일한 값). 원문에서
  결산기준일을 못 뽑는 경우(PDF 등)만 `from_current_period` 플래그로 폴백한다.
- `--dry-run` 지원, API 호출 0건, 재실행 멱등(이미 채워진 행은 건너뜀).

사용법:
    python -m scripts.reparse_financial_snapshots --dry-run   # 변경 예정만 집계
    python -m scripts.reparse_financial_snapshots             # 실제 갱신
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.db import get_session_factory  # noqa: E402
from app.models.financial_snapshot import FinancialSnapshot  # noqa: E402
from app.parsers.base import NON_OPERATING_FINANCIAL_FIELDS, ParsedFinancials  # noqa: E402
from app.parsers.pdf_parser import parse_pdf_financials  # noqa: E402
from app.parsers.xml_parser import parse_xml_financials  # noqa: E402

# STEP7의 헬퍼를 그대로 재사용(로직 중복 금지)
from app.core.pipeline import _extract_fiscal_date, _pick_document_file  # noqa: E402


def _parse_doc(doc_path: Path) -> tuple[ParsedFinancials, str | None]:
    """STEP7 내부 루프와 동일한 파싱 경로. (parsed, fiscal_date) 반환."""
    raw_bytes = doc_path.read_bytes()
    suffix = doc_path.suffix.lower()
    if suffix == ".xml":
        parsed = parse_xml_financials(raw_bytes)
        raw_text = raw_bytes.decode("utf-8", errors="ignore")
    elif suffix == ".pdf":
        parsed = parse_pdf_financials(raw_bytes)
        raw_text = ""
    else:
        return ParsedFinancials(parse_status="FAILED", parse_note=f"지원하지 않는 원문 형식: {suffix}"), None
    fiscal_date = _extract_fiscal_date(raw_text) if raw_text else None
    return parsed, fiscal_date


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="변경 예정만 집계하고 DB에 쓰지 않음")
    ap.add_argument("--limit", type=int, default=None, help="처리 건수 상한(디버그용)")
    args = ap.parse_args()

    settings = get_settings()
    cache_root = Path(settings.document_cache_dir)
    Session = get_session_factory()

    # 대상: rcept_no가 있고, 두 컬럼 중 하나라도 아직 NULL인 행(멱등 — 이미 둘 다
    # 채워진 행은 애초에 제외돼 재실행 시 재파싱 자체를 하지 않는다).
    with Session() as db:
        rows = db.execute(
            select(
                FinancialSnapshot.id,
                FinancialSnapshot.rcept_no,
                FinancialSnapshot.fiscal_year,
                FinancialSnapshot.from_current_period,
            )
            .where(
                FinancialSnapshot.rcept_no.is_not(None),
                (
                    FinancialSnapshot.non_operating_income.is_(None)
                    | FinancialSnapshot.non_operating_expense.is_(None)
                ),
            )
            .order_by(FinancialSnapshot.id)
        ).all()
    if args.limit:
        rows = rows[: args.limit]

    stats = Counter()
    stats["target_rows"] = len(rows)
    missing_cache_rcepts: set[str] = set()
    period_source = Counter()  # cur / prv / from_flag_cur / from_flag_prv / ambiguous
    changed_examples: list[str] = []

    # rcept_no별 파싱 결과 캐시(같은 원문이 여러 스냅샷 행에 재사용됨 — CPU 절약).
    parse_cache: dict[str, tuple[ParsedFinancials, str | None]] = {}

    for snap_id, rcept_no, fiscal_year, from_current_period in rows:
        target_dir = cache_root / rcept_no
        doc_path = _pick_document_file(target_dir) if target_dir.is_dir() else None
        if doc_path is None:
            stats["missing_cache"] += 1
            missing_cache_rcepts.add(rcept_no)
            continue

        if rcept_no not in parse_cache:
            try:
                parse_cache[rcept_no] = _parse_doc(doc_path)
            except Exception as exc:  # noqa: BLE001
                stats["parse_exception"] += 1
                parse_cache[rcept_no] = (ParsedFinancials(parse_status="FAILED", parse_note=str(exc)), None)
        parsed, fiscal_date = parse_cache[rcept_no]

        # 이 스냅샷 행이 원문의 당기/전기 중 어느 열에서 왔는지 판정.
        # 1순위: 원문 결산기준일로 당기 연도를 뽑아 fiscal_year와 비교(STEP7과 동일).
        # 2순위(결산기준일 없음, 예: PDF): from_current_period 플래그로 폴백.
        values = None
        if fiscal_date is not None:
            fiscal_year_cur = fiscal_date[:4]
            if fiscal_year == fiscal_year_cur:
                values = parsed.values_cur
                period_source["cur"] += 1
            elif fiscal_year == str(int(fiscal_year_cur) - 1):
                values = parsed.values_prv
                period_source["prv"] += 1
            else:
                # 원문 당기 연도와도, 그 전년과도 안 맞음 — 안전하게 스킵(집계만).
                stats["year_mismatch"] += 1
                period_source["ambiguous"] += 1
                continue
        else:
            if from_current_period:
                values = parsed.values_cur
                period_source["from_flag_cur"] += 1
            else:
                values = parsed.values_prv
                period_source["from_flag_prv"] += 1

        new_inc = values.get("non_operating_income")
        new_exp = values.get("non_operating_expense")

        with Session() as db:
            snap = db.get(FinancialSnapshot, snap_id)
            if snap is None:
                continue
            changed = False
            filled = []
            if snap.non_operating_income is None and new_inc is not None:
                if not args.dry_run:
                    snap.non_operating_income = new_inc
                changed = True
                stats["income_filled"] += 1
                filled.append(f"inc={new_inc}")
            if snap.non_operating_expense is None and new_exp is not None:
                if not args.dry_run:
                    snap.non_operating_expense = new_exp
                changed = True
                stats["expense_filled"] += 1
                filled.append(f"exp={new_exp}")
            if changed:
                stats["rows_changed"] += 1
                if not args.dry_run:
                    db.commit()
                if len(changed_examples) < 30:
                    changed_examples.append(
                        f"snap_id={snap_id} rcept={rcept_no} fy={fiscal_year} {' '.join(filled)}"
                    )

    print("=" * 70)
    print(f"모드: {'DRY-RUN (쓰기 없음)' if args.dry_run else '실제 갱신'}")
    print(f"대상 행(둘 중 하나라도 NULL): {stats['target_rows']}")
    print(f"캐시 결측(스킵): {stats['missing_cache']}  (distinct rcept: {len(missing_cache_rcepts)})")
    print(f"파싱 예외: {stats['parse_exception']}")
    print(f"연도 불일치로 스킵: {stats['year_mismatch']}")
    print("-" * 70)
    print(f"값이 채워진 행(둘 중 하나 이상): {stats['rows_changed']}")
    print(f"  non_operating_income  NULL->값: {stats['income_filled']}")
    print(f"  non_operating_expense NULL->값: {stats['expense_filled']}")
    print("-" * 70)
    print("기간 판정 출처:")
    for k in ("cur", "prv", "from_flag_cur", "from_flag_prv", "ambiguous"):
        print(f"  {k}: {period_source[k]}")
    print("-" * 70)
    print("변경 예시(최대 30건):")
    for ex in changed_examples:
        print("  " + ex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
