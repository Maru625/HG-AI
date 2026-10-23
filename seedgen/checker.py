"""시드가 시연 대본의 조건을 만족하는지 확인한다(계획서 7.5).

결재 중요도처럼 P1의 판정이 필요한 조건은 여기서 '대본이 기대한 값이 있는지'까지만 보고,
실제 판정은 P1 시험(tests/)에서 확인한다.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

from .generate import STAGES, SOURCE_DIR, load_sources


def _bdays_until(today: dt.date, target: dt.date, holidays: set[str]) -> int:
    n, d = 0, today
    while d < target:
        d += dt.timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in holidays:
            n += 1
    return n


def _add_bdays(start: dt.date, n: int, holidays: set[str]) -> dt.date:
    d = start
    while n > 0:
        d += dt.timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in holidays:
            n -= 1
    return d


def run_checks(seed_dir: Path, inputs_dir: Path, source_dir: Path = SOURCE_DIR) -> list[tuple[bool, str]]:
    reg, scn = load_sources(source_dir)
    results: list[tuple[bool, str]] = []

    def ok(cond: bool, msg: str):
        results.append((bool(cond), msg))

    holidays = set(scn.get("holidays") or [])
    today = dt.datetime.fromisoformat(scn["demo"]["start"]).date()
    org = json.loads((seed_dir / "org.json").read_text(encoding="utf-8"))
    emps = {e["emp_no"]: e for e in org["employees"]}
    depts = {d["code"]: d for d in org["departments"]}
    history = json.loads((seed_dir / "history.json").read_text(encoding="utf-8"))
    procs = {p["id"]: p for p in json.loads((seed_dir / "processes.json").read_text(encoding="utf-8"))["processes"]}
    trip = scn["trip"]

    # 1. 중복 영수증 (중복 판정은 같은 기안자 기준: 고속철도 요금처럼 남과 금액이 같을 수 있다)
    hist_ev = {(e["date"], e["merchant"], e["amount"]) for h in history
               if h["outcome"] == "APPROVED" and h["drafter"] == trip["traveler"] for e in h["evidence"]}
    dups = [r for r in trip["receipts"] if "중복" in r.get("problem", "")]
    ok(dups and all((r["date"], r["merchant"], r["amount"]) in hist_ev for r in dups),
       "중복으로 넣은 영수증이 출장자 본인의 과거 결재 이력(승인)에 있다")
    others = [r for r in trip["receipts"] if r not in dups]
    ok(all((r["date"], r["merchant"], r["amount"]) not in hist_ev for r in others),
       "나머지 영수증은 출장자 본인의 과거 이력과 겹치지 않는다")

    # 2. 숙박비 한도 초과
    grade = emps[trip["traveler"]]["grade"]
    limit = procs["EXP-02"]["limits"]["lodging_per_night"][grade]
    lodging = [r for r in trip["receipts"] if r["category"] == "숙박비"]
    ok(lodging and all(r["amount"] / r["nights"] > limit for r in lodging), f"숙박비가 1박 상한({grade} {limit:,}원)을 넘는다")

    # 3. 흐린 영수증
    ok(any(r.get("blur") for r in trip["receipts"]), "흐린 영수증이 1장 이상 있다")

    # 4. 회의일 긴급
    mreq = trip["meeting_request"]
    n = _bdays_until(today, dt.date.fromisoformat(mreq["date"]), holidays)
    ok(n <= 3, f"시연 시작일 기준 회의일까지 {n}영업일 → 긴급 기준(3영업일 이내)에 든다")

    # 5. 결과보고 기한
    deadline = _add_bdays(dt.date.fromisoformat(trip["end"]), procs["RPT-01"]["deadline"]["business_days"], holidays)
    left = _bdays_until(today, deadline, holidays)
    ok(today <= deadline and left <= 2, f"결과보고 기한 {deadline}까지 {left}영업일 → 긴급 기준(2영업일 이내), 기한 경과는 아님")

    # 6. 회의실: 소회의실 A는 겹치고 B는 빈다
    rooms = json.loads((seed_dir / "rooms.json").read_text(encoding="utf-8"))
    def busy(room):
        return any(r["room"] == room and r["date"] == mreq["date"] and r["start"] < mreq["end"] and mreq["start"] < r["end"]
                   for r in rooms["reservations"])
    ok(busy("RM-A") and not busy("RM-B"), "요청 시간에 소회의실 A는 예약돼 있고 소회의실 B는 비어 있다")

    # 7. 결재 대기 문서의 기대 중요도
    expects = {p["expect"].split()[0] for p in scn["pending_docs"]}
    ok(expects == {"긴급", "보통", "낮음"}, "결재 대기 더미 문서가 긴급·보통·낮음을 하나씩 기대한다 (실제 판정은 P1 시험)")
    team_head = {d["code"]: d["head"] for d in org["departments"]}
    ok(all(team_head[emps[p["drafter"]]["dept"]] == "E2001" for p in scn["pending_docs"]),
       "결재 대기 문서의 첫 결재자가 모두 박서연 팀장이다")

    # 8. 연결 토큰
    conns = json.loads((seed_dir / "connections.json").read_text(encoding="utf-8"))
    modes = {c["mode"] for c in conns}
    ok(modes == {"drafter", "approver"} and all(c["emp_no"] in emps for c in conns), "기안자용·결재자용 연결 토큰이 있다")
    appr = next(c for c in conns if c["mode"] == "approver")
    ok(team_head[emps[trip["traveler"]]["dept"]] == appr["emp_no"], "결재자용 연결의 사용자가 출장자의 팀장이다")

    # 9. 범주
    allowed = set(STAGES) | {""}
    with (seed_dir / "dashboard" / "work_allocation.csv").open(encoding="utf-8") as f:
        a_stages = {r["stage"] for r in csv.DictReader(f)}
    with (seed_dir / "dashboard" / "overtime.csv").open(encoding="utf-8") as f:
        o_stages = {r["cause_stage"] for r in csv.DictReader(f)}
    with (seed_dir / "dashboard" / "survey.csv").open(encoding="utf-8") as f:
        s_opts = {r[k] for r in csv.DictReader(f) for k in ("rank1", "rank2", "rank3")}
    ok(a_stages <= allowed and o_stages <= allowed and s_opts <= set(STAGES) | {"기타 사무"},
       "대시보드 데이터의 범주가 설계서 1.2의 전결 단계 6개와 같다")

    # 10. 입력 자료
    files = [r["file"] for r in trip["receipts"]]
    ok(all((inputs_dir / f).exists() for f in files) and (inputs_dir / "출장메모_대전.txt").exists(), "시연 입력 자료(메모, 영수증 5장)가 있다")
    ok("담당 미정" in trip["memo"], "출장 메모에 담당이 빠진 후속 조치가 있다(AI가 물어야 함)")
    return results
