"""로컬 캐시만으로 기존 results 행을 소급 재파싱하는 일회성 유틸리티.

배경(2026-07-21): CLAUDE.md "파서 핵심 사실"의 "현재 소급 재파싱 대기 중인
후보" 4종(EUC-KR 인코딩 복구분 / noncurrent_assets 로마숫자 접두어 버그
수정분 / 라벨 정규화 확장으로 정정 가능한 PARTIAL / auditor_* NULL)을
사용자가 명시적으로 일괄 처리하기로 선택해 작성했다. 스키마 확장은 원래
"컬럼 추가 + 소급 재파싱 없음"이 기본 패턴이므로 이 스크립트는 항구적
엔드포인트가 아니라 재사용 가능한 일회성 배치다.

원칙:
- **DART/FSC API 호출 0건.** 오직 DOCUMENT_CACHE_DIR 로컬 캐시만 읽는다.
  캐시가 없는 rcept_no는 재다운로드(쿼터 발생)가 필요하므로 스킵하고 집계에만
  남긴다.
- 파싱 로직은 STEP5(`_run_financial_parsing`)의 내부 루프와 **완전히 동일**한
  경로(parse_xml_financials / parse_pdf_financials / extract_auditor /
  extract_audit_opinion / _extract_fiscal_date)를 재사용한다 — 새 파싱 규칙을
  여기서 만들지 않는다.
- 갱신 대상 컬럼도 STEP5의 `_apply_parsed_result`와 동일하게
  DIRECT_FINANCIAL_FIELDS + CF_FINANCIAL_FIELDS(_cur/_prv) + audit_opinion +
  fiscal_date + auditor_name/address + parse_status + parse_note로 한정한다.
  excluded_by_*/ref_* 등 필터·참고값 컬럼은 건드리지 않는다.

사용법:
    python -m scripts.reparse_local_cache --dry-run   # 변경 예정만 집계(쓰기 없음)
    python -m scripts.reparse_local_cache             # 실제 갱신
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# backend 루트를 import path에 추가 (스크립트를 직접 실행하는 경우 대비)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.db import get_session_factory  # noqa: E402
from app.core.filters import revenue_matches  # noqa: E402
from app.models.job import Job  # noqa: E402
from app.models.result import Result  # noqa: E402
from app.parsers.audit_opinion import extract_audit_opinion  # noqa: E402
from app.parsers.auditor import AuditorInfo, extract_auditor  # noqa: E402
from app.parsers.base import CF_FINANCIAL_FIELDS, DIRECT_FINANCIAL_FIELDS, ParsedFinancials  # noqa: E402
from app.parsers.pdf_parser import parse_pdf_financials  # noqa: E402
from app.parsers.xml_parser import parse_xml_financials  # noqa: E402

# STEP5의 헬퍼 두 개를 그대로 재사용(로직 중복 금지)
from app.core.pipeline import _extract_fiscal_date, _pick_document_file  # noqa: E402

_ALL_VALUE_FIELDS = tuple(
    f"{f}_{p}" for f in (DIRECT_FINANCIAL_FIELDS + CF_FINANCIAL_FIELDS) for p in ("cur", "prv")
)


def _reparse_one(doc_path: Path):
    """STEP5 내부 루프와 동일한 파싱 경로. (parsed, opinion, fiscal_date, auditor) 반환."""
    raw_bytes = doc_path.read_bytes()
    suffix = doc_path.suffix.lower()
    auditor = AuditorInfo()
    if suffix == ".xml":
        parsed = parse_xml_financials(raw_bytes)
        raw_text = raw_bytes.decode("utf-8", errors="ignore")
        auditor = extract_auditor(raw_bytes)
    elif suffix == ".pdf":
        parsed = parse_pdf_financials(raw_bytes)
        raw_text = ""
    else:
        parsed = ParsedFinancials(parse_status="FAILED", parse_note=f"지원하지 않는 원문 형식: {suffix}")
        raw_text = ""
    opinion = extract_audit_opinion(raw_text) if raw_text else None
    fiscal_date = _extract_fiscal_date(raw_text) if raw_text else None
    return parsed, opinion, fiscal_date, auditor


def _verify_signs(cache_root: Path, Session) -> int:
    """재파싱 자체검증: 원문에서 부호를 독립적으로 다시 읽어 저장값과 대조한다.

    2026-07-21 추가(dart-qa 지적) — revenue-cogs 항등식과 value→NULL 회귀
    체크만으로는 "손실/이익 라벨 부호 오분류"를 놓쳤다. 여기서는 파싱 로직과
    **독립된** 회계 항등식으로 검증한다:
    - `gross_profit == revenue - cogs`(정의상 반드시 성립). |차이|가 0이 아니고
      **크기는 같은데 부호만 반대**면 부호 오분류(치명)로, 크기까지 다르면
      다른 원인(예: 상세열 "-" 오파싱으로 cogs=0)으로 분리 집계한다.
    저장값(DB)과 현재 파서 출력이 일치하는지도 함께 확인해 재파싱이 실제로
    반영됐는지(멱등/드리프트) 본다. 반환값: 부호 오분류 건수(0이어야 정상).
    """
    with Session() as db:
        rows = db.execute(
            select(Result.id, Result.rcept_no).where(
                Result.rcept_no.is_not(None), Result.parse_status.is_not(None)
            ).order_by(Result.id)
        ).all()

    sign_flip = []      # |gp|==|rev-cogs|, 부호만 반대 (치명)
    magnitude_off = []  # 크기까지 다름 (부호 아님 — 별도 원인)
    db_drift = []       # 저장값 != 현재 파서 출력 (재파싱 미반영)
    checked = 0
    for result_id, rcept_no in rows:
        target_dir = cache_root / rcept_no
        doc_path = _pick_document_file(target_dir) if target_dir.is_dir() else None
        if doc_path is None or doc_path.suffix.lower() != ".xml":
            continue
        parsed = parse_xml_financials(doc_path.read_bytes())
        with Session() as db:
            result = db.get(Result, result_id)
        for per, vals in (("cur", parsed.values_cur), ("prv", parsed.values_prv)):
            gp, rev, cogs = vals.get("gross_profit"), vals.get("revenue"), vals.get("cogs")
            if gp is None or rev is None or cogs is None:
                continue
            checked += 1
            ident = rev - cogs
            if abs(gp - ident) > 1:
                if abs(abs(gp) - abs(ident)) <= 1:
                    sign_flip.append((result_id, rcept_no, per, gp, ident))
                else:
                    magnitude_off.append((result_id, rcept_no, per, gp, ident, cogs))
            # DB 저장값과 현재 파서 출력 부호 대조(재파싱 반영 여부)
            for fld in ("gross_profit", "operating_income", "net_income"):
                stored = getattr(result, f"{fld}_{per}")
                cur_val = vals.get(fld)
                sv = None if stored is None else int(stored)
                cv = None if cur_val is None else int(cur_val)
                if sv != cv:
                    db_drift.append((result_id, rcept_no, per, fld, sv, cv))

    print("=" * 70)
    print("부호 자체검증 (원문 회계 항등식 gross_profit == revenue - cogs)")
    print(f"검사한 (행,기간) 조합: {checked}")
    print(f"부호 오분류(|gp|==|rev-cogs|, 부호만 반대) — 치명: {len(sign_flip)}")
    for e in sign_flip[:20]:
        print("   ", e)
    print(f"크기 불일치(부호 아님, 예: 상세열 '-'로 cogs 오파싱): {len(magnitude_off)}")
    for e in magnitude_off[:20]:
        print("   ", e)
    print(f"DB 저장값 != 현재 파서 출력(재파싱 미반영/드리프트): {len(db_drift)}")
    for e in db_drift[:20]:
        print("   ", e)
    return len(sign_flip)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="변경 예정만 집계하고 DB에 쓰지 않음")
    ap.add_argument("--verify", action="store_true", help="원문 부호 항등식 자체검증만 수행(쓰기 없음)")
    ap.add_argument("--limit", type=int, default=None, help="처리 건수 상한(디버그용)")
    args = ap.parse_args()

    settings = get_settings()
    cache_root = Path(settings.document_cache_dir)
    Session = get_session_factory()

    if args.verify:
        return 1 if _verify_signs(cache_root, Session) else 0

    # 재파싱 대상: rcept_no가 있고 이미 한 번 파싱된(parse_status IS NOT NULL) 행.
    # (FAILED이면서 rcept_no가 없는 "감사보고서 없음" 858건은 애초에 원문이
    #  없어 재파싱 불가라 자연히 제외된다.)
    with Session() as db:
        rows = db.execute(
            select(Result.id, Result.rcept_no)
            .where(Result.rcept_no.is_not(None), Result.parse_status.is_not(None))
            .order_by(Result.id)
        ).all()
    if args.limit:
        rows = rows[: args.limit]

    stats = Counter()
    stats["target_rows"] = len(rows)

    # 카테고리별 집계
    cat = Counter()
    status_transition = Counter()  # (old, new)
    changed_examples: list[str] = []
    missing_cache_rcepts: set[str] = set()
    job_cond_cache: dict[int | None, tuple[dict, dict]] = {}

    def _job_conditions(db, job_id: int | None) -> tuple[dict, dict]:
        """Job의 매출액·총자산 조건(JSON)을 캐시해 반환. 없으면 빈 dict 두 개."""
        if job_id in job_cond_cache:
            return job_cond_cache[job_id]
        cond_rev: dict = {}
        cond_assets: dict = {}
        if job_id is not None:
            job = db.get(Job, job_id)
            if job is not None:
                cond_rev = json.loads(job.cond_revenue) if job.cond_revenue else {}
                cond_assets = json.loads(job.cond_total_assets) if job.cond_total_assets else {}
        job_cond_cache[job_id] = (cond_rev, cond_assets)
        return cond_rev, cond_assets

    for result_id, rcept_no in rows:
        target_dir = cache_root / rcept_no
        doc_path = _pick_document_file(target_dir) if target_dir.is_dir() else None
        if doc_path is None:
            stats["missing_cache"] += 1
            missing_cache_rcepts.add(rcept_no)
            continue

        try:
            parsed, opinion, fiscal_date, auditor = _reparse_one(doc_path)
        except Exception as exc:  # noqa: BLE001
            stats["reparse_exception"] += 1
            parsed = ParsedFinancials(parse_status="FAILED", parse_note=f"재파싱 중 예외: {exc}")
            opinion = None
            fiscal_date = None
            auditor = AuditorInfo()

        with Session() as db:
            result = db.get(Result, result_id)
            if result is None:
                continue

            old_status = result.parse_status
            old_auditor_name = result.auditor_name
            old_auditor_address = result.auditor_address
            old_noncurrent_cur = result.noncurrent_assets_cur
            old_noncurrent_prv = result.noncurrent_assets_prv

            # 값 변경 감지 (숫자 필드)
            new_values = {}
            for f in DIRECT_FINANCIAL_FIELDS + CF_FINANCIAL_FIELDS:
                new_values[f"{f}_cur"] = parsed.values_cur.get(f)
                new_values[f"{f}_prv"] = parsed.values_prv.get(f)

            value_changed_fields = [
                name for name in _ALL_VALUE_FIELDS if getattr(result, name) != new_values[name]
            ]

            new_status = parsed.parse_status
            new_auditor = auditor or AuditorInfo()

            auditor_filled = (old_auditor_name is None and new_auditor.name is not None) or (
                old_auditor_address is None and new_auditor.address is not None
            )
            noncurrent_filled = (old_noncurrent_cur is None and new_values["noncurrent_assets_cur"] is not None) or (
                old_noncurrent_prv is None and new_values["noncurrent_assets_prv"] is not None
            )

            row_changed = bool(
                value_changed_fields
                or new_status != old_status
                or new_auditor.name != old_auditor_name
                or new_auditor.address != old_auditor_address
            )

            if row_changed:
                stats["rows_changed"] += 1
            if value_changed_fields:
                stats["rows_value_changed"] += 1
            if new_status != old_status:
                status_transition[(old_status, new_status)] += 1
            if old_status == "PARTIAL" and new_status == "OK":
                cat["partial_to_ok"] += 1
            if noncurrent_filled:
                cat["noncurrent_filled"] += 1
            if auditor_filled:
                cat["auditor_filled"] += 1
            if new_values["revenue_cur"] != result.revenue_cur or new_values["total_assets_cur"] != result.total_assets_cur:
                cat["revenue_or_assets_changed"] += 1

            if row_changed and len(changed_examples) < 40:
                changed_examples.append(
                    f"id={result_id} rcept={rcept_no} {old_status}->{new_status} "
                    f"val_changed={value_changed_fields[:6]}"
                    f"{'...' if len(value_changed_fields) > 6 else ''} "
                    f"auditor:{old_auditor_name}/{old_auditor_address}"
                    f" -> {new_auditor.name}/{new_auditor.address}"
                )

            # revenue_cur/total_assets_cur가 새로 채워지면 사후필터 플래그가 stale해질
            # 수 있어, 변경 행에 한해 파이프라인 STEP6/B4와 동일 로직으로 재계산한다.
            # 값을 못 파싱한 경우(None)는 파이프라인과 동일하게 제외하지 않는다(0).
            filter_recomputed = False
            if new_status != old_status or (
                new_values["revenue_cur"] != result.revenue_cur
                or new_values["total_assets_cur"] != result.total_assets_cur
            ):
                cond_rev, cond_assets = _job_conditions(db, result.job_id)
                new_excl_rev = result.excluded_by_revenue
                new_excl_assets = result.excluded_by_assets
                if cond_rev.get("min_krw") is not None or cond_rev.get("max_krw") is not None:
                    rc_val = new_values["revenue_cur"]
                    new_excl_rev = 0 if rc_val is None else (0 if revenue_matches(rc_val, cond_rev) else 1)
                if cond_assets.get("min_krw") is not None or cond_assets.get("max_krw") is not None:
                    ta_val = new_values["total_assets_cur"]
                    new_excl_assets = 0 if ta_val is None else (0 if revenue_matches(ta_val, cond_assets) else 1)
                if new_excl_rev != result.excluded_by_revenue or new_excl_assets != result.excluded_by_assets:
                    filter_recomputed = True
                    cat["filter_flag_recomputed"] += 1

            if not args.dry_run and row_changed:
                for name, val in new_values.items():
                    setattr(result, name, val)
                result.audit_opinion = opinion
                result.fiscal_date = fiscal_date
                result.auditor_name = new_auditor.name
                result.auditor_address = new_auditor.address
                result.parse_status = new_status
                result.parse_note = parsed.parse_note
                if filter_recomputed:
                    result.excluded_by_revenue = new_excl_rev
                    result.excluded_by_assets = new_excl_assets
                db.commit()

    print("=" * 70)
    print(f"모드: {'DRY-RUN (쓰기 없음)' if args.dry_run else '실제 갱신'}")
    print(f"대상 행: {stats['target_rows']}")
    print(f"캐시 결측(스킵): {stats['missing_cache']}  (distinct rcept: {len(missing_cache_rcepts)})")
    print(f"재파싱 예외: {stats['reparse_exception']}")
    print(f"변경된 행(어떤 필드든): {stats['rows_changed']}")
    print(f"  그중 숫자 값 변경: {stats['rows_value_changed']}")
    print("-" * 70)
    print("카테고리별:")
    print(f"  PARTIAL -> OK 전환: {cat['partial_to_ok']}")
    print(f"  noncurrent_assets NULL->값 채워짐: {cat['noncurrent_filled']}")
    print(f"  auditor_name/address NULL->채워짐: {cat['auditor_filled']}")
    print(f"  revenue_cur 또는 total_assets_cur 값 변경: {cat['revenue_or_assets_changed']}")
    print(f"  excluded_by_* 필터 플래그 재계산됨: {cat['filter_flag_recomputed']}")
    print("-" * 70)
    print("parse_status 전환 (old -> new):")
    for (o, n), cnt in sorted(status_transition.items(), key=lambda x: -x[1]):
        print(f"  {o} -> {n}: {cnt}")
    print("-" * 70)
    print("변경 예시(최대 40건):")
    for ex in changed_examples:
        print("  " + ex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
