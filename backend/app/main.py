"""FastAPI 엔트리포인트.

상세개발계획.md §3, §8(M1/M2): 시작 시 SQLite DB/테이블을 생성하고
meta/jobs/results 라우터를 등록한다. jobs/results는 M2에서 파이프라인
(app/core/pipeline.py)이 준비되어 함께 등록되었다.

로컬 실행:
    cd backend
    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import jobs, meta, results
from app.config import get_settings
from app.core.db import create_all_tables

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 번들된 읽기전용 리소스(프론트엔드 빌드 산출물) 기준 디렉터리.
# exe로 패키징된 경우 launcher.py가 PyInstaller 번들 임시폴더(sys._MEIPASS)를
# DART_SEARCH_RESOURCE_DIR로 넘겨준다. 없으면(=소스 실행) backend/ 디렉터리를 쓴다
# — 이 경우 보통 frontend/dist가 없어 아래 마운트는 조용히 건너뛴다(dev 서버는
# vite가 별도로 프론트를 서빙하므로 문제 없음).
_resource_env = os.environ.get("DART_SEARCH_RESOURCE_DIR")
RESOURCE_DIR = Path(_resource_env) if _resource_env else Path(__file__).resolve().parent.parent
FRONTEND_DIST_DIR = RESOURCE_DIR / "frontend_dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    create_all_tables()
    logger.info("dart-search backend 시작 — DB/테이블 준비 완료 (%s)", settings.database_url)
    yield


app = FastAPI(title="dart-search backend", version="0.1.0", lifespan=lifespan)

app.include_router(meta.router)
app.include_router(jobs.router)
app.include_router(results.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# --- 프론트엔드 정적 파일 서빙 (exe 배포용) ---
# `npm run build` 산출물(frontend/dist)이 frontend_dist라는 이름으로 존재할
# 때만 활성화된다. API 라우터들보다 반드시 뒤에 등록해야 catch-all이 /api를
# 가로채지 않는다. React Router(BrowserRouter)를 쓰므로 정적 파일이 아닌
# 모든 경로는 index.html로 폴백시켜 클라이언트 라우팅이 동작하게 한다.
if FRONTEND_DIST_DIR.is_dir():
    assets_dir = FRONTEND_DIST_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        candidate = FRONTEND_DIST_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST_DIR / "index.html")
