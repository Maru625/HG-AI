"""P4 생성기: 시드 파일과 시연 입력 자료를 만든다.

- 원본은 seedgen/source/*.yaml 이다. 규정 원본 하나에서 사람용 문서와 기계용 정의를 만든다(설계서 7.1).
- 무작위 값은 시나리오의 seed 하나로 고정한다. 같은 seed면 같은 파일이 나온다(설계서 8.2).
- 특정 수치에 맞추려고 값을 고치지 않는다. 생성 가정은 generation_log.md에 남긴다.
- DB에는 쓰지 않는다. P1이 seed/ 폴더를 읽어 적재한다(계약 C3).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import random
from pathlib import Path

import yaml

SOURCE_DIR = Path(__file__).parent / "source"

STAGES = ["처리 파악", "증빙 준비", "문서 작성", "반려 후 재작성", "진행 확인·문의", "결재 검토"]
TOP_CATEGORIES = ["본업", "회의", "전결 처리", "기타 사무"]
GRADE_GROUPS = ["사원", "선임", "책임", "수석", "팀장 이상"]

# 직급군별 주당 시간 기본값 (가정). 표의 값에 ±5% 범위의 잡음을 더한다.
BASE_ALLOCATION = {
    "본업": [26.0, 25.0, 23.0, 21.0, 14.0],
    "회의": [4.0, 5.0, 6.0, 8.0, 11.0],
    "기타 사무": [4.0, 4.0, 4.0, 4.0, 5.0],
    ("전결 처리", "처리 파악"): [1.2, 1.1, 1.0, 0.8, 0.8],
    ("전결 처리", "증빙 준비"): [1.8, 1.6, 1.4, 1.1, 0.9],
    ("전결 처리", "문서 작성"): [1.5, 1.6, 1.7, 1.6, 1.4],
    ("전결 처리", "반려 후 재작성"): [0.9, 0.8, 0.7, 0.6, 0.4],
    ("전결 처리", "진행 확인·문의"): [0.6, 0.6, 0.7, 0.7, 0.8],
    ("전결 처리", "결재 검토"): [0.0, 0.0, 0.3, 0.6, 4.2],
}

# 자동화 효과 가정표 (설계서 8.2). 값은 가정이다.
AUTOMATION = [
    ("처리 파악", 0.6, "어떤 양식에 무엇을 써야 하는지 AI가 규정에서 찾아 제시, 결재선은 서버가 계산"),
    ("증빙 준비", 0.7, "가진 영수증의 값을 AI가 읽어 양식 칸에 옮겨 적고, 합계·중복·한도·휴일은 서버가 확인"),
    ("문서 작성", 0.6, "메모·대화 내용을 AI가 전결 양식에 맞춰 채우고, 필수 항목은 서버가 검증"),
    ("반려 후 재작성", 0.5, "양식·규정에 안 맞는 부분을 올리기 전에 서버가 잡음"),
    ("진행 확인·문의", 0.3, "결재가 어디까지 갔는지 대화로 조회(get_status)"),
    ("결재 검토", 0.4, "결재 중요도·주의 항목 판정, 묶음 알림, AI 브리핑"),
]

SURNAMES = list("김이박최정강조윤장임한오서신권황안송류홍")
GIVEN = ["민서", "지우", "서준", "하은", "도윤", "예린", "시우", "유나", "현우", "다인", "준호", "수아",
         "태윤", "가은", "지민", "승현", "보람", "재원", "나연", "우진", "소율", "건우", "채은", "윤호"]

REJECT_REASONS = [
    ("EVIDENCE_REQUIRED", "증빙 누락", 0.26),
    ("SUM_MISMATCH", "합계 불일치", 0.18),
    ("LIMIT_EXCEEDED", "한도 초과 사유 부족", 0.2),
    ("REQUIRED_MISSING", "필수 항목 누락", 0.2),
    ("DEADLINE_PASSED", "기한 경과", 0.08),
    ("DUPLICATE_EVIDENCE", "중복 증빙", 0.08),
]

SURVEY_REASONS = {
    "처리 파악": ["어떤 결재를 올려야 하는지 매번 찾아봐야 함", "전결권자가 헷갈림"],
    "증빙 준비": ["영수증 하나하나 옮겨 적기", "합계 맞추기가 번거로움", "영수증 분실·흐림"],
    "문서 작성": ["양식 항목이 많음", "메모를 다시 옮겨 써야 함"],
    "반려 후 재작성": ["사소한 누락으로 반려됨", "반려 사유를 늦게 알게 됨"],
    "진행 확인·문의": ["결재가 어디까지 갔는지 모름", "결재자에게 따로 물어봐야 함"],
    "결재 검토": ["결재가 한꺼번에 몰림", "무엇부터 봐야 할지 모름", "문서마다 규정을 다시 확인"],
    "기타 사무": ["자료 정리", "일정 조율"],
}


def load_sources(source_dir: Path = SOURCE_DIR) -> tuple[dict, dict]:
    reg = yaml.safe_load((source_dir / "regulations.yaml").read_text(encoding="utf-8"))
    scn = yaml.safe_load((source_dir / "scenario.yaml").read_text(encoding="utf-8"))
    return reg, scn


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _is_business_day(d: dt.date, holidays: set[str]) -> bool:
    return d.weekday() < 5 and d.isoformat() not in holidays


def _business_days(start: dt.date, end: dt.date, holidays: set[str]) -> list[dt.date]:
    out, d = [], start
    while d <= end:
        if _is_business_day(d, holidays):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _grade_group(emp: dict) -> str:
    return "팀장 이상" if emp.get("title") else emp["grade"]


# ───────────────────────── 조직 ─────────────────────────
def build_org(scn: dict, rng: random.Random) -> dict:
    org = scn["org"]
    depts = []
    for d in org["divisions"]:
        depts.append({"code": d["code"], "name": d["name"], "type": "division", "parent": None, "head": d["head"]})
    for o in org["offices"]:
        depts.append({"code": o["code"], "name": o["name"], "type": "office", "parent": o["division"], "head": o["head"]})
    for t in org["teams"]:
        depts.append({"code": t["code"], "name": t["name"], "type": "team", "parent": t["office"], "head": t["head"]})

    employees = [dict(p) for p in org["named_people"]]
    used = {e["name"] for e in employees}
    seq = 4001
    grades = ["사원", "선임", "선임", "책임", "책임", "수석"]
    for team, n in org["generated_per_team"].items():
        for _ in range(n):
            while True:
                name = rng.choice(SURNAMES) + rng.choice(GIVEN)
                if name not in used:
                    used.add(name)
                    break
            employees.append({"emp_no": f"E{seq}", "name": name, "grade": rng.choice(grades), "title": "", "dept": team})
            seq += 1
    return {"departments": depts, "employees": employees}


# ───────────────────────── 대시보드 데이터 ─────────────────────────
def build_work_allocation(rng: random.Random) -> list[dict]:
    rows = []
    for key, values in BASE_ALLOCATION.items():
        cat, stage = (key if isinstance(key, tuple) else (key, ""))
        for g, base in zip(GRADE_GROUPS, values):
            v = round(base * rng.uniform(0.95, 1.05), 1) if base else 0.0
            rows.append({"grade_group": g, "category": cat, "stage": stage, "hours_per_week": v})
    return rows


def build_survey(org: dict, rng: random.Random, n: int = 120) -> list[dict]:
    options = STAGES + ["기타 사무"]
    weights = {
        "사원": [1.2, 2.6, 2.0, 2.2, 1.0, 0.1, 0.9],
        "선임": [1.1, 2.4, 2.2, 2.0, 1.1, 0.1, 0.9],
        "책임": [1.0, 1.8, 2.0, 1.8, 1.3, 0.6, 0.8],
        "수석": [0.9, 1.4, 1.8, 1.5, 1.3, 1.2, 0.8],
        "팀장 이상": [0.8, 0.9, 1.2, 1.1, 1.2, 2.8, 0.7],
    }
    emps = [e for e in org["employees"] if e["dept"].startswith("T-")]
    rows = []
    for i in range(n):
        e = rng.choice(emps)
        g = _grade_group(e)
        picks: list[str] = []
        w = list(weights[g])
        opts = list(options)
        for _ in range(3):
            c = rng.choices(opts, weights=w, k=1)[0]
            idx = opts.index(c)
            picks.append(c)
            opts.pop(idx)
            w.pop(idx)
        team = next(d["name"] for d in org["departments"] if d["code"] == e["dept"])
        rows.append({
            "resp_id": f"S{i + 1:03d}", "dept": team, "grade_group": g,
            "rank1": picks[0], "rank2": picks[1], "rank3": picks[2],
            "hours_per_week_self": round(rng.uniform(3.0, 9.0) if g != "팀장 이상" else rng.uniform(6.0, 12.0), 1),
            "reason": rng.choice(SURVEY_REASONS[picks[0]]),
        })
    return rows


def build_overtime(org: dict, scn: dict, rng: random.Random) -> list[dict]:
    holidays = set(scn.get("holidays") or [])
    start, end = _d("2026-09-14"), _d("2026-11-06")
    team_p = {"T-R1": 0.36, "T-R2": 0.28, "T-QA": 0.24, "T-MS": 0.18}
    cause_w = {  # 상위 범주 가중치 (팀별 가정)
        "T-R1": {"본업": 0.40, "회의": 0.12, "전결 처리": 0.38, "기타 사무": 0.10},
        "T-R2": {"본업": 0.52, "회의": 0.16, "전결 처리": 0.22, "기타 사무": 0.10},
        "T-QA": {"본업": 0.55, "회의": 0.12, "전결 처리": 0.23, "기타 사무": 0.10},
        "T-MS": {"본업": 0.35, "회의": 0.20, "전결 처리": 0.30, "기타 사무": 0.15},
    }
    stage_w = [0.12, 0.27, 0.24, 0.17, 0.08, 0.12]
    rows = []
    teams = {d["code"]: d["name"] for d in org["departments"] if d["type"] == "team"}
    for day in _business_days(start, end, holidays):
        for e in org["employees"]:
            if e["dept"] not in teams:
                continue
            if rng.random() >= team_p[e["dept"]]:
                continue
            cw = cause_w[e["dept"]]
            cat = rng.choices(list(cw), weights=list(cw.values()), k=1)[0]
            stage = ""
            if cat == "전결 처리":
                w = list(stage_w)
                if e.get("title"):
                    w = [0.06, 0.06, 0.1, 0.06, 0.12, 0.6]
                stage = rng.choices(STAGES, weights=w, k=1)[0]
            rows.append({"date": day.isoformat(), "emp_no": e["emp_no"], "dept": teams[e["dept"]],
                         "hours": round(rng.choice([0.5, 1.0, 1.0, 1.5, 2.0, 2.5, 3.0]), 1),
                         "cause_category": cat, "cause_stage": stage})
    return rows


# ───────────────────────── 과거 결재 이력 ─────────────────────────
MERCHANTS = {
    "회의비": ["판교 숲속식당", "가상분식 판교", "한솥가상도시락", "정자동 국수집"],
    "소모품비": ["가상문구 판교점", "오피스가상마트"],
    "복리후생비": ["가상카페 판교", "판교 가상베이커리"],
    "도서인쇄비": ["가상서점 판교", "판교 인쇄소"],
}


def _rand_time(rng: random.Random, day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(rng.randint(9, 17), rng.choice([0, 10, 20, 30, 40, 50])))


def build_history(org: dict, scn: dict, rng: random.Random, n: int = 160) -> list[dict]:
    holidays = set(scn.get("holidays") or [])
    days = _business_days(_d("2026-08-17"), _d("2026-11-10"), holidays)
    staff = [e for e in org["employees"] if e["dept"].startswith("T-") and not e.get("title")]
    mix = [("EXP-01", 0.45), ("EXP-02", 0.2), ("RPT-01", 0.15), ("PUR-01", 0.12), ("MTG-01", 0.08)]
    # 출장자 본인의 시연 영수증과 우연히 겹치면 안 된다(중복은 dup-basis 하나만 의도한다).
    trip = scn["trip"]
    reserved = {(r["date"], r["merchant"], r["amount"]) for r in trip["receipts"]}
    docs = []
    for i in range(n):
        e = rng.choice(staff)
        proc = rng.choices([m[0] for m in mix], weights=[m[1] for m in mix], k=1)[0]
        day = rng.choice(days)
        values, evidence = _history_values(proc, e, day, rng)
        while e["emp_no"] == trip["traveler"] and any((x["date"], x["merchant"], x["amount"]) in reserved for x in evidence):
            day = rng.choice(days)
            values, evidence = _history_values(proc, e, day, rng)
        rejected = rng.random() < 0.16
        reason = None
        if rejected:
            reason = rng.choices(REJECT_REASONS, weights=[r[2] for r in REJECT_REASONS], k=1)[0]
        # 결재 단계별 처리 지연 배율. P1이 결재선을 계산한 뒤 '결재자별 평균 처리 시간 × 배율'로 시각을 정한다.
        factors = [round(min(2.5, max(0.3, rng.lognormvariate(0, 0.45))), 2) for _ in range(4)]
        docs.append({
            "key": f"hist-{i + 1:03d}", "process": proc, "drafter": e["emp_no"],
            "submitted": _rand_time(rng, day).isoformat(), "values": values, "evidence": evidence,
            "outcome": "REJECTED" if rejected else "APPROVED",
            "reject_reason": None if not reason else {"code": reason[0], "label": reason[1]},
            "delay_factors": factors,
        })
    return docs


# 결재자별 평균 처리 시간(업무 시간 기준, 가정). '결재자별 대기 시간' 지표의 바탕이다.
APPROVER_SPEED_HOURS = {"E2001": 6.0, "E2002": 20.0, "E2003": 11.0, "E2004": 9.0,
                        "E1101": 14.0, "E1102": 12.0, "E1001": 22.0, "E1002": 18.0}


def _history_values(proc: str, e: dict, day: dt.date, rng: random.Random) -> tuple[dict, list]:
    if proc == "EXP-01":
        account = rng.choice(list(MERCHANTS))
        merchant = rng.choice(MERCHANTS[account])
        amount = rng.choice([12000, 18000, 24000, 36000, 42000, 58000, 96000, 132000, 188000, 245000])
        v = {"use_date": day.isoformat(), "merchant": merchant, "amount": amount, "account": account,
             "purpose": {"회의비": "업무 협의 식사", "소모품비": "사무용품 구매", "복리후생비": "팀 간식", "도서인쇄비": "기술 도서 구매"}[account]}
        if account == "회의비":
            v["attendees"] = [e["name"], "팀원"]
        return v, [{"date": day.isoformat(), "merchant": merchant, "category": account, "amount": amount,
                    "filename": f"h_{rng.randint(1000, 9999)}.png", "confidence": 0.97}]
    if proc == "EXP-02":
        nights = rng.choice([1, 2])
        lodging = nights * rng.choice([85000, 95000, 100000])
        trans = rng.choice([23700, 35000, 59800])
        start = day - dt.timedelta(days=nights + 1)
        ev = [{"date": start.isoformat(), "merchant": "누리고속철도", "category": "교통비", "amount": trans, "filename": "h_t1.png", "confidence": 0.97},
              {"date": (start + dt.timedelta(days=nights)).isoformat(), "merchant": "누리고속철도", "category": "교통비", "amount": trans, "filename": "h_t2.png", "confidence": 0.97},
              {"date": (start + dt.timedelta(days=nights)).isoformat(), "merchant": "가상호텔", "category": "숙박비", "amount": lodging, "nights": nights, "filename": "h_h.png", "confidence": 0.96}]
        total = sum(x["amount"] for x in ev)
        return ({"trip_start": start.isoformat(), "trip_end": (start + dt.timedelta(days=nights)).isoformat(),
                 "destination": rng.choice(["대전", "부산", "광주", "구미"]), "purpose": "협력사 기술 협의", "total_amount": total}, ev)
    if proc == "RPT-01":
        end = day - dt.timedelta(days=rng.randint(1, 4))
        return ({"trip_start": (end - dt.timedelta(days=1)).isoformat(), "trip_end": end.isoformat(), "traveler": e["name"],
                 "visit": "협력사", "schedule": "기술 협의", "result": "협의 완료",
                 "follow_ups": [{"task": "회의록 공유", "owner": e["name"], "due": (day + dt.timedelta(days=7)).isoformat()}]}, [])
    if proc == "PUR-01":
        qty, price = rng.choice([(1, 89000), (2, 45000), (3, 120000), (1, 650000), (5, 30000)])
        return ({"item": rng.choice(["키보드", "모니터 암", "외장 SSD", "측정용 케이블", "시험용 지그"]), "qty": qty, "unit_price": price,
                 "amount": qty * price, "reason": "업무용", "due_date": (day + dt.timedelta(days=10)).isoformat()},
                [{"date": day.isoformat(), "merchant": "가상전자상사", "category": "견적서", "amount": qty * price, "filename": "h_q.pdf", "confidence": 1.0}])
    # MTG-01
    md = day + dt.timedelta(days=rng.randint(1, 5))
    while md.weekday() >= 5:          # 회의는 평일에만 잡는다
        md += dt.timedelta(days=1)
    return ({"date": md.isoformat(), "start": "10:00", "end": "11:00", "room": rng.choice(["RM-A", "RM-B"]), "people": rng.randint(3, 6),
             "title": "업무 회의", "purpose": "업무 협의", "external": False}, [])


# ───────────────────────── 출력 ─────────────────────────
def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        if not rows:
            f.write("")
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_all(seed_dir: Path, inputs_dir: Path, font: str | None = None, source_dir: Path = SOURCE_DIR) -> dict:
    from . import receipts, render

    reg, scn = load_sources(source_dir)
    rng = random.Random(scn["seed"])

    org = build_org(scn, rng)
    write_json(seed_dir / "org.json", org)
    write_json(seed_dir / "processes.json", render.processes_json(reg))
    (seed_dir / "regulations.md").write_text(render.regulations_md(reg), encoding="utf-8")
    write_json(seed_dir / "connections.json", scn["connections"])
    write_json(seed_dir / "rooms.json", {"rooms": scn["rooms"], "reservations": scn["room_reservations"]})
    write_json(seed_dir / "calendar.json", {"holidays": scn.get("holidays") or []})

    alloc = build_work_allocation(rng)
    survey = build_survey(org, rng)
    overtime = build_overtime(org, scn, rng)
    write_csv(seed_dir / "dashboard" / "work_allocation.csv", alloc)
    write_csv(seed_dir / "dashboard" / "survey.csv", survey)
    write_csv(seed_dir / "dashboard" / "overtime.csv", overtime)
    write_csv(seed_dir / "dashboard" / "automation_assumptions.csv",
              [{"stage": s, "rate": r, "feature": f} for s, r, f in AUTOMATION])

    history = build_history(org, scn, rng)
    for h in scn["history_fixed"]:
        history.append({"key": h["key"], "process": h["process"], "drafter": h["drafter"], "submitted": h["submitted"],
                        "final": h.get("final"), "values": h["values"], "evidence": h["evidence"],
                        "outcome": h["status"], "reject_reason": None, "delay_factors": [1.0, 1.0, 1.0, 1.0]})
    history.sort(key=lambda x: x["submitted"])
    write_json(seed_dir / "history.json", history)
    write_json(seed_dir / "approver_speed_hours.json", APPROVER_SPEED_HOURS)

    write_json(seed_dir / "demo_state.json", {
        "start": scn["demo"]["start"], "confirm_mode": scn["demo"]["confirm_mode"],
        "digest_times": scn["demo"]["digest_times"], "pending_docs": scn["pending_docs"],
    })

    inputs_dir.mkdir(parents=True, exist_ok=True)
    (inputs_dir / "출장메모_대전.txt").write_text(scn["trip"]["memo"], encoding="utf-8")
    receipts.draw_all(scn["trip"]["receipts"], inputs_dir, font)
    (inputs_dir / "README.txt").write_text(
        "시연 때 김지훈 선임의 AI 대화에 올리는 자료입니다.\n"
        "- 출장메모_대전.txt: 출장 메모(후속 조치 하나의 담당·기한이 비어 있음)\n"
        "- receipt_*.png: 영수증 5장(가상 상호, DEMO 표시)\n"
        "  · 03 숙박비는 1박 상한 초과, 04는 흐린 영수증, 05는 이미 정산된 영수증(중복)\n",
        encoding="utf-8")

    log = render.generation_log(scn, org, alloc, survey, overtime, history)
    (seed_dir / "generation_log.md").write_text(log, encoding="utf-8")

    from .workbook import build_workbook  # 과제 '활용 데이터' 엑셀 (직급별 업무 시간 할당량, 하기 싫은 업무 설문)
    build_workbook(seed_dir / "과제_활용데이터.xlsx", alloc, survey, scn["seed"], scn["demo"]["start"])
    return {"org": org, "history": history, "overtime": overtime, "survey": survey, "alloc": alloc}
