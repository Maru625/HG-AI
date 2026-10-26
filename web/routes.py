"""P3 웹 화면 (설계서 9장). 보여줄 값은 모두 P1 서비스 계층에서 받는다. 승인·반려는 여기에만 있다."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from core import duel as D
from core import services as S
from core.config import get_settings
from core.errors import ServiceError
from web.gate import safe_next, set_entry_cookie

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
    if parts[0] == "LIMIT_EXCEEDED" and len(parts) == 2 and parts[1] == "per_person":
        return "회의비 1인당 한도 초과 사유"
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


# ───────── 입장 코드 (APPROVAL_SITE_KEY가 있을 때만 쓰인다. 문 자체는 web/gate.py) ─────────
@router.get("/enter", response_class=HTMLResponse)
def enter_page(request: Request, next: str = "/", err: str = ""):
    if not os.environ.get("APPROVAL_SITE_KEY"):
        return RedirectResponse(safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "enter.html", {"next": safe_next(next), "err": err})


@router.post("/enter")
def enter_submit(request: Request, code: str = Form(""), next: str = Form("/")):
    key = os.environ.get("APPROVAL_SITE_KEY", "")
    if not key:
        return RedirectResponse(safe_next(next), status_code=303)
    if not hmac.compare_digest(code.strip().encode(), key.encode()):
        return RedirectResponse("/enter?err=1&next=" + quote(safe_next(next), safe=""), status_code=303)
    r = RedirectResponse(safe_next(next), status_code=303)
    set_entry_cookie(r, key, request.url.scheme == "https")
    return r


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
    r.set_cookie("demo_user", emp_no, samesite="lax", max_age=365 * 86400)
    return r


@router.get("/as/{emp_no}")
def as_user(request: Request, emp_no: str, next: str = "/"):
    """기기 세팅용 링크: 이 주소를 열면 그 사용자로 고정된다(북마크해 두면 기기마다 사용자가 정해짐)."""
    if emp_no not in {u["emp_no"] for u in S.employees_for_switch()}:
        return back("/", err=f"없는 사용자입니다: {emp_no}")
    r = RedirectResponse(safe_next(next), status_code=303)
    r.set_cookie("demo_user", emp_no, samesite="lax", max_age=365 * 86400)
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
    hl_seq = {e["seq"] for e in d.get("low_confidence", []) if not e["checked"]}
    hl_seq |= {x.get("evidence_seq") for x in d.get("errors", []) if x.get("evidence_seq")}
    hl_fields = {x.get("field") for x in d.get("errors", []) if x.get("field")}
    return page(request, "draft.html", d=d, live=True, hl_seq=hl_seq, hl_fields=hl_fields)


@router.post("/drafts/{doc_id}/confirm")
async def draft_confirm(request: Request, doc_id: str):
    form = await request.form()
    checked = [int(x) for x in form.getlist("original_checked") if str(x).isdigit()]
    S.confirm_draft_ui(_ctx(request), doc_id, checked)
    d = S.doc_detail(_ctx(request), doc_id)
    nxt = "아래 [상신]으로 올리세요(확인 ③)." if d.get("can_submit") else "이제 AI가 상신할 수 있습니다(대화에서 '올려 줘')."
    return back(f"/drafts/{doc_id}", msg=f"초안을 확인했습니다(확인 ②). {nxt}")


@router.post("/drafts/{doc_id}/reasons")
async def draft_reasons(request: Request, doc_id: str):
    form = await request.form()
    reasons = {k[2:]: str(v) for k, v in form.multi_items() if k.startswith("r.") and str(v).strip()}
    r = S.update_reasons_ui(_ctx(request), doc_id, reasons)
    return back(f"/drafts/{doc_id}", msg=f"사유를 저장했습니다 → {r['status_label']}" + (" · 남은 사유 " + str(len(r["missing_reasons"])) + "개" if r["missing_reasons"] else ""))


@router.post("/drafts/{doc_id}/submit")
def draft_submit(request: Request, doc_id: str):
    r = S.submit_draft_ui(_ctx(request), doc_id)
    line = " → ".join(x["name"] for x in r["approval_line"])
    return back(f"/docs/{doc_id}", msg=f"{r['doc_no']} 상신했습니다(확인 ③). 결재선: {line}" + (" · 회의실 가예약" if r.get("reservation") else "")
                + (" · 결재자에게 긴급 알림" if r.get("notified") else ""))


@router.post("/drafts/{doc_id}/cancel")
def draft_cancel(request: Request, doc_id: str):
    S.cancel_draft_ui(_ctx(request), doc_id, "화면에서 취소")
    return back("/drafts", msg=f"{doc_id} 초안을 취소했습니다.")


# ───────── 화면에서 직접 기안 (AI 없이) ─────────
def _parse_draft_form(form, spec: dict) -> dict:
    """작성 양식 → save_draft_ui 입력. 이름 규칙: v.<키>, it.<키>.<번호>.<하위 키>, ev.<번호>.<항목>, r.<사유 키>, memo."""
    values: dict = {}
    items = list(form.multi_items())
    for f in spec["fields"]:
        k = f["key"]
        if f["type"] == "items":
            rows: dict[int, dict] = {}
            for key, val in items:
                if key.startswith(f"it.{k}."):
                    _, _, i, sub = key.split(".", 3)
                    rows.setdefault(int(i), {})[sub] = str(val).strip()
            got = [rows[i] for i in sorted(rows) if any(rows[i].values())]
            if got:
                values[k] = got
        else:
            v = str(form.get(f"v.{k}") or "").strip()
            if v != "":
                values[k] = v
    ev_rows: dict[int, dict] = {}
    for key, val in items:
        if key.startswith("ev."):
            _, i, fld = key.split(".", 2)
            ev_rows.setdefault(int(i), {})[fld] = str(val).strip()
    evidence = []
    for i in sorted(ev_rows):
        r = ev_rows[i]
        if not any(r.get(x) for x in ("date", "merchant", "amount")):
            continue
        evidence.append({k: v for k, v in r.items() if v != ""})
    reasons = {key[2:]: str(val).strip() for key, val in items if key.startswith("r.") and str(val).strip()}
    return {"values": values, "evidence": evidence, "reasons": reasons, "memo": str(form.get("memo") or "").strip()}


@router.get("/new", response_class=HTMLResponse)
def new_index(request: Request):
    return page(request, "new.html", data=S.case_list(_ctx(request)))


@router.get("/new/{process_id}", response_class=HTMLResponse)
def new_form(request: Request, process_id: str, case: str | None = None):
    return page(request, "edit.html", f=S.draft_form(_ctx(request), process_id=process_id, case_id=case))


@router.post("/new/{process_id}")
async def new_save(request: Request, process_id: str):
    form = await request.form()
    spec = S.get_process_spec(_ctx(request), process_id)
    r = S.save_draft_ui(_ctx(request), _parse_draft_form(form, spec), process_id=process_id)
    return back(f"/drafts/{r['doc_id']}", msg=_saved_msg(r))


@router.get("/drafts/{doc_id}/edit", response_class=HTMLResponse)
def draft_edit(request: Request, doc_id: str):
    return page(request, "edit.html", f=S.draft_form(_ctx(request), doc_id=doc_id))


@router.post("/drafts/{doc_id}/edit")
async def draft_edit_save(request: Request, doc_id: str):
    form = await request.form()
    d = S.doc_detail(_ctx(request), doc_id)
    spec = S.get_process_spec(_ctx(request), d["process_id"])
    r = S.save_draft_ui(_ctx(request), _parse_draft_form(form, spec), doc_id=doc_id)
    return back(f"/drafts/{r['doc_id']}", msg=_saved_msg(r))


def _saved_msg(r: dict) -> str:
    out = f"{r['doc_id']} 저장 v{r['version']} → {r['status_label']}"
    if r["errors"]:
        out += f" · 오류 {len(r['errors'])}개를 고쳐 주세요"
    elif r["missing_reasons"]:
        out += f" · 사유 {len(r['missing_reasons'])}개가 필요합니다"
    if r.get("note"):
        out += f" · {r['note']}"
    return out


@router.post("/docs/{doc_id}/redraft")
def doc_redraft(request: Request, doc_id: str):
    r = S.redraft_ui(_ctx(request), doc_id)
    if r["existing"]:
        return back(f"/drafts/{r['doc_id']}" if r["status"] in ("INVALID", "VALID", "CONFIRMED") else f"/docs/{r['doc_id']}",
                    msg="이미 다시 기안한 문서가 있습니다.")
    return back(f"/drafts/{r['doc_id']}/edit", msg="반려된 문서를 원본으로 잇는 새 초안을 만들었습니다. 반려 사유에 맞게 고쳐 저장하세요.")


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
def decide(request: Request, doc_id: str, decision: str = Form(...), comment: str = Form(""), kind: str = Form("결재")):
    d = S.decide(_ctx(request), doc_id, decision, comment)
    if kind == "합의":
        label = "합의하지 않았습니다(반려)" if decision == "reject" else "합의했습니다"
    else:
        label = "반려했습니다" if decision == "reject" else "승인했습니다"
    return back("/inbox", msg=f"{d.get('doc_no')} {d['title']} — {label}.")


@router.post("/review/{doc_id}/delay")
def delay_notice(request: Request, doc_id: str, reason: str = Form(""), until: str = Form("")):
    S.notify_delay(_ctx(request), doc_id, reason, until or None)
    return back(f"/review/{doc_id}", msg="기안자에게 처리 지연 사유를 알렸습니다(제4조).")


# ───────── 회의실 가위바위보 대결 (재미 기능) ─────────
def _json(fn, *a) -> JSONResponse:
    """화면 스크립트(duel.js)가 부르는 주소. 막히면 오류 형식 그대로 JSON으로 돌려준다."""
    try:
        return JSONResponse(fn(*a))
    except ServiceError as e:
        return JSONResponse(e.to_dict(), status_code=e.status)


@router.get("/duel/state")
def duel_state(request: Request):
    return _json(D.state, _ctx(request))


@router.post("/duel/start")
def duel_start(request: Request, doc_id: str = Form(...), res_id: int = Form(...)):
    D.start(_ctx(request), doc_id, res_id)
    return back(f"/drafts/{doc_id}", msg="가위바위보 도전장을 보냈습니다! 대결 창에서 고르세요.")


@router.post("/duel/{duel_id}/pick")
async def duel_pick(request: Request, duel_id: int):
    body = await request.json()
    return _json(D.pick, _ctx(request), duel_id, str(body.get("pick", "")))


@router.post("/duel/{duel_id}/yield")
def duel_yield(request: Request, duel_id: int):
    return _json(D.yield_, _ctx(request), duel_id)


@router.post("/duel/{duel_id}/cancel")
def duel_cancel(request: Request, duel_id: int):
    return _json(D.cancel, _ctx(request), duel_id)


@router.post("/duel/{duel_id}/seen")
def duel_seen(request: Request, duel_id: int):
    return _json(D.seen, _ctx(request), duel_id)


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


# ───────── AI 연결 안내 (참가자용) ─────────
def _connect_view(user: str) -> dict:
    """등록할 MCP 연결 목록. 배포 모드면 공개 주소, 발표 PC(gpt 실행)면 로컬 주소. 연결 = 사용자."""
    from core.app import MCP_INFO
    st = get_settings()
    conns = json.loads((st.seed_dir / "connections.json").read_text(encoding="utf-8"))
    gpt_file = st.db_path.parent / "gpt_connect.json"
    gpt = json.loads(gpt_file.read_text(encoding="utf-8")) if gpt_file.exists() else None
    if MCP_INFO:
        where, token_url = "deploy", MCP_INFO.get("token_url")
        by = {c["slug"]: {"url": c["url"], "bearer": c.get("bearer")} for c in MCP_INFO["connections"]}
    elif gpt:
        where, token_url = "local", gpt.get("token_url")
        by = {(c.get("slug") or c["mode"]): {"url": c.get("local_url"), "web_url": c.get("url"), "bearer": c.get("bearer"),
                                             "lan_url": c.get("lan_url")}
              for c in gpt.get("connections", [])}
    else:
        where, token_url, by = None, None, {}
    items = []
    for i, c in enumerate(conns):
        slug = c.get("slug") or c["mode"]
        b = by.get(slug, {})
        items.append({"slug": slug, "name": "approval_" + slug.replace("-", "_"), "label": c.get("label", ""),
                      "mode": c["mode"], "tools": 14 if c["mode"] == "drafter" else 8, "default": i < 2,
                      "is_me": c["emp_no"] == user, "url": b.get("url"), "web_url": b.get("web_url"), "lan_url": b.get("lan_url"),
                      "bearer": b.get("bearer")})
    me = next((u for u in S.employees_for_switch() if u["emp_no"] == user), None)
    return {"items": items, "where": where, "token_url": token_url if any(x["bearer"] for x in items) else None,
            "me_label": me["label"] if me else user}


@router.get("/connect", response_class=HTMLResponse)
def connect(request: Request):
    return page(request, "connect.html", **_connect_view(_user(request)))


# ───────── 시연 제어 패널 ─────────
# 배포처럼 여러 사람이 같은 서버를 쓰면 APPROVAL_ADMIN_KEY를 둔다. 그러면 초기화·시계·확인 방식 같은 조작은
# 운영자 키를 넣은 브라우저에서만 된다(사용자 전환·결재 같은 일반 기능은 그대로 누구나).
def _admin_hash(key: str) -> str:
    return hashlib.sha256(("approval-demo:" + key).encode("utf-8")).hexdigest()


def _admin_ok(request: Request) -> bool:
    key = os.environ.get("APPROVAL_ADMIN_KEY", "")
    return not key or request.cookies.get("demo_admin") == _admin_hash(key)


def _locked(request: Request):
    if not _admin_ok(request):
        return back("/demo", err="운영자 키가 필요합니다. 시연 제어 패널 위쪽에서 운영자 키를 넣으세요.")
    return None


@router.get("/demo", response_class=HTMLResponse)
def demo(request: Request):
    from core.app import MCP_INFO
    conns = json.loads((get_settings().seed_dir / "connections.json").read_text(encoding="utf-8"))
    gpt_file = get_settings().db_path.parent / "gpt_connect.json"
    gpt = json.loads(gpt_file.read_text(encoding="utf-8")) if gpt_file.exists() else None
    from core import clock
    return page(request, "demo.html", conns=conns, settings=get_settings(), gpt=gpt, mcp=MCP_INFO or None,
                devices=S.employees_for_switch(), base=get_settings().base_url,
                locked=bool(os.environ.get("APPROVAL_ADMIN_KEY")), admin_ok=_admin_ok(request), flowing=clock.flowing())


@router.post("/demo/unlock")
def demo_unlock(request: Request, key: str = Form("")):
    want = os.environ.get("APPROVAL_ADMIN_KEY", "")
    if not want or key != want:
        return back("/demo", err="운영자 키가 맞지 않습니다.")
    r = back("/demo", msg="운영자 키를 확인했습니다. 이 브라우저에서 시연 제어를 쓸 수 있습니다.")
    r.set_cookie("demo_admin", _admin_hash(want), samesite="lax", httponly=True, secure=request.url.scheme == "https")
    return r


@router.post("/demo/clock")
def demo_clock(request: Request, when: str = Form(...)):
    if (r := _locked(request)):
        return r
    S.demo_set_clock(_ctx(request), when.replace(" ", "T") + (":00" if len(when) == 16 else ""))
    return back("/demo", msg="시연 시계를 맞췄습니다.")


@router.post("/demo/advance")
def demo_advance(request: Request, minutes: int = Form(...)):
    if (r := _locked(request)):
        return r
    S.demo_advance(_ctx(request), minutes)
    return back("/demo", msg=f"시연 시계를 {minutes:+}분 옮겼습니다.")


@router.post("/demo/digest")
def demo_digest(request: Request):
    if (r := _locked(request)):
        return r
    n = S.demo_send_digest(_ctx(request))
    return back("/demo", msg=f"정기 알림을 {n}명에게 보냈습니다.")


@router.post("/demo/mode")
def demo_mode(request: Request, mode: str = Form(...)):
    if (r := _locked(request)):
        return r
    S.demo_set_confirm_mode(_ctx(request), mode)
    return back("/demo", msg=f"확인 방식을 {mode}로 바꿨습니다.")


@router.post("/demo/duel")
def demo_duel(request: Request, on: str = Form("1")):
    if (r := _locked(request)):
        return r
    D.set_enabled(_ctx(request), on == "1")
    return back("/demo", msg=f"회의실 가위바위보를 {'켰' if on == '1' else '껐'}습니다.")


@router.post("/demo/reset")
def demo_reset(request: Request):
    if (r := _locked(request)):
        return r
    from core import seed_loader
    seed_loader.reset(get_settings().seed_dir)
    r = back("/demo", msg="초기화했습니다. 시드를 다시 적재하고 시연 시계를 처음으로 맞췄습니다.")
    r.set_cookie("demo_user", DEFAULT_USER, samesite="lax")
    return r
