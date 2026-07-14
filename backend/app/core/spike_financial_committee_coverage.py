"""M1 스파이크: 금융위원회_기업기본정보 API 커버리지 실측.

============================================================================
 실행 금지 안내 (지금 실행하지 말 것)
============================================================================
이 스크립트는 **DART_API_KEY와 DATA_GO_KR_API_KEY가 모두 발급된 이후에만**
실행 의미가 있다. 현재 저장소에는 두 키 모두 없는 상태이므로 지금 실행하면
DartApiKeyMissingError로 즉시 실패한다 (의도된 동작).

키 발급 후 아래처럼 실행하라:

    cd backend
    python -m app.core.spike_financial_committee_coverage

============================================================================
 목적 (상세개발계획.md §4-1, §8 M1 체크리스트)
============================================================================
OpenDART에는 지역 검색이 없어, 공공데이터포털 금융위원회_기업기본정보 API로
주소 DB를 일괄 구축해 지역 후보를 사전에 추리는 "대응 1"을 우선 검증해야
한다. 이 API가 **소형 외감법인(감사보고서만 제출, 사업보고서 미제출)까지도
커버하는지가 관건**이다. 상장사/사업보고서 제출 대상 위주로만 존재한다면
대응 1은 무의미하고, 곧바로 "대응 2(corp_profiles 전역 캐시)"로 가야 한다.

============================================================================
 절차
============================================================================
1. corp_cache(corpCode.xml)가 없으면 갱신한다 (STEP 1 재사용).
2. corp_cache에서 상장사(stock_code 있음)를 제외한 후보를 무작위로 뽑아
   DART company.json으로 실제 주소를 확인한다 (그라운드 트루스 확보).
   -> 목표 시도(target_sido, 기본 "경상남도")에 해당하는 회사가
      sample_size(기본 20)건 모일 때까지 반복한다.
   -> 이 과정 자체가 DART 쿼터를 소모하지만, 이는 정상적인 corp_profiles
      캐시 적재(STEP 3)와 동일한 성격의 호출이라 낭비가 아니다.
3. 확인된 경남 소재 회사명으로 금융위 API(getCorpBasicInfo)를 조회해,
   같은 회사가 검색되고 주소가 일치하는지 확인한다.
4. 커버리지율 = (금융위 API에서 매칭된 건수) / (그라운드 트루스 표본 수)
   을 계산해 출력한다.

============================================================================
 채택 기준 (제안, 실측 후 팀 판단으로 확정)
============================================================================
- 커버리지율 >= 80% : 대응 1(금융위 API 주소 DB) 채택
- 커버리지율 < 80%  : 대응 2(corp_profiles 전역 캐시)로 폴백
- 실측 결과와 채택 결정은 CLAUDE.md / 상세개발계획.md §4-1, §9에 반드시
  반영할 것 (스파이크 결과에 따라 설계가 바뀌는 지점).
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.corp_cache import refresh_corp_cache
from app.core.dart_client import DartClient, FscCorpInfoClient
from app.core.db import create_all_tables, get_session_factory
from app.core.filters import normalize_corp_name
from app.models.corp_cache import CorpCache

logger = logging.getLogger(__name__)


@dataclass
class CoverageSpikeResult:
    target_sido: str
    sample_size_target: int
    ground_truth: list[dict] = field(default_factory=list)  # 경남 소재로 확인된 회사
    matched: list[dict] = field(default_factory=list)       # 금융위 API에서도 확인된 회사
    unmatched: list[dict] = field(default_factory=list)     # 금융위 API 미확인
    dart_calls_used: int = 0

    @property
    def coverage_rate(self) -> float:
        if not self.ground_truth:
            return 0.0
        return len(self.matched) / len(self.ground_truth)


async def _find_ground_truth_sample(
    dart_client: DartClient,
    target_sido: str,
    sample_size: int,
    max_dart_calls: int,
) -> list[dict]:
    """corp_cache에서 무작위 후보를 뽑아 company.json으로 target_sido 소재 회사를 찾는다.

    비상장(stock_code 없음) 후보만 사용한다 — 상장사는 커버리지 검증 목적상
    의미가 적다 (금융위 API는 상장/사업보고서 제출 대상은 당연히 커버할 것이므로).
    """
    session_factory = get_session_factory()
    with session_factory() as db:
        candidates = [
            row[0]
            for row in db.execute(
                select(CorpCache.corp_code).where(CorpCache.stock_code.is_(None))
            ).all()
        ]

    random.shuffle(candidates)

    found: list[dict] = []
    calls_used = 0
    for corp_code in candidates:
        if len(found) >= sample_size or calls_used >= max_dart_calls:
            break
        try:
            company = await dart_client.get_company(corp_code)
        except Exception as exc:  # noqa: BLE001 - 스파이크 스크립트는 개별 실패를 넘어간다
            logger.warning("company.json 조회 실패 corp_code=%s: %s", corp_code, exc)
            calls_used += 1
            continue
        calls_used += 1
        address = (company.get("adres") or "").strip()
        if target_sido in address or target_sido.replace("특별자치도", "").replace(
            "도", ""
        ) in address:
            found.append(
                {
                    "corp_code": corp_code,
                    "corp_name": company.get("corp_name"),
                    "address": address,
                    "phone": company.get("phn_no"),
                    "ceo_name": company.get("ceo_nm"),
                }
            )

    logger.info(
        "그라운드 트루스 표본 수집 완료: %s건 확인 (DART 호출 %s건 소모)",
        len(found),
        calls_used,
    )
    return found


async def run_coverage_spike(
    target_sido: str = "경상남도",
    sample_size: int = 20,
    max_dart_calls: int = 500,
) -> CoverageSpikeResult:
    """금융위 API 커버리지 스파이크 실행. 반드시 키 발급 후에만 호출할 것."""
    create_all_tables()

    result = CoverageSpikeResult(target_sido=target_sido, sample_size_target=sample_size)

    async with DartClient() as dart_client, FscCorpInfoClient() as fsc_client:
        # STEP 1 재사용: corp_cache가 없거나 오래되었으면 먼저 갱신
        await refresh_corp_cache(dart_client)

        ground_truth = await _find_ground_truth_sample(
            dart_client, target_sido, sample_size, max_dart_calls
        )
        result.ground_truth = ground_truth

        for company in ground_truth:
            corp_nm_norm = normalize_corp_name(company["corp_name"] or "")
            try:
                fsc_data = await fsc_client.get_corp_basic_info(
                    page_no=1, num_of_rows=5, corp_nm=corp_nm_norm
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("금융위 API 조회 실패 corp_name=%s: %s", corp_nm_norm, exc)
                result.unmatched.append(company)
                continue

            # NOTE: 실제 응답 스키마는 발급 후 확인 필요. 아래는 잠정 판정 로직이며
            # 실측 시 응답 필드명에 맞게 조정해야 한다.
            items = (
                fsc_data.get("response", {})
                .get("body", {})
                .get("items", [])
            )
            if items:
                result.matched.append(company)
            else:
                result.unmatched.append(company)

    return result


def print_report(result: CoverageSpikeResult) -> None:
    print("=" * 70)
    print(f"금융위 API 커버리지 스파이크 결과 (target_sido={result.target_sido})")
    print("=" * 70)
    print(f"그라운드 트루스 표본 수: {len(result.ground_truth)} / 목표 {result.sample_size_target}")
    print(f"금융위 API 매칭 건수  : {len(result.matched)}")
    print(f"미매칭 건수           : {len(result.unmatched)}")
    print(f"커버리지율            : {result.coverage_rate:.1%}")
    print("-" * 70)
    if result.coverage_rate >= 0.8:
        print("판정: 커버리지 충분 → 대응 1(금융위 API 주소 DB) 채택 검토")
    else:
        print("판정: 커버리지 부족 → 대응 2(corp_profiles 전역 캐시)로 폴백 검토")
    print("=" * 70)
    print("※ 이 결과와 채택 결정은 CLAUDE.md / 상세개발계획.md §4-1, §9에 반영할 것.")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(run_coverage_spike())
    print_report(result)


if __name__ == "__main__":
    main()
