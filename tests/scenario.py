"""시연 대본의 AI 쪽 호출을 그대로 담은 자료(설계서 10장 대본, 김지훈 선임의 대전 출장 정리).

AI 서비스가 영수증과 메모를 읽고 MCP 툴을 부를 때 넣을 값을 미리 적어 둔다.
- pytest(서비스 계층 직접 호출)와 MCP 재현 시험이 같은 값을 쓴다.
- call(tool, **args)만 바꿔 끼우면 된다.
"""
from __future__ import annotations

RECEIPTS = [
    {"date": "2026-11-04", "merchant": "누리고속철도", "category": "교통비", "amount": 23700,
     "filename": "receipt_01_train_go.png", "confidence": 0.97},
    {"date": "2026-11-06", "merchant": "누리고속철도", "category": "교통비", "amount": 23700,
     "filename": "receipt_02_train_back.png", "confidence": 0.96},
    {"date": "2026-11-04", "merchant": "대전 별빛호텔", "category": "숙박비", "amount": 260000, "nights": 2,
     "filename": "receipt_03_hotel.png", "confidence": 0.95},
    {"date": "2026-11-05", "merchant": "은행동 칼국수", "category": "식비", "amount": 28000,
     "filename": "receipt_04_dinner_blur.png", "confidence": 0.62},
    {"date": "2026-11-04", "merchant": "한밭골 국밥", "category": "식비", "amount": 18000,
     "filename": "receipt_05_lunch_dup.png", "confidence": 0.94},
]
KEPT = RECEIPTS[:4]                      # 중복(5번)을 뺀 증빙
LODGING_REASON = "행사장 근처 숙소가 거의 만실이라 별빛호텔을 이용함"
TRIP = {"trip_start": "2026-11-04", "trip_end": "2026-11-06"}

PLAN_ITEMS = [
    {"process_id": "EXP-02", "summary": "대전 출장비 정산 (교통·숙박·식비)", "sources": "영수증 5장, 출장 메모",
     "evidence": RECEIPTS},
    {"process_id": "RPT-01", "summary": "한빛기술연구원 출장결과보고 (출장 종료 후 5영업일 이내)", "sources": "출장 메모"},
    {"process_id": "MTG-01", "summary": "출장 결과 공유 회의 회의실 신청 (11/17 14:00~15:00, 6명)", "sources": "출장 메모"},
]

FOLLOW_UPS_MISSING = [
    {"task": "시험 계획서 초안 송부", "owner": "김지훈", "due": "2026-11-20"},
    {"task": "장비 사양 비교표 작성", "owner": "", "due": ""},
]
FOLLOW_UPS_FILLED = [
    {"task": "시험 계획서 초안 송부", "owner": "김지훈", "due": "2026-11-20"},
    {"task": "장비 사양 비교표 작성", "owner": "오세준", "due": "2026-11-24"},
]


def exp02(reasons: dict | None = None) -> dict:
    return {"item_no": 1, "values": {**TRIP, "destination": "대전", "purpose": "한빛기술연구원 공동과제 착수회의, 시험장비 확인",
                                     "total_amount": sum(e["amount"] for e in KEPT)},
            "evidence": KEPT, "reasons": reasons or {}}


def rpt01(follow_ups: list[dict]) -> dict:
    return {"item_no": 2, "values": {**TRIP, "traveler": "김지훈 선임", "visit": "한빛기술연구원",
                                     "schedule": "11/4 착수회의, 11/5 시험장비 확인, 11/6 결과 정리 후 복귀",
                                     "result": "공동과제 일정(12월 착수)과 역할 분담을 확정했고, 장비 2종의 사양 차이를 확인했다.",
                                     "follow_ups": follow_ups}}


def mtg01(room: str = "RM-B") -> dict:
    return {"item_no": 3, "values": {"date": "2026-11-17", "start": "14:00", "end": "15:00", "room": room, "people": 6,
                                     "title": "대전 출장 결과 공유", "purpose": "출장 결과와 후속 조치 공유", "external": False}}


def run_drafter(call, *, until: str = "submitted") -> dict:
    """기안자 대본을 차례로 부른다. call(tool, **args) → dict.

    until: planned / approved / saved / confirmed / submitted 중 어디까지 갈지.
    결과를 모아 돌려준다(각 단계의 응답).
    """
    out: dict = {}
    out["whoami"] = call("whoami")
    out["processes"] = call("list_processes")
    out["rules"] = call("search_rules", query="출장")
    out["evidence"] = call("check_evidence", process_id="EXP-02", evidence=RECEIPTS)
    out["rooms"] = call("check_room_availability", date="2026-11-17", start="14:00", end="15:00", people=6)
    plan = call("create_plan", title="대전 출장 정리", summary="11/4~6 대전 출장의 정산, 결과보고, 결과 공유 회의실 신청",
                items=PLAN_ITEMS)
    out["plan"] = plan
    pid = plan["plan_id"]
    if until == "planned":
        return out
    out["save_before_approve"] = call("save_drafts", plan_id=pid, drafts=[exp02()])
    out["approve"] = call("approve_plan", plan_id=pid)
    if until == "approved":
        return out
    out["save1"] = call("save_drafts", plan_id=pid, drafts=[exp02(), rpt01(FOLLOW_UPS_MISSING), mtg01(out["rooms"]["recommended"])])
    out["save2"] = call("save_drafts", plan_id=pid, drafts=[exp02({"LIMIT_EXCEEDED:3": LODGING_REASON}), rpt01(FOLLOW_UPS_FILLED)])
    if until == "saved":
        return out
    ids = [r["doc_id"] for r in out["save1"]["results"]]
    out["doc_ids"] = ids
    out["submit_before_confirm"] = call("submit_drafts", doc_ids=ids[:1])
    out["confirm"] = call("confirm_drafts", doc_ids=ids)
    if until == "confirmed":
        return out
    out["submit"] = call("submit_drafts", doc_ids=ids)
    out["status"] = call("get_status", plan_id=pid)
    return out
