"""FastAPI 엔트리포인트.

상세개발계획.md §3, §8(M1): 시작 시 SQLite DB/테이블을 생성하고 meta 라우터를
등록한다. jobs/results 라우터는 M2 이후 파이프라인(app/core/pipeline.py)이
준비되면 등록한다.

로컬 실행:
    cd backend
    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import meta
from app.config import get_settings
from app.core.db import create_all_tables

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    create_all_tables()
    logger.info("dart-search backend 시작 — DB/테이블 준비 완료 (%s)", settings.database_url)
    yield


app = FastAPI(title="dart-search backend", version="0.1.0", lifespan=lifespan)

app.include_router(meta.router)

# TODO(M2): app.api.jobs.router, app.api.results.router 등록


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
