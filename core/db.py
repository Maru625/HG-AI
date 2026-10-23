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
