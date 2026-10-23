"""MCP 툴 명세를 코드에서 뽑아 문서로 쓴다 (docs/specs/04_MCP_툴_명세.md).

  uv run python -m mcp_server.export_spec [출력 경로]

툴 이름·설명·입력 스키마·주석(readOnlyHint 등)을 서버 코드에서 그대로 읽으므로, 문서와 코드가 어긋나지 않는다.
전결 서버가 꺼져 있어도 된다(툴 등록만 하고 부르지 않는다).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from .server import INSTRUCTIONS, Api, build

ROLE_LABEL = {"drafter": "기안자용 연결", "approver": "결재자용 연결"}


def _type(p: dict, defs: dict) -> str:
    if "$ref" in p:
        return p["$ref"].split("/")[-1]
    if "anyOf" in p:
        return " 또는 ".join(_type(x, defs) for x in p["anyOf"] if x.get("type") != "null") + " (선택)"
    if "enum" in p:
        return " | ".join(map(str, p["enum"]))
    t = p.get("type", "")
    if t == "array":
        return f"{_type(p.get('items', {}), defs)} 목록"
    if t == "object":
        return "객체"
    return {"string": "문자열", "integer": "정수", "number": "숫자", "boolean": "참/거짓"}.get(t, t or "?")


def _params(schema: dict) -> list[str]:
    defs = schema.get("$defs", {})
    req = set(schema.get("required", []))
    rows = []
    for name, p in schema.get("properties", {}).items():
        rows.append(f"| `{name}` | {_type(p, defs)} | {'필수' if name in req else '선택'} | {p.get('description', '')} |")
    return rows


async def collect() -> dict:
    out = {}
    for mode in ("drafter", "approver"):
        srv = build(Api("http://localhost:8000", "-"), mode)
        out[mode] = [t.model_dump(by_alias=True, exclude_none=True) for t in await srv.list_tools()]
    return out


def render(tools: dict) -> str:
    names = {m: [t["name"] for t in ts] for m, ts in tools.items()}
    common = [n for n in names["drafter"] if n in names["approver"]]
    lines = ["# 04. MCP 툴 명세", "",
             "> 이 문서는 `uv run python -m mcp_server.export_spec`로 서버 코드에서 만든다. 손으로 고치지 않는다.", "",
             "## 서버 지침 (instructions)", "", f"> {INSTRUCTIONS}", "",
             f"- 글자 수: {len(INSTRUCTIONS)}자 (앞 512자 안에 핵심을 담는다, 설계서 6.1)",
             "- 연결별로 끝에 연결 이름과 역할 안내 한 줄이 붙는다.", "",
             "## 연결별 툴 범위", "",
             "| 연결 | 툴 수 | 툴 |", "|---|---|---|"]
    for m in ("drafter", "approver"):
        lines.append(f"| {ROLE_LABEL[m]} | {len(names[m])} | {', '.join(f'`{n}`' for n in names[m])} |")
    lines += ["", f"- 공통 {len(common)}개: {', '.join(f'`{n}`' for n in common)}",
              "- 승인·반려 툴은 어느 연결에도 없다. 결재 처리는 웹 화면에서만 한다(설계서 3.2).", "",
              "## 오류 응답", "",
              "툴이 실패하면 `isError: true`와 함께 아래 형식의 JSON 문자열을 돌려준다(앞에 SDK가 붙이는 `Error executing tool …:` 머리말이 있다).", "",
              "```json", '{"ok": false, "code": "PLAN_NOT_APPROVED", "message": "…", "fix_hint": "다음에 할 일", "details": {}}', "```", "",
              "- 전결 서버 판정 오류: 설계서 6.4의 11개 + `ALREADY_SUBMITTED`, `NOT_FOUND`, `FORBIDDEN`, `BAD_REQUEST`, `UNAUTHORIZED`",
              "- MCP 서버가 붙이는 오류: `SERVER_UNAVAILABLE`(전결 서버에 연결할 수 없음), `SERVER_ERROR`(알 수 없는 응답)",
              "- 입력 형식이 스키마와 다르면 SDK가 필드 이름과 함께 검증 오류를 돌려준다.", "",
              "## 툴 상세", ""]
    seen = set()
    for m in ("drafter", "approver"):
        for t in tools[m]:
            if t["name"] in seen:
                continue
            seen.add(t["name"])
            ann = t.get("annotations", {})
            where = "공통" if t["name"] in common else ROLE_LABEL[m]
            kind = "읽기" if ann.get("readOnlyHint") else ("쓰기 · 되돌리기 어려움" if ann.get("destructiveHint") else "쓰기")
            lines += [f"### `{t['name']}` — {t.get('title', '')}", "",
                      f"- 연결: {where} · 종류: {kind} · 주석: `{json.dumps(ann, ensure_ascii=False)}`", "",
                      t.get("description", ""), ""]
            rows = _params(t.get("inputSchema", {}))
            if rows:
                lines += ["| 입력 | 형식 | 필수 | 설명 |", "|---|---|---|---|", *rows, ""]
            else:
                lines += ["입력 없음", ""]
            defs = t.get("inputSchema", {}).get("$defs", {})
            for dn, d in defs.items():
                key = (t["name"], dn)
                lines += [f"`{dn}` 항목", "", "| 필드 | 형식 | 필수 | 설명 |", "|---|---|---|---|", *_params(d), ""]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    out = Path(argv[0]) if argv else Path("docs/specs/04_MCP_툴_명세.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(asyncio.run(collect())), encoding="utf-8")
    print(f"썼습니다: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
