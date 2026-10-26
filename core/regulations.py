"""규정 지식: 프로세스 정의, 조항 검색, AI용 명세 (설계서 7.1, 7.2).

검색은 키워드 검색이다(설계서 13.2). 임베딩 RAG는 확장 계획으로 둔다.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import ServiceError
from .models import Article, DemoState, Process, Room


def processes(s: Session) -> dict[str, dict]:
    return {p.id: p.data for p in s.scalars(select(Process).order_by(Process.ord))}


def get(s: Session, pid: str) -> dict:
    p = s.get(Process, (pid or "").strip().upper())
    if not p:
        raise ServiceError("UNKNOWN_PROCESS", f"없는 프로세스 ID입니다: {pid}",
                           "list_processes로 프로세스 ID 목록을 확인하세요.")
    return p.data


def common(s: Session) -> dict:
    row = s.get(DemoState, "regulation_common")
    return row.value if row else {}


def articles(s: Session) -> list[Article]:
    return list(s.scalars(select(Article).order_by(Article.ord)))


def _tokens(q: str) -> list[str]:
    toks = [t for t in re.split(r"[\s,./()·?!]+", q or "") if t]
    out = []
    for t in toks:
        t = re.sub(r"(은|는|이|가|을|를|에|의|도|로|으로|와|과|에서|하고|해줘|해|줘|하려면|하면)$", "", t)
        if len(t) >= 2 or t.isdigit():
            out.append(t)
    return out or toks


def search(s: Session, query: str, limit: int = 6) -> list[dict]:
    toks = _tokens(query)
    procs = processes(s)
    linked: dict[str, list[str]] = {}
    for pid, p in procs.items():
        for a in p["articles"]:
            linked.setdefault(a, []).append(pid)
    scored = []
    for a in articles(s):
        names = " ".join(procs[p]["name"] + " " + procs[p]["when"] for p in linked.get(a.id, []))
        score = 0.0
        for t in toks:
            score += 3 * a.title.count(t) + a.body.count(t) + 0.5 * names.count(t)
        if score > 0:
            scored.append((score, a, linked.get(a.id, [])))
    scored.sort(key=lambda x: (-x[0], x[1].ord))
    return [{"article": a.id, "title": a.title, "body": a.body, "processes": pids, "score": round(sc, 1)}
            for sc, a, pids in scored[:limit]]


def spec(s: Session, pid: str) -> dict:
    """get_process_spec 결과. AI가 초안을 쓰는 데 필요한 것을 모두 담는다."""
    p = get(s, pid)
    arts = {a.id: a for a in articles(s)}
    com = common(s)
    bands = []
    for b in p["approval"]["bands"]:
        bands.append({"max_amount": b["max"], "level": b["level"], "agreement": b.get("agreement", []),
                      "label": ("제한 없음" if b["max"] is None else f"{b['max']:,}원 이하") + f" → {b['level']}"})
    out = {
        "process_id": p["id"], "name": p["name"], "when": p["when"], "approval_required": p["approval_required"],
        "fields": p["fields"], "amount_field": p.get("amount_field"),
        "evidence": p.get("evidence", {}),
        "evidence_item_format": {
            "date": "YYYY-MM-DD", "merchant": "상호", "category": "비목 (" + ", ".join(
                p.get("evidence", {}).get("categories") or com.get("evidence_categories", [])) + ")",
            "amount": "정수(원)",
            "filename": "첨부 파일 이름(모르면 '대화 첨부 이미지 3'처럼 어느 첨부인지). 증빙이 필수면 비우지 않는다(제20조)",
            "confidence": "판독 신뢰도 0~1 (AI가 판단). 0.8 미만이면 확인 ② 때 원본 대조 여부를 사용자에게 묻는다(제22조)",
            "nights": "숙박비일 때 박 수"},
        "limits": p.get("limits", {}), "deadline": p.get("deadline"),
        "approval": {"bands": bands, "agreement_if": p["approval"].get("agreement_if", [])},
        "articles": [{"id": a, "title": arts[a].title, "body": arts[a].body} for a in p["articles"] if a in arts],
        "reasons_format": "검증 결과의 reason_key를 키로, 사유 문장을 값으로 save_drafts의 reasons에 넣는다.",
        "example": p.get("example"),
    }
    if pid.upper() == "MTG-01":
        out["rooms"] = [{"id": r.id, "name": r.name, "capacity": r.capacity, "kind": r.kind}
                        for r in s.scalars(select(Room).order_by(Room.id))]
    return out
