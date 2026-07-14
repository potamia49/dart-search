"""pytest 공통 픽스처.

M2 파이프라인/필터 테스트는 실제 OpenDART 호출 없이(DartClient를 모킹해)
동작해야 하므로, 매 테스트마다 독립된 인메모리 SQLite DB를 만들어 준다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base


@pytest.fixture
def db_session_factory():
    """테스트마다 격리된 인메모리 SQLite 세션 팩토리.

    StaticPool을 사용해 여러 세션이 같은 인메모리 DB 커넥션을 공유하게 한다
    (기본 설정이면 세션마다 새 인메모리 DB가 생겨 데이터가 보이지 않는다).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    engine.dispose()
