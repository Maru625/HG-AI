# 시연 화면 배치: 왼쪽 2/3 = 전결 사이트(브라우저 앱 창), 오른쪽 1/3 = ChatGPT 데스크톱 앱
#   팀장 PC: start_lead_pc.bat  → 서버를 켜고(이미 켜져 있으면 그대로) 박서연 팀장(E2001)으로 연다
#   사원 PC: start_staff_pc.bat → 팀장 PC 주소를 물어(한 번 넣으면 server_ip.txt에 기억) 김지훈 선임(E3001)으로 연다
# 서버가 Render 같은 공개 주소에 있으면: 이 폴더의 server_url.txt에 https 주소 한 줄을 넣는다 → 두 PC 모두 서버를 켜지 않고 그 주소로 연다
#   (한 번만: start_lead_pc.bat -Server https://hg-approval-demo.onrender.com)
#   사원 PC는 질문에 IP 대신 https 주소를 넣으면 된다. 한 번 넣은 값은 server_ip.txt에 기억
# 옵션: -User E2003(다른 사용자) -WebShare 0.6(사이트 폭 비율) -Server 172.x.x.x 또는 https://… -NoServer
param(
  [ValidateSet('lead', 'staff')][string]$Role = 'lead',
  [string]$Server = '',
  [string]$User = '',
  [double]$WebShare = 0.6667,
  [switch]$NoServer
)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $User) { $User = if ($Role -eq 'lead') { 'E2001' } else { 'E3001' } }
$ipFile = Join-Path $root 'server_ip.txt'
$urlFile = Join-Path $root 'server_url.txt'      # 공개 서버(Render 등) 주소 한 줄. 있으면 두 PC 모두 이 주소로 연다
if (-not $Server -and (Test-Path $urlFile)) { $Server = (Get-Content $urlFile -Raw).Trim() }
if (-not $Server) {
  if ($Role -eq 'lead') { $Server = 'localhost' }
  else {
    $saved = if (Test-Path $ipFile) { (Get-Content $ipFile -Raw).Trim() } else { '' }
    $ans = Read-Host "서버 주소를 넣으세요 - 팀장 PC IP 또는 https 주소$(if ($saved) { " [Enter = $saved]" } else { ' (예: 172.26.37.141 / https://hg-approval-demo.onrender.com)' })"
    $Server = if ($ans) { $ans.Trim() } else { $saved }
    if (-not $Server) { Write-Host '주소가 없어 끝냅니다.'; Read-Host '닫으려면 Enter'; exit 1 }
    Set-Content -Path $ipFile -Value $Server -Encoding UTF8
  }
}
$remote = $Server -match '^https?://'
$base = if ($remote) { $Server.TrimEnd('/') } else { "http://${Server}:8000" }

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class DemoWin {
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
}
"@
Add-Type -AssemblyName System.Windows.Forms
$wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea   # 작업 표시줄을 뺀 화면
$webW = [int]($wa.Width * $WebShare)

function Test-Up([string]$u) {
  try { return (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "$u/healthz").StatusCode -eq 200 } catch { return $false }
}
function Place([IntPtr]$h, [int]$x, [int]$w) {
  [void][DemoWin]::ShowWindow($h, 9)                                   # 최대화·최소화 풀기
  [void][DemoWin]::SetWindowPos($h, [IntPtr]::Zero, $x, $wa.Y, $w, $wa.Height, 0x0040)
}

# 1. 서버 (팀장 PC만)
if ($Role -eq 'lead' -and -not $NoServer -and -not $remote) {
  if (Test-Up $base) { Write-Host '이미 켜져 있는 서버를 그대로 씁니다. (새로 시작하려면 서버 창을 닫고 다시 실행)' }
  else {
    Write-Host '서버를 켭니다 (작업 표시줄에 최소화된 창). 방화벽 창이 뜨면 "개인 네트워크" 허용.'
    Start-Process -FilePath (Join-Path $root 'start_lan_demo.bat') -WorkingDirectory $root -WindowStyle Minimized
  }
}
Write-Host "서버 확인: $base $(if ($remote) { '(무료 Render는 잠들어 있으면 깨우는 데 1분쯤 걸립니다)' })"
$ok = $false
for ($i = 0; $i -lt 90; $i++) { if (Test-Up $base) { $ok = $true; break }; Start-Sleep -Seconds 1 }
if (-not $ok) {
  Write-Host "서버에 닿지 않습니다: $base  (팀장 PC 서버가 켜져 있는지, 같은 와이파이인지, 방화벽 허용을 확인)"
  Read-Host '닫으려면 Enter'; exit 1
}

# 2. 사이트: 왼쪽 2/3. 시연 전용 브라우저 프로필이라 평소 브라우저 창과 섞이지 않는다
$browser = @(
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
$prof = Join-Path $root "data\browser-$Role"
New-Item -ItemType Directory -Force -Path $prof | Out-Null
Start-Process -FilePath $browser -ArgumentList @(
  "--app=$base/as/$User", "--user-data-dir=`"$prof`"",
  "--window-position=$($wa.X),$($wa.Y)", "--window-size=$webW,$($wa.Height)",
  '--no-first-run', '--no-default-browser-check'
)
for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep -Milliseconds 500
  $web = Get-Process msedge, chrome -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -like '*DEMO*' } | Select-Object -First 1
  if ($web) { Place $web.MainWindowHandle $wa.X $webW; break }
}

# 3. ChatGPT 데스크톱 앱: 오른쪽 1/3
function Find-Gpt {
  Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.MainWindowHandle -ne 0 -and (
      ($_.Path -and $_.Path -like '*\WindowsApps\OpenAI*') -or $_.ProcessName -match '^(ChatGPT|Codex)$')
  } | Select-Object -First 1
}
$gpt = Find-Gpt
if (-not $gpt) {
  $app = Get-StartApps | Where-Object { $_.Name -eq 'ChatGPT' } | Select-Object -First 1
  if ($app) { Start-Process "shell:AppsFolder\$($app.AppID)" } else { Write-Host 'ChatGPT 앱을 찾지 못했습니다. 설치되어 있는지 확인하세요.' }
  for ($i = 0; $i -lt 30 -and -not $gpt; $i++) { Start-Sleep -Seconds 1; $gpt = Find-Gpt }
}
if ($gpt) { Place $gpt.MainWindowHandle ($wa.X + $webW) ($wa.Width - $webW) }

Write-Host "배치 완료: 사이트 $webW px | ChatGPT $($wa.Width - $webW) px (화면 $($wa.Width)x$($wa.Height))"
Start-Sleep -Seconds 2
