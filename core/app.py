"""전결 서버 = P1 전결 코어(REST API) + P3 웹 화면 (설계서 3.3)."""
from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, services
from .api import router as api_router
from .config import get_settings
from .errors import ServiceError

log = logging.getLogger("approval")


async def _ticker(seconds: float) -> None:
    while True:
        await asyncio.sleep(seconds)
        try:
            await anyio.to_thread.run_sync(services.run_tick)
        except Exception:  # 주기 작업이 서버를 멈추게 하지 않는다
            log.exception("알림 주기 작업 실패")


_WRITES = [(re.compile(r"^/api/plans$"), "create_plan"), (re.compile(r"^/api/plans/([^/]+)/approve$"), "approve_plan"),
           (re.compile(r"^/api/plans/([^/]+)/cancel$"), "cancel_plan"), (re.compile(r"^/api/plans/([^/]+)/drafts$"), "save_drafts"),
           (re.compile(r"^/api/drafts/confirm$"), "confirm_drafts"), (re.compile(r"^/api/drafts/submit$"), "submit_drafts")]


def _write_action(method: str, path: str) -> tuple[str, str] | None:
    """쓰기 API면 (작업 이름, 대상)을 돌려준다. 읽기(POST로 받는 확인 툴 포함)는 None."""
    if method != "POST":
        return None
    for rx, name in _WRITES:
        m = rx.match(path)
        if m:
            return name, (m.group(1) if m.groups() else "")
    return None


def create_app(start_ticker: bool = True) -> FastAPI:
    from web.routes import router as web_router

    settings = get_settings()
    db.init_engine()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(_ticker(settings.tick_seconds)) if start_ticker else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="목업 전결 시스템 (DEMO)", lifespan=lifespan,
                  description="P1 전결 코어 REST API와 P3 웹 화면. 모든 데이터는 시연용 더미다.")

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError):
        if request.url.path.startswith("/api"):
            action = _write_action(request.method, request.url.path)
            if action and exc.code != "UNAUTHORIZED":
                try:
                    await anyio.to_thread.run_sync(services.audit_failure, request.headers.get("x-approval-token"),
                                                   action[0], action[1], exc)
                except Exception:
                    log.exception("실패 기록을 남기지 못함")
            return JSONResponse(exc.to_dict(), status_code=exc.status)
        from web.routes import render_error
        return render_error(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse({"ok": False, "code": "BAD_REQUEST", "message": "입력 형식이 잘못되었습니다.",
                             "fix_hint": "툴 입력 형식을 확인하세요.", "details": exc.errors()[:5]}, status_code=400)

    app.include_router(api_router)
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent.parent / "web" / "static"), name="static")
    return app
