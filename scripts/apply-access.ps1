$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskLog = Join-Path $taskRoot 'logs\apply-access.log'
& $taskPython (Join-Path $PSScriptRoot 'set_access.py') --check *> $taskLog
if ($LASTEXITCODE -ne 0) { throw 'The QQ list is invalid. See logs/apply-access.log.' }
& (Join-Path $PSScriptRoot 'manage.ps1') -Action Stop >> $taskLog
& $taskPython (Join-Path $PSScriptRoot 'set_access.py') >> $taskLog
if ($LASTEXITCODE -ne 0) { throw 'The QQ list could not be applied.' }
& (Join-Path $PSScriptRoot 'manage.ps1') -Action Start >> $taskLog
