"""시드 적재와 초기화 (계약 C3). P4가 만든 seed/ 폴더를 읽어 DB를 처음 상태로 만든다."""
from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

from sqlalchemy.orm import Session

from . import approval_line, clock, db, notify, regulations, validation
from .models import (ApprovalStep, Article, AuditLog, AutomationAssumption, Connection, Department, Document,
                     Employee, OvertimeRecord, Process, Reservation, Room, SurveyResponse, WorkAllocation)


def _json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _csv(p: Path) -> list[dict]:
    with p.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def reset(seed_dir: Path) -> dict:
    """DB를 비우고 시드를 적재한다."""
    if not (seed_dir / "processes.json").exists():
        raise SystemExit(f"시드 파일이 없습니다: {seed_dir}\n먼저 'uv run approval-seed build'를 실행하세요.")
    eng = db.engine()
    db.Base.metadata.drop_all(eng)
    db.Base.metadata.create_all(eng)
    with db.session() as s:
        _load_static(s, seed_dir)
    with db.session() as s:
        stats = _load_docs(s, seed_dir)
    return stats


def _load_static(s: Session, seed: Path) -> None:
    org = _json(seed / "org.json")
    for d in org["departments"]:
        s.add(Department(**d))
    for e in org["employees"]:
        s.add(Employee(emp_no=e["emp_no"], name=e["name"], grade=e["grade"], title=e.get("title", ""), dept=e["dept"]))
    for c in _json(seed / "connections.json"):
        s.add(Connection(token=c["token"], emp_no=c["emp_no"], mode=c["mode"], label=c.get("label", "")))
    reg = _json(seed / "processes.json")
    for i, a in enumerate(reg["articles"]):
        s.add(Article(id=a["id"], ord=i, title=a["title"], body=a["body"].strip()))
    for i, p in enumerate(reg["processes"]):
        s.add(Process(id=p["id"], ord=i, name=p["name"], data=p))
    clock.set_state(s, "regulation_common", {k: reg[k] for k in ("company", "title", "grades", "business_hours",
                                                                   "confidence_threshold", "evidence_categories")})
    rooms = _json(seed / "rooms.json")
    for r in rooms["rooms"]:
        s.add(Room(**r))
    for r in rooms["reservations"]:
        s.add(Reservation(room_id=r["room"], date=r["date"], start=r["start"], end=r["end"], title=r["title"],
                          emp_no=r["emp_no"], status="CONFIRMED"))
    clock.set_state(s, "holidays", _json(seed / "calendar.json").get("holidays", []))
    dash = seed / "dashboard"
    for r in _csv(dash / "work_allocation.csv"):
        s.add(WorkAllocation(grade_group=r["grade_group"], category=r["category"], stage=r["stage"], hours_per_week=float(r["hours_per_week"])))
    for r in _csv(dash / "survey.csv"):
        s.add(SurveyResponse(resp_id=r["resp_id"], dept=r["dept"], grade_group=r["grade_group"], rank1=r["rank1"], rank2=r["rank2"],
                             rank3=r["rank3"], hours_per_week_self=float(r["hours_per_week_self"]), reason=r["reason"]))
    for r in _csv(dash / "overtime.csv"):
        s.add(OvertimeRecord(date=r["date"], emp_no=r["emp_no"], dept=r["dept"], hours=float(r["hours"]),
                             cause_category=r["cause_category"], cause_stage=r["cause_stage"]))
    for r in _csv(dash / "automation_assumptions.csv"):
        s.add(AutomationAssumption(stage=r["stage"], rate=float(r["rate"]), feature=r["feature"]))
    demo = _json(seed / "demo_state.json")
    start = dt.datetime.fromisoformat(demo["start"])
    clock.set_clock(s, start)
    clock.set_state(s, "confirm_mode", {"mode": demo.get("confirm_mode", "A")})
    clock.set_state(s, "digest", {"times": demo.get("digest_times", ["09:00", "14:00"]), "last_check": demo["start"]})
    clock.set_state(s, "demo_start", {"start": demo["start"]})


def _doc_no(s: Session, when: dt.datetime, counters: dict) -> str:
    key = when.strftime("%Y%m%d")
    counters[key] = counters.get(key, 0) + 1
    return f"ST-{key}-{counters[key]:03d}"


def _load_docs(s: Session, seed: Path) -> dict:
    hol = clock.holidays(s)
    speed = _json(seed / "approver_speed_hours.json")
    history = _json(seed / "history.json")
    demo = _json(seed / "demo_state.json")
    counters: dict[str, int] = {}
    seq = 0
    entries = [("H", h) for h in history] + [("P", p) for p in demo["pending_docs"]]
    entries.sort(key=lambda x: x[1]["submitted"])
    stats = {"history": 0, "pending": 0}
    for kind, h in entries:
        seq += 1
        proc = regulations.get(s, h["process"])
        drafter = s.get(Employee, h["drafter"])
        submitted = dt.datetime.fromisoformat(h["submitted"])
        vals, _ = validation.normalize_values(s, proc, h["values"])
        evs, _ = validation.normalize_evidence(h.get("evidence") or [])
        d = Document(id=f"DOC-{seq:04d}", doc_no=_doc_no(s, submitted, counters), process_id=proc["id"], drafter=drafter.emp_no,
                     values=vals, evidence=evs, reasons={}, memo="", status="SUBMITTED", validation={}, version=1,
                     confirmed_version=1, confirmed_at=h["submitted"], created_via="SEED", updated_via="SEED",
                     created_at=h["submitted"], updated_at=h["submitted"], submitted_at=h["submitted"],
                     is_history=(kind == "H"))
        s.add(d)
        s.flush()
        line = approval_line.compute(s, proc, drafter, vals)["steps"]
        if kind == "P":
            res = validation.validate(s, proc, drafter, vals, evs, {}, now=submitted, doc_id=d.id)
            d.validation = {k: res[k] for k in ("errors", "warnings", "missing_reasons")}
            for st in line:
                s.add(ApprovalStep(doc_id=d.id, seq=st["seq"], approver=st["approver"], kind=st["kind"], level=st["level"],
                                   article=st["article"], basis=st["basis"], status="PENDING" if st["seq"] == 1 else "WAITING",
                                   started_at=h["submitted"] if st["seq"] == 1 else None))
            stats["pending"] += 1
            continue
        # 과거 이력: 결재자별 평균 처리 시간 × 배율로 처리 시각을 만든다
        factors = h.get("delay_factors") or [1.0] * 4
        t = submitted
        reject_at = 0 if h["outcome"] == "REJECTED" else None
        for i, st in enumerate(line):
            started = t
            hours = speed.get(st["approver"], 10.0) * factors[min(i, len(factors) - 1)]
            acted = clock.add_work_hours(started, hours, hol)
            if h.get("final") and i == len(line) - 1:
                acted = dt.datetime.fromisoformat(h["final"])
            status = "APPROVED"
            if reject_at is not None and i == reject_at:
                status = "REJECTED"
            s.add(ApprovalStep(doc_id=d.id, seq=st["seq"], approver=st["approver"], kind=st["kind"], level=st["level"],
                               article=st["article"], basis=st["basis"], status=status,
                               comment=(h.get("reject_reason") or {}).get("label") if status == "REJECTED" else None,
                               started_at=clock.iso(started), acted_at=clock.iso(acted)))
            t = acted
            if status == "REJECTED":
                for rest in line[i + 1:]:
                    s.add(ApprovalStep(doc_id=d.id, seq=rest["seq"], approver=rest["approver"], kind=rest["kind"],
                                       level=rest["level"], article=rest["article"], basis=rest["basis"], status="SKIPPED"))
                break
        if h["outcome"] == "REJECTED":
            d.status, d.reject_code = "REJECTED", (h.get("reject_reason") or {}).get("code")
            d.reject_comment = (h.get("reject_reason") or {}).get("label")
        else:
            d.status = "APPROVED"
            if proc["id"] == "MTG-01":
                v = vals
                s.add(Reservation(room_id=v["room"], date=v["date"], start=v["start"], end=v["end"], title=v.get("title", ""),
                                  emp_no=drafter.emp_no, doc_id=d.id, status="CONFIRMED"))
        d.final_at = clock.iso(t)
        stats["history"] += 1
    s.flush()
    # 시연 시작 시점에 이미 긴급인 결재는 그날 09:00 정기 알림으로 받은 것으로 둔다(읽음 처리).
    now = clock.now(s)
    nine = dt.datetime.combine(now.date(), dt.time(9))
    for n in notify.send_digest(s, nine, "정기 알림 09:00"):
        n.read = True
    s.add(AuditLog(at=clock.iso(now), real_at=dt.datetime.now().replace(microsecond=0).isoformat(), channel="SYSTEM",
                   actor="SYSTEM", action="reset", target="", summary=f"시드 적재: 과거 이력 {stats['history']}건, 결재 대기 {stats['pending']}건"))
    return stats

