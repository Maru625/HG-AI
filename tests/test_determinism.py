"""같은 시드면 같은 결과 (계획서 P4 완료 기준)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import inspect, select

from core import db, seed_loader
from core.config import get_settings


def _dump() -> dict:
    """비교용 DB 내용. 실제 시각이 들어가는 칸(감사 기록의 real_at, 시연 시계의 real_start)은 뺀다."""
    out = {}
    eng = db._engine
    with db.session() as s:
        for table in db.Base.metadata.sorted_tables:
            cols = [c.name for c in table.columns if c.name not in ("real_at",)]
            rows = s.execute(select(*[table.c[c] for c in cols])).all()
            rows = [tuple(r) for r in rows]
            if table.name == "demo_state":
                rows = [r for r in rows if r[0] != "clock"]
            out[table.name] = sorted(map(repr, rows))
    assert inspect(eng).get_table_names()
    return out


def test_reset_is_deterministic():
    st = get_settings()
    db.init_engine(st.db_path)
    seed_loader.reset(st.seed_dir)
    a = _dump()
    seed_loader.reset(st.seed_dir)
    b = _dump()
    assert a == b
    assert len(a["documents"]) >= 160


def _hashes(d: Path) -> dict:
    return {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*")) if p.is_file() and p.suffix != ".xlsx"}


def test_seed_build_is_deterministic(tmp_path):
    from seedgen.generate import build_all
    build_all(tmp_path / "s1", tmp_path / "i1")
    build_all(tmp_path / "s2", tmp_path / "i2")
    assert _hashes(tmp_path / "s1") == _hashes(tmp_path / "s2")
    assert _hashes(tmp_path / "i1") == _hashes(tmp_path / "i2")
    # 저장소의 시드와도 같다 (생성기를 고친 뒤 build를 다시 돌렸는지)
    repo = Path(get_settings().seed_dir)
    assert _hashes(tmp_path / "s1") == _hashes(repo)
