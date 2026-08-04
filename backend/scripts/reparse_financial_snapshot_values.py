"""financial_snapshots 테이블의 표준 재무 13항목 + CF 4항목 + 영업외손익 2항목을
로컬 캐시로 소급 재파싱하는 일회성 유틸리티.

배경(2026-07-26, 사용자 스크린샷 지적으로 발견): 결과조회 상세의 "재무 이력
(최근 N년)" 표가 특정 회사에서 전 연도 "-"로 비어 있는데, 같은 회사의
`results`(결과조회 목록) 행에는 자산총계 등 값이 정상적으로 들어 있는
불일치가 확인됐다. 원인은 `financial_snapshots`가 STEP7이 **그 시점의
파서 버전**으로 한 번 파싱한 결과를 그대로 들고 있고, 이후 파서가 개선돼도
(IFRS 첨부 서식 지원, 라벨 alias 보강 등) 자동으로 재파싱되지 않기
때문이었다. `results`는 `reparse_local_cache.py`로 이미 여러 차례 소급
반영됐지만, `financial_snapshots`를 대상으로 한 기존 스크립트
(`reparse_financial_snapshots.py`)는 영업외수익/영업외비용 2컬럼만
좁게 채우도록 설계돼 있어 이번처럼 재무상태표/손익계산서 자체가
통째로 비는 문제는 다루지 못했다.

원칙(기존 소급 재파싱 스크립트들과 동일):
- **DART/FSC API 호출 0건.** 오직 DOCUMENT_CACHE_DIR 로컬 캐시만 읽는다.
  캐시가 없는 rcept_no는 스킵하고 집계에만 남긴다.
- 파싱 로직은 STEP7(`_collect_history_for_result`)과 **완전히 동일**한 경로
  (parse_xml_financials / parse_pdf_financials / _extract_fiscal_date)를
  재사용한다 — 새 파싱 규칙을 여기서 만들지 않는다.
- 이 스냅샷 행이 원문의 당기/전기 중 어느 열에서 왔는지는
  `reparse_financial_snapshots.py`와 동일하게 판정한다: 원문 결산기준일로
  당기 연도를 뽑아 `fiscal_year`와 비교(1순위) → 결산기준일을 못 뽑으면
  `from_current_period` 플래그로 폴백(2순위). 원문 당기 연도와도 그 전년과도
  안 맞으면 안전하게 스킵한다(연도 오귀속 방지).
- 갱신 대상 컬럼은 `_upsert_financial_snapshot`과 동일하게 표준 13항목 +
  CF 4항목 + 영업외손익 2항목 + 세부계정 5항목(2026-08-05 추가,
  cur/prv 접미어 없음) + parse_status + parse_note로 한정한다.
  `rcept_no`/`fiscal_year`/`from_current_period`/`auditor_name`은 건드리지
  않는다(각각 별도 책임 — auditor_name은 `backfill_auditor_names.py`가
  이미 채웠다).
- `--dry-run` 지원, 재실행 멱등(값이 이미 파서 출력과 같으면 재기록 안 함),
  `--verify`로 회계 항등식 자체검증.

사용법:
    python -m scripts.reparse_financial_snapshot_values --dry-run   # 변경 예정만 집계
    python -m scripts.reparse_financial_snapshot_values             # 실제 갱신
    python -m scripts.reparse_financial_snapshot_values --verify    # 항등식 자체검증만
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
from app.parsers.base import (  # noqa: E402
    CF_FINANCIAL_FIELDS,
    DETAIL_FINANCIAL_FIELDS,
    NON_OPERATING_FINANCIAL_FIELDS,
    STANDARD_FINANCIAL_FIELDS,
    ParsedFinancials,
)
from app.parsers.pdf_parser import parse_pdf_financials  # noqa: E402
from app.parsers.xml_parser import parse_xml_financials  # noqa: E402

# STEP7의 헬퍼를 그대로 재사용(로직 중복 금지)
from app.core.pipeline import _extract_fiscal_date, _pick_document_file  # noqa: E402

_ALL_VALUE_FIELDS = (
    STANDARD_FINANCIAL_FIELDS
    + CF_FINANCIAL_FIELDS
    + NON_OPERATING_FINANCIAL_FIELDS
    + DETAIL_FINANCIAL_FIELDS
)


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


def _resolve_values(
    parsed: ParsedFinancials, fiscal_date: str | None, fiscal_year: str, from_current_period: int | None
) -> tuple[dict[str, float | None] | None, str]:
    """스냅샷 행이 당기/전기 어느 열 유래인지 판정해 값 dict를 고른다.

    반환: (values 또는 None(판정 불가/스킵), 판정 출처 라벨).
    """
    if fiscal_date is not None:
        fiscal_year_cur = fiscal_date[:4]
        if fiscal_year == fiscal_year_cur:
            return parsed.values_cur, "cur"
        if fiscal_year == str(int(fiscal_year_cur) - 1):
            return parsed.values_prv, "prv"
        return None, "ambiguous"
    if from_current_period:
        return parsed.values_cur, "from_flag_cur"
    return parsed.values_prv, "from_flag_prv"


def _verify(cache_root: Path, Session) -> int:
    """재파싱 자체검증: 원문에서 다시 뽑은 값으로 회계 항등식과 DB 드리프트를 확인.

    - `total_assets == current_assets + noncurrent_assets`
    - `total_liab + total_equity == total_assets`
    - `gross_profit == revenue - cogs`
    3개 항등식 모두 정의상 성립해야 한다. 위반 중 |차이|가 0이 아니고 부호만
    반대면 부호 오분류(치명), 아니면 다른 원인(개별 항목 결측 등)으로 분리
    집계한다. DB 저장값과 현재 파서 출력이 다르면(재파싱 미반영) 드리프트로
    센다. 반환값: 부호 오분류 건수(0이어야 정상).
    """
    with Session() as db:
        rows = db.execute(
            select(FinancialSnapshot.id, FinancialSnapshot.rcept_no, FinancialSnapshot.fiscal_year,
                   FinancialSnapshot.from_current_period)
            .where(FinancialSnapshot.rcept_no.is_not(None))
            .order_by(FinancialSnapshot.id)
        ).all()

    sign_flip = []
    magnitude_off = []
    db_drift = []
    checked = 0
    parse_cache: dict[str, tuple[ParsedFinancials, str | None]] = {}

    for snap_id, rcept_no, fiscal_year, from_current_period in rows:
        target_dir = cache_root / rcept_no
        doc_path = _pick_document_file(target_dir) if target_dir.is_dir() else None
        if doc_path is None or doc_path.suffix.lower() != ".xml":
            continue
        if rcept_no not in parse_cache:
            try:
                parse_cache[rcept_no] = _parse_doc(doc_path)
            except Exception as exc:  # noqa: BLE001
                parse_cache[rcept_no] = (ParsedFinancials(parse_status="FAILED", parse_note=str(exc)), None)
        parsed, fiscal_date = parse_cache[rcept_no]
        values, _source = _resolve_values(parsed, fiscal_date, fiscal_year, from_current_period)
        if values is None:
            continue
        checked += 1

        gp, rev, cogs = values.get("gross_profit"), values.get("revenue"), values.get("cogs")
        if gp is not None and rev is not None and cogs is not None:
            ident = rev - cogs
            if abs(gp - ident) > 1:
                if abs(abs(gp) - abs(ident)) <= 1:
                    sign_flip.append((snap_id, rcept_no, fiscal_year, "gross_profit", gp, ident))
                else:
                    magnitude_off.append((snap_id, rcept_no, fiscal_year, "gross_profit", gp, ident))

        ca, nca, ta = values.get("current_assets"), values.get("noncurrent_assets"), values.get("total_assets")
        if ca is not None and nca is not None and ta is not None:
            ident = ca + nca
            if abs(ta - ident) > 1:
                magnitude_off.append((snap_id, rcept_no, fiscal_year, "total_assets", ta, ident))

        tl, te = values.get("total_liab"), values.get("total_equity")
        if tl is not None and te is not None and ta is not None:
            ident = tl + te
            if abs(ta - ident) > 1:
                magnitude_off.append((snap_id, rcept_no, fiscal_year, "total_assets==liab+equity", ta, ident))

        with Session() as db:
            snap = db.get(FinancialSnapshot, snap_id)
        for fld in ("gross_profit", "operating_income", "net_income", "total_assets"):
            stored = getattr(snap, fld)
            cur_val = values.get(fld)
            sv = None if stored is None else int(stored)
            cv = None if cur_val is None else int(cur_val)
            if sv != cv:
                db_drift.append((snap_id, rcept_no, fiscal_year, fld, sv, cv))

    print("=" * 70)
    print("재무이력(financial_snapshots) 항등식 자체검증")
    print(f"검사한 (스냅샷행) 수: {checked}")
    print(f"부호 오분류(치명): {len(sign_flip)}")
    for e in sign_flip[:20]:
        print("   ", e)
    print(f"크기 불일치(부호 아님): {len(magnitude_off)}")
    for e in magnitude_off[:20]:
        print("   ", e)
    print(f"DB 저장값 != 현재 파서 출력(드리프트): {len(db_drift)}")
    for e in db_drift[:20]:
        print("   ", e)
    return len(sign_flip)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="변경 예정만 집계하고 DB에 쓰지 않음")
    ap.add_argument("--verify", action="store_true", help="회계 항등식 자체검증만 수행(쓰기 없음)")
    ap.add_argument("--limit", type=int, default=None, help="처리 건수 상한(디버그용)")
    args = ap.parse_args()

    settings = get_settings()
    cache_root = Path(settings.document_cache_dir)
    Session = get_session_factory()

    if args.verify:
        return 1 if _verify(cache_root, Session) else 0

    with Session() as db:
        rows = db.execute(
            select(
                FinancialSnapshot.id,
                FinancialSnapshot.rcept_no,
                FinancialSnapshot.fiscal_year,
                FinancialSnapshot.from_current_period,
            )
            .where(FinancialSnapshot.rcept_no.is_not(None))
            .order_by(FinancialSnapshot.id)
        ).all()
    if args.limit:
        rows = rows[: args.limit]

    stats = Counter()
    stats["target_rows"] = len(rows)
    cat = Counter()
    status_transition = Counter()
    period_source = Counter()
    changed_examples: list[str] = []
    missing_cache_rcepts: set[str] = set()

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

        values, source = _resolve_values(parsed, fiscal_date, fiscal_year, from_current_period)
        period_source[source] += 1
        if values is None:
            stats["year_mismatch"] += 1
            continue

        new_values = {f: values.get(f) for f in _ALL_VALUE_FIELDS}
        new_status = parsed.parse_status
        new_note = parsed.parse_note

        with Session() as db:
            snap = db.get(FinancialSnapshot, snap_id)
            if snap is None:
                continue

            old_status = snap.parse_status
            value_changed_fields = [f for f in _ALL_VALUE_FIELDS if getattr(snap, f) != new_values[f]]
            row_changed = bool(value_changed_fields or new_status != old_status or new_note != snap.parse_note)

            if row_changed:
                stats["rows_changed"] += 1
            if value_changed_fields:
                stats["rows_value_changed"] += 1
            if new_status != old_status:
                status_transition[(old_status, new_status)] += 1
            if old_status == "PARTIAL" and new_status == "OK":
                cat["partial_to_ok"] += 1

            if row_changed and len(changed_examples) < 40:
                changed_examples.append(
                    f"snap_id={snap_id} rcept={rcept_no} fy={fiscal_year} {old_status}->{new_status} "
                    f"val_changed={value_changed_fields[:6]}"
                    f"{'...' if len(value_changed_fields) > 6 else ''}"
                )

            if not args.dry_run and row_changed:
                for name, val in new_values.items():
                    setattr(snap, name, val)
                snap.parse_status = new_status
                snap.parse_note = new_note
                db.commit()

    print("=" * 70)
    print(f"모드: {'DRY-RUN (쓰기 없음)' if args.dry_run else '실제 갱신'}")
    print(f"대상 행: {stats['target_rows']}")
    print(f"캐시 결측(스킵): {stats['missing_cache']}  (distinct rcept: {len(missing_cache_rcepts)})")
    print(f"파싱 예외: {stats['parse_exception']}")
    print(f"연도 불일치로 스킵: {stats['year_mismatch']}")
    print("-" * 70)
    print(f"변경된 행(어떤 필드든): {stats['rows_changed']}")
    print(f"  그중 숫자 값 변경: {stats['rows_value_changed']}")
    print(f"  PARTIAL -> OK 전환: {cat['partial_to_ok']}")
    print("-" * 70)
    print("parse_status 전환 (old -> new):")
    for (o, n), cnt in sorted(status_transition.items(), key=lambda x: -x[1]):
        print(f"  {o} -> {n}: {cnt}")
    print("-" * 70)
    print("기간 판정 출처:")
    for k in ("cur", "prv", "from_flag_cur", "from_flag_prv", "ambiguous"):
        print(f"  {k}: {period_source[k]}")
    print("-" * 70)
    print("변경 예시(최대 40건):")
    for ex in changed_examples:
        print("  " + ex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
