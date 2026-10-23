"""REST API(P2가 쓰는 계약 C1)와 웹 화면(P3)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.app import create_app
from tests import scenario as sc
from tests.conftest import DRAFTER, caller

DRAFT_TOKEN = {"X-Approval-Token": "demo-drafter-kimjihoon-7f3a"}
APPR_TOKEN = {"X-Approval-Token": "demo-approver-parkseoyeon-9c2e"}


@pytest.fixture()
def client(fresh):
    with TestClient(create_app(start_ticker=False)) as c:
        yield c


def as_user(c: TestClient, emp_no: str) -> TestClient:
    c.cookies.set("demo_user", emp_no)
    return c


# ───────── REST API ─────────
def test_api_auth_and_roles(client):
    r = client.get("/api/whoami")
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHORIZED"
    r = client.get("/api/connection", headers=APPR_TOKEN)
    assert r.json()["mode"] == "approver" and r.json()["emp_no"] == "E2001"
    r = client.post("/api/plans", headers=APPR_TOKEN, json={"title": "x", "items": [{"process_id": "EXP-01"}]})
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"
    r = client.get("/api/approvals/pending", headers=DRAFT_TOKEN)
    assert r.status_code == 403


def test_api_has_no_decide_endpoint(client):
    paths = {getattr(r, "path", "") for r in client.app.routes}
    api = {p for p in paths if p.startswith("/api")}
    assert not any("decide" in p or "reject" in p for p in api)
    assert all(not p.startswith("/api/approvals/{doc_id}/") or p.endswith("/brief") for p in api)


def test_api_error_format_and_bad_input(client):
    r = client.post("/api/plans/PLAN-001/drafts", headers=DRAFT_TOKEN, json={"drafts": []})
    body = r.json()
    assert r.status_code == 404 and set(body) >= {"ok", "code", "message", "fix_hint"} and body["ok"] is False
    r = client.post("/api/rooms/availability", headers=DRAFT_TOKEN, json={"date": "2026-11-17"})
    assert r.status_code == 400 and r.json()["code"] == "BAD_REQUEST"
    r = client.get("/api/processes/XXX-99/spec", headers=DRAFT_TOKEN)
    assert r.json()["code"] == "UNKNOWN_PROCESS"


def test_api_blocked_writes_are_audited(client):
    p = client.post("/api/plans", headers=DRAFT_TOKEN, json={"title": "t", "summary": "", "items": sc.PLAN_ITEMS}).json()
    r = client.post(f"/api/plans/{p['plan_id']}/drafts", headers=DRAFT_TOKEN, json={"drafts": [sc.exp02()]})
    assert r.json()["code"] == "PLAN_NOT_APPROVED"
    client.get("/api/processes/XXX-99/spec", headers=DRAFT_TOKEN)                 # 읽기 실패는 기록하지 않는다
    from core import services as S
    rows = S.audit_list()
    assert rows[0]["action"] == "save_drafts" and rows[0]["result"] == "PLAN_NOT_APPROVED" and rows[0]["target"] == p["plan_id"]
    assert rows[0]["channel"] == "MCP" and rows[0]["actor"].startswith("김지훈")
    as_user(client, "E3001")
    assert "PLAN_NOT_APPROVED" in client.get("/audit").text


def test_api_links_point_to_screens(client):
    r = client.post("/api/plans", headers=DRAFT_TOKEN, json={"title": "t", "summary": "", "items": sc.PLAN_ITEMS})
    assert r.json()["link"].endswith("/plans/" + r.json()["plan_id"])


# ───────── 웹 화면 ─────────
PAGES = ["/", "/plans", "/drafts", "/inbox", "/inbox?view=drafter", "/rooms", "/rules", "/audit", "/demo",
         "/partials/notifications"]


def test_pages_render_for_both_roles(client):
    for user in ("E3001", "E2001"):
        as_user(client, user)
        for p in PAGES:
            r = client.get(p)
            assert r.status_code == 200, (user, p)
            assert "Traceback" not in r.text


def test_full_flow_on_screens(client):
    out = sc.run_drafter(caller(DRAFTER), until="saved")
    plan_id, ids = out["plan"]["plan_id"], [r["doc_id"] for r in out["save1"]["results"]]
    c = as_user(client, "E3001")
    assert "PLAN-001" in c.get("/plans").text
    page = c.get(f"/plans/{plan_id}").text
    assert "제외 제안" in page and "증빙 3 한도 초과 사유" in page
    # 확인 ② 화면에서
    r = c.post(f"/drafts/{ids[0]}/confirm", follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    assert "확인완료" in c.get(f"/drafts/{ids[0]}").text
    for d in ids[1:]:
        c.post(f"/drafts/{d}/confirm")
    caller(DRAFTER)("submit_drafts", doc_ids=ids)
    # 상신 후 임시저장함 링크는 문서 화면으로 넘어간다
    assert c.get(f"/drafts/{ids[0]}", follow_redirects=False).headers["location"] == f"/docs/{ids[0]}"
    # 결재자: 알림 토스트, 결재함, 검토 화면의 주의 항목 강조
    a = as_user(client, "E2001")
    bell = a.get("/partials/notifications").text
    assert "긴급 결재 알림" in bell and bell.count("<li>") == 6
    inbox = a.get("/inbox").text
    assert inbox.index("긴급") < inbox.index("보통") < inbox.index("낮음")
    review = a.get(f"/review/{ids[0]}").text
    assert "주의해서 볼 항목 2개" in review and review.count("row-hl") >= 2 and "⚠ 주의" in review
    assert a.get(f"/docs/{ids[0]}", follow_redirects=False).headers["location"] == f"/review/{ids[0]}"
    # 반려는 의견이 있어야 한다
    r = a.post(f"/review/{ids[1]}/decide", data={"decision": "reject", "comment": ""},
               headers={"referer": f"http://testserver/review/{ids[1]}"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    a.post(f"/review/{ids[1]}/decide", data={"decision": "reject", "comment": "기한을 당겨 주세요"})
    a.post(f"/review/{ids[2]}/decide", data={"decision": "approve", "comment": ""})
    rooms = a.get("/rooms").text
    assert "대전 출장 결과 공유" in rooms and "확정" in rooms
    audit = a.get("/audit").text
    assert "AI(MCP)" in audit and "사람(화면)" in audit
    # 기안자는 결재 검토 화면에서 결정할 수 없다(결정 버튼 없음)
    as_user(client, "E3001")
    assert 'action="/review/' not in client.get(f"/docs/{ids[0]}").text


def test_forbidden_page_for_outsider(client):
    caller(DRAFTER)("create_plan", title="t", summary="", items=sc.PLAN_ITEMS)
    as_user(client, "E2001")
    r = client.get("/plans/PLAN-001")
    assert r.status_code == 403 and "사용자 전환" in r.text


def test_demo_panel_controls(client):
    c = as_user(client, "E3001")
    c.post("/demo/mode", data={"mode": "B"})
    assert "화면 확인형(B)" in c.get("/demo").text
    c.post("/demo/clock", data={"when": "2026-11-12 13:59"})
    c.post("/demo/advance", data={"minutes": "2"})
    notes = c.get("/partials/notifications")
    as_user(client, "E2001")
    assert "정기 결재 알림" in client.get("/partials/notifications").text
    r = client.post("/demo/reset", follow_redirects=False)
    assert r.status_code == 303 and "초기화" in client.get(r.headers["location"]).text
