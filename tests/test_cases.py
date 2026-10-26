"""설계서 14.3 7단계 시험 케이스 10종 + 상태 전이 (설계서 5장).

TC01 정상 · TC02 순서 위반 · TC03 중복 · TC04 한도 초과 · TC05 확인 후 수정 · TC06 반려 후 재기안
TC07 화면 확인형 모드 · TC08 회의실 가예약 충돌·반려 시 해제 · TC09 긴급 결재 묶음 알림 · TC10 결재선 밖 조회 차단
"""
from __future__ import annotations

from core import services as S
from tests import scenario as sc
from tests.conftest import APPROVER, DRAFTER, WEB_APPROVER, WEB_DRAFTER, caller


def _submit_all(drafter) -> dict:
    out = sc.run_drafter(drafter, until="submitted")
    assert all(r["ok"] for r in out["submit"]["results"]), out["submit"]
    return out


def _doc(doc_id: str, ctx=WEB_DRAFTER) -> dict:
    return S.doc_detail(ctx, doc_id)


def _approve_all_steps(doc_id: str) -> dict:
    """결재선의 차례대로 웹 화면에서 승인한다."""
    while True:
        d = S.doc_detail(S.Ctx(_doc(doc_id)["drafter"]["emp_no"]), doc_id)
        if d["status"] != "SUBMITTED":
            return d
        cur = next(s for s in d["approval_line"] if s["status"] == "PENDING")
        S.decide(S.Ctx(cur["approver"], channel="WEB"), doc_id, "approve", "")


# ───────── TC01 정상 ─────────
def test_tc01_normal_flow(drafter):
    out = _submit_all(drafter)
    ids = out["doc_ids"]
    sub = {r["doc_id"]: r for r in out["submit"]["results"]}
    assert [s["name"] for s in sub[ids[0]]["approval_line"]] == ["박서연"]          # 335,400원 → 팀장 전결
    assert sub[ids[2]]["reservation"] == "가예약"
    for did in ids:
        assert _approve_all_steps(did)["status"] == "APPROVED"
    mtg = _doc(ids[2])
    assert mtg["reservation"]["status"] == "CONFIRMED"
    st = drafter("get_status", plan_id=out["plan"]["plan_id"])
    assert {d["status"] for d in st["documents"]} == {"APPROVED"}
    # 감사 기록은 AI(MCP)와 사람(화면)을 구분한다
    rows = S.audit_list()
    assert any(r["channel"] == "MCP" and r["action"] == "submit_draft" for r in rows)
    assert any(r["channel"] == "WEB" and r["action"] == "approve" for r in rows)


# ───────── TC02 순서 위반 ─────────
def test_tc02_order_violations(drafter):
    out = sc.run_drafter(drafter, until="saved")
    assert out["save_before_approve"]["code"] == "PLAN_NOT_APPROVED"
    ids = [r["doc_id"] for r in out["save1"]["results"]]
    r = drafter("submit_drafts", doc_ids=[ids[0]])["results"][0]
    assert r["code"] == "NOT_CONFIRMED"
    # 검증 오류가 있는 문서는 확인할 수 없다
    plan_id = out["plan"]["plan_id"]
    drafter("save_drafts", plan_id=plan_id, drafts=[sc.rpt01(sc.FOLLOW_UPS_MISSING)])
    r = drafter("confirm_drafts", doc_ids=[ids[1]])["results"][0]
    assert r["code"] == "VALIDATION_FAILED"
    # 취소한 계획은 이어서 쓸 수 없다
    drafter("cancel_plan", plan_id=plan_id, reason="시험")
    assert drafter("save_drafts", plan_id=plan_id, drafts=[sc.exp02()])["code"] == "PLAN_CANCELED"
    # 승인 전 계획을 다른 사람이 승인할 수 없다
    other = caller(S.Ctx("E3002", channel="MCP", mode="drafter"))
    assert other("approve_plan", plan_id=plan_id)["code"] == "FORBIDDEN"


# ───────── TC03 중복 ─────────
def test_tc03_duplicate_evidence(drafter):
    ck = drafter("check_evidence", process_id="EXP-02", evidence=sc.RECEIPTS)
    assert ck["exclude_suggested"] == [5]
    dup = next(x for x in ck["items"] if x["seq"] == 5)["results"][0]
    assert dup["code"] == "DUPLICATE_EVIDENCE" and "ST-" in dup["message"]
    assert ck["total_without_excluded"] == sum(e["amount"] for e in sc.KEPT)
    # 제외 제안을 무시하고 저장하면 오류로 막힌다
    out = sc.run_drafter(drafter, until="approved")
    bad = {**sc.exp02({"LIMIT_EXCEEDED:3": sc.LODGING_REASON}), "evidence": sc.RECEIPTS}
    bad["values"] = {**bad["values"], "total_amount": sum(e["amount"] for e in sc.RECEIPTS)}
    r = drafter("save_drafts", plan_id=out["plan"]["plan_id"], drafts=[bad])["results"][0]
    assert r["status"] == "INVALID" and any(e["code"] == "DUPLICATE_EVIDENCE" for e in r["errors"])
    # 다른 기안자는 같은 상호·금액이어도 중복이 아니다 (같은 기안자 기준)
    other = caller(S.Ctx("E3002", channel="MCP", mode="drafter"))
    ck2 = other("check_evidence", process_id="EXP-02", evidence=sc.RECEIPTS[4:])
    assert ck2["exclude_suggested"] == []


# ───────── TC04 한도 초과 ─────────
def test_tc04_limit_exceeded(drafter, approver):
    out = sc.run_drafter(drafter, until="saved")
    s1 = out["save1"]["results"][0]
    assert s1["status"] == "INVALID"
    assert any(e["code"] == "LIMIT_EXCEEDED" and e.get("reason_key") == "LIMIT_EXCEEDED:3" for e in s1["errors"])
    s2 = out["save2"]["results"][0]
    assert s2["status"] == "VALID"
    w = next(x for x in s2["warnings"] if x["code"] == "LIMIT_EXCEEDED")
    assert w["reason"] == sc.LODGING_REASON
    # 사유 없이 확인하려 하면 사유 필요 / 사유가 있으면 결재자에게 주의 항목으로 강조된다
    ids = out["doc_ids"] if "doc_ids" in out else [r["doc_id"] for r in out["save1"]["results"]]
    drafter("confirm_drafts", doc_ids=ids, original_checked={ids[0]: [sc.BLURRY_SEQ]})
    drafter("submit_drafts", doc_ids=ids)
    brief = approver("get_review_brief", doc_id=ids[0])
    codes = {c["code"] for c in brief["cautions"]}
    assert {"LIMIT_EXCEEDED", "LOW_CONFIDENCE"} <= codes
    low = next(c for c in brief["cautions"] if c["code"] == "LOW_CONFIDENCE")
    assert low["evidence_seq"] == sc.BLURRY_SEQ and low["original_checked"] and low["original_checked_at"]
    lim = next(c for c in brief["cautions"] if c["code"] == "LIMIT_EXCEEDED")
    assert lim["reason"] == sc.LODGING_REASON and lim["evidence_seq"] == 3 and "article_text" in lim


def test_tc04b_holiday_use_reason_required(drafter):
    """휴일 사용은 경고지만 사유가 있어야 확인된다: 사유 없으면 REASON_REQUIRED, 넣으면 확인된다."""
    ev = [{"date": "2026-11-08", "merchant": "가상문구 판교점", "category": "소모품비", "amount": 15000,
           "filename": "r.png", "confidence": 0.99}]
    p = drafter("create_plan", title="소모품", summary="", items=[{"process_id": "EXP-01", "summary": "소모품 정산", "evidence": ev}])
    drafter("approve_plan", plan_id=p["plan_id"])
    vals = {"use_date": "2026-11-08", "merchant": "가상문구 판교점", "amount": 15000, "account": "소모품비", "purpose": "시험 소모품"}
    r = drafter("save_drafts", plan_id=p["plan_id"], drafts=[{"item_no": 1, "values": vals, "evidence": ev}])["results"][0]
    assert r["status"] == "VALID" and r["missing_reasons"] == ["HOLIDAY_USE:1"]
    assert drafter("confirm_drafts", doc_ids=[r["doc_id"]])["results"][0]["code"] == "REASON_REQUIRED"
    r = drafter("save_drafts", plan_id=p["plan_id"], drafts=[{"item_no": 1, "values": vals, "evidence": ev,
                                                               "reasons": {"HOLIDAY_USE:1": "주말 시험 대응"}}])["results"][0]
    assert r["missing_reasons"] == [] and drafter("confirm_drafts", doc_ids=[r["doc_id"]])["results"][0]["ok"]


# ───────── TC05 확인 후 수정 ─────────
def test_tc05_modified_after_confirm(drafter):
    out = sc.run_drafter(drafter, until="confirmed")
    ids, plan_id = out["doc_ids"], out["plan"]["plan_id"]
    assert all(r["ok"] for r in out["confirm"]["results"])
    r = drafter("save_drafts", plan_id=plan_id, drafts=[sc.mtg01("RM-B") | {"values": {**sc.mtg01()["values"], "people": 5}}])
    res = r["results"][0]
    assert res["status"] == "VALID" and res["version"] == 2 and "확인이 해제" in res["note"]
    sub = drafter("submit_drafts", doc_ids=[ids[2]])["results"][0]
    assert sub["code"] == "MODIFIED_AFTER_CONFIRM"
    # 다시 확인하면 상신된다
    drafter("confirm_drafts", doc_ids=[ids[2]])
    assert drafter("submit_drafts", doc_ids=[ids[2]])["results"][0]["ok"]
    # 상신한 문서는 고칠 수 없다
    again = drafter("save_drafts", plan_id=plan_id, drafts=[sc.mtg01("RM-B")])["results"][0]
    assert again["code"] == "ALREADY_SUBMITTED"


# ───────── TC06 반려 후 재기안 ─────────
def test_tc06_reject_and_resubmit(drafter, approver):
    out = _submit_all(drafter)
    rpt = out["doc_ids"][1]
    S.decide(WEB_APPROVER, rpt, "reject", "장비 사양 비교표 기한을 11/20으로 당겨 주세요.")
    st = drafter("get_status", doc_id=rpt)
    assert st["status"] == "REJECTED" and "11/20" in st["reject_comment"]
    assert [s["status"] for s in st["approval_line"]] == ["REJECTED"]
    fixed = [dict(x) for x in sc.FOLLOW_UPS_FILLED]
    fixed[1]["due"] = "2026-11-20"
    r = drafter("save_drafts", plan_id=out["plan"]["plan_id"], drafts=[sc.rpt01(fixed)])["results"][0]
    new_id = r["doc_id"]
    assert new_id != rpt and r["status"] == "VALID" and "다시 기안" in r["note"]
    drafter("confirm_drafts", doc_ids=[new_id])
    assert drafter("submit_drafts", doc_ids=[new_id])["results"][0]["ok"]
    brief = approver("get_review_brief", doc_id=new_id)
    rc = next(c for c in brief["cautions"] if c["code"] == "RESUBMIT_CHANGES")
    assert "follow_ups" in rc["changed_fields"] and brief["resubmit_of"] == rpt


# ───────── TC07 화면 확인형 모드 ─────────
def test_tc07_confirm_in_ui_mode(drafter):
    S.demo_set_confirm_mode(WEB_DRAFTER, "B")
    out = sc.run_drafter(drafter, until="planned")
    plan_id = out["plan"]["plan_id"]
    r = drafter("approve_plan", plan_id=plan_id)
    assert r["code"] == "CONFIRM_IN_UI_REQUIRED" and r["details"]["link"].endswith(f"/plans/{plan_id}")
    S.approve_plan(WEB_DRAFTER, plan_id)                                   # 화면에서 승인
    res = drafter("save_drafts", plan_id=plan_id, drafts=[sc.mtg01("RM-B")])["results"][0]
    r = drafter("confirm_drafts", doc_ids=[res["doc_id"]])
    assert r["code"] == "CONFIRM_IN_UI_REQUIRED"
    S.confirm_draft_ui(WEB_DRAFTER, res["doc_id"])                          # 화면에서 확인
    assert drafter("submit_drafts", doc_ids=[res["doc_id"]])["results"][0]["ok"]
    assert S.plan_detail(WEB_DRAFTER, plan_id)["approved_via"] == "WEB"


# ───────── TC08 회의실 가예약 충돌, 반려 시 해제 ─────────
def test_tc08_room_hold_conflict_and_release(drafter):
    # 기존 확정 예약(RM-A 13:00~15:00)과 겹치면 저장 단계에서 오류
    out = sc.run_drafter(drafter, until="approved")
    plan_id = out["plan"]["plan_id"]
    r = drafter("save_drafts", plan_id=plan_id, drafts=[sc.mtg01("RM-A")])["results"][0]
    assert r["status"] == "INVALID" and any(e["code"] == "ROOM_CONFLICT" for e in r["errors"])
    r = drafter("save_drafts", plan_id=plan_id, drafts=[sc.mtg01("RM-B")])["results"][0]
    drafter("confirm_drafts", doc_ids=[r["doc_id"]])
    sub = drafter("submit_drafts", doc_ids=[r["doc_id"]])["results"][0]
    assert sub["reservation"] == "가예약"
    # 다른 사람이 같은 시간대를 찾으면 가예약도 겹침으로 보인다
    other = caller(S.Ctx("E3002", channel="MCP", mode="drafter"))
    avail = other("check_room_availability", date="2026-11-17", start="14:30", end="15:30", people=6)
    rmb = next(x for x in avail["rooms"] if x["room_id"] == "RM-B")
    assert not rmb["available"] and rmb["conflicts"][0]["status"] == "가예약"
    plan2 = other("create_plan", title="겹치는 회의", summary="", items=[{"process_id": "MTG-01", "summary": "회의"}])
    other("approve_plan", plan_id=plan2["plan_id"])
    d2 = other("save_drafts", plan_id=plan2["plan_id"], drafts=[{"item_no": 1, "values": {
        **sc.mtg01("RM-B")["values"], "start": "14:30", "end": "15:30", "title": "겹치는 회의"}}])["results"][0]
    assert any(e["code"] == "ROOM_CONFLICT" for e in d2["errors"])
    # 반려하면 가예약이 풀려 같은 시간대를 쓸 수 있다
    S.decide(WEB_APPROVER, r["doc_id"], "reject", "회의 시간을 오전으로 옮겨 주세요.")
    assert _doc(r["doc_id"])["reservation"]["status"] == "RELEASED"
    avail = other("check_room_availability", date="2026-11-17", start="14:30", end="15:30", people=6)
    assert next(x for x in avail["rooms"] if x["room_id"] == "RM-B")["available"]


# ───────── TC09 긴급 결재가 들어올 때 대기 결재 전체를 묶은 알림 ─────────
def test_tc09_urgent_notification_bundles_all_pending(drafter):
    before = S.notifications(S.Ctx("E2001"))["items"]
    assert before and before[0]["kind"] == "DIGEST" and before[0]["read"]            # 시드: 09:00 정기 알림(읽음)
    out = _submit_all(drafter)
    assert out["submit"]["notified"] and out["submit"]["notified"][0]["kind"] == "IMMEDIATE"
    n = S.notifications(S.Ctx("E2001"))
    latest = n["items"][0]
    assert n["unread"] == 1 and latest["kind"] == "IMMEDIATE"
    levels = [d["importance"] for d in latest["docs"]]
    assert len(latest["docs"]) == 6                                                    # 시드 대기 3 + 상신 3
    assert levels == sorted(levels, key=["긴급", "보통", "낮음"].index)                  # 중요도순
    assert levels.count("긴급") == 3
    # 같은 긴급 문서로 다시 알리지 않는다
    assert S.run_tick() == 0
    # 정기 알림 14:00: 시연 시계를 넘기면 대기 결재 전체를 묶어 보낸다
    S.demo_set_clock(WEB_DRAFTER, "2026-11-12T13:59:00")
    S.demo_advance(WEB_DRAFTER, 2)
    latest = S.notifications(S.Ctx("E2001"))["items"][0]
    assert latest["kind"] == "DIGEST" and "14:00" in latest["trigger"] and len(latest["docs"]) == 6


# ───────── TC10 결재선 밖 문서 조회 차단 ─────────
def test_tc10_access_outside_approval_line(drafter, approver):
    out = _submit_all(drafter)
    doc = out["doc_ids"][0]
    outsider = caller(S.Ctx("E2004", channel="MCP", mode="approver"))         # 경영지원팀장: 결재선 밖
    assert outsider("get_review_brief", doc_id=doc)["code"] == "NOT_IN_APPROVAL_LINE"
    assert outsider("get_status", doc_id=doc)["code"] == "NOT_IN_APPROVAL_LINE"
    assert doc not in {r["doc_id"] for r in outsider("list_pending_approvals")["documents"]}
    # 역할이 다른 연결로는 상대 툴을 쓸 수 없다
    assert drafter("list_pending_approvals")["code"] == "FORBIDDEN"
    assert approver("create_plan", title="x", summary="", items=[{"process_id": "EXP-01"}])["code"] == "FORBIDDEN"
    # 승인·반려는 웹 화면에서만
    assert caller(APPROVER)("decide", doc_id=doc, decision="approve")["code"] == "FORBIDDEN"
    # 웹에서도 차례가 아닌 사람은 결재할 수 없다
    assert caller(S.Ctx("E1101", channel="WEB"))("decide", doc_id=doc, decision="approve")["code"] == "NOT_IN_APPROVAL_LINE"


# ───────── 상태 전이 (설계서 5장) ─────────
def test_transitions_plan_cancel_cascades(drafter):
    out = sc.run_drafter(drafter, until="saved")
    plan_id = out["plan"]["plan_id"]
    r = drafter("cancel_plan", plan_id=plan_id, reason="출장 정산을 다음 주로 미룸")
    assert r["status"] == "CANCELED" and len(r["canceled_documents"]) == 3
    assert {d["status"] for d in S.plan_detail(WEB_DRAFTER, plan_id)["documents"]} == {"CANCELED"}
    # 승인대기 → 취소
    p2 = drafter("create_plan", title="t", summary="", items=[{"process_id": "EXP-01", "summary": "s"}])
    assert drafter("cancel_plan", plan_id=p2["plan_id"])["status"] == "CANCELED"


def _purchase(drafter, qty: int, price: int) -> str:
    p = drafter("create_plan", title="장비 구매", summary="", items=[{"process_id": "PUR-01", "summary": "시험용 지그 구매",
                "evidence": [{"date": "2026-11-12", "merchant": "가상전자상사", "category": "견적서", "amount": qty * price,
                              "filename": "quote.pdf", "confidence": 1.0}]}])
    drafter("approve_plan", plan_id=p["plan_id"])
    r = drafter("save_drafts", plan_id=p["plan_id"], drafts=[{"item_no": 1, "values": {
        "item": "시험용 지그", "qty": qty, "unit_price": price, "amount": qty * price, "reason": "시험 장비 보강",
        "due_date": "2026-11-30"}, "evidence": [{"date": "2026-11-12", "merchant": "가상전자상사", "category": "견적서",
                                                 "amount": qty * price, "filename": "quote.pdf", "confidence": 1.0}]}])["results"][0]
    assert r["ok"], r
    drafter("confirm_drafts", doc_ids=[r["doc_id"]])
    assert drafter("submit_drafts", doc_ids=[r["doc_id"]])["results"][0]["ok"]
    return r["doc_id"]


def test_transitions_multi_step_and_agreement(drafter):
    """350만 원 구매: 팀장 → 실장 → 본부장 + 경영지원팀 합의. WAITING→PENDING→APPROVED, 반려 시 SKIPPED."""
    doc = _purchase(drafter, 5, 700000)
    line = _doc(doc)["approval_line"]
    assert [(s["kind"], s["level"], s["status"]) for s in line] == [
        ("결재", "팀장", "PENDING"), ("결재", "실장", "WAITING"), ("결재", "본부장", "WAITING"), ("합의", "합의", "WAITING")]
    S.decide(WEB_APPROVER, doc, "approve", "")
    line = _doc(doc)["approval_line"]
    assert [s["status"] for s in line] == ["APPROVED", "PENDING", "WAITING", "WAITING"]
    assert _approve_all_steps(doc)["status"] == "APPROVED"
    assert [s["status"] for s in _doc(doc)["approval_line"]] == ["APPROVED"] * 4
    # 첫 단계에서 반려하면 뒤 단계는 SKIPPED
    doc2 = _purchase(drafter, 2, 400000)                                       # 80만 원 → 팀장 → 실장
    S.decide(WEB_APPROVER, doc2, "reject", "견적 비교 자료를 붙여 주세요.")
    assert [s["status"] for s in _doc(doc2)["approval_line"]] == ["REJECTED", "SKIPPED"]
    # 다음 결재자에게 넘어가면 그 사람의 결재함에 보인다
    doc3 = _purchase(drafter, 1, 650000)
    S.decide(WEB_APPROVER, doc3, "approve", "")
    nxt = next(s for s in _doc(doc3)["approval_line"] if s["status"] == "PENDING")["approver"]
    assert doc3 in {r["doc_id"] for r in S.inbox(S.Ctx(nxt))["briefing"]}


def test_large_room_needs_agreement(drafter):
    out = sc.run_drafter(drafter, until="approved")
    v = {**sc.mtg01()["values"], "room": "RM-L", "people": 12}
    r = drafter("save_drafts", plan_id=out["plan"]["plan_id"], drafts=[{"item_no": 3, "values": v}])["results"][0]
    assert [s["kind"] for s in r["approval_line_preview"]] == ["결재", "합의"]


def test_reject_requires_comment(drafter):
    out = _submit_all(drafter)
    r = caller(WEB_APPROVER)("decide", doc_id=out["doc_ids"][0], decision="reject", comment=" ")
    assert r["code"] == "BAD_REQUEST"


def test_importance_rules(fresh):
    docs = S.list_pending_approvals(APPROVER)["documents"]
    by = {d["process_id"] + ":" + d["importance"]: d for d in docs}
    assert "PUR-01:긴급" in by and any("대기" in x for x in by["PUR-01:긴급"]["importance_reasons"])
    assert "EXP-01:보통" in by and "EXP-01:낮음" in by
    brief = S.get_review_brief(APPROVER, by["EXP-01:보통"]["doc_id"])
    assert {"NEAR_LIMIT", "SIMILAR_RECENT"} <= {c["code"] for c in brief["cautions"]}
