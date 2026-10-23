"""결재 검토 지원: 결재 중요도와 주의 항목 (설계서 7.6). 날짜 판단은 시연 시계를 쓴다."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import approval_line, clock, regulations, validation
from .models import ApprovalStep, Document

LEVEL_ORDER = {"긴급": 0, "보통": 1, "낮음": 2}


def current_step(s: Session, doc: Document) -> ApprovalStep | None:
    return s.scalars(select(ApprovalStep).where(ApprovalStep.doc_id == doc.id, ApprovalStep.status == "PENDING")
                     .order_by(ApprovalStep.seq)).first()


def importance(s: Session, doc: Document, now: dt.datetime) -> tuple[str, list[str]]:
    hol = clock.holidays(s)
    today = now.date()
    process = regulations.get(s, doc.process_id)
    v = doc.values or {}
    urgent: list[str] = []
    if doc.process_id == "MTG-01" and v.get("date"):
        n = clock.bdays_until(today, dt.date.fromisoformat(v["date"]), hol)
        if n <= 3:
            urgent.append(f"회의일({v['date']})까지 {max(n, 0)}영업일")
    dl = validation.deadline_of(s, process, v)
    if dl:
        n = clock.bdays_until(today, dl, hol)
        if n <= 2:
            urgent.append(f"처리 기한({dl})까지 {n}영업일" if n >= 0 else f"처리 기한({dl}) 경과")
    st = current_step(s, doc)
    started = (st.started_at if st and st.started_at else doc.submitted_at)
    if started:
        waited = clock.bdays_until(dt.datetime.fromisoformat(started).date(), today, hol)
        if waited > 3:
            urgent.append(f"상신 후 {waited}영업일 대기")
    if urgent:
        return "긴급", urgent
    c = cautions(s, doc)
    if c:
        return "보통", [f"주의 항목 {len(c)}개"]
    return "낮음", []


def cautions(s: Session, doc: Document) -> list[dict]:
    """결재자에게 강조할 주의 항목. 서버 판정만 담는다(AI 의견은 넣지 않는다, 설계서 13.2)."""
    process = regulations.get(s, doc.process_id)
    out: list[dict] = []
    for w in (doc.validation or {}).get("warnings", []):
        if w["code"] in ("LIMIT_EXCEEDED", "HOLIDAY_USE"):
            out.append({"code": w["code"], "label": "사유를 붙여 넘어온 경고", "detail": w["message"],
                        "reason": w.get("reason"), "evidence_seq": w.get("evidence_seq"), "field": w.get("field"),
                        "article": w.get("article")})
        elif w["code"] == "LOW_CONFIDENCE":
            seq = w.get("evidence_seq")
            ev = next((e for e in doc.evidence or [] if e.get("seq") == seq), {})
            out.append({"code": "LOW_CONFIDENCE", "label": "판독 신뢰도가 낮은 값", "detail": w["message"],
                        "evidence_seq": seq, "value": {"date": ev.get("date"), "merchant": ev.get("merchant"), "amount": ev.get("amount")},
                        "article": w.get("article")})
        elif w["code"] == "DEADLINE_PASSED":
            out.append({"code": "DEADLINE_PASSED", "label": "기한 경과", "detail": w["message"], "article": w.get("article")})
    amount = approval_line.amount_of(process, doc.values)
    if amount is not None:
        band = approval_line.band_for(process, amount)
        if band["max"] and amount >= 0.9 * band["max"]:
            out.append({"code": "NEAR_LIMIT", "label": "전결 한도 근접",
                        "detail": f"{amount:,}원은 전결 기준 {band['max']:,}원의 {amount / band['max']:.0%}입니다. 분할 청구가 아닌지 확인하세요.",
                        "field": process["amount_field"], "article": process["articles"][0]})
    if doc.process_id in validation.EXPENSE:
        out += _similar_recent(s, doc)
    if doc.original_doc_id:
        orig = s.get(Document, doc.original_doc_id)
        if orig:
            changed = [k for k in sorted(set(orig.values or {}) | set(doc.values or {}))
                       if (orig.values or {}).get(k) != (doc.values or {}).get(k)]
            ev_changed = [{"date": e.get("date"), "merchant": e.get("merchant"), "amount": e.get("amount")} for e in doc.evidence or []] != \
                         [{"date": e.get("date"), "merchant": e.get("merchant"), "amount": e.get("amount")} for e in orig.evidence or []]
            out.append({"code": "RESUBMIT_CHANGES", "label": "재상신 변경점",
                        "detail": f"반려된 {orig.doc_no or orig.id}을 다시 올린 문서입니다. 바뀐 항목: "
                                  + (", ".join(changed) if changed else "없음") + (" / 증빙 변경" if ev_changed else ""),
                        "changed_fields": changed, "evidence_changed": ev_changed, "original": orig.doc_no or orig.id})
    return out


def _similar_recent(s: Session, doc: Document) -> list[dict]:
    mine = [(e.get("merchant"), e.get("date")) for e in doc.evidence or [] if e.get("merchant") and e.get("date")]
    if not mine:
        return []
    q = select(Document).where(Document.drafter == doc.drafter, Document.id != doc.id,
                               Document.status.in_(("SUBMITTED", "APPROVED")),
                               Document.process_id.in_(tuple(validation.EXPENSE)))
    hits = []
    for other in s.scalars(q):
        if other.id == doc.original_doc_id:
            continue
        for e in other.evidence or []:
            for m, d in mine:
                if e.get("merchant") == m and e.get("date") and e.get("date") != d:
                    gap = abs((dt.date.fromisoformat(e["date"]) - dt.date.fromisoformat(d)).days)
                    if gap <= 7:
                        hits.append({"code": "SIMILAR_RECENT", "label": "짧은 기간의 유사 정산",
                                     "detail": f"같은 기안자가 {gap}일 차이로 같은 사용처({m})를 정산했습니다: {other.doc_no or other.id} ({e['date']}, {e.get('amount', 0):,}원)",
                                     "other_doc": other.doc_no or other.id})
    return hits[:3]
