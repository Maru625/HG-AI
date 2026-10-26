# 목업 전결 시스템 + MCP (시연용)

한화 AI 과제 **"칼퇴를 위한 1시간 줄이기"** 시연 프로그램입니다.
AI 서비스(Claude Desktop, ChatGPT 등)를 그대로 쓰고, MCP로 연결한 **목업 전결 시스템**에서 전결 처리에 새는 시간(처리 파악·증빙 준비·문서 작성·반려 후 재작성·진행 확인·결재 검토)을 줄이는 흐름을 보여줍니다.

> 모든 회사·사람·금액·영수증은 **더미**입니다. 실제 회사 자료를 넣지 마세요.

## 1. 구성

| 프로그램 | 폴더 | 하는 일 |
|---|---|---|
| P1 전결 코어 | `core/` | 규정·검증·결재선·상태·알림·감사 기록. REST API(`/api`) |
| P3 웹 화면 | `web/` | 홈(이야기형 문제 제시), 데이터 자세히(도표), 새 기안(화면에서 직접 작성·규정 사례), 작업 계획, 임시저장함, 결재함·결재 검토, 회의실, 규정, 작업 기록, 시연 제어 패널 |
| P2 MCP 서버 | `mcp_server/` | AI 서비스가 부르는 툴(기안자용 14개 / 결재자용 읽기 8개). 판정 없이 REST API를 부른다. stdio(Claude Desktop)와 HTTP(ChatGPT, `http_multi.py`) |
| P4 생성기 | `seedgen/` | 더미 규정·조직·이력·대시보드 데이터·시연 자료(영수증 5장, 메모)·과제 엑셀 |

```
AI 서비스 ──MCP(stdio 또는 streamable HTTP)──▶ approval-mcp ──REST──▶ approval-server (전결 코어 + 웹 화면) ◀── 브라우저
```

- 판정(결재선, 한도, 중복, 시점, 첨부, 중요도, 처리 기한)은 전결 서버만 한다. AI는 서버 결과를 따른다.
- 사람 확인 3번: ① 작업 계획 승인 ② 초안 확인(판독이 흐린 영수증은 원본 대조 표시 포함) ③ 상신. 승인·반려와 처리 지연 알림은 **웹 화면에서만** 한다(MCP·REST에 없음).
- 규정 조항마다 대응 기능과 해 볼 수 있는 사례(45개)가 있다: 웹 '새 기안', '규정' 화면, [08 규정 대응표](docs/specs/08_규정_대응표.md).
- 재미 기능: 겹치는 시간에 회의실을 신청하면 기존 예약자와 **가위바위보**로 자리를 다툴 수 있다(두 사람 화면에 대결 창). 시연 제어 패널에서 끌 수 있다.

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

## 4. ChatGPT로 시연 (권장)

### 4.1 ChatGPT 데스크톱 앱 — 터널 없이 (10/20 실기 확인)

[사실·실기] Windows용 ChatGPT 데스크톱 앱의 설정 › 플러그인 › 추가 › **MCP 서버 추가**는 STDIO와 스트리밍 가능한 HTTP를 받고, 이 컴퓨터에서 바로 연결합니다. 10/20 개인 계정으로 `http://127.0.0.1:8765/…` 주소를 등록해 Work 모드에서 툴 호출까지 확인했습니다. 공식 문서 출처는 찾지 못했습니다.

1. 프로젝트 폴더의 **`start_gpt_demo.bat`을 더블클릭**합니다. 시드를 초기화하고 전결 서버(8000)와 MCP 서버(8765, 연결 2개 — 김지훈 기안자용·박서연 결재자용)를 띄운 뒤, 등록할 주소를 보여줍니다. 시연 제어 패널(`/demo`)에도 보입니다. 창을 닫으면 끊깁니다.
2. ChatGPT 데스크톱 앱 › 설정 › 플러그인 › 추가 › MCP 서버 추가 › **스트리밍 가능한 HTTP**로 두 개를 등록합니다(한 번만).
   - 이름 `approval_drafter`, URL `http://127.0.0.1:8765/mcp/<비밀값>/drafter`
   - 이름 `approval_approver`, URL `http://127.0.0.1:8765/mcp/<비밀값>/approver`
   - 비밀값은 `data\mcp_secret.txt`에 한 번 만들어져 다시 실행해도 같습니다.
3. Work 대화에서 첫 메시지에 어느 연결로 할지 말합니다(“기안자 연결(approval_drafter)로 …”). 두 연결이 모든 대화에 함께 켜져 있기 때문입니다.

- 초기화 없이 켜려면: `.venv\Scripts\approval-server.exe gpt`
- 시연 순서: Claude 문서 「목업 전결 시연 순서 (ChatGPT)」, 자세한 대본: `docs/specs/01_시연_대본.md`
- 10/23부터 확인 ② 때 흐린 영수증(④ 은행동 칼국수)은 원본 대조를 확인합니다. 대화로 확인하면 AI가 "원본과 맞나요?"라고 묻고, 화면으로 확인하면 임시저장함의 체크 칸에 표시한 뒤 [내용 확인]을 누릅니다. **`confirm_drafts`의 입력이 바뀌었으니 ChatGPT에서 연결의 툴 목록을 한 번 새로 고칩니다**(연결 설정에서 새로 고침, 또는 삭제 후 다시 추가).

### 4.3 시연 사용자와 연결 (10/26 정리)

- **연결은 두 개뿐입니다**: `approval_drafter`(김지훈 선임, 기안자용)와 `approval_approver`(박서연 팀장, 결재자용).
  - 이전(10/23): 사례별 추가 연결 6개(정하늘·박서연 기안·송서준·이도현·최민준·윤재민)가 더 있었습니다. 10/26에 뺐습니다.
- **사용자 전환 목록은 5명입니다**: 시연 — 김지훈 선임, 박서연 팀장 / 회의실 예약자(가위바위보 상대) — 문채원 팀장, 강민호 팀장, 최민준 본부장.
  - 나머지 직원(33명 중 28명)은 결재선·결재 이력·대시보드 데이터로만 남아 있습니다. 결재선 규칙(실장·본부장·합의)은 그대로 계산됩니다.
  - 다른 사용자가 필요한 사례 3개(실장 기안, 책임 숙박 한도, 경영지원팀 대회의실)는 새 기안 사례 목록에서 뺐습니다. 규칙 시험은 그대로 돕니다.

### 4.2 ChatGPT 웹 — 공개 터널 필요 (예비)

[사실] ChatGPT 웹은 공개 HTTPS 주소의 MCP 서버에만 연결합니다. 쓰기 툴까지 쓰는 사용자 지정 MCP는 Business·Enterprise/Edu 워크스페이스에서 열려 있고, Business는 관리자·소유자만 만들 수 있습니다. Pro는 읽기·조회 권한으로만 연결됩니다. ([OpenAI 도움말](https://help.openai.com/en/articles/12584461-developer-mode-apps-and-full-mcp-connectors-in-chatgpt)) OpenAI [개발자 문서](https://developers.openai.com/api/docs/guides/developer-mode)는 Plus·Pro도 개발자 모드를 쓸 수 있다고 적고 있어 도움말과 다릅니다.

1. cloudflared를 `tools\cloudflared.exe`로 받습니다([Cloudflare 내려받기 문서](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/)).
   ```powershell
   mkdir tools
   Invoke-WebRequest -Uri https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe -OutFile tools\cloudflared.exe
   ```
2. `start_gpt_demo.bat --tunnel cloudflared` (또는 `uv run approval-server gpt --tunnel cloudflared`) → 공개 주소 2개(`https://….trycloudflare.com/mcp/<비밀값>/drafter`, `…/approver`)가 함께 나옵니다.
3. ChatGPT 웹 → Plugins → + → **Add custom MCP server**에 등록합니다. 인증은 No authentication. 대화에서는 메시지마다 `@앱 이름`을 붙입니다(앱 선택은 그 메시지에만 적용, 같은 도움말).

- 빠른 터널은 테스트용이고 실행할 때마다 주소가 바뀝니다. [사실] SSE를 지원하지 않아([Cloudflare 문서](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)) 이 모드의 MCP 서버는 JSON 응답·세션 없음으로 동작합니다.
- 고정 주소: `--tunnel ngrok --ngrok-domain <도메인>`. 공개 노출 없는 대안: OpenAI Secure MCP Tunnel([문서](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)).
- **시연이 끝나면 창을 닫아 터널을 닫습니다.** 회사 보안 정책을 먼저 확인하세요(설계서 12장).

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
| `start_gpt_demo.bat` (더블클릭) | ChatGPT 시연: 초기화 + 전결 서버 + MCP(HTTP, 연결 8개), 등록할 주소 출력 |
| `uv run approval-server gpt [--tunnel cloudflared]` | 위와 같고 초기화 없음. `--tunnel`이면 ChatGPT 웹용 공개 주소도 |
| `start_lan_demo.bat` (더블클릭) | 위와 같고 **같은 와이파이의 다른 PC**도 접속(`gpt --lan`). 웹 `http://<이 PC IP>:8000`, 연결 주소는 'AI 연결' 메뉴 |
| `start_lead_pc.bat` (더블클릭, 팀장 PC) | 서버를 켜고(이미 켜져 있으면 그대로) 화면을 **사이트 왼쪽 2/3 + ChatGPT 앱 오른쪽 1/3**로 배치. 사이트는 시연 전용 브라우저 창으로 박서연 팀장(E2001). `-User E2003`, `-WebShare 0.6`로 바꿀 수 있다 (`demo_layout.ps1`) |
| `start_staff_pc.bat` (더블클릭, 사원 PC) | 팀장 PC IP를 한 번 물어(`server_ip.txt`에 기억) 같은 배치로 김지훈 선임(E3001). 사원 PC에는 이 파일과 `demo_layout.ps1`만 있으면 된다 |
| `uv run approval-server deploy [--port 8000]` | 서버 배포용: 웹·REST·MCP를 포트 하나로 (11장) |
| `uv run approval-mcp` | MCP 서버(stdio). 보통 Claude Desktop이 직접 띄운다 |
| `uv run approval-seed build` | 시드와 시연 자료를 다시 만든다 (seed 고정: 같은 결과) |
| `uv run approval-seed check` | 시드 검사 15개 |
| `uv run python -m mcp_server.export_spec` | MCP 툴 명세 문서를 코드에서 다시 만든다 |
| `uv run pytest` | 자동 시험 70개 (임시 DB 사용) |

## 8. 문서

| 문서 | 내용 |
|---|---|
| [01 시연 대본](docs/specs/01_시연_대본.md) | 준비, 장면별 말·툴·기대 결과, 선택 장면, 막혔을 때 |
| [02 전결 규정](docs/specs/02_전결_규정.md) | 프로세스 5종, 검증 규칙, 결재 중요도·주의 항목 (전문: `seed/regulations.md`) |
| [03 데이터 모델](docs/specs/03_데이터_모델.md) | 테이블, 상태 전이, 시연 시계, 오류 형식 |
| [04 MCP 툴 명세](docs/specs/04_MCP_툴_명세.md) | 코드에서 생성. 서버 지침, 연결별 툴, 입력 스키마 |
| [05 화면 명세](docs/specs/05_화면_명세.md) | 화면별 내용, 자동 갱신, 알림, 강조·접근성 |
| [06 지표·더미 데이터](docs/specs/06_지표_더미데이터.md) | 과제 데이터 파일, 대시보드 지표 계산, 결정성 |
| [07 시험 케이스](docs/specs/07_시험_케이스.md) | 설계서 시험 케이스 10종과 시험 함수, 상태 전이 확인, 규정 대응 시험 |
| [08 규정 대응표](docs/specs/08_규정_대응표.md) | 조항별 대응 기능, 해 볼 사례 45개, 해석으로 정한 것, 10/23 바뀐 점 |
| [09 서버 배포 준비](docs/specs/09_배포_준비.md) | 배포하면 달라지는 점, 실행·환경 변수, AI 서비스별 MCP 붙이기, 사용자 구분, 보안 점검 |

## 9. 폴더

```
approval-demo/
├─ core/          P1 전결 코어 (services.py가 중심, api.py = REST, cli.py = approval-server)
├─ web/           P3 화면 (routes.py, templates/, static/ — htmx·Chart.js 로컬 사본)
├─ mcp_server/    P2 MCP 서버 (server.py, export_spec.py)
├─ seedgen/       P4 생성기 (source/*.yaml이 원본)
├─ seed/          생성된 시드 (규정, 조직, 이력, 대시보드 CSV, 과제 엑셀)
├─ demo_inputs/   시연 때 AI에 올리는 자료
├─ docs/specs/    명세 문서 01~09
├─ deploy/        서버 배포 묶음 (docker compose + Caddy: HTTPS까지)
├─ tests/         자동 시험 (scenario.py = 대본의 AI 호출 값)
├─ Dockerfile     배포 이미지 (웹·REST·MCP 한 포트)
└─ data/          실행 중 DB (git 제외)
```

## 10. 화면 미리보기

`docs/screens/`에 시연 대본대로 MCP 클라이언트로 진행한 상태의 화면 캡처 8장이 있습니다(1366px).
홈(문제 제시·GPT 시연 두 사람), 데이터 자세히, 작업 계획, 임시저장함 초안, 결재함과 긴급 알림, 결재 검토(주의 강조), 회의실 가예약, 작업 기록, 시연 제어 패널.
지금 화면은 기능 확인용 기본 디자인이며, 화면 디자인 작업(D2) 시안이 오면 `web/templates`와 `web/static/app.css`만 바꾸면 됩니다.

## 11. 서버에 배포 (준비, 10/23)

여러 사람이 쓰도록 서버 한 대에 올릴 때는 `deploy`로 실행합니다. 웹 화면·REST API·MCP가 **포트 하나**에 함께 열립니다.
배포 뒤에도 **ChatGPT + MCP만** 씁니다(10/23 결정). 참가자는 웹의 **AI 연결**(`/connect`) 화면에서 주소를 복사해 각자 ChatGPT 데스크톱 앱에 등록합니다.
자세한 내용(서비스별 MCP 붙이기, 사용자 구분, 보안 점검, 보류한 안): [09 서버 배포 준비](docs/specs/09_배포_준비.md).

```bash
APPROVAL_BASE_URL=https://demo.example.com APPROVAL_MCP_SECRET=<임의 문자열> \
APPROVAL_ADMIN_KEY=<운영자 키> APPROVAL_SITE_KEY=<입장 코드> APPROVAL_CLOCK_FLOW=frozen \
uv run approval-server deploy --port 8000
# 또는: docker build -t approval-demo . && docker run -p 8000:8000 -v approval-data:/app/data -e APPROVAL_BASE_URL=... approval-demo
# 서버(VM) 한 대 + 도메인이면 HTTPS까지: cd deploy && cp .env.example .env (값 채우기) && docker compose up -d --build
```

- MCP 주소: `https://<공개 주소>/mcp/<비밀값>/<slug>` (연결 8개), 또는 `https://<공개 주소>/mcp` + 헤더 `Authorization: Bearer <헤더 토큰>`. 주소와 헤더 토큰은 시연 제어 패널에 나옵니다.
- 사용자 구분은 지금처럼 **화면에서 고른 사용자**입니다(로그인 없음). MCP는 연결마다 사용자가 고정됩니다.
- 배포 모드에서 REST API는 서버 안 MCP 툴만 부를 수 있고, 시연 제어는 운영자 키, 웹 화면은 (선택) 입장 코드로 잠급니다.
- HTTPS는 앞단(클라우드 플랫폼·프록시)이 붙입니다. 시연이 끝나면 서버를 내리거나 `APPROVAL_MCP_SECRET`을 바꿉니다.
