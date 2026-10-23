"""설정. 프로젝트 폴더의 .env 를 읽고, 없으면 기본값을 쓴다(계획서 3.4)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    home: Path
    db_path: Path
    seed_dir: Path
    host: str
    port: int
    base_url: str
    tick_seconds: float


@lru_cache
def get_settings() -> Settings:
    home = Path(os.environ.get("APPROVAL_HOME", os.getcwd())).resolve()
    _load_dotenv(home / ".env")
    host = os.environ.get("APPROVAL_HOST", "127.0.0.1")
    port = int(os.environ.get("APPROVAL_PORT", "8000"))
    return Settings(
        home=home,
        db_path=Path(os.environ.get("APPROVAL_DB", home / "data" / "approval.db")),
        seed_dir=Path(os.environ.get("APPROVAL_SEED_DIR", home / "seed")),
        host=host,
        port=port,
        base_url=os.environ.get("APPROVAL_BASE_URL", f"http://localhost:{port}").rstrip("/"),
        tick_seconds=float(os.environ.get("APPROVAL_TICK_SECONDS", "15")),
    )
