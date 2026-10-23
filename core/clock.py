"""시연 시계와 영업일 계산 (설계서 5.4).

시간에 따라 달라지는 판단은 모두 now()를 쓴다. 실제 시계(datetime.now)를 직접 쓰지 않는다.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from .models import DemoState


def _state(s: Session, key: str, default=None):
    row = s.get(DemoState, key)
    return row.value if row else default


def set_state(s: Session, key: str, value) -> None:
    row = s.get(DemoState, key)
    if row:
        row.value = value
    else:
        s.add(DemoState(key=key, value=value))


def now(s: Session) -> dt.datetime:
    c = _state(s, "clock")
    real = dt.datetime.now()
    if not c:
        return real.replace(microsecond=0)
    demo_start = dt.datetime.fromisoformat(c["demo_start"])
    real_start = dt.datetime.fromisoformat(c["real_start"])
    return (demo_start + (real - real_start)).replace(microsecond=0)


def set_clock(s: Session, when: dt.datetime) -> None:
    set_state(s, "clock", {"demo_start": when.isoformat(), "real_start": dt.datetime.now().isoformat()})


def advance(s: Session, minutes: int) -> dt.datetime:
    set_clock(s, now(s) + dt.timedelta(minutes=minutes))
    return now(s)


def iso(t: dt.datetime) -> str:
    return t.replace(microsecond=0).isoformat()


# ── 영업일 ──
def holidays(s: Session) -> set[str]:
    return set(_state(s, "holidays", []) or [])


def is_bday(d: dt.date, hol: set[str]) -> bool:
    return d.weekday() < 5 and d.isoformat() not in hol


def add_bdays(d: dt.date, n: int, hol: set[str]) -> dt.date:
    while n > 0:
        d += dt.timedelta(days=1)
        if is_bday(d, hol):
            n -= 1
    return d


def bdays_until(today: dt.date, target: dt.date, hol: set[str]) -> int:
    """today 다음 날부터 target까지의 영업일 수. target이 지났으면 음수."""
    if target < today:
        return -bdays_until(target, today, hol)
    n, d = 0, today
    while d < target:
        d += dt.timedelta(days=1)
        if is_bday(d, hol):
            n += 1
    return n


def add_work_hours(t: dt.datetime, hours: float, hol: set[str], start: int = 9, end: int = 18) -> dt.datetime:
    """업무 시간(09~18시, 영업일)만 세어 hours만큼 더한다. 과거 결재 이력의 처리 시각을 만들 때 쓴다."""
    remaining = dt.timedelta(hours=hours)
    cur = t
    while remaining > dt.timedelta(0):
        if not is_bday(cur.date(), hol) or cur.hour >= end:
            cur = dt.datetime.combine(cur.date() + dt.timedelta(days=1), dt.time(start))
            continue
        if cur.hour < start:
            cur = dt.datetime.combine(cur.date(), dt.time(start))
        day_end = dt.datetime.combine(cur.date(), dt.time(end))
        step = min(remaining, day_end - cur)
        cur += step
        remaining -= step
    return cur
