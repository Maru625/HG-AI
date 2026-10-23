"""결재선 계산 (설계서 7.5). 결재선은 서버가 계산하고 근거 조항을 함께 돌려준다."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import regulations
from .models import Department, Employee, Room

LEVELS = ["팀장", "실장", "본부장"]
LEVEL_TYPE = {"팀장": "team", "실장": "office", "본부장": "division"}


def amount_of(process: dict, values: dict | None):
    f = process.get("amount_field")
    if not f or not values:
        return None
    try:
        return int(values.get(f)) if values.get(f) is not None else None
    except (TypeError, ValueError):
        return None


def band_for(process: dict, amount) -> dict:
    bands = process["approval"]["bands"]
    if amount is None:
        return bands[0] if len(bands) == 1 else bands[0]
    for b in bands:
        if b["max"] is None or amount <= b["max"]:
            return b
    return bands[-1]


def _chain(s: Session, dept_code: str) -> dict[str, Department]:
    """부서에서 위로 올라가며 팀/실/본부를 찾는다."""
    out: dict[str, Department] = {}
    d = s.get(Department, dept_code)
    while d:
        out.setdefault(d.type, d)
        d = s.get(Department, d.parent) if d.parent else None
    return out


def agreement_conditions(s: Session, process: dict, values: dict | None) -> list[dict]:
    """MTG-01의 합의 조건(대회의실, 외부인, 업무시간 외)."""
    conds = process["approval"].get("agreement_if", [])
    if not conds or not values:
        return []
    bh = regulations.common(s).get("business_hours", {"start": "09:00", "end": "18:00"})
    hit = []
    for c in conds:
        cond = c["condition"]
        if cond == "large_room":
            room = s.get(Room, str(values.get("room", "")))
            ok = bool(room and room.kind == "large")
        elif cond == "external":
            ok = values.get("external") in (True, "true", "True", 1, "예")
        elif cond == "outside_hours":
            st, en = str(values.get("start", "")), str(values.get("end", ""))
            ok = bool(st and en and (st < bh["start"] or en > bh["end"]))
        else:
            ok = False
        if ok:
            hit.append(c)
    return hit


def compute(s: Session, process: dict, drafter: Employee, values: dict | None = None, dept: str | None = None) -> dict:
    amount = amount_of(process, values)
    band = band_for(process, amount)
    chain = _chain(s, dept or drafter.dept)
    need = LEVELS[: LEVELS.index(band["level"]) + 1]
    article = process["articles"][0]
    steps, seen = [], {drafter.emp_no}
    for level in need:
        d = chain.get(LEVEL_TYPE[level])
        if not d or not d.head or d.head in seen:
            continue
        seen.add(d.head)
        e = s.get(Employee, d.head)
        steps.append({"approver": d.head, "name": e.name if e else d.head, "title": e.title if e else "",
                      "kind": "결재", "level": level, "dept": d.name, "article": article,
                      "basis": f"{band_label(band)} → {band['level']}까지 결재 ({article}, 제3조)"})
    agree_depts = list(band.get("agreement", []))
    reasons = {}
    for c in agreement_conditions(s, process, values):
        agree_depts.append(c["dept"])
        reasons.setdefault(c["dept"], []).append(c["label"])
    for name in dict.fromkeys(agree_depts):
        d = s.scalars(select(Department).where(Department.name == name)).first()
        if not d or not d.head or d.head in seen:
            continue
        seen.add(d.head)
        e = s.get(Employee, d.head)
        why = ", ".join(reasons.get(name, [])) or band_label(band)
        steps.append({"approver": d.head, "name": e.name if e else d.head, "title": e.title if e else "",
                      "kind": "합의", "level": "합의", "dept": d.name, "article": article,
                      "basis": f"{why} → {d.name} 합의 ({article}, 제3조)"})
    for i, st in enumerate(steps, 1):
        st["seq"] = i
    return {"process_id": process["id"], "amount": amount, "band": band_label(band) + f" → {band['level']}",
            "steps": steps, "articles": [article, "제3조"]}


def band_label(band: dict) -> str:
    return "금액 제한 없음" if band["max"] is None else f"{band['max']:,}원 이하"
