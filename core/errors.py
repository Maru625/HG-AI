"""오류 형식 통일: ok, code, message, fix_hint (설계서 6.4)."""
from __future__ import annotations

# 설계서 6.4 오류 코드 초안 11개 + 구현에 필요해 더한 코드 6개(★)
ERROR_CODES: dict[str, tuple[int, str]] = {
    "UNKNOWN_PROCESS": (404, "없는 프로세스 ID입니다."),
    "PLAN_NOT_APPROVED": (409, "작업 계획이 아직 승인되지 않았습니다."),
    "PLAN_CANCELED": (409, "취소된 작업 계획입니다."),
    "VALIDATION_FAILED": (422, "검증 오류가 있습니다."),
    "REASON_REQUIRED": (422, "사유가 필요한 항목에 사유가 없습니다."),
    "DUPLICATE_EVIDENCE": (422, "이미 정산된 증빙입니다."),
    "NOT_CONFIRMED": (409, "사용자 재확인(확인완료)이 끝나지 않은 문서입니다."),
    "MODIFIED_AFTER_CONFIRM": (409, "확인 뒤에 수정된 문서입니다."),
    "CONFIRM_IN_UI_REQUIRED": (409, "화면 확인형 모드입니다. 이 확인은 웹 화면에서만 할 수 있습니다."),
    "ROOM_CONFLICT": (409, "같은 시간대에 예약(가예약 포함)이 있습니다."),
    "NOT_IN_APPROVAL_LINE": (403, "연결된 사용자가 결재선에 없는 문서입니다."),
    "ALREADY_SUBMITTED": (409, "이미 상신된 문서는 고칠 수 없습니다."),            # ★
    "NOT_FOUND": (404, "대상을 찾을 수 없습니다."),                                # ★
    "FORBIDDEN": (403, "이 연결 또는 사용자에게 허용되지 않은 작업입니다."),          # ★
    "BAD_REQUEST": (400, "입력 형식이 잘못되었습니다."),                            # ★
    "UNAUTHORIZED": (401, "연결 토큰이 없거나 잘못되었습니다."),                     # ★
    "ORIGINAL_CHECK_REQUIRED": (409, "판독이 불확실한 증빙을 기안자가 원본과 대조했는지 확인이 필요합니다."),  # ★ 제22조
}


class ServiceError(Exception):
    def __init__(self, code: str, message: str | None = None, fix_hint: str = "", details=None):
        status, default = ERROR_CODES.get(code, (400, "오류"))
        super().__init__(message or default)
        self.code = code
        self.status = status
        self.message = message or default
        self.fix_hint = fix_hint
        self.details = details

    def to_dict(self) -> dict:
        d = {"ok": False, "code": self.code, "message": self.message, "fix_hint": self.fix_hint}
        if self.details is not None:
            d["details"] = self.details
        return d
