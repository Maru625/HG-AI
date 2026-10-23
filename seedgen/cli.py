"""approval-seed: P4 생성기 명령.

  uv run approval-seed build   # seed/ 와 demo_inputs/ 를 만들고 검사한다
  uv run approval-seed check   # 검사만 한다
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="approval-seed", description="P4 더미 데이터·시연 자료 생성기")
    ap.add_argument("command", choices=["build", "check"])
    ap.add_argument("--seed-dir", default="seed")
    ap.add_argument("--inputs-dir", default="demo_inputs")
    ap.add_argument("--font", default=None, help="영수증용 한글 글꼴 경로 (.ttf/.ttc)")
    a = ap.parse_args(argv)
    seed_dir, inputs_dir = Path(a.seed_dir), Path(a.inputs_dir)

    from .checker import run_checks
    if a.command == "build":
        from .generate import build_all
        build_all(seed_dir, inputs_dir, a.font)
        print(f"시드 파일: {seed_dir.resolve()}")
        print(f"시연 입력 자료: {inputs_dir.resolve()}")
    results = run_checks(seed_dir, inputs_dir)
    failed = 0
    for passed, msg in results:
        print(("  [통과] " if passed else "  [실패] ") + msg)
        failed += 0 if passed else 1
    print(f"검사: {len(results) - failed}/{len(results)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
