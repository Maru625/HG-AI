"""`approval-server gpt`: ChatGPT 시연을 명령 하나로 띄운다.

1) 전결 서버 + 웹 화면 (이미 켜져 있으면 그것을 쓴다)
2) MCP 서버: 연결 전부(시연 기본 drafter·approver + 여러 사례용)를 한 포트에서 streamable HTTP로
   (http://127.0.0.1:8765/mcp/<비밀값>/<slug>, slug는 seed/connections.json)
3) 터널(선택, --tunnel): cloudflared 또는 ngrok로 공개 HTTPS 주소를 받는다. ChatGPT '웹'에서 등록할 때만 필요하다.
   ChatGPT 데스크톱 앱(Work)의 'MCP 서버 추가'는 이 컴퓨터에서 바로 붙으므로 로컬 주소로 충분하다(10/20 실기 확인).
4) 등록할 주소를 화면에 보여주고 data/gpt_connect.json에 남긴다(시연 제어 패널에도 보임)

끝내려면 Ctrl+C. 터널도 함께 닫힌다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn

from .config import get_settings

log = logging.getLogger("approval-gpt")
URL_RX = re.compile(r"https://[a-zA-Z0-9.-]+\.(?:trycloudflare\.com|ngrok-free\.app|ngrok-free\.dev|ngrok\.app|ngrok\.dev|ngrok\.io)")


def _port_open(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def find_tool(name: str) -> str | None:
    """PATH에서 찾고, 없으면 Windows의 흔한 설치 위치를 본다."""
    env = os.environ.get(name.upper() + "_PATH")
    if env and Path(env).exists():
        return env
    p = shutil.which(name)
    if p:
        return p
    if os.name == "nt":
        cands = [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "cloudflared" / "cloudflared.exe",
                 Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "cloudflared" / "cloudflared.exe",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe",
                 get_settings().home / "tools" / f"{name}.exe"]
        for c in cands:
            if c.exists():
                return str(c)
    return None


class Tunnel:
    """cloudflared / ngrok 하위 프로세스. 출력에서 공개 주소를 찾는다."""

    def __init__(self, kind: str, exe: str, port: int, domain: str | None = None):
        self.kind, self.exe, self.port, self.domain = kind, exe, port, domain
        self.proc: subprocess.Popen | None = None
        self.url: str | None = None
        self.lines: list[str] = []
        self._found = threading.Event()

    def start(self, timeout: float = 60) -> str | None:
        target = f"http://127.0.0.1:{self.port}"
        if self.kind == "cloudflared":
            cmd = [self.exe, "tunnel", "--no-autoupdate", "--url", target]
        else:
            cmd = [self.exe, "http", target, "--log", "stdout", "--log-format", "logfmt"]
            if self.domain:
                cmd += ["--url", self.domain]
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace", creationflags=flags)
        threading.Thread(target=self._read, daemon=True).start()
        self._found.wait(timeout)
        return self.url

    def _read(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.lines.append(line.rstrip())
            if not self.url:
                m = URL_RX.search(line)
                if m and "api.trycloudflare.com" not in m.group(0):
                    self.url = m.group(0)
                    self._found.set()
        self._found.set()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def _box(lines: list[str]) -> str:
    w = max(len(x) for x in lines) + 2
    return "\n".join(["=" * w, *lines, "=" * w])


async def run(args) -> int:
    from mcp_server.http_multi import bearer_token, build_app, conn_name, conn_slug, load_connections, load_or_create_secret

    st = get_settings()
    out_file = st.db_path.parent / "gpt_connect.json"
    servers: list[uvicorn.Server] = []

    # 1) 전결 서버
    if _port_open("127.0.0.1", st.port):
        print(f"전결 서버가 이미 {st.base_url}에서 실행 중입니다. 그것을 씁니다.")
    else:
        from . import db, seed_loader
        from .app import create_app
        if not st.db_path.exists():
            db.init_engine()
            seed_loader.reset(st.seed_dir)
        servers.append(uvicorn.Server(uvicorn.Config(create_app(), host=st.host, port=st.port, log_level="warning")))

    # 2) MCP (연결 전부, 한 포트)
    secret = load_or_create_secret(st.db_path.parent / "mcp_secret.txt", args.secret)
    conns = load_connections(st.seed_dir)
    app, paths = build_app(st.base_url, conns, secret, check_host=False)
    if _port_open("127.0.0.1", args.mcp_port):
        ours = False
        try:
            import httpx
            ours = "approval-demo MCP ok" in httpx.get(f"http://127.0.0.1:{args.mcp_port}/healthz", timeout=2).text
        except Exception:
            pass
        if ours:
            print(f"[알림] 이 시연의 MCP 서버가 이미 {args.mcp_port} 포트에서 실행 중입니다(이전 창이 아직 살아 있음).")
            print("  그대로 써도 시연은 됩니다. 주소는 같습니다:")
            for c in conns:
                print(f"    {conn_name(c)}: http://127.0.0.1:{args.mcp_port}{paths[conn_slug(c)]}")
            print("  새로 띄우려면 이전 검은 창을 닫거나, 작업 관리자에서 approval-server.exe·cloudflared.exe를 끝낸 뒤 다시 실행하세요.")
        else:
            print(f"[오류] MCP 포트 {args.mcp_port}을 다른 프로그램이 쓰고 있습니다. --mcp-port로 바꾸세요.")
        return 2
    lan = os.environ.get("APPROVAL_LAN_IP") if getattr(args, "lan", False) else None
    servers.append(uvicorn.Server(uvicorn.Config(app, host="0.0.0.0" if lan else "127.0.0.1", port=args.mcp_port,
                                                 log_level="warning")))

    # 3) 터널
    tunnel = None
    public = None
    kind = args.tunnel
    if kind == "auto":
        kind = "cloudflared" if find_tool("cloudflared") else ("ngrok" if find_tool("ngrok") else "none")
    if kind != "none":
        exe = find_tool(kind)
        if not exe:
            print(f"[오류] {kind}를 찾을 수 없습니다. 설치하거나 {kind.upper()}_PATH 환경 변수에 실행 파일 경로를 넣으세요.")
            return 2
        tunnel = Tunnel(kind, exe, args.mcp_port, args.ngrok_domain)
        import atexit
        atexit.register(tunnel.stop)

    async def announce():
        nonlocal public
        while not all(getattr(s, "started", False) for s in servers):
            await asyncio.sleep(0.2)
        if tunnel:
            print(f"{tunnel.kind} 터널을 여는 중입니다(최대 60초)...")
            public = await asyncio.to_thread(tunnel.start)
            if not public:
                print("[오류] 터널 주소를 받지 못했습니다. 터널 프로그램의 마지막 출력:")
                print("\n".join(tunnel.lines[-15:]))
        local = f"http://127.0.0.1:{args.mcp_port}"
        info = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "tunnel": tunnel.kind if tunnel else None,
                "public": bool(public), "web": st.base_url,
                "token_url": local + "/mcp", "public_token_url": (public + "/mcp") if public else None,
                "connections": [{"mode": c["mode"], "slug": conn_slug(c), "name": conn_name(c), "label": c.get("label", ""),
                                 "default": i < 2, "local_url": local + paths[conn_slug(c)],
                                 "bearer": bearer_token(secret, conn_slug(c)),
                                 "lan_url": f"http://{lan}:{args.mcp_port}{paths[conn_slug(c)]}" if lan else None,
                                 "url": (public + paths[conn_slug(c)]) if public else None} for i, c in enumerate(conns)]}
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = ["ChatGPT 시연 준비 완료", "",
                 f"웹 화면(발표 PC 브라우저): {st.base_url}", "",
                 "ChatGPT 데스크톱 앱: 설정 > 플러그인 > 추가 > MCP 서버 추가 > 스트리밍 가능한 HTTP"]
        for c in info["connections"]:
            if c["default"]:
                lines.append(f"  [{c['label'] or c['mode']}] 이름 {c['name']}")
                lines.append(f"    {c['local_url']}")
        extra = [c for c in info["connections"] if not c["default"]]
        if extra:
            lines += ["", "여러 사례용 추가 연결 (필요한 것만 등록. 대화마다 연결은 하나만 켠다):"]
            for c in extra:
                lines.append(f"  [{c['label']}] 이름 {c['name']}")
                lines.append(f"    {c['local_url']}")
        if lan:
            lines += ["", f"같은 와이파이의 다른 PC: 웹 화면 {st.base_url}  (AI 연결 주소는 웹의 'AI 연결' 메뉴)"]
            for c in info["connections"]:
                if c["default"]:
                    lines.append(f"  [{c['label'] or c['mode']}] {c['lan_url']}")
            if not os.environ.get("APPROVAL_SITE_KEY"):
                lines.append("  [주의] 입장 코드(APPROVAL_SITE_KEY)가 없어 같은 망의 누구나 화면을 열 수 있습니다.")
        if public:
            lines += ["", "ChatGPT 웹(Plugins > Add custom MCP server)용 공개 주소:"]
            for c in info["connections"]:
                lines.append(f"  [{c['label'] or c['mode']}] {c['url']}")
        lines += ["", "인증: 없음 — 주소 안의 비밀값이 토큰 역할. 비밀값은 data/mcp_secret.txt에 남아 재시작해도 같다.",
                  f"(헤더 토큰 방식) {info['token_url']} + Authorization: Bearer <헤더 토큰> — 토큰은 시연 제어 패널",
                  "이 창을 닫으면 연결이 끊긴다. 시연이 끝나면 Ctrl+C로 닫는다."]
        print(_box(lines), flush=True)

    try:
        await asyncio.gather(*(s.serve() for s in servers), announce())
    finally:
        if tunnel:
            tunnel.stop()
        try:
            out_file.unlink()
        except OSError:
            pass
    return 0


def main(args) -> int:
    logging.basicConfig(level=logging.WARNING)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n종료했습니다. 터널과 서버를 닫았습니다.")
        return 0
