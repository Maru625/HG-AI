"""시험 공통 준비. 시험은 임시 DB를 쓰고, 시연용 data/approval.db는 건드리지 않는다."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="approval-test-"))
os.environ["APPROVAL_HOME"] = str(ROOT)
os.environ["APPROVAL_DB"] = str(_TMP / "test.db")
os.environ["APPROVAL_TICK_SECONDS"] = "3600"

import pytest  # noqa: E402

from core import db, seed_loader, services as S  # noqa: E402
from core.config import get_settings  # noqa: E402
from core.errors import ServiceError  # noqa: E402

DRAFTER = S.Ctx("E3001", channel="MCP", mode="drafter")       # 김지훈 선임 (기안자용 연결)
APPROVER = S.Ctx("E2001", channel="MCP", mode="approver")     # 박서연 팀장 (결재자용 연결)
WEB_APPROVER = S.Ctx("E2001", channel="WEB")
WEB_DRAFTER = S.Ctx("E3001", channel="WEB")


def caller(ctx: S.Ctx):
    """서비스 함수를 MCP 툴처럼 부른다. 오류는 {ok: False, code…} 사전으로 돌려준다."""
    def call(tool: str, **kw):
        try:
            return getattr(S, tool)(ctx, **kw)
        except ServiceError as e:
            return e.to_dict()
    return call


@pytest.fixture()
def fresh():
    """시드 상태로 초기화한 DB."""
    db.init_engine(get_settings().db_path)
    seed_loader.reset(get_settings().seed_dir)
    yield


@pytest.fixture()
def drafter(fresh):
    return caller(DRAFTER)


@pytest.fixture()
def approver(fresh):
    return caller(APPROVER)
