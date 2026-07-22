"""데스크톱 실행용 진입점 (PyInstaller로 exe 패키징되는 대상).

더블클릭 시:
  1) exe 옆에 .env가 없으면 템플릿을 만들어 메모장으로 열고 안내 후 종료한다
     (최초 1회, API 키를 사용자가 직접 입력하게 하기 위함 — CLAUDE.md 원칙상
     API 키를 exe에 하드코딩하지 않는다).
  2) .env가 있으면 백엔드(uvicorn)를 127.0.0.1에서 구동하고 준비되는 대로
     기본 브라우저를 자동으로 연다.

소스에서 그냥 실행할 수도 있다: `python launcher.py` (backend/ 디렉터리 기준).
"""

from __future__ import annotations

import ctypes
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

PORT = int(os.environ.get("DART_SEARCH_PORT", "8000"))


def _app_dir() -> Path:
    """DB/.env 등이 저장될, exe 옆의 쓰기 가능한 디렉터리."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resource_dir() -> Path:
    """PyInstaller가 번들한 읽기전용 리소스(프론트엔드 빌드 등)가 풀리는 디렉터리."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


def _show_message(text: str, title: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
    except Exception:
        print(f"[{title}] {text}")


def _ensure_env(app_dir: Path, resource_dir: Path) -> bool:
    """.env가 이미 있으면 True. 없으면 템플릿을 만들고 False(=여기서 종료)."""
    env_path = app_dir / ".env"
    if env_path.exists():
        return True

    template = resource_dir / ".env.example"
    if template.exists():
        shutil.copy(template, env_path)
    else:
        env_path.write_text(
            "DART_API_KEY=\nDATA_GO_KR_API_KEY=\nDATABASE_URL=sqlite:///./dart_search.db\n"
            "CORP_CACHE_DIR=./data/corp_cache\nDOCUMENT_CACHE_DIR=./data/documents\n",
            encoding="utf-8",
        )

    try:
        # 사용자 PC에 .env 확장자가 다른 프로그램(보안 프로그램 등)에 연결돼
        # 있을 수 있어(실측 확인됨) os.startfile 기본 연결에 맡기지 않고
        # 메모장을 명시적으로 지정한다.
        subprocess.Popen(["notepad.exe", str(env_path)])
    except Exception:
        try:
            os.startfile(str(env_path))  # noqa: S606
        except Exception:
            pass

    _show_message(
        "처음 실행되었습니다.\n\n"
        "방금 열린 .env 파일에 DART_API_KEY와 DATA_GO_KR_API_KEY 값을 입력한 뒤\n"
        "저장하고 파일을 닫아 주세요.\n\n"
        "그 다음 이 프로그램을 다시 실행하면 정상적으로 시작됩니다.",
        "dart-search 최초 설정",
    )
    return False


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _open_browser_when_ready(url: str) -> None:
    import httpx

    for _ in range(120):
        try:
            resp = httpx.get(url + "/health", timeout=1.0)
            if resp.status_code == 200:
                webbrowser.open(url)
                return
        except Exception:
            pass
        time.sleep(0.5)
    webbrowser.open(url)  # 타임아웃돼도 일단 열어본다


def main() -> None:
    app_dir = _app_dir()
    resource_dir = _resource_dir()
    os.chdir(app_dir)

    if not _ensure_env(app_dir, resource_dir):
        return

    os.environ["DART_SEARCH_APP_DIR"] = str(app_dir)
    os.environ["DART_SEARCH_RESOURCE_DIR"] = str(resource_dir)

    url = f"http://127.0.0.1:{PORT}"

    if _port_in_use(PORT):
        # 이미 실행 중(다른 창에서 켜둔 상태) — 새 창만 열어준다.
        webbrowser.open(url)
        return

    import uvicorn

    from app.main import app as fastapi_app

    threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(fastapi_app, host="127.0.0.1", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
