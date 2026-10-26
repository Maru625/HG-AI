"""입장 코드 (APPROVAL_SITE_KEY). 배포한 웹 화면을 코드를 아는 사람만 쓰게 하는 가벼운 문이다.

로그인이 아니다. 들어온 뒤 사용자는 지금처럼 화면 오른쪽 위에서 고른다(사용자 선택 = 신원).
- 코드를 한 번 넣으면 브라우저 쿠키(site_ok)로 30일 기억한다.
- 링크 하나로 나눠 줄 때는 https://<주소>/?enter=<코드> 처럼 붙이면 바로 들어온다(주소창에서 코드는 곧 지운다).
- /healthz, /static, /mcp…(주소 비밀값·헤더 토큰으로 지킴), /api…(연결 토큰·내부 열쇠로 지킴)는 이 문과 상관없다.
"""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import parse_qsl, quote, urlencode

from starlette.requests import cookie_parser
from starlette.responses import JSONResponse, RedirectResponse

COOKIE = "site_ok"
MAX_AGE = 30 * 86400
OPEN_PATHS = ("/healthz", "/favicon.ico")
OPEN_PREFIXES = ("/static/", "/mcp", "/api/", "/enter")


def site_hash(key: str) -> str:
    return hashlib.sha256(("approval-site:" + key).encode("utf-8")).hexdigest()


def safe_next(n: str | None) -> str:
    n = n or "/"
    return n if n.startswith("/") and not n.startswith("//") and "\\" not in n else "/"


def set_entry_cookie(resp, key: str, https: bool) -> None:
    resp.set_cookie(COOKIE, site_hash(key), max_age=MAX_AGE, httponly=True, samesite="lax", secure=https)


class SiteGate:
    """순수 ASGI 미들웨어(스트리밍 응답을 감싸지 않는다)."""

    def __init__(self, app, key: str):
        self.app, self.key, self.want = app, key, site_hash(key)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.key:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        if hmac.compare_digest(cookie_parser(headers.get("cookie", "")).get(COOKIE, ""), self.want):
            return await self.app(scope, receive, send)
        raw_qs = (scope.get("query_string") or b"").decode("latin-1")
        qs = parse_qsl(raw_qs, keep_blank_values=True)
        given = next((v for k, v in qs if k == "enter"), "")
        if given and hmac.compare_digest(given.strip().encode(), self.key.encode()):
            rest = [(k, v) for k, v in qs if k != "enter"]
            resp = RedirectResponse(path + ("?" + urlencode(rest) if rest else ""), status_code=303)
            set_entry_cookie(resp, self.key, scope.get("scheme") == "https")
            return await resp(scope, receive, send)
        nxt = quote(path + ("?" + raw_qs if raw_qs and not given else ""), safe="")
        if scope.get("method") != "GET" or headers.get("hx-request") or path.startswith(("/duel/", "/partials/")):
            resp = JSONResponse({"ok": False, "code": "ENTRY_REQUIRED", "message": "입장 코드가 필요합니다.",
                                 "fix_hint": "브라우저에서 이 주소를 열어 입장 코드를 넣으세요."},
                                status_code=401, headers={"HX-Redirect": f"/enter?next={nxt}"})
        else:
            resp = RedirectResponse(f"/enter?next={nxt}" + ("&err=1" if given else ""), status_code=303)
        await resp(scope, receive, send)
