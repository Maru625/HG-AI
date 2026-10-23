"""규정 원본(YAML)에서 사람용 문서와 기계용 정의를 만들고, 생성 기록을 쓴다."""
from __future__ import annotations

from collections import Counter

TYPE_LABEL = {"date": "날짜(YYYY-MM-DD)", "text": "글", "int": "정수(원/개)", "enum": "선택", "list": "목록",
              "items": "항목 목록", "time": "시각(HH:MM)", "bool": "예/아니오", "room": "회의실 ID"}


def processes_json(reg: dict) -> dict:
    """P1이 읽는 기계용 정의. 원본의 구조를 그대로 쓰되, 조항 본문을 함께 싣는다."""
    return {
        "company": reg["company"], "title": reg["title"], "effective": reg["effective"],
        "grades": reg["grades"], "business_hours": reg["business_hours"],
        "confidence_threshold": reg["confidence_threshold"], "evidence_categories": reg["evidence_categories"],
        "articles": reg["articles"], "processes": reg["processes"],
    }


def _won(v) -> str:
    return "제한 없음" if v is None else f"{v:,}원 이하"


def regulations_md(reg: dict) -> str:
    out = [f"# {reg['title']}", "", f"- 회사: {reg['company']} (가상)", f"- 시행: {reg['effective']}",
           "- 이 문서는 `seedgen/source/regulations.yaml`에서 자동으로 만든다. 직접 고치지 말고 원본을 고친다.", "",
           "## 조항", ""]
    for a in reg["articles"]:
        out += [f"### {a['id']} ({a['title']})", "", a["body"].strip(), ""]
    out += ["## 프로세스 정의", ""]
    for p in reg["processes"]:
        out += [f"### {p['id']} {p['name']}", "", f"- 언제: {p['when']}", f"- 설계 깊이: {p['depth']}",
                f"- 근거 조항: {', '.join(p['articles'])}", ""]
        out += ["| 항목 | 키 | 형식 | 필수 | 설명 |", "|---|---|---|---|---|"]
        for f in p["fields"]:
            req = "예" if f.get("required") else ("조건부" if f.get("required_if") else "아니오")
            note = f.get("help", "")
            if f.get("options"):
                note = (note + " " if note else "") + "선택지: " + ", ".join(f["options"])
            if f.get("required_if"):
                k, v = next(iter(f["required_if"].items()))
                note = (note + " " if note else "") + f"{k}={v}일 때 필수"
            if f.get("item_fields"):
                note = (note + " " if note else "") + "각 항목: " + ", ".join(
                    f"{x['label']}({x['key']})" for x in f["item_fields"])
            out.append(f"| {f['label']} | `{f['key']}` | {TYPE_LABEL.get(f['type'], f['type'])} | {req} | {note} |")
        ev = p.get("evidence", {})
        out += ["", f"- 증빙: {ev.get('label', '없음')} ({'필수' if ev.get('required') else '선택'})"]
        if p.get("limits"):
            lim = p["limits"]
            if "lodging_per_night" in lim:
                out.append("- 숙박비 1박 상한: " + ", ".join(f"{g} {v:,}원" for g, v in lim["lodging_per_night"].items()))
            if "meal_per_day" in lim:
                out.append(f"- 식비 1일 상한: {lim['meal_per_day']:,}원")
            if "per_person_meal" in lim:
                out.append(f"- 회의비(식사·회식) 1인당 상한: {lim['per_person_meal']:,}원")
        if p.get("deadline"):
            out.append(f"- 처리 기한: {p['deadline']['from_field']} 후 {p['deadline']['business_days']}영업일")
        bands = p["approval"]["bands"]
        out.append("- 전결 기준: " + " / ".join(
            f"{_won(b['max'])} {b['level']}" + (f" + {', '.join(b['agreement'])} 합의" if b.get("agreement") else "")
            for b in bands))
        for c in p["approval"].get("agreement_if", []):
            out.append(f"- 합의 조건: {c['label']} → {c['dept']} 합의")
        out.append("")
    return "\n".join(out) + "\n"


def generation_log(scn: dict, org: dict, alloc: list, survey: list, overtime: list, history: list) -> str:
    oc = Counter(r["cause_category"] for r in overtime)
    hc = Counter(h["process"] for h in history)
    rej = sum(1 for h in history if h["outcome"] == "REJECTED")
    lines = [
        "# 생성 기록", "",
        f"- seed: {scn['seed']}", f"- 시연 시작 시각(시연 시계): {scn['demo']['start']}",
        f"- 직원 수: {len(org['employees'])}명 (등장 인물 {len(scn['org']['named_people'])}명 + 생성)",
        "", "## 가정", "",
        "- 직급별 업무 시간 할당량: 직급군별 기본값(seedgen/generate.py의 BASE_ALLOCATION)에 ±5% 잡음을 더했다.",
        "- 설문: 120명. 직급군별로 1~3순위 선택 가중치를 다르게 두었다(결재 검토는 팀장 이상에서 높게).",
        "- 야근 기록: 2026-09-14 ~ 2026-11-06 영업일. 팀별 야근 확률과 원인 가중치는 generate.py의 build_overtime에 있다.",
        "- 과거 결재 이력: 2026-08-17 ~ 2026-11-10, 반려 확률 16%. 결재 단계별 처리 시간은 결재자별 평균(approver_speed_hours.json) × 문서별 배율.",
        "- 자동화 효과 가정표의 절감률은 가정값이다. 실측은 파일럿에서 한다(설계서 8.2, 13.2).",
        "- 하루 1시간 같은 특정 수치에 맞추려고 값을 고치지 않았다.",
        "", "## 결과 요약", "",
        f"- 할당량 행: {len(alloc)}", f"- 설문 응답: {len(survey)}",
        f"- 야근 기록: {len(overtime)}건 (" + ", ".join(f"{k} {v}" for k, v in oc.most_common()) + ")",
        f"- 과거 결재 이력: {len(history)}건, 반려 {rej}건 (" + ", ".join(f"{k} {v}" for k, v in sorted(hc.items())) + ")",
        "",
    ]
    return "\n".join(lines)
