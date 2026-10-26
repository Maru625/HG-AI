"""규정 대응표와 시험 사례 (화면 '새 기안'·'규정'에서 쓴다).

- ARTICLE_FEATURES: 조항마다 이 시스템이 무엇으로 대응하는지. 규정 화면과 docs/specs/08_규정_대응표.md의 근거다.
- presets(): 조항별로 해 볼 수 있는 사례. 화면 작성 양식에 값을 미리 채운다.
  날짜는 시연 시계의 오늘을 기준으로 만든다(시계를 옮겨도 사례가 규정의 의도대로 걸리게).
- 모든 상호·사람은 시연용 더미다.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .models import Reservation

# ───────── 조항별 대응 ─────────
# how: 어디서 판정하는가 (서버 검증 · 결재선 · 알림 · 화면 · MCP)
ARTICLE_FEATURES: dict[str, list[dict]] = {
    "제1조": [{"text": "규정 원문과 프로세스 정의를 한 원본(YAML)에서 만들어 화면·AI가 같은 내용을 읽는다", "how": "규정 화면 · search_rules",
               "cases": []}],
    "제2조": [{"text": "적용 업무 5종: 일반경비 정산, 국내 출장비 정산, 출장결과보고, 물품 구매 요청, 회의실 사용 신청", "how": "list_processes · 새 기안",
               "cases": []}],
    "제3조": [
        {"text": "결재선 자동 계산: 기안자 → 팀장 → 실장 → 본부장, 금액 기준의 전결권자까지만", "how": "결재선(서버)",
         "cases": ["exp01-normal", "exp01-office", "exp01-division"]},
        {"text": "기안자가 팀장이면 팀장 단계를 건너뛴다", "how": "결재선(서버)", "cases": ["exp01-team-head"]},
        {"text": "기안자가 전결권자 본인이라 결재할 사람이 없으면 바로 위 직책자가 결재한다(실장이 기안하면 본부장)", "how": "결재선(서버)",
         "cases": ["rpt01-team-head", "exp01-team-head-small"]},
        {"text": "합의가 필요한 업무는 결재선 끝에 합의 부서장을 둔다(기안자·앞 결재자와 같은 사람이면 생략)", "how": "결재선(서버)",
         "cases": ["pur01-division", "mtg01-large"]},
    ],
    "제4조": [
        {"text": "단계별 처리 기한: 결재 차례가 온 날부터 3영업일. 결재함·결재 검토·기안자 화면에 기한과 경과를 표시", "how": "결재선(서버) · 화면",
         "cases": []},
        {"text": "기한을 넘긴 문서는 '긴급'으로 올리고 결재자에게 즉시 알림(대기 결재 전체를 묶어서)", "how": "중요도 · 알림", "cases": []},
        {"text": "처리할 수 없으면 결재자가 결재 검토 화면에서 사유(와 처리 예정일)를 기안자에게 알린다", "how": "화면 · 기안자 알림",
         "cases": []},
    ],
    "제10조": [
        {"text": "사용한 뒤에 정산: 사용일·영수증 날짜가 오늘보다 뒤면 TIMING 오류", "how": "서버 검증", "cases": ["exp01-before-use"]},
        {"text": "영수증 첨부 필수(EVIDENCE_REQUIRED), 금액 = 영수증 합계(SUM_MISMATCH)", "how": "서버 검증", "cases": ["exp01-sum"]},
        {"text": "전결권자: 30만 원 이하 팀장, 100만 원 이하 실장, 초과 본부장", "how": "결재선(서버)",
         "cases": ["exp01-normal", "exp01-office", "exp01-division"]},
    ],
    "제10조의2": [
        {"text": "회의비면 참석자 명단 필수(REQUIRED_MISSING)", "how": "서버 검증", "cases": ["exp01-attendees"]},
        {"text": "1인당 5만 원 초과면 사유 필요. 사유가 없으면 오류(LIMIT_EXCEEDED)", "how": "서버 검증", "cases": ["exp01-per-person"]},
    ],
    "제11조": [
        {"text": "출장을 마친 뒤 정산: 출장 종료일이 오늘보다 뒤면 TIMING 오류", "how": "서버 검증", "cases": ["exp02-not-ended"]},
        {"text": "정산 비목은 교통비·숙박비·식비", "how": "서버 검증", "cases": ["exp02-category"]},
        {"text": "전결권자: 합계 50만 원 이하 팀장, 초과 실장", "how": "결재선(서버)", "cases": ["exp02-normal", "exp02-office"]},
    ],
    "제11조의2": [
        {"text": "숙박비 1박 상한(사원·선임 10만 원, 책임·수석 12만 원) 초과 → 사유 없으면 정산 불가", "how": "서버 검증(기안자 직급 기준)",
         "cases": ["exp02-lodging"]},
        {"text": "식비 1일 3만 원 초과 → 사유 없으면 정산 불가", "how": "서버 검증", "cases": ["exp02-meal"]},
    ],
    "제12조": [
        {"text": "출장을 마친 뒤 보고: 출장 종료일이 오늘보다 뒤면 TIMING 오류", "how": "서버 검증", "cases": ["rpt01-not-ended"]},
        {"text": "출장 종료 후 5영업일 기한: 지나면 경고(DEADLINE_PASSED), 2영업일 이내면 결재자에게 긴급", "how": "서버 검증 · 중요도",
         "cases": ["rpt01-late"]},
        {"text": "후속 조치마다 담당자와 기한 필수", "how": "서버 검증", "cases": ["rpt01-missing"]},
        {"text": "전결권자 팀장(팀장이 올리면 실장, 제3조)", "how": "결재선(서버)", "cases": ["rpt01-normal", "rpt01-team-head"]},
    ],
    "제13조": [
        {"text": "구매 전에 요청: 희망 납기가 이미 지났으면 TIMING 오류", "how": "서버 검증", "cases": ["pur01-past-due"]},
        {"text": "견적서 첨부 필수(EVIDENCE_REQUIRED·ATTACHMENT_MISSING), 금액 = 수량 × 단가", "how": "서버 검증",
         "cases": ["pur01-no-quote", "pur01-qty"]},
        {"text": "전결권자: 50만 원 이하 팀장, 300만 원 이하 실장, 초과 본부장 + 경영지원팀 합의", "how": "결재선(서버)",
         "cases": ["pur01-team", "pur01-office", "pur01-division"]},
        {"text": "전결 기준 금액의 90% 이상이면 결재자에게 '전결 한도 근접' 주의 항목", "how": "주의 항목(서버)", "cases": ["pur01-near"]},
    ],
    "제14조": [
        {"text": "회의실은 팀장 결재. 정원 초과(ROOM_CAPACITY)·겹치는 예약(ROOM_CONFLICT)·지난 시각(TIMING)은 막는다", "how": "서버 검증",
         "cases": ["mtg01-small", "mtg01-capacity", "mtg01-conflict", "mtg01-past"]},
        {"text": "대회의실·외부인 참석·업무시간 밖 사용 → 경영지원팀 합의. 주말·공휴일 사용도 업무시간 밖으로 본다(해석)", "how": "결재선(서버)",
         "cases": ["mtg01-large", "mtg01-external", "mtg01-evening", "mtg01-weekend"]},
        {"text": "상신하면 가예약, 최종 승인되면 확정, 반려되면 가예약 해제", "how": "예약 상태(서버) · 회의실 화면", "cases": ["mtg01-urgent"]},
    ],
    "제20조": [
        {"text": "첨부 파일이 없는 증빙은 오류(ATTACHMENT_MISSING)", "how": "서버 검증", "cases": ["exp01-no-file"]},
        {"text": "이미 정산한 증빙(같은 기안자의 상신·승인 문서)은 다시 정산할 수 없다(DUPLICATE_EVIDENCE)", "how": "서버 검증 · check_evidence",
         "cases": ["exp01-duplicate"]},
    ],
    "제21조": [
        {"text": "주말·공휴일에 쓴 경비는 사유를 적어야 확인(②)할 수 있다(HOLIDAY_USE)", "how": "서버 검증", "cases": ["exp01-weekend", "exp02-weekend"]},
    ],
    "제22조": [
        {"text": "판독 신뢰도가 낮은 증빙은 확인(②) 때 기안자가 원본과 대조했다고 표시해야 한다(ORIGINAL_CHECK_REQUIRED)", "how": "확인 ② · 화면 · MCP",
         "cases": ["exp01-blurry", "exp02-blurry"]},
        {"text": "결재자에게 '판독 신뢰도가 낮은 값'을 주의 항목으로 강조하고 기안자의 원본 대조 여부를 함께 보여준다", "how": "주의 항목(서버)",
         "cases": ["exp02-blurry"]},
    ],
}


# ───────── 날짜 도우미 ─────────
def _back(today: dt.date, n: int, hol: set[str]) -> dt.date:
    d, k = today, 0
    while k < n:
        d -= dt.timedelta(days=1)
        if clock.is_bday(d, hol):
            k += 1
    return d


def _fwd(today: dt.date, n: int, hol: set[str]) -> dt.date:
    d, k = today, 0
    while k < n:
        d += dt.timedelta(days=1)
        if clock.is_bday(d, hol):
            k += 1
    return d


def _span_back(today: dt.date, nights: int, hol: set[str]) -> tuple[dt.date, dt.date]:
    """오늘 전에 끝난, 주말·공휴일이 끼지 않은 (nights+1)일 출장의 시작·종료일."""
    end = today - dt.timedelta(days=1)
    while True:
        days = [end - dt.timedelta(days=i) for i in range(nights + 1)]
        if all(clock.is_bday(x, hol) for x in days):
            return days[-1], end
        end -= dt.timedelta(days=1)


def _last_weekday(today: dt.date, wd: int) -> dt.date:
    d = today - dt.timedelta(days=1)
    while d.weekday() != wd:
        d -= dt.timedelta(days=1)
    return d


def _next_weekday(today: dt.date, wd: int) -> dt.date:
    d = today + dt.timedelta(days=1)
    while d.weekday() != wd:
        d += dt.timedelta(days=1)
    return d


def _ev(date, merchant, category, amount, filename="", confidence=0.97, nights=None) -> dict:
    e = {"date": str(date), "merchant": merchant, "category": category, "amount": amount, "confidence": confidence}
    if filename:
        e["filename"] = filename
    if nights:
        e["nights"] = nights
    return e


TEAM = ["김지훈", "오세준", "한유진", "정하늘", "조보람", "서윤호", "홍하은", "박서연", "이도현", "외부 자문위원"]


def presets(s: Session) -> list[dict]:
    """사례 목록. 각 사례: id, process_id, label, articles, expect(사람이 읽는 기대 결과), codes(서버가 낼 규칙 코드),
    line(기대 결재선의 단계 이름), user(이 사용자로 해야 의미가 있는 사례면 사번), data(values·evidence·reasons)."""
    hol = clock.holidays(s)
    today = clock.now(s).date()
    d1 = _back(today, 1, hol)
    sat = _last_weekday(today, 5)
    out: list[dict] = []

    def add(pid, cid, label, articles, expect, *, values, evidence=None, reasons=None, codes=(), line=None, user=None):
        out.append({"id": cid, "process_id": pid, "label": label, "articles": list(articles), "expect": expect,
                    "codes": list(codes), "line": line, "user": user,
                    "data": {"values": values, "evidence": evidence or [], "reasons": reasons or {}}})

    # ── EXP-01 일반경비 정산 ──
    sup = {"use_date": str(d1), "merchant": "가상문구 판교점", "amount": 36000, "account": "소모품비", "purpose": "A4 용지와 토너 구매"}
    sup_ev = [_ev(d1, "가상문구 판교점", "소모품비", 36000, "receipt_paper.png", 0.98)]
    add("EXP-01", "exp01-normal", "소모품 3만 6천 원 → 팀장 전결", ["제10조", "제3조"], "검증 통과 · 결재선 팀장",
        values=sup, evidence=sup_ev, line=["팀장"])
    add("EXP-01", "exp01-office", "팀 회식 45만 원(10명) → 실장 전결", ["제10조", "제10조의2"], "검증 통과 · 결재선 팀장 → 실장",
        values={"use_date": str(d1), "merchant": "가상한정식 판교점", "amount": 450000, "account": "회의비",
                "purpose": "과제 착수 후 팀 회식", "attendees": TEAM},
        evidence=[_ev(d1, "가상한정식 판교점", "회의비", 450000, "dinner_kickoff.png")], line=["팀장", "실장"])
    add("EXP-01", "exp01-division", "워크숍 비용 120만 원 → 본부장 전결", ["제10조", "제3조"], "검증 통과 · 결재선 팀장 → 실장 → 본부장",
        values={"use_date": str(d1), "merchant": "가상리조트 연수원", "amount": 1200000, "account": "복리후생비",
                "purpose": "팀 워크숍 숙소·식사"},
        evidence=[_ev(d1, "가상리조트 연수원", "복리후생비", 1200000, "workshop_invoice.pdf", 0.99)], line=["팀장", "실장", "본부장"])
    office_vals = {"use_date": str(d1), "merchant": "가상한정식 판교점", "amount": 450000, "account": "회의비",
                   "purpose": "과제 착수 후 팀 회식", "attendees": TEAM}
    add("EXP-01", "exp01-team-head", "팀장이 올린 회식비 45만 원 → 팀장 단계 생략, 실장 전결", ["제3조"],
        "결재선 실장 (기안자가 팀장이라 팀장 단계를 건너뜀)", values=office_vals,
        evidence=[_ev(d1, "가상한정식 판교점", "회의비", 450000, "dinner_kickoff.png")], line=["실장"], user="E2001")
    add("EXP-01", "exp01-team-head-small", "팀장이 올린 3만 6천 원 정산 → 실장 결재", ["제3조"],
        "결재선 실장 (전결권자인 팀장 본인이 기안해 바로 위 직책자가 결재)", values=sup, evidence=sup_ev, line=["실장"], user="E2001")
    add("EXP-01", "exp01-per-person", "회의비 1인당 6만 원(5만 원 초과)", ["제10조의2"], "한도 초과 오류 → 사유를 넣으면 경고로 바뀜",
        values={"use_date": str(d1), "merchant": "가상스시 판교점", "amount": 180000, "account": "회의비",
                "purpose": "외부 자문 회의 식사", "attendees": ["김지훈", "정하늘", "외부 자문위원"]},
        evidence=[_ev(d1, "가상스시 판교점", "회의비", 180000, "sushi.png")], codes=["LIMIT_EXCEEDED"])
    add("EXP-01", "exp01-attendees", "회의비인데 참석자 명단 없음", ["제10조의2"], "필수 항목 누락 오류(참석자)",
        values={"use_date": str(d1), "merchant": "가상분식 판교점", "amount": 32000, "account": "회의비", "purpose": "주간회의 간식"},
        evidence=[_ev(d1, "가상분식 판교점", "회의비", 32000, "snack.png")], codes=["REQUIRED_MISSING"])
    add("EXP-01", "exp01-weekend", f"주말({sat:%m-%d} 토) 사용 경비", ["제21조"], "휴일 사용 경고 → 사유를 넣어야 확인 ② 가능",
        values={"use_date": str(sat), "merchant": "가상문구 판교점", "amount": 15000, "account": "소모품비", "purpose": "주말 시험 대응 소모품"},
        evidence=[_ev(sat, "가상문구 판교점", "소모품비", 15000, "receipt_weekend.png", 0.98)], codes=["HOLIDAY_USE"])
    add("EXP-01", "exp01-no-file", "영수증 첨부 파일 없음", ["제20조"], "첨부 누락 오류",
        values={**sup, "merchant": "가상문구 판교점", "amount": 12000, "purpose": "필기구 구매"},
        evidence=[_ev(d1, "가상문구 판교점", "소모품비", 12000, "", 0.98)], codes=["ATTACHMENT_MISSING"])
    nxt = _fwd(today, 1, hol)
    add("EXP-01", "exp01-before-use", f"사용 전 정산 시도(사용일 {nxt:%m-%d})", ["제10조"], "시점 오류(사용 후 정산)",
        values={**sup, "use_date": str(nxt), "purpose": "다음 주 행사 소모품(미리 정산)"},
        evidence=[_ev(nxt, "가상문구 판교점", "소모품비", 36000, "receipt_future.png", 0.98)], codes=["TIMING"])
    add("EXP-01", "exp01-sum", "금액 5만 원 ≠ 영수증 합계 4만 5천 원", ["제10조"], "합계 불일치 오류",
        values={**sup, "amount": 50000}, evidence=[_ev(d1, "가상문구 판교점", "소모품비", 45000, "receipt_sum.png", 0.98)],
        codes=["SUM_MISMATCH"])
    add("EXP-01", "exp01-blurry", "흐린 영수증(판독 신뢰도 0.55)", ["제22조"], "판독 신뢰도 경고 → 확인 ② 때 원본 대조 표시 필요",
        values={**sup, "amount": 27000, "purpose": "토너 구매"},
        evidence=[_ev(d1, "가상문구 판교점", "소모품비", 27000, "receipt_blur.png", 0.55)], codes=["LOW_CONFIDENCE"])
    add("EXP-01", "exp01-duplicate", "이미 정산한 영수증(11/4 한밭골 국밥)", ["제20조"], "중복 증빙 오류(기존 문서번호 표시)",
        values={"use_date": "2026-11-04", "merchant": "한밭골 국밥", "amount": 18000, "account": "회의비",
                "purpose": "출장지 점심 회의", "attendees": ["김지훈", "한빛기술연구원 담당자"]},
        evidence=[_ev("2026-11-04", "한밭골 국밥", "회의비", 18000, "receipt_05_lunch_dup.png", 0.94)],
        codes=["DUPLICATE_EVIDENCE"], user="E3001")

    # ── EXP-02 국내 출장비 정산 ──
    t0, t1 = _span_back(today, 1, hol)
    base_trip = {"trip_start": str(t0), "trip_end": str(t1), "destination": "세종", "purpose": "공동과제 실무회의"}

    def trip(evs, **kw):
        return {**base_trip, **kw, "total_amount": sum(e["amount"] for e in evs)}
    ev_ok = [_ev(t0, "누리고속철도", "교통비", 21500, "ktx_go.png"), _ev(t1, "누리고속철도", "교통비", 21500, "ktx_back.png"),
             _ev(t0, "가상호텔 세종점", "숙박비", 92000, "hotel.png", 0.96, nights=1),
             _ev(t0, "가상식당 세종점", "식비", 14000, "meal_1.png")]
    add("EXP-02", "exp02-normal", "1박 2일 출장, 한도 안 → 팀장 전결", ["제11조", "제3조"], "검증 통과 · 결재선 팀장",
        values=trip(ev_ok), evidence=ev_ok, line=["팀장"])
    ev_lodge = ev_ok[:2] + [_ev(t0, "가상호텔 세종점", "숙박비", 115000, "hotel_115.png", 0.96, nights=1)]
    add("EXP-02", "exp02-lodging", "숙박비 1박 11만 5천 원(선임 상한 10만 원)", ["제11조의2"],
        "한도 초과 오류 → 사유를 넣으면 경고",
        values=trip(ev_lodge), evidence=ev_lodge, codes=["LIMIT_EXCEEDED"], user="E3001")
    ev_meal = ev_ok[:2] + [_ev(t0, "가상식당 세종점", "식비", 18000, "lunch.png"), _ev(t0, "가상국밥 세종점", "식비", 16000, "dinner.png")]
    add("EXP-02", "exp02-meal", "하루 식비 3만 4천 원(1일 상한 3만 원)", ["제11조의2"], "한도 초과 오류 → 사유를 넣으면 경고",
        values=trip(ev_meal), evidence=ev_meal, codes=["LIMIT_EXCEEDED"])
    s0, s3 = _span_back(today, 3, hol)
    ev_long = [_ev(s0, "누리고속철도", "교통비", 59800, "ktx_busan_go.png"), _ev(s3, "누리고속철도", "교통비", 59800, "ktx_busan_back.png"),
               _ev(s0, "가상호텔 부산점", "숙박비", 294000, "hotel_3n.png", 0.96, nights=3)]
    ev_long += [_ev(s0 + dt.timedelta(days=i), "가상식당 부산점", "식비", 25000, f"meal_{i + 1}.png") for i in range(4)]
    add("EXP-02", "exp02-office", "3박 4일 부산 출장 51만 3,600원 → 실장 전결", ["제11조"], "검증 통과 · 결재선 팀장 → 실장",
        values={"trip_start": str(s0), "trip_end": str(s3), "destination": "부산", "purpose": "시험장 현장 점검",
                "total_amount": sum(e["amount"] for e in ev_long)}, evidence=ev_long, line=["팀장", "실장"])
    ev_ne = [_ev(d1, "누리고속철도", "교통비", 21500, "ktx_go.png")]
    add("EXP-02", "exp02-not-ended", f"출장 종료({nxt:%m-%d}) 전에 정산", ["제11조"], "시점 오류(출장 종료 후 정산)",
        values={**base_trip, "trip_start": str(d1), "trip_end": str(nxt), "total_amount": 21500}, evidence=ev_ne, codes=["TIMING"])
    fri = sat - dt.timedelta(days=1)
    ev_we = [_ev(fri, "누리고속철도", "교통비", 21500, "ktx_go.png"), _ev(sat, "누리고속철도", "교통비", 21500, "ktx_back.png"),
             _ev(fri, "가상호텔 세종점", "숙박비", 92000, "hotel.png", 0.96, nights=1), _ev(sat, "가상식당 세종점", "식비", 12000, "meal_sat.png")]
    add("EXP-02", "exp02-weekend", f"금·토({fri:%m-%d}~{sat:%m-%d}) 출장, 토요일 영수증", ["제21조"], "휴일 사용 경고(토요일 증빙마다) → 사유 필요",
        values={**base_trip, "trip_start": str(fri), "trip_end": str(sat), "purpose": "주말 현장 시험 지원",
                "total_amount": sum(e["amount"] for e in ev_we)}, evidence=ev_we, codes=["HOLIDAY_USE"])
    ev_blur = ev_ok[:3] + [_ev(t0, "가상식당 세종점", "식비", 14000, "meal_blur.png", 0.6)]
    add("EXP-02", "exp02-blurry", "흐린 식비 영수증(판독 신뢰도 0.6)", ["제22조"], "판독 신뢰도 경고 → 확인 ② 때 원본 대조, 결재자에게 주의 항목",
        values=trip(ev_blur), evidence=ev_blur, codes=["LOW_CONFIDENCE"])
    ev_cat = ev_ok[:2] + [_ev(t0, "가상문구 세종점", "소모품비", 8000, "pen.png")]
    add("EXP-02", "exp02-category", "출장비에 소모품비 영수증", ["제11조"], "비목 오류(교통비·숙박비·식비만)",
        values=trip(ev_cat), evidence=ev_cat, codes=["INVALID_VALUE"])

    # ── RPT-01 출장결과보고 ──
    fu = [{"task": "회의록 공유", "owner": "김지훈", "due": str(_fwd(today, 3, hol))},
          {"task": "시험 일정표 작성", "owner": "오세준", "due": str(_fwd(today, 6, hol))}]
    rpt = {"trip_start": str(t0), "trip_end": str(t1), "traveler": "김지훈 선임", "visit": "가상기술연구소 세종분원",
           "schedule": f"{t0:%m/%d} 실무회의, {t1:%m/%d} 시험장 확인 후 복귀", "result": "시험 범위와 일정 초안을 맞췄다.", "follow_ups": fu}
    add("RPT-01", "rpt01-normal", "기한 안 보고 → 팀장 전결", ["제12조"], "검증 통과 · 결재선 팀장", values=rpt, line=["팀장"])
    add("RPT-01", "rpt01-missing", "후속 조치의 담당·기한 누락", ["제12조"], "필수 항목 누락 오류(담당, 기한)",
        values={**rpt, "follow_ups": [fu[0], {"task": "장비 사양 비교표 작성", "owner": "", "due": ""}]}, codes=["REQUIRED_MISSING"])
    l1 = _back(today, 7, hol)
    l0 = _back(l1, 1, hol)
    add("RPT-01", "rpt01-late", f"출장 종료({l1:%m-%d}) 후 5영업일 지남", ["제12조"], "기한 경과 경고 · 상신하면 결재자에게 긴급",
        values={**rpt, "trip_start": str(l0), "trip_end": str(l1), "schedule": f"{l0:%m/%d}~{l1:%m/%d} 현장 협의"},
        codes=["DEADLINE_PASSED"], line=["팀장"])
    add("RPT-01", "rpt01-not-ended", f"출장 중({nxt:%m-%d} 종료 예정)에 보고", ["제12조"], "시점 오류(출장 종료 후 보고)",
        values={**rpt, "trip_start": str(d1), "trip_end": str(nxt)}, codes=["TIMING"])
    add("RPT-01", "rpt01-team-head", "팀장이 올린 출장결과보고 → 실장 결재", ["제3조", "제12조"],
        "결재선 실장 (전결권자인 팀장 본인이 기안해 바로 위 직책자)",
        values={**rpt, "traveler": "박서연 팀장"}, line=["실장"], user="E2001")

    # ── PUR-01 물품 구매 요청 ──
    due = str(_fwd(today, 10, hol))

    def pur(item, qty, price, reason, amount=None, **kw):
        return {"item": item, "qty": qty, "unit_price": price, "amount": qty * price if amount is None else amount,
                "reason": reason, "due_date": kw.get("due_date", due)}

    def quote(amount, name="quote.pdf"):
        return [_ev(d1, "가상전자상사", "견적서", amount, name, 1.0)]
    add("PUR-01", "pur01-team", "모니터 2대 39만 8천 원 → 팀장 전결", ["제13조"], "검증 통과 · 결재선 팀장",
        values=pur("27인치 모니터", 2, 199000, "시험 데이터 확인용"), evidence=quote(398000), line=["팀장"])
    add("PUR-01", "pur01-office", "노트북 185만 원 → 실장 전결", ["제13조"], "검증 통과 · 결재선 팀장 → 실장",
        values=pur("시험용 노트북", 1, 1850000, "현장 시험 장비 제어"), evidence=quote(1850000, "quote_laptop.pdf"), line=["팀장", "실장"])
    add("PUR-01", "pur01-division", "시험 장비 420만 원 → 본부장 + 경영지원팀 합의", ["제13조", "제3조"],
        "검증 통과 · 결재선 팀장 → 실장 → 본부장 → 경영지원팀 합의",
        values=pur("전력 분석기", 1, 4200000, "시험 항목 추가"), evidence=quote(4200000, "quote_analyzer.pdf"),
        line=["팀장", "실장", "본부장", "합의"])
    add("PUR-01", "pur01-no-quote", "견적서 없이 요청", ["제13조"], "증빙 필요 오류(견적서)",
        values=pur("케이블 정리함", 3, 25000, "실험실 정리"), codes=["EVIDENCE_REQUIRED"])
    add("PUR-01", "pur01-qty", "수량 3 × 단가 2만 5천 원 ≠ 금액 8만 원", ["제13조"], "금액 불일치 오류",
        values=pur("케이블 정리함", 3, 25000, "실험실 정리", amount=80000), evidence=quote(80000, "quote_box.pdf"), codes=["SUM_MISMATCH"])
    add("PUR-01", "pur01-past-due", f"희망 납기({d1:%m-%d})가 이미 지남", ["제13조"], "시점 오류(구매 전에 요청)",
        values=pur("시험용 지그", 2, 60000, "시험 보강", due_date=str(d1)), evidence=quote(120000, "quote_jig.pdf"), codes=["TIMING"])
    add("PUR-01", "pur01-near", "49만 원 구매(전결 기준 50만 원의 98%)", ["제13조"], "검증 통과 · 상신하면 결재자에게 '전결 한도 근접' 주의 항목",
        values=pur("측정 프로브", 2, 245000, "측정 범위 확대"), evidence=quote(490000, "quote_probe.pdf"), line=["팀장"])

    # ── MTG-01 회의실 사용 신청 ──
    m = _fwd(today, 4, hol)

    def mtg(date, start, end, room, people, title, external=False):
        return {"date": str(date), "start": start, "end": end, "room": room, "people": people, "title": title,
                "purpose": "업무 협의", "external": external}
    add("MTG-01", "mtg01-small", "소회의실 B, 업무시간 안 → 팀장 결재", ["제14조"], "검증 통과 · 결재선 팀장 · 상신하면 가예약",
        values=mtg(m, "16:00", "17:00", "RM-B", 5, "시험 결과 검토"), line=["팀장"])
    add("MTG-01", "mtg01-large", "대회의실 사용 → 경영지원팀 합의", ["제14조", "제3조"], "결재선 팀장 → 경영지원팀 합의",
        values=mtg(m, "16:00", "17:00", "RM-L", 14, "분기 성과 공유"), line=["팀장", "합의"])
    add("MTG-01", "mtg01-external", "외부인 참석 회의 → 경영지원팀 합의", ["제14조"], "결재선 팀장 → 경영지원팀 합의",
        values=mtg(m, "10:30", "11:30", "RM-A", 5, "협력사 기술 협의", external=True), line=["팀장", "합의"])
    add("MTG-01", "mtg01-evening", "업무시간 밖(18:30~20:00) → 경영지원팀 합의", ["제14조"], "결재선 팀장 → 경영지원팀 합의",
        values=mtg(m, "18:30", "20:00", "RM-B", 6, "야간 시험 준비 회의"), line=["팀장", "합의"])
    nsat = _next_weekday(today, 5)
    add("MTG-01", "mtg01-weekend", f"토요일({nsat:%m-%d}) 사용 → 업무시간 밖으로 보고 합의", ["제14조"],
        "결재선 팀장 → 경영지원팀 합의 (주말을 업무시간 밖으로 본 해석)",
        values=mtg(nsat, "10:00", "12:00", "RM-B", 6, "주말 시험 준비"), line=["팀장", "합의"])
    u1 = _fwd(today, 1, hol)
    add("MTG-01", "mtg01-urgent", f"내일({u1:%m-%d}) 회의 → 결재자에게 긴급 알림", ["제14조", "제4조"],
        "검증 통과 · 상신하면 회의일이 3영업일 안이라 '긴급', 결재자에게 즉시 알림",
        values=mtg(u1, "16:00", "17:00", "RM-A", 4, "긴급 일정 조율"), line=["팀장"])
    busy = s.scalars(select(Reservation).where(Reservation.status.in_(("HOLD", "CONFIRMED")), Reservation.date > str(today))
                     .order_by(Reservation.date, Reservation.start)).first()
    if busy:
        add("MTG-01", "mtg01-conflict", f"이미 예약된 시간({busy.date[5:]} {busy.start}~{busy.end}, {busy.room_id})", ["제14조"],
            "예약 겹침 오류(확정·가예약 모두)",
            values=mtg(busy.date, busy.start, busy.end, busy.room_id, 4, "겹치는 회의"), codes=["ROOM_CONFLICT"])
    add("MTG-01", "mtg01-capacity", "소회의실 A(6명)에 9명", ["제14조"], "정원 초과 오류",
        values=mtg(m, "13:00", "14:00", "RM-A", 9, "확대 회의"), codes=["ROOM_CAPACITY"])
    add("MTG-01", "mtg01-past", f"지난 시각({d1:%m-%d} 10:00) 신청", ["제14조"], "시점 오류(사용 전에 신청)",
        values=mtg(d1, "10:00", "11:00", "RM-B", 4, "지난 회의"), codes=["TIMING"])
    return out


def by_process(s: Session) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in presets(s):
        out.setdefault(c["process_id"], []).append(c)
    return out


def get(s: Session, case_id: str) -> dict | None:
    return next((c for c in presets(s) if c["id"] == case_id), None)
