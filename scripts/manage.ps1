param([ValidateSet('Start','Stop','Status')][string]$Action = 'Start')
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskPidPath = Join-Path $taskRoot 'data\service.json'

function Get-CuteQProcess {
    if (-not (Test-Path -LiteralPath $taskPidPath)) { return $null }
    $taskState = Get-Content -LiteralPath $taskPidPath -Raw -Encoding utf8 | ConvertFrom-Json
    $taskProcess = Get-CimInstance Win32_Process -Filter ("ProcessId = " + [int]$taskState.pid) -ErrorAction SilentlyContinue
    if ($taskProcess -and $taskProcess.ExecutablePath -ieq $taskPython -and $taskProcess.CommandLine -match '-m\s+astrbot\.cli\s+run' -and $taskProcess.CreationDate.ToUniversalTime().Ticks -eq ([datetime]$taskState.createdUtc).ToUniversalTime().Ticks) {
        return $taskProcess
    }
    return $null
}

$taskProcess = Get-CuteQProcess
if ($Action -eq 'Start') {
    if (-not $taskProcess) {
        New-Item -ItemType Directory -Path (Join-Path $taskRoot 'logs') -Force | Out-Null
        $env:PYTHONUTF8 = '1'
        $env:PYTHONUNBUFFERED = '1'
        $env:ASTRBOT_ROOT = $taskRoot
        $taskVideoTool = Get-ChildItem -LiteralPath (Join-Path $taskRoot 'runtime\ffmpeg') -Filter 'ffmpeg.exe' -Recurse -File -ErrorAction Stop | Select-Object -First 1
        if (-not $taskVideoTool) { throw 'The project video tools are missing.' }
        $env:PATH = (Join-Path $taskRoot '.venv\Scripts') + ';' + $taskVideoTool.Directory.FullName + ';' + $env:PATH
        $taskStarted = Start-Process -FilePath $taskPython -ArgumentList @('-m','astrbot.cli','run') -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskRoot 'logs\service.log') -RedirectStandardError (Join-Path $taskRoot 'logs\service-error.log') -PassThru
        $taskActual = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $taskStarted.Id) -ErrorAction Stop
        [pscustomobject]@{pid=$taskStarted.Id;createdUtc=$taskActual.CreationDate.ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $taskPidPath -Encoding utf8
        $taskProcess = $taskActual
    }
    & (Join-Path $PSScriptRoot 'napcat.ps1') -Action Start
} elseif ($Action -eq 'Stop') {
    if ($taskProcess) {
        # Windows venv Python starts a child interpreter that owns the listeners.
        $taskChildren = @(Get-CimInstance Win32_Process -Filter ("ParentProcessId = " + $taskProcess.ProcessId))
        foreach ($taskChild in $taskChildren) {
            if ($taskChild.CommandLine -and $taskChild.CommandLine.Contains($taskPython) -and $taskChild.CommandLine -match '-m\s+astrbot\.cli\s+run') {
                Stop-Process -Id $taskChild.ProcessId -ErrorAction Stop
            }
        }
        if (Get-Process -Id $taskProcess.ProcessId -ErrorAction SilentlyContinue) { Stop-Process -Id $taskProcess.ProcessId -ErrorAction Stop }
    }
    & (Join-Path $PSScriptRoot 'napcat.ps1') -Action Stop
    [pscustomobject]@{running=$false;root=$taskRoot} | ConvertTo-Json -Compress
    exit
}

$taskHealthy = $false
try { $taskHealthy = (Invoke-WebRequest -Uri 'http://127.0.0.1:6185/' -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { }
[pscustomobject]@{running=[bool]$taskProcess;dashboardReady=$taskHealthy;dashboard='http://127.0.0.1:6185';root=$taskRoot} | ConvertTo-Json -Compress
