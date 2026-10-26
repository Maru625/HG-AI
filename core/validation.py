"""검증 규칙 (설계서 7.4). 증빙 확인(check_evidence), 저장(save_drafts), 상신(submit_drafts)이 같은 정의를 쓴다.

규칙 코드
- REQUIRED_MISSING, INVALID_VALUE, EVIDENCE_REQUIRED, SUM_MISMATCH, DUPLICATE_EVIDENCE : 오류
- ATTACHMENT_MISSING : 오류. 필수 증빙에 첨부 파일(원본 파일 이름)이 없다 (제20조)
- TIMING : 오류. 시점이 규정과 맞지 않는다 (사용 전 정산 제10조, 출장 종료 전 정산·보고 제11·12조,
           희망 납기 경과 제13조, 지난 시각 회의실 신청 제14조, 오늘 뒤 날짜의 증빙)
- LIMIT_EXCEEDED : 경고. 사유(reasons)가 없으면 오류
- HOLIDAY_USE    : 경고. 사유가 없으면 확인(confirm) 불가
- LOW_CONFIDENCE : 경고. 확인(confirm) 때 기안자가 원본과 대조했다고 확인해야 한다 (제22조)
- DEADLINE_PASSED : 경고
- ROOM_CAPACITY, ROOM_CONFLICT : 오류
중복 판정은 같은 기안자의 상신·승인 문서를 기준으로 한다(고속철도 요금처럼 남과 금액이 같을 수 있다).
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock, regulations
from .models import Document, Employee, Reservation, Room

EXPENSE = {"EXP-01", "EXP-02"}
ACTIVE = ("SUBMITTED", "APPROVED")


def issue(code: str, level: str, message: str, *, field=None, seq=None, reason_key=None, needs_reason=False,
          article=None, fix_hint="", **extra) -> dict:
    d = {"code": code, "level": level, "message": message}
    for k, v in (("field", field), ("evidence_seq", seq), ("reason_key", reason_key), ("article", article)):
        if v is not None:
            d[k] = v
    if needs_reason:
        d["needs_reason"] = True
    if fix_hint:
        d["fix_hint"] = fix_hint
    d.update(extra)
    return d


# ───────── 값 정리 ─────────
def _to_int(v):
    if isinstance(v, bool):
        raise ValueError
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"-?[\d,]+(원)?", v.strip()):
        return int(v.strip().replace(",", "").replace("원", ""))
    raise ValueError


def _to_date(v) -> str:
    return dt.date.fromisoformat(str(v).strip()[:10]).isoformat()


def _to_time(v) -> str:
    t = str(v).strip()
    if not re.fullmatch(r"\d{1,2}:\d{2}", t):
        raise ValueError
    h, m = t.split(":")
    if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
        raise ValueError
    return f"{int(h):02d}:{m}"


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if str(v).strip().lower() in ("true", "예", "yes", "y", "1", "있음"):
        return True
    if str(v).strip().lower() in ("false", "아니오", "no", "n", "0", "없음"):
        return False
    raise ValueError


def normalize_values(s: Session, process: dict, values: dict) -> tuple[dict, list[dict]]:
    out, issues = dict(values or {}), []
    for f in process["fields"]:
        k, t = f["key"], f["type"]
        if k not in out or out[k] in (None, "", []):
            continue
        v = out[k]
        try:
            if t == "int":
                out[k] = _to_int(v)
            elif t == "date":
                out[k] = _to_date(v)
            elif t == "time":
                out[k] = _to_time(v)
            elif t == "bool":
                out[k] = _to_bool(v)
            elif t == "enum":
                if v not in f.get("options", []):
                    raise ValueError
            elif t == "list":
                if isinstance(v, str):
                    out[k] = [x.strip() for x in re.split(r"[,、·]", v) if x.strip()]
                elif not isinstance(v, list):
                    raise ValueError
            elif t == "room":
                room = s.get(Room, str(v)) or s.scalars(select(Room).where(Room.name == str(v))).first()
                if not room:
                    raise ValueError
                out[k] = room.id
            elif t == "items":
                if not isinstance(v, list):
                    raise ValueError
                for item in v:
                    for sub in f.get("item_fields", []):
                        if sub["type"] == "date" and item.get(sub["key"]):
                            item[sub["key"]] = _to_date(item[sub["key"]])
        except (ValueError, TypeError):
            hint = f"선택지: {', '.join(f['options'])}" if t == "enum" else f"형식: {t}"
            issues.append(issue("INVALID_VALUE", "error", f"'{f['label']}' 값의 형식이 잘못되었습니다: {v}",
                                field=k, fix_hint=hint))
    return out, issues


def normalize_evidence(evidence: list[dict] | None) -> tuple[list[dict], list[dict]]:
    out, issues = [], []
    for i, e in enumerate(evidence or [], 1):
        e = dict(e)
        e["seq"] = i
        try:
            e["date"] = _to_date(e.get("date"))
        except (ValueError, TypeError):
            issues.append(issue("INVALID_VALUE", "error", f"증빙 {i}의 날짜 형식이 잘못되었습니다.", seq=i, fix_hint="YYYY-MM-DD"))
        try:
            e["amount"] = _to_int(e.get("amount"))
        except (ValueError, TypeError):
            issues.append(issue("INVALID_VALUE", "error", f"증빙 {i}의 금액이 숫자가 아닙니다.", seq=i))
        if e.get("nights") not in (None, ""):
            try:
                e["nights"] = _to_int(e["nights"])
            except (ValueError, TypeError):
                issues.append(issue("INVALID_VALUE", "error", f"증빙 {i}의 박 수가 숫자가 아닙니다.", seq=i))
        try:
            e["confidence"] = float(e.get("confidence", 1.0))
        except (ValueError, TypeError):
            e["confidence"] = 1.0
        for k in ("merchant", "category"):
            if not e.get(k):
                issues.append(issue("REQUIRED_MISSING", "error", f"증빙 {i}의 {'상호' if k == 'merchant' else '비목'}이 비어 있습니다.", seq=i))
        out.append(e)
    return out, issues


# ───────── 증빙 규칙 ─────────
def _other_evidence(s: Session, drafter: str, exclude: set[str]) -> dict[tuple, str]:
    found: dict[tuple, str] = {}
    q = select(Document).where(Document.drafter == drafter, Document.status.in_(ACTIVE))
    for d in s.scalars(q):
        if d.id in exclude:
            continue
        for e in d.evidence or []:
            found.setdefault((e.get("date"), e.get("merchant"), e.get("amount")), d.doc_no or d.id)
    return found


def evidence_rules(s: Session, process: dict, drafter: Employee, evidence: list[dict], reasons: dict,
                   exclude_doc_ids: set[str] | None = None) -> list[dict]:
    pid = process["id"]
    com = regulations.common(s)
    hol = clock.holidays(s)
    today = clock.now(s).date().isoformat()
    threshold = com.get("confidence_threshold", 0.8)
    out: list[dict] = []
    ev_spec = process.get("evidence", {})
    allowed = ev_spec.get("categories")
    others = _other_evidence(s, drafter.emp_no, exclude_doc_ids or set())
    seen: dict[tuple, int] = {}
    for e in evidence:
        i = e["seq"]
        key = (e.get("date"), e.get("merchant"), e.get("amount"))
        if allowed and e.get("category") and e["category"] not in allowed:
            out.append(issue("INVALID_VALUE", "error", f"증빙 {i}의 비목 '{e['category']}'은 이 프로세스에서 쓰지 않습니다.",
                             seq=i, fix_hint="허용 비목: " + ", ".join(allowed)))
        if key in others:
            out.append(issue("DUPLICATE_EVIDENCE", "error",
                             f"증빙 {i}({e.get('merchant')}, {e.get('date')}, {e.get('amount'):,}원)은 이미 {others[key]}에서 정산되었습니다."
                             if isinstance(e.get("amount"), int) else f"증빙 {i}은 이미 정산되었습니다.",
                             seq=i, article="제20조", existing_doc=others[key],
                             fix_hint="이 증빙을 빼고 다시 확인하세요."))
        elif key in seen:
            out.append(issue("DUPLICATE_EVIDENCE", "error", f"증빙 {i}이 증빙 {seen[key]}과 같습니다.", seq=i, article="제20조",
                             fix_hint="같은 영수증을 두 번 넣지 마세요."))
        seen.setdefault(key, i)
        if ev_spec.get("required") and not str(e.get("filename") or "").strip():
            out.append(issue("ATTACHMENT_MISSING", "error",
                             f"증빙 {i}({e.get('merchant') or '상호 없음'})에 첨부 파일이 없습니다. {ev_spec.get('label', '증빙')} 원본 또는 사본을 첨부해야 합니다.",
                             seq=i, article="제20조" if "제20조" in process["articles"] else process["articles"][0],
                             fix_hint="자료에서 읽은 원본 파일 이름을 filename에 넣으세요(화면에서는 '첨부 파일' 칸)."))
        if isinstance(e.get("date"), str) and len(e["date"]) == 10 and e["date"] > today:
            out.append(issue("TIMING", "error", f"증빙 {i}의 날짜({e['date']})가 오늘({today})보다 뒤입니다.",
                             seq=i, article=process["articles"][0], fix_hint="증빙 날짜를 다시 확인하세요."))
        if pid in EXPENSE and e.get("date"):
            try:
                d = dt.date.fromisoformat(e["date"])
                if not clock.is_bday(d, hol):
                    rk = f"HOLIDAY_USE:{i}"
                    out.append(issue("HOLIDAY_USE", "warning", f"증빙 {i}은 주말·공휴일({e['date']}) 사용입니다. 사유가 필요합니다.",
                                     seq=i, reason_key=rk, needs_reason=True, article="제21조", reason=reasons.get(rk)))
            except ValueError:
                pass
        if float(e.get("confidence", 1.0)) < threshold:
            out.append(issue("LOW_CONFIDENCE", "warning",
                             f"증빙 {i}의 판독 신뢰도가 낮습니다({e.get('confidence')}). 원본의 날짜·금액을 확인하세요.",
                             seq=i, article="제22조", needs_original_check=True,
                             fix_hint="확인(②) 때 기안자가 원본과 대조했는지 확인합니다. 원본을 다시 받을 필요는 없습니다."))
    limits = process.get("limits", {})
    if "lodging_per_night" in limits:
        cap = limits["lodging_per_night"].get(drafter.grade)
        for e in evidence:
            if e.get("category") != "숙박비" or not isinstance(e.get("amount"), int):
                continue
            nights = e.get("nights")
            if not nights:
                out.append(issue("REQUIRED_MISSING", "error", f"증빙 {e['seq']}(숙박비)에 박 수(nights)가 없습니다.", seq=e["seq"]))
                continue
            per = e["amount"] / nights
            if cap and per > cap:
                rk = f"LIMIT_EXCEEDED:{e['seq']}"
                out.append(_limit_issue(rk, reasons, seq=e["seq"], article="제11조의2",
                                        message=f"숙박비 1박 {per:,.0f}원이 {drafter.grade} 상한 {cap:,}원을 넘습니다.",
                                        over=round(per - cap)))
    if "meal_per_day" in limits:
        per_day: dict[str, int] = defaultdict(int)
        for e in evidence:
            if e.get("category") == "식비" and isinstance(e.get("amount"), int) and e.get("date"):
                per_day[e["date"]] += e["amount"]
        for day, total in sorted(per_day.items()):
            if total > limits["meal_per_day"]:
                rk = f"LIMIT_EXCEEDED:meal:{day}"
                out.append(_limit_issue(rk, reasons, article="제11조의2",
                                        message=f"{day} 식비 {total:,}원이 1일 상한 {limits['meal_per_day']:,}원을 넘습니다.",
                                        over=total - limits["meal_per_day"]))
    return out


def _limit_issue(rk: str, reasons: dict, *, article: str, message: str, seq=None, over=None) -> dict:
    reason = (reasons or {}).get(rk)
    if reason:
        return issue("LIMIT_EXCEEDED", "warning", message, seq=seq, reason_key=rk, needs_reason=True,
                     article=article, reason=reason, over=over)
    return issue("LIMIT_EXCEEDED", "error", message + " 사유가 없으면 정산할 수 없습니다.", seq=seq, reason_key=rk,
                 needs_reason=True, article=article, over=over,
                 fix_hint=f"사용자에게 사유를 물어 reasons에 '{rk}' 키로 넣고 다시 저장하세요.")


# ───────── 문서 전체 검증 ─────────
def validate(s: Session, process: dict, drafter: Employee, values: dict, evidence: list[dict], reasons: dict, *,
             now: dt.datetime, doc_id: str | None = None, original_doc_id: str | None = None) -> dict:
    """저장·상신 때의 전체 검증. 반환: normalized values/evidence, errors, warnings, missing_reasons."""
    reasons = dict(reasons or {})
    vals, issues = normalize_values(s, process, values)
    evs, ev_issues = normalize_evidence(evidence)
    issues += ev_issues
    pid = process["id"]

    for f in process["fields"]:
        k = f["key"]
        req = f.get("required")
        if f.get("required_if"):
            ck, cv = next(iter(f["required_if"].items()))
            req = vals.get(ck) == cv
        v = vals.get(k)
        if req and (v is None or v == "" or v == []):
            issues.append(issue("REQUIRED_MISSING", "error", f"필수 항목 '{f['label']}'이 비어 있습니다.", field=k,
                                fix_hint="자료에서 찾을 수 없으면 사용자에게 물어보세요."))
        if f["type"] == "items" and isinstance(v, list):
            for n, item in enumerate(v, 1):
                for sub in f.get("item_fields", []):
                    if sub.get("required") and not (item or {}).get(sub["key"]):
                        issues.append(issue("REQUIRED_MISSING", "error",
                                            f"{f['label']} {n}번째 항목의 '{sub['label']}'이 비어 있습니다.",
                                            field=f"{k}[{n}].{sub['key']}", fix_hint="사용자에게 물어보세요."))
    ev_spec = process.get("evidence", {})
    if ev_spec.get("required") and not evs:
        issues.append(issue("EVIDENCE_REQUIRED", "error", f"{ev_spec.get('label', '증빙')}이 필요합니다.", article=process["articles"][-1]))

    exclude = {x for x in (doc_id, original_doc_id) if x}
    issues += evidence_rules(s, process, drafter, evs, reasons, exclude)

    checks = set(process.get("checks", []))
    if "sum_matches_evidence" in checks and evs:
        f = process["amount_field"]
        total = sum(e["amount"] for e in evs if isinstance(e.get("amount"), int))
        if isinstance(vals.get(f), int) and vals[f] != total:
            issues.append(issue("SUM_MISMATCH", "error", f"'{f}' {vals[f]:,}원이 증빙 합계 {total:,}원과 다릅니다.", field=f,
                                fix_hint=f"{f}를 {total:,}으로 고치거나 증빙을 확인하세요.", evidence_total=total))
    if "qty_times_price" in checks and all(isinstance(vals.get(k), int) for k in ("qty", "unit_price", "amount")):
        if vals["qty"] * vals["unit_price"] != vals["amount"]:
            issues.append(issue("SUM_MISMATCH", "error", f"금액 {vals['amount']:,}원이 수량 × 단가 {vals['qty'] * vals['unit_price']:,}원과 다릅니다.",
                                field="amount"))
    if "per_person_meal_limit" in checks and vals.get("account") == "회의비" and isinstance(vals.get("amount"), int):
        people = len(vals.get("attendees") or [])
        cap = process.get("limits", {}).get("per_person_meal")
        if people and cap and vals["amount"] / people > cap:
            issues.append(_limit_issue("LIMIT_EXCEEDED:per_person", reasons, article="제10조의2",
                                       message=f"1인당 {vals['amount'] / people:,.0f}원이 상한 {cap:,}원을 넘습니다."))
    if "trip_dates" in checks and vals.get("trip_start") and vals.get("trip_end"):
        if str(vals["trip_end"]) < str(vals["trip_start"]):
            issues.append(issue("INVALID_VALUE", "error", "출장 종료일이 시작일보다 앞섭니다.", field="trip_end"))
    if "report_deadline" in checks and process.get("deadline"):
        dl = deadline_of(s, process, vals)
        if dl and now.date() > dl:
            issues.append(issue("DEADLINE_PASSED", "warning", f"처리 기한({dl})이 지났습니다.", field=process["deadline"]["from_field"],
                                article=process["articles"][0]))
    issues += timing_rules(process, vals, now)
    if pid == "MTG-01":
        issues += room_rules(s, vals, doc_id)

    errors = [x for x in issues if x["level"] == "error"]
    warnings = [x for x in issues if x["level"] == "warning"]
    missing = sorted({x["reason_key"] for x in issues if x.get("needs_reason") and not x.get("reason")})
    return {"values": vals, "evidence": evs, "errors": errors, "warnings": warnings, "missing_reasons": missing}


def timing_rules(process: dict, vals: dict, now: dt.datetime) -> list[dict]:
    """규정이 정한 시점과 맞는지 본다. 날짜 판단은 시연 시계(now) 기준이다."""
    checks = set(process.get("checks", []))
    today, hm = now.date().isoformat(), now.strftime("%H:%M")
    art = process["articles"][0]
    out = []

    def _d(k):
        v = vals.get(k)
        return v if isinstance(v, str) and len(v) == 10 else None
    if "use_after" in checks and _d("use_date") and vals["use_date"] > today:
        out.append(issue("TIMING", "error", f"사용일({vals['use_date']})이 오늘({today})보다 뒤입니다. 일반경비는 사용한 뒤에 정산합니다.",
                         field="use_date", article=art, fix_hint="사용일을 확인하거나, 사용한 뒤에 정산하세요."))
    if "trip_ended" in checks and _d("trip_end") and vals["trip_end"] > today:
        what = "출장비는 출장을 마친 뒤 정산합니다" if process["id"] == "EXP-02" else "출장결과보고는 출장을 마친 뒤 올립니다"
        out.append(issue("TIMING", "error", f"출장 종료일({vals['trip_end']})이 오늘({today})보다 뒤입니다. {what}.",
                         field="trip_end", article=art, fix_hint="출장 종료일을 확인하세요."))
    if "due_not_past" in checks and _d("due_date") and vals["due_date"] < today:
        out.append(issue("TIMING", "error", f"희망 납기({vals['due_date']})가 이미 지났습니다. 물품은 구매 전에 요청합니다.",
                         field="due_date", article=art, fix_hint="희망 납기를 오늘 이후로 고치세요."))
    if "not_past" in checks and _d("date"):
        st = vals.get("start") if isinstance(vals.get("start"), str) else ""
        if vals["date"] < today or (vals["date"] == today and st and st <= hm):
            out.append(issue("TIMING", "error", f"지난 시각({vals['date']} {st})은 신청할 수 없습니다. 회의실은 사용 전에 신청해 결재를 받습니다.",
                             field="date", article=art, fix_hint="check_room_availability로 앞으로의 시간을 찾으세요."))
    return out


def deadline_of(s: Session, process: dict, values: dict) -> dt.date | None:
    dl = process.get("deadline")
    if not dl or not values.get(dl["from_field"]):
        return None
    try:
        base = dt.date.fromisoformat(str(values[dl["from_field"]]))
    except ValueError:
        return None
    return clock.add_bdays(base, dl["business_days"], clock.holidays(s))


def room_rules(s: Session, vals: dict, doc_id: str | None) -> list[dict]:
    out = []
    room = s.get(Room, str(vals.get("room", ""))) if vals.get("room") else None
    st, en, day = vals.get("start"), vals.get("end"), vals.get("date")
    if st and en and st >= en:
        out.append(issue("INVALID_VALUE", "error", "종료 시각이 시작 시각보다 늦어야 합니다.", field="end"))
    if room and isinstance(vals.get("people"), int) and vals["people"] > room.capacity:
        out.append(issue("ROOM_CAPACITY", "error", f"{room.name} 정원({room.capacity}명)보다 인원({vals['people']}명)이 많습니다.",
                         field="people", article="제14조", fix_hint="check_room_availability로 다른 회의실을 찾으세요."))
    if room and st and en and day:
        for c in conflicts(s, room.id, day, st, en, doc_id):
            out.append(issue("ROOM_CONFLICT", "error",
                             f"{room.name} {day} {c.start}~{c.end}에 '{c.title}' 예약({'가예약' if c.status == 'HOLD' else '확정'})이 있습니다.",
                             field="room", article="제14조", fix_hint="check_room_availability로 빈 회의실이나 시간을 찾으세요."))
    return out


def conflicts(s: Session, room_id: str, day: str, st: str, en: str, doc_id: str | None = None) -> list[Reservation]:
    q = select(Reservation).where(Reservation.room_id == room_id, Reservation.date == day,
                                  Reservation.status.in_(("HOLD", "CONFIRMED")))
    return [r for r in s.scalars(q) if r.start < en and st < r.end and (doc_id is None or r.doc_id != doc_id)]
