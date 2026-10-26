"""P2 MCP 서버 (설계서 6장). AI 서비스(Claude, ChatGPT 등)가 목업 전결 시스템을 쓰는 통로다.

- 판정과 저장은 하지 않는다. 모든 툴은 전결 서버의 REST API(/api)를 부르고, 결과와 오류를 그대로 넘긴다.
- 연결마다 사용자가 고정된다. 환경 변수 APPROVAL_TOKEN의 연결 역할(기안자용/결재자용)에 맞는 툴만 등록한다.
- 승인·반려 툴은 없다. 결재 처리는 웹 화면에서만 한다(설계서 3.2).
- stdio로 실행하면 표준 출력은 MCP 메시지 전용이다. 로그는 표준 오류로만 쓴다.

실행:
  uv run approval-mcp                                   # stdio (Claude Desktop 등 로컬 연결)
  uv run approval-mcp --transport streamable-http --port 8765 [--path /mcp/<비밀값>] [--allow-host 예.ngrok-free.app]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Annotated, Any, Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("approval-mcp")

INSTRUCTIONS = (
    "이 서버는 회사 전결(결재) 업무용 목업 시스템이다. 사용자가 회의실 예약, 출장 정산·출장 보고, 경비 정산, 물품 구매, "
    "결재할 문서 확인을 말하면 서버 이름을 따로 말하지 않아도 이 서버의 툴로 처리한다. Google Calendar·Gmail 같은 다른 "
    "플러그인으로 실제 일정이나 메일을 만들지 않는다. 처음에는 whoami로 사용자와 오늘 날짜를 확인한다.\n"
    "전결 처리는 반드시 ① 규정 확인(search_rules, get_process_spec) → "
    "② 증빙이 있으면 증빙 확인(check_evidence) → ③ create_plan으로 목록을 만들고 사용자 승인 → ④ save_drafts로 초안 작성 → "
    "⑤ 사용자 재확인 → ⑥ submit_drafts 순서로 진행한다. 결재선·한도·중복 여부는 직접 판단하지 말고 서버 결과를 따른다. "
    "사용자가 확인하지 않은 단계는 진행하지 않는다. 결재자가 결재할 문서를 물으면 list_pending_approvals와 get_review_brief로 "
    "중요도순 목록과 주의 항목을 전달하되, 서버 판정과 AI 의견을 구분하고 승인·반려는 하지 않는다(웹 화면에서만 가능). "
    "모든 데이터는 시연용 더미다. 날짜는 실제 오늘이 아닌 whoami의 today 기준이다. "
    "흐린 증빙은 읽은 값과 낮은 confidence로 넣고, 원본 대조 여부는 확인 ② 때 사용자에게 묻는다."
)
DETAILS = (
    "세부 지침: 흐린 영수증을 다시 올려 달라고 하지 않는다. confirm_drafts가 ORIGINAL_CHECK_REQUIRED를 돌려주면 "
    "그 증빙의 날짜·상호·금액을 보여주며 원본(종이 영수증·카드 사용 내역)과 맞는지 묻고, 맞다고 하면 original_checked에 "
    "{문서 ID: [증빙 번호]}를 넣어 다시 부른다. 증빙의 filename에는 첨부 파일 이름을 넣고, 이름을 알 수 없으면 "
    "'대화 첨부 이미지 3'처럼 어느 첨부인지 적는다(제20조, 비우면 ATTACHMENT_MISSING). 사유·담당자·기한처럼 사람이 정할 값은 "
    "지어내지 않는다. 자료에 적힌 기관·상호·사람 이름은 그대로 쓰고 실제 이름으로 바꾸지 않는다. "
    "TIMING 오류(사용 전 정산, 지난 시각의 회의실 등)는 날짜를 사용자에게 다시 확인한다."
)

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
WRITE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)


# ───────────────────────── 입력 모델 ─────────────────────────
class EvidenceItem(BaseModel):
    """증빙 한 건. AI가 영수증·견적서 등 자료에서 읽은 값을 넣는다."""
    model_config = ConfigDict(extra="allow")
    date: str = Field(description="사용일 YYYY-MM-DD")
    merchant: str = Field(description="상호")
    category: str = Field(description="비목. get_process_spec의 evidence.categories 중 하나 (예: 교통비, 숙박비, 식비, 회의비, 견적서)")
    amount: int = Field(description="금액(원, 정수)")
    filename: str | None = Field(default=None, description=(
        "첨부한 영수증·견적서 파일 이름. 이름을 알 수 없으면 '대화 첨부 이미지 3'처럼 어느 첨부인지 적는다. "
        "증빙이 필수인 프로세스에서 비우면 ATTACHMENT_MISSING 오류(제20조)"))
    confidence: float | None = Field(default=None, ge=0, le=1, description="판독 신뢰도 0~1. 흐리거나 확실하지 않으면 낮게 적는다")
    nights: int | None = Field(default=None, description="숙박비일 때 박 수")


class PlanItem(BaseModel):
    process_id: str = Field(description="프로세스 ID (list_processes 결과, 예: EXP-02)")
    summary: str = Field(description="이 항목에서 할 일 한 줄 요약")
    sources: str = Field(default="", description="근거 자료 (예: 영수증 5장, 출장 메모)")
    evidence: list[EvidenceItem] = Field(default_factory=list, description="이 항목의 증빙 목록 (없으면 비움)")


class Draft(BaseModel):
    item_no: int = Field(description="create_plan 결과의 항목 번호")
    values: dict[str, Any] = Field(description="get_process_spec의 fields 키에 맞춘 입력값")
    evidence: list[EvidenceItem] = Field(default_factory=list, description="증빙 목록. check_evidence에서 제외 제안된 증빙은 빼고 넣는다")
    reasons: dict[str, str] = Field(default_factory=dict,
                                    description="사유. 검증 결과의 reason_key를 키로, 사용자에게 받은 사유 문장을 값으로 넣는다")
    memo: str = Field(default="", description="메모 (선택)")


# ───────────────────────── 전결 서버 호출 ─────────────────────────
class Api:
    def __init__(self, base: str, token: str, internal_key: str | None = None):
        self.base = base.rstrip("/")
        self.token = token
        self.headers = {"X-Approval-Token": token, **({"X-Approval-Internal": internal_key} if internal_key else {})}

    def _fail(self, code: str, message: str, fix_hint: str = "", details: dict | None = None) -> ToolError:
        return ToolError(json.dumps({"ok": False, "code": code, "message": message, "fix_hint": fix_hint,
                                     "details": details or {}}, ensure_ascii=False))

    async def call(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> dict:
        try:
            async with httpx.AsyncClient(base_url=self.base, timeout=20) as c:
                r = await c.request(method, path, params={k: v for k, v in (params or {}).items() if v is not None},
                                    json=body, headers=self.headers)
        except httpx.HTTPError as e:
            raise self._fail("SERVER_UNAVAILABLE", f"전결 서버({self.base})에 연결할 수 없습니다.",
                             "사용자에게 전결 서버(uv run approval-server)가 켜져 있는지 확인해 달라고 하세요.",
                             {"error": type(e).__name__})
        try:
            data = r.json()
        except ValueError:
            raise self._fail("SERVER_ERROR", f"전결 서버가 알 수 없는 응답을 보냈습니다(HTTP {r.status_code}).")
        if r.status_code >= 400 or (isinstance(data, dict) and data.get("ok") is False):
            if isinstance(data, dict) and "code" in data:
                raise ToolError(json.dumps(data, ensure_ascii=False))
            raise self._fail("SERVER_ERROR", f"HTTP {r.status_code}", details={"body": data})
        return data

    def connection(self) -> dict | None:
        try:
            r = httpx.get(self.base + "/api/connection", headers=self.headers, timeout=5)
            d = r.json()
            return d if d.get("ok") else {"error": d}
        except (httpx.HTTPError, ValueError):
            return None


# ───────────────────────── 툴 등록 ─────────────────────────
def build(api: Api, mode: str, label: str = "") -> MCPServer:
    instructions = INSTRUCTIONS + "\n" + DETAILS + (f"\n\n이 연결: {label}." if label else "") + (
        "\n기안자용 연결이다. 결재 대기 조회 툴은 없다." if mode == "drafter" else
        "\n결재자용 연결이다. 읽기 툴만 있고 기안·상신·승인·반려는 할 수 없다.")
    mcp = MCPServer(name="approval-demo", title="전결 시스템 (목업)", instructions=instructions, version="1.0.0",
                    log_level="WARNING")

    # 공통 읽기 툴 6개
    @mcp.tool(annotations=READ, title="연결된 사용자 확인")
    async def whoami() -> dict:
        """이럴 때 쓴다: 전결 작업을 시작할 때 맨 처음. 이 연결에 고정된 사용자(이름, 사번, 부서, 직급, 팀장)와 시연 날짜(today), 확인 방식(A=채팅 확인, B=화면 확인)을 돌려준다. '지난주', '다음 주 화요일' 같은 날짜는 실제 오늘이 아니라 이 today를 기준으로 계산한다."""
        return await api.call("GET", "/api/whoami")

    @mcp.tool(annotations=READ, title="전결 프로세스 목록")
    async def list_processes() -> dict:
        """이럴 때 쓴다: 사용자의 지시와 자료를 보고 어떤 전결 처리가 필요한지 후보를 찾을 때. 프로세스 ID, 이름, 적용 상황, 결재 필요 여부를 돌려준다."""
        return await api.call("GET", "/api/processes")

    @mcp.tool(annotations=READ, title="전결 규정 검색")
    async def search_rules(query: Annotated[str, Field(description="찾을 낱말 (예: 출장, 숙박, 회의실, 중복)")]) -> dict:
        """이럴 때 쓴다: 판단 근거가 되는 규정 조항이 필요할 때. 조항 번호, 제목, 본문, 관련 프로세스를 돌려준다. 결과가 없으면 다른 낱말로 다시 찾는다."""
        return await api.call("GET", "/api/rules/search", params={"q": query})

    @mcp.tool(annotations=READ, title="프로세스 작성 명세")
    async def get_process_spec(process_id: Annotated[str, Field(description="프로세스 ID (예: EXP-02)")]) -> dict:
        """이럴 때 쓴다: 초안을 쓰기 전에 반드시. 필수 입력 항목(키·이름·형식), 증빙 요건과 형식, 한도, 기한, 결재선 기준, 작성 예시, 근거 조항을 돌려준다. save_drafts의 values는 여기 fields의 키를 쓴다."""
        return await api.call("GET", f"/api/processes/{process_id}/spec")

    @mcp.tool(annotations=READ, title="결재선 확인")
    async def check_approval_line(
        process_id: Annotated[str, Field(description="프로세스 ID")],
        amount: Annotated[int | None, Field(description="금액(원). 금액 기준이 있는 프로세스일 때")] = None,
        dept: Annotated[str | None, Field(description="부서 이름이나 코드. 비우면 연결된 사용자의 부서")] = None,
        values: Annotated[dict[str, Any] | None, Field(description="합의 조건 판단용 입력값(회의실 등). 선택")] = None,
    ) -> dict:
        """이럴 때 쓴다: 결재선을 사용자에게 보여줄 때. 결재자 순서와 합의 부서, 근거 조항을 서버가 계산해 돌려준다. 결재선은 직접 추측하지 않는다."""
        return await api.call("POST", "/api/approval-line",
                              body={"process_id": process_id, "amount": amount, "dept": dept, "values": values})

    @mcp.tool(annotations=READ, title="진행 상황 조회")
    async def get_status(
        plan_id: Annotated[str | None, Field(description="작업 계획 ID (예: PLAN-001)")] = None,
        doc_id: Annotated[str | None, Field(description="문서 ID(DOC-…) 또는 문서번호(ST-…)")] = None,
    ) -> dict:
        """이럴 때 쓴다: 진행 상황이나 반려 사유를 확인할 때. 계획 ID를 주면 계획과 문서들의 상태를, 문서 ID를 주면 그 문서의 상태·결재 진행(단계별 처리 기한, 결재자가 알린 처리 지연 사유)·반려 사유를, 둘 다 비우면 최근 계획을 돌려준다. 화면 링크가 함께 온다."""
        return await api.call("GET", "/api/status", params={"plan_id": plan_id, "doc_id": doc_id})

    if mode == "drafter":
        @mcp.tool(annotations=READ, title="증빙 확인")
        async def check_evidence(
            process_id: Annotated[str, Field(description="프로세스 ID (예: EXP-02)")],
            evidence: Annotated[list[EvidenceItem], Field(description="자료에서 읽은 증빙 전체 (중복이 의심돼도 일단 모두 넣는다)")],
            reasons: Annotated[dict[str, str] | None, Field(description="이미 받은 사유 (reason_key → 사유). 선택")] = None,
        ) -> dict:
            """이럴 때 쓴다: 영수증 등 증빙을 모은 뒤, create_plan을 부르기 전. 흐린 영수증도 원본을 다시 달라고 하지 말고 읽은 값을 넣고 confidence를 낮게 적는다(판독 신뢰도 판정은 서버가 한다). 증빙별로 중복(이미 정산된 문서번호), 한도 초과, 휴일 사용, 판독 신뢰도 낮음을 서버가 판정하고, 제외를 제안하는 증빙과 사유가 필요한 항목(reason_key), 비목별 합계를 돌려준다."""
            return await api.call("POST", "/api/evidence/check", body={
                "process_id": process_id, "evidence": [e.model_dump(exclude_none=True) for e in evidence], "reasons": reasons})

        @mcp.tool(annotations=READ, title="회의실 빈 시간 확인")
        async def check_room_availability(
            date: Annotated[str, Field(description="날짜 YYYY-MM-DD")],
            start: Annotated[str, Field(description="시작 시각 HH:MM")],
            end: Annotated[str, Field(description="종료 시각 HH:MM")],
            people: Annotated[int, Field(description="인원")],
        ) -> dict:
            """이럴 때 쓴다: 회의실 사용 신청(MTG-01) 초안을 쓰기 전. 회의실별 수용 인원, 겹치는 예약(가예약 포함), 합의가 필요한 조건, 추천 회의실을 돌려준다."""
            return await api.call("POST", "/api/rooms/availability", body={"date": date, "start": start, "end": end, "people": people})

        @mcp.tool(annotations=WRITE, title="작업 계획 만들기")
        async def create_plan(
            title: Annotated[str, Field(description="계획 제목 (예: 대전 출장 정리)")],
            summary: Annotated[str, Field(description="사용자 요청 요약")],
            items: Annotated[list[PlanItem], Field(description="처리할 항목 목록. 프로세스 하나당 한 항목")],
        ) -> dict:
            """이럴 때 쓴다: 규정과 증빙을 확인해 필요한 처리 목록이 정해졌을 때. 계획 ID, 항목 번호, 항목별 증빙 확인 결과, 화면 링크를 돌려준다. 만든 뒤에는 목록을 사용자에게 보여주고 승인(확인 ①)을 받는다. 승인 전에는 초안을 쓸 수 없다."""
            return await api.call("POST", "/api/plans", body={
                "title": title, "summary": summary, "items": [i.model_dump(exclude_none=True) for i in items]})

        @mcp.tool(annotations=WRITE_IDEMPOTENT, title="작업 계획 승인 (확인 ①)")
        async def approve_plan(plan_id: Annotated[str, Field(description="작업 계획 ID")]) -> dict:
            """이럴 때 쓴다: 사용자가 대화에서 작업 계획 목록을 승인한다고 분명히 말했을 때만. 사용자 승인 없이 부르지 않는다. 확인 방식이 B(화면 확인형)면 CONFIRM_IN_UI_REQUIRED를 돌려주므로, 그때는 화면 링크를 안내한다."""
            return await api.call("POST", f"/api/plans/{plan_id}/approve")

        @mcp.tool(annotations=DESTRUCTIVE, title="작업 계획 취소")
        async def cancel_plan(
            plan_id: Annotated[str, Field(description="작업 계획 ID")],
            reason: Annotated[str, Field(description="취소 사유")] = "",
        ) -> dict:
            """이럴 때 쓴다: 사용자가 작업 계획을 그만두겠다고 할 때. 상신 전 초안도 함께 취소된다. 이미 상신한 문서는 취소되지 않는다."""
            return await api.call("POST", f"/api/plans/{plan_id}/cancel", body={"reason": reason})

        @mcp.tool(annotations=WRITE, title="초안 저장")
        async def save_drafts(
            plan_id: Annotated[str, Field(description="승인된 작업 계획 ID")],
            drafts: Annotated[list[Draft], Field(description="항목별 초안. 여러 건을 한 번에 넣는다")],
        ) -> dict:
            """이럴 때 쓴다: 작업 계획이 승인된 뒤 항목별 초안을 쓰거나 고칠 때(같은 항목 번호로 다시 부르면 고친다). 반려된 문서의 항목을 다시 저장하면 원문서를 잇는 재기안이 된다. 문서별 상태, 오류·경고, 필요한 사유(missing_reasons), 원본 대조가 필요한 증빙(original_check_needed), 결재선 미리보기, 화면 링크를 돌려준다. 담당자·사유처럼 사람이 정해야 하는 값은 지어내지 말고 사용자에게 묻는다. TIMING 오류는 규정이 정한 시점(사용 후 정산, 출장 종료 후, 구매 전, 사용 전 신청)과 날짜가 맞지 않는다는 뜻이다."""
            return await api.call("POST", f"/api/plans/{plan_id}/drafts",
                                  body={"drafts": [d.model_dump(exclude_none=True) for d in drafts]})

        @mcp.tool(annotations=WRITE_IDEMPOTENT, title="초안 확인 (확인 ②)")
        async def confirm_drafts(
            doc_ids: Annotated[list[str], Field(description="확인할 문서 ID 목록")],
            original_checked: Annotated[dict[str, list[int]] | None, Field(description=(
                "판독이 불확실한 증빙을 사용자가 원본과 대조해 맞다고 답한 경우에만: {문서 ID: [증빙 번호]} (제22조). "
                "사용자에게 묻지 않고 채우지 않는다"))] = None,
        ) -> dict:
            """이럴 때 쓴다: 사용자가 초안 내용(화면 링크 또는 대화에서 보여준 요약)을 보고 맞다고 분명히 말했을 때만. 판독 신뢰도가 낮은 증빙이 있는 문서는 ORIGINAL_CHECK_REQUIRED를 돌려준다. 그때는 그 증빙의 값을 보여주며 원본과 맞는지 사용자에게 묻고, 맞다고 하면 original_checked를 넣어 다시 부른다. 확인 방식이 B면 CONFIRM_IN_UI_REQUIRED를 돌려주므로 임시저장함 화면에서 확인해 달라고 안내한다."""
            body: dict[str, Any] = {"doc_ids": doc_ids}
            if original_checked:
                body["original_checked"] = original_checked
            return await api.call("POST", "/api/drafts/confirm", body=body)

        @mcp.tool(annotations=WRITE, title="상신 (확인 ③)")
        async def submit_drafts(doc_ids: Annotated[list[str], Field(description="상신할 문서 ID 목록 (확인완료 상태)")]) -> dict:
            """이럴 때 쓴다: 확인완료된 초안을 사용자가 올려 달라고 했을 때. 문서별 문서번호, 결재선, 회의실 가예약 결과, 결재자에게 간 알림을 돌려준다. 확인 뒤 내용이 바뀌었으면 MODIFIED_AFTER_CONFIRM을 돌려준다."""
            return await api.call("POST", "/api/drafts/submit", body={"doc_ids": doc_ids})

    if mode == "approver":
        @mcp.tool(annotations=READ, title="결재할 문서 목록")
        async def list_pending_approvals(
            sort: Annotated[Literal["importance", "submitted"], Field(description="정렬: importance(중요도순, 기본) 또는 submitted(상신순)")] = "importance",
            only: Annotated[Literal["긴급", "보통", "낮음"] | None, Field(description="이 중요도만 보기. 선택")] = None,
        ) -> dict:
            """이럴 때 쓴다: 결재자가 결재할 문서를 묻거나 결재 알림을 받은 뒤. 연결된 사용자가 결재할 차례인 문서 전체를 중요도(긴급·보통·낮음)순으로, 중요도 이유·대기 기간·처리 기한(제4조, 결재 차례가 온 날부터 3영업일)·결재/합의 구분·주의 항목 수·화면 링크와 함께 돌려준다. 승인·반려와 처리 지연 알림은 웹 화면에서만 할 수 있다."""
            return await api.call("GET", "/api/approvals/pending", params={"sort": sort, "only": only})

        @mcp.tool(annotations=READ, title="결재 검토 요약")
        async def get_review_brief(doc_id: Annotated[str, Field(description="문서 ID(DOC-…) 또는 문서번호(ST-…)")]) -> dict:
            """이럴 때 쓴다: 결재자가 문서 하나를 검토할 때. 입력값·증빙 요약, 서버가 판정한 주의 항목(규칙, 해당 항목, 근거 조항, 기안자 사유), 결재선 진행, 재상신이면 달라진 점, 화면 링크를 돌려준다. 주의 항목은 서버 판정이므로 AI 의견과 구분해 전달한다."""
            return await api.call("GET", f"/api/approvals/{doc_id}/brief")

    return mcp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="approval-mcp", description="목업 전결 MCP 서버 (P2)")
    ap.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--path", default="/mcp", help="streamable HTTP 경로. 공개 주소로 열 때는 추측하기 어려운 값을 넣는다")
    ap.add_argument("--allow-host", action="append", default=[], help="공개 주소의 호스트 이름(터널 주소 등). 여러 번 쓸 수 있다")
    a = ap.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="[approval-mcp] %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = os.environ.get("APPROVAL_TOKEN", "")
    base = os.environ.get("APPROVAL_SERVER", "http://localhost:8000")
    if not token:
        log.error("APPROVAL_TOKEN 환경 변수가 없습니다. `uv run approval-server connections`로 설정 예를 보세요.")
        return 2
    api = Api(base, token)
    info = api.connection()
    mode, label = os.environ.get("APPROVAL_ROLE", ""), ""
    if info and "error" not in info:
        mode, label = info["mode"], info.get("label", "")
    elif info and "error" in info:
        log.error("연결 토큰이 맞지 않습니다: %s", info["error"].get("message"))
        return 2
    else:
        log.warning("전결 서버(%s)에 연결할 수 없어 APPROVAL_ROLE(%s)로 툴을 등록합니다.", base, mode or "없음")
    if mode not in ("drafter", "approver"):
        log.error("연결 역할을 알 수 없습니다. 전결 서버를 먼저 켜거나 APPROVAL_ROLE=drafter|approver를 넣으세요.")
        return 2
    log.info("연결: %s (%s) → 전결 서버 %s", label or token, mode, base)

    mcp = build(api, mode, label)
    if a.transport == "stdio":
        mcp.run("stdio")
    else:
        sec = None
        if a.allow_host:
            hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"] + a.allow_host + [f"{h}:*" for h in a.allow_host]
            sec = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                            allowed_origins=[f"https://{h}" for h in a.allow_host])
        log.info("streamable HTTP: http://%s:%d%s", a.host, a.port, a.path)
        mcp.run("streamable-http", host=a.host, port=a.port, streamable_http_path=a.path, transport_security=sec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
