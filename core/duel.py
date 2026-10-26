"""회의실 가위바위보 대결 (재미 기능, 규정 밖).

겹치는 시간에 회의실을 신청한 사람(도전자)이 그 시간의 기존 예약자(수비자)에게 도전한다.
두 사람이 각자 자기 화면에서 고르고, 이긴 쪽이 자리를 갖는다.

- 도전자가 이기면: 수비자 예약은 해제(note에 사유)하고, 수비자의 신청이 결재대기였으면 그 신청은 취소한다.
  다른 겹침이 남지 않으면 도전자 초안에 '승리 가예약'을 바로 잡고 초안을 다시 검증한다(작성완료로 바뀜).
  상신하면 그 가예약을 그대로 쓴다. 초안의 날짜·시간·회의실을 고치거나 초안을 취소하면 가예약은 풀린다.
- 수비자가 이기면: 예약은 그대로, 도전자 초안은 ROOM_CONFLICT 그대로. 같은 초안으로 같은 예약에 다시 도전할 수 없다.
- 비기면 같은 대결에서 한 판 더 한다.
- 수비자는 [양보], 도전자는 [도전 취소]를 할 수 있다.
- 시연 제어 패널에서 끌 수 있다(demo_state.duel_mode). 웹 화면에서만 한다(AI 연결로는 열지 않는다).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import clock, db, notify, regulations, validation
from .errors import ServiceError
from .models import ApprovalStep, Document, Duel, Employee, Reservation, Room

PICKS = {"rock": "바위", "scissors": "가위", "paper": "보"}
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
DRAFT_STATES = ("INVALID", "VALID", "CONFIRMED")
WIN_NOTE = "가위바위보 승리 · 상신 전 가예약"


def _svc():
    from . import services  # 순환 참조 방지
    return services


def enabled(s: Session) -> bool:
    return bool((clock._state(s, "duel_mode", {"on": True}) or {}).get("on", True))


def set_enabled(ctx, on: bool) -> dict:
    S = _svc()
    with db.session() as s:
        clock.set_state(s, "duel_mode", {"on": bool(on)})
        S.audit(s, ctx, "demo_duel_mode", "", f"회의실 가위바위보 {'켬' if on else '끔'}")
    return {"on": bool(on)}


# ───────── 조회 ─────────
def _latest(s: Session, doc_id: str, res_id: int) -> Duel | None:
    return s.scalars(select(Duel).where(Duel.challenger_doc == doc_id, Duel.defender_res == res_id)
                     .order_by(Duel.id.desc())).first()


def conflicts_for(s: Session, d: Document, me: str) -> list[dict]:
    """회의실 신청 초안이 겹치는 예약과 도전할 수 있는지. 초안 화면이 쓴다."""
    S = _svc()
    v = d.values or {}
    room, date, st, en = v.get("room"), v.get("date"), v.get("start"), v.get("end")
    if d.process_id != "MTG-01" or not all(isinstance(x, str) and x for x in (room, date, st, en)):
        return []
    on = enabled(s)
    other_errors = [e for e in (d.validation or {}).get("errors", []) if e.get("code") != "ROOM_CONFLICT"]
    out = []
    for r in validation.conflicts(s, room, date, st, en, d.id):
        last = _latest(s, d.id, r.id)
        busy = s.scalars(select(Duel).where(Duel.defender_res == r.id, Duel.status == "WAITING")).first()
        why = None
        if not on:
            why = "가위바위보 대결이 꺼져 있습니다."
        elif d.drafter != me:
            why = "본인 초안에서만 도전할 수 있습니다."
        elif r.emp_no == me:
            why = "내 예약과는 대결할 수 없습니다."
        elif d.status not in DRAFT_STATES:
            why = "상신 전 초안에서만 도전할 수 있습니다."
        elif other_errors:
            why = "회의실 겹침 말고도 오류가 있어 먼저 고쳐야 합니다."
        elif last and last.status == "DONE" and last.winner == "defender":
            why = "이 예약에는 이미 도전해서 졌습니다."
        elif busy and (not last or busy.id != last.id):
            why = "다른 사람이 이 예약에 도전하는 중입니다."
        rm = s.get(Room, r.room_id)
        out.append({"res_id": r.id, "room": r.room_id, "room_name": rm.name if rm else r.room_id, "date": r.date,
                    "start": r.start, "end": r.end, "title": r.title, "holder": r.emp_no, "holder_name": S._name(s, r.emp_no),
                    "status": r.status, "status_label": S.RES_LABEL.get(r.status, r.status), "note": r.note,
                    "duel": {"id": last.id, "status": last.status, "winner": last.winner} if last else None,
                    "can_challenge": why is None and not (last and last.status == "WAITING"), "why_not": why})
    return out


def _role(du: Duel, me: str) -> str | None:
    return "challenger" if me == du.challenger else ("defender" if me == du.defender else None)


def _view(s: Session, du: Duel, me: str) -> dict:
    S = _svc()
    role = _role(du, me)
    other = "defender" if role == "challenger" else "challenger"
    opp = du.defender if role == "challenger" else du.challenger
    room = s.get(Room, du.room_id)
    res = s.get(Reservation, du.defender_res)
    mine = du.c_pick if role == "challenger" else du.d_pick
    theirs_done = bool(du.d_pick if role == "challenger" else du.c_pick)
    key_me, key_opp = ("c", "d") if role == "challenger" else ("d", "c")
    rounds = [{"round": r["round"], "me": r[key_me], "opp": r[key_opp],
               "result": "tie" if r["result"] == "tie" else ("win" if r["result"] == role else "lose")} for r in du.rounds or []]
    won = du.status == "DONE" and du.winner == role
    cd = s.get(Document, du.challenger_doc)
    hold = s.scalars(select(Reservation).where(Reservation.doc_id == du.challenger_doc, Reservation.status == "HOLD")).first()
    slot = f"{du.date[5:].replace('-', '/')} {du.start}~{du.end}"
    room_name = room.name if room else du.room_id
    text, nxt = "", None
    if du.status == "DONE":
        if role == "challenger":
            if won:
                text = (f"{S._name(s, opp)}이 양보했습니다. " if du.how == "yield" else "") + (
                    f"{room_name} {slot} 자리를 차지했습니다! 내 초안에 가예약을 잡았으니 확인 ②와 상신 ③을 이어서 하세요."
                    if hold else "이겼습니다! 다만 다른 겹침이 남아 있어 아직 가예약을 잡지 못했습니다.")
            else:
                text = f"졌습니다. {S._name(s, opp)}의 예약이 그대로 남습니다. 다른 시간이나 회의실을 찾아보세요."
            nxt = {"label": "초안으로 가기", "href": f"/drafts/{du.challenger_doc}" if cd and cd.status in DRAFT_STATES
                   else f"/docs/{du.challenger_doc}"}
        else:
            if won:
                text = f"자리를 지켰습니다! {room_name} {slot} 예약은 그대로입니다."
            else:
                ddoc = s.get(Document, du.defender_doc) if du.defender_doc else None
                extra = " 결재대기였던 신청은 취소되었습니다." if ddoc is not None and ddoc.status == "CANCELED" else ""
                text = ("양보했습니다. " if du.how == "yield" else "") + f"{room_name} {slot} 예약이 {S._name(s, opp)}에게 넘어갔습니다.{extra}"
            nxt = {"label": "회의실 현황 보기", "href": f"/rooms?start={du.date}"}
    elif du.status == "CANCELED":
        text = "도전을 취소했습니다." if role == "challenger" else f"{S._name(s, opp)}이 도전을 취소했습니다."
    return {"id": du.id, "rev": du.rev, "status": du.status, "role": role, "round": du.round,
            "me": {"emp_no": me, "name": S._name(s, me)}, "opponent": {"emp_no": opp, "name": S._name(s, opp)},
            "challenger_name": S._name(s, du.challenger), "defender_name": S._name(s, du.defender),
            "room": room_name, "slot": slot, "date": du.date, "start": du.start, "end": du.end,
            "title": res.title if res else "", "doc_title": S.doc_title(s, cd) if cd else "",
            "my_pick": mine, "opp_picked": theirs_done, "rounds": rounds,
            "winner": du.winner, "won": won, "how": du.how, "result_text": text, "next": nxt,
            "other_role": other}


def state(ctx) -> dict:
    """지금 사용자에게 보일 대결 하나: 결과를 아직 안 본 대결(먼저 끝난 것부터), 없으면 진행 중인 대결."""
    me = ctx.emp_no
    with db.session() as s:
        mine = or_(Duel.challenger == me, Duel.defender == me)
        for du in s.scalars(select(Duel).where(mine, Duel.status == "WAITING")):
            _settle(s, du)                      # 두 사람이 거의 동시에 골랐을 때 남는 판을 마저 가린다
        s.flush()
        unseen = [du for du in s.scalars(select(Duel).where(mine, Duel.status.in_(("DONE", "CANCELED")))
                                         .order_by(Duel.decided_at, Duel.id)) if me not in (du.seen or [])]
        active = list(s.scalars(select(Duel).where(mine, Duel.status == "WAITING").order_by(Duel.id.desc())))
        du = unseen[0] if unseen else (active[0] if active else None)
        return {"enabled": enabled(s), "duel": _view(s, du, me) if du else None,
                "waiting": len(active), "unseen": len(unseen)}


# ───────── 쓰기 (웹 화면에서만) ─────────
def _web_only(ctx) -> None:
    if ctx.channel != "WEB":
        raise ServiceError("FORBIDDEN", "가위바위보 대결은 웹 화면에서만 할 수 있습니다.")


def start(ctx, doc_id: str, res_id: int) -> dict:
    _web_only(ctx)
    S = _svc()
    with db.session() as s:
        if not enabled(s):
            raise ServiceError("FORBIDDEN", "가위바위보 대결이 꺼져 있습니다(시연 제어 패널에서 켤 수 있습니다).")
        d = S._own_doc(s, ctx, doc_id)
        if d.process_id != "MTG-01" or d.status not in DRAFT_STATES:
            raise ServiceError("BAD_REQUEST", "상신 전 회의실 사용 신청 초안에서만 도전할 수 있습니다.")
        cs = {c["res_id"]: c for c in conflicts_for(s, d, ctx.emp_no)}
        c = cs.get(int(res_id))
        if not c:
            raise ServiceError("BAD_REQUEST", "이 초안과 겹치는 예약이 아닙니다.")
        if c["duel"] and c["duel"]["status"] == "WAITING":
            return _view(s, s.get(Duel, c["duel"]["id"]), ctx.emp_no)
        if not c["can_challenge"]:
            raise ServiceError("BAD_REQUEST", c["why_not"] or "도전할 수 없습니다.")
        r = s.get(Reservation, int(res_id))
        now = clock.now(s)
        v = d.values
        du = Duel(room_id=v["room"], date=v["date"], start=v["start"], end=v["end"], challenger=ctx.emp_no,
                  challenger_doc=d.id, defender=r.emp_no, defender_res=r.id, defender_doc=r.doc_id, status="WAITING",
                  round=1, rounds=[], seen=[], created_at=clock.iso(now), rev=1)
        s.add(du)
        s.flush()
        S.audit(s, ctx, "duel_start", d.id, f"가위바위보 도전: {c['room_name']} {r.date} {r.start}~{r.end} '{r.title}' "
                                            f"({S._name(s, r.emp_no)}) ← {S._name(s, ctx.emp_no)}")
        return _view(s, du, ctx.emp_no)


def _get(s: Session, ctx, duel_id: int) -> tuple[Duel, str]:
    du = s.get(Duel, int(duel_id))
    if not du:
        raise ServiceError("NOT_FOUND", "대결을 찾을 수 없습니다.")
    role = _role(du, ctx.emp_no)
    if not role:
        raise ServiceError("FORBIDDEN", "이 대결의 참가자가 아닙니다.")
    return du, role


def pick(ctx, duel_id: int, choice: str) -> dict:
    _web_only(ctx)
    S = _svc()
    if choice not in PICKS:
        raise ServiceError("BAD_REQUEST", "가위·바위·보 중 하나를 고르세요.")
    with db.session() as s:
        du, role = _get(s, ctx, duel_id)
        if du.status == "WAITING":
            if role == "challenger" and not du.c_pick:
                du.c_pick = choice
            elif role == "defender" and not du.d_pick:
                du.d_pick = choice
            du.rev += 1
            S.audit(s, ctx, "duel_pick", du.challenger_doc, f"대결 #{du.id} {du.round}판 선택 완료")
            _settle(s, du)
        return _view(s, du, ctx.emp_no)


def yield_(ctx, duel_id: int) -> dict:
    _web_only(ctx)
    S = _svc()
    with db.session() as s:
        du, role = _get(s, ctx, duel_id)
        if role != "defender":
            raise ServiceError("FORBIDDEN", "양보는 기존 예약자만 할 수 있습니다.")
        if du.status == "WAITING":
            S.audit(s, ctx, "duel_yield", du.challenger_doc, f"대결 #{du.id} 양보")
            _finish(s, du, "challenger", "yield")
        return _view(s, du, ctx.emp_no)


def cancel(ctx, duel_id: int) -> dict:
    _web_only(ctx)
    S = _svc()
    with db.session() as s:
        du, role = _get(s, ctx, duel_id)
        if role != "challenger":
            raise ServiceError("FORBIDDEN", "도전 취소는 도전자만 할 수 있습니다.")
        if du.status == "WAITING":
            du.status, du.how, du.decided_at = "CANCELED", "cancel", clock.iso(clock.now(s))
            du.seen = [du.defender]            # 수비자에게는 따로 알리지 않는다
            du.rev += 1
            S.audit(s, ctx, "duel_cancel", du.challenger_doc, f"대결 #{du.id} 도전 취소")
        return _view(s, du, ctx.emp_no)


def seen(ctx, duel_id: int) -> dict:
    with db.session() as s:
        du, _ = _get(s, ctx, duel_id)
        if du.status != "WAITING" and ctx.emp_no not in (du.seen or []):
            du.seen = [*(du.seen or []), ctx.emp_no]
        return {"ok": True}


# ───────── 판정 ─────────
def _settle(s: Session, du: Duel) -> None:
    if du.status != "WAITING" or not (du.c_pick and du.d_pick):
        return
    c, d = du.c_pick, du.d_pick
    result = "tie" if c == d else ("challenger" if BEATS[c] == d else "defender")
    du.rounds = [*(du.rounds or []), {"round": du.round, "c": c, "d": d, "result": result}]
    du.c_pick = du.d_pick = None
    du.rev += 1
    if result == "tie":
        du.round += 1
        return
    _finish(s, du, result, "rps")


def _finish(s: Session, du: Duel, winner: str, how: str) -> None:
    S = _svc()
    now = clock.now(s)
    du.status, du.winner, du.how, du.decided_at = "DONE", winner, how, clock.iso(now)
    du.rev += 1
    r = s.get(Reservation, du.defender_res)
    cd = s.get(Document, du.challenger_doc)
    sysctx = S.Ctx("SYSTEM", channel="SYSTEM")
    if winner == "challenger":
        if r and r.status != "RELEASED":
            r.status = "RELEASED"
            r.note = f"가위바위보에서 {S._name(s, du.challenger)}에게 져서 해제" if how == "rps" else f"{S._name(s, du.challenger)}에게 양보해 해제"
            if r.doc_id:
                dd = s.get(Document, r.doc_id)
                if dd and dd.status == "SUBMITTED":
                    for st in s.scalars(select(ApprovalStep).where(ApprovalStep.doc_id == dd.id,
                                                                   ApprovalStep.status.in_(("WAITING", "PENDING")))):
                        st.status = "SKIPPED"
                    dd.status, dd.final_at = "CANCELED", clock.iso(now)
                    dd.reject_comment = "회의실 가위바위보에서 져서 예약이 해제되어 신청이 취소되었습니다(재미 기능)."
                    notify.notify_drafter(s, dd, "RESULT", "회의실 가위바위보 패배 · 신청 취소", now, comment=dd.reject_comment)
        s.flush()
        if cd and cd.status in DRAFT_STATES:
            v = cd.values or {}
            left = validation.conflicts(s, v.get("room"), v.get("date"), v.get("start"), v.get("end"), cd.id)
            if not left:
                mine = s.scalars(select(Reservation).where(Reservation.doc_id == cd.id)).first()
                if mine:
                    mine.room_id, mine.date, mine.start, mine.end = v["room"], v["date"], v["start"], v["end"]
                    mine.title, mine.status, mine.note = v.get("title", ""), "HOLD", WIN_NOTE
                else:
                    s.add(Reservation(room_id=v["room"], date=v["date"], start=v["start"], end=v["end"], title=v.get("title", ""),
                                      emp_no=cd.drafter, doc_id=cd.id, status="HOLD", note=WIN_NOTE))
                s.flush()
            revalidate(s, cd, now)
    else:
        if r:
            s.flush()
            n = len(list(s.scalars(select(Duel.id).where(Duel.defender_res == r.id, Duel.status == "DONE",
                                                         Duel.winner == "defender"))))
            r.note = f"가위바위보 방어 성공 {n}회"
    text = {"rps": "가위바위보", "yield": "양보"}[how]
    S.audit(s, sysctx, "duel_result", du.challenger_doc,
            f"대결 #{du.id} {text}: {'도전자 ' + S._name(s, du.challenger) if winner == 'challenger' else '수비자 ' + S._name(s, du.defender)} 승"
            + (f" ({len(du.rounds or [])}판)" if how == "rps" else ""))


def revalidate(s: Session, d: Document, now: dt.datetime) -> None:
    """값은 그대로 두고 다시 검증한다(대결 결과로 겹침이 풀렸을 때)."""
    p = regulations.get(s, d.process_id)
    drafter = s.get(Employee, d.drafter)
    res = validation.validate(s, p, drafter, d.values, d.evidence, d.reasons, now=now, doc_id=d.id,
                              original_doc_id=d.original_doc_id)
    d.validation = {k: res[k] for k in ("errors", "warnings", "missing_reasons")}
    if d.status != "CONFIRMED":
        d.status = "INVALID" if res["errors"] else "VALID"


def release_for_doc(s: Session, d: Document, why: str) -> None:
    """초안이 바뀌거나 취소되면 대결로 잡은 상신 전 가예약을 푼다."""
    for r in s.scalars(select(Reservation).where(Reservation.doc_id == d.id, Reservation.status == "HOLD")):
        if d.status in DRAFT_STATES or d.status == "CANCELED":
            r.status, r.note = "RELEASED", why
