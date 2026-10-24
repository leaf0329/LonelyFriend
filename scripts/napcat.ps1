param([ValidateSet('Start','Stop','Status')][string]$Action = 'Start')
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$taskNode = Join-Path $taskRoot 'runtime\napcat\node.exe'
$taskPidPath = Join-Path $taskRoot 'data\napcat-service.json'
$taskProcess = $null
if (Test-Path -LiteralPath $taskPidPath) {
    $taskState = Get-Content -LiteralPath $taskPidPath -Raw -Encoding utf8 | ConvertFrom-Json
    $taskFound = Get-CimInstance Win32_Process -Filter ("ProcessId = " + [int]$taskState.pid) -ErrorAction SilentlyContinue
    if ($taskFound -and $taskFound.ExecutablePath -ieq $taskNode -and $taskFound.CreationDate.ToUniversalTime().Ticks -eq ([datetime]$taskState.createdUtc).ToUniversalTime().Ticks) { $taskProcess = $taskFound }
}
if ($Action -eq 'Start' -and -not $taskProcess) {
    $env:NAPCAT_WORKDIR = Join-Path $taskRoot 'data\napcat'
    $env:NAPCAT_DISABLE_TIME_SYNC = '1'
    $env:NAPCAT_DISABLE_BYPASS = '1'
    $env:NAPCAT_DISABLE_MULTI_PROCESS = '1'
    $env:NAPCAT_DISABLE_PIPE = '1'
    foreach ($taskName in @('NAPCAT_QUICK_ACCOUNT','NAPCAT_QUICK_PASSWORD','NAPCAT_QUICK_PASSWORD_MD5')) { Remove-Item -LiteralPath ('Env:\' + $taskName) -ErrorAction SilentlyContinue }
    $taskStarted = Start-Process -FilePath $taskNode -ArgumentList @('index.js') -WorkingDirectory (Join-Path $taskRoot 'runtime\napcat') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskRoot 'logs\napcat.log') -RedirectStandardError (Join-Path $taskRoot 'logs\napcat-error.log') -PassThru
    $taskProcess = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $taskStarted.Id) -ErrorAction Stop
    [pscustomobject]@{pid=$taskStarted.Id;createdUtc=$taskProcess.CreationDate.ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $taskPidPath -Encoding utf8
} elseif ($Action -eq 'Stop' -and $taskProcess) {
    Stop-Process -Id $taskProcess.ProcessId -ErrorAction Stop
    $taskProcess = $null
}
$taskHealthy = $false
try { $taskHealthy = (Invoke-WebRequest -Uri 'http://127.0.0.1:6099/' -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { }
[pscustomobject]@{napcatRunning=[bool]$taskProcess;loginPageReady=$taskHealthy;loginPage='http://127.0.0.1:6099'} | ConvertTo-Json -Compress
