# Вся платформа без Docker одной командой: сервер (8001), модуль «Документы» (8010),
# draw.io (8081) и фронтенд Vite (5173). Окон нет, вывод — в папке logs.
# При входе в Windows запускается ярлыком «SQB Process Hub» в автозагрузке
# (powershell -WindowStyle Hidden -File ...; запуск из VBS блокирует Касперский).
# Из открытого окна PowerShell сервисы закроются вместе с ним.
# PostgreSQL (5432) поднимает свой ярлык в автозагрузке; здесь его только ждём.
$root = Split-Path $PSScriptRoot -Parent
$logs = Join-Path $root 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null
Start-Transcript (Join-Path $logs 'start-all.log') | Out-Null

function Test-Port($port) { [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }

function Start-Hidden($name, $port, $file, $arguments, $dir = $root) {
    if (Test-Port $port) { Write-Host "$name уже запущен (порт $port)"; return }
    $log = Join-Path $logs $name
    Start-Process $file -ArgumentList $arguments -WorkingDirectory $dir -NoNewWindow `
        -RedirectStandardOutput "$log.log" -RedirectStandardError "$log.err.log"
    Write-Host "Запущено: $name (порт $port)"
}

for ($i = 0; -not (Test-Port 5432) -and $i -lt 60; $i++) { Start-Sleep 2 }
if (-not (Test-Port 5432)) { Write-Host 'PostgreSQL не отвечает на порту 5432 — сервисы без базы не заработают.' }

function Script($name) { @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$(Join-Path $PSScriptRoot $name)`"") }
Start-Hidden 'backend' 8001 powershell (Script 'start-backend-8001.ps1')
Start-Hidden 'drawio' 8081 powershell (Script 'start-drawio.ps1')
Start-Hidden 'documents' 8010 powershell ((Script 'start-documents.ps1') + @('-LogDir', "`"$logs`""))
Start-Hidden 'web' 5173 npm.cmd @('run', 'dev') (Join-Path $root 'app')
Stop-Transcript | Out-Null
