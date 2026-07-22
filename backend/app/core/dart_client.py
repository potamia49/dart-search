"""OpenDART API 비동기 래퍼.

CLAUDE.md 핵심 제약 4번: 일일 호출 한도 20,000건 — `api_usage` 테이블에
호출 카운터를 기록하고, 설정된 안전 상한(`Settings.daily_quota_limit`,
기본 19,000) 도달 시 `QuotaExceededError`를 던져 상위(파이프라인)가 Job을
`PAUSED_QUOTA`로 전환할 수 있게 한다.

- 429/5xx는 지수 백오프로 최대 `Settings.max_retries`회 재시도.
- 요청 간 기본 `Settings.request_delay_sec`(0.1초) 딜레이.
- 실제 API 키가 없어도 이 모듈의 임포트/인스턴스화는 항상 가능해야 한다
  (키 검증은 실제 호출 시점에만 수행).

이 모듈은 OpenDART만 대상으로 한다. 공공데이터포털(금융위 기업기본정보)
호출은 이 클라이언트의 카운터/쿼터 로직과는 무관하므로 별도 클라이언트로
분리한다 (`FscCorpInfoClient`, 이 파일 하단 참고).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.core.db import get_session_factory
from app.models.api_usage import ApiUsage

logger = logging.getLogger(__name__)


class _ApiKeyRedactionFilter(logging.Filter):
    """httpx의 기본 INFO 로그("HTTP Request: GET <url>...")에는 요청 URL 전체가
    그대로 찍히는데, 여기에 crtfc_key(DART)/serviceKey(공공데이터포털) 값이 쿼리
    파라미터로 노출된다. 로그가 파일/외부로 전달될 가능성에 대비해 마스킹한다.
    """

    _PATTERN = re.compile(r"(crtfc_key|serviceKey)=[^&\s]+")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "crtfc_key=" in message or "serviceKey=" in message:
            record.msg = self._PATTERN.sub(r"\1=***REDACTED***", message)
            record.args = ()
        return True


logging.getLogger("httpx").addFilter(_ApiKeyRedactionFilter())


class DartApiKeyMissingError(RuntimeError):
    """DART_API_KEY가 설정되지 않은 상태에서 호출을 시도한 경우."""


class DartApiError(RuntimeError):
    """OpenDART가 HTTP 오류 또는 업무 오류(status != 000/013)를 반환한 경우."""


class QuotaExceededError(RuntimeError):
    """오늘자 호출량이 안전 상한(daily_quota_limit)에 도달한 경우.

    파이프라인(app/core/pipeline.py, M2)은 이 예외를 잡아 Job.status를
    PAUSED_QUOTA로 전환하고 progress를 체크포인트에 남겨야 한다.
    """

    def __init__(self, current_count: int, limit: int):
        self.current_count = current_count
        self.limit = limit
        super().__init__(
            f"오늘 API 호출량({current_count}건)이 안전 상한({limit}건)에 도달했습니다."
        )


# DART 공통 응답 코드: 000=정상, 013=조회된 데이터 없음(오류 아님)
_DART_OK_STATUS_CODES = {"000", "013"}


class DartClient:
    """OpenDART API 래퍼.

    키가 없어도 생성자는 항상 성공한다 (M1 요구사항 — 키 발급 전에도 앱이 뜰 것).
    실제 호출(`get_json`/`get_bytes` 등)에서만 키를 검증한다.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        session_factory: sessionmaker[Session] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._session_factory = session_factory or get_session_factory()
        self._client = http_client or httpx.AsyncClient(
            base_url=self.settings.dart_base_url, timeout=30.0
        )
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "DartClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # 쿼터 관리
    # ------------------------------------------------------------------

    def _check_and_increment_quota(self) -> int:
        """오늘 날짜의 호출 카운터를 조회 후 +1.

        상한 초과 시 증가시키지 않고 QuotaExceededError를 던진다.
        SQLite 로컬 파일 기반이라 동기 세션으로 짧게 처리한다.
        """
        today = date.today().isoformat()
        limit = self.settings.daily_quota_limit
        with self._session_factory() as db:
            row = db.execute(
                select(ApiUsage).where(ApiUsage.date == today)
            ).scalar_one_or_none()
            current = row.call_count if row else 0
            if current >= limit:
                raise QuotaExceededError(current_count=current, limit=limit)
            if row is None:
                row = ApiUsage(date=today, call_count=1)
                db.add(row)
            else:
                row.call_count = current + 1
            db.commit()
            return row.call_count

    def get_quota_status(self) -> dict[str, int]:
        """GET /api/meta/quota 에서 사용."""
        today = date.today().isoformat()
        with self._session_factory() as db:
            row = db.execute(
                select(ApiUsage).where(ApiUsage.date == today)
            ).scalar_one_or_none()
            used = row.call_count if row else 0
        limit = self.settings.daily_quota_limit
        return {"date": today, "call_count": used, "limit": limit, "remaining": max(limit - used, 0)}

    # ------------------------------------------------------------------
    # 내부 요청 실행 (재시도/백오프 포함)
    # ------------------------------------------------------------------

    def _require_api_key(self) -> str:
        if not self.settings.dart_api_key:
            raise DartApiKeyMissingError(
                "DART_API_KEY가 설정되지 않았습니다. backend/.env 에 키를 설정하세요."
            )
        return self.settings.dart_api_key

    async def _backoff_sleep(self, attempt: int) -> None:
        # 1회차 실패 후 1초, 2회차 2초, 3회차 4초 ... 지수 백오프
        await asyncio.sleep(2 ** (attempt - 1))

    async def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        raw: bool = False,
    ) -> Any:
        """path에 대해 GET 요청. raw=True면 bytes, 아니면 JSON dict 반환.

        각 실제 HTTP 시도(재시도 포함)는 OpenDART 쿼터를 1건씩 소모하므로
        시도할 때마다 카운터를 증가시킨다.
        """
        api_key = self._require_api_key()
        request_params = {**(params or {}), "crtfc_key": api_key}

        last_exc: Exception | None = None
        for attempt in range(1, self.settings.max_retries + 1):
            # 쿼터 초과면 아예 네트워크 호출을 시도하지 않는다.
            self._check_and_increment_quota()

            if self.settings.request_delay_sec > 0:
                await asyncio.sleep(self.settings.request_delay_sec)

            try:
                resp = await self._client.get(f"/{path.lstrip('/')}", params=request_params)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning("DART 요청 실패(attempt=%s, path=%s): %s", attempt, path, exc)
                if attempt < self.settings.max_retries:
                    await self._backoff_sleep(attempt)
                    continue
                raise DartApiError(f"{path} 요청 중 네트워크 오류: {exc}") from exc

            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = DartApiError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                logger.warning(
                    "DART 요청 재시도 대상 상태코드(attempt=%s, path=%s): %s",
                    attempt,
                    path,
                    resp.status_code,
                )
                if attempt < self.settings.max_retries:
                    await self._backoff_sleep(attempt)
                    continue
                raise last_exc

            resp.raise_for_status()

            if raw:
                return resp.content

            data = resp.json()
            status = data.get("status")
            if status is not None and status not in _DART_OK_STATUS_CODES:
                raise DartApiError(
                    f"{path} 업무 오류 status={status} message={data.get('message')}"
                )
            return data

        # 이론상 도달하지 않지만 안전망
        raise DartApiError(f"{path} 요청 재시도 소진") from last_exc

    # ------------------------------------------------------------------
    # 공개 API — 상세개발계획.md §4 파이프라인 STEP에서 사용
    # ------------------------------------------------------------------

    async def download_corp_code_zip(self) -> bytes:
        """고유번호 API — corpCode.xml (zip 바이너리). STEP 1에서 사용."""
        return await self._request("corpCode.xml", raw=True)

    async def get_disclosure_list(self, **params: Any) -> dict[str, Any]:
        """공시검색 API — list.json. STEP 2에서 사용 (페이징 순회는 호출부 책임)."""
        return await self._request("list.json", params=params)

    async def get_company(self, corp_code: str) -> dict[str, Any]:
        """기업개황 API — company.json. STEP 3에서 사용."""
        return await self._request("company.json", params={"corp_code": corp_code})

    async def get_document(self, rcept_no: str) -> bytes:
        """공시서류원본 API — document.xml (zip 바이너리). STEP 4에서 사용."""
        return await self._request("document.xml", params={"rcept_no": rcept_no}, raw=True)

    async def validate_key(self) -> tuple[bool, str]:
        """키 유효성 최소 확인 (POST /api/meta/validate-key 에서 사용).

        삼성전자(corp_code=00126380) 기업개황 1건 조회로 확인한다. 유효하지
        않은 키는 DART가 status=010 등으로 응답하므로 DartApiError가 발생한다.
        """
        try:
            await self.get_company("00126380")
            return True, "DART_API_KEY 유효함"
        except DartApiKeyMissingError as exc:
            return False, str(exc)
        except DartApiError as exc:
            return False, f"DART_API_KEY 검증 실패: {exc}"


def _is_quota_exceeded_response(response_text: str) -> bool:
    """data.go.kr 응답 본문이 일일 쿼터 소진을 뜻하는지 판단(재시도 스킵용).

    실측(2026-07-17)한 응답 문구는 "API token quota exceeded"였고,
    data.go.kr가 공통으로 쓰는 에러코드 22의 메시지
    "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"도 함께 흡수한다.
    """
    lowered = response_text.lower()
    return "quota" in lowered or "limited_number_of_service_requests" in lowered


class FscCorpInfoClient:
    """공공데이터포털 금융위원회_기업기본정보 API 클라이언트.

    상세개발계획.md §4-1 대응 1(우선 검증 대상)에서 사용. OpenDART 일일 쿼터와는
    별도이므로 DartClient의 api_usage 카운터를 사용하지 않는다.

    M1에서는 키 검증(`validate_key`)까지만 필요하고, 페이지네이션 일괄 수집
    로직은 M2에서 대응 1 채택이 확정된 뒤 구현한다.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = http_client or httpx.AsyncClient(
            base_url=self.settings.data_go_kr_fsc_corp_base_url, timeout=30.0
        )
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "FscCorpInfoClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    def _require_api_key(self) -> str:
        if not self.settings.data_go_kr_api_key:
            raise DartApiKeyMissingError(
                "DATA_GO_KR_API_KEY가 설정되지 않았습니다. backend/.env 에 키를 설정하세요."
            )
        return self.settings.data_go_kr_api_key

    async def _get_with_retry(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET + 지수 백오프 재시도(타임아웃/네트워크 오류/429/5xx 대상).

        A1 전수 크롤(`crawl_fsc_index`)이 12,821페이지를 순차 호출하는 동안
        ReadTimeout 1건에 크롤 태스크 전체가 조용히 죽는 문제를 실제로 겪어서
        (2026-07-16) 추가했다 — `DartClient._request`와 동일한 정책
        (`Settings.max_retries`, 지수 백오프)을 재사용한다.

        단, data.go.kr 자체의 **일일 쿼터 소진** 429는 재시도해도 절대 성공하지
        않는다(2026-07-17 Job #13 실전 실행에서 확인 — `GetFinaStatInfoService_V2`가
        DART 일일 20,000건 한도와 별개로 자체 쿼터를 갖고 있고, A3 스크리닝
        24,869개사 처리 중 소진되면 이후 모든 호출이 이 429를 반환한다). 응답
        본문에 "quota"/"LIMITED_NUMBER_OF_SERVICE_REQUESTS" 문구가 있으면
        백오프 없이 즉시 예외를 던져 호출부(`_fetch_financial_stat_with_retry`)가
        곧바로 "조회 실패, 안전하게 통과" 처리를 하도록 한다 — 건당 최대 3초
        (1초+2초 백오프)를 아낀다.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self.settings.max_retries + 1):
            try:
                resp = await self._client.get(url, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning("FSC 요청 실패(attempt=%s, url=%s): %s", attempt, url, exc)
                if attempt < self.settings.max_retries:
                    await asyncio.sleep(2 ** (attempt - 1))
                    continue
                raise DartApiError(f"{url} 요청 중 네트워크 오류: {exc}") from exc

            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = DartApiError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                if resp.status_code == 429 and _is_quota_exceeded_response(resp.text):
                    logger.warning(
                        "FSC 일일 쿼터 소진으로 판단, 재시도 없이 즉시 실패 처리(url=%s): %s",
                        url,
                        resp.text[:200],
                    )
                    raise last_exc
                logger.warning(
                    "FSC 요청 재시도 대상 상태코드(attempt=%s, url=%s): %s",
                    attempt,
                    url,
                    resp.status_code,
                )
                if attempt < self.settings.max_retries:
                    await asyncio.sleep(2 ** (attempt - 1))
                    continue
                raise last_exc

            resp.raise_for_status()
            return resp.json()

        # 이론상 도달하지 않지만 안전망
        raise DartApiError(f"{url} 요청 재시도 소진") from last_exc

    async def get_corp_basic_info(
        self, *, page_no: int = 1, num_of_rows: int = 100, corp_nm: str | None = None
    ) -> dict[str, Any]:
        """기업기본정보 조회 (페이지 단위). 실제 응답 스키마는 발급 후 확인 필요."""
        api_key = self._require_api_key()
        params: dict[str, Any] = {
            "serviceKey": api_key,
            "pageNo": page_no,
            "numOfRows": num_of_rows,
            "resultType": "json",
        }
        if corp_nm:
            params["corpNm"] = corp_nm
        return await self._get_with_retry("/getCorpOutline_V2", params)

    # 구 A3(`get_summary_financial_stat`, `getSummFinaStat_V2`를 `crno`+`bizYear`로
    # 건별 호출하던 재무 사전 스크리닝 메서드)은 M8 재설계로 호출 경로가 끊긴
    # 죽은 코드였고 2026-07-22에 물리 삭제됐다. 매출액/총자산 참고값은 이제
    # 아래 `list_summary_financial_stats`(같은 오퍼레이션을 `crno` 없이 연도
    # 단위로 전수 페이징 — fsc_financial_stat.py가 사용)로만 적재한다.

    async def list_summary_financial_stats(
        self, *, biz_year: str, page_no: int = 1, num_of_rows: int = 5000
    ) -> dict[str, Any]:
        """같은 오퍼레이션을 `crno` **없이** 호출해 회계연도 단위로 전수 페이징한다.

        2026-07-20 스파이크에서 확인한 동작(§4-10-B) — `getCorpOutline_V2`를
        `corp_nm` 없이 페이징하는 A1과 같은 방식이다. Job마다 후보 수만큼
        호출하던 것(경남 4,538회)을 **3개년 63요청**으로 대체하므로
        data.go.kr 일일 쿼터 문제가 사라진다.

        - `num_of_rows`는 **5,000까지** 받는다(100건일 때와 응답속도 차이가 거의 없음).
        - 연도별 `totalCount`: 2021 162,292 / 2022 157,604 / 2023 135,378 /
          2024 109,130 / 2025 62,774 — 최신 연도는 적재가 진행 중이라 적다.
        - 응답에 회사명이 없다. `crno`(법인등록번호)로만 조인한다.
        - `fnclDcd`가 `120`(별도)/`110`(연결)/`999`(NA)로 섞여 오므로 **호출부가
          반드시 필터**해야 한다 — 실측에서 같은 회사의 연결 매출이 별도의 1.77배였다.
        """
        api_key = self._require_api_key()
        url = f"{self.settings.data_go_kr_fsc_finstat_base_url}/getSummFinaStat_V2"
        params = {
            "serviceKey": api_key,
            "bizYear": biz_year,
            "resultType": "json",
            "pageNo": page_no,
            "numOfRows": num_of_rows,
        }
        return await self._get_with_retry(url, params)

    async def validate_key(self) -> tuple[bool, str]:
        try:
            data = await self.get_corp_basic_info(page_no=1, num_of_rows=1)
            return True, f"DATA_GO_KR_API_KEY 유효함 (응답 일부: {str(data)[:200]})"
        except DartApiKeyMissingError as exc:
            return False, str(exc)
        except httpx.HTTPStatusError as exc:
            return False, f"DATA_GO_KR_API_KEY 검증 실패: HTTP {exc.response.status_code}"
        except httpx.HTTPError as exc:
            return False, f"DATA_GO_KR_API_KEY 검증 중 네트워크 오류: {exc}"
