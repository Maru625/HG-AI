"""MCP 끝단 시험: 실제 MCP 클라이언트로 MCP 서버(stdio)를 띄우고, 시연 대본 전체를 툴 호출로 재현한다.

전결 서버는 이 시험 안에서 스레드로 띄운다(임시 DB). AI 서비스가 하는 일(자료 읽기, 사용자에게 묻기)은
tests/scenario.py에 적어 둔 값으로 대신한다.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time

import pytest
import uvicorn

from core import services as S
from core.app import create_app
from tests import scenario as sc
from tests.conftest import ROOT, WEB_APPROVER

DRAFT_TOKEN = "demo-drafter-kimjihoon-7f3a"
APPR_TOKEN = "demo-approver-parkseoyeon-9c2e"


@pytest.fixture()
def server(fresh):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = uvicorn.Server(uvicorn.Config(create_app(start_ticker=False), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=5)


def _parse(r) -> dict:
    text = r.content[0].text if r.content else "{}"
    if r.is_error:
        return json.loads(text[text.index("{"):]) if "{" in text else {"ok": False, "code": "MCP_ERROR", "message": text}
    return json.loads(text)


async def _with_client(base: str, token: str, fn):
    from mcp import Client, StdioServerParameters
    env = {**os.environ, "APPROVAL_TOKEN": token, "APPROVAL_SERVER": base, "PYTHONPATH": str(ROOT)}
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"], env=env, cwd=str(ROOT))
    async with Client(params) as c:
        return await fn(c)


def run_mcp(base: str, token: str, script):
    """script(call, tools, instructions)를 MCP 연결 위에서 돌린다. call은 동기 함수(시연 대본을 그대로 쓰려고)."""
    async def fn(c):
        loop = asyncio.get_running_loop()
        tools = {t.name: t for t in (await c.list_tools()).tools}

        def call(tool, **kw):
            return _parse(asyncio.run_coroutine_threadsafe(c.call_tool(tool, kw), loop).result(timeout=30))
        return await asyncio.to_thread(script, call, tools, c.instructions or "")
    return asyncio.run(_with_client(base, token, fn))


def test_mcp_tool_scopes(server):
    def script(call, tools, instr):
        return set(tools), {n: bool(t.annotations and t.annotations.read_only_hint) for n, t in tools.items()}, instr
    d_tools, d_ro, d_instr = run_mcp(server, DRAFT_TOKEN, script)
    a_tools, a_ro, a_instr = run_mcp(server, APPR_TOKEN, script)
    assert len(d_tools) == 14 and len(a_tools) == 8
    assert {"list_pending_approvals", "get_review_brief"}.isdisjoint(d_tools)
    assert {"create_plan", "save_drafts", "submit_drafts", "approve_plan"}.isdisjoint(a_tools)
    assert all(a_ro.values())                                                  # 결재자용은 모두 읽기
    assert [n for n, ro in d_ro.items() if not ro] == ["create_plan", "approve_plan", "cancel_plan", "save_drafts",
                                                         "confirm_drafts", "submit_drafts"]
    assert not any("decide" in n or "approve_doc" in n or "reject" in n for n in d_tools | a_tools)
    assert "김지훈" in d_instr and "박서연" in a_instr and len(d_instr.split("\n")[0]) <= 512


def test_mcp_full_demo_script(server):
    # 1) 기안자 대화: 대본 전체
    out = run_mcp(server, DRAFT_TOKEN, lambda call, *_: sc.run_drafter(call, until="submitted"))
    assert out["whoami"]["name"] == "김지훈"
    assert out["evidence"]["exclude_suggested"] == [5]
    assert out["rooms"]["recommended"] == "RM-B"
    assert out["save_before_approve"]["code"] == "PLAN_NOT_APPROVED"
    s1 = {r["item_no"]: r for r in out["save1"]["results"]}
    assert s1[1]["status"] == "INVALID" and s1[2]["status"] == "INVALID" and s1[3]["status"] == "VALID"
    assert {e["code"] for e in s1[2]["errors"]} == {"REQUIRED_MISSING"}
    assert all(r["status"] == "VALID" for r in out["save2"]["results"])
    assert out["submit_before_confirm"]["results"][0]["code"] == "NOT_CONFIRMED"
    blur = out["confirm_without_original_check"]["results"][0]                     # 제22조: 원본 대조 확인 필요
    assert blur["code"] == "ORIGINAL_CHECK_REQUIRED" and blur["details"]["evidence"][0]["seq"] == sc.BLURRY_SEQ
    assert all(r["ok"] for r in out["confirm"]["results"])
    assert [r["status"] for r in out["submit"]["results"]] == ["SUBMITTED"] * 3
    assert out["submit"]["notified"][0]["kind"] == "IMMEDIATE"
    # 막힌 시도도 작업 기록에 남는다
    blocked = {r["result"] for r in S.audit_list() if r["channel"] == "MCP" and r["result"] != "ok"}
    assert {"PLAN_NOT_APPROVED", "NOT_CONFIRMED", "ORIGINAL_CHECK_REQUIRED"} <= blocked

    # 2) 결재자 대화: 묶음 목록과 검토 요약 (읽기만)
    def approver_script(call, *_):
        lst = call("list_pending_approvals")
        brief = call("get_review_brief", doc_id=out["doc_ids"][0])
        return lst, brief
    lst, brief = run_mcp(server, APPR_TOKEN, approver_script)
    assert lst["counts"] == {"긴급": 3, "보통": 2, "낮음": 1}
    assert [d["importance"] for d in lst["documents"]][:3] == ["긴급"] * 3
    assert {"LIMIT_EXCEEDED", "LOW_CONFIDENCE"} <= {c["code"] for c in brief["cautions"]}

    # 3) 결재는 웹 화면에서 (MCP로는 불가)
    for d in out["doc_ids"]:
        S.decide(WEB_APPROVER, d, "approve", "")
    st = run_mcp(server, DRAFT_TOKEN, lambda call, *_: call("get_status", plan_id=out["plan"]["plan_id"]))
    assert {d["status"] for d in st["documents"]} == {"APPROVED"}


def test_mcp_server_down_message(fresh):
    """전결 서버가 꺼져 있으면 안내 오류를 돌려준다(역할은 APPROVAL_ROLE로)."""
    from mcp import Client, StdioServerParameters

    async def main():
        env = {**os.environ, "APPROVAL_TOKEN": DRAFT_TOKEN, "APPROVAL_SERVER": "http://127.0.0.1:9",
               "APPROVAL_ROLE": "drafter", "PYTHONPATH": str(ROOT)}
        params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"], env=env, cwd=str(ROOT))
        async with Client(params) as c:
            return _parse(await c.call_tool("whoami", {}))
    r = asyncio.run(main())
    assert r["code"] == "SERVER_UNAVAILABLE" and "approval-server" in r["fix_hint"]


def test_mcp_stdio_stdout_is_clean():
    """stdio로 실행하면 표준 출력에는 프로토콜 메시지 말고 아무것도 나오지 않는다(계획서 5.6). 로그는 표준 오류로."""
    import subprocess
    env = {**os.environ, "APPROVAL_TOKEN": DRAFT_TOKEN, "APPROVAL_SERVER": "http://127.0.0.1:9",
           "APPROVAL_ROLE": "drafter", "PYTHONPATH": str(ROOT)}
    p = subprocess.run([sys.executable, "-m", "mcp_server.server"], env=env, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=30)
    assert p.returncode == 0 and p.stdout == b""
    assert "APPROVAL_ROLE" in p.stderr.decode("utf-8")


def test_http_multi_for_chatgpt(server, tmp_path):
    """ChatGPT용: 한 포트에서 연결 전부(기안자용·결재자용, 여러 사례용)를 비밀 경로로 연다. 터널을 지나 Host가 달라도 받는다."""
    import httpx
    from mcp import Client
    from mcp_server.http_multi import build_app, load_connections, load_or_create_secret
    from core.config import get_settings

    secret = load_or_create_secret(tmp_path / "s.txt")
    assert secret == load_or_create_secret(tmp_path / "s.txt")              # 다시 띄워도 같은 주소
    app, paths = build_app(server, load_connections(get_settings().seed_dir), secret)
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True); t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"

    async def main():
        out = {}
        for slug, path in paths.items():
            async with Client(base + path) as c:
                out[slug] = (len((await c.list_tools()).tools), _parse(await c.call_tool("whoami", {}))["name"])
        return out
    try:
        assert asyncio.run(main()) == {
            "drafter": (14, "김지훈"), "approver": (8, "박서연"),                          # 시연 기본
            "drafter-jung": (14, "정하늘"), "drafter-park": (14, "박서연"), "drafter-song": (14, "송서준"),
            "approver-lee": (8, "이도현"), "approver-choi": (8, "최민준"), "approver-yoon": (8, "윤재민")}
        r = httpx.post(base + paths["drafter"], headers={"Host": "x-y.trycloudflare.com", "Accept": "application/json, text/event-stream"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                           "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")   # SSE 아님(빠른 터널 대비)
        assert httpx.post(base + "/mcp/wrong/drafter", json={}).status_code == 404
    finally:
        srv.should_exit = True
        t.join(timeout=5)
