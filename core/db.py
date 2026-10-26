"""SQLite 연결과 세션."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_Session: sessionmaker | None = None


def init_engine(db_path: Path | None = None) -> Engine:
    """엔진을 만든다. 시험에서는 임시 경로를 넘긴다."""
    global _engine, _Session
    path = Path(db_path or get_settings().db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(_engine, "connect")
    def _pragma(dbapi_conn, _):  # 여러 프로세스(전결 서버, 시험)가 같은 파일을 써도 되게
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def engine() -> Engine:
    return _engine or init_engine()


def ensure_schema(eng: Engine | None = None) -> list[str]:
    """예전 DB 파일에 새 표·열을 더한다(가벼운 이전). 열을 지우거나 형식을 바꾸지는 않는다.
    초기화(reset) 없이 새 코드로 서버를 켜도 동작하게 하려는 것이다. 더한 열 이름을 돌려준다."""
    from sqlalchemy import inspect, text
    from . import models  # noqa: F401  (표 정의를 Base에 올린다)
    eng = eng or engine()
    Base.metadata.create_all(eng)           # 없는 표만 만든다
    insp = inspect(eng)
    added = []
    with eng.begin() as conn:
        for t in Base.metadata.sorted_tables:
            have = {c["name"] for c in insp.get_columns(t.name)}
            for col in t.columns:
                if col.name not in have:
                    conn.execute(text(f'ALTER TABLE "{t.name}" ADD COLUMN "{col.name}" {col.type.compile(dialect=eng.dialect)}'))
                    added.append(f"{t.name}.{col.name}")
    return added


@contextmanager
def session() -> Iterator[Session]:
    if _Session is None:
        init_engine()
    s = _Session()  # type: ignore[misc]
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
