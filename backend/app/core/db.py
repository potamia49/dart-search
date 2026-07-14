"""SQLite 엔진/세션 팩토리.

단일 파일 SQLite를 사용하되, `Settings.database_url`을 그대로 SQLAlchemy에
전달하므로 배포 시 PostgreSQL 접속 문자열로 바꾸기만 하면 전환 가능하다
(CLAUDE.md 아키텍처 원칙).
"""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings


def _connect_args(database_url: str) -> dict:
    # SQLite는 기본적으로 커넥션을 만든 스레드에서만 사용 가능 → FastAPI의
    # BackgroundTasks/여러 요청 스레드에서 공유하려면 check_same_thread=False 필요.
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


@lru_cache
def get_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    return create_engine(
        settings.database_url,
        connect_args=_connect_args(settings.database_url),
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def create_all_tables() -> None:
    """앱 시작 시 호출 — 스키마가 없으면 생성 (상세개발계획.md §5)."""
    from app.models import Base  # 지연 임포트: 모델 등록 순서 문제 방지

    Base.metadata.create_all(bind=get_engine())


def get_db() -> Generator[Session, None, None]:
    """FastAPI 의존성 주입용 세션 제너레이터."""
    session_factory = get_session_factory()
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
