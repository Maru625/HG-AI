"""한 프로세스·한 포트에서 연결 여러 개(기안자용·결재자용)를 streamable HTTP로 여는 앱.

ChatGPT처럼 원격 MCP만 붙는 AI 서비스용이다. 연결마다 주소가 다르고, 주소 안의 비밀값이 접근 토큰 역할을 한다.
  /mcp/<비밀값>/drafter   → 김지훈 선임(기안자용) 툴 14개
  /mcp/<비밀값>/approver  → 박서연 팀장(결재자용) 읽기 툴 8개
"""
from __future__ import annotations

import contextlib
import json
import secrets
from pathlib import Path

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from .server import Api, build


def load_connections(seed_dir: Path) -> list[dict]:
    return json.loads((Path(seed_dir) / "connections.json").read_text(encoding="utf-8"))


def load_or_create_secret(path: Path, given: str | None = None) -> str:
    """주소 비밀값. 한 번 만들면 파일에 두고 다시 쓴다(AI 서비스에 등록한 주소가 재시작해도 바뀌지 않게)."""
    if given:
        return given
    if path.exists():
        s = path.read_text(encoding="utf-8").strip()
        if s:
            return s
    s = secrets.token_urlsafe(12).replace("-", "x").replace("_", "y")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(s, encoding="utf-8")
    return s


def build_app(server_url: str, connections: list[dict], secret: str, *, check_host: bool = False,
              json_response: bool = True, stateless: bool = True) -> tuple[Starlette, dict]:
    """연결별 MCP 서버를 만들어 한 Starlette 앱으로 묶는다. 돌려주는 paths: {mode: 경로}.

    - check_host=False: 터널(공개 주소)로 들어오는 요청의 Host가 매번 달라서 Host 검사를 끈다. 주소의 비밀값이 문지기다.
    - json_response=True: 응답을 SSE 스트림 대신 JSON 한 덩어리로 보낸다. Cloudflare 빠른 터널(trycloudflare)은
      SSE를 지원하지 않는다고 문서에 적혀 있어서다. 이 서버의 툴은 진행 알림 없이 결과만 돌려주므로 잃는 것이 없다.
    - stateless=True: 세션을 서버에 두지 않는다. 터널이 다시 연결되거나 AI 서비스가 요청을 여러 곳에서 보내도 끊기지 않는다.
    """
    sec = (None if check_host else TransportSecuritySettings(enable_dns_rebinding_protection=False))
    apps, paths = [], {}
    for c in connections:
        mode = c["mode"]
        path = f"/mcp/{secret}/{mode}"
        srv = build(Api(server_url, c["token"]), mode, c.get("label", ""))
        apps.append(srv.streamable_http_app(streamable_http_path=path, transport_security=sec,
                                            json_response=json_response, stateless_http=stateless))
        paths[mode] = path

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with contextlib.AsyncExitStack() as stack:
            for a in apps:
                await stack.enter_async_context(a.router.lifespan_context(a))
            yield

    async def health(_request):
        return PlainTextResponse("approval-demo MCP ok")

    routes = [Route("/healthz", health)]
    for a in apps:
        routes.extend(a.routes)
    return Starlette(routes=routes, lifespan=lifespan), paths
