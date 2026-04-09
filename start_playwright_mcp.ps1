$ErrorActionPreference = "Stop"

$port = 8931
$workdir = $PSScriptRoot
$logDir = Join-Path $workdir "output\playwright"
$stdoutLog = Join-Path $logDir "playwright-mcp.out.log"
$stderrLog = Join-Path $logDir "playwright-mcp.err.log"
$userDataDir = Join-Path $logDir "user-data"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path $userDataDir | Out-Null

$existing = Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -eq "node.exe" -and
    $_.CommandLine -match "@playwright/mcp@latest" -and
    $_.CommandLine -match "--port\s+$port"
  } |
  Select-Object -First 1

if ($existing) {
  Write-Output "Playwright MCP already running on port $port (PID $($existing.ProcessId))."
  exit 0
}

$process = Start-Process `
  -FilePath "npx.cmd" `
  -ArgumentList "@playwright/mcp@latest", "--host", "127.0.0.1", "--port", "$port", "--output-dir", $logDir, "--user-data-dir", $userDataDir `
  -WorkingDirectory $workdir `
  -RedirectStandardOutput $stdoutLog `
  -RedirectStandardError $stderrLog `
  -PassThru

Start-Sleep -Seconds 3

$listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
  throw "Playwright MCP did not start correctly. Check $stderrLog"
}

Write-Output "Playwright MCP started on http://127.0.0.1:$port/mcp (PID $($process.Id))."
