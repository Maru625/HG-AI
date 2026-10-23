"""알림 (설계서 7.6).

- 긴급 결재가 들어오면 즉시 알린다. 알림에는 결재할 차례인 문서 전체를 중요도순으로 묶는다.
- 긴급이 없으면 정해진 시각(기본 09:00, 14:00)에 묶어서 알린다. 대기 문서가 없으면 보내지 않는다.
- 같은 문서로 즉시 알림을 두 번 보내지 않는다. 중요도가 긴급으로 올라간 경우에만 다시 알린다.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock, regulations, review
from .models import ApprovalStep, Document, Employee, Notification, NotifyMark


def pending_docs(s: Session, approver: str) -> list[Document]:
    q = (select(Document).join(ApprovalStep, ApprovalStep.doc_id == Document.id)
         .where(ApprovalStep.approver == approver, ApprovalStep.status == "PENDING", Document.status == "SUBMITTED"))
    return list(s.scalars(q).unique())


def briefing(s: Session, approver: str, now: dt.datetime) -> list[dict]:
    from .services import doc_title  # 순환 참조 방지
    rows = []
    for d in pending_docs(s, approver):
        level, reasons = review.importance(s, d, now)
        st = review.current_step(s, d)
        started = (st.started_at if st and st.started_at else d.submitted_at) or ""
        waited = clock.bdays_until(dt.datetime.fromisoformat(started).date(), now.date(), clock.holidays(s)) if started else 0
        drafter = s.get(Employee, d.drafter)
        rows.append({"doc_id": d.id, "doc_no": d.doc_no, "process_id": d.process_id,
                     "process_name": regulations.get(s, d.process_id)["name"], "title": doc_title(s, d),
                     "drafter": {"emp_no": d.drafter, "name": drafter.name if drafter else d.drafter},
                     "amount": _amount(s, d), "submitted_at": d.submitted_at, "waiting_bdays": waited,
                     "importance": level, "importance_reasons": reasons, "caution_count": len(review.cautions(s, d)),
                     "ai_written": d.created_via == "MCP" or d.updated_via == "MCP"})
    rows.sort(key=lambda r: (review.LEVEL_ORDER[r["importance"]], -r["waiting_bdays"], r["submitted_at"] or ""))
    return rows


def _amount(s: Session, d: Document):
    p = regulations.get(s, d.process_id)
    f = p.get("amount_field")
    return (d.values or {}).get(f) if f else None


def _create(s: Session, approver: str, kind: str, rows: list[dict], trigger: str, now: dt.datetime) -> Notification:
    n = Notification(recipient=approver, kind=kind, created_at=clock.iso(now), trigger=trigger, docs=rows, read=False)
    s.add(n)
    return n


def check_urgent(s: Session, approvers: set[str], now: dt.datetime, trigger: str) -> list[Notification]:
    """새로 긴급이 된 문서가 있으면 즉시 알림을 만든다."""
    made = []
    for ap in approvers:
        rows = briefing(s, ap, now)
        new_urgent = [r for r in rows if r["importance"] == "긴급" and not s.get(NotifyMark, (ap, r["doc_id"]))]
        if not new_urgent:
            continue
        for r in new_urgent:
            s.add(NotifyMark(recipient=ap, doc_id=r["doc_id"], level="긴급"))
        titles = ", ".join(r["title"] for r in new_urgent[:3])
        made.append(_create(s, ap, "IMMEDIATE", rows, f"{trigger}: 긴급 {len(new_urgent)}건 ({titles})", now))
    return made


def all_approvers_with_pending(s: Session) -> set[str]:
    q = (select(ApprovalStep.approver).join(Document, Document.id == ApprovalStep.doc_id)
         .where(ApprovalStep.status == "PENDING", Document.status == "SUBMITTED"))
    return set(s.scalars(q))


def send_digest(s: Session, now: dt.datetime, trigger: str) -> list[Notification]:
    made = []
    for ap in sorted(all_approvers_with_pending(s)):
        rows = briefing(s, ap, now)
        if rows:
            for r in rows:
                if r["importance"] == "긴급" and not s.get(NotifyMark, (ap, r["doc_id"])):
                    s.add(NotifyMark(recipient=ap, doc_id=r["doc_id"], level="긴급"))
            made.append(_create(s, ap, "DIGEST", rows, trigger, now))
    return made


def tick(s: Session) -> list[Notification]:
    """주기 작업. 시연 시계 기준으로 정기 알림 시각을 지났는지, 새로 긴급이 된 문서가 있는지 본다."""
    now = clock.now(s)
    made = check_urgent(s, all_approvers_with_pending(s), now, "중요도 재판정")
    st = clock._state(s, "digest", {}) or {}
    last = dt.datetime.fromisoformat(st["last_check"]) if st.get("last_check") else now
    times = st.get("times", ["09:00", "14:00"])
    due = []
    day = last.date()
    while day <= now.date():
        for t in times:
            h, m = map(int, t.split(":"))
            at = dt.datetime.combine(day, dt.time(h, m))
            if last < at <= now and clock.is_bday(day, clock.holidays(s)):
                due.append(at)
        day += dt.timedelta(days=1)
    if due:
        made += send_digest(s, now, f"정기 알림 {due[-1].strftime('%H:%M')}")
    clock.set_state(s, "digest", {"times": times, "last_check": clock.iso(now)})
    return made
