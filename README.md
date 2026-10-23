# 목업 전결 시스템 + MCP (시연용)

한화 AI 과제 **"칼퇴를 위한 1시간 줄이기"** 시연 프로그램입니다.
AI 서비스(Claude Desktop, ChatGPT 등)를 그대로 쓰고, MCP로 연결한 **목업 전결 시스템**에서 전결 처리에 새는 시간(처리 파악·증빙 준비·문서 작성·반려 후 재작성·진행 확인·결재 검토)을 줄이는 흐름을 보여줍니다.

> 모든 회사·사람·금액·영수증은 **더미**입니다. 실제 회사 자료를 넣지 마세요.

## 1. 구성

| 프로그램 | 폴더 | 하는 일 |
|---|---|---|
| P1 전결 코어 | `core/` | 규정·검증·결재선·상태·알림·감사 기록. REST API(`/api`) |
| P3 웹 화면 | `web/` | 대시보드, 작업 계획, 임시저장함, 결재함·결재 검토, 회의실, 규정, 작업 기록, 시연 제어 패널 |
| P2 MCP 서버 | `mcp_server/` | AI 서비스가 부르는 툴(기안자용 14개 / 결재자용 읽기 8개). 판정 없이 REST API를 부른다. stdio(Claude Desktop)와 HTTP(ChatGPT, `http_multi.py`) |
| P4 생성기 | `seedgen/` | 더미 규정·조직·이력·대시보드 데이터·시연 자료(영수증 5장, 메모)·과제 엑셀 |

```
AI 서비스 ──MCP(stdio 또는 streamable HTTP)──▶ approval-mcp ──REST──▶ approval-server (전결 코어 + 웹 화면) ◀── 브라우저
```

- 판정(결재선, 한도, 중복, 중요도)은 전결 서버만 한다. AI는 서버 결과를 따른다.
- 사람 확인 3번: ① 작업 계획 승인 ② 초안 확인 ③ 상신. 승인·반려는 **웹 화면에서만** 한다(MCP·REST에 없음).

## 2. 준비물 (Windows 기준)

- Python 3.12 — uv가 알아서 받습니다.
- uv — PowerShell에서 아래 중 하나 ([uv 설치 문서](https://docs.astral.sh/uv/getting-started/installation/))
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  # 또는
  winget install --id=astral-sh.uv -e
  ```
- ChatGPT(원격 시연, 4장) 또는 Claude Desktop(로컬 시연, 5장)

## 3. 설치와 실행

```powershell
cd approval-demo
uv sync                          # 의존성 설치 (.venv)
uv run approval-server reset     # DB를 만들고 시드를 적재, 시연 시계를 2026-11-12 10:00으로
uv run approval-server           # 전결 서버 + 웹 화면: http://localhost:8000
```

- 끄려면 `Ctrl+C`. 시연을 처음부터 다시 하려면 웹의 **시연 제어 패널(`/demo`) → 처음 상태로 초기화**.
- 포트를 바꾸려면 `.env`에 `APPROVAL_PORT=8010` (`.env.example` 참고).

## 4. ChatGPT로 시연 (원격, 권장 방식)

[사실] ChatGPT는 공개 HTTPS 주소의 MCP 서버에만 연결하고, 노트북의 로컬 서버에는 바로 붙지 못합니다. 쓰기 툴까지 쓰는 사용자 지정 MCP는 Business·Enterprise/Edu 워크스페이스에서 열려 있고, Business는 관리자·소유자만 만들 수 있습니다. Pro는 읽기·조회 권한으로만 연결됩니다. ([OpenAI 도움말](https://help.openai.com/en/articles/12584461-developer-mode-apps-and-full-mcp-connectors-in-chatgpt))
[해석] 기안 흐름(계획·저장·확인·상신)은 쓰기 툴이라, 개인 Plus/Pro 계정에서는 막힐 수 있습니다. 실제 계정에서 먼저 확인하세요. (OpenAI [개발자 문서](https://developers.openai.com/api/docs/guides/developer-mode)는 Plus·Pro도 개발자 모드를 쓸 수 있다고 적고 있어 도움말과 다릅니다.)

1. 터널 프로그램 cloudflared를 프로젝트의 `tools` 폴더에 받습니다(한 번만, [Cloudflare 내려받기 문서](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/)).
   ```powershell
   mkdir tools
   Invoke-WebRequest -Uri https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe -OutFile tools\cloudflared.exe
   ```
2. 한 명령으로 전결 서버 + MCP 서버(기안자용·결재자용 연결 2개, 한 포트) + 터널을 띄웁니다.
   ```powershell
   uv run approval-server reset
   uv run approval-server gpt
   ```
   화면에 ChatGPT에 등록할 주소 2개(`https://….trycloudflare.com/mcp/<비밀값>/drafter`, `…/approver`)가 나옵니다. 시연 제어 패널(`/demo`)에도 보입니다. 이 창을 닫으면 연결이 끊깁니다.
3. ChatGPT → Plugins → + → **Add custom MCP server**(이전 화면: 설정 → 앱 → Create app)에서 앱 2개를 등록합니다. 인증은 **No authentication**. 주소 안의 비밀값이 토큰 역할을 합니다.
4. 대화에서는 메시지마다 `@앱 이름`을 붙입니다. ChatGPT에서 앱 선택은 그 메시지에만 적용됩니다(같은 도움말).

- 빠른 터널(trycloudflare)은 계정이 필요 없지만 테스트용이고, 실행할 때마다 주소가 바뀝니다. 바뀌면 ChatGPT 앱의 주소를 고치고 새로고침합니다. 비밀값은 `data/mcp_secret.txt`에 남아 그대로 씁니다.
- [사실] 빠른 터널은 SSE를 지원하지 않습니다([Cloudflare 문서](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)). 그래서 이 모드의 MCP 서버는 응답을 JSON 한 덩어리로 보내고 세션을 두지 않습니다.
- 주소가 바뀌지 않게 하려면 ngrok 고정 도메인을 씁니다: `uv run approval-server gpt --tunnel ngrok --ngrok-domain <도메인>`.
- 공개 주소를 열지 않는 대안은 OpenAI Secure MCP Tunnel입니다. OpenAI Platform 조직의 터널 권한과 API 키가 필요합니다([문서](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)).
- **시연이 끝나면 Ctrl+C로 터널과 서버를 닫습니다.** 회사 보안 정책을 먼저 확인하세요(설계서 12장).

## 5. Claude Desktop으로 시연 (로컬, 예비)

1. 설정 예를 출력합니다. **이 컴퓨터의 실제 경로**가 들어간 설정이 나옵니다. 아래 예의 경로를 그대로 쓰지 마세요.
   ```powershell
   uv run approval-server connections
   ```
2. Claude Desktop → 설정 → Developer → **Edit Config**로 `%APPDATA%\Claude\claude_desktop_config.json`을 열어 출력된 `mcpServers`를 붙여 넣습니다. ([MCP 문서: Connect to local MCP servers](https://modelcontextprotocol.io/docs/develop/connect-local-servers)) 출력 모양은 이렇습니다.
   ```json
   {
     "mcpServers": {
       "approval-drafter": {
         "command": "<프로젝트 폴더>\\.venv\\Scripts\\approval-mcp.exe",
         "args": [],
         "env": {"APPROVAL_TOKEN": "demo-drafter-kimjihoon-7f3a", "APPROVAL_SERVER": "http://localhost:8000", "APPROVAL_ROLE": "drafter"}
       },
       "approval-approver": {
         "command": "<프로젝트 폴더>\\.venv\\Scripts\\approval-mcp.exe",
         "args": [],
         "env": {"APPROVAL_TOKEN": "demo-approver-parkseoyeon-9c2e", "APPROVAL_SERVER": "http://localhost:8000", "APPROVAL_ROLE": "approver"}
       }
     }
   }
   ```
3. Claude Desktop을 **완전히 종료했다가** 다시 엽니다(설정은 시작할 때 읽음, 같은 문서).
4. 대화마다 연결을 하나만 켭니다. 기안자 대화에는 `approval-drafter`, 결재자 대화에는 `approval-approver`. 기안 툴과 결재 조회 툴은 연결별로 나뉘어 있어 섞이지 않지만, 공통 툴(`whoami`, `get_status` 등)은 두 연결에 다 있어 함께 켜면 어느 명의로 부를지 AI가 고르게 됩니다.
   - 'Server disconnected'가 나오면 먼저 `command` 경로가 실제로 있는지 봅니다. 로그: `%APPDATA%\Claude\logs\mcp-server-approval-drafter.log`.
   - 전결 서버가 꺼진 채로 Claude Desktop을 열어도 `APPROVAL_ROLE` 값으로 툴이 등록됩니다. 툴을 부르면 "전결 서버를 켜 달라"는 안내 오류가 돌아옵니다.

## 6. 시연

- 대본: [`docs/specs/01_시연_대본.md`](docs/specs/01_시연_대본.md)
- 시연 자료: `demo_inputs/` (영수증 5장 + `출장메모_대전.txt`) → 기안자 대화에 올립니다.
- 과제 지정 데이터(직급별 업무 시간 할당량, 하기 싫은 업무 설문): `seed/과제_활용데이터.xlsx`

## 7. 명령 모음

| 명령 | 하는 일 |
|---|---|
| `uv run approval-server` | 전결 서버 + 웹 화면 실행 (DB가 없으면 먼저 초기화) |
| `uv run approval-server reset` | DB 초기화, 시드 적재, 시연 시계 처음으로 |
| `uv run approval-server connections` | MCP 연결 토큰과 Claude Desktop 설정 예 |
| `uv run approval-server gpt` | ChatGPT 시연: 전결 서버 + MCP(HTTP, 연결 2개) + 터널, 등록할 주소 출력 |
| `uv run approval-mcp` | MCP 서버(stdio). 보통 Claude Desktop이 직접 띄운다 |
| `uv run approval-seed build` | 시드와 시연 자료를 다시 만든다 (seed 고정: 같은 결과) |
| `uv run approval-seed check` | 시드 검사 14개 |
| `uv run python -m mcp_server.export_spec` | MCP 툴 명세 문서를 코드에서 다시 만든다 |
| `uv run pytest` | 자동 시험 32개 (임시 DB 사용) |

## 8. 문서

| 문서 | 내용 |
|---|---|
| [01 시연 대본](docs/specs/01_시연_대본.md) | 준비, 장면별 말·툴·기대 결과, 선택 장면, 막혔을 때 |
| [02 전결 규정](docs/specs/02_전결_규정.md) | 프로세스 5종, 검증 규칙, 결재 중요도·주의 항목 (전문: `seed/regulations.md`) |
| [03 데이터 모델](docs/specs/03_데이터_모델.md) | 테이블, 상태 전이, 시연 시계, 오류 형식 |
| [04 MCP 툴 명세](docs/specs/04_MCP_툴_명세.md) | 코드에서 생성. 서버 지침, 연결별 툴, 입력 스키마 |
| [05 화면 명세](docs/specs/05_화면_명세.md) | 화면별 내용, 자동 갱신, 알림, 강조·접근성 |
| [06 지표·더미 데이터](docs/specs/06_지표_더미데이터.md) | 과제 데이터 파일, 대시보드 지표 계산, 결정성 |
| [07 시험 케이스](docs/specs/07_시험_케이스.md) | 설계서 시험 케이스 10종과 시험 함수, 상태 전이 확인 |

## 9. 폴더

```
approval-demo/
├─ core/          P1 전결 코어 (services.py가 중심, api.py = REST, cli.py = approval-server)
├─ web/           P3 화면 (routes.py, templates/, static/ — htmx·Chart.js 로컬 사본)
├─ mcp_server/    P2 MCP 서버 (server.py, export_spec.py)
├─ seedgen/       P4 생성기 (source/*.yaml이 원본)
├─ seed/          생성된 시드 (규정, 조직, 이력, 대시보드 CSV, 과제 엑셀)
├─ demo_inputs/   시연 때 AI에 올리는 자료
├─ docs/specs/    명세 문서 01~07
├─ tests/         자동 시험 (scenario.py = 대본의 AI 호출 값)
└─ data/          실행 중 DB (git 제외)
```

## 10. 화면 미리보기

`docs/screens/`에 시연 대본대로 MCP 클라이언트로 진행한 상태의 화면 캡처 8장이 있습니다(1366px).
홈 대시보드, 작업 계획, 임시저장함 초안, 결재함과 긴급 알림, 결재 검토(주의 강조), 회의실 가예약, 작업 기록, 시연 제어 패널.
지금 화면은 기능 확인용 기본 디자인이며, 화면 디자인 작업(D2) 시안이 오면 `web/templates`와 `web/static/app.css`만 바꾸면 됩니다.
