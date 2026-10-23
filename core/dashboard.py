"""홈 대시보드 지표 (설계서 8.2, 9장). 예상 절감 시간은 가정값으로 계산한 추정치이며 목표와 비교하지 않는다."""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .models import (ApprovalStep, AutomationAssumption, Department, Document, Employee, OvertimeRecord,
                     SurveyResponse, WorkAllocation)

STAGES = ["처리 파악", "증빙 준비", "문서 작성", "반려 후 재작성", "진행 확인·문의", "결재 검토"]
TOP = ["본업", "회의", "전결 처리", "기타 사무"]
GRADE_GROUPS = ["사원", "선임", "책임", "수석", "팀장 이상"]

REJECT_LABEL = {"EVIDENCE_REQUIRED": "증빙 누락", "SUM_MISMATCH": "합계 불일치", "LIMIT_EXCEEDED": "한도 초과 사유 부족",
                "REQUIRED_MISSING": "필수 항목 누락", "DEADLINE_PASSED": "기한 경과", "DUPLICATE_EVIDENCE": "중복 증빙"}


def _grade_group(e: Employee) -> str:
    return "팀장 이상" if e.title else e.grade


def build(s: Session) -> dict:
    now = clock.now(s)
    teams = [d for d in s.scalars(select(Department).where(Department.type == "team"))]

    # 1. 부서별 야근 시간과 원인 (상위 범주, 최근 8주)
    ot = list(s.scalars(select(OvertimeRecord)))
    by_dept: dict[str, Counter] = defaultdict(Counter)
    stage_ot: Counter = Counter()
    for r in ot:
        by_dept[r.dept][r.cause_category] += r.hours
        if r.cause_stage:
            stage_ot[r.cause_stage] += r.hours
    weeks = 8
    overtime = {"depts": [t.name for t in teams], "categories": TOP,
                "hours": {t.name: [round(by_dept[t.name][c], 1) for c in TOP] for t in teams},
                "jeongyeol_share": {t.name: round(by_dept[t.name]["전결 처리"] / max(1e-9, sum(by_dept[t.name].values())), 3)
                                    for t in teams},
                "stage_hours": [round(stage_ot[st], 1) for st in STAGES], "weeks": weeks}

    # 2. 직급별 전결 단계 시간 (주당)
    alloc = list(s.scalars(select(WorkAllocation)))
    stage_by_grade = {g: [0.0] * len(STAGES) for g in GRADE_GROUPS}
    total_by_grade: Counter = Counter()
    for a in alloc:
        total_by_grade[a.grade_group] += a.hours_per_week
        if a.category == "전결 처리" and a.stage in STAGES:
            stage_by_grade[a.grade_group][STAGES.index(a.stage)] = a.hours_per_week
    allocation = {"grades": GRADE_GROUPS, "stages": STAGES, "hours": stage_by_grade,
                  "jeongyeol_total": {g: round(sum(v), 1) for g, v in stage_by_grade.items()},
                  "week_total": {g: round(total_by_grade[g], 1) for g in GRADE_GROUPS}}

    # 3. 기피 업무 순위 (1순위 3점, 2순위 2점, 3순위 1점)
    score: Counter = Counter()
    first: Counter = Counter()
    sv = list(s.scalars(select(SurveyResponse)))
    for r in sv:
        score[r.rank1] += 3
        score[r.rank2] += 2
        score[r.rank3] += 1
        first[r.rank1] += 1
    survey = {"n": len(sv), "ranking": [{"option": k, "score": v, "first": first[k]} for k, v in score.most_common()]}

    # 4. 반려율과 반려 사유 (과거 이력 + 진행 문서)
    finals = list(s.scalars(select(Document).where(Document.status.in_(("APPROVED", "REJECTED")))))
    rej = [d for d in finals if d.status == "REJECTED"]
    reasons = Counter(REJECT_LABEL.get(d.reject_code or "", "기타") for d in rej)
    rejection = {"total": len(finals), "rejected": len(rej), "rate": round(len(rej) / max(1, len(finals)), 3),
                 "reasons": [{"label": k, "count": v} for k, v in reasons.most_common()]}

    # 5. 결재자별 대기 건수와 평균 처리 시간
    steps = list(s.scalars(select(ApprovalStep).where(ApprovalStep.acted_at.is_not(None), ApprovalStep.started_at.is_not(None))))
    hol = clock.holidays(s)
    proc_hours: dict[str, list[float]] = defaultdict(list)
    for st in steps:
        a, b = dt.datetime.fromisoformat(st.started_at), dt.datetime.fromisoformat(st.acted_at)
        proc_hours[st.approver].append(_work_hours_between(a, b, hol))
    pending = Counter(s.scalars(select(ApprovalStep.approver).join(Document, Document.id == ApprovalStep.doc_id)
                                .where(ApprovalStep.status == "PENDING", Document.status == "SUBMITTED")))
    approvers = []
    for emp_no in sorted(set(proc_hours) | set(pending)):
        e = s.get(Employee, emp_no)
        hrs = proc_hours.get(emp_no, [])
        approvers.append({"emp_no": emp_no, "name": e.name if e else emp_no, "title": e.title if e else "",
                          "pending": pending.get(emp_no, 0), "avg_hours": round(sum(hrs) / len(hrs), 1) if hrs else None,
                          "handled": len(hrs)})
    approvers.sort(key=lambda x: (-x["pending"], -(x["avg_hours"] or 0)))

    # 6. 예상 절감 시간 (가정): 직급군별 전결 단계 시간 × 절감률 ÷ 주 5일
    rates = {a.stage: (a.rate, a.feature) for a in s.scalars(select(AutomationAssumption))}
    staff = list(s.scalars(select(Employee).where(Employee.dept.like("T-%"))))
    mix = Counter(_grade_group(e) for e in staff)
    n_staff = sum(mix.values()) or 1
    savings_stage = []
    for i, st in enumerate(STAGES):
        rate, feat = rates.get(st, (0.0, ""))
        weekly = sum(stage_by_grade[g][i] * mix[g] for g in GRADE_GROUPS) / n_staff
        savings_stage.append({"stage": st, "rate": rate, "feature": feat, "hours_week_avg": round(weekly, 2),
                              "minutes_day_saved": round(weekly * rate / 5 * 60, 1)})
    savings_grade = {g: round(sum(stage_by_grade[g][i] * rates.get(st, (0, ""))[0] for i, st in enumerate(STAGES)) / 5 * 60, 1)
                     for g in GRADE_GROUPS}
    savings = {"by_stage": savings_stage, "by_grade_minutes_day": savings_grade,
               "avg_minutes_day": round(sum(x["minutes_day_saved"] for x in savings_stage), 1),
               "note": "절감률은 가정값이다. 목표치와 비교하지 않고 단계별 추정만 보여준다(설계서 8.2)."}

    return {"now": clock.iso(now), "overtime": overtime, "allocation": allocation, "survey": survey,
            "rejection": rejection, "approvers": approvers, "savings": savings}


def _work_hours_between(a: dt.datetime, b: dt.datetime, hol: set[str]) -> float:
    if b <= a:
        return 0.0
    total, cur = 0.0, a
    while cur.date() <= b.date():
        if clock.is_bday(cur.date(), hol):
            ds = dt.datetime.combine(cur.date(), dt.time(9))
            de = dt.datetime.combine(cur.date(), dt.time(18))
            lo, hi = max(ds, a), min(de, b)
            if hi > lo:
                total += (hi - lo).total_seconds() / 3600
        cur = dt.datetime.combine(cur.date() + dt.timedelta(days=1), dt.time(0))
    return round(total, 1)
