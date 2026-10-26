"""데이터 모델 (설계서 8.1). 상태 값은 설계서 5장과 같다.

문서 상태: INVALID(작성중) · VALID(작성완료) · CONFIRMED(확인완료) · SUBMITTED(결재대기) · APPROVED(승인) · REJECTED(반려) · CANCELED(취소, 계획 취소 시)
계획 상태: PENDING(승인대기) · APPROVED(승인됨) · CANCELED(취소)
예약 상태: HOLD(가예약) · CONFIRMED(확정) · RELEASED(해제). 가위바위보에서 이긴 초안은 상신 전에도 HOLD(note에 표시)
결재 단계: WAITING(앞 단계 대기) · PENDING(결재 차례) · APPROVED · REJECTED · SKIPPED(반려로 중단)
"""
from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Department(Base):
    __tablename__ = "departments"
    code: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)          # division / office / team
    parent: Mapped[str | None] = mapped_column(String, nullable=True)
    head: Mapped[str | None] = mapped_column(String, nullable=True)


class Employee(Base):
    __tablename__ = "employees"
    emp_no: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    grade: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String, default="")
    dept: Mapped[str] = mapped_column(String)


class Connection(Base):
    """MCP 연결 토큰. 연결마다 사용자와 역할이 고정된다(설계서 6.4, 13.2)."""
    __tablename__ = "connections"
    token: Mapped[str] = mapped_column(String, primary_key=True)
    emp_no: Mapped[str] = mapped_column(String)
    mode: Mapped[str] = mapped_column(String)          # drafter / approver
    label: Mapped[str] = mapped_column(String, default="")


class Article(Base):
    __tablename__ = "articles"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    ord: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)


class Process(Base):
    __tablename__ = "processes"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    ord: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSON)


class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    capacity: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String)          # small / large


class Reservation(Base):
    __tablename__ = "reservations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[str] = mapped_column(String)
    date: Mapped[str] = mapped_column(String)
    start: Mapped[str] = mapped_column(String)
    end: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    emp_no: Mapped[str] = mapped_column(String)
    doc_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)        # HOLD / CONFIRMED / RELEASED
    note: Mapped[str | None] = mapped_column(Text, nullable=True)   # 가위바위보 승리·패배 등 (재미 기능)


class Duel(Base):
    """회의실 가위바위보 대결 (재미 기능, 규정 밖). 겹치는 시간에 신청한 사람(도전자)이 기존 예약자(수비자)에게 도전한다.
    상태: WAITING(고르는 중) · DONE(결판) · CANCELED(도전 취소). winner: challenger / defender."""
    __tablename__ = "duels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[str] = mapped_column(String)
    date: Mapped[str] = mapped_column(String)
    start: Mapped[str] = mapped_column(String)
    end: Mapped[str] = mapped_column(String)
    challenger: Mapped[str] = mapped_column(String, index=True)
    challenger_doc: Mapped[str] = mapped_column(String)
    defender: Mapped[str] = mapped_column(String, index=True)
    defender_res: Mapped[int] = mapped_column(Integer)
    defender_doc: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)
    round: Mapped[int] = mapped_column(Integer, default=1)
    c_pick: Mapped[str | None] = mapped_column(String, nullable=True)
    d_pick: Mapped[str | None] = mapped_column(String, nullable=True)
    rounds: Mapped[list] = mapped_column(JSON, default=list)        # [{round, c, d, result: challenger/defender/tie}]
    winner: Mapped[str | None] = mapped_column(String, nullable=True)
    how: Mapped[str | None] = mapped_column(String, nullable=True)   # rps / yield / cancel
    seen: Mapped[list] = mapped_column(JSON, default=list)          # 결과를 본 사람(사번)
    created_at: Mapped[str] = mapped_column(String)
    decided_at: Mapped[str | None] = mapped_column(String, nullable=True)
    rev: Mapped[int] = mapped_column(Integer, default=0)            # 바뀔 때마다 1씩(화면 갱신 판단용)


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(Text, default="")
    drafter: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    created_via: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
    approved_at: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_via: Mapped[str | None] = mapped_column(String, nullable=True)
    canceled_at: Mapped[str | None] = mapped_column(String, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class PlanItem(Base):
    __tablename__ = "plan_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String)
    item_no: Mapped[int] = mapped_column(Integer)
    process_id: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(Text)
    sources: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    evidence_check: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Document(Base):
    """문서. 증빙은 evidence 열에 목록으로 둔다(항목: date, merchant, category, amount, filename, confidence, nights)."""
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    doc_no: Mapped[str | None] = mapped_column(String, nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String, nullable=True)
    item_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    process_id: Mapped[str] = mapped_column(String)
    drafter: Mapped[str] = mapped_column(String)
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    reasons: Mapped[dict] = mapped_column(JSON, default=dict)
    memo: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String)
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    confirmed_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    original_doc_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_via: Mapped[str] = mapped_column(String)
    updated_via: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
    submitted_at: Mapped[str | None] = mapped_column(String, nullable=True)
    final_at: Mapped[str | None] = mapped_column(String, nullable=True)
    reject_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reject_code: Mapped[str | None] = mapped_column(String, nullable=True)
    is_history: Mapped[bool] = mapped_column(Boolean, default=False)
    # 제22조: 판독이 불확실한 증빙을 기안자가 원본과 대조했다고 확인한 증빙 번호(확인 ② 때). 내용을 고치면 비운다.
    original_checked: Mapped[list] = mapped_column(JSON, default=list)
    original_checked_at: Mapped[str | None] = mapped_column(String, nullable=True)


class ApprovalStep(Base):
    __tablename__ = "approval_steps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(String, index=True)
    seq: Mapped[int] = mapped_column(Integer)
    approver: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)          # 결재 / 합의
    level: Mapped[str] = mapped_column(String)         # 팀장 / 실장 / 본부장 / 합의
    article: Mapped[str] = mapped_column(String, default="")
    basis: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    acted_at: Mapped[str | None] = mapped_column(String, nullable=True)
    # 제4조: 결재자가 기한 안에 처리할 수 없을 때 기안자에게 알린 사유와 처리 예정일
    delay_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    delay_until: Mapped[str | None] = mapped_column(String, nullable=True)
    delay_at: Mapped[str | None] = mapped_column(String, nullable=True)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipient: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)          # 결재자: IMMEDIATE / DIGEST · 기안자: RESULT(승인·반려) / DELAY(처리 지연, 제4조) · 모두: DUEL(가위바위보 결과)
    created_at: Mapped[str] = mapped_column(String)
    trigger: Mapped[str] = mapped_column(Text, default="")
    docs: Mapped[list] = mapped_column(JSON, default=list)
    read: Mapped[bool] = mapped_column(Boolean, default=False)


class NotifyMark(Base):
    """긴급 즉시 알림을 보낸 문서. 같은 문서로 즉시 알림을 두 번 보내지 않는다(설계서 7.6)."""
    __tablename__ = "notify_marks"
    recipient: Mapped[str] = mapped_column(String, primary_key=True)
    doc_id: Mapped[str] = mapped_column(String, primary_key=True)
    level: Mapped[str] = mapped_column(String)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[str] = mapped_column(String)            # 시연 시계 시각
    real_at: Mapped[str] = mapped_column(String)
    channel: Mapped[str] = mapped_column(String)       # MCP / WEB / SYSTEM
    actor: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    target: Mapped[str] = mapped_column(String, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(String, default="ok")


class DemoState(Base):
    __tablename__ = "demo_state"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


# ── 대시보드용 더미 데이터 (설계서 8.2) ──
class WorkAllocation(Base):
    __tablename__ = "work_allocation"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    grade_group: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    stage: Mapped[str] = mapped_column(String)
    hours_per_week: Mapped[float] = mapped_column(Float)


class SurveyResponse(Base):
    __tablename__ = "survey"
    resp_id: Mapped[str] = mapped_column(String, primary_key=True)
    dept: Mapped[str] = mapped_column(String)
    grade_group: Mapped[str] = mapped_column(String)
    rank1: Mapped[str] = mapped_column(String)
    rank2: Mapped[str] = mapped_column(String)
    rank3: Mapped[str] = mapped_column(String)
    hours_per_week_self: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)


class OvertimeRecord(Base):
    __tablename__ = "overtime"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String)
    emp_no: Mapped[str] = mapped_column(String)
    dept: Mapped[str] = mapped_column(String)
    hours: Mapped[float] = mapped_column(Float)
    cause_category: Mapped[str] = mapped_column(String)
    cause_stage: Mapped[str] = mapped_column(String)


class AutomationAssumption(Base):
    __tablename__ = "automation_assumptions"
    stage: Mapped[str] = mapped_column(String, primary_key=True)
    rate: Mapped[float] = mapped_column(Float)
    feature: Mapped[str] = mapped_column(Text)
