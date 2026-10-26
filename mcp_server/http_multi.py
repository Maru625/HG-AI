"""한 프로세스·한 포트에서 연결 여러 개(기안자용·결재자용)를 streamable HTTP로 여는 앱.

ChatGPT처럼 원격 MCP만 붙는 AI 서비스용이다. 연결마다 주소가 다르고, 주소 안의 비밀값이 접근 토큰 역할을 한다.
주소의 마지막 부분은 연결의 slug다(seed/connections.json).
  /mcp/<비밀값>/drafter        → 김지훈 선임(기안자용) 툴 14개      ← 시연 기본
  /mcp/<비밀값>/approver       → 박서연 팀장(결재자용) 읽기 툴 8개   ← 시연 기본
  /mcp/<비밀값>/drafter-jung   → 정하늘 책임(기안자용)  … 여러 사례용 추가 연결(필요한 것만 등록)
  /mcp  + 헤더 Authorization: Bearer <헤더 토큰>  → 토큰의 연결로 넘긴다(배포용. 주소에 비밀값을 넣지 않는 방식)

헤더 토큰은 주소 비밀값에서 연결마다 만든다(bearer_token). 시드의 REST 토큰(connections.json의 token)은
문서에 적혀 있어 누구나 알 수 있으므로, 밖으로 열리는 MCP 인증에는 쓰지 않는다.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import secrets
from pathlib import Path

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
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


def conn_slug(c: dict) -> str:
    return c.get("slug") or c["mode"]


def conn_name(c: dict) -> str:
    """AI 서비스에 등록할 때 쓰는 이름 (예: approval_drafter, approval_drafter_jung)."""
    return "approval_" + conn_slug(c).replace("-", "_")


def bearer_token(secret: str, slug: str) -> str:
    """헤더 토큰 방식(/mcp + Authorization: Bearer)의 연결별 토큰. 비밀값이 같으면 재시작해도 같다."""
    return f"amk-{slug}-" + hmac.new(secret.encode(), f"bearer:{slug}".encode(), hashlib.sha256).hexdigest()[:20]


def internal_key(secret: str) -> str:
    """배포 모드에서 MCP 툴이 같은 서버의 REST API를 부를 때 붙이는 내부 열쇠(X-Approval-Internal)."""
    return hmac.new(secret.encode(), b"internal-api", hashlib.sha256).hexdigest()


def public_hosts(base_url: str | None, extra: str | None = None) -> list[str]:
    """공개 주소(APPROVAL_BASE_URL)와 추가 호스트 목록(쉼표)에서 Host 검사에 쓸 호스트 이름을 뽑는다."""
    from urllib.parse import urlparse
    hosts = []
    if base_url:
        h = urlparse(base_url).hostname
        if h and h not in ("localhost", "127.0.0.1"):
            hosts.append(h)
    hosts += [x.strip() for x in (extra or "").split(",") if x.strip()]
    return list(dict.fromkeys(hosts))


def build_app(server_url: str, connections: list[dict], secret: str, *, check_host: bool = False,
              json_response: bool = True, stateless: bool = True,
              allowed_hosts: list[str] | None = None, api_key: str | None = None) -> tuple[Starlette, dict]:
    """연결별 MCP 서버를 만들어 한 Starlette 앱으로 묶는다. 돌려주는 paths: {slug: 경로} (slug가 없으면 mode).

    - check_host=False: 터널(공개 주소)로 들어오는 요청의 Host가 매번 달라서 Host 검사를 끈다. 주소의 비밀값이 문지기다.
    - json_response=True: 응답을 SSE 스트림 대신 JSON 한 덩어리로 보낸다. Cloudflare 빠른 터널(trycloudflare)은
      SSE를 지원하지 않는다고 문서에 적혀 있어서다. 이 서버의 툴은 진행 알림 없이 결과만 돌려주므로 잃는 것이 없다.
    - stateless=True: 세션을 서버에 두지 않는다. 터널이 다시 연결되거나 AI 서비스가 요청을 여러 곳에서 보내도 끊기지 않는다.
    - allowed_hosts: 배포 때 공개 도메인을 주면 그 Host만 받는다(DNS 리바인딩 방어). 주지 않으면 check_host에 따른다.
    - api_key: 배포 모드의 내부 열쇠. REST API가 밖에서 직접 불리지 않게 MCP 툴 호출에만 붙인다.
    """
    if allowed_hosts:
        hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"] + [h for x in allowed_hosts for h in (x, f"{x}:*")]
        sec = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                        allowed_origins=[f"https://{x}" for x in allowed_hosts] + [f"http://{x}" for x in allowed_hosts])
    else:
        sec = (None if check_host else TransportSecuritySettings(enable_dns_rebinding_protection=False))
    apps, paths, by_token = [], {}, {}
    for c in connections:
        mode, slug = c["mode"], conn_slug(c)
        if slug in paths:
            raise ValueError(f"연결 주소(slug)가 겹칩니다: {slug}")
        path = f"/mcp/{secret}/{slug}"
        srv = build(Api(server_url, c["token"], api_key), mode, c.get("label", ""))
        apps.append(srv.streamable_http_app(streamable_http_path=path, transport_security=sec,
                                            json_response=json_response, stateless_http=stateless))
        paths[slug] = path
        # 같은 연결을 /mcp + Bearer 토큰으로도 연다(툴 정의는 같고, 주소만 다르다)
        tok_app = build(Api(server_url, c["token"], api_key), mode, c.get("label", "")).streamable_http_app(
            streamable_http_path="/mcp", transport_security=sec, json_response=json_response, stateless_http=stateless)
        apps.append(tok_app)
        by_token[bearer_token(secret, slug)] = tok_app

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with contextlib.AsyncExitStack() as stack:
            for a in apps:
                await stack.enter_async_context(a.router.lifespan_context(a))
            yield

    async def health(_request):
        return PlainTextResponse("approval-demo MCP ok")

    routes = [Route("/healthz", health), Route("/mcp", endpoint=TokenDispatch(by_token), methods=["GET", "POST", "DELETE"])]
    for a in apps:
        if a not in by_token.values():
            routes.extend(a.routes)
    return Starlette(routes=routes, lifespan=lifespan), paths


class TokenDispatch:
    """/mcp 요청을 Authorization: Bearer <연결 토큰>에 맞는 연결로 넘기는 ASGI 앱.

    토큰이 곧 사용자 선택이다(로그인 없음). 주소 안 비밀값과 달리 토큰이 접속 기록·브라우저 기록에 남지 않는다.
    토큰 비교는 사전 조회로 한다(토큰 길이·형식이 같아 시간차로 알아낼 정보가 거의 없다).
    ChatGPT 데스크톱 앱(Codex와 같은 MCP 설정)은 헤더 토큰을 넣을 수 있다고 문서에 적혀 있다.
    """

    def __init__(self, apps_by_token: dict):
        self.apps = apps_by_token

    async def __call__(self, scope, receive, send):
        auth = ""
        for k, v in scope.get("headers") or []:
            if k == b"authorization":
                auth = v.decode("latin-1")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        app = self.apps.get(token)
        if app is None:
            resp = JSONResponse({"ok": False, "code": "UNAUTHORIZED",
                                 "message": "헤더 토큰이 없거나 잘못되었습니다. 헤더에 Authorization: Bearer <헤더 토큰>을 넣으세요.",
                                 "fix_hint": "헤더 토큰(amk-로 시작)은 시연 제어 패널(/demo)의 AI 연결 표에 있습니다."},
                                status_code=401, headers={"WWW-Authenticate": 'Bearer realm="approval-demo"'})
            await resp(scope, receive, send)
            return
        await app(scope, receive, send)
