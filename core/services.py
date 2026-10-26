"""P1 서비스 계층 (계약 C1). P2(REST API를 거쳐)와 P3(직접)가 같은 함수를 쓴다.

- 판정과 저장은 여기서만 한다(설계서 3.1).
- 승인·반려(decide)는 웹 화면(channel=WEB)에서만 부를 수 있다. REST API로 열지 않는다(설계서 3.2).
- 모든 쓰기는 감사 기록을 남긴다(설계서 6.4).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import approval_line, cases, clock, dashboard, db, notify, regulations, review, validation
from .config import get_settings
from .errors import ServiceError
from .models import (ApprovalStep, Article, AuditLog, Connection, Department, Document, Employee, Notification,
                     Plan, PlanItem, Reservation, Room)

STATUS_LABEL = {"INVALID": "작성중 (검증 오류)", "VALID": "작성완료", "CONFIRMED": "확인완료", "SUBMITTED": "결재대기",
                "APPROVED": "승인", "REJECTED": "반려", "CANCELED": "취소"}
PLAN_LABEL = {"PENDING": "승인대기", "APPROVED": "승인됨", "CANCELED": "취소"}
STEP_LABEL = {"WAITING": "대기 전", "PENDING": "결재 차례", "APPROVED": "승인", "REJECTED": "반려", "SKIPPED": "중단"}
AGREE_LABEL = {"PENDING": "합의 차례", "APPROVED": "합의", "REJECTED": "합의 안 함"}
RES_LABEL = {"HOLD": "가예약", "CONFIRMED": "확정", "RELEASED": "해제"}
NOTE_LABEL = {"IMMEDIATE": "즉시", "DIGEST": "정기", "RESULT": "결재 결과", "DELAY": "처리 지연"}
DRAFT_STATES = ("INVALID", "VALID", "CONFIRMED")
APPROVER_TITLES = ("팀장", "실장", "본부장")


@dataclass(frozen=True)
class Ctx:
    emp_no: str
    channel: str = "WEB"          # MCP / WEB / SYSTEM
    mode: str | None = None       # drafter / approver (MCP 연결의 역할)


# ───────────────────────── 공통 도우미 ─────────────────────────
def link(path: str) -> str:
    return get_settings().base_url + path


def _emp(s: Session, emp_no: str) -> Employee:
    e = s.get(Employee, emp_no)
    if not e:
        raise ServiceError("NOT_FOUND", f"직원을 찾을 수 없습니다: {emp_no}")
    return e


def _name(s: Session, emp_no: str | None) -> str:
    e = s.get(Employee, emp_no) if emp_no else None
    return f"{e.name} {e.title or e.grade}" if e else (emp_no or "")


def audit(s: Session, ctx: Ctx, action: str, target: str = "", summary: str = "", result: str = "ok") -> None:
    s.add(AuditLog(at=clock.iso(clock.now(s)), real_at=dt.datetime.now().replace(microsecond=0).isoformat(),
                   channel=ctx.channel, actor=ctx.emp_no, action=action, target=target, summary=summary, result=result))


def audit_failure(token: str | None, action: str, target: str, exc: ServiceError) -> None:
    """막힌 쓰기 시도도 작업 기록에 남긴다(설계서 6.4). 예외로 끝난 요청은 그 세션이 되돌려지므로 따로 쓴다."""
    with db.session() as s:
        c = s.get(Connection, token or "")
        audit(s, Ctx(c.emp_no if c else "?", channel="MCP"), action, target, f"{exc.message} {exc.fix_hint}".strip(),
              result=exc.code)


def _next_id(s: Session, model, prefix: str, width: int) -> str:
    ids = [i for i in s.scalars(select(model.id)) if isinstance(i, str) and i.startswith(prefix)]
    n = max([int(i[len(prefix):]) for i in ids if i[len(prefix):].isdigit()] or [0]) + 1
    return f"{prefix}{n:0{width}d}"


def _next_doc_no(s: Session, when: dt.datetime) -> str:
    prefix = f"ST-{when:%Y%m%d}-"
    n = s.scalar(select(func.count()).select_from(Document).where(Document.doc_no.like(prefix + "%"))) or 0
    return f"{prefix}{n + 1:03d}"


def _require_mode(ctx: Ctx, mode: str) -> None:
    if ctx.channel == "MCP" and ctx.mode != mode:
        raise ServiceError("FORBIDDEN", f"{'기안자용' if mode == 'drafter' else '결재자용'} 연결에서만 쓸 수 있는 기능입니다.")


def doc_title(s: Session, d: Document) -> str:
    v = d.values or {}
    try:
        if d.process_id == "EXP-01":
            return f"{v.get('merchant', '')} {v.get('account', '')} {int(v.get('amount') or 0):,}원".strip()
        if d.process_id == "EXP-02":
            return f"{v.get('destination', '')} 출장비 정산 {int(v.get('total_amount') or 0):,}원"
        if d.process_id == "RPT-01":
            return f"{v.get('visit', '')} 출장결과보고"
        if d.process_id == "PUR-01":
            return f"{v.get('item', '')} {v.get('qty', '')}개 구매 {int(v.get('amount') or 0):,}원"
        if d.process_id == "MTG-01":
            room = s.get(Room, str(v.get("room", "")))
            return f"{v.get('title', '회의실')} ({v.get('date', '')} {v.get('start', '')}~{v.get('end', '')}, {room.name if room else v.get('room', '')})"
    except (TypeError, ValueError):
        pass
    return regulations.get(s, d.process_id)["name"]


def steps_view(s: Session, doc_id: str) -> list[dict]:
    d = s.get(Document, doc_id)
    now = clock.now(s)
    out = []
    for st in s.scalars(select(ApprovalStep).where(ApprovalStep.doc_id == doc_id).order_by(ApprovalStep.seq)):
        label = AGREE_LABEL.get(st.status, STEP_LABEL[st.status]) if st.kind == "합의" else STEP_LABEL[st.status]
        row = {"seq": st.seq, "approver": st.approver, "name": _name(s, st.approver), "kind": st.kind,
               "level": st.level, "status": st.status, "status_label": label,
               "basis": st.basis, "comment": st.comment, "started_at": st.started_at, "acted_at": st.acted_at,
               "delay_reason": st.delay_reason, "delay_until": st.delay_until, "delay_at": st.delay_at}
        if st.status == "PENDING" and d is not None and d.status == "SUBMITTED":
            row["due"] = review.step_due(s, d, st, now)      # 제4조 처리 기한
        out.append(row)
    return out


def _low_conf_seqs(d: Document) -> list[int]:
    """판독 신뢰도가 낮다고 서버가 판정한 증빙 번호 (제22조)."""
    return sorted({w["evidence_seq"] for w in (d.validation or {}).get("warnings", [])
                   if w.get("code") == "LOW_CONFIDENCE" and w.get("evidence_seq")})


def doc_view(s: Session, d: Document, *, full: bool = True) -> dict:
    p = regulations.get(s, d.process_id)
    v = {"doc_id": d.id, "doc_no": d.doc_no, "plan_id": d.plan_id, "item_no": d.item_no, "process_id": d.process_id,
         "process_name": p["name"], "title": doc_title(s, d), "drafter": {"emp_no": d.drafter, "name": _name(s, d.drafter)},
         "status": d.status, "status_label": STATUS_LABEL[d.status], "version": d.version,
         "ai_written": d.created_via == "MCP" or d.updated_via == "MCP",
         "created_via": d.created_via, "updated_via": d.updated_via,
         "submitted_at": d.submitted_at, "final_at": d.final_at, "link": link(_doc_path(d))}
    if full:
        low = _low_conf_seqs(d)
        checked = list(d.original_checked or [])
        v.update({"values": d.values, "evidence": d.evidence, "reasons": d.reasons, "memo": d.memo,
                  "errors": (d.validation or {}).get("errors", []), "warnings": (d.validation or {}).get("warnings", []),
                  "missing_reasons": (d.validation or {}).get("missing_reasons", []),
                  "low_confidence": [{"seq": e.get("seq"), "date": e.get("date"), "merchant": e.get("merchant"),
                                      "amount": e.get("amount"), "confidence": e.get("confidence"),
                                      "checked": e.get("seq") in checked}
                                     for e in d.evidence or [] if e.get("seq") in low],
                  "original_checked": checked, "original_checked_at": d.original_checked_at,
                  "confirmed_at": d.confirmed_at, "original_doc_id": d.original_doc_id,
                  "reject_comment": d.reject_comment, "approval_line": steps_view(s, d.id) if d.submitted_at else None})
        if d.status == "SUBMITTED":
            v["due"] = review.step_due(s, d, review.current_step(s, d), clock.now(s))
        if d.process_id == "MTG-01":
            res = s.scalars(select(Reservation).where(Reservation.doc_id == d.id)).first()
            v["reservation"] = {"status": res.status, "status_label": RES_LABEL[res.status], "note": res.note} if res else None
    return v


def _doc_path(d: Document) -> str:
    return f"/drafts/{d.id}" if d.status in DRAFT_STATES else f"/docs/{d.id}"


def _plan_view(s: Session, p: Plan) -> dict:
    items = list(s.scalars(select(PlanItem).where(PlanItem.plan_id == p.id).order_by(PlanItem.item_no)))
    docs = list(s.scalars(select(Document).where(Document.plan_id == p.id).order_by(Document.id)))
    return {"plan_id": p.id, "title": p.title, "summary": p.summary, "status": p.status, "status_label": PLAN_LABEL[p.status],
            "drafter": {"emp_no": p.drafter, "name": _name(s, p.drafter)}, "created_via": p.created_via,
            "ai_written": p.created_via == "MCP", "created_at": p.created_at, "approved_at": p.approved_at,
            "approved_via": p.approved_via, "cancel_reason": p.cancel_reason,
            "items": [{"item_no": it.item_no, "process_id": it.process_id, "process_name": regulations.get(s, it.process_id)["name"],
                       "summary": it.summary, "sources": it.sources, "evidence": it.evidence, "evidence_check": it.evidence_check}
                      for it in items],
            "documents": [doc_view(s, d, full=False) for d in docs], "link": link(f"/plans/{p.id}")}


def _own_doc(s: Session, ctx: Ctx, doc_id: str) -> Document:
    d = s.get(Document, doc_id)
    if not d:
        raise ServiceError("NOT_FOUND", f"문서를 찾을 수 없습니다: {doc_id}")
    if d.drafter != ctx.emp_no:
        raise ServiceError("FORBIDDEN", "본인이 기안한 문서만 다룰 수 있습니다.")
    return d


def _own_plan(s: Session, ctx: Ctx, plan_id: str) -> Plan:
    p = s.get(Plan, plan_id)
    if not p:
        raise ServiceError("NOT_FOUND", f"작업 계획을 찾을 수 없습니다: {plan_id}")
    if p.drafter != ctx.emp_no:
        raise ServiceError("FORBIDDEN", "본인의 작업 계획만 다룰 수 있습니다.")
    return p


def confirm_mode(s: Session) -> str:
    return (clock._state(s, "confirm_mode", {"mode": "A"}) or {}).get("mode", "A")


# ───────────────────────── 연결·사용자 ─────────────────────────
def connection_info(token: str | None) -> dict:
    with db.session() as s:
        c = s.get(Connection, token or "")
        if not c:
            raise ServiceError("UNAUTHORIZED", fix_hint="Claude Desktop 설정의 APPROVAL_TOKEN 값을 확인하세요.")
        e = _emp(s, c.emp_no)
        return {"emp_no": e.emp_no, "name": e.name, "mode": c.mode, "label": c.label}


def whoami(ctx: Ctx) -> dict:
    with db.session() as s:
        e = _emp(s, ctx.emp_no)
        chain = approval_line._chain(s, e.dept)
        team = chain.get("team")
        dept = s.get(Department, e.dept)
        return {"emp_no": e.emp_no, "name": e.name, "grade": e.grade, "title": e.title, "dept": dept.name if dept else e.dept,
                "office": chain["office"].name if "office" in chain else None,
                "division": chain["division"].name if "division" in chain else None,
                "team_head": _name(s, team.head) if team and team.head != e.emp_no else None,
                "connection_mode": ctx.mode, "today": clock.iso(clock.now(s)), "confirm_mode": confirm_mode(s)}


# ───────────────────────── 규정 ─────────────────────────
def list_processes(ctx: Ctx) -> dict:
    with db.session() as s:
        return {"processes": [{"process_id": pid, "name": p["name"], "when": p["when"],
                               "approval_required": p["approval_required"], "depth": p.get("depth")}
                              for pid, p in regulations.processes(s).items()]}


def search_rules(ctx: Ctx, query: str) -> dict:
    with db.session() as s:
        hits = regulations.search(s, query)
        return {"query": query, "results": hits,
                "hint": "결과가 없으면 다른 낱말로 다시 찾거나 list_processes를 쓰세요." if not hits else ""}


def get_process_spec(ctx: Ctx, process_id: str) -> dict:
    with db.session() as s:
        return regulations.spec(s, process_id)


def check_approval_line(ctx: Ctx, process_id: str, amount: int | None = None, dept: str | None = None,
                        values: dict | None = None) -> dict:
    with db.session() as s:
        p = regulations.get(s, process_id)
        vals = dict(values or {})
        if amount is not None and p.get("amount_field"):
            vals[p["amount_field"]] = amount
        dept_code = None
        if dept:
            d = s.get(Department, dept) or s.scalars(select(Department).where(Department.name == dept)).first()
            if not d:
                raise ServiceError("NOT_FOUND", f"부서를 찾을 수 없습니다: {dept}")
            dept_code = d.code
        return approval_line.compute(s, p, _emp(s, ctx.emp_no), vals, dept_code)


# ───────────────────────── 증빙·회의실 ─────────────────────────
def check_evidence(ctx: Ctx, process_id: str, evidence: list[dict], reasons: dict | None = None) -> dict:
    _require_mode(ctx, "drafter")
    with db.session() as s:
        p = regulations.get(s, process_id)
        evs, issues = validation.normalize_evidence(evidence)
        issues += validation.evidence_rules(s, p, _emp(s, ctx.emp_no), evs, reasons or {})
        return _evidence_report(p, evs, issues)


def _evidence_report(p: dict, evs: list[dict], issues: list[dict]) -> dict:
    per = {e["seq"]: [] for e in evs}
    general = []
    for x in issues:
        (per[x["evidence_seq"]] if x.get("evidence_seq") in per else general).append(x)
    by_cat: dict[str, int] = {}
    for e in evs:
        if isinstance(e.get("amount"), int):
            by_cat[e.get("category", "?")] = by_cat.get(e.get("category", "?"), 0) + e["amount"]
    exclude = sorted({x["evidence_seq"] for x in issues if x["code"] == "DUPLICATE_EVIDENCE" and x.get("evidence_seq")})
    kept_total = sum(e["amount"] for e in evs if isinstance(e.get("amount"), int) and e["seq"] not in exclude)
    return {"process_id": p["id"],
            "items": [{"seq": e["seq"], "date": e.get("date"), "merchant": e.get("merchant"), "category": e.get("category"),
                       "amount": e.get("amount"), "results": per[e["seq"]] or [{"code": "OK", "level": "ok", "message": "문제 없음"}]}
                      for e in evs],
            "general": general, "totals_by_category": by_cat,
            "total": sum(by_cat.values()), "total_without_excluded": kept_total,
            "exclude_suggested": exclude,
            "reasons_needed": sorted({x["reason_key"] for x in issues if x.get("needs_reason") and not x.get("reason")}),
            "summary": f"증빙 {len(evs)}건, 오류 {sum(1 for x in issues if x['level'] == 'error')}건, 경고 {sum(1 for x in issues if x['level'] == 'warning')}건"}


def check_room_availability(ctx: Ctx, date: str, start: str, end: str, people: int) -> dict:
    _require_mode(ctx, "drafter")
    with db.session() as s:
        try:
            day = validation._to_date(date)
            st, en = validation._to_time(start), validation._to_time(end)
            people = int(people)
        except (ValueError, TypeError):
            raise ServiceError("BAD_REQUEST", "날짜(YYYY-MM-DD), 시각(HH:MM), 인원(정수)을 확인하세요.")
        bh = regulations.common(s).get("business_hours", {"start": "09:00", "end": "18:00"})
        rooms = []
        for r in s.scalars(select(Room).order_by(Room.id)):
            cs = validation.conflicts(s, r.id, day, st, en)
            notes = []
            if r.kind == "large":
                notes.append("대회의실 사용 → 경영지원팀 합의 필요 (제14조)")
            if st < bh["start"] or en > bh["end"]:
                notes.append("업무시간 외 사용 → 경영지원팀 합의 필요 (제14조)")
            rooms.append({"room_id": r.id, "name": r.name, "capacity": r.capacity, "kind": r.kind,
                          "fits": people <= r.capacity, "available": not cs and people <= r.capacity,
                          "conflicts": [{"start": c.start, "end": c.end, "title": c.title,
                                         "status": RES_LABEL[c.status]} for c in cs], "notes": notes})
        avail = [r for r in rooms if r["available"]]
        best = next((r for r in avail if r["kind"] == "small"), avail[0] if avail else None)
        return {"date": day, "start": st, "end": en, "people": people, "rooms": rooms,
                "recommended": best["room_id"] if best else None,
                "summary": (f"추천: {best['name']}" if best else "가능한 회의실이 없습니다. 시간을 바꿔 다시 찾으세요.")}


# ───────────────────────── 작업 계획 ─────────────────────────
def create_plan(ctx: Ctx, title: str, summary: str, items: list[dict]) -> dict:
    _require_mode(ctx, "drafter")
    if not items:
        raise ServiceError("BAD_REQUEST", "항목이 하나 이상 있어야 합니다.")
    with db.session() as s:
        drafter = _emp(s, ctx.emp_no)
        now = clock.now(s)
        pid = _next_id(s, Plan, "PLAN-", 3)
        plan = Plan(id=pid, title=title or "작업 계획", summary=summary or "", drafter=ctx.emp_no, status="PENDING",
                    created_via=ctx.channel, created_at=clock.iso(now))
        s.add(plan)
        for n, it in enumerate(items, 1):
            p = regulations.get(s, it.get("process_id", ""))
            ev = it.get("evidence") or []
            check = None
            if ev:
                evs, iss = validation.normalize_evidence(ev)
                iss += validation.evidence_rules(s, p, drafter, evs, it.get("reasons") or {})
                check = _evidence_report(p, evs, iss)
                ev = evs
            s.add(PlanItem(plan_id=pid, item_no=n, process_id=p["id"], summary=it.get("summary", ""),
                           sources=it.get("sources", ""), evidence=ev, evidence_check=check))
        s.flush()
        audit(s, ctx, "create_plan", pid, f"{title} ({len(items)}건)")
        view = _plan_view(s, plan)
        view["next"] = ("사용자에게 목록을 보여주고 승인을 받은 뒤 approve_plan을 부르세요."
                        if confirm_mode(s) == "A" else "화면 확인형(B)입니다. 사용자에게 화면 링크에서 승인해 달라고 안내하세요.")
        return view


def approve_plan(ctx: Ctx, plan_id: str) -> dict:
    with db.session() as s:
        p = _own_plan(s, ctx, plan_id)
        if ctx.channel == "MCP" and confirm_mode(s) == "B":
            raise ServiceError("CONFIRM_IN_UI_REQUIRED", fix_hint=f"사용자에게 화면에서 승인해 달라고 안내하세요: {link('/plans/' + p.id)}",
                               details={"link": link("/plans/" + p.id)})
        if p.status == "CANCELED":
            raise ServiceError("PLAN_CANCELED")
        if p.status == "APPROVED":
            return {**_plan_view(s, p), "message": "이미 승인된 계획입니다."}
        now = clock.now(s)
        p.status, p.approved_at, p.approved_via = "APPROVED", clock.iso(now), ctx.channel
        audit(s, ctx, "approve_plan", p.id, "확인 ① 계획 승인")
        return {**_plan_view(s, p), "next": "save_drafts로 항목별 초안을 저장하세요."}


def cancel_plan(ctx: Ctx, plan_id: str, reason: str = "") -> dict:
    with db.session() as s:
        p = _own_plan(s, ctx, plan_id)
        if p.status == "CANCELED":
            return _plan_view(s, p)
        now = clock.now(s)
        canceled = []
        from . import duel
        for d in s.scalars(select(Document).where(Document.plan_id == p.id, Document.status.in_(("INVALID", "VALID", "CONFIRMED")))):
            d.status, d.updated_at, d.updated_via = "CANCELED", clock.iso(now), ctx.channel
            duel.release_for_doc(s, d, "계획 취소로 가위바위보 승리 가예약을 풂")
            canceled.append(d.id)
        p.status, p.canceled_at, p.cancel_reason = "CANCELED", clock.iso(now), reason
        audit(s, ctx, "cancel_plan", p.id, f"사유: {reason} / 함께 취소: {', '.join(canceled) or '없음'}")
        return {**_plan_view(s, p), "canceled_documents": canceled}


def list_plans(ctx: Ctx) -> list[dict]:
    with db.session() as s:
        return [_plan_view(s, p) for p in s.scalars(select(Plan).where(Plan.drafter == ctx.emp_no).order_by(Plan.id.desc()))]


def plan_detail(ctx: Ctx, plan_id: str) -> dict:
    with db.session() as s:
        return _plan_view(s, _own_plan(s, ctx, plan_id))


# ───────────────────────── 초안 ─────────────────────────
def save_drafts(ctx: Ctx, plan_id: str, drafts: list[dict]) -> dict:
    _require_mode(ctx, "drafter")
    with db.session() as s:
        plan = _own_plan(s, ctx, plan_id)
        if plan.status == "CANCELED":
            raise ServiceError("PLAN_CANCELED")
        if plan.status != "APPROVED":
            raise ServiceError("PLAN_NOT_APPROVED", fix_hint="사용자 승인(확인 ①)을 받고 approve_plan을 먼저 부르세요.")
        drafter = _emp(s, ctx.emp_no)
        now = clock.now(s)
        results = []
        for dr in drafts or []:
            results.append(_save_one(s, ctx, plan, drafter, dr, now))
        audit(s, ctx, "save_drafts", plan.id,
              ", ".join(f"{r.get('doc_id', '?')}:{r.get('status', r.get('code'))}" for r in results))
        return {"plan_id": plan.id, "results": results,
                "next": "오류가 있으면 고쳐서 다시 저장하고, 사람이 정해야 하는 값(사유 등)은 사용자에게 물으세요. "
                        "모두 작성완료면 사용자에게 화면 링크로 초안을 확인받으세요(확인 ②)."}


def _save_one(s: Session, ctx: Ctx, plan: Plan, drafter: Employee, dr: dict, now: dt.datetime) -> dict:
    item = s.scalars(select(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.item_no == dr.get("item_no"))).first()
    if not item:
        return {"item_no": dr.get("item_no"), "ok": False, "code": "BAD_REQUEST", "message": "계획에 없는 항목 번호입니다."}
    p = regulations.get(s, item.process_id)
    existing = s.scalars(select(Document).where(Document.plan_id == plan.id, Document.item_no == item.item_no,
                                                Document.status != "CANCELED").order_by(Document.id.desc())).first()
    original = None
    if existing and existing.status in ("SUBMITTED", "APPROVED"):
        return {"item_no": item.item_no, "doc_id": existing.id, "ok": False, "code": "ALREADY_SUBMITTED",
                "message": "이미 상신된 문서는 고칠 수 없습니다."}
    if existing and existing.status == "REJECTED":
        original, existing = existing, None
    d, res, note = _write_doc(s, ctx, drafter, p, dr, now, existing=existing, original=original,
                              plan_id=plan.id, item_no=item.item_no)
    return _save_result(s, p, drafter, d, res, note)


def _write_doc(s: Session, ctx: Ctx, drafter: Employee, p: dict, dr: dict, now: dt.datetime, *,
               existing: Document | None = None, original: Document | None = None,
               plan_id: str | None = None, item_no: int | None = None) -> tuple[Document, dict, str]:
    """초안을 검증해 새로 만들거나(existing 없음) 고친다. AI(save_drafts)와 화면(save_draft_ui)이 같이 쓴다."""
    orig_id = original.id if original else (existing.original_doc_id if existing else None)
    res = validation.validate(s, p, drafter, dr.get("values") or {}, dr.get("evidence") or [], dr.get("reasons") or {},
                              now=now, doc_id=existing.id if existing else None, original_doc_id=orig_id)
    status = "INVALID" if res["errors"] else "VALID"
    state = {k: res[k] for k in ("errors", "warnings", "missing_reasons")}
    reasons = {str(k): str(v) for k, v in (dr.get("reasons") or {}).items() if str(v or "").strip()}
    note = ""
    if existing:
        d = existing
        if d.status == "CONFIRMED":
            note = "확인완료였던 문서를 고쳐 확인이 해제되었습니다. 다시 확인(②)을 받아야 합니다."
        d.values, d.evidence, d.reasons = res["values"], res["evidence"], reasons
        d.memo = dr.get("memo", d.memo)
        d.version += 1
        d.status, d.confirmed_at = status, None
        d.original_checked, d.original_checked_at = [], None      # 원본 대조(제22조)도 확인 ②와 함께 다시 받는다
        d.validation = state
        d.updated_at, d.updated_via = clock.iso(now), ctx.channel
    else:
        d = Document(id=_next_id(s, Document, "DOC-", 4), plan_id=plan_id, item_no=item_no, process_id=p["id"],
                     drafter=drafter.emp_no, values=res["values"], evidence=res["evidence"], reasons=reasons,
                     memo=dr.get("memo", "") or "", status=status, validation=state, version=1,
                     original_doc_id=original.id if original else None, original_checked=[],
                     created_via=ctx.channel, updated_via=ctx.channel, created_at=clock.iso(now), updated_at=clock.iso(now))
        s.add(d)
        s.flush()
        if original:
            note = f"반려된 {original.doc_no}을 고쳐 새 문서로 다시 기안했습니다."
    if p["id"] == "MTG-01":
        _sync_room_hold(s, d)
    return d, res, note


def _save_result(s: Session, p: dict, drafter: Employee, d: Document, res: dict, note: str) -> dict:
    preview, line_note = None, ""
    if not res["errors"]:
        preview = [{"seq": x["seq"], "name": x["name"], "kind": x["kind"], "level": x["level"]}
                   for x in approval_line.compute(s, p, drafter, d.values)["steps"]]
        if not preview:
            line_note = "결재할 사람이 없습니다. 기안자 위로 결재할 직책자가 조직도에 없어 상신할 수 없습니다(제3조)."
    low = _low_conf_seqs(d)
    return {"item_no": d.item_no, "doc_id": d.id, "ok": not res["errors"], "status": d.status,
            "status_label": STATUS_LABEL[d.status], "version": d.version, "errors": res["errors"],
            "warnings": res["warnings"], "missing_reasons": res["missing_reasons"], "approval_line_preview": preview,
            "approval_line_note": line_note,
            "original_check_needed": low,
            "note": " ".join(x for x in (note, (f"증빙 {', '.join(map(str, low))}번은 판독이 불확실합니다. 확인(②) 때 기안자가 원본과 "
                                                 "대조했는지 확인합니다(제22조)." if low else "")) if x),
            "link": link(f"/drafts/{d.id}")}


def _sync_room_hold(s: Session, d: Document) -> None:
    """초안 단계에서는 예약을 잡지 않는다. 상신 때 가예약한다(설계서 5.3).
    예외: 회의실 가위바위보에서 이긴 초안은 상신 전에도 가예약이 있다(재미 기능). 날짜·시간·회의실을 바꾸면 그 가예약을 푼다."""
    v = d.values or {}
    for r in s.scalars(select(Reservation).where(Reservation.doc_id == d.id, Reservation.status == "HOLD")):
        if (r.room_id, r.date, r.start, r.end) != (v.get("room"), v.get("date"), v.get("start"), v.get("end")):
            r.status, r.note = "RELEASED", "초안의 날짜·시간·회의실을 바꿔 가위바위보 승리 가예약을 풂"


def confirm_drafts(ctx: Ctx, doc_ids: list[str], original_checked: dict | None = None) -> dict:
    """확인 ②. original_checked: {문서 ID: [기안자가 원본과 대조했다고 답한 증빙 번호]} (제22조)."""
    checked_map = {str(k): v for k, v in (original_checked or {}).items()}
    with db.session() as s:
        if ctx.channel == "MCP":
            _require_mode(ctx, "drafter")
            if confirm_mode(s) == "B":
                raise ServiceError("CONFIRM_IN_UI_REQUIRED", fix_hint=f"사용자에게 임시저장함 화면에서 확인해 달라고 안내하세요: {link('/drafts')}",
                                   details={"link": link("/drafts")})
        now = clock.now(s)
        results = []
        for did in doc_ids or []:
            try:
                d = _own_doc(s, ctx, did)
                results.append(_confirm_one(s, ctx, d, now, checked_map.get(did, checked_map.get(d.doc_no or ""))))
            except ServiceError as e:
                results.append({"doc_id": did, **e.to_dict()})
                audit(s, ctx, "confirm_draft", did, e.message, result=e.code)
        return {"results": results, "next": "확인완료 문서는 submit_drafts로 상신할 수 있습니다(확인 ③). "
                                            "ORIGINAL_CHECK_REQUIRED가 온 문서는 사용자에게 원본 대조 여부를 물은 뒤 다시 부르세요."}


def _as_seqs(v) -> set[int]:
    if v is None:
        return set()
    if isinstance(v, (int, str)):
        v = [v]
    out = set()
    for x in v:
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            pass
    return out


def _confirm_one(s: Session, ctx: Ctx, d: Document, now: dt.datetime, checked=None) -> dict:
    if d.status in ("SUBMITTED", "APPROVED"):
        raise ServiceError("ALREADY_SUBMITTED")
    if d.status == "CANCELED":
        raise ServiceError("PLAN_CANCELED", "취소된 문서입니다.")
    if d.status == "REJECTED":
        raise ServiceError("ALREADY_SUBMITTED", "반려된 문서입니다. 다시 기안한 새 문서를 확인하세요.")
    if d.status == "INVALID":
        raise ServiceError("VALIDATION_FAILED", fix_hint="오류를 고쳐 다시 저장하세요.",
                           details={"errors": (d.validation or {}).get("errors", [])})
    missing = (d.validation or {}).get("missing_reasons", [])
    if missing:
        raise ServiceError("REASON_REQUIRED", fix_hint=f"사용자에게 사유를 물어 reasons에 {', '.join(missing)}를 넣고 다시 저장하세요.",
                           details={"missing_reasons": missing})
    if d.status == "CONFIRMED" and d.confirmed_version == d.version:
        return {"doc_id": d.id, "ok": True, "status": d.status, "message": "이미 확인완료입니다."}
    need = _low_conf_seqs(d)
    got = _as_seqs(checked)
    lacking = [x for x in need if x not in got]
    if lacking:
        evs = [{"seq": e.get("seq"), "date": e.get("date"), "merchant": e.get("merchant"), "amount": e.get("amount"),
                "confidence": e.get("confidence")} for e in d.evidence or [] if e.get("seq") in lacking]
        hint = ("사용자에게 이 영수증의 원본(종이 영수증·카드 사용 내역)과 날짜·금액이 맞는지 물으세요. 맞다고 하면 "
                f"original_checked에 {{\"{d.id}\": {lacking}}}를 넣어 confirm_drafts를 다시 부르세요. 값이 다르면 고쳐서 save_drafts로 다시 저장하세요."
                if ctx.channel == "MCP" else
                "원본(종이 영수증·카드 사용 내역)과 날짜·금액을 대조한 뒤 '원본과 대조했습니다'에 표시하고 다시 확인하세요.")
        raise ServiceError("ORIGINAL_CHECK_REQUIRED",
                           f"판독이 불확실한 증빙 {', '.join(map(str, lacking))}번을 기안자가 원본과 대조했는지 확인이 필요합니다(제22조).",
                           fix_hint=hint, details={"doc_id": d.id, "evidence": evs})
    d.original_checked = [x for x in need if x in got]
    d.original_checked_at = clock.iso(now) if need else None
    d.status, d.confirmed_version, d.confirmed_at = "CONFIRMED", d.version, clock.iso(now)
    audit(s, ctx, "confirm_draft", d.id, f"확인 ② v{d.version}"
          + (f" · 증빙 {', '.join(map(str, need))}번 원본 대조 확인(제22조)" if need else ""))
    return {"doc_id": d.id, "ok": True, "status": d.status, "status_label": STATUS_LABEL[d.status],
            "original_checked": d.original_checked}


def confirm_draft_ui(ctx: Ctx, doc_id: str, original_checked: list | None = None) -> dict:
    with db.session() as s:
        d = _own_doc(s, ctx, doc_id)
        try:
            return _confirm_one(s, ctx, d, clock.now(s), original_checked)
        except ServiceError as e:
            audit_web_failure(ctx, "confirm_draft", doc_id, e)
            raise


def audit_web_failure(ctx: Ctx, action: str, target: str, exc: ServiceError) -> None:
    """화면에서 막힌 시도도 작업 기록에 남긴다. 실패한 요청의 세션은 되돌려지므로 따로 쓴다."""
    with db.session() as s2:
        audit(s2, ctx, action, target, f"{exc.message} {exc.fix_hint}".strip(), result=exc.code)


def submit_drafts(ctx: Ctx, doc_ids: list[str]) -> dict:
    _require_mode(ctx, "drafter")
    with db.session() as s:
        now = clock.now(s)
        results, approvers = [], set()
        for did in doc_ids or []:
            try:
                d = _own_doc(s, ctx, did)
                r = _submit_one(s, ctx, d, now)
                approvers.add(r.pop("_first_approver"))
                results.append(r)
            except ServiceError as e:
                results.append({"doc_id": did, **e.to_dict()})
                audit(s, ctx, "submit_draft", did, e.message, result=e.code)
        s.flush()
        notified = notify.check_urgent(s, approvers, now, "상신")
        return {"results": results, "notified": [{"recipient": _name(s, n.recipient), "kind": n.kind, "trigger": n.trigger}
                                                  for n in notified],
                "next": "결재자가 웹 화면에서 승인·반려합니다. 진행 상황은 get_status로 확인하세요."}


def _submit_one(s: Session, ctx: Ctx, d: Document, now: dt.datetime) -> dict:
    if d.status in ("SUBMITTED", "APPROVED"):
        raise ServiceError("ALREADY_SUBMITTED")
    if d.status == "CANCELED":
        raise ServiceError("PLAN_CANCELED")
    plan = s.get(Plan, d.plan_id) if d.plan_id else None
    if plan and plan.status == "CANCELED":
        raise ServiceError("PLAN_CANCELED")
    if d.status == "REJECTED":
        raise ServiceError("ALREADY_SUBMITTED", "반려된 문서는 다시 올릴 수 없습니다. 고쳐서 다시 기안한 새 문서를 상신하세요.")
    if d.status != "CONFIRMED":
        if d.confirmed_version and d.confirmed_version != d.version:
            raise ServiceError("MODIFIED_AFTER_CONFIRM", fix_hint="확인 뒤 고친 내용(v%d → v%d)을 사용자에게 다시 확인받으세요."
                               % (d.confirmed_version, d.version), details={"link": link(f"/drafts/{d.id}")})
        raise ServiceError("NOT_CONFIRMED", fix_hint="사용자 재확인(확인 ②)을 받고 confirm_drafts를 먼저 부르세요.")
    if d.confirmed_version != d.version:
        raise ServiceError("MODIFIED_AFTER_CONFIRM", fix_hint="고친 내용을 사용자에게 다시 확인받으세요.")
    p = regulations.get(s, d.process_id)
    drafter = _emp(s, d.drafter)
    res = validation.validate(s, p, drafter, d.values, d.evidence, d.reasons, now=now, doc_id=d.id,
                              original_doc_id=d.original_doc_id)
    if res["errors"]:
        room = [x for x in res["errors"] if x["code"] == "ROOM_CONFLICT"]
        raise ServiceError("ROOM_CONFLICT" if room else "VALIDATION_FAILED",
                           fix_hint="check_room_availability로 다시 찾아 초안을 고치세요." if room else "오류를 고쳐 다시 저장·확인하세요.",
                           details={"errors": res["errors"]})
    line = approval_line.compute(s, p, drafter, d.values)
    if not line["steps"]:
        raise ServiceError("BAD_REQUEST", "결재선을 만들 수 없습니다. 기안자 위로 결재할 직책자가 조직도에 없습니다(제3조).",
                           fix_hint="이 시연 조직도에서 본부장은 최상위 직책이라 결재받을 사람이 없습니다. 다른 사용자로 기안하세요.")
    d.doc_no = _next_doc_no(s, now)
    d.status, d.submitted_at = "SUBMITTED", clock.iso(now)
    d.validation = {k: res[k] for k in ("errors", "warnings", "missing_reasons")}
    for st in line["steps"]:
        s.add(ApprovalStep(doc_id=d.id, seq=st["seq"], approver=st["approver"], kind=st["kind"], level=st["level"],
                           article=st["article"], basis=st["basis"], status="PENDING" if st["seq"] == 1 else "WAITING",
                           started_at=clock.iso(now) if st["seq"] == 1 else None))
    hold = None
    if d.process_id == "MTG-01":
        v = d.values
        mine = s.scalars(select(Reservation).where(Reservation.doc_id == d.id)).first()   # 가위바위보 승리 가예약이 있으면 그대로 쓴다
        if mine:
            from .duel import WIN_NOTE
            won = mine.status == "HOLD" and mine.note == WIN_NOTE
            mine.room_id, mine.date, mine.start, mine.end = v["room"], v["date"], v["start"], v["end"]
            mine.title, mine.emp_no, mine.status = v.get("title", ""), d.drafter, "HOLD"
            mine.note = "가위바위보 승리 자리" if won else None
        else:
            s.add(Reservation(room_id=v["room"], date=v["date"], start=v["start"], end=v["end"], title=v.get("title", ""),
                              emp_no=d.drafter, doc_id=d.id, status="HOLD"))
        hold = "가예약"
    s.flush()
    audit(s, ctx, "submit_draft", d.id, f"확인 ③ 상신 {d.doc_no} → {line['steps'][0]['name']}" + (" / 회의실 가예약" if hold else ""))
    return {"doc_id": d.id, "doc_no": d.doc_no, "ok": True, "status": d.status, "status_label": STATUS_LABEL[d.status],
            "approval_line": [{"seq": x["seq"], "name": x["name"], "kind": x["kind"], "level": x["level"]} for x in line["steps"]],
            "reservation": hold, "link": link(f"/docs/{d.id}"), "_first_approver": line["steps"][0]["approver"]}


def drafts_box(ctx: Ctx) -> list[dict]:
    with db.session() as s:
        q = select(Document).where(Document.drafter == ctx.emp_no, Document.status.in_(("INVALID", "VALID", "CONFIRMED"))).order_by(Document.id.desc())
        return [doc_view(s, d) for d in s.scalars(q)]


def doc_detail(ctx: Ctx, doc_id: str) -> dict:
    with db.session() as s:
        d = s.get(Document, doc_id)
        if not d:
            raise ServiceError("NOT_FOUND")
        in_line = s.scalars(select(ApprovalStep).where(ApprovalStep.doc_id == d.id, ApprovalStep.approver == ctx.emp_no)).first()
        if d.drafter != ctx.emp_no and not in_line:
            raise ServiceError("FORBIDDEN", "기안자나 결재선에 있는 사람만 볼 수 있습니다.")
        v = doc_view(s, d)
        own = d.drafter == ctx.emp_no
        v["is_own"] = own
        v["can_confirm"] = own and d.status == "VALID" and not v["missing_reasons"]
        v["can_edit"] = own and d.status in DRAFT_STATES
        v["can_cancel"] = own and d.status in DRAFT_STATES
        # 화면에서 쓴 문서는 화면에서 상신한다. AI가 쓴 문서의 상신(확인 ③)은 대화에서 한다(설계서 4장).
        v["can_submit"] = own and d.status == "CONFIRMED" and d.confirmed_version == d.version and d.created_via == "WEB"
        v["plan_status"] = (s.get(Plan, d.plan_id).status if d.plan_id and s.get(Plan, d.plan_id) else None)
        if d.status == "REJECTED":
            again = s.scalars(select(Document).where(Document.original_doc_id == d.id, Document.status != "CANCELED")
                              .order_by(Document.id.desc())).first()
            v["redraft"] = doc_view(s, again, full=False) if again else None
            v["can_redraft"] = own and not again
        if d.status == "SUBMITTED":
            st = review.current_step(s, d)
            v["can_decide"] = bool(st and st.approver == ctx.emp_no)
            v["current_step"] = ({"seq": st.seq, "kind": st.kind, "level": st.level, "name": _name(s, st.approver),
                                  "delay_reason": st.delay_reason, "delay_until": st.delay_until, "delay_at": st.delay_at}
                                 if st else None)
            lvl, why = review.importance(s, d, clock.now(s))
            v["importance"], v["importance_reasons"] = lvl, why
        v["cautions"] = review.cautions(s, d) if d.status in ("SUBMITTED", "APPROVED", "REJECTED") else []
        if d.original_doc_id:
            o = s.get(Document, d.original_doc_id)
            v["original"] = doc_view(s, o, full=True) if o else None
        if d.process_id == "MTG-01" and d.status in DRAFT_STATES:
            from . import duel
            v["conflicts"] = duel.conflicts_for(s, d, ctx.emp_no)
            v["duel_enabled"] = duel.enabled(s)
        return v


# ───────────────────────── 화면에서 직접 기안 (AI 없이) ─────────────────────────
def case_list(ctx: Ctx) -> dict:
    """'새 기안' 화면: 프로세스별 빈 양식과 조항별 사례."""
    with db.session() as s:
        me = _emp(s, ctx.emp_no)
        byp = cases.by_process(s)
        arts = {a.id: a.title for a in regulations.articles(s)}
        procs = []
        for pid, p in regulations.processes(s).items():
            sp = regulations.spec(s, pid)
            line = approval_line.compute(s, p, me, (p.get("example") or {}).get("values"))
            procs.append({"process_id": pid, "name": p["name"], "when": p["when"],
                          "articles": [{"id": a, "title": arts.get(a, "")} for a in p["articles"]],
                          "bands": [b["label"] + (" + " + ", ".join(b["agreement"]) + " 합의" if b["agreement"] else "")
                                    for b in sp["approval"]["bands"]],
                          "agreement_if": [c["label"] for c in sp["approval"]["agreement_if"]],
                          "my_line_example": [f"{x['name']} {x['level'] if x['kind'] == '결재' else '합의'}" for x in line["steps"]],
                          "cases": [{k: v for k, v in c.items() if k != "data"} | {"user_label": _name(s, c["user"]) if c["user"] else None}
                                    for c in byp.get(pid, [])]})
        return {"me": {"emp_no": me.emp_no, "label": _name(s, me.emp_no), "grade": me.grade}, "processes": procs,
                "today": clock.iso(clock.now(s))}


def draft_form(ctx: Ctx, *, process_id: str | None = None, doc_id: str | None = None, case_id: str | None = None) -> dict:
    """작성 양식에 채울 값. doc_id면 그 초안의 값, case_id면 사례 값, 둘 다 없으면 빈 양식."""
    with db.session() as s:
        d = None
        if doc_id:
            d = _own_doc(s, ctx, doc_id)
            if d.status not in DRAFT_STATES:
                raise ServiceError("ALREADY_SUBMITTED", "상신 전 초안만 고칠 수 있습니다.")
            process_id = d.process_id
        sp = regulations.spec(s, process_id or "")
        case = cases.get(s, case_id) if case_id else None
        if case and case["process_id"] != sp["process_id"]:
            case = None
        if d:
            data = {"values": dict(d.values or {}), "evidence": list(d.evidence or []), "reasons": dict(d.reasons or {}),
                    "memo": d.memo or ""}
            vstate = d.validation or {}
            keys = set(data["reasons"]) | set(vstate.get("missing_reasons", [])) | {
                x["reason_key"] for x in vstate.get("errors", []) + vstate.get("warnings", []) if x.get("reason_key")}
        elif case:
            data = {**case["data"], "memo": ""}
            keys = set(data.get("reasons") or {})
        else:
            data = {"values": {}, "evidence": [], "reasons": {}, "memo": ""}
            keys = set()
        com = regulations.common(s)
        cats = (sp.get("evidence") or {}).get("categories") or com.get("evidence_categories", [])
        case_view = ({k: v for k, v in case.items() if k != "data"} | {"user_label": _name(s, case["user"]) if case["user"] else None}
                     if case else None)
        return {"spec": sp, "doc": doc_view(s, d) if d else None, "case": case_view,
                "data": data, "reason_keys": sorted(keys), "categories": cats,
                "has_evidence": bool((sp.get("evidence") or {}).get("label")) and (sp.get("evidence") or {}).get("label") != "없음",
                "has_nights": "lodging_per_night" in (sp.get("limits") or {}),
                "confidence_threshold": com.get("confidence_threshold", 0.8), "today": clock.now(s).date().isoformat(),
                "me": _name(s, ctx.emp_no)}


def update_reasons_ui(ctx: Ctx, doc_id: str, reasons: dict) -> dict:
    """초안 화면에서 사유만 채운다(사람이 정해야 하는 값). 나머지 값은 그대로 두고 다시 검증한다."""
    with db.session() as s:
        d = _own_doc(s, ctx, doc_id)
        merged = {**(d.reasons or {}), **{k: v.strip() for k, v in (reasons or {}).items() if str(v or "").strip()}}
        dr = {"values": dict(d.values or {}), "reasons": merged, "memo": d.memo or "",
              "evidence": [{k: v for k, v in e.items() if k != "seq"} for e in d.evidence or []]}
    return save_draft_ui(ctx, dr, doc_id=doc_id)


def save_draft_ui(ctx: Ctx, dr: dict, *, process_id: str | None = None, doc_id: str | None = None) -> dict:
    """화면의 작성 양식으로 초안을 새로 쓰거나(doc_id 없음) 고친다. AI가 쓴 초안과 같은 검증을 거친다."""
    with db.session() as s:
        drafter = _emp(s, ctx.emp_no)
        now = clock.now(s)
        existing = None
        if doc_id:
            existing = _own_doc(s, ctx, doc_id)
            if existing.status not in DRAFT_STATES:
                raise ServiceError("ALREADY_SUBMITTED", "상신 전 초안만 고칠 수 있습니다.",
                                   fix_hint="반려된 문서는 [고쳐서 다시 기안]으로 새 문서를 만드세요." if existing.status == "REJECTED" else "")
            p = regulations.get(s, existing.process_id)
        else:
            p = regulations.get(s, process_id or "")
        d, res, note = _write_doc(s, ctx, drafter, p, dr, now, existing=existing)
        audit(s, ctx, "save_draft", d.id, f"화면에서 {'고침' if existing else '작성'} v{d.version} → {STATUS_LABEL[d.status]}"
              + (f" (오류 {len(res['errors'])})" if res["errors"] else "")
              + (f" · {note}" if note else ""))
        return _save_result(s, p, drafter, d, res, note)


def redraft_ui(ctx: Ctx, doc_id: str) -> dict:
    """반려된 문서를 원본으로 잇는 새 초안을 만든다(값·증빙·사유를 그대로 옮긴다). 이미 있으면 그것을 돌려준다."""
    with db.session() as s:
        o = _own_doc(s, ctx, doc_id)
        if o.status != "REJECTED":
            raise ServiceError("BAD_REQUEST", "반려된 문서만 다시 기안할 수 있습니다.")
        again = s.scalars(select(Document).where(Document.original_doc_id == o.id, Document.status != "CANCELED")
                          .order_by(Document.id.desc())).first()
        if again:
            return {"doc_id": again.id, "status": again.status, "existing": True}
        p = regulations.get(s, o.process_id)
        dr = {"values": dict(o.values or {}), "reasons": dict(o.reasons or {}), "memo": o.memo or "",
              "evidence": [{k: v for k, v in e.items() if k != "seq"} for e in o.evidence or []]}
        d, res, _ = _write_doc(s, ctx, _emp(s, ctx.emp_no), p, dr, clock.now(s), original=o,
                               plan_id=o.plan_id, item_no=o.item_no)
        audit(s, ctx, "redraft", d.id, f"반려된 {o.doc_no}을 원본으로 잇는 새 초안 (화면) → {STATUS_LABEL[d.status]}")
        return {"doc_id": d.id, "status": d.status, "existing": False}


def submit_draft_ui(ctx: Ctx, doc_id: str) -> dict:
    """화면에서 쓴 초안의 상신(확인 ③). AI가 쓴 초안은 대화에서 submit_drafts로 올린다."""
    with db.session() as s:
        d = _own_doc(s, ctx, doc_id)
        now = clock.now(s)
        try:
            if d.created_via != "WEB":
                raise ServiceError("FORBIDDEN", "AI가 쓴 초안은 대화에서 AI에게 올려 달라고 하면 상신됩니다(확인 ③).")
            r = _submit_one(s, ctx, d, now)
        except ServiceError as e:
            audit_web_failure(ctx, "submit_draft", doc_id, e)
            raise
        first = r.pop("_first_approver")
        s.flush()
        notified = notify.check_urgent(s, {first}, now, "상신")
        return {**r, "notified": [{"recipient": _name(s, n.recipient), "kind": n.kind} for n in notified]}


def cancel_draft_ui(ctx: Ctx, doc_id: str, reason: str = "") -> dict:
    with db.session() as s:
        d = _own_doc(s, ctx, doc_id)
        if d.status not in DRAFT_STATES:
            raise ServiceError("ALREADY_SUBMITTED", "상신 전 초안만 취소할 수 있습니다.")
        now = clock.now(s)
        d.status, d.updated_at, d.updated_via = "CANCELED", clock.iso(now), ctx.channel
        from . import duel
        duel.release_for_doc(s, d, "초안을 취소해 가위바위보 승리 가예약을 풂")
        audit(s, ctx, "cancel_draft", d.id, "초안 취소 (화면)" + (f": {reason}" if reason else ""))
        return doc_view(s, d, full=False)


def notify_delay(ctx: Ctx, doc_id: str, reason: str, until: str | None = None) -> dict:
    """제4조: 처리 기한 안에 처리할 수 없으면 결재자가 기안자에게 사유를 알린다. 웹 화면에서만 한다."""
    if ctx.channel != "WEB":
        raise ServiceError("FORBIDDEN", "처리 지연 알림은 웹 화면에서만 보낼 수 있습니다.")
    if not (reason or "").strip():
        raise ServiceError("BAD_REQUEST", "지연 사유를 적어 주세요.")
    with db.session() as s:
        d = s.get(Document, doc_id)
        if not d or d.status != "SUBMITTED":
            raise ServiceError("NOT_FOUND", "결재대기 문서가 아닙니다.")
        st = review.current_step(s, d)
        if not st or st.approver != ctx.emp_no:
            raise ServiceError("NOT_IN_APPROVAL_LINE", "지금 결재할 차례인 결재자만 지연 사유를 알릴 수 있습니다.")
        u = None
        if until:
            try:
                u = validation._to_date(until)
            except (TypeError, ValueError):
                raise ServiceError("BAD_REQUEST", "처리 예정일은 YYYY-MM-DD로 적어 주세요.")
        now = clock.now(s)
        st.delay_reason, st.delay_until, st.delay_at = reason.strip(), u, clock.iso(now)
        notify.notify_drafter(s, d, "DELAY", f"{_name(s, ctx.emp_no)} · 처리 지연 사유 (제4조)", now,
                              comment=reason.strip(), until=u)
        audit(s, ctx, "delay_notice", d.id, f"{d.doc_no} 처리 지연 사유를 기안자에게 알림: {reason.strip()}"
              + (f" (처리 예정 {u})" if u else ""))
        return doc_view(s, d)


# ───────────────────────── 상태 조회 ─────────────────────────
def get_status(ctx: Ctx, plan_id: str | None = None, doc_id: str | None = None) -> dict:
    with db.session() as s:
        if plan_id:
            p = s.get(Plan, plan_id)
            if not p:
                raise ServiceError("NOT_FOUND", f"작업 계획을 찾을 수 없습니다: {plan_id}")
            if p.drafter != ctx.emp_no:
                raise ServiceError("FORBIDDEN", "본인의 작업 계획만 볼 수 있습니다.")
            v = _plan_view(s, p)
            v["documents"] = [doc_view(s, d) for d in s.scalars(select(Document).where(Document.plan_id == p.id).order_by(Document.id))]
            return v
        if doc_id:
            d = s.get(Document, doc_id) or s.scalars(select(Document).where(Document.doc_no == doc_id)).first()
            if not d:
                raise ServiceError("NOT_FOUND", f"문서를 찾을 수 없습니다: {doc_id}")
            in_line = s.scalars(select(ApprovalStep).where(ApprovalStep.doc_id == d.id, ApprovalStep.approver == ctx.emp_no)).first()
            if d.drafter != ctx.emp_no and not in_line:
                raise ServiceError("NOT_IN_APPROVAL_LINE" if ctx.mode == "approver" else "FORBIDDEN")
            return doc_view(s, d)
        mine = s.scalars(select(Plan).where(Plan.drafter == ctx.emp_no).order_by(Plan.id.desc()).limit(5))
        return {"recent_plans": [_plan_view(s, p) for p in mine]}


# ───────────────────────── 결재자 ─────────────────────────
def list_pending_approvals(ctx: Ctx, sort: str = "importance", only: str | None = None) -> dict:
    _require_mode(ctx, "approver")
    with db.session() as s:
        now = clock.now(s)
        rows = notify.briefing(s, ctx.emp_no, now)
        if only:
            rows = [r for r in rows if r["importance"] == only]
        if sort == "submitted":
            rows.sort(key=lambda r: r["submitted_at"] or "")
        for r in rows:
            r["link"] = link(f"/review/{r['doc_id']}")
        counts = {k: sum(1 for r in rows if r["importance"] == k) for k in ("긴급", "보통", "낮음")}
        return {"approver": _name(s, ctx.emp_no), "now": clock.iso(now), "counts": counts, "documents": rows,
                "link": link("/inbox"),
                "note": "승인·반려는 웹 화면의 결재 검토에서만 할 수 있습니다. AI 의견은 서버 판정과 구분해 전달하세요."}


def get_review_brief(ctx: Ctx, doc_id: str) -> dict:
    _require_mode(ctx, "approver")
    with db.session() as s:
        d = s.get(Document, doc_id) or s.scalars(select(Document).where(Document.doc_no == doc_id)).first()
        if not d:
            raise ServiceError("NOT_FOUND", f"문서를 찾을 수 없습니다: {doc_id}")
        if not s.scalars(select(ApprovalStep).where(ApprovalStep.doc_id == d.id, ApprovalStep.approver == ctx.emp_no)).first():
            raise ServiceError("NOT_IN_APPROVAL_LINE")
        now = clock.now(s)
        lvl, why = review.importance(s, d, now) if d.status == "SUBMITTED" else (None, [])
        p = regulations.get(s, d.process_id)
        arts = {a.id: a for a in s.scalars(select(Article))}
        cs = review.cautions(s, d)
        for c in cs:
            if c.get("article") in arts:
                c["article_text"] = f"{c['article']}({arts[c['article']].title}): {arts[c['article']].body}"
        v = doc_view(s, d)
        return {"doc_id": d.id, "doc_no": d.doc_no, "title": v["title"], "process_name": p["name"], "drafter": v["drafter"],
                "status_label": v["status_label"], "importance": lvl, "importance_reasons": why,
                "due": v.get("due"),
                "summary": {"values": d.values, "evidence": d.evidence, "reasons": d.reasons},
                "cautions": cs, "approval_line": v["approval_line"],
                "resubmit_of": d.original_doc_id, "link": link(f"/review/{d.id}"),
                "note": "주의 항목은 서버 판정입니다. 승인·반려는 결재 검토 화면에서 결재자가 합니다."}


def decide(ctx: Ctx, doc_id: str, decision: str, comment: str = "") -> dict:
    """승인·반려. 웹 화면에서만 부를 수 있다(설계서 3.2)."""
    if ctx.channel != "WEB":
        raise ServiceError("FORBIDDEN", "승인·반려는 웹 화면에서만 할 수 있습니다.")
    with db.session() as s:
        d = s.get(Document, doc_id)
        if not d or d.status != "SUBMITTED":
            raise ServiceError("NOT_FOUND", "결재대기 문서가 아닙니다.")
        st = review.current_step(s, d)
        if not st or st.approver != ctx.emp_no:
            raise ServiceError("NOT_IN_APPROVAL_LINE", "지금 결재할 차례가 아닙니다.")
        now = clock.now(s)
        st.acted_at, st.comment = clock.iso(now), comment or None
        res = s.scalars(select(Reservation).where(Reservation.doc_id == d.id)).first()
        nxt_approver = None
        if decision not in ("approve", "reject"):
            raise ServiceError("BAD_REQUEST", "결정은 approve 또는 reject입니다.")
        if decision == "reject":
            if not comment.strip():
                raise ServiceError("BAD_REQUEST", "반려 사유를 적어 주세요." if st.kind != "합의" else "합의하지 않는 사유를 적어 주세요.")
            st.status = "REJECTED"
            for w in s.scalars(select(ApprovalStep).where(ApprovalStep.doc_id == d.id, ApprovalStep.status == "WAITING")):
                w.status = "SKIPPED"
            d.status, d.final_at, d.reject_comment = "REJECTED", clock.iso(now), comment
            if res and res.status != "RELEASED":
                res.status = "RELEASED"
            audit(s, ctx, "reject", d.id, f"{d.doc_no} 반려: {comment}" + (" / 가예약 해제" if res else ""))
            notify.notify_drafter(s, d, "RESULT", f"{_name(s, ctx.emp_no)} · {'합의 거부' if st.kind == '합의' else '반려'}",
                                  now, comment=comment)
        else:
            st.status = "APPROVED"
            nxt = s.scalars(select(ApprovalStep).where(ApprovalStep.doc_id == d.id, ApprovalStep.status == "WAITING")
                            .order_by(ApprovalStep.seq)).first()
            if nxt:
                nxt.status, nxt.started_at = "PENDING", clock.iso(now)
                nxt_approver = nxt.approver
                audit(s, ctx, "approve_step", d.id, f"{d.doc_no} {st.level} {'합의' if st.kind == '합의' else '승인'} → {_name(s, nxt.approver)}")
            else:
                d.status, d.final_at = "APPROVED", clock.iso(now)
                if res and res.status == "HOLD":
                    res.status = "CONFIRMED"
                audit(s, ctx, "approve", d.id, f"{d.doc_no} 최종 승인" + (" / 회의실 확정" if res else ""))
                notify.notify_drafter(s, d, "RESULT", f"{_name(s, ctx.emp_no)} · 최종 승인" + (" · 회의실 확정" if res else ""),
                                      now, comment=comment or None)
        s.flush()
        if nxt_approver:
            notify.check_urgent(s, {nxt_approver}, now, "앞 단계 승인")
        return doc_view(s, d)


def inbox(ctx: Ctx) -> dict:
    with db.session() as s:
        now = clock.now(s)
        mine = list(s.scalars(select(Document).where(Document.drafter == ctx.emp_no, Document.status.in_(("SUBMITTED", "APPROVED", "REJECTED")),
                                                     Document.is_history.is_(False)).order_by(Document.submitted_at.desc())))
        recent_hist = list(s.scalars(select(Document).where(Document.drafter == ctx.emp_no, Document.is_history.is_(True))
                                     .order_by(Document.submitted_at.desc()).limit(5)))
        brief = notify.briefing(s, ctx.emp_no, now)
        return {"sent": [doc_view(s, d) for d in mine], "history": [doc_view(s, d, full=False) for d in recent_hist],
                "briefing": brief, "counts": {k: sum(1 for r in brief if r["importance"] == k) for k in ("긴급", "보통", "낮음")}}


# ───────────────────────── 알림 ─────────────────────────
def notifications(ctx: Ctx, limit: int = 20) -> dict:
    with db.session() as s:
        rows = list(s.scalars(select(Notification).where(Notification.recipient == ctx.emp_no).order_by(Notification.id.desc()).limit(limit)))
        unread = s.scalar(select(func.count()).select_from(Notification).where(Notification.recipient == ctx.emp_no, Notification.read.is_(False)))
        return {"unread": unread or 0, "items": [{"id": n.id, "kind": n.kind, "kind_label": NOTE_LABEL.get(n.kind, n.kind),
                                                  "for_drafter": n.kind in ("RESULT", "DELAY"),
                                                  "created_at": n.created_at, "trigger": n.trigger, "docs": n.docs, "read": n.read} for n in rows]}


def mark_notifications_read(ctx: Ctx, nid: int | None = None) -> None:
    with db.session() as s:
        q = select(Notification).where(Notification.recipient == ctx.emp_no, Notification.read.is_(False))
        if nid:
            q = q.where(Notification.id == nid)
        for n in s.scalars(q):
            n.read = True


# ───────────────────────── 화면용 조회 ─────────────────────────
SWITCH_DRAFTERS = ["E3001", "E3002", "E3003", "E4017"]           # 김지훈 선임(시연), 정하늘 책임, 오세준 사원, 송서준 선임(경영지원팀)
SWITCH_APPROVERS = ["E2001", "E1101", "E1001", "E2004", "E1102", "E1002", "E2002", "E2003"]


def employees_for_switch() -> list[dict]:
    """화면 상단 사용자 전환 목록. 기안자 묶음과 결재자(직책자) 묶음. 직책자도 화면에서 기안할 수 있다."""
    with db.session() as s:
        out = []
        for group, people in (("기안자", SWITCH_DRAFTERS), ("결재자", SWITCH_APPROVERS)):
            for e in people:
                emp = s.get(Employee, e)
                if not emp:
                    continue
                dept = s.get(Department, emp.dept)
                out.append({"emp_no": e, "label": _name(s, e), "group": group, "dept": dept.name if dept else emp.dept,
                            "role": "approver" if emp.title in APPROVER_TITLES else "drafter", "title": emp.title})
        return out


def rooms_schedule(start: str | None = None, days: int = 7) -> dict:
    with db.session() as s:
        today = clock.now(s).date()
        d0 = dt.date.fromisoformat(start) if start else today
        dates = [(d0 + dt.timedelta(days=i)).isoformat() for i in range(days)]
        rooms = list(s.scalars(select(Room).order_by(Room.id)))
        res = list(s.scalars(select(Reservation).where(Reservation.date.in_(dates), Reservation.status != "RELEASED")
                             .order_by(Reservation.date, Reservation.start)))
        released = list(s.scalars(select(Reservation).where(Reservation.date.in_(dates), Reservation.status == "RELEASED")))
        def row(r):
            doc = s.get(Document, r.doc_id) if r.doc_id else None
            return {"room": r.room_id, "date": r.date, "start": r.start, "end": r.end, "title": r.title, "note": r.note,
                    "who": _name(s, r.emp_no), "status": r.status, "status_label": RES_LABEL[r.status],
                    "doc": {"doc_id": doc.id, "doc_no": doc.doc_no} if doc else None}
        hol = clock.holidays(s)
        wd = "월화수목금토일"
        return {"today": today.isoformat(), "dates": dates,
                "offdays": [x for x in dates if not clock.is_bday(dt.date.fromisoformat(x), hol)],
                "weekday": {x: wd[dt.date.fromisoformat(x).weekday()] for x in dates}, "rooms": [{"id": r.id, "name": r.name, "capacity": r.capacity, "kind": r.kind} for r in rooms],
                "reservations": [row(r) for r in res], "released": [row(r) for r in released]}


def rules_page() -> dict:
    with db.session() as s:
        procs = regulations.processes(s)
        allc = {c["id"]: c for c in cases.presets(s)}

        def feats(aid):
            return [{"text": f["text"], "how": f["how"],
                     "cases": [{"id": cid, "label": allc[cid]["label"], "process_id": allc[cid]["process_id"],
                                "user": allc[cid]["user"], "user_label": _name(s, allc[cid]["user"]) if allc[cid]["user"] else None}
                               for cid in f["cases"] if cid in allc]}
                    for f in cases.ARTICLE_FEATURES.get(aid, [])]
        return {"articles": [{"id": a.id, "title": a.title, "body": a.body, "features": feats(a.id),
                              "processes": [pid for pid, p in procs.items() if a.id in p["articles"]]} for a in regulations.articles(s)],
                "processes": [regulations.spec(s, pid) for pid in procs]}


def audit_list(limit: int = 200) -> list[dict]:
    with db.session() as s:
        rows = s.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
        return [{"id": r.id, "at": r.at, "real_at": r.real_at, "channel": r.channel,
                 "who": "AI(MCP)" if r.channel == "MCP" else ("시스템" if r.channel == "SYSTEM" else "사람(화면)"),
                 "actor": _name(s, r.actor) if r.actor != "SYSTEM" else "시스템", "action": r.action, "target": r.target,
                 "summary": r.summary, "result": r.result} for r in rows]


def home_dashboard() -> dict:
    with db.session() as s:
        return dashboard.build(s)


# ───────────────────────── 시연 운영 ─────────────────────────
def demo_state() -> dict:
    from . import duel
    with db.session() as s:
        return {"now": clock.iso(clock.now(s)), "confirm_mode": confirm_mode(s), "duel_on": duel.enabled(s),
                "start": (clock._state(s, "demo_start", {}) or {}).get("start"),
                "digest": clock._state(s, "digest", {}), "clock": clock._state(s, "clock", {})}


def demo_set_clock(ctx: Ctx, when: str) -> dict:
    with db.session() as s:
        t = dt.datetime.fromisoformat(when)
        clock.set_clock(s, t)
        st = clock._state(s, "digest", {}) or {}
        clock.set_state(s, "digest", {**st, "last_check": clock.iso(t)})
        audit(s, ctx, "demo_set_clock", "", f"시연 시계 → {when}")
    return demo_state()


def demo_advance(ctx: Ctx, minutes: int) -> dict:
    with db.session() as s:
        t = clock.advance(s, minutes)
        audit(s, ctx, "demo_advance", "", f"시연 시계 {minutes:+}분 → {clock.iso(t)}")
        notify.tick(s)
    return demo_state()


def demo_send_digest(ctx: Ctx) -> int:
    with db.session() as s:
        made = notify.send_digest(s, clock.now(s), "정기 알림(시연 제어 패널에서 바로 보냄)")
        audit(s, ctx, "demo_send_digest", "", f"정기 알림 {len(made)}건")
        return len(made)


def demo_set_confirm_mode(ctx: Ctx, mode: str) -> dict:
    if mode not in ("A", "B"):
        raise ServiceError("BAD_REQUEST", "확인 방식은 A 또는 B입니다.")
    with db.session() as s:
        clock.set_state(s, "confirm_mode", {"mode": mode})
        audit(s, ctx, "demo_confirm_mode", "", f"확인 방식 → {mode}")
    return demo_state()


def run_tick() -> int:
    with db.session() as s:
        return len(notify.tick(s))
