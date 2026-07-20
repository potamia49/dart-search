"""SQLite 엔진/세션 팩토리.

단일 파일 SQLite를 사용하되, `Settings.database_url`을 그대로 SQLAlchemy에
전달하므로 배포 시 PostgreSQL 접속 문자열로 바꾸기만 하면 전환 가능하다
(CLAUDE.md 아키텍처 원칙).
"""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
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
    engine = create_engine(
        settings.database_url,
        connect_args=_connect_args(settings.database_url),
    )
    if engine.dialect.name == "sqlite":
        # A1 전수 크롤(fsc_corp_index, 약 128만 행 upsert)이 기본 저널 모드
        # (journal_mode=DELETE, synchronous=FULL)에서는 커밋마다 fsync가 걸려
        # 실측 약 3.4행/초로 병목이 됐다(2026-07-16) — WAL은 매 커밋마다
        # 메인 DB 파일 전체를 동기화하지 않고 WAL 파일에 append만 하므로
        # 훨씬 빠르다. synchronous=NORMAL은 WAL 모드에서 공식적으로 안전한
        # 조합(OS 크래시 시에도 커밋된 데이터는 보존되며, 앱 크래시 시에는
        # 애초에 fsync 여부와 무관)이라 데이터 정합성 저하 없이 채택했다.
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


# 2026-07-15 M6 재설계로 기존 테이블(jobs/results)에 추가된 컬럼.
# `Base.metadata.create_all()`은 신규 테이블만 만들고 기존 테이블의 컬럼을
# 추가해주지 않으므로(`backend/dart_search.db`는 이미 실 데이터가 든 상태라
# 삭제할 수 없다), `ALTER TABLE`로 직접 추가한다. Alembic 등 정식 마이그레이션
# 도구는 도입하지 않는다(CLAUDE.md 관행 — 이 프로젝트는 지금까지 이런 스키마
# 변경을 전부 ad-hoc ALTER TABLE로 처리해왔다).
_JOBS_NEW_COLUMNS: dict[str, str] = {
    "phase": "TEXT DEFAULT 'CANDIDATES'",
    "cond_total_assets": "TEXT",
}
_RESULTS_NEW_COLUMNS: dict[str, str] = {
    "excluded_by_assets": "INTEGER DEFAULT 0",
    "excluded_manually": "INTEGER DEFAULT 0",
    # 현금흐름표 4항목 (§4-8, 2026-07-19)
    "cf_operating_cur": "INTEGER",
    "cf_operating_prv": "INTEGER",
    "cf_investing_cur": "INTEGER",
    "cf_investing_prv": "INTEGER",
    "cf_financing_cur": "INTEGER",
    "cf_financing_prv": "INTEGER",
    "cf_ending_cash_cur": "INTEGER",
    "cf_ending_cash_prv": "INTEGER",
    # 감사인(회계법인/감사반) 이름·주소 (2026-07-20, app/parsers/auditor.py)
    "auditor_name": "TEXT",
    "auditor_address": "TEXT",
    # 금융위 요약재무 참고값 + 기준연도 (2026-07-20 M8 3단계, §4-10-C/D).
    # 필터 판정용이 아니라 후보 목록 표시용이다.
    "ref_revenue": "INTEGER",
    "ref_total_assets": "INTEGER",
    "ref_fin_year": "TEXT",
}
# financial_snapshots(2026-07-15 STEP7 신설)에도 §4-8 CF 4컬럼을 추가한다.
# 이 테이블은 이미 실 데이터가 있어 create_all이 컬럼을 못 붙이므로 ALTER 필요.
_FINANCIAL_SNAPSHOTS_NEW_COLUMNS: dict[str, str] = {
    "cf_operating": "INTEGER",
    "cf_investing": "INTEGER",
    "cf_financing": "INTEGER",
    "cf_ending_cash": "INTEGER",
    # 이 행의 수치·rcept_no가 "그 연도를 당기로 하는" 공시에서 나왔는지(1) 아니면
    # 다음 연도 공시의 전기 열에서 임시로 채워졌는지(0). 기존 행은 후자일 수
    # 있으므로 기본값 0으로 붙인다(연도별 원문 보기 라벨에 그대로 쓰인다).
    "from_current_period": "INTEGER DEFAULT 0",
}


def _existing_columns(engine: Engine, table_name: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}  # row[1] = 컬럼명


def _ensure_columns(engine: Engine, table_name: str, columns: dict[str, str]) -> None:
    """`table_name`에 `columns`(컬럼명 -> DDL 타입) 중 없는 컬럼만 ALTER TABLE로 추가.

    여러 번 호출돼도 안전하다(컬럼이 이미 있으면 skip) — 앱 재기동 시마다
    호출돼도 문제없다.
    """
    existing = _existing_columns(engine, table_name)
    with engine.begin() as conn:
        for name, ddl_type in columns.items():
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl_type}")


def run_schema_migrations() -> None:
    """SQLite ad-hoc 마이그레이션 — 기존 테이블(jobs/results)에 M6 신규 컬럼 추가.

    `create_all_tables()`가 새 테이블(예: fsc_corp_index)을 만든 뒤 호출한다.
    SQLite가 아닌 DB(추후 PostgreSQL 전환 시)에서는 아무 것도 하지 않는다 —
    이 ad-hoc 방식은 SQLite 전용이며, PostgreSQL 전환 시점에는 정식
    마이그레이션 도구 도입을 재검토해야 한다.
    """
    engine = get_engine()
    if engine.dialect.name != "sqlite":
        return
    _ensure_columns(engine, "jobs", _JOBS_NEW_COLUMNS)
    _ensure_columns(engine, "results", _RESULTS_NEW_COLUMNS)
    _ensure_columns(engine, "financial_snapshots", _FINANCIAL_SNAPSHOTS_NEW_COLUMNS)


def create_all_tables() -> None:
    """앱 시작 시 호출 — 스키마가 없으면 생성 (상세개발계획.md §5)."""
    from app.models import Base  # 지연 임포트: 모델 등록 순서 문제 방지

    Base.metadata.create_all(bind=get_engine())
    run_schema_migrations()


def get_db() -> Generator[Session, None, None]:
    """FastAPI 의존성 주입용 세션 제너레이터."""
    session_factory = get_session_factory()
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
