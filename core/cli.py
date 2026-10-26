"""approval-server: 전결 서버 명령.

  uv run approval-server            # 전결 서버 실행 (웹 화면 http://localhost:8000)
  uv run approval-server reset      # DB를 비우고 시드를 적재, 시연 시계를 맞춘다
  uv run approval-server connections  # MCP 연결 토큰과 Claude Desktop 설정 예를 보여준다
  uv run approval-server gpt          # ChatGPT 시연: 전결 서버 + MCP(HTTP, 연결 전부) + 터널을 한 번에
  uv run approval-server deploy       # 서버 배포: 웹 화면 + REST + MCP(연결 전부)를 한 포트에서. 주소는 APPROVAL_BASE_URL
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="approval-server", description="전결 서버 (P1 + P3)")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "reset", "connections", "gpt", "deploy"])
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--mcp-port", type=int, default=8765, help="gpt: MCP(HTTP) 포트")
    ap.add_argument("--tunnel", choices=["auto", "cloudflared", "ngrok", "none"], default="none",
                    help="gpt: 공개 주소를 여는 터널(ChatGPT 웹에서 등록할 때만 필요). auto = 설치된 것을 찾아 쓴다")
    ap.add_argument("--ngrok-domain", default=None, help="gpt: ngrok 고정 도메인(있으면 주소가 바뀌지 않음)")
    ap.add_argument("--secret", default=None, help="gpt: MCP 주소 비밀값(기본: data/mcp_secret.txt에 한 번 만들어 계속 씀)")
    ap.add_argument("--lan", action="store_true",
                    help="같은 와이파이·사내망의 다른 PC도 들어오게 연다(0.0.0.0, 화면 링크는 이 PC의 IP 주소)")
    a = ap.parse_args(argv)
    if a.lan:
        ip = lan_ip()
        port = a.port or int(os.environ.get("APPROVAL_PORT", "8000"))
        os.environ.setdefault("APPROVAL_HOST", "0.0.0.0")
        os.environ.setdefault("APPROVAL_BASE_URL", f"http://{ip}:{port}")
        os.environ["APPROVAL_LAN_IP"] = ip

    from . import db, seed_loader
    from .config import get_settings
    st = get_settings()

    if a.command == "reset":
        db.init_engine()
        stats = seed_loader.reset(st.seed_dir)
        print(f"초기화 완료: {st.db_path}")
        print(f"  과거 결재 이력 {stats['history']}건, 결재 대기 {stats['pending']}건 적재")
        return 0

    if a.command == "connections":
        conns = json.loads((st.seed_dir / "connections.json").read_text(encoding="utf-8"))
        cfg = {"mcpServers": {}}
        exe = Path(sys.executable).parent / ("approval-mcp.exe" if os.name == "nt" else "approval-mcp")
        for c in conns:
            name = "approval-" + (c.get("slug") or c["mode"])
            env = {"APPROVAL_TOKEN": c["token"], "APPROVAL_SERVER": st.base_url, "APPROVAL_ROLE": c["mode"]}
            if exe.exists():   # 가상환경의 실행 파일을 바로 띄운다(uv·경로 설정에 덜 흔들림)
                cfg["mcpServers"][name] = {"command": str(exe), "args": [], "env": env}
            else:
                cfg["mcpServers"][name] = {"command": "uv", "args": ["--directory", str(st.home), "run", "approval-mcp"], "env": env}
            print(f"- {c['label']}: {c['token']}")
        print("\nClaude Desktop 설정(claude_desktop_config.json)에 넣을 내용:\n")
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
        return 0

    if a.command == "gpt":
        from . import gpt
        return gpt.main(a)

    if a.command == "deploy":
        return deploy(a, st)

    if not st.db_path.exists():
        print("DB가 없어 먼저 초기화합니다.")
        db.init_engine()
        seed_loader.reset(st.seed_dir)
    import uvicorn
    from .app import create_app
    print(f"전결 서버: {st.base_url}  (끝내려면 Ctrl+C)")
    uvicorn.run(create_app(), host=a.host or st.host, port=a.port or st.port, log_level="warning")
    return 0


def lan_ip() -> str:
    """이 PC의 사내망·와이파이 IP. 밖으로 패킷을 보내지 않고(UDP connect) 기본 경로의 주소만 읽는다."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def deploy(a, st) -> int:
    """한 포트 배포. 컨테이너·클라우드에서 포트 하나만 열 수 있을 때 쓴다."""
    import uvicorn

    from . import db, seed_loader
    from .app import MCP_INFO, create_app
    host = a.host or os.environ.get("APPROVAL_HOST", "0.0.0.0")
    port = a.port or st.port
    if a.port and a.port != st.port:
        os.environ["APPROVAL_INTERNAL_URL"] = os.environ.get("APPROVAL_INTERNAL_URL", f"http://127.0.0.1:{a.port}")
    if not st.db_path.exists():
        print("DB가 없어 먼저 초기화합니다.")
        db.init_engine()
        seed_loader.reset(st.seed_dir)
    app = create_app(mount_mcp=True)
    print(f"전결 서버(배포): {st.base_url}  ← APPROVAL_BASE_URL (듣는 주소 {host}:{port})")
    if st.base_url.startswith("http://localhost") or st.base_url.startswith("http://127."):
        print("[주의] APPROVAL_BASE_URL이 로컬 주소입니다. 공개 주소(https://…)로 넣어야 툴 결과의 화면 링크가 맞습니다.")
    for c in MCP_INFO.get("connections", []):
        print(f"  MCP {'[기본] ' if c['default'] else ''}{c['name']}: {c['url']}")
    if MCP_INFO.get("token_url"):
        print(f"  (헤더 토큰 방식) {MCP_INFO['token_url']} + Authorization: Bearer <헤더 토큰>  ← 토큰은 시연 제어 패널(/demo)")
    if not os.environ.get("APPROVAL_ADMIN_KEY"):
        print("[주의] APPROVAL_ADMIN_KEY가 없어 누구나 시연 제어 패널(초기화·시계)을 쓸 수 있습니다.")
    uvicorn.run(app, host=host, port=port, log_level="warning", proxy_headers=True, forwarded_allow_ips="*")
    return 0


if __name__ == "__main__":
    sys.exit(main())
