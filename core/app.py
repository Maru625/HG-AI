"""전결 서버 = P1 전결 코어(REST API) + P3 웹 화면 (설계서 3.3)."""
from __future__ import annotations

import asyncio
import logging
import os
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


# 한 포트 배포(approval-server deploy)에서 MCP 연결 주소. 시연 제어 패널이 읽는다.
MCP_INFO: dict = {}


def _on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def mcp_mount_info(settings, secret: str, paths: dict, conns: list[dict]) -> dict:
    from mcp_server.http_multi import bearer_token
    return {"mounted": True, "token_url": settings.base_url + "/mcp", "connections": [
        {"slug": slug, "name": "approval_" + slug.replace("-", "_"), "mode": c["mode"], "label": c.get("label", ""),
         "url": settings.base_url + paths[slug], "bearer": bearer_token(secret, slug), "default": i < 2}
        for i, c in enumerate(conns) for slug in [c.get("slug") or c["mode"]]]}


def create_app(start_ticker: bool = True, mount_mcp: bool | None = None) -> FastAPI:
    """mount_mcp=True(또는 APPROVAL_MCP_MOUNT=1)면 MCP 연결을 같은 포트의 /mcp/<비밀값>/<slug>에 함께 연다(배포용).
    MCP 툴은 REST API를 APPROVAL_INTERNAL_URL(기본 http://127.0.0.1:<포트>)로 부른다."""
    from web.routes import router as web_router

    settings = get_settings()
    db.init_engine()
    added = db.ensure_schema()
    if added:
        log.warning("예전 DB에 새 열을 더했습니다: %s", ", ".join(added))
    if mount_mcp is None:
        mount_mcp = _on("APPROVAL_MCP_MOUNT")
    mcp_app = None
    api_key = None           # 배포 모드에서 REST API를 MCP 툴만 부르게 하는 내부 열쇠
    MCP_INFO.clear()
    if mount_mcp:
        from mcp_server.http_multi import build_app, internal_key, load_connections, load_or_create_secret, public_hosts
        secret = os.environ.get("APPROVAL_MCP_SECRET") or load_or_create_secret(settings.db_path.parent / "mcp_secret.txt")
        internal = os.environ.get("APPROVAL_INTERNAL_URL", f"http://127.0.0.1:{settings.port}")
        conns = load_connections(settings.seed_dir)
        hosts = public_hosts(settings.base_url, os.environ.get("APPROVAL_PUBLIC_HOSTS"))
        mcp_app, paths = build_app(internal, conns, secret, check_host=False, allowed_hosts=hosts or None,
                                   api_key=internal_key(secret))
        if not _on("APPROVAL_API_PUBLIC"):
            api_key = internal_key(secret)
        MCP_INFO.update(mcp_mount_info(settings, secret, paths, conns))
        MCP_INFO["api_public"] = api_key is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(_ticker(settings.tick_seconds)) if start_ticker else None
        if mcp_app is not None:
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        else:
            yield
        if task:
            task.cancel()

    app = FastAPI(title="목업 전결 시스템 (DEMO)", lifespan=lifespan,
                  description="P1 전결 코어 REST API와 P3 웹 화면. 모든 데이터는 시연용 더미다.")
    app.state.api_internal_key = api_key
    if os.environ.get("APPROVAL_SITE_KEY"):
        from web.gate import SiteGate
        app.add_middleware(SiteGate, key=os.environ["APPROVAL_SITE_KEY"])

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

    @app.get("/healthz", include_in_schema=False)
    def _healthz():
        from starlette.responses import PlainTextResponse
        return PlainTextResponse("approval-demo ok" + (" + MCP" if mcp_app is not None else ""))

    if mcp_app is not None:
        for r in mcp_app.routes:
            if getattr(r, "path", "") != "/healthz":
                app.router.routes.append(r)
    app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent.parent / "web" / "static"), name="static")
    return app
