"""연도별 감사인 이름(`financial_snapshots.auditor_name`)과 회사 단위
"감사인 변동"(`results.auditor_changed`)을 로컬 캐시로 소급 채우는 일회성 유틸리티.

배경(2026-07-26): "연도별 감사인 + 감사인 변동" 기능(`financial_snapshots.
auditor_name` 신규 컬럼 + `results.auditor_changed` tri-state 캐시)은 이 저장소의
기본 관행("컬럼 추가만, 소급 재파싱 없음")대로 **신규 Phase 2 실행분부터만** 값이
채워지도록 구현됐다. 그래서 이미 완료된 Job의 기존 `financial_snapshots` 4,147행은
전부 `auditor_name IS NULL`이고, `results.auditor_changed`도 전부 NULL이었다.
사용자가 이번 건에 한해 명시적으로 소급 반영을 승인해 작성했다(과거 CF/영업외손익/
`results.auditor_name` 교정과 동일한 일회성 예외 절차).

`reparse_financial_snapshots.py`(영업외수익/영업외비용 전용)를 확장하지 않고 새
스크립트로 분리한 이유: 그 스크립트는 "오직 non_operating_* 2컬럼만 채운다"는 좁은
책임과 그에 맞춘 기간(당기/전기) 판정 로직을 갖고 있는데, 감사인 이름은 **기간 판정이
필요 없고**(`from_current_period=1`인 행만 대상), 대신 **회사 단위 집계
(`results.auditor_changed`) 후속 갱신**이라는 다른 책임이 붙기 때문이다.

원칙(기존 백필 스크립트들과 동일):
- **DART/FSC API 호출 0건.** 오직 DOCUMENT_CACHE_DIR 로컬 캐시만 읽는다. 캐시가
  없는 rcept_no는 스킵하고 집계에만 남긴다(예외로 죽지 않는다).
- 파싱·비교 로직을 여기서 새로 만들지 않는다 — 감사인 추출은 STEP7과 동일하게
  `pipeline._safe_extract_auditor_name()`(내부적으로 `parsers.auditor.extract_auditor`),
  회사 단위 판정은 `pipeline._update_auditor_change_flag()`(내부적으로 `_auditor_key`),
  연도별 변경 플래그 검증은 `pipeline.auditor_change_flags()`를 그대로 재사용한다.
- 갱신 대상 컬럼은 `financial_snapshots.auditor_name`과 `results.auditor_changed`
  **두 개뿐**이다. 다른 컬럼(수치/parse_status/from_current_period/results의 다른
  필드)은 절대 건드리지 않는다.
- 대상: `from_current_period = 1 AND auditor_name IS NULL`인 스냅샷 행. 전기 열
  유래 행(`from_current_period=0`)은 연도-감사인 대응이 어긋나므로 애초에 대상이
  아니다(app/models/financial_snapshot.py 주석 참고). 원문에 감사인 서명이 없으면
  (의견거절/미첨부 등) NULL로 남긴다 — 진성 결측.
- `--dry-run` 지원, 재실행 멱등(이미 채워진 행은 조회 단계에서 제외된다).
- 실제 갱신 모드에서는 **실행 전 자동으로 DB를 백업**한다(sqlite 온라인 백업 API를
  써서 WAL까지 포함한 일관 스냅샷을 만든다):
  `dart_search.db.bak.pre_auditor_backfill.<YYYYMMDD_HHMMSS>`

사용법:
    python -m scripts.backfill_auditor_names --dry-run   # 변경 예정만 집계(쓰기 없음)
    python -m scripts.backfill_auditor_names             # 백업 후 실제 갱신
    python -m scripts.backfill_auditor_names --verify    # 백업본 대비 드리프트/정합성 검증
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# backend 루트를 import path에 추가 (스크립트를 직접 실행하는 경우 대비)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.db import get_session_factory  # noqa: E402
from app.models.financial_snapshot import FinancialSnapshot  # noqa: E402
from app.models.result import Result  # noqa: E402

# 파싱/비교 로직은 파이프라인 것을 그대로 재사용한다(중복 구현 금지)
from app.core.pipeline import (  # noqa: E402
    _auditor_key,
    _pick_document_file,
    _safe_extract_auditor_name,
    _update_auditor_change_flag,
    auditor_change_flags,
)

_BACKUP_PREFIX = "dart_search.db.bak.pre_auditor_backfill."

# 검증에서 "안 바뀌었어야 하는" 컬럼 목록 (갱신 대상 2컬럼만 제외)
_SNAPSHOT_IMMUTABLE_COLS = (
    "id",
    "result_id",
    "rcept_no",
    "fiscal_year",
    "current_assets",
    "noncurrent_assets",
    "total_assets",
    "current_liab",
    "noncurrent_liab",
    "total_liab",
    "total_equity",
    "revenue",
    "cogs",
    "gross_profit",
    "sga",
    "operating_income",
    "net_income",
    "cf_operating",
    "cf_investing",
    "cf_financing",
    "cf_ending_cash",
    "non_operating_income",
    "non_operating_expense",
    "parse_status",
    "parse_note",
    "from_current_period",
)


def _db_path(settings) -> Path:
    """settings.database_url(sqlite:///...)에서 실제 파일 경로를 뽑는다."""
    url = settings.database_url
    if not url.startswith("sqlite:///"):
        raise SystemExit(f"이 스크립트는 SQLite 전용이다: {url}")
    return Path(url[len("sqlite:///") :])


def _backup_db(db_path: Path) -> Path:
    """sqlite 온라인 백업 API로 일관 스냅샷을 만든다(WAL 내용 포함).

    단순 파일 복사는 WAL 모드에서 아직 체크포인트되지 않은 변경을 놓칠 수 있어
    `sqlite3.Connection.backup()`을 쓴다.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = db_path.with_name(f"{_BACKUP_PREFIX}{stamp}")
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    if not dest.exists() or dest.stat().st_size == 0:
        raise SystemExit(f"백업 생성 실패: {dest}")
    return dest


def _latest_backup(db_path: Path) -> Path | None:
    candidates = sorted(db_path.parent.glob(f"{_BACKUP_PREFIX}*"))
    return candidates[-1] if candidates else None


# --------------------------------------------------------------------------- #
# 검증
# --------------------------------------------------------------------------- #
def _verify(db_path: Path, baseline: Path) -> int:
    """백업본(baseline) 대비 "감사인 2컬럼 외에는 아무것도 안 바뀌었는지" 검증.

    반환값: 치명 문제 건수(0이어야 정상).
    """
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    con.execute("ATTACH DATABASE ? AS base", (str(baseline),))
    cur = con.cursor()
    problems = 0

    print("=" * 70)
    print(f"검증 대상 DB : {db_path}")
    print(f"기준 백업본  : {baseline}")
    print("-" * 70)

    # 1) financial_snapshots: auditor_name 외 전 컬럼 드리프트
    cols = ", ".join(_SNAPSHOT_IMMUTABLE_COLS)
    drift = cur.execute(
        f"SELECT COUNT(*) FROM ("
        f"  SELECT {cols} FROM main.financial_snapshots"
        f"  EXCEPT SELECT {cols} FROM base.financial_snapshots)"
    ).fetchone()[0]
    print(f"financial_snapshots 다른 컬럼 드리프트(행): {drift}")
    problems += drift

    # 행 수 자체가 변하지 않았는지
    n_main = cur.execute("SELECT COUNT(*) FROM main.financial_snapshots").fetchone()[0]
    n_base = cur.execute("SELECT COUNT(*) FROM base.financial_snapshots").fetchone()[0]
    print(f"financial_snapshots 행 수: {n_base} -> {n_main}")
    problems += 0 if n_main == n_base else 1

    # auditor_name 전이 (NULL->값만 있어야 하고, 값->다른 값/값->NULL은 회귀)
    trans = cur.execute(
        "SELECT CASE WHEN b.auditor_name IS NULL THEN 'NULL' ELSE 'val' END,"
        "       CASE WHEN m.auditor_name IS NULL THEN 'NULL' ELSE 'val' END,"
        "       COUNT(*)"
        " FROM main.financial_snapshots m JOIN base.financial_snapshots b ON m.id = b.id"
        " GROUP BY 1, 2"
    ).fetchall()
    print("auditor_name 전이(base -> main):")
    for old, new, cnt in trans:
        print(f"  {old} -> {new}: {cnt}")
        if old == "val" and new == "NULL":
            problems += cnt
    regress = cur.execute(
        "SELECT COUNT(*) FROM main.financial_snapshots m JOIN base.financial_snapshots b ON m.id = b.id"
        " WHERE b.auditor_name IS NOT NULL AND m.auditor_name IS NOT b.auditor_name"
    ).fetchone()[0]
    print(f"  값->다른 값 회귀: {regress}")
    problems += regress

    # 전기 유래(from_current_period=0) 행에 값이 들어가지 않았는지 (설계 위반)
    bad_prv = cur.execute(
        "SELECT COUNT(*) FROM main.financial_snapshots"
        " WHERE from_current_period = 0 AND auditor_name IS NOT NULL"
    ).fetchone()[0]
    print(f"  from_current_period=0 인데 이름이 채워진 행(설계 위반): {bad_prv}")
    problems += bad_prv

    # 2) results: auditor_changed 외 전 컬럼 드리프트
    print("-" * 70)
    result_cols = [
        row[1]
        for row in cur.execute("PRAGMA main.table_info(results)").fetchall()
        if row[1] != "auditor_changed"
    ]
    rcols = ", ".join(result_cols)
    rdrift = cur.execute(
        f"SELECT COUNT(*) FROM ("
        f"  SELECT {rcols} FROM main.results"
        f"  EXCEPT SELECT {rcols} FROM base.results)"
    ).fetchone()[0]
    print(f"results 다른 컬럼 드리프트(행): {rdrift}")
    problems += rdrift

    rn_main = cur.execute("SELECT COUNT(*) FROM main.results").fetchone()[0]
    rn_base = cur.execute("SELECT COUNT(*) FROM base.results").fetchone()[0]
    print(f"results 행 수: {rn_base} -> {rn_main}")
    problems += 0 if rn_main == rn_base else 1

    dist = cur.execute(
        "SELECT auditor_changed, COUNT(*) FROM main.results GROUP BY 1 ORDER BY 1"
    ).fetchall()
    print("results.auditor_changed 분포(전체 행 기준):")
    for val, cnt in dist:
        label = {1: "변동 있음", 0: "변동 없음", None: "판정 불가(NULL)"}[val]
        print(f"  {val} ({label}): {cnt}")
    hist_dist = cur.execute(
        "SELECT r.auditor_changed, COUNT(*) FROM main.results r"
        " WHERE EXISTS (SELECT 1 FROM main.financial_snapshots s WHERE s.result_id = r.id)"
        " GROUP BY 1 ORDER BY 1"
    ).fetchall()
    print("  (이력 보유 회사만): " + ", ".join(f"{v}={c}" for v, c in hist_dist))
    con.close()

    # 3) 연도별 플래그와 회사 단위 캐시의 정합성:
    #    "연도별 auditor_change_flags에 True가 하나라도 있다" == "auditor_changed = 1"
    Session = get_session_factory()
    with Session() as db:
        rows = db.execute(
            select(
                FinancialSnapshot.result_id,
                FinancialSnapshot.fiscal_year,
                FinancialSnapshot.auditor_name,
            ).order_by(FinancialSnapshot.result_id, FinancialSnapshot.fiscal_year)
        ).all()
        flags_by_result: dict[int, list[str | None]] = defaultdict(list)
        for result_id, _year, name in rows:
            flags_by_result[result_id].append(name)
        mismatch = 0
        for result_id, names in flags_by_result.items():
            any_change = any(flag is True for flag in auditor_change_flags(names))
            result = db.get(Result, result_id)
            if result is None:
                continue
            if any_change != (result.auditor_changed == 1):
                mismatch += 1
    print("-" * 70)
    print(f"연도별 플래그 vs auditor_changed 캐시 모순: {mismatch}")
    problems += mismatch

    print("-" * 70)
    print(f"검증 결과: {'정상(문제 0)' if problems == 0 else f'문제 {problems}건'}")
    return problems


# --------------------------------------------------------------------------- #
# 백필
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="변경 예정만 집계하고 DB에 쓰지 않음")
    ap.add_argument("--verify", action="store_true", help="백업본 대비 드리프트/정합성 검증만 수행(쓰기 없음)")
    ap.add_argument("--baseline", type=str, default=None, help="--verify 기준 백업본 경로(기본: 가장 최근 백업)")
    ap.add_argument("--limit", type=int, default=None, help="처리 건수 상한(디버그용)")
    args = ap.parse_args()

    settings = get_settings()
    db_path = _db_path(settings)
    cache_root = Path(settings.document_cache_dir)
    Session = get_session_factory()

    if args.verify:
        baseline = Path(args.baseline) if args.baseline else _latest_backup(db_path)
        if baseline is None or not baseline.exists():
            raise SystemExit("기준 백업본을 찾을 수 없다 — --baseline 으로 지정하라.")
        return 1 if _verify(db_path, baseline) else 0

    backup_path: Path | None = None
    if not args.dry_run:
        backup_path = _backup_db(db_path)
        print(f"백업 생성: {backup_path}")

    # 대상: 당기 유래 행 중 아직 이름이 없는 행(멱등 — 이미 채워진 행은 제외).
    with Session() as db:
        rows = db.execute(
            select(FinancialSnapshot.id, FinancialSnapshot.rcept_no, FinancialSnapshot.result_id)
            .where(
                FinancialSnapshot.rcept_no.is_not(None),
                FinancialSnapshot.from_current_period == 1,
                FinancialSnapshot.auditor_name.is_(None),
            )
            .order_by(FinancialSnapshot.id)
        ).all()
    if args.limit:
        rows = rows[: args.limit]

    stats = Counter()
    stats["target_rows"] = len(rows)
    missing_cache_rcepts: set[str] = set()
    filled_examples: list[str] = []
    # dry-run에서도 회사 단위 판정 분포를 예측하기 위해 채울 이름을 모아 둔다.
    predicted_names: dict[int, list[str | None]] = defaultdict(list)
    name_cache: dict[str, str | None] = {}

    for snap_id, rcept_no, result_id in rows:
        target_dir = cache_root / rcept_no
        doc_path = _pick_document_file(target_dir) if target_dir.is_dir() else None
        if doc_path is None:
            stats["missing_cache"] += 1
            missing_cache_rcepts.add(rcept_no)
            continue
        if doc_path.suffix.lower() != ".xml":
            # 감사인 추출은 STEP5/STEP7과 동일하게 XML 원문에서만 한다(PDF 미지원).
            stats["non_xml_skipped"] += 1
            continue

        if rcept_no not in name_cache:
            try:
                name_cache[rcept_no] = _safe_extract_auditor_name(doc_path.read_bytes())
            except Exception as exc:  # noqa: BLE001 - 원문 서식 편차는 회사마다 크다
                stats["parse_exception"] += 1
                print(f"  [예외] rcept={rcept_no}: {exc}")
                name_cache[rcept_no] = None
        name = name_cache[rcept_no]

        if name is None:
            # 원문에 감사인 서명란이 없는 경우(의견거절/미첨부 등) — 진성 결측.
            stats["genuine_missing"] += 1
            continue

        predicted_names[result_id].append(name)
        stats["rows_filled"] += 1
        if len(filled_examples) < 30:
            filled_examples.append(f"snap_id={snap_id} rcept={rcept_no} name={name!r}")

        if not args.dry_run:
            with Session() as db:
                snap = db.get(FinancialSnapshot, snap_id)
                if snap is None:
                    continue
                snap.auditor_name = name
                db.commit()

    # --- 회사 단위 `results.auditor_changed` 갱신 ---
    # 실제 갱신 모드에서는 파이프라인의 `_update_auditor_change_flag()`를 그대로
    # 호출한다(비교 규칙을 여기서 다시 만들지 않는다). dry-run에서는 쓰기를 하지
    # 않으므로 같은 `_auditor_key()`로 분포만 예측한다.
    with Session() as db:
        history_result_ids = [
            rid
            for (rid,) in db.execute(
                select(FinancialSnapshot.result_id)
                .where(FinancialSnapshot.result_id.is_not(None))
                .distinct()
                .order_by(FinancialSnapshot.result_id)
            ).all()
        ]

    changed_dist = Counter()
    if args.dry_run:
        with Session() as db:
            existing_names: dict[int, list[str]] = defaultdict(list)
            for rid, nm in db.execute(
                select(FinancialSnapshot.result_id, FinancialSnapshot.auditor_name).where(
                    FinancialSnapshot.auditor_name.is_not(None)
                )
            ).all():
                existing_names[rid].append(nm)
        for rid in history_result_ids:
            names = existing_names.get(rid, []) + [
                n for n in predicted_names.get(rid, []) if n is not None
            ]
            keys = {k for k in (_auditor_key(n) for n in names) if k}
            if len(keys) >= 2:
                changed_dist[1] += 1
            elif len(keys) == 1 and len(names) >= 2:
                changed_dist[0] += 1
            else:
                changed_dist[None] += 1
    else:
        for rid in history_result_ids:
            _update_auditor_change_flag(Session, rid)
        with Session() as db:
            for rid in history_result_ids:
                result = db.get(Result, rid)
                if result is not None:
                    changed_dist[result.auditor_changed] += 1

    print("=" * 70)
    print(f"모드: {'DRY-RUN (쓰기 없음)' if args.dry_run else '실제 갱신'}")
    if backup_path:
        print(f"백업: {backup_path}")
    print(f"대상 행(from_current_period=1 AND auditor_name IS NULL): {stats['target_rows']}")
    print(f"캐시 결측(스킵): {stats['missing_cache']}  (distinct rcept: {len(missing_cache_rcepts)})")
    print(f"XML 아님(스킵): {stats['non_xml_skipped']}")
    print(f"파싱 예외: {stats['parse_exception']}")
    print(f"원문에 감사인 없음(진성 결측, NULL 유지): {stats['genuine_missing']}")
    print("-" * 70)
    print(f"auditor_name NULL->값 채운 행: {stats['rows_filled']}")
    print("-" * 70)
    print(f"이력 보유 회사(results): {len(history_result_ids)}")
    print("results.auditor_changed 판정 분포(이력 보유 회사 기준):")
    print(f"  변동 있음(1)      : {changed_dist[1]}")
    print(f"  변동 없음(0)      : {changed_dist[0]}")
    print(f"  판정 불가(NULL)   : {changed_dist[None]}")
    print("-" * 70)
    print("채운 예시(최대 30건):")
    for ex in filled_examples:
        print("  " + ex)
    if backup_path:
        print("-" * 70)
        print(f"검증: python -m scripts.backfill_auditor_names --verify --baseline {backup_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
