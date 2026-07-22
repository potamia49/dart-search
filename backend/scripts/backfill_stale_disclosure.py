""""1년 이내 미공시 회사 제외" 판정을 기존 완료 Job의 results에 소급 반영하는
일회성 유틸리티.

배경(2026-07-22): `results.excluded_by_stale_disclosure`/`latest_disclosure_date`
(2026-07-21 추가, 실사례 "주식회사 유진")는 컬럼 추가 시점 이후 실행되는 Phase 2
Job(`_backfill_latest_rcept_no_for_job`)에서만 채워지고, 그보다 먼저 완료된 Job의
기존 행은 기본값 0/NULL로 남아 있었다(CLAUDE.md의 "컬럼 추가만, 소급 재파싱 없음"
관행). 사용자가 이번 건에 한해 명시적으로 소급 반영을 승인해 작성했다.

원칙(reparse_local_cache.py와 동일한 패턴을 따름):
- **API 호출 0건.** `_disclosure_date_from_rcept_no()`/`_is_disclosure_stale()`는
  이미 `results.rcept_no`에 저장된 접수번호 앞 8자리에서 접수일자를 뽑아 판정하는
  순수 함수라, 저장된 DB 값만으로 재계산이 가능하다(`_backfill_latest_rcept_no_for_job`가
  Phase 2 실행 당시 list.json으로 찾은 latest_rcept_no를 그대로 `results.rcept_no`에
  저장해 뒀기 때문 — 이 스크립트가 다시 list.json을 호출할 필요가 없다).
- 새 판정 로직을 여기서 만들지 않는다 — `app.core.pipeline`의
  `_disclosure_date_from_rcept_no`/`_is_disclosure_stale`를 그대로 재사용한다.
- 갱신 대상 컬럼은 `latest_disclosure_date`/`excluded_by_stale_disclosure` 두
  개뿐이다. 다른 필드(`parse_status`, `revenue_cur` 등)는 건드리지 않는다.
- 대상 범위: `jobs.phase == 'FINANCIALS' AND jobs.status == 'DONE'`인 Job의
  results 중 `rcept_no IS NOT NULL AND parse_status IS NOT NULL`인 행(작성 시점
  기준 job_id 22/24/25/26, 1,211행). `rcept_no IS NULL`(감사보고서 원문을 아예
  찾지 못한 858행)은 이 스크립트의 대상이 아니다 — 그 케이스의 소급 처리는 별도
  판단이 필요해 이번 승인 범위에 포함하지 않는다.

사용법:
    python -m scripts.backfill_stale_disclosure --dry-run   # 변경 예정만 집계(쓰기 없음)
    python -m scripts.backfill_stale_disclosure             # 실제 갱신
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

# backend 루트를 import path에 추가 (스크립트를 직접 실행하는 경우 대비)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.db import get_session_factory  # noqa: E402
from app.core.pipeline import _disclosure_date_from_rcept_no, _is_disclosure_stale  # noqa: E402
from app.models.job import Job, JobPhase, JobStatus  # noqa: E402
from app.models.result import Result  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="변경 예정만 집계하고 DB에 쓰지 않음")
    args = ap.parse_args()

    Session = get_session_factory()

    with Session() as db:
        target_job_ids = [
            job_id
            for (job_id,) in db.execute(
                select(Job.id).where(
                    Job.phase == JobPhase.FINANCIALS, Job.status == JobStatus.DONE
                )
            ).all()
        ]
        rows = db.execute(
            select(Result.id, Result.job_id, Result.corp_code, Result.corp_name, Result.rcept_no)
            .where(
                Result.job_id.in_(target_job_ids),
                Result.rcept_no.is_not(None),
                Result.parse_status.is_not(None),
            )
            .order_by(Result.id)
        ).all()

    print(f"대상 Job: {target_job_ids}")
    print(f"대상 행: {len(rows)}")

    stats = Counter()
    transitions = Counter()  # (old_excluded, new_excluded)
    changed_examples: list[str] = []

    for result_id, job_id, corp_code, corp_name, rcept_no in rows:
        new_date = _disclosure_date_from_rcept_no(rcept_no)
        new_excluded = 1 if _is_disclosure_stale(new_date) else 0

        with Session() as db:
            result = db.get(Result, result_id)
            if result is None:
                continue
            old_date = result.latest_disclosure_date
            old_excluded = result.excluded_by_stale_disclosure

            changed = old_date != new_date or old_excluded != new_excluded
            if changed:
                stats["rows_changed"] += 1
                transitions[(old_excluded, new_excluded)] += 1
                if len(changed_examples) < 40:
                    changed_examples.append(
                        f"id={result_id} job={job_id} corp_code={corp_code} corp_name={corp_name} "
                        f"rcept_no={rcept_no} date:{old_date}->{new_date} "
                        f"excluded:{old_excluded}->{new_excluded}"
                    )
                if not args.dry_run:
                    result.latest_disclosure_date = new_date
                    result.excluded_by_stale_disclosure = new_excluded
                    db.commit()
            stats["rows_checked"] += 1

    print("=" * 70)
    print(f"모드: {'DRY-RUN (쓰기 없음)' if args.dry_run else '실제 갱신'}")
    print(f"확인한 행: {stats['rows_checked']}")
    print(f"변경된 행: {stats['rows_changed']}")
    print("-" * 70)
    print("excluded_by_stale_disclosure 전환 (old -> new):")
    for (o, n), cnt in sorted(transitions.items(), key=lambda x: -x[1]):
        print(f"  {o} -> {n}: {cnt}")
    print("-" * 70)
    print("변경 예시(최대 40건):")
    for ex in changed_examples:
        print("  " + ex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
