"""배포 대비 시험: 한 포트에 MCP 함께 열기, 헤더 토큰 연결, REST 내부 전용, Host 검사, 입장 코드, 운영자 키, 시연 시계 멈춤."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from core import services as S
from core.app import MCP_INFO, create_app

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
DRAFT_TOKEN = "demo-drafter-kimjihoon-7f3a"
APPR_TOKEN = "demo-approver-parkseoyeon-9c2e"


@pytest.fixture()
def deployed(fresh, monkeypatch):
    monkeypatch.setenv("APPROVAL_MCP_SECRET", "s3cret")
    monkeypatch.setenv("APPROVAL_PUBLIC_HOSTS", "demo.example.com")
    with TestClient(create_app(start_ticker=False, mount_mcp=True), base_url="https://demo.example.com") as c:
        yield c


def _rpc(c, path, body, **headers):
    r = c.post(path, json=body, headers={**ACCEPT, **headers})
    return r, (r.json() if r.headers.get("content-type", "").startswith("application/json") else None)


def test_one_port_serves_web_api_and_mcp(deployed):
    c = deployed
    assert c.get("/healthz").text == "approval-demo ok + MCP"
    assert c.get("/").status_code == 200 and c.get("/api/whoami").status_code == 401
    r, body = _rpc(c, "/mcp/s3cret/drafter", INIT)
    assert r.status_code == 200 and body["result"]["serverInfo"]["name"] == "approval-demo"
    assert "김지훈" in body["result"]["instructions"]
    r, body = _rpc(c, "/mcp/s3cret/approver-choi", LIST)
    assert r.status_code == 200 and len(body["result"]["tools"]) == 8
    assert _rpc(c, "/mcp/wrong/drafter", INIT)[0].status_code == 404
    assert [x["slug"] for x in MCP_INFO["connections"]][:2] == ["drafter", "approver"]


def _bearer(slug: str) -> str:
    return next(x["bearer"] for x in MCP_INFO["connections"] if x["slug"] == slug)


def test_bearer_token_endpoint(deployed):
    c = deployed
    tok = _bearer("drafter")
    assert tok.startswith("amk-drafter-") and tok != DRAFT_TOKEN
    r, body = _rpc(c, "/mcp", INIT, Authorization=f"Bearer {tok}")
    assert r.status_code == 200 and "김지훈" in body["result"]["instructions"]
    r, body = _rpc(c, "/mcp", LIST, Authorization=f"Bearer {tok}")
    assert len(body["result"]["tools"]) == 14
    r, body = _rpc(c, "/mcp", LIST, Authorization=f"Bearer {_bearer('approver-yoon')}")
    assert len(body["result"]["tools"]) == 8 and "윤재민" in _rpc(c, "/mcp", INIT, Authorization=f"Bearer {_bearer('approver-yoon')}")[1]["result"]["instructions"]
    r, body = _rpc(c, "/mcp", INIT)
    assert r.status_code == 401 and body["code"] == "UNAUTHORIZED" and r.headers["www-authenticate"].startswith("Bearer")
    assert _rpc(c, "/mcp", INIT, Authorization="Bearer nope")[0].status_code == 401
    # 문서에 공개된 시드 토큰으로는 헤더 토큰 연결이 열리지 않는다
    assert _rpc(c, "/mcp", INIT, Authorization=f"Bearer {DRAFT_TOKEN}")[0].status_code == 401


def test_rest_is_internal_only_when_deployed(deployed, fresh, monkeypatch):
    c = deployed
    r = c.get("/api/whoami", headers={"X-Approval-Token": DRAFT_TOKEN})
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHORIZED" and "APPROVAL_API_PUBLIC" in r.json()["fix_hint"]
    from mcp_server.http_multi import internal_key
    r = c.get("/api/whoami", headers={"X-Approval-Token": DRAFT_TOKEN, "X-Approval-Internal": internal_key("s3cret")})
    assert r.status_code == 200 and r.json()["name"] == "김지훈"
    c.cookies.set("demo_user", "E3001")
    page = c.get("/demo").text
    assert _bearer("drafter") in page and DRAFT_TOKEN not in page and "밖에서 직접 부르면 401" in page
    monkeypatch.setenv("APPROVAL_API_PUBLIC", "1")
    with TestClient(create_app(start_ticker=False, mount_mcp=True)) as c2:
        assert c2.get("/api/whoami", headers={"X-Approval-Token": DRAFT_TOKEN}).status_code == 200
        assert DRAFT_TOKEN in c2.get("/demo").text


def test_local_mode_keeps_rest_open(fresh):
    with TestClient(create_app(start_ticker=False)) as c:
        assert c.get("/api/whoami", headers={"X-Approval-Token": DRAFT_TOKEN}).status_code == 200
        assert c.get("/healthz").text == "approval-demo ok" and MCP_INFO == {}


def test_site_entry_code(fresh, monkeypatch):
    monkeypatch.setenv("APPROVAL_SITE_KEY", "hanwha-2026")
    with TestClient(create_app(start_ticker=False)) as c:
        r = c.get("/inbox?x=1", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/enter?next=%2Finbox%3Fx%3D1")
        assert "입장 코드를 넣으세요" in c.get(r.headers["location"]).text
        assert c.get("/healthz").status_code == 200 and c.get("/static/app.css").status_code == 200
        assert c.get("/api/whoami", headers={"X-Approval-Token": DRAFT_TOKEN}).status_code == 200   # REST는 토큰으로 지킨다
        r = c.get("/partials/notifications", headers={"HX-Request": "true"})
        assert r.status_code == 401 and r.headers["hx-redirect"].startswith("/enter")
        assert c.post("/switch-user", data={"emp_no": "E2001", "next": "/"}, follow_redirects=False).status_code == 401
        r = c.post("/enter", data={"code": "wrong", "next": "/inbox"}, follow_redirects=False)
        assert "err=1" in r.headers["location"] and "site_ok" not in c.cookies
        r = c.post("/enter", data={"code": " hanwha-2026 ", "next": "//evil.example"}, follow_redirects=False)
        assert r.headers["location"] == "/" and c.cookies.get("site_ok")
        assert c.get("/inbox").status_code == 200
    with TestClient(create_app(start_ticker=False)) as c:                    # 링크 하나로 입장
        r = c.get("/rooms?enter=hanwha-2026&start=2026-11-16", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/rooms?start=2026-11-16" and c.cookies.get("site_ok")
        assert c.get("/rooms?start=2026-11-16").status_code == 200
        c.cookies.clear()
        r = c.get("/?enter=nope", follow_redirects=False)
        assert r.headers["location"] == "/enter?next=%2F&err=1"


def test_host_check_blocks_other_domains(deployed):
    c = deployed
    r = c.post("/mcp/s3cret/drafter", json=INIT, headers={**ACCEPT, "Host": "evil.example.net"})
    assert r.status_code in (400, 403, 421)
    r = c.post("/mcp/s3cret/drafter", json=INIT, headers={**ACCEPT, "Origin": "https://evil.example.net"})
    assert r.status_code in (400, 403)


def test_admin_key_locks_demo_controls(fresh, monkeypatch):
    monkeypatch.setenv("APPROVAL_ADMIN_KEY", "open-sesame")
    with TestClient(create_app(start_ticker=False)) as c:
        c.cookies.set("demo_user", "E3001")
        assert "운영자 키가 필요한 서버" in c.get("/demo").text
        before = S.demo_state()["now"]
        r = c.post("/demo/advance", data={"minutes": "60"}, follow_redirects=False)
        assert "err=" in r.headers["location"] and S.demo_state()["now"][:13] == before[:13]
        assert "err=" in c.post("/demo/reset", follow_redirects=False).headers["location"]
        assert "err=" in c.post("/demo/unlock", data={"key": "wrong"}, follow_redirects=False).headers["location"]
        r = c.post("/demo/unlock", data={"key": "open-sesame"}, follow_redirects=False)
        assert "msg=" in r.headers["location"]
        r = c.post("/demo/advance", data={"minutes": "60"}, follow_redirects=False)
        assert "msg=" in r.headers["location"]
        # 일반 기능(사용자 전환, 화면 보기)은 키 없이 된다
        assert c.post("/switch-user", data={"emp_no": "E2001", "next": "/"}, follow_redirects=False).status_code == 303


def test_frozen_clock(fresh, monkeypatch):
    monkeypatch.setenv("APPROVAL_CLOCK_FLOW", "frozen")
    a = S.demo_state()["now"]
    time.sleep(1.2)
    assert S.demo_state()["now"] == a == "2026-11-12T10:00:00"
    S.demo_advance(S.Ctx("E3001"), 30)
    assert S.demo_state()["now"] == "2026-11-12T10:30:00"


@pytest.fixture()
def live_deploy(fresh, monkeypatch):
    """배포 모드(웹+REST+MCP 한 포트)를 실제 포트로 띄운다. MCP 툴이 REST를 내부 주소로 부르는 길까지 시험한다."""
    import socket
    import threading

    import uvicorn
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setenv("APPROVAL_MCP_SECRET", "s3cret")
    monkeypatch.setenv("APPROVAL_INTERNAL_URL", f"http://127.0.0.1:{port}")
    srv = uvicorn.Server(uvicorn.Config(create_app(start_ticker=False, mount_mcp=True), host="127.0.0.1", port=port,
                                        log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=5)


def _remote(url: str, token: str | None, tool: str, **kw):
    import asyncio
    import json

    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    async def go():
        headers = {"Authorization": f"Bearer {token}"} if token else None
        async with create_mcp_http_client(headers=headers) as http:
            async with Client(streamable_http_client(url, http_client=http)) as c:
                tools = [t.name for t in (await c.list_tools()).tools]
                r = await c.call_tool(tool, kw)
                return tools, json.loads(r.content[0].text)
    return asyncio.run(go())


def test_live_deploy_tools_reach_the_api(live_deploy):
    tools, me = _remote(f"{live_deploy}/mcp/s3cret/drafter", None, "whoami")
    assert len(tools) == 14 and me["name"] == "김지훈" and me["today"].startswith("2026-11-12")
    tools, me = _remote(f"{live_deploy}/mcp", _bearer("approver"), "whoami")
    assert len(tools) == 8 and me["name"] == "박서연" and me["connection_mode"] == "approver"
    _, pend = _remote(f"{live_deploy}/mcp", _bearer("approver"), "list_pending_approvals")
    assert "code" not in pend


def test_connect_page_local_without_mcp(fresh):
    with TestClient(create_app(start_ticker=False)) as c:
        c.cookies.set("demo_user", "E3001")
        page = c.get("/connect").text
        assert "MCP가 켜져 있지 않습니다" in page and "approval_drafter" in page and "지금 고른 사용자" in page
        assert 'href="/connect"' in page                                   # 메뉴에 있다
        c.cookies.set("demo_user", "E2002")                                # 연결이 없는 사용자
        assert "AI 연결이 없습니다" in c.get("/connect").text


def test_connect_page_deploy_shows_urls_and_tokens(deployed):
    c = deployed
    c.cookies.set("demo_user", "E2001")                                    # 박서연 팀장: 결재자용 + 팀장 기안용 연결 2개
    page = c.get("/connect").text
    assert "/mcp/s3cret/approver" in page and "/mcp/s3cret/drafter-park" in page
    assert _bearer("approver") in page and _bearer("drafter-park") in page and DRAFT_TOKEN not in page
    assert page.count('class="is-me"') == 4                                # 주소 표 2줄 + 헤더 토큰 표 2줄
    assert "approval_approver" in page and "approval_drafter_park" in page


def test_connect_page_presenter_pc(fresh):
    """발표 PC(gpt 실행 중): gpt_connect.json의 로컬 주소와 헤더 토큰을 보여 준다."""
    import json

    from core.config import get_settings
    f = get_settings().db_path.parent / "gpt_connect.json"
    f.write_text(json.dumps({"created": "x", "token_url": "http://127.0.0.1:8765/mcp", "connections": [
        {"mode": "drafter", "slug": "drafter", "local_url": "http://127.0.0.1:8765/mcp/abc/drafter", "url": None,
         "bearer": "amk-drafter-1234"}]}), encoding="utf-8")
    try:
        with TestClient(create_app(start_ticker=False)) as c:
            page = c.get("/connect").text
            assert "http://127.0.0.1:8765/mcp/abc/drafter" in page and "amk-drafter-1234" in page
            assert "이 PC에서만 열림" in page
    finally:
        f.unlink()
