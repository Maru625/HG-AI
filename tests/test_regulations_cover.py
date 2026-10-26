"""규정 대응 시험 (docs/specs/08_규정_대응표.md).

- 조항마다 대응 기능이 정의되어 있고, 화면 '새 기안'의 사례가 설명대로 걸린다(시연 시계를 옮겨도).
- 이번에 넓힌 기능: 제3조 위 직책자 결재, 제4조 처리 기한·지연 알림, 제14조 주말 합의, 제20조 첨부 누락,
  제22조 원본 대조 확인, 시점 규칙(사용 후 정산·출장 종료 후·구매 전·사용 전 신청), 화면 기안·재기안·상신, 기안자 알림.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core import cases, db, regulations, services as S
from core.app import create_app
from tests import scenario as sc
from tests.conftest import DRAFTER, WEB_APPROVER, WEB_DRAFTER, caller

WEB = lambda emp: S.Ctx(emp, channel="WEB")          # noqa: E731


def _preset(case_id: str) -> dict:
    with db.session() as s:
        c = cases.get(s, case_id)
    assert c, case_id
    return c


def _save(case_id: str, emp: str | None = None) -> dict:
    c = _preset(case_id)
    return S.save_draft_ui(WEB(emp or c["user"] or "E3001"), c["data"], process_id=c["process_id"])


def _codes(r: dict) -> set[str]:
    return {x["code"] for x in r["errors"] + r["warnings"]}


def _submit_web(case_id: str, emp: str | None = None, reasons: dict | None = None, checked=None) -> str:
    c = _preset(case_id)
    who = emp or c["user"] or "E3001"
    data = {**c["data"], "reasons": {**c["data"]["reasons"], **(reasons or {})}}
    r = S.save_draft_ui(WEB(who), data, process_id=c["process_id"])
    assert r["ok"], (case_id, r["errors"])
    S.confirm_draft_ui(WEB(who), r["doc_id"], checked)
    sub = S.submit_draft_ui(WEB(who), r["doc_id"])
    assert sub["status"] == "SUBMITTED"
    return r["doc_id"]


def _drafter(doc_id: str) -> str:
    from core.models import Document
    with db.session() as s:
        return s.get(Document, doc_id).drafter


def _line(doc_id: str) -> list[dict]:
    return S.doc_detail(WEB(_drafter(doc_id)), doc_id)["approval_line"]


def _pending(doc_id: str) -> dict:
    return next(s for s in _line(doc_id) if s["status"] == "PENDING")


# ───────── 대응표 ─────────
def test_every_article_has_features_and_cases_exist(fresh):
    with db.session() as s:
        art_ids = {a.id for a in regulations.articles(s)}
        known = {c["id"] for c in cases.presets(s)}
        used = {a for p in regulations.processes(s).values() for a in p["articles"]}
    assert art_ids == set(cases.ARTICLE_FEATURES), "조항과 대응표의 조항이 같아야 한다"
    assert used <= art_ids
    for aid, feats in cases.ARTICLE_FEATURES.items():
        assert feats, aid
        for f in feats:
            assert set(f["cases"]) <= known, (aid, set(f["cases"]) - known)
    # 프로세스 조항(제3조 이후 업무 조항)은 하나 이상 해 볼 수 있는 사례가 있다
    for aid in used:
        assert any(f["cases"] for f in cases.ARTICLE_FEATURES[aid]), aid
    page = S.rules_page()
    assert all(a["features"] for a in page["articles"])


@pytest.mark.parametrize("when", ["2026-11-12T10:00:00", "2026-11-14T11:00:00", "2026-11-19T17:30:00"])
def test_presets_behave_as_described(fresh, when):
    """사례마다 기대한 규칙 코드와 결재선이 나온다. 시연 시계를 평일·토요일·다음 주로 옮겨도 같다."""
    S.demo_set_clock(WEB_DRAFTER, when)
    with db.session() as s:
        ps = cases.presets(s)
    assert len(ps) >= 40 and {c["process_id"] for c in ps} == {"EXP-01", "EXP-02", "RPT-01", "PUR-01", "MTG-01"}
    for c in ps:
        r = S.save_draft_ui(WEB(c["user"] or "E3001"), c["data"], process_id=c["process_id"])
        got = _codes(r)
        assert set(c["codes"]) <= got, (when, c["id"], got, [e["message"] for e in r["errors"]])
        if not c["codes"]:
            assert r["ok"], (when, c["id"], [e["message"] for e in r["errors"]])
        if c["line"]:
            assert [x["level"] for x in r["approval_line_preview"]] == c["line"], (when, c["id"], r["approval_line_preview"])


# ───────── 제3조 결재선 ─────────
def test_art3_team_head_drafts_report_and_office_head_approves(fresh):
    doc = _submit_web("rpt01-team-head")
    assert [(s["name"], s["level"]) for s in _line(doc)] == [("이도현 실장", "실장")]
    assert "바로 위 실장" in _line(doc)[0]["basis"]
    S.decide(WEB("E1101"), doc, "approve", "")
    assert S.doc_detail(WEB("E2001"), doc)["status"] == "APPROVED"
    note = S.notifications(WEB("E2001"))["items"][0]                     # 기안자 결과 알림
    assert note["kind"] == "RESULT" and note["for_drafter"] and note["docs"][0]["status"] == "APPROVED"


def test_art3_top_position_cannot_submit(fresh):
    r = S.save_draft_ui(WEB("E1001"), _preset("exp01-normal")["data"], process_id="EXP-01")     # 본부장이 기안
    assert r["ok"] and r["approval_line_preview"] == [] and "결재할 사람이 없습니다" in r["approval_line_note"]
    S.confirm_draft_ui(WEB("E1001"), r["doc_id"])
    with pytest.raises(Exception) as e:
        S.submit_draft_ui(WEB("E1001"), r["doc_id"])
    assert "제3조" in str(e.value)


def test_art3_agreement_same_person_is_skipped(fresh):
    doc = _submit_web("mtg01-mgmt-self")                                  # 송서준(경영지원팀) 대회의실
    assert [(s["name"], s["kind"]) for s in _line(doc)] == [("윤재민 팀장", "결재")]


# ───────── 제4조 처리 기한·지연 알림 ─────────
def test_art4_due_overdue_and_delay_notice(fresh):
    doc = _submit_web("pur01-office")                                    # 팀장 → 실장
    st = _pending(doc)
    assert st["due"]["due"] == "2026-11-17" and not st["due"]["overdue"]         # 11/12(목) + 3영업일
    # 처리 기한이 지나면 경과 표시 + 긴급
    S.demo_set_clock(WEB_DRAFTER, "2026-11-18T09:30:00")
    st = _pending(doc)
    assert st["due"]["overdue"] and "경과" in st["due"]["label"]
    row = next(r for r in S.inbox(WEB("E2001"))["briefing"] if r["doc_id"] == doc)
    assert row["importance"] == "긴급" and row["overdue"] and any("제4조" in x for x in row["importance_reasons"])
    # 지연 알림: 차례가 아닌 사람·AI 연결은 못 한다
    assert caller(WEB("E1101"))("notify_delay", doc_id=doc, reason="x")["code"] == "NOT_IN_APPROVAL_LINE"
    assert caller(S.Ctx("E2001", channel="MCP", mode="approver"))("notify_delay", doc_id=doc, reason="x")["code"] == "FORBIDDEN"
    assert caller(WEB("E2001"))("notify_delay", doc_id=doc, reason=" ")["code"] == "BAD_REQUEST"
    S.notify_delay(WEB("E2001"), doc, "견적 비교 자료를 확인 중입니다", "2026-11-20")
    st = _pending(doc)
    assert st["delay_reason"] == "견적 비교 자료를 확인 중입니다" and st["delay_until"] == "2026-11-20"
    n = S.notifications(WEB("E3001"))["items"][0]
    assert n["kind"] == "DELAY" and n["kind_label"] == "처리 지연" and n["docs"][0]["until"] == "2026-11-20"
    assert any(r["action"] == "delay_notice" for r in S.audit_list())
    # 다음 단계는 그 결재자에게 차례가 온 날부터 다시 3영업일
    S.decide(WEB("E2001"), doc, "approve", "")
    st = _pending(doc)
    assert st["name"].startswith("이도현") and st["due"]["due"] == "2026-11-23" and not st["due"]["overdue"]


# ───────── 제14조 회의실 ─────────
def test_art14_weekend_needs_agreement_and_agreement_refusal(fresh):
    doc = _submit_web("mtg01-weekend")
    line = _line(doc)
    assert [s["kind"] for s in line] == ["결재", "합의"] and "주말·공휴일" in line[1]["basis"]
    assert S.doc_detail(WEB("E3001"), doc)["reservation"]["status"] == "HOLD"
    S.decide(WEB("E2001"), doc, "approve", "")
    d = S.doc_detail(WEB("E2004"), doc)
    assert d["can_decide"] and d["current_step"]["kind"] == "합의"
    assert caller(WEB("E2004"))("decide", doc_id=doc, decision="reject", comment="")["code"] == "BAD_REQUEST"
    S.decide(WEB("E2004"), doc, "reject", "주말에는 건물 냉난방이 꺼집니다")
    d = S.doc_detail(WEB("E3001"), doc)
    assert d["status"] == "REJECTED" and d["reservation"]["status"] == "RELEASED"
    n = S.notifications(WEB("E3001"))["items"][0]
    assert n["kind"] == "RESULT" and "합의 거부" in n["trigger"] and "냉난방" in n["docs"][0]["comment"]


def test_art14_urgent_meeting_notifies_approver_immediately(fresh):
    _submit_web("mtg01-urgent")
    n = S.notifications(WEB("E2001"))["items"][0]
    assert n["kind"] == "IMMEDIATE" and n["docs"][0]["importance"] == "긴급"


# ───────── 제20조 첨부 ─────────
def test_art20_attachment_required_in_mcp_too(fresh):
    ev = [{**sc.RECEIPTS[0]}]
    ev[0].pop("filename")
    ck = caller(DRAFTER)("check_evidence", process_id="EXP-02", evidence=ev)
    assert any(r["code"] == "ATTACHMENT_MISSING" for r in ck["items"][0]["results"])


# ───────── 제22조 원본 대조 ─────────
def test_art22_original_check_on_web_confirm_and_reset_on_edit(fresh):
    c = _preset("exp02-blurry")
    r = S.save_draft_ui(WEB("E3001"), c["data"], process_id="EXP-02")
    assert r["original_check_needed"] == [4] and "원본" in r["note"]
    res = caller(WEB("E3001"))("confirm_draft_ui", doc_id=r["doc_id"])
    assert res["code"] == "ORIGINAL_CHECK_REQUIRED" and res["details"]["evidence"][0]["seq"] == 4
    assert any(a["action"] == "confirm_draft" and a["result"] == "ORIGINAL_CHECK_REQUIRED" for a in S.audit_list())
    ok = S.confirm_draft_ui(WEB("E3001"), r["doc_id"], [4])
    assert ok["original_checked"] == [4]
    # 고치면 확인과 원본 대조가 함께 풀린다
    S.save_draft_ui(WEB("E3001"), {**c["data"], "memo": "메모 추가"}, doc_id=r["doc_id"])
    d = S.doc_detail(WEB("E3001"), r["doc_id"])
    assert d["status"] == "VALID" and d["original_checked"] == [] and d["low_confidence"][0]["checked"] is False
    S.confirm_draft_ui(WEB("E3001"), r["doc_id"], [4])
    S.submit_draft_ui(WEB("E3001"), r["doc_id"])
    low = next(x for x in S.doc_detail(WEB("E2001"), r["doc_id"])["cautions"] if x["code"] == "LOW_CONFIDENCE")
    assert low["original_checked"] and low["original_checked_at"]


# ───────── 화면 기안·재기안·상신 ─────────
def test_web_submit_only_for_web_docs(fresh):
    out = sc.run_drafter(caller(DRAFTER), until="confirmed")
    r = caller(WEB_DRAFTER)("submit_draft_ui", doc_id=out["doc_ids"][2])
    assert r["code"] == "FORBIDDEN" and "대화" in r["message"]
    assert not S.doc_detail(WEB_DRAFTER, out["doc_ids"][2])["can_submit"]


def test_web_redraft_after_reject(fresh):
    doc = _submit_web("pur01-team")
    S.decide(WEB_APPROVER, doc, "reject", "수량을 1대로 줄여 주세요")
    n = S.notifications(WEB("E3001"))["items"][0]
    assert n["kind"] == "RESULT" and n["docs"][0]["status"] == "REJECTED"
    d = S.doc_detail(WEB("E3001"), doc)
    assert d["can_redraft"] and d["redraft"] is None
    again = S.redraft_ui(WEB("E3001"), doc)
    assert not again["existing"] and S.redraft_ui(WEB("E3001"), doc)["existing"]          # 두 번 눌러도 하나
    nd = S.doc_detail(WEB("E3001"), again["doc_id"])
    assert nd["original_doc_id"] == doc and nd["status"] == "VALID"
    c = _preset("pur01-team")["data"]
    fixed = {**c, "values": {**c["values"], "qty": 1, "amount": 199000},
             "evidence": [{**c["evidence"][0], "amount": 199000}]}
    S.save_draft_ui(WEB("E3001"), fixed, doc_id=again["doc_id"])
    S.confirm_draft_ui(WEB("E3001"), again["doc_id"])
    S.submit_draft_ui(WEB("E3001"), again["doc_id"])
    rc = next(x for x in S.doc_detail(WEB("E2001"), again["doc_id"])["cautions"] if x["code"] == "RESUBMIT_CHANGES")
    assert {"qty", "amount"} <= set(rc["changed_fields"])
    assert not S.doc_detail(WEB("E3001"), doc)["can_redraft"]


def test_switch_users_grouped(fresh):
    users = S.employees_for_switch()
    groups = {g: [u["emp_no"] for u in users if u["group"] == g] for g in ("기안자", "결재자")}
    assert groups["기안자"][0] == "E3001" and {"E3002", "E3003", "E4017"} <= set(groups["기안자"])
    assert {"E2001", "E1101", "E1001", "E2004"} <= set(groups["결재자"])
    assert all(u["role"] == "approver" for u in users if u["group"] == "결재자")


# ───────── 화면 양식 ─────────
@pytest.fixture()
def client(fresh):
    with TestClient(create_app(start_ticker=False)) as c:
        yield c


def test_web_form_new_draft_reasons_confirm_submit(client):
    client.cookies.set("demo_user", "E3001")
    form = {"v.use_date": "2026-11-07", "v.merchant": "가상문구 판교점", "v.amount": "15,000", "v.account": "소모품비",
            "v.purpose": "주말 시험 대응 소모품", "ev.0.date": "2026-11-07", "ev.0.merchant": "가상문구 판교점",
            "ev.0.category": "소모품비", "ev.0.amount": "15000", "ev.0.filename": "r.png", "ev.0.confidence": "",
            "ev.1.date": "", "ev.1.merchant": "", "ev.1.amount": "", "memo": ""}
    r = client.post("/new/EXP-01", data=form, follow_redirects=False)
    assert r.status_code == 303 and "/drafts/DOC-" in r.headers["location"]
    doc = r.headers["location"].split("/drafts/")[1].split("?")[0]
    page = client.get(f"/drafts/{doc}").text
    assert "사유가 필요합니다" in page and 'name="r.HOLIDAY_USE:1"' in page
    client.post(f"/drafts/{doc}/reasons", data={"r.HOLIDAY_USE:1": "주말 시험 대응"})
    d = S.doc_detail(WEB("E3001"), doc)
    assert d["values"]["amount"] == 15000 and d["reasons"] == {"HOLIDAY_USE:1": "주말 시험 대응"} and d["can_confirm"]
    client.post(f"/drafts/{doc}/confirm")
    assert "[상신]" in client.get(f"/drafts/{doc}").text
    r = client.post(f"/drafts/{doc}/submit", follow_redirects=False)
    assert r.headers["location"].startswith(f"/docs/{doc}")
    assert S.doc_detail(WEB("E3001"), doc)["status"] == "SUBMITTED"
    # 고치기 양식은 값이 채워져 나온다(상신 뒤에는 고칠 수 없다)
    assert client.get(f"/drafts/{doc}/edit").status_code == 409


def test_web_case_pages_and_delay_form(client):
    client.cookies.set("demo_user", "E3002")
    page = client.get("/new").text
    assert "김지훈 선임으로 바꿔 시작" in page and "결재 쪽 사례" in page
    page = client.get("/new/EXP-02?case=exp02-lodging").text
    assert "김지훈 선임으로 해야" in page and 'value="115000"' in page
    client.cookies.set("demo_user", "E3001")
    doc = _submit_web("pur01-office")
    client.cookies.set("demo_user", "E2001")
    review = client.get(f"/review/{doc}").text
    assert "처리 지연 알리기" in review and "D-3" in review
    client.post(f"/review/{doc}/delay", data={"reason": "다음 주 검토", "until": ""})
    client.cookies.set("demo_user", "E3001")
    assert "처리 지연 알림" in client.get("/partials/notifications").text
    assert "처리 지연을 알렸습니다" in client.get(f"/docs/{doc}").text
    rules = client.get("/rules").text
    assert "해 보기" in rules and "/new/MTG-01?case=mtg01-weekend" in rules


def test_web_agreement_buttons(client):
    doc = _submit_web("mtg01-large")
    S.decide(WEB("E2001"), doc, "approve", "")
    client.cookies.set("demo_user", "E2004")
    page = client.get(f"/review/{doc}").text
    assert ">합의</button>" in page and "합의하지 않음" in page and "합의 요청" in page


def test_pages_render_for_every_switch_user(client):
    for u in S.employees_for_switch():
        client.cookies.set("demo_user", u["emp_no"])
        for p in ("/", "/new", "/drafts", "/inbox", "/inbox?view=drafter", "/rules", "/partials/notifications"):
            r = client.get(p)
            assert r.status_code == 200 and "Traceback" not in r.text, (u["emp_no"], p)
