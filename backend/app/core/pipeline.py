"""수집 파이프라인 오케스트레이션 (Job 실행).

상세개발계획.md §4 STEP 0~7을 구현한다(STEP 1~4는 M2, STEP 5~6은 M3,
STEP 7은 2026-07-15 추가된 "최근 N년 재무이력" 확장).

각 STEP은 `jobs.current_step`/`progress_done`/`progress_total`을 DB에
체크포인트로 남겨 중단 후 이어하기(resume)가 가능해야 한다
(CLAUDE.md 핵심 제약 5번). `dart_client.QuotaExceededError` 발생 시
Job.status를 PAUSED_QUOTA로 전환하고 그 시점까지의 진행 상태를 보존한다
(CLAUDE.md 핵심 제약 4번).

| STEP | 내용 | 사용 API |
|---|---|---|
| 0 | 조건 입력 검증, Job 생성 | - (app/api/jobs.py 책임) |
| 1 | corp_cache 확인/갱신 | corpCode.xml (app/core/corp_cache.py) |
| 2 | 외부감사관련(pblntf_ty=F) 공시 목록 페이징 수집 | list.json |
| 3 | 지역 사전 추림(금융위 API) + 기업개황 확정 + corp_profiles 캐시 적재 | 금융위 기업기본정보, company.json (대응 1) |
| 4 | 감사보고서 원본 다운로드 (zip 해제, 형식 판별) | document.xml |
| 5 | 재무제표 파싱(당기/전기 13항목) + 감사의견 추출 | - (app/parsers, M3 구현 완료) |
| 6 | 매출액 범위 사후 필터 | - (M3 구현 완료) |
| 7 | 최근 N년 재무 이력 수집 (excluded_by_revenue=0인 최종 결과만 대상) | list.json(corp_code 지정) + document.xml (2026-07-15 추가) |

### STEP 7 설계 메모 (2026-07-15 추가 — "최근 N년치 재무정보 이력")

- **"필터 통과 후에만" 원칙**: STEP 3(FSC 사전 추림)와 같은 철학으로, STEP 7은
  전체 후보가 아니라 STEP 1~6을 다 통과해 `results`에 남아 있고
  `excluded_by_revenue=0`인 회사만 대상으로 한다 — 쿼터 영향이 최종 결과
  건수에만 비례한다.
- **실측(2026-07-15): `list.json`에 `corp_code`를 지정하면 3개월 조회기간
  제한이 사라진다.** STEP 2가 겪은 "corp_code 없이는 90일 제한"(위 §4-1
  근처 설명)과 달리, corp_code를 지정한 조회는 10년 범위(`bgn_de=20160101`
  ~`end_de=20260630`)도 한 번에 성공했다(응답 5건, 2021~2025 회계연도
  감사보고서). 따라서 STEP 7은 `_split_period_into_windows()`를 사용하지
  않고 단일 기간으로 그 회사의 list.json을 조회한다.
- **처리 순서는 최신 공시 → 과거 공시 순(newest-first)이다** — 최초 요청
  문서는 "오래된 것부터"를 제안했지만, "목표 연도수만큼 모이면 그만
  찾는다"는 조기 중단 조건과 결합하면 oldest-first는 정작 가장 최근 연도를
  놓칠 수 있다(예: N=4년치를 채우려는데 오래된 보고서부터 훑다가 4개
  연도를 다 채우면 가장 최신 보고서를 아예 열어보지 않게 됨 — "최근 N년"이라는
  기능 취지에 반한다). newest-first로 훑어야 항상 최근 연도부터 확정되고,
  중복되는 연도(정정 공시 포함)는 "이미 있으면 건너뜀" 규칙으로 자동으로
  최신 rcept_no 값이 유지된다.
- **연도별 값은 "그 연도를 당기로 하는" 공시를 우선한다 (2026-07-20 변경)** —
  newest-first 순회에서 어떤 연도는 다음 연도 공시의 **전기** 열로 먼저 채워지는데,
  그 상태로 두면 화면의 연도별 "원문 보기"가 여는 원문(그 행의 rcept_no)의 당기가
  표시 연도와 어긋난다(예: 2024년 열의 원문을 열면 당기가 2025년). 그래서 전기
  유래 값은 `from_current_period=0`으로 표시해 두고, 이후 그 연도의 자기 공시를
  열면 값·rcept_no·parse_status를 통째로 덮어쓴다. 결과적으로 목표 N개 연도마다
  자기 공시 1건씩(총 N건 안팎)을 내려받게 된다 — 변경 전(N-1건)보다 문서
  다운로드가 회사당 최대 1건 늘지만, 표시 수치와 원문이 항상 같은 연도를 가리킨다.
  가장 오래된 연도처럼 자기 공시를 끝내 못 찾은 연도는 전기 유래 값이 그대로
  남고(화면에 "전기 기준" 표시), 목표 연도를 다 채운 뒤 더 오래된 공시만 남으면
  즉시 중단해 헛다운로드를 막는다.
- **회계연도(fiscal_year) 판정**: 원문에는 당기 결산기준일(PERIODTO)만 있고
  전기 결산기준일은 별도 마커가 없다 — 당기 연도는 PERIODTO 연도 그대로,
  전기 연도는 "당기 연도 - 1"로 계산한다(연 1회 정기감사 가정,
  `app/models/financial_snapshot.py` 참고).
- **조회 기간(`bgn_de`)은 목표 연도수(N)의 N/2+2년 전 1/1로 잡는다** — 결산월이
  회사마다 다르고 감사보고서 제출 시점도 매년 정확히 같지 않을 수 있어 여유를
  둔다(`_history_window()`).
- **다운로드/파싱은 STEP 4/5 로직을 100% 재사용한다** — 새 파서를 만들지
  않고 `parse_xml_financials`/`parse_pdf_financials`/`_extract_fiscal_date`를
  그대로 호출한다. document.xml 다운로드는 STEP 4와 동일한
  `DOCUMENT_CACHE_DIR` 로컬 캐시를 공유한다(`_ensure_document_cached()`로
  STEP 4/7이 공유하도록 추출).
- **resume**: 회사(result_id)별로 이미 `financial_snapshots`에 쌓인 distinct
  fiscal_year 수가 `history_years` 이상이면 그 회사는 list.json 호출조차
  하지 않고 건너뛴다. STEP 2/3처럼 list.json 자체는 호출 비용이 낮아
  resume 시 다시 호출해도 무방하다 — 실제로 비용이 큰 document.xml
  다운로드만 `DOCUMENT_CACHE_DIR` 로컬 캐시로 진짜 resume된다.

### M3 실측 메모 (원문 서식 분포)

`backend/tests/fixtures/manifest.json`(2026-07-15 실제 DART API로 확보, 25건
+ 2012년 원문 5건 = 30건)를 실측한 결과 **전부 XML**이었다 — 최근(2026년
4~6월) 분기 보고서는 물론 2012년 초 원문까지도 document.xml API가 XML로
반환했다. `pdf_parser.py`는 실제 PDF 표본으로 검증하지 못한 best-effort
구현이며, HWP는 여전히 미구현(실패 기록만) 상태다. 감사의견 분포는 적정
15건, 한정 2건, 의견거절 10건(재무제표 자체가 첨부되지 않음), 부적정은
표본에 없었다.

### resume 설계 메모 (상세개발계획.md §5는 후보 목록을 위한 별도 테이블을 두지
않으므로, STEP별로 "이어하기"의 의미가 다르다는 점을 명시해 둔다):

- STEP 1(corp_cache)과 STEP 2(공시 목록 조회)는 멱등(idempotent)이고 상대적으로
  호출 비용이 낮다(각각 TTL 체크 1회, list.json 페이지 수만큼). resume 시 이
  두 STEP은 항상 처음부터 다시 실행한다 — corp_cache는 TTL 이내면 즉시
  스킵되고, list.json 재조회는 안전하며 후보 목록을 다시 만드는 데 필요하다
  (후보 목록 자체는 DB에 영구 저장하지 않는다).
- STEP 3(기업개황 확정)과 STEP 4(원문 다운로드)가 실제로 비용이 큰 구간이며,
  이 두 STEP의 "이어하기"는 각각 `corp_profiles` 전역 캐시(TTL 기반)와
  `DOCUMENT_CACHE_DIR`의 로컬 파일 캐시로 구현된다 — 이미 처리된 회사/문서는
  API를 재호출하지 않는다. `results` 테이블에도 이미 삽입된 corp_code는
  중복 삽입하지 않는다.
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.core.corp_cache import refresh_corp_cache
from app.core.dart_client import DartApiError, DartClient, FscCorpInfoClient, QuotaExceededError
from app.core.dart_corp_index import filter_local_candidates, is_dart_index_stale
from app.core.db import get_session_factory
from app.core.filters import (
    industry_matches,
    normalize_corp_name,
    parse_address,
    region_matches,
    revenue_matches,
)
from app.core.fsc_financial_stat import get_latest_stat_by_crno
from app.models.corp_cache import CorpCache
from app.models.corp_profile import CorpProfile
from app.models.dart_corp_index import DartCorpIndex
from app.models.financial_snapshot import FinancialSnapshot
from app.models.job import Job, JobPhase, JobStatus
from app.models.result import ParseStatus, Result
from app.parsers.audit_opinion import extract_audit_opinion
from app.parsers.auditor import AuditorInfo, extract_auditor
from app.parsers.base import (
    CF_FINANCIAL_FIELDS,
    DIRECT_FINANCIAL_FIELDS,
    STANDARD_FINANCIAL_FIELDS,
    ParsedFinancials,
)
from app.parsers.pdf_parser import parse_pdf_financials
from app.parsers.xml_parser import parse_xml_financials

logger = logging.getLogger(__name__)

# STEP 번호 (상세개발계획.md §4). STEP 0(Job 생성)은 app/api/jobs.py 책임이다.
STEP_CORP_CACHE = 1
STEP_DISCLOSURE_LIST = 2
STEP_REGION_INDUSTRY_FILTER = 3
STEP_DOCUMENT_DOWNLOAD = 4
STEP_PARSE_FINANCIALS = 5
STEP_REVENUE_FILTER = 6
STEP_HISTORY_COLLECTION = 7

# Phase 1(§4-7, 2026-07-15 M6 재설계) — A2~A4를 단일 스텝으로 취급해 진행률을
# 표시한다. STEP 1~3(구 파이프라인의 corp_cache/list.json/company.json 전국
# 순회)을 대체하므로 기존 STEP 번호와 겹치지 않게 새 번호를 쓴다.
STEP_PHASE1_CANDIDATES = 10

DEFAULT_HISTORY_YEARS = 4  # JobCreateRequest.history_years 기본값과 동일 (app/api/jobs.py)

_DISCLOSURE_PAGE_COUNT = 100  # list.json 1회 최대 100건
_CHECKPOINT_INTERVAL = 20  # STEP 3/4에서 N건마다 진행률 커밋 + 취소 여부 확인

_UNSET = object()  # _checkpoint()의 error_msg=None(명시적 초기화)과 미지정을 구분하기 위한 sentinel


class JobCancelledError(Exception):
    """다음 체크포인트에서 취소를 감지했을 때 STEP 루프를 즉시 빠져나오기 위한 내부 신호.

    Job.status는 API 레이어(`POST /api/jobs/{id}/cancel`)가 이미 CANCELLED로
    기록해 두었으므로, 이 예외를 잡는 쪽에서 상태를 다시 덮어쓰지 않는다.
    """


# ---------------------------------------------------------------------------
# Job 로딩 / 체크포인트 헬퍼
# ---------------------------------------------------------------------------


def _load_job(session_factory: sessionmaker[Session], job_id: int) -> Job | None:
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job is not None:
            db.expunge(job)
        return job


def _job_status(session_factory: sessionmaker[Session], job_id: int) -> str | None:
    with session_factory() as db:
        job = db.get(Job, job_id)
        return job.status if job else None


def _checkpoint(
    session_factory: sessionmaker[Session],
    job_id: int,
    *,
    status: str | None = None,
    phase: str | None = None,
    current_step: int | None = None,
    progress_done: int | None = None,
    progress_total: int | None = None,
    error_msg: str | None | object = _UNSET,
) -> None:
    """jobs 테이블에 진행 상태를 커밋 — 이것이 곧 "체크포인트"다.

    `phase`(§4-7-1, 2026-07-15 추가)는 Phase 1(`run_job_phase1`)/Phase 2
    (`run_job_phase2`)가 완료 시점에 `jobs.phase`를 명시적으로 남길 때만 쓴다.
    """
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        if status is not None:
            job.status = status
        if phase is not None:
            job.phase = phase
        if current_step is not None:
            job.current_step = current_step
        if progress_done is not None:
            job.progress_done = progress_done
        if progress_total is not None:
            job.progress_total = progress_total
        if error_msg is not _UNSET:
            job.error_msg = error_msg
        db.commit()


def _raise_if_cancelled(session_factory: sessionmaker[Session], job_id: int) -> None:
    if _job_status(session_factory, job_id) == JobStatus.CANCELLED:
        raise JobCancelledError()


# ---------------------------------------------------------------------------
# STEP 2 — 외부감사관련 공시 목록 페이징 수집
# ---------------------------------------------------------------------------


_LIST_JSON_MAX_WINDOW_DAYS = 90  # corp_code 없이 날짜만으로 조회할 때 list.json이 허용하는 최대 기간(실측: 3개월 초과 시 status=100)


def _split_period_into_windows(bgn_de: str, end_de: str, max_days: int = _LIST_JSON_MAX_WINDOW_DAYS) -> list[tuple[str, str]]:
    """`bgn_de`~`end_de`(YYYYMMDD)를 `max_days`일 이하 구간으로 분할한다.

    OpenDART list.json은 corp_code를 지정하지 않고 날짜 범위로만 검색할 경우
    조회 기간이 3개월(90일)을 넘을 수 없다(실측: 초과 시 `status=100,
    message="corp_code가 없는 경우 검색기간은 3개월만 가능합니다"`로 즉시 실패).
    달력월(month) 경계 계산(1/31 + 3개월이 4/30인지 5/1인지 등)은 엣지케이스가
    많아 보수적으로 90일 고정 폭으로 분할한다. 마지막 구간은 원래 `end_de`를
    넘지 않도록 clamp한다.
    """
    start = datetime.strptime(bgn_de, "%Y%m%d")
    end = datetime.strptime(end_de, "%Y%m%d")
    if start > end:
        raise ValueError(f"cond_period.bgn_de({bgn_de})가 end_de({end_de})보다 늦을 수 없습니다.")

    windows: list[tuple[str, str]] = []
    window_start = start
    while window_start <= end:
        window_end = min(window_start + timedelta(days=max_days - 1), end)
        windows.append((window_start.strftime("%Y%m%d"), window_end.strftime("%Y%m%d")))
        window_start = window_end + timedelta(days=1)
    return windows


async def _collect_candidates(
    dart_client: DartClient,
    session_factory: sessionmaker[Session],
    job_id: int,
    cond_period: dict[str, Any],
) -> list[dict[str, str]]:
    """STEP 2: list.json(pblntf_ty=F)을 끝까지 페이징 순회해 후보를 모은다.

    `bgn_de`~`end_de` 전체 구간이 90일을 넘으면 `_split_period_into_windows()`로
    90일 이하 구간으로 나눠 구간별로 페이징 호출한다(위 `_LIST_JSON_MAX_WINDOW_DAYS`
    설명 참고 — 상세개발계획.md §7-1 기본값인 "최근 1년" 검색이 그대로 실패하지
    않도록 하기 위한 대응, 2026-07-15 실측으로 발견).

    같은 회사가 여러 구간에 걸쳐(정정 포함) 여러 건 공시했을 수 있으므로
    `by_corp` dict를 구간 루프 바깥에 두고 corp_code 기준으로 dedup하며,
    rcept_no가 가장 큰(=가장 최근 접수된) 건을 대표로 남긴다 (rcept_no는
    "접수일자+일련번호"라 문자열 비교로 최신 판별 가능).

    진행률(`progress_done`/`progress_total`)은 페이지 단위 누적 카운트로
    관리한다 — 구간별 총 페이지 수는 그 구간을 조회하기 전까지 알 수 없으므로,
    각 구간을 "최소 1페이지"로 잡아 `progress_total`을 구간 수만큼으로
    초기화해 두고, 구간의 실제 `total_page`를 알게 되는 순간 차이만큼
    보정한다(기존 단일 구간 코드의 "우선 1로 잡고 첫 응답으로 갱신" 방식을
    구간이 여러 개인 경우로 자연스럽게 확장한 것).
    """
    bgn_de = cond_period.get("bgn_de")
    end_de = cond_period.get("end_de")
    if not bgn_de or not end_de:
        raise ValueError("cond_period.bgn_de/end_de가 필요합니다.")

    windows = _split_period_into_windows(bgn_de, end_de)

    by_corp: dict[str, dict[str, str]] = {}
    pages_done = 0
    pages_total = len(windows)  # 구간마다 최소 1페이지로 초기 추정, 실제 total_page를 알게 되면 보정

    for window_bgn, window_end in windows:
        page_no = 1
        total_page = 1

        while page_no <= total_page:
            _raise_if_cancelled(session_factory, job_id)

            data = await dart_client.get_disclosure_list(
                bgn_de=window_bgn,
                end_de=window_end,
                pblntf_ty="F",
                page_no=page_no,
                page_count=_DISCLOSURE_PAGE_COUNT,
            )
            new_total_page = int(data.get("total_page") or 1)
            if new_total_page != total_page:
                pages_total += new_total_page - total_page
                total_page = new_total_page

            for item in data.get("list") or []:
                corp_code = item.get("corp_code")
                rcept_no = item.get("rcept_no")
                if not corp_code or not rcept_no:
                    continue
                existing = by_corp.get(corp_code)
                if existing is None or rcept_no > existing["rcept_no"]:
                    by_corp[corp_code] = {
                        "corp_code": corp_code,
                        "corp_name": item.get("corp_name") or "",
                        "rcept_no": rcept_no,
                    }

            pages_done += 1
            _checkpoint(
                session_factory,
                job_id,
                current_step=STEP_DISCLOSURE_LIST,
                progress_done=pages_done,
                progress_total=pages_total,
            )
            page_no += 1

    logger.info(
        "STEP2 완료: 구간 %s개, 후보 %s개사 (job_id=%s)", len(windows), len(by_corp), job_id
    )
    return list(by_corp.values())


# ---------------------------------------------------------------------------
# STEP 3 — 지역 사전 추림 + 기업개황 확정 + corp_profiles 캐시 적재 + 필터
# ---------------------------------------------------------------------------


def _profile_is_fresh(profile: CorpProfile, ttl_days: int) -> bool:
    if profile is None or not profile.fetched_at:
        return False
    try:
        fetched = datetime.fromisoformat(profile.fetched_at)
    except ValueError:
        return False
    return datetime.now() - fetched <= timedelta(days=ttl_days)


async def _fsc_lookup_region(
    fsc_client: FscCorpInfoClient, corp_name: str
) -> tuple[str | None, str | None] | None:
    """금융위 기업기본정보 API(getCorpOutline_V2)로 회사명 조회 후 (시도, 시군구)를 반환.

    반환값 의미:
    - `None`: 매칭되는 회사가 없거나(이름 검색 결과 없음) FSC 호출 자체가
      실패했다 — 호출부는 이 경우 "보수적으로" company.json을 직접 호출해
      확정해야 한다 (상세개발계획.md §4-1, 스파이크 결과 커버리지 100%지만
      안전망으로 유지).
    - `(sido, sigungu)`: 매칭은 됐다는 뜻(주소 파싱에 실패해 둘 다 None일
      수도 있음 — 이 경우도 지역 조건이 있으면 `region_matches`가 자연히
      탈락시킨다).

    corp_name이 비어 있으면 애초에 검색 자체가 무의미하므로(빈 문자열은
    `FscCorpInfoClient`가 필터 파라미터를 아예 안 붙여 전체 목록을 반환할
    위험이 있다) FSC를 호출하지 않고 곧바로 None(미매칭 취급)을 반환한다.
    """
    corp_nm_norm = normalize_corp_name(corp_name or "")
    if not corp_nm_norm:
        return None

    try:
        fsc_data = await fsc_client.get_corp_basic_info(
            page_no=1, num_of_rows=5, corp_nm=corp_nm_norm
        )
    except Exception as exc:  # noqa: BLE001 - FSC는 DART와 별도 쿼터라 여기서만 흡수하고 폴백
        logger.warning("FSC 기업기본정보 조회 실패 corp_name=%s: %s", corp_name, exc)
        return None

    # 실측 확인된 응답 스키마(spike_financial_committee_coverage.py 참고):
    # response.body.items.item = [...] (리스트). 결과 없음일 때도 items는
    # {"item": []}로 비어있지 않은 dict라서 "if items:" 식의 truthy 판정은
    # 항상 참이 되므로, 반드시 item 리스트 자체의 길이로 매칭 여부를 판정한다.
    body = fsc_data.get("response", {}).get("body", {})
    item_list = body.get("items", {}).get("item") or []
    if isinstance(item_list, dict):  # 단건 응답 시 dict로 오는 경우 대비
        item_list = [item_list]
    if not item_list:
        return None

    address = (item_list[0].get("enpBsadr") or "").strip() or None
    return parse_address(address)


async def _resolve_candidate_profile(
    dart_client: DartClient,
    fsc_client: FscCorpInfoClient,
    session_factory: sessionmaker[Session],
    settings: Settings,
    corp_code: str,
    corp_name: str,
    cond_region: dict[str, Any],
) -> CorpProfile | None:
    """후보 1건의 지역/업종 판정에 필요한 corp_profiles 레코드를 확정한다.

    ★ 지역 사전 추림 경계 지점 (상세개발계획.md §4-1, 대응 1) ★
    1. corp_profiles 캐시에 TTL 이내로 신선한 레코드가 있으면 API 호출 없이
       그대로 재사용한다 (대응 2와 동일, 변경 없음).
    2. 캐시 미스면 DART company.json(회사당 1건, 일일 쿼터 20,000건 소모)을
       바로 부르지 않고, 먼저 금융위 기업기본정보 API(별도 쿼터, 무료)로
       회사명을 조회해 주소를 가볍게 확인한다.
       - 지역이 맞거나(cond_region이 비어 지역 필터 자체가 없는 경우 포함)
         FSC에서 매칭되지 않으면(이름 검색 결과 없음 — 보수적으로 놓치는
         것보다 낫다는 판단), company.json을 호출해 전화번호/대표자/
         업종코드까지 확정하고 corp_profiles를 풀 데이터로 upsert한다
         (기존 대응 2 로직과 동일).
       - FSC에서 매칭됐는데 지역이 명백히 다르면 company.json 호출을
         생략해 DART 쿼터를 아낀다. 대신 FSC에서 얻은 sido/sigungu만
         corp_profiles에 upsert해 두면(phone/ceo_name/induty_code는
         null) 다음 Job 실행 때도 캐시가 fresh하면 FSC/DART 모두
         재호출하지 않는다. 이 후보는 `region_matches`가 False를
         반환해 자연히 필터에서 탈락한다.

    company.json 조회가 (쿼터 초과가 아닌) 실패하면 None을 반환해 해당
    후보를 이번 STEP에서는 건너뛰게 한다 — Job 전체를 실패시키지 않는다.
    """
    with session_factory() as db:
        profile = db.get(CorpProfile, corp_code)
        if profile is not None and _profile_is_fresh(profile, settings.corp_profile_ttl_days):
            db.expunge(profile)
            return profile

    fsc_result = await _fsc_lookup_region(fsc_client, corp_name)
    if fsc_result is not None:
        fsc_sido, fsc_sigungu = fsc_result
        if not region_matches(fsc_sido, fsc_sigungu, cond_region):
            # FSC로 지역 불일치가 명백 → company.json 호출 생략, 부분 데이터만 upsert.
            now_iso = datetime.now().isoformat(timespec="seconds")
            with session_factory() as db:
                profile = db.get(CorpProfile, corp_code)
                if profile is None:
                    profile = CorpProfile(corp_code=corp_code)
                    db.add(profile)
                profile.corp_name = profile.corp_name or corp_name
                profile.sido = fsc_sido
                profile.sigungu = fsc_sigungu
                profile.fetched_at = now_iso
                db.commit()
                db.refresh(profile)
                db.expunge(profile)
                logger.info(
                    "STEP3: FSC로 지역 불일치 확인, company.json 호출 생략 corp_code=%s (sido=%s)",
                    corp_code,
                    fsc_sido,
                )
                return profile

    # 여기 도달하는 경우: (a) FSC에서 지역이 맞다고 확인됨, (b) cond_region이
    # 비어 지역 필터가 없음, (c) FSC 매칭 자체가 안 되거나 호출 실패 — 모두
    # company.json으로 직접 확정한다 (기존 대응 2 로직과 동일).
    try:
        company = await dart_client.get_company(corp_code)
    except QuotaExceededError:
        raise
    except DartApiError as exc:
        logger.warning("company.json 조회 실패 corp_code=%s: %s", corp_code, exc)
        return None

    address = (company.get("adres") or "").strip() or None
    sido, sigungu = parse_address(address)
    now_iso = datetime.now().isoformat(timespec="seconds")

    with session_factory() as db:
        profile = db.get(CorpProfile, corp_code)
        if profile is None:
            profile = CorpProfile(corp_code=corp_code)
            db.add(profile)
        profile.corp_name = company.get("corp_name")
        profile.address = address
        profile.sido = sido
        profile.sigungu = sigungu
        profile.induty_code = company.get("induty_code")
        profile.phone = company.get("phn_no")
        profile.ceo_name = company.get("ceo_nm")
        profile.fetched_at = now_iso
        db.commit()
        db.refresh(profile)
        db.expunge(profile)
        return profile


async def _run_region_industry_filter(
    dart_client: DartClient,
    fsc_client: FscCorpInfoClient,
    session_factory: sessionmaker[Session],
    settings: Settings,
    job_id: int,
    candidates: list[dict[str, str]],
    cond_region: dict[str, Any],
    cond_industry: list[str],
) -> None:
    """STEP 3: 후보 전체에 대해 지역/업종 필터를 적용하고 통과 건만 results에 선삽입."""
    total = len(candidates)
    _checkpoint(
        session_factory,
        job_id,
        current_step=STEP_REGION_INDUSTRY_FILTER,
        progress_done=0,
        progress_total=total,
    )

    with session_factory() as db:
        existing_corp_codes = {
            row[0]
            for row in db.execute(
                select(Result.corp_code).where(Result.job_id == job_id)
            ).all()
        }

    done = 0
    for candidate in candidates:
        if done % _CHECKPOINT_INTERVAL == 0:
            _raise_if_cancelled(session_factory, job_id)

        corp_code = candidate["corp_code"]
        profile = await _resolve_candidate_profile(
            dart_client,
            fsc_client,
            session_factory,
            settings,
            corp_code,
            candidate.get("corp_name") or "",
            cond_region,
        )
        done += 1

        if (
            profile is not None
            and corp_code not in existing_corp_codes
            and region_matches(profile.sido, profile.sigungu, cond_region)
            and industry_matches(profile.induty_code, cond_industry)
        ):
            with session_factory() as db:
                db.add(
                    Result(
                        job_id=job_id,
                        corp_code=corp_code,
                        rcept_no=candidate.get("rcept_no"),
                        corp_name=profile.corp_name or candidate.get("corp_name"),
                        address=profile.address,
                        phone=profile.phone,
                        ceo_name=profile.ceo_name,
                        induty_code=profile.induty_code,
                    )
                )
                db.commit()
            existing_corp_codes.add(corp_code)

        if done % _CHECKPOINT_INTERVAL == 0 or done == total:
            _checkpoint(session_factory, job_id, progress_done=done, progress_total=total)

    logger.info(
        "STEP3 완료: 후보 %s개사 중 %s개사 필터 통과 (job_id=%s)",
        total,
        len(existing_corp_codes),
        job_id,
    )


# ---------------------------------------------------------------------------
# STEP 4 — 감사보고서 원본 다운로드
# ---------------------------------------------------------------------------


def _classify_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext in ("xml", "pdf", "hwp"):
        return ext.upper()
    return ext.upper() or "UNKNOWN"


async def _ensure_document_cached(
    dart_client: DartClient, settings: Settings, rcept_no: str
) -> Path | None:
    """rcept_no 1건에 대응하는 원문을 `DOCUMENT_CACHE_DIR/{rcept_no}/`에 확보하고 그 경로를 반환.

    이미 로컬 캐시에 있으면 재다운로드하지 않는다(§9 리스크 대응, resume의
    핵심). STEP 4(`_run_document_download`)와 STEP 7(`_collect_history_for_result`)이
    이 헬퍼를 공유한다 — 다운로드/zip 해제 로직은 두 STEP에서 완전히
    동일하므로 새로 만들지 않고 추출했다. `QuotaExceededError`는 그대로
    상위로 전파하고, 그 외 다운로드/압축해제 실패는 로그만 남기고 None을
    반환한다(해당 rcept_no 1건만 건너뛰고 Job 전체를 실패시키지 않는다).
    """
    target_dir = Path(settings.document_cache_dir) / rcept_no
    if target_dir.is_dir() and any(target_dir.iterdir()):
        logger.info("원문 로컬 캐시 재사용 rcept_no=%s (%s)", rcept_no, target_dir)
        return target_dir

    try:
        zip_bytes = await dart_client.get_document(rcept_no)
    except QuotaExceededError:
        raise
    except DartApiError as exc:
        logger.warning("document.xml 다운로드 실패 rcept_no=%s: %s", rcept_no, exc)
        return None

    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(target_dir)
    except zipfile.BadZipFile:
        logger.warning("원문 zip 해제 실패 rcept_no=%s", rcept_no)
        return None

    extensions = sorted({_classify_extension(p.name) for p in target_dir.rglob("*") if p.is_file()})
    logger.info("원문 다운로드 완료 rcept_no=%s 파일형식=%s", rcept_no, extensions)
    return target_dir


async def _run_document_download(
    dart_client: DartClient,
    session_factory: sessionmaker[Session],
    settings: Settings,
    job_id: int,
) -> None:
    """STEP 4: results에 선삽입된 rcept_no 각각에 대해 감사보고서 원본을 다운로드.

    실제 다운로드/캐시 재사용/zip 해제는 `_ensure_document_cached()`가
    담당한다 — 실제 재무제표 파싱은 다음 STEP인 `_run_financial_parsing`
    (STEP 5)이 별도로 맡는다.
    """
    with session_factory() as db:
        rows = db.execute(
            select(Result.rcept_no).where(
                Result.job_id == job_id, Result.rcept_no.is_not(None)
            )
        ).all()
    rcept_nos = sorted({row[0] for row in rows if row[0]})

    total = len(rcept_nos)
    _checkpoint(
        session_factory,
        job_id,
        current_step=STEP_DOCUMENT_DOWNLOAD,
        progress_done=0,
        progress_total=total,
    )

    done = 0
    for rcept_no in rcept_nos:
        if done % _CHECKPOINT_INTERVAL == 0:
            _raise_if_cancelled(session_factory, job_id)

        await _ensure_document_cached(dart_client, settings, rcept_no)

        done += 1
        if done % _CHECKPOINT_INTERVAL == 0 or done == total:
            _checkpoint(session_factory, job_id, progress_done=done, progress_total=total)

    logger.info("STEP4 완료: 원문 %s건 처리 (job_id=%s)", total, job_id)


# ---------------------------------------------------------------------------
# STEP 5 — 재무제표 파싱(당기/전기 13항목) + 감사의견 추출
# ---------------------------------------------------------------------------

_FISCAL_DATE_RE = re.compile(r'AUNIT="PERIODTO"\s+AUNITVALUE="(\d{4})(\d{2})(\d{2})"')


def _pick_document_file(target_dir: Path) -> Path | None:
    """rcept_no 캐시 디렉터리에서 파싱 대상 원문 1개를 고른다 (XML 우선)."""
    xml_files = sorted(target_dir.rglob("*.xml"))
    if xml_files:
        return xml_files[0]
    pdf_files = sorted(target_dir.rglob("*.pdf"))
    if pdf_files:
        return pdf_files[0]
    return None


def _extract_fiscal_date(raw_text: str) -> str | None:
    """XML 커버 페이지의 PERIODTO(결산기준일) 속성에서 YYYY-MM-DD를 뽑는다."""
    match = _FISCAL_DATE_RE.search(raw_text)
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _apply_parsed_result(
    session_factory: sessionmaker[Session],
    result_id: int,
    parsed: ParsedFinancials,
    audit_opinion: str | None,
    fiscal_date: str | None,
    auditor: AuditorInfo | None = None,
) -> None:
    with session_factory() as db:
        result = db.get(Result, result_id)
        if result is None:
            return
        for f in DIRECT_FINANCIAL_FIELDS + ("gross_margin",) + CF_FINANCIAL_FIELDS:
            setattr(result, f"{f}_cur", parsed.values_cur.get(f))
            setattr(result, f"{f}_prv", parsed.values_prv.get(f))
        result.audit_opinion = audit_opinion
        result.fiscal_date = fiscal_date
        auditor = auditor or AuditorInfo()
        result.auditor_name = auditor.name
        result.auditor_address = auditor.address
        result.parse_status = parsed.parse_status
        result.parse_note = parsed.parse_note
        db.commit()


async def _run_financial_parsing(
    session_factory: sessionmaker[Session],
    settings: Settings,
    job_id: int,
) -> None:
    """STEP 5: parse_status가 아직 없는 results만 원문을 열어 파싱한다.

    파싱은 로컬 파일(STEP 4 캐시)만 사용하므로 DART/FSC API 호출이 없다 —
    쿼터와 무관하다. `parse_status IS NULL`을 재시도 조건으로 삼아 이미
    파싱된 건은 다시 열지 않는다(resume/재시도 겸용, retry_failed_parsing도
    동일 함수를 재사용한다).
    """
    with session_factory() as db:
        rows = db.execute(
            select(Result.id, Result.rcept_no).where(
                Result.job_id == job_id,
                Result.rcept_no.is_not(None),
                Result.parse_status.is_(None),
            )
        ).all()

    total = len(rows)
    _checkpoint(
        session_factory,
        job_id,
        current_step=STEP_PARSE_FINANCIALS,
        progress_done=0,
        progress_total=total,
    )

    cache_root = Path(settings.document_cache_dir)
    done = 0
    for result_id, rcept_no in rows:
        if done % _CHECKPOINT_INTERVAL == 0:
            _raise_if_cancelled(session_factory, job_id)

        target_dir = cache_root / rcept_no
        doc_path = _pick_document_file(target_dir) if target_dir.is_dir() else None

        if doc_path is None:
            _apply_parsed_result(
                session_factory,
                result_id,
                ParsedFinancials(parse_status="FAILED", parse_note="원문 파일을 찾을 수 없음(STEP4 다운로드 실패 추정)"),
                None,
                None,
            )
        else:
            raw_bytes = doc_path.read_bytes()
            suffix = doc_path.suffix.lower()
            auditor = AuditorInfo()
            try:
                if suffix == ".xml":
                    parsed = parse_xml_financials(raw_bytes)
                    raw_text = raw_bytes.decode("utf-8", errors="ignore")
                    # 감사인은 XML 원문에서만 추출한다(PDF는 미지원 — 감사의견과 동일).
                    auditor = extract_auditor(raw_bytes)
                elif suffix == ".pdf":
                    parsed = parse_pdf_financials(raw_bytes)
                    raw_text = ""
                else:
                    parsed = ParsedFinancials(parse_status="FAILED", parse_note=f"지원하지 않는 원문 형식: {suffix}")
                    raw_text = ""

                opinion = extract_audit_opinion(raw_text) if raw_text else None
                fiscal_date = _extract_fiscal_date(raw_text) if raw_text else None
            except Exception as exc:  # noqa: BLE001 - 원문 서식은 회사마다 편차가 커서
                # 예상 못한 파서 예외(예: EUC-KR 등 비UTF-8 인코딩 원문)가 이 건
                # 하나 때문에 Job 전체(나머지 수백~수천 건)를 실패시키면 안 된다 —
                # 이 건만 FAILED로 기록하고 계속 진행한다(CLAUDE.md "파싱은 100%
                # 자동화되지 않는다" 원칙).
                logger.warning(
                    "STEP5 파싱 중 예외(건너뛰고 FAILED로 기록) result_id=%s rcept_no=%s: %s",
                    result_id, rcept_no, exc,
                )
                parsed = ParsedFinancials(parse_status="FAILED", parse_note=f"파싱 중 예외 발생: {exc}")
                opinion = None
                fiscal_date = None
                auditor = AuditorInfo()
            _apply_parsed_result(session_factory, result_id, parsed, opinion, fiscal_date, auditor)

        done += 1
        if done % _CHECKPOINT_INTERVAL == 0 or done == total:
            _checkpoint(session_factory, job_id, progress_done=done, progress_total=total)

    logger.info("STEP5 완료: %s건 파싱 (job_id=%s)", total, job_id)


# ---------------------------------------------------------------------------
# STEP 6 — 매출액 범위 사후 필터
# ---------------------------------------------------------------------------


def _run_revenue_filter(
    session_factory: sessionmaker[Session], job_id: int, cond_revenue: dict[str, Any]
) -> None:
    """STEP 6: 당기 매출액(revenue_cur)이 조건 범위를 벗어나면 excluded_by_revenue=1.

    매출액을 파싱하지 못한 건(revenue_cur is None)은 사후 필터를 적용할 수
    없으므로 그대로 두고(제외하지 않음) parse_status로 검수하게 한다
    (상세개발계획.md §4-3).
    """
    if cond_revenue.get("min_krw") is None and cond_revenue.get("max_krw") is None:
        return

    with session_factory() as db:
        results = db.execute(select(Result).where(Result.job_id == job_id)).scalars().all()
        for result in results:
            if result.revenue_cur is None:
                continue
            result.excluded_by_revenue = 0 if revenue_matches(result.revenue_cur, cond_revenue) else 1
        db.commit()

    logger.info("STEP6 완료: 매출액 필터 적용 (job_id=%s)", job_id)


def _run_assets_filter(
    session_factory: sessionmaker[Session], job_id: int, cond_total_assets: dict[str, Any]
) -> None:
    """B4(구 STEP 6과 병행, §4-7-2 2026-07-15 추가): 당기 총자산(total_assets_cur)이
    조건 범위를 벗어나면 excluded_by_assets=1.

    `_run_revenue_filter`(STEP 6)와 완전히 동일한 패턴이다 — `revenue_matches`가
    이름과 무관하게 순수 값 범위 판정이라 그대로 재사용한다. 총자산을 파싱하지
    못한 건은 그대로 두고(제외하지 않음) parse_status로 검수하게 한다.
    """
    if cond_total_assets.get("min_krw") is None and cond_total_assets.get("max_krw") is None:
        return

    with session_factory() as db:
        results = db.execute(select(Result).where(Result.job_id == job_id)).scalars().all()
        for result in results:
            if result.total_assets_cur is None:
                continue
            result.excluded_by_assets = 0 if revenue_matches(result.total_assets_cur, cond_total_assets) else 1
        db.commit()

    logger.info("STEP6(총자산) 완료: 총자산 필터 적용 (job_id=%s)", job_id)


# ---------------------------------------------------------------------------
# STEP 7 — 최근 N년 재무 이력 수집 (2026-07-15 추가)
# ---------------------------------------------------------------------------


def _history_window(history_years: int) -> tuple[str, str]:
    """STEP 7이 list.json(corp_code 지정)에 넘길 (bgn_de, end_de)를 계산.

    목표 연도수(N)를 커버하기에 충분히 여유를 둔 과거 시점까지 잡는다 —
    회사마다 결산월이 다르고 감사보고서 제출 시점도 매년 정확히 같은 시기가
    아닐 수 있어, 필요한 보고서 수(대략 N/2건)보다 넉넉하게 N/2+2년을
    거슬러 올라간다. corp_code를 지정하면 3개월 제한이 없다는 점은 실측으로
    확인했다(위 STEP 7 설계 메모 참고) — 그래서 STEP 2와 달리
    `_split_period_into_windows()`가 필요 없다.
    """
    back_years = history_years // 2 + 2
    end_de = datetime.now().strftime("%Y%m%d")
    bgn_de = f"{datetime.now().year - back_years}0101"
    return bgn_de, end_de


async def _fetch_all_disclosures_for_corp(
    dart_client: DartClient, corp_code: str, bgn_de: str, end_de: str
) -> list[dict[str, Any]]:
    """corp_code를 지정해 그 회사의 외부감사관련(F) 공시를 전량 페이징 수집한다.

    한 회사가 감사보고서를 100건 넘게 제출하는 경우는 실무상 없다고 봐도
    무방하지만(연 1회 정기감사 기준 수십 년치), STEP 2와의 일관성을 위해
    total_page를 그대로 신뢰해 끝까지 순회한다.
    """
    items: list[dict[str, Any]] = []
    page_no = 1
    total_page = 1
    while page_no <= total_page:
        data = await dart_client.get_disclosure_list(
            corp_code=corp_code,
            bgn_de=bgn_de,
            end_de=end_de,
            pblntf_ty="F",
            page_no=page_no,
            page_count=_DISCLOSURE_PAGE_COUNT,
        )
        total_page = int(data.get("total_page") or 1)
        items.extend(data.get("list") or [])
        page_no += 1
    return items


def _upsert_financial_snapshot(
    session_factory: sessionmaker[Session],
    result_id: int,
    rcept_no: str,
    fiscal_year: str,
    values: dict[str, float | None],
    parse_status: str,
    parse_note: str | None,
    from_current_period: bool,
) -> None:
    """회사(result_id)-회계연도 단위로 financial_snapshots를 upsert.

    이미 그 연도의 행이 있으면 갱신한다 — 호출부(`_collect_history_for_result`)가
    "전기 열로 임시로 채워둔 연도(`from_current_period=0`)를 나중에 그 연도의
    자기 공시(당기)로 덮어쓴다"는 규칙을 쓰기 때문에 같은 연도가 두 번
    upsert될 수 있다(그 외의 중복 upsert는 호출부에서 걸러진다).
    """
    with session_factory() as db:
        existing = db.execute(
            select(FinancialSnapshot).where(
                FinancialSnapshot.result_id == result_id,
                FinancialSnapshot.fiscal_year == fiscal_year,
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = FinancialSnapshot(result_id=result_id, fiscal_year=fiscal_year)
            db.add(existing)
        existing.rcept_no = rcept_no
        for f in STANDARD_FINANCIAL_FIELDS + CF_FINANCIAL_FIELDS:  # gross_margin·CF 포함
            setattr(existing, f, values.get(f))
        existing.parse_status = parse_status
        existing.parse_note = parse_note
        existing.from_current_period = 1 if from_current_period else 0
        db.commit()


async def _collect_history_for_result(
    dart_client: DartClient,
    session_factory: sessionmaker[Session],
    settings: Settings,
    result_id: int,
    corp_code: str,
    history_years: int,
) -> None:
    """회사(result) 1건에 대해 최근 `history_years`개 회계연도를 채운다.

    **연도별 1차 자료 우선 규칙(2026-07-20)**: 각 연도는 "그 연도를 당기로 하는"
    감사보고서에서 값을 가져오는 것이 원칙이다(`from_current_period=1`) — 화면의
    연도별 "원문 보기"가 여는 원문(= 그 행의 `rcept_no`)과 그 열에 표시된 수치의
    당기가 항상 같은 연도여야 한다는 요구에서 나온 규칙이다. newest-first 순회
    특성상 어떤 연도는 다음 연도 공시의 **전기** 열로 먼저 채워지는데, 이는
    임시(`from_current_period=0`)로 두고 나중에 그 연도의 자기 공시를 열면
    덮어쓴다. 자기 공시가 끝내 없으면(가장 오래된 연도에서 흔함) 전기 유래 값이
    그대로 남고, 화면은 그 연도 버튼에 "전기 기준"이라고 표시한다.

    이미 `financial_snapshots`에 history_years개 이상의 distinct fiscal_year가
    채워져 있고 **가장 오래된 연도를 뺀 나머지가 전부 당기 유래**면 아무 API도
    호출하지 않고 즉시 반환한다(resume의 핵심). 가장 오래된 연도를 예외로 두는
    것은 그 연도의 자기 공시가 조회 기간 밖이라 끝내 못 여는 게 정상이기
    때문이다 — 이 예외가 없으면 resume 때마다 헛되이 list.json을 다시 부른다.
    반대로 이 규칙 도입(2026-07-20) 이전에 수집돼 전부 전기 유래(0)로 남아 있는
    기존 데이터는 다음 resume 때 한 번 다시 훑어 당기 유래로 교정된다.
    """
    with session_factory() as db:
        existing_rows = db.execute(
            select(FinancialSnapshot.fiscal_year, FinancialSnapshot.from_current_period).where(
                FinancialSnapshot.result_id == result_id
            )
        ).all()
    existing_years = {year for year, _flag in existing_rows}
    own_years = {year for year, flag in existing_rows if flag}
    if len(existing_years) >= history_years:
        pending = existing_years - own_years - {min(existing_years)}
        if not pending:
            return

    bgn_de, end_de = _history_window(history_years)
    disclosures = await _fetch_all_disclosures_for_corp(dart_client, corp_code, bgn_de, end_de)

    # newest-first (rcept_no 내림차순) — 위 STEP 7 설계 메모 참고. 정정 공시도
    # 자연히 원본보다 먼저 훑이게 되어 "연도별 최신 rcept_no 우선" dedup이 된다.
    disclosures.sort(key=lambda item: item.get("rcept_no") or "", reverse=True)

    collected_years = set(existing_years)
    for item in disclosures:
        if len(collected_years) >= history_years and collected_years <= own_years:
            # 목표 연도를 다 모았고 전부 자기 공시(당기)로 확정됐다 — 더 볼 필요 없다.
            break
        rcept_no = item.get("rcept_no")
        if not rcept_no:
            continue

        target_dir = await _ensure_document_cached(dart_client, settings, rcept_no)
        if target_dir is None:
            continue
        doc_path = _pick_document_file(target_dir)
        if doc_path is None:
            continue

        raw_bytes = doc_path.read_bytes()
        suffix = doc_path.suffix.lower()
        try:
            if suffix == ".xml":
                parsed = parse_xml_financials(raw_bytes)
                raw_text = raw_bytes.decode("utf-8", errors="ignore")
            elif suffix == ".pdf":
                parsed = parse_pdf_financials(raw_bytes)
                raw_text = ""
            else:
                continue
        except Exception as exc:  # noqa: BLE001 - _run_financial_parsing과 동일한 이유
            # (예: EUC-KR 등 비UTF-8 원문) — 이 공시 1건만 건너뛰고 다른 연도
            # 공시로 history_years를 채운다. Job 전체를 죽이면 안 된다.
            logger.warning(
                "STEP7 파싱 중 예외(건너뜀) result_id=%s rcept_no=%s: %s", result_id, rcept_no, exc
            )
            continue

        fiscal_date = _extract_fiscal_date(raw_text) if raw_text else None
        if fiscal_date is None:
            # 결산기준일을 못 뽑으면 연도를 알 수 없어 이 공시는 이력에 반영하지 않는다
            # (results의 최신 스냅샷과 달리, 이 STEP은 연도 키가 필수다).
            continue

        fiscal_year_cur = fiscal_date[:4]
        fiscal_year_prv = str(int(fiscal_year_cur) - 1)

        if len(collected_years) >= history_years and fiscal_year_cur not in collected_years:
            # 목표 연도는 다 모았는데 이 공시의 당기는 그중에 없다 = 더 오래된
            # 공시만 남았다는 뜻(newest-first). 남은 미확정 연도를 채워줄 공시는
            # 이제 없으므로 헛다운로드를 막기 위해 여기서 중단한다.
            break

        # 당기: 그 연도의 1차 자료다. 전기 열로 임시로 채워둔 연도(from_current_period=0)면
        # 덮어쓰고, 아직 목표 연도 여유가 있으면 새로 추가한다.
        if fiscal_year_cur not in own_years and (
            fiscal_year_cur in collected_years or len(collected_years) < history_years
        ):
            _upsert_financial_snapshot(
                session_factory,
                result_id,
                rcept_no,
                fiscal_year_cur,
                parsed.values_cur,
                parsed.parse_status,
                parsed.parse_note,
                from_current_period=True,
            )
            collected_years.add(fiscal_year_cur)
            own_years.add(fiscal_year_cur)

        # 전기: 아직 아무 자료도 없는 연도만 임시로 채운다(자기 공시를 나중에
        # 열게 되면 위 분기가 덮어쓴다).
        if fiscal_year_prv not in collected_years and len(collected_years) < history_years:
            _upsert_financial_snapshot(
                session_factory,
                result_id,
                rcept_no,
                fiscal_year_prv,
                parsed.values_prv,
                parsed.parse_status,
                parsed.parse_note,
                from_current_period=False,
            )
            collected_years.add(fiscal_year_prv)


async def _run_history_collection(
    dart_client: DartClient,
    session_factory: sessionmaker[Session],
    settings: Settings,
    job_id: int,
    history_years: int,
) -> None:
    """STEP 7: 최종 결과(`excluded_by_revenue=0` and `excluded_by_assets=0`)에
    남은 회사만 최근 N년 이력을 채운다.

    STEP 3의 FSC 사전 추림과 동일한 "값비싼 호출은 후보를 최대한 추린 뒤에"
    원칙 — 매출액·총자산 필터까지 다 통과한 회사만 대상으로 해 쿼터 영향을
    최종 결과 건수에만 비례하게 한다. `excluded_by_assets`는 §4-7-2(2026-07-15
    추가) 총자산 필터 컬럼이다 — 이 조건을 쓰지 않는 기존 run_job() 흐름에서는
    모든 행이 기본값 0이라 이 조건 추가로 동작이 바뀌지 않는다.
    """
    with session_factory() as db:
        rows = db.execute(
            select(Result.id, Result.corp_code).where(
                Result.job_id == job_id,
                Result.excluded_by_revenue == 0,
                Result.excluded_by_assets == 0,
                Result.corp_code.is_not(None),
            )
        ).all()

    total = len(rows)
    _checkpoint(
        session_factory,
        job_id,
        current_step=STEP_HISTORY_COLLECTION,
        progress_done=0,
        progress_total=total,
    )

    done = 0
    for result_id, corp_code in rows:
        if done % _CHECKPOINT_INTERVAL == 0:
            _raise_if_cancelled(session_factory, job_id)

        await _collect_history_for_result(
            dart_client, session_factory, settings, result_id, corp_code, history_years
        )

        done += 1
        if done % _CHECKPOINT_INTERVAL == 0 or done == total:
            _checkpoint(session_factory, job_id, progress_done=done, progress_total=total)

    logger.info(
        "STEP7 완료: 최근 %s년 이력 수집 대상 %s건 처리 (job_id=%s)", history_years, total, job_id
    )


async def retry_failed_parsing(job_id: int) -> None:
    """parse_status=FAILED인 results만 골라 STEP 5(파싱)를 재시도한다.

    `POST /api/jobs/{id}/retry-failed`(app/api/jobs.py)가 FAILED 건의
    parse_status를 NULL로 리셋해 두면, `_run_financial_parsing`이 그 건만
    다시 열어 파싱한다(위 STEP 5 설명 참고 — 별도 재시도 로직을 새로 만들
    필요 없이 동일 함수를 재사용).
    """
    settings = get_settings()
    session_factory = get_session_factory()
    job = _load_job(session_factory, job_id)
    if job is None:
        logger.error("retry_failed_parsing: job_id=%s 를 찾을 수 없습니다.", job_id)
        return

    try:
        await _run_financial_parsing(session_factory, settings, job_id)
        cond_revenue: dict[str, Any] = json.loads(job.cond_revenue) if job.cond_revenue else {}
        cond_total_assets: dict[str, Any] = (
            json.loads(job.cond_total_assets) if job.cond_total_assets else {}
        )
        _run_revenue_filter(session_factory, job_id, cond_revenue)
        _run_assets_filter(session_factory, job_id, cond_total_assets)
        logger.info("Job %s 재파싱 완료", job_id)
    except JobCancelledError:
        logger.info("Job %s 재파싱 중 취소 감지", job_id)
    except Exception:  # noqa: BLE001 - 백그라운드 작업은 여기서 반드시 흡수해야 한다
        logger.exception("Job %s 재파싱 중 예외 발생", job_id)


# ---------------------------------------------------------------------------
# Job 실행 엔트리포인트 (BackgroundTasks에서 호출)
# ---------------------------------------------------------------------------


async def run_job(job_id: int) -> None:
    """Job 1건을 STEP 1~7까지 실행한다.

    - `QuotaExceededError`: Job을 PAUSED_QUOTA로 전환하고 정상 반환한다
      (예외를 상위로 전파하지 않는다 — CLAUDE.md 핵심 제약 4번).
    - `JobCancelledError`: 체크포인트에서 취소를 감지 — 상태는 이미
      CANCELLED이므로 그대로 반환한다.
    - 그 외 예외: FAILED + error_msg를 기록하고 반환한다. `BackgroundTasks`로
      실행되므로 예외를 그대로 던지면 로그에만 남고 Job 상태가 갱신되지
      않는다 — 반드시 이 함수 내부에서 흡수해야 한다.
    """
    settings = get_settings()
    settings.ensure_dirs()
    session_factory = get_session_factory()

    job = _load_job(session_factory, job_id)
    if job is None:
        logger.error("run_job: job_id=%s 를 찾을 수 없습니다.", job_id)
        return
    if job.status == JobStatus.CANCELLED:
        logger.info("run_job: job_id=%s 는 이미 CANCELLED 상태 — 실행하지 않음.", job_id)
        return

    _checkpoint(session_factory, job_id, status=JobStatus.RUNNING, error_msg=None)

    cond_region: dict[str, Any] = json.loads(job.cond_region) if job.cond_region else {}
    cond_revenue: dict[str, Any] = json.loads(job.cond_revenue) if job.cond_revenue else {}
    cond_industry: list[str] = json.loads(job.cond_industry) if job.cond_industry else []
    cond_period: dict[str, Any] = json.loads(job.cond_period) if job.cond_period else {}

    dart_client = DartClient(settings=settings, session_factory=session_factory)
    # 금융위 기업기본정보 API 클라이언트 — DART와 별도 쿼터라 dart_client와
    # 독립적으로 생성/종료한다 (상세개발계획.md §4-1 대응 1).
    fsc_client = FscCorpInfoClient(settings=settings)
    try:
        # STEP 1: corp_cache 갱신 (TTL 이내면 사실상 즉시 반환)
        _raise_if_cancelled(session_factory, job_id)
        _checkpoint(session_factory, job_id, current_step=STEP_CORP_CACHE)
        await refresh_corp_cache(dart_client, session_factory, settings)

        # STEP 2: 외부감사관련 공시 목록 수집
        _raise_if_cancelled(session_factory, job_id)
        candidates = await _collect_candidates(dart_client, session_factory, job_id, cond_period)

        # STEP 3: 지역 사전 추림(FSC) + 지역/업종 필터 + corp_profiles 캐시 적재 + results 선삽입
        _raise_if_cancelled(session_factory, job_id)
        await _run_region_industry_filter(
            dart_client,
            fsc_client,
            session_factory,
            settings,
            job_id,
            candidates,
            cond_region,
            cond_industry,
        )

        # STEP 4: 감사보고서 원문 다운로드
        _raise_if_cancelled(session_factory, job_id)
        await _run_document_download(dart_client, session_factory, settings, job_id)

        # STEP 5: 재무제표 파싱(당기/전기 13항목) + 감사의견 추출 (API 호출 없음)
        _raise_if_cancelled(session_factory, job_id)
        await _run_financial_parsing(session_factory, settings, job_id)

        # STEP 6: 매출액 범위 사후 필터
        _raise_if_cancelled(session_factory, job_id)
        _run_revenue_filter(session_factory, job_id, cond_revenue)

        # STEP 7: 최근 N년 재무 이력 수집 (excluded_by_revenue=0인 최종 결과만 대상,
        # 2026-07-15 추가) — Job 완료(DONE) 시점이 STEP 6에서 이 지점으로 이동했다
        # (M3에서 STEP4->STEP6로 옮긴 전례와 동일한 패턴).
        _raise_if_cancelled(session_factory, job_id)
        await _run_history_collection(
            dart_client, session_factory, settings, job_id, job.history_years or DEFAULT_HISTORY_YEARS
        )

        _checkpoint(session_factory, job_id, status=JobStatus.DONE, current_step=STEP_HISTORY_COLLECTION)
        logger.info("Job %s 완료 (STEP1~7)", job_id)

    except JobCancelledError:
        logger.info("Job %s 취소 감지 — 중단.", job_id)
        # 상태는 이미 CANCELLED (cancel API가 기록) — 여기서 덮어쓰지 않는다.
    except QuotaExceededError as exc:
        logger.warning("Job %s 쿼터 초과로 일시정지: %s", job_id, exc)
        _checkpoint(session_factory, job_id, status=JobStatus.PAUSED_QUOTA, error_msg=str(exc))
    except Exception as exc:  # noqa: BLE001 - 백그라운드 작업은 여기서 반드시 흡수해야 한다
        logger.exception("Job %s 실행 중 예외 발생", job_id)
        _checkpoint(session_factory, job_id, status=JobStatus.FAILED, error_msg=str(exc))
    finally:
        await dart_client.aclose()
        await fsc_client.aclose()


# ---------------------------------------------------------------------------
# Phase 1 — 후보 발굴 (A2, §4-7/§4-7-1 M6 재설계 → §4-10 M8 3단계로 인덱스 교체)
# ---------------------------------------------------------------------------
#
# run_job()(위, STEP 1~7)은 손대지 않고 그대로 둔다 — Phase 2(아래 run_job_phase2)가
# 그 STEP 4~7 함수를 그대로 재사용한다. run_job_phase1()은 run_job()을 참고해
# 만든 새 오케스트레이터로, STEP 1~3(corp_cache 갱신 + list.json 전국 수집 +
# company.json 지역 필터)을 완전히 대체한다 — 외부 API를 전혀 호출하지 않는다.
#
# **M8 3단계(2026-07-20)에서 A3/A4가 제거됐다.** 후보 확정은 이제 A2
# (`dart_corp_index` 로컬 쿼리) 하나로 끝난다:
#   - A3(FSC 건별 재무 스크리닝) 폐기 — 1년 묵은 값으로 거르면 조건에 맞는 회사의
#     25.3%를 조용히 놓친다(§4-10-C 실측). 매출액/총자산 판정은 B4 한 곳뿐이다.
#   - A4(이름 매칭 corp_code 해석) 불필요 — `corp_code`가 인덱스의 PK다.
#     동명이인 오매칭(실측 11.6%)과 상장사 조회가 구조적으로 사라졌다.
#
# **전역 인덱스 크롤(약 23분)은 이 함수 안에서 트리거하지 않는다.**
# `dart_corp_index`가 비어 있으면 이 Job은 안내 메시지와 함께 FAILED 처리된다 —
# 관리자가 별도로 `POST /api/meta/dart-index/refresh`(app/api/meta.py)를 호출해야
# 한다. TTL이 지났지만 데이터 자체는 있으면 경고 로그만 남기고 그 데이터로 계속
# 진행한다(오래된 데이터라도 없는 것보다 낫다는 판단 — corp_profiles/corp_cache는
# "매 Job 실행 중 자연히 갱신"되지만 전역 인덱스는 그렇지 않기 때문).


def _insert_phase1_candidates_into_results(
    session_factory: sessionmaker[Session],
    job_id: int,
    candidates: list[DartCorpIndex],
) -> int:
    """A2가 확정한 `dart_corp_index` 후보를 results에 선삽입한다(M8 3단계, §4-10).

    구 A4(이름 매칭으로 corp_code 해석)가 사라졌다 — `corp_code`는 인덱스의
    PK라 그대로 쓰면 된다. 채워지는 값의 성격도 달라졌다:

    - `corp_name`/`address`/`ceo_name`/`induty_code`/`induty_name`은 **DART
      기업개황 원본**이다(구 FSC 인덱스의 느슨한 추정값이 아니다). `induty_code`도
      이제 회사별 정밀 코드가 있어 처음으로 채운다.
    - `phone`은 채울 수 없다 — 기업개황 엑셀에 전화번호 열이 없다
      (§4-10-G 열린 질문 4). 후보 목록에서는 빈 값으로 남는다.
    - 매출액/총자산은 `_cur` 컬럼이 아니라 **`ref_*` 참고 컬럼**에 기준연도와
      함께 넣는다(§4-10-C/D). `_cur`는 Phase 2가 원문을 파싱해 채우는
      확정치 전용이며, B4가 그 값으로만 판정한다 — 참고값이 확정치 자리에
      섞여 들어가 필터에 관여하던 구 A3 방식을 여기서 끊는다.
    """
    crnos = [c.jurir_no for c in candidates if c.jurir_no]
    with session_factory() as db:
        stats = get_latest_stat_by_crno(db, crnos)
        existing_corp_codes = {
            row[0]
            for row in db.execute(
                select(Result.corp_code).where(Result.job_id == job_id)
            ).all()
        }
        inserted = 0
        for candidate in candidates:
            corp_code = candidate.corp_code
            if not corp_code or corp_code in existing_corp_codes:
                continue
            stat = stats.get(candidate.jurir_no) if candidate.jurir_no else None
            db.add(
                Result(
                    job_id=job_id,
                    corp_code=corp_code,
                    corp_name=candidate.corp_name,
                    address=candidate.address,
                    ceo_name=candidate.ceo_name,
                    induty_code=candidate.induty_code,
                    induty_name=candidate.induty_name,
                    ref_revenue=stat.sale_amt if stat else None,
                    ref_total_assets=stat.tast_amt if stat else None,
                    ref_fin_year=stat.biz_year if stat else None,
                )
            )
            existing_corp_codes.add(corp_code)
            inserted += 1
        db.commit()
    return inserted


async def run_job_phase1(job_id: int) -> None:
    """Phase 1(A2)을 실행해 후보 corp_code를 확정하고 results에 선삽입한다.

    M8 3단계(§4-10)에서 A3(FSC 건별 사전 스크리닝)와 A4(이름 매칭 corp_code
    해석)가 **함께 사라져** 이 함수는 로컬 DB 쿼리만 하는 동기 작업이 됐다 —
    외부 API 호출이 0건이라 쿼터/네트워크 실패 경로 자체가 없다.

    완료 시 `status=DONE`, `phase=CANDIDATES`로 멈춘다 — Phase 2(재무정보
    수집)는 사용자가 `POST /api/jobs/{id}/start-financials`를 호출해야
    시작된다(§4-7-1). `run_job()`과 마찬가지로 예외는 이 함수 내부에서
    흡수해 Job 상태에 반영한다(BackgroundTasks가 예외를 그냥 삼켜버리므로).
    """
    settings = get_settings()
    session_factory = get_session_factory()

    job = _load_job(session_factory, job_id)
    if job is None:
        logger.error("run_job_phase1: job_id=%s 를 찾을 수 없습니다.", job_id)
        return
    if job.status == JobStatus.CANCELLED:
        logger.info("run_job_phase1: job_id=%s 는 이미 CANCELLED 상태 — 실행하지 않음.", job_id)
        return

    _checkpoint(
        session_factory,
        job_id,
        status=JobStatus.RUNNING,
        error_msg=None,
        current_step=STEP_PHASE1_CANDIDATES,
    )

    with session_factory() as db:
        has_index = db.execute(select(DartCorpIndex.corp_code).limit(1)).first() is not None

    if not has_index:
        _checkpoint(
            session_factory,
            job_id,
            status=JobStatus.FAILED,
            error_msg=(
                "dart_corp_index가 비어 있습니다. 관리자가 먼저 "
                "POST /api/meta/dart-index/refresh로 DART 기업개황 전역 인덱스를 "
                "구축해야 합니다(약 23분)."
            ),
        )
        logger.error("run_job_phase1: job_id=%s — dart_corp_index가 비어 있어 실행 불가.", job_id)
        return

    if is_dart_index_stale(session_factory, settings=settings):
        logger.warning(
            "run_job_phase1: job_id=%s — dart_corp_index가 TTL(%s일)을 초과했지만 "
            "기존 데이터로 계속 진행합니다. 필요하면 관리자가 갱신하세요.",
            job_id,
            settings.dart_index_ttl_days,
        )

    cond_region: dict[str, Any] = json.loads(job.cond_region) if job.cond_region else {}
    cond_industry: list[str] = json.loads(job.cond_industry) if job.cond_industry else []
    # 매출액/총자산 조건(cond_revenue/cond_total_assets)은 Phase 1에서 읽지 않는다 —
    # 판정 지점은 Phase 2 B4 한 곳뿐이다(§4-10-C, 사전 제외 전면 폐기).

    try:
        _raise_if_cancelled(session_factory, job_id)
        with session_factory() as db:
            candidates = filter_local_candidates(
                db, cond_region=cond_region, cond_industry=cond_industry
            )
            for row in candidates:
                db.expunge(row)

        _checkpoint(session_factory, job_id, progress_done=0, progress_total=len(candidates))
        logger.info(
            "Job %s Phase1 A2 완료: 지역/업종/비상장 필터 통과 %s개사", job_id, len(candidates)
        )

        _raise_if_cancelled(session_factory, job_id)
        inserted = _insert_phase1_candidates_into_results(session_factory, job_id, candidates)

        _checkpoint(
            session_factory,
            job_id,
            status=JobStatus.DONE,
            phase=JobPhase.CANDIDATES,
            progress_done=len(candidates),
            progress_total=len(candidates),
        )
        logger.info("Job %s Phase1(A2) 완료 — 후보 %s개사 확정", job_id, inserted)

    except JobCancelledError:
        logger.info("Job %s Phase1 실행 중 취소 감지 — 중단.", job_id)
    except Exception as exc:  # noqa: BLE001 - 백그라운드 작업은 여기서 반드시 흡수해야 한다
        logger.exception("Job %s Phase1 실행 중 예외 발생", job_id)
        _checkpoint(session_factory, job_id, status=JobStatus.FAILED, error_msg=str(exc))


# ---------------------------------------------------------------------------
# Phase 2 — 재무정보 크롤링 (B1~B5, 기존 STEP 4~7 재사용, §4-7/§4-7-1)
# ---------------------------------------------------------------------------


def _build_corp_cache_name_multimap(db: Session) -> dict[str, list[str]]:
    """corp_cache를 정규화 회사명 → [corp_code, ...] 다중매핑으로 만든다.

    A4의 `_build_corp_cache_name_index`(이름 1개당 corp_code 1개만 보관)와 달리,
    같은 이름을 가진 corp_code를 **전부** 모은다 — B1의 동명이인 재해석
    (아래 `_resolve_alternative_corp_code`)이 후보군 전체를 훑어야 하기 때문이다.
    각 리스트는 `modify_date` 내림차순(최근 갱신 우선)으로 정렬해, 활성 법인이
    앞에 오도록 한다(company.json 호출 횟수를 줄이는 최적화 — 정확성은 주소
    대조가 보장한다).
    """
    rows = db.execute(
        select(CorpCache.corp_code, CorpCache.corp_name, CorpCache.modify_date)
    ).all()
    multimap: dict[str, list[tuple[str, str]]] = {}
    for corp_code, corp_name, modify_date in rows:
        norm = normalize_corp_name(corp_name or "")
        if norm and corp_code:
            multimap.setdefault(norm, []).append((corp_code, modify_date or ""))
    result: dict[str, list[str]] = {}
    for norm, entries in multimap.items():
        entries.sort(key=lambda t: t[1], reverse=True)
        result[norm] = [corp_code for corp_code, _md in entries]
    return result


async def _resolve_alternative_corp_code(
    dart_client: DartClient,
    *,
    assigned_corp_code: str,
    corp_name: str | None,
    want_sido: str | None,
    want_sigungu: str | None,
    cond_region: dict | None,
    used_codes: set[str],
    name_multimap: dict[str, list[str]],
    bgn_de: str,
    end_de: str,
) -> tuple[str, str] | None:
    """B1 폴백: 배정된 corp_code에 감사보고서 공시가 0건일 때, 같은 정규화 이름을
    가진 **다른** corp_code 중 "실제 감사보고서가 있고 주소(시도/시군구)가 일치"하는
    것을 찾아 `(corp_code, latest_rcept_no)`로 돌려준다.

    동명이인 충돌(예: corpCode.xml에 '유성정밀'이 여러 개 — 그중 하나는 이미 폐지돼
    공시가 0건) 때문에 A4 이름 매칭이 죽은 법인에 후보를 붙이는 문제를 바로잡는다.
    주소 대조 기준은 이 후보의 FSC 주소(`want_sido`/`want_sigungu`)를 우선 쓰고,
    FSC 주소를 못 파싱했으면 Job의 지역 조건(`region_matches`)으로 폴백한다.
    이미 이 Job의 다른 결과가 쓰고 있는 corp_code(`used_codes`)는 중복 방지를 위해
    건너뛴다. 값비싼 company.json 호출은 공시가 실제로 있는 후보에만 한다.
    """
    norm = normalize_corp_name(corp_name or "")
    if not norm:
        return None
    for alt_code in name_multimap.get(norm, []):
        if alt_code == assigned_corp_code or alt_code in used_codes:
            continue

        disclosures = await _fetch_all_disclosures_for_corp(dart_client, alt_code, bgn_de, end_de)
        if not disclosures:
            continue

        try:
            company = await dart_client.get_company(alt_code)
        except QuotaExceededError:
            raise
        except DartApiError as exc:
            logger.warning("B1 대체 corp_code company.json 조회 실패 %s: %s", alt_code, exc)
            continue

        alt_sido, alt_sigungu = parse_address((company.get("adres") or "").strip() or None)
        if want_sido is not None:
            if alt_sido != want_sido:
                continue
            if want_sigungu is not None and alt_sigungu != want_sigungu:
                continue
        elif not region_matches(alt_sido, alt_sigungu, cond_region):
            continue

        disclosures.sort(key=lambda item: item.get("rcept_no") or "", reverse=True)
        latest = disclosures[0].get("rcept_no")
        if latest:
            return alt_code, latest
    return None


async def _backfill_latest_rcept_no_for_job(
    dart_client: DartClient,
    session_factory: sessionmaker[Session],
    job_id: int,
    history_years: int,
) -> None:
    """Phase 2 B2/B3 준비 단계: results에 이미 있는 corp_code마다 최신 감사보고서의
    rcept_no를 찾아 채운다.

    기존 STEP 4(`_run_document_download`)/STEP 5(`_run_financial_parsing`)는
    `results.rcept_no`가 이미 채워져 있다는 전제로 동작한다(구 파이프라인에서는
    STEP 2/3이 이를 채웠다). Phase 1(A1~A4)은 FSC 데이터만으로 후보를 확정하므로
    rcept_no를 모른다 — 이 함수가 그 간극을 메운다. **새 다운로드/파싱 로직을
    만들지 않고** 이미 STEP 7이 쓰는 `_fetch_all_disclosures_for_corp()`(corp_code
    지정 list.json 페이징, 기간 제한 없음 실측)를 그대로 재사용해 후보 회사의
    공시 목록만 가볍게 조회한다 — 실제 원문 다운로드는 뒤이은 STEP 4가 담당한다.

    A4 이름 매칭이 동명이인 중 폐지된 corp_code(공시 0건)를 붙일 수 있어, 배정된
    corp_code로 공시가 0건이면 `_resolve_alternative_corp_code()`로 같은 이름의
    다른 corp_code 중 주소가 일치하고 실제 공시가 있는 것으로 **교체**한 뒤
    rcept_no를 채운다(교체 시 `results.corp_code`도 갱신 — 이후 STEP 4/5/7이 그
    corp_code로 동작한다). 그래도 못 찾으면 기존처럼 FAILED로 남긴다.
    """
    with session_factory() as db:
        rows = db.execute(
            select(Result.id, Result.corp_code, Result.corp_name, Result.address).where(
                Result.job_id == job_id,
                Result.corp_code.is_not(None),
                Result.rcept_no.is_(None),
            )
        ).all()
        job = db.get(Job, job_id)
        cond_region: dict[str, Any] = (
            json.loads(job.cond_region) if job is not None and job.cond_region else {}
        )
        used_codes: set[str] = {
            code
            for (code,) in db.execute(
                select(Result.corp_code).where(
                    Result.job_id == job_id, Result.corp_code.is_not(None)
                )
            ).all()
        }

    if not rows:
        return

    bgn_de, end_de = _history_window(history_years)
    name_multimap: dict[str, list[str]] | None = None
    done = 0
    found = 0
    total = len(rows)
    for result_id, corp_code, corp_name, address in rows:
        if done % _CHECKPOINT_INTERVAL == 0:
            _raise_if_cancelled(session_factory, job_id)

        disclosures = await _fetch_all_disclosures_for_corp(dart_client, corp_code, bgn_de, end_de)
        disclosures.sort(key=lambda item: item.get("rcept_no") or "", reverse=True)
        latest_rcept_no = disclosures[0].get("rcept_no") if disclosures else None
        resolved_corp_code = corp_code

        if latest_rcept_no is None:
            # 배정된 corp_code에 공시가 없다 — 동명이인 폐지 법인일 수 있으므로
            # 같은 이름의 다른 corp_code(주소 일치)로 재해석을 시도한다.
            if name_multimap is None:
                with session_factory() as db:
                    name_multimap = _build_corp_cache_name_multimap(db)
            want_sido, want_sigungu = parse_address(address)
            alt = await _resolve_alternative_corp_code(
                dart_client,
                assigned_corp_code=corp_code,
                corp_name=corp_name,
                want_sido=want_sido,
                want_sigungu=want_sigungu,
                cond_region=cond_region,
                used_codes=used_codes,
                name_multimap=name_multimap,
                bgn_de=bgn_de,
                end_de=end_de,
            )
            if alt is not None:
                resolved_corp_code, latest_rcept_no = alt
                used_codes.add(resolved_corp_code)

        with session_factory() as db:
            result = db.get(Result, result_id)
            if result is not None:
                if latest_rcept_no:
                    result.corp_code = resolved_corp_code
                    result.rcept_no = latest_rcept_no
                    found += 1
                else:
                    # 공시를 못 찾은 건은 FAILED로 명시해 검수 대상으로 노출한다.
                    # `_cur` 비우기는 M8 이후로는 사실상 no-op이다 — 참고값이
                    # `ref_*`로 분리돼 Phase 1이 `_cur`를 채우지 않기 때문(§4-10-C).
                    # 구 A3 시절에 만들어진 기존 Job의 결과 행을 위해 남겨둔다.
                    result.parse_status = ParseStatus.FAILED
                    result.parse_note = "최근 감사보고서 공시를 찾을 수 없음(참고값만 존재)"
                    result.revenue_cur = None
                    result.total_assets_cur = None
                db.commit()

        done += 1

    logger.info("Phase2 rcept_no 백필 완료: %s/%s개사 (job_id=%s)", found, total, job_id)


async def run_job_phase2(job_id: int) -> None:
    """Phase 2(B1~B5)를 실행해 Phase 1이 확정한 corp_code 목록에 대해서만
    재무 이력을 채운다.

    `POST /api/jobs/{id}/start-financials`가 `phase=FINANCIALS`/`status=RUNNING`
    으로 전환한 뒤 백그라운드로 호출한다. 입력은 "STEP 2가 모은 전국 후보"가
    아니라 "이미 results에 있는(Phase 1이 확정한) corp_code 목록"이라는 점만
    다르고, 실제 다운로드/파싱/필터 로직(STEP 4/5/6/7)은 전혀 새로 만들지
    않고 그대로 재사용한다.
    """
    settings = get_settings()
    settings.ensure_dirs()
    session_factory = get_session_factory()

    job = _load_job(session_factory, job_id)
    if job is None:
        logger.error("run_job_phase2: job_id=%s 를 찾을 수 없습니다.", job_id)
        return
    if job.status == JobStatus.CANCELLED:
        logger.info("run_job_phase2: job_id=%s 는 이미 CANCELLED 상태 — 실행하지 않음.", job_id)
        return

    _checkpoint(session_factory, job_id, status=JobStatus.RUNNING, error_msg=None)

    cond_revenue: dict[str, Any] = json.loads(job.cond_revenue) if job.cond_revenue else {}
    cond_total_assets: dict[str, Any] = (
        json.loads(job.cond_total_assets) if job.cond_total_assets else {}
    )
    history_years = job.history_years or DEFAULT_HISTORY_YEARS

    dart_client = DartClient(settings=settings, session_factory=session_factory)
    try:
        # B2/B3 준비: results의 corp_code마다 최신 공시(rcept_no) 백필 —
        # 기존 STEP4가 rcept_no를 입력으로 삼기 때문에 필요한 최소한의 준비 단계.
        _raise_if_cancelled(session_factory, job_id)
        _checkpoint(session_factory, job_id, current_step=STEP_DOCUMENT_DOWNLOAD)
        await _backfill_latest_rcept_no_for_job(dart_client, session_factory, job_id, history_years)

        # B3: 감사보고서 원문 다운로드 (기존 STEP 4 재사용)
        _raise_if_cancelled(session_factory, job_id)
        await _run_document_download(dart_client, session_factory, settings, job_id)

        # B3: 재무제표 파싱(당기/전기 13항목) + 감사의견 추출 (기존 STEP 5 재사용, API 호출 없음)
        _raise_if_cancelled(session_factory, job_id)
        await _run_financial_parsing(session_factory, settings, job_id)

        # B4: 매출액·총자산 최종 확정 필터 (기존 STEP 6 재사용 + 총자산 병행, §4-7-2)
        _raise_if_cancelled(session_factory, job_id)
        _run_revenue_filter(session_factory, job_id, cond_revenue)
        _run_assets_filter(session_factory, job_id, cond_total_assets)

        # B5: 최근 N년 재무 이력 수집 (기존 STEP 7 재사용)
        _raise_if_cancelled(session_factory, job_id)
        await _run_history_collection(
            dart_client, session_factory, settings, job_id, history_years
        )

        _checkpoint(
            session_factory,
            job_id,
            status=JobStatus.DONE,
            phase=JobPhase.FINANCIALS,
            current_step=STEP_HISTORY_COLLECTION,
        )
        logger.info("Job %s Phase2(B1~B5) 완료", job_id)

    except JobCancelledError:
        logger.info("Job %s Phase2 실행 중 취소 감지 — 중단.", job_id)
    except QuotaExceededError as exc:
        logger.warning("Job %s Phase2 실행 중 쿼터 초과로 일시정지: %s", job_id, exc)
        _checkpoint(session_factory, job_id, status=JobStatus.PAUSED_QUOTA, error_msg=str(exc))
    except Exception as exc:  # noqa: BLE001 - 백그라운드 작업은 여기서 반드시 흡수해야 한다
        logger.exception("Job %s Phase2 실행 중 예외 발생", job_id)
        _checkpoint(session_factory, job_id, status=JobStatus.FAILED, error_msg=str(exc))
    finally:
        await dart_client.aclose()
