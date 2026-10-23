"""P3 웹 화면 (설계서 9장). 보여줄 값은 모두 P1 서비스 계층에서 받는다. 승인·반려는 여기에만 있다."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from core import services as S
from core.config import get_settings
from core.errors import ServiceError

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.filters["won"] = lambda v: f"{int(v):,}원" if isinstance(v, (int, float)) else (v or "")
templates.env.filters["tojson_ko"] = lambda v: json.dumps(v, ensure_ascii=False)
templates.env.filters["hm"] = lambda s: (s or "").replace("T", " ")[:16]


def _reason_label(key: str) -> str:
    """검증의 reason_key를 사람이 읽는 이름으로 (예: LIMIT_EXCEEDED:3 → 증빙 3 한도 초과 사유)."""
    parts = str(key).split(":")
    if parts[0] == "LIMIT_EXCEEDED" and len(parts) == 3 and parts[1] == "meal":
        return f"{parts[2][5:]} 식비 한도 초과 사유"
    if parts[0] == "LIMIT_EXCEEDED" and len(parts) == 2:
        return f"증빙 {parts[1]} 한도 초과 사유"
    if parts[0] == "HOLIDAY_USE" and len(parts) == 2:
        return f"증빙 {parts[1]} 휴일 사용 사유"
    return str(key)


templates.env.filters["rk"] = _reason_label

router = APIRouter(include_in_schema=False)
DEFAULT_USER = "E3001"


def _user(request: Request) -> str:
    return request.cookies.get("demo_user") or DEFAULT_USER


def _ctx(request: Request) -> S.Ctx:
    return S.Ctx(emp_no=_user(request), channel="WEB")


def page(request: Request, name: str, **ctx) -> HTMLResponse:
    user = _user(request)
    base = {"request": request, "user": user, "users": S.employees_for_switch(), "demo": S.demo_state(),
            "notes": S.notifications(S.Ctx(emp_no=user)), "msg": request.query_params.get("msg"),
            "err": request.query_params.get("err"), "path": request.url.path}
    base.update(ctx)
    return templates.TemplateResponse(request, name, base)


def back(url: str, msg: str = "", err: str = "") -> RedirectResponse:
    q = []
    if msg:
        q.append("msg=" + quote(msg))
    if err:
        q.append("err=" + quote(err))
    return RedirectResponse(url + ("?" + "&".join(q) if q else ""), status_code=303)


def render_error(request: Request, exc: ServiceError) -> HTMLResponse:
    if request.method == "POST":
        ref = request.headers.get("referer") or "/"
        return back(ref.split("?")[0], err=f"{exc.message} {exc.fix_hint}".strip())
    r = page(request, "error.html", error=exc)
    r.status_code = exc.status
    return r


# ───────── 공통 ─────────
@router.post("/switch-user")
def switch_user(request: Request, emp_no: str = Form(...), next: str = Form("/")):
    r = RedirectResponse(next or "/", status_code=303)
    r.set_cookie("demo_user", emp_no, samesite="lax")
    return r


@router.get("/partials/notifications", response_class=HTMLResponse)
def notif_partial(request: Request):
    return templates.TemplateResponse(request, "_bell.html", {"request": request, "notes": S.notifications(_ctx(request)),
                                                              "demo": S.demo_state()})


@router.post("/notifications/dismiss", response_class=HTMLResponse)
def notif_dismiss(request: Request, nid: int | None = Form(None)):
    S.mark_notifications_read(_ctx(request), nid)
    return notif_partial(request)


@router.post("/notifications/read")
def notif_read(request: Request, nid: int | None = Form(None), next: str = Form("/inbox")):
    S.mark_notifications_read(_ctx(request), nid)
    return RedirectResponse(next, status_code=303)


# ───────── 홈 ─────────
@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return page(request, "home.html", dash=S.home_dashboard())


# ───────── 작업 계획 ─────────
@router.get("/plans", response_class=HTMLResponse)
def plans(request: Request):
    return page(request, "plans.html", plans=S.list_plans(_ctx(request)), live=True)


@router.get("/plans/{plan_id}", response_class=HTMLResponse)
def plan(request: Request, plan_id: str):
    return page(request, "plan.html", plan=S.plan_detail(_ctx(request), plan_id), live=True)


@router.post("/plans/{plan_id}/approve")
def plan_approve(request: Request, plan_id: str):
    S.approve_plan(_ctx(request), plan_id)
    return back(f"/plans/{plan_id}", msg="작업 계획을 승인했습니다(확인 ①).")


@router.post("/plans/{plan_id}/cancel")
def plan_cancel(request: Request, plan_id: str, reason: str = Form("")):
    S.cancel_plan(_ctx(request), plan_id, reason)
    return back(f"/plans/{plan_id}", msg="작업 계획을 취소했습니다.")


# ───────── 임시저장함 ─────────
@router.get("/drafts", response_class=HTMLResponse)
def drafts(request: Request):
    return page(request, "drafts.html", docs=S.drafts_box(_ctx(request)), live=True)


def _detail(request: Request, doc_id: str) -> dict:
    d = S.doc_detail(_ctx(request), doc_id)
    d["spec"] = S.get_process_spec(_ctx(request), d["process_id"])
    d["labels"] = {f["key"]: f["label"] for f in d["spec"]["fields"]}
    if not d.get("approval_line") and d["status"] in ("VALID", "CONFIRMED"):
        try:
            d["line_preview"] = S.check_approval_line(_ctx(request), d["process_id"], values=d["values"])
        except ServiceError:
            d["line_preview"] = None
    return d


@router.get("/drafts/{doc_id}", response_class=HTMLResponse)
def draft(request: Request, doc_id: str):
    d = _detail(request, doc_id)
    if d["status"] not in ("INVALID", "VALID", "CONFIRMED"):
        return RedirectResponse(f"/docs/{doc_id}", status_code=303)
    return page(request, "draft.html", d=d, live=True)


@router.post("/drafts/{doc_id}/confirm")
def draft_confirm(request: Request, doc_id: str):
    S.confirm_draft_ui(_ctx(request), doc_id)
    return back(f"/drafts/{doc_id}", msg="초안을 확인했습니다(확인 ②). 이제 AI가 상신할 수 있습니다.")


@router.get("/docs/{doc_id}", response_class=HTMLResponse)
def doc(request: Request, doc_id: str):
    d = _detail(request, doc_id)
    if d.get("can_decide"):
        return RedirectResponse(f"/review/{doc_id}", status_code=303)
    return page(request, "doc.html", d=d, live=True)


# ───────── 결재함·결재 검토 ─────────
@router.get("/inbox", response_class=HTMLResponse)
def inbox(request: Request, view: str | None = None):
    data = S.inbox(_ctx(request))
    if view not in ("approver", "drafter"):
        view = "approver" if data["briefing"] else "drafter"
    return page(request, "inbox.html", data=data, view=view, live=True)


@router.get("/review/{doc_id}", response_class=HTMLResponse)
def review(request: Request, doc_id: str):
    d = _detail(request, doc_id)
    hl_fields = {c.get("field") for c in d.get("cautions", []) if c.get("field")}
    hl_seq = {c.get("evidence_seq") for c in d.get("cautions", []) if c.get("evidence_seq")}
    return page(request, "review.html", d=d, hl_fields=hl_fields, hl_seq=hl_seq, live=True)


@router.post("/review/{doc_id}/decide")
def decide(request: Request, doc_id: str, decision: str = Form(...), comment: str = Form("")):
    d = S.decide(_ctx(request), doc_id, decision, comment)
    label = "반려했습니다" if decision == "reject" else "승인했습니다"
    return back("/inbox", msg=f"{d.get('doc_no')} {d['title']} — {label}.")


# ───────── 회의실·규정·작업 기록 ─────────
@router.get("/rooms", response_class=HTMLResponse)
def rooms(request: Request, start: str | None = None):
    return page(request, "rooms.html", sch=S.rooms_schedule(start), live=True)


@router.get("/rules", response_class=HTMLResponse)
def rules(request: Request):
    return page(request, "rules.html", rules=S.rules_page())


@router.get("/audit", response_class=HTMLResponse)
def audit(request: Request):
    return page(request, "audit.html", rows=S.audit_list(), live=True)


# ───────── 시연 제어 패널 ─────────
@router.get("/demo", response_class=HTMLResponse)
def demo(request: Request):
    conns = json.loads((get_settings().seed_dir / "connections.json").read_text(encoding="utf-8"))
    gpt_file = get_settings().db_path.parent / "gpt_connect.json"
    gpt = json.loads(gpt_file.read_text(encoding="utf-8")) if gpt_file.exists() else None
    return page(request, "demo.html", conns=conns, settings=get_settings(), gpt=gpt)


@router.post("/demo/clock")
def demo_clock(request: Request, when: str = Form(...)):
    S.demo_set_clock(_ctx(request), when.replace(" ", "T") + (":00" if len(when) == 16 else ""))
    return back("/demo", msg="시연 시계를 맞췄습니다.")


@router.post("/demo/advance")
def demo_advance(request: Request, minutes: int = Form(...)):
    S.demo_advance(_ctx(request), minutes)
    return back("/demo", msg=f"시연 시계를 {minutes:+}분 옮겼습니다.")


@router.post("/demo/digest")
def demo_digest(request: Request):
    n = S.demo_send_digest(_ctx(request))
    return back("/demo", msg=f"정기 알림을 {n}명에게 보냈습니다.")


@router.post("/demo/mode")
def demo_mode(request: Request, mode: str = Form(...)):
    S.demo_set_confirm_mode(_ctx(request), mode)
    return back("/demo", msg=f"확인 방식을 {mode}로 바꿨습니다.")


@router.post("/demo/reset")
def demo_reset(request: Request):
    from core import seed_loader
    seed_loader.reset(get_settings().seed_dir)
    r = back("/demo", msg="초기화했습니다. 시드를 다시 적재하고 시연 시계를 처음으로 맞췄습니다.")
    r.set_cookie("demo_user", DEFAULT_USER, samesite="lax")
    return r
