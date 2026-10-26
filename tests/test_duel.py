"""회의실 가위바위보 대결 (재미 기능, 규정 밖) 시험."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core import db, duel as D, services as S
from core.app import create_app
from core.models import Duel, Reservation

W = lambda emp: S.Ctx(emp, channel="WEB")          # noqa: E731
SEED_RM_A = {"room": "RM-A", "date": "2026-11-17", "start": "13:30", "end": "14:30"}   # 연구2팀 주간회의(강민호 팀장) 13:00~15:00과 겹침


def _mtg(emp: str, **slot) -> str:
    v = {"date": "2026-11-17", "start": "13:30", "end": "14:30", "room": "RM-A", "people": 4, "title": "급한 회의",
         "purpose": "업무 협의", "external": False, **slot}
    r = S.save_draft_ui(W(emp), {"values": v}, process_id="MTG-01")
    return r["doc_id"]


def _res(res_id: int) -> Reservation:
    with db.session() as s:
        return s.get(Reservation, res_id)


def _doc_res(doc_id: str) -> Reservation | None:
    from sqlalchemy import select
    with db.session() as s:
        return s.scalars(select(Reservation).where(Reservation.doc_id == doc_id)).first()


def test_challenger_wins_and_takes_the_room(fresh):
    doc = _mtg("E3001")
    d = S.doc_detail(W("E3001"), doc)
    assert d["status"] == "INVALID" and d["duel_enabled"]
    (c,) = d["conflicts"]
    assert c["holder"] == "E2002" and c["can_challenge"] and c["status"] == "CONFIRMED"
    v = D.start(W("E3001"), doc, c["res_id"])
    assert v["status"] == "WAITING" and v["role"] == "challenger" and v["opponent"]["emp_no"] == "E2002"
    st = D.state(W("E2002"))["duel"]
    assert st["role"] == "defender" and st["my_pick"] is None and st["opp_picked"] is False
    D.pick(W("E3001"), v["id"], "rock")
    st = D.state(W("E2002"))["duel"]
    assert st["opp_picked"] is True and "c_pick" not in st and st["my_pick"] is None      # 상대 선택은 알려 주지 않는다
    out = D.pick(W("E2002"), v["id"], "scissors")
    assert out["status"] == "DONE" and out["won"] is False and out["rounds"][0]["result"] == "lose"
    seed = _res(c["res_id"])
    assert seed.status == "RELEASED" and "가위바위보" in seed.note
    d = S.doc_detail(W("E3001"), doc)
    assert d["status"] == "VALID" and not d["errors"]
    hold = _doc_res(doc)
    assert hold.status == "HOLD" and hold.note == D.WIN_NOTE
    # 상신하면 그 가예약을 그대로 쓰고, 승인되면 확정
    S.confirm_draft_ui(W("E3001"), doc)
    S.submit_draft_ui(W("E3001"), doc)
    again = _doc_res(doc)
    assert again.id == hold.id and again.status == "HOLD" and again.note == "가위바위보 승리 자리"
    S.decide(W("E2001"), doc, "approve", "")
    assert _doc_res(doc).status == "CONFIRMED"
    # 두 사람 모두 결과를 한 번 본다
    for emp, won in (("E3001", True), ("E2002", False)):
        r = D.state(W(emp))["duel"]
        assert r["status"] == "DONE" and r["won"] is won and r["result_text"]
        D.seen(W(emp), r["id"])
        assert D.state(W(emp))["duel"] is None
    rows = S.audit_list()
    assert any(a["action"] == "duel_result" and "도전자" in a["summary"] for a in rows)


def test_tie_then_defender_wins(fresh):
    doc = _mtg("E3001")
    c = S.doc_detail(W("E3001"), doc)["conflicts"][0]
    du = D.start(W("E3001"), doc, c["res_id"])["id"]
    D.pick(W("E3001"), du, "paper")
    v = D.pick(W("E2002"), du, "paper")
    assert v["status"] == "WAITING" and v["round"] == 2 and v["rounds"][0]["result"] == "tie" and v["my_pick"] is None
    D.pick(W("E3001"), du, "scissors")
    v = D.pick(W("E2002"), du, "rock")
    assert v["status"] == "DONE" and v["won"] is True and len(v["rounds"]) == 2
    assert _res(c["res_id"]).status == "CONFIRMED" and _res(c["res_id"]).note == "가위바위보 방어 성공 1회"
    d = S.doc_detail(W("E3001"), doc)
    assert d["status"] == "INVALID" and not d["conflicts"][0]["can_challenge"] and "졌습니다" in d["conflicts"][0]["why_not"]
    with pytest.raises(Exception):
        D.start(W("E3001"), doc, c["res_id"])


def test_yield_cancels_a_submitted_request(fresh):
    victim = _mtg("E3002", room="RM-B", date="2026-11-18", start="10:00", end="11:00", title="정하늘 회의")
    S.confirm_draft_ui(W("E3002"), victim)
    S.submit_draft_ui(W("E3002"), victim)
    doc = _mtg("E3001", room="RM-B", date="2026-11-18", start="10:30", end="11:30")
    c = S.doc_detail(W("E3001"), doc)["conflicts"][0]
    assert c["holder"] == "E3002" and c["status"] == "HOLD"
    du = D.start(W("E3001"), doc, c["res_id"])["id"]
    with pytest.raises(Exception):
        D.yield_(W("E3001"), du)                     # 양보는 수비자만
    v = D.yield_(W("E3002"), du)
    assert v["status"] == "DONE" and v["won"] is False and v["how"] == "yield" and "취소" in v["result_text"]
    vd = S.doc_detail(W("E3002"), victim)
    assert vd["status"] == "CANCELED" and "가위바위보" in vd["reject_comment"]
    assert {s["status"] for s in vd["approval_line"]} == {"SKIPPED"}
    n = S.notifications(W("E3002"))["items"][0]
    assert n["kind"] == "RESULT" and "가위바위보" in n["trigger"]
    assert victim not in {r["doc_id"] for r in S.inbox(W("E2001"))["briefing"]}
    assert S.doc_detail(W("E3001"), doc)["status"] == "VALID" and _doc_res(doc).status == "HOLD"


def test_rules_and_guards(fresh):
    doc = _mtg("E3001")
    c = S.doc_detail(W("E3001"), doc)["conflicts"][0]
    mcp = S.Ctx("E3001", channel="MCP", mode="drafter")
    with pytest.raises(Exception) as e:
        D.start(mcp, doc, c["res_id"])
    assert "웹 화면" in str(e.value)
    with pytest.raises(Exception):
        D.start(W("E3002"), doc, c["res_id"])         # 남의 초안
    # 회의실 겹침 말고 다른 오류가 있으면 도전할 수 없다(정원 초과)
    big = _mtg("E3001", people=9, start="14:00", end="14:40")
    cc = S.doc_detail(W("E3001"), big)["conflicts"][0]
    assert not cc["can_challenge"] and "오류" in cc["why_not"]
    du = D.start(W("E3001"), doc, c["res_id"])["id"]
    assert D.start(W("E3001"), doc, c["res_id"])["id"] == du      # 진행 중이면 같은 대결
    with pytest.raises(Exception):
        D.pick(W("E1001"), du, "rock")                # 참가자가 아님
    with pytest.raises(Exception):
        D.pick(W("E3001"), du, "lizard")
    D.pick(W("E3001"), du, "rock")
    D.pick(W("E3001"), du, "paper")                    # 고른 뒤에는 바꿀 수 없다
    with db.session() as s:
        assert s.get(Duel, du).c_pick == "rock"
    # 도전 취소: 수비자에게는 따로 보이지 않는다
    v = D.cancel(W("E3001"), du)
    assert v["status"] == "CANCELED" and D.state(W("E2002"))["duel"] is None
    # 꺼 두면 도전할 수 없다
    D.set_enabled(W("E3001"), False)
    assert not S.doc_detail(W("E3001"), doc)["duel_enabled"]
    with pytest.raises(Exception):
        D.start(W("E3001"), doc, c["res_id"])


def test_editing_after_win_releases_the_hold(fresh):
    doc = _mtg("E3001")
    c = S.doc_detail(W("E3001"), doc)["conflicts"][0]
    du = D.start(W("E3001"), doc, c["res_id"])["id"]
    D.yield_(W("E2002"), du)
    assert _doc_res(doc).status == "HOLD"
    d = S.doc_detail(W("E3001"), doc)
    S.save_draft_ui(W("E3001"), {"values": {**d["values"], "start": "16:00", "end": "17:00"}}, doc_id=doc)
    r = _doc_res(doc)
    assert r.status == "RELEASED" and "바꿔" in r.note


def test_simultaneous_picks_are_settled_by_state(fresh):
    doc = _mtg("E3001")
    c = S.doc_detail(W("E3001"), doc)["conflicts"][0]
    du = D.start(W("E3001"), doc, c["res_id"])["id"]
    with db.session() as s:                            # 두 요청이 거의 동시에 들어와 둘 다 판정 전에 저장된 경우
        x = s.get(Duel, du)
        x.c_pick, x.d_pick = "scissors", "paper"
    v = D.state(W("E2002"))["duel"]
    assert v["status"] == "DONE" and v["won"] is False


@pytest.fixture()
def client(fresh):
    with TestClient(create_app(start_ticker=False)) as c:
        yield c


def test_duel_screens_and_json(client):
    doc = _mtg("E3001")
    c = S.doc_detail(W("E3001"), doc)["conflicts"][0]
    client.cookies.set("demo_user", "E3001")
    page = client.get(f"/drafts/{doc}").text
    assert "가위바위보로 도전" in page and 'id="duel-root"' in page and "/static/duel.js" in page
    r = client.post("/duel/start", data={"doc_id": doc, "res_id": str(c["res_id"])}, follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    st = client.get("/duel/state").json()
    du = st["duel"]["id"]
    assert st["duel"]["role"] == "challenger" and st["waiting"] == 1
    assert client.post(f"/duel/{du}/pick", json={"pick": "rock"}).json()["my_pick"] == "rock"
    bad = client.post(f"/duel/{du}/yield", json={})
    assert bad.status_code == 403 and bad.json()["code"] == "FORBIDDEN"
    client.cookies.set("demo_user", "E2002")
    assert client.get("/duel/state").json()["duel"]["role"] == "defender"
    v = client.post(f"/duel/{du}/pick", json={"pick": "paper"}).json()
    assert v["status"] == "DONE" and v["won"] is True
    rooms = client.get("/rooms?start=2026-11-17").text
    assert "방어 성공" in rooms
    client.cookies.set("demo_user", "E3001")
    demo = client.get("/demo").text
    assert "회의실 가위바위보" in demo
    client.post("/demo/duel", data={"on": "0"})
    assert "가위바위보로 도전" not in client.get(f"/drafts/{doc}").text
