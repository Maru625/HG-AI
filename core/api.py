"""REST API (P2 MCP 서버용, 계약 C1). 서비스 계층을 그대로 감싼다.

- 인증: 헤더 X-Approval-Token (연결 토큰 → 사용자·역할)
- 배포 모드(MCP를 같은 포트에 연 경우)에는 헤더 X-Approval-Internal(내부 열쇠)도 있어야 한다. MCP 툴만 REST를 부른다.
- 승인·반려는 열지 않는다(설계서 3.2). 결재 처리는 웹 화면에서만 한다.
"""
from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field

from . import services as S
from .errors import ServiceError


def internal_guard(request: Request, x_approval_internal: str | None = Header(default=None)) -> None:
    """배포 모드에서는 REST API를 MCP 툴(같은 서버 안)만 부를 수 있다. 시드의 연결 토큰은 문서에 공개돼 있어서다."""
    key = getattr(request.app.state, "api_internal_key", None)
    if key and not hmac.compare_digest((x_approval_internal or "").encode(), key.encode()):
        raise ServiceError("UNAUTHORIZED", "배포 모드에서는 REST API를 밖에서 바로 부를 수 없습니다.",
                           "AI 서비스에는 MCP 연결 주소(/mcp/…)를 등록하세요. 밖에서 REST를 꼭 써야 하면 서버를 "
                           "APPROVAL_API_PUBLIC=1로 실행합니다.")


router = APIRouter(prefix="/api", tags=["MCP용 REST API"], dependencies=[Depends(internal_guard)])


def ctx_from_token(x_approval_token: str | None = Header(default=None)) -> S.Ctx:
    info = S.connection_info(x_approval_token)
    return S.Ctx(emp_no=info["emp_no"], channel="MCP", mode=info["mode"])


def ok(d: Any) -> dict:
    return {"ok": True, **d} if isinstance(d, dict) else {"ok": True, "data": d}


class ApprovalLineIn(BaseModel):
    process_id: str
    amount: int | None = None
    dept: str | None = None
    values: dict | None = None


class EvidenceIn(BaseModel):
    process_id: str
    evidence: list[dict] = Field(default_factory=list)
    reasons: dict | None = None


class RoomIn(BaseModel):
    date: str
    start: str
    end: str
    people: int


class PlanIn(BaseModel):
    title: str
    summary: str = ""
    items: list[dict]


class CancelIn(BaseModel):
    reason: str = ""


class DraftsIn(BaseModel):
    drafts: list[dict]


class DocIdsIn(BaseModel):
    doc_ids: list[str]


class ConfirmIn(BaseModel):
    doc_ids: list[str]
    original_checked: dict[str, list[int]] | None = None   # 문서 ID → 원본과 대조한 증빙 번호 (제22조)


@router.get("/connection")
def connection(x_approval_token: str | None = Header(default=None)):
    return ok(S.connection_info(x_approval_token))


@router.get("/whoami")
def whoami(ctx: S.Ctx = Depends(ctx_from_token)):
    return ok(S.whoami(ctx))


@router.get("/processes")
def processes(ctx: S.Ctx = Depends(ctx_from_token)):
    return ok(S.list_processes(ctx))


@router.get("/rules/search")
def rules_search(q: str = Query(""), ctx: S.Ctx = Depends(ctx_from_token)):
    return ok(S.search_rules(ctx, q))


@router.get("/processes/{process_id}/spec")
def process_spec(process_id: str, ctx: S.Ctx = Depends(ctx_from_token)):
    return ok(S.get_process_spec(ctx, process_id))


@router.post("/approval-line")
def approval_line(body: ApprovalLineIn, ctx: S.Ctx = Depends(ctx_from_token)):
    return ok(S.check_approval_line(ctx, body.process_id, body.amount, body.dept, body.values))


@router.post("/evidence/check")
def evidence_check(body: EvidenceIn, ctx: S.Ctx = Depends(ctx_from_token)):
    return ok(S.check_evidence(ctx, body.process_id, body.evidence, body.reasons))


@router.post("/rooms/availability")
def rooms(body: RoomIn, ctx: S.Ctx = Depends(ctx_from_token)):
    return ok(S.check_room_availability(ctx, body.date, body.start, body.end, body.people))


@router.get("/status")
def status(plan_id: str | None = None, doc_id: str | None = None, ctx: S.Ctx = Depends(ctx_from_token)):
    return ok(S.get_status(ctx, plan_id, doc_id))


@router.post("/plans")
def create_plan(body: PlanIn, ctx: S.Ctx = Depends(ctx_from_token)):
    return ok(S.create_plan(ctx, body.title, body.summary, body.items))


@router.post("/plans/{plan_id}/approve")
def approve_plan(plan_id: str, ctx: S.Ctx = Depends(ctx_from_token)):
    if ctx.mode != "drafter":
        raise ServiceError("FORBIDDEN", "기안자용 연결에서만 쓸 수 있는 기능입니다.")
    return ok(S.approve_plan(ctx, plan_id))


@router.post("/plans/{plan_id}/cancel")
def cancel_plan(plan_id: str, body: CancelIn, ctx: S.Ctx = Depends(ctx_from_token)):
    if ctx.mode != "drafter":
        raise ServiceError("FORBIDDEN", "기안자용 연결에서만 쓸 수 있는 기능입니다.")
    return ok(S.cancel_plan(ctx, plan_id, body.reason))


@router.post("/plans/{plan_id}/drafts")
def save_drafts(plan_id: str, body: DraftsIn, ctx: S.Ctx = Depends(ctx_from_token)):
    return ok(S.save_drafts(ctx, plan_id, body.drafts))


@router.post("/drafts/confirm")
def confirm(body: ConfirmIn, ctx: S.Ctx = Depends(ctx_from_token)):
    return ok(S.confirm_drafts(ctx, body.doc_ids, body.original_checked))


@router.post("/drafts/submit")
def submit(body: DocIdsIn, ctx: S.Ctx = Depends(ctx_from_token)):
    return ok(S.submit_drafts(ctx, body.doc_ids))


@router.get("/approvals/pending")
def pending(sort: str = "importance", only: str | None = None, ctx: S.Ctx = Depends(ctx_from_token)):
    return ok(S.list_pending_approvals(ctx, sort, only))


@router.get("/approvals/{doc_id}/brief")
def brief(doc_id: str, ctx: S.Ctx = Depends(ctx_from_token)):
    return ok(S.get_review_brief(ctx, doc_id))
