"""애플리케이션 설정.

`.env` 파일을 pydantic-settings로 로드한다. API 키를 포함한 모든 설정값은
반드시 이 클래스를 통해서만 접근하며, 코드에 하드코딩하지 않는다
(CLAUDE.md "하지 말 것" 참고).

실제 `.env` 파일은 저장소에 커밋하지 않는다. `.env.example`을 복사해 사용할 것.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 디렉터리 (이 파일 기준 두 단계 위: app/config.py -> app/ -> backend/)
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """`.env`에서 로드되는 전역 설정.

    필드명은 .env.example의 변수명과 1:1 대응한다 (대소문자 무관).
    """

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- API 키 (미발급 상태에서는 빈 문자열 — 호출 시점에만 검증) ---
    dart_api_key: str = ""
    data_go_kr_api_key: str = ""

    # --- DB ---
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'dart_search.db').as_posix()}"

    # --- 로컬 캐시 경로 ---
    corp_cache_dir: str = str(BACKEND_DIR / "data" / "corp_cache")
    document_cache_dir: str = str(BACKEND_DIR / "data" / "documents")

    # --- OpenDART 호출 정책 (상세개발계획.md §4-5) ---
    daily_quota_limit: int = 19000
    request_delay_sec: float = 0.1
    max_retries: int = 3

    # --- 캐시 갱신 주기 (일) ---
    corp_cache_ttl_days: int = 7
    corp_profile_ttl_days: int = 180
    # fsc_corp_index(§4-7 Phase 1 A1) 전역 인덱스 TTL. corp_profile_ttl_days와
    # 같은 철학(180일)이지만 완전히 다른 캐시 테이블의 갱신 주기라 별도 설정으로 둔다.
    fsc_index_ttl_days: int = 180

    # --- 외부 API Base URL (하드코딩 X 대상은 아니지만, 한 곳에서 관리) ---
    dart_base_url: str = "https://opendart.fss.or.kr/api"
    data_go_kr_fsc_corp_base_url: str = (
        "https://apis.data.go.kr/1160100/service/GetCorpBasicInfoService_V2"
    )
    # 금융위원회_기업 재무정보 API (§4-7 스파이크로 확인된 실제 base URL,
    # 2026-07-15 — 기업기본정보(GetCorpBasicInfoService_V2)와는 별개 서비스).
    data_go_kr_fsc_finstat_base_url: str = (
        "https://apis.data.go.kr/1160100/service/GetFinaStatInfoService_V2"
    )

    def ensure_dirs(self) -> None:
        """로컬 캐시 디렉터리가 없으면 생성."""
        Path(self.corp_cache_dir).mkdir(parents=True, exist_ok=True)
        Path(self.document_cache_dir).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """설정 싱글턴. FastAPI 의존성 주입(`Depends(get_settings)`)에도 사용 가능."""
    return Settings()
