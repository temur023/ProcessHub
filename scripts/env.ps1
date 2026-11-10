# Читает .env из корня проекта в переменные окружения текущего окна PowerShell.
# Без Docker сервер платформы .env сам не читает — его подключают так.
# Пустые значения пропускаются; уже заданные в окне переменные не перезаписываются.
$envFile = Join-Path (Split-Path $PSScriptRoot -Parent) '.env'
if (-not (Test-Path $envFile)) {
    Write-Host "Нет файла $envFile — скопируйте .env.example в .env и заполните." -ForegroundColor Red
    exit 1
}
foreach ($line in Get-Content $envFile -Encoding UTF8) {
    if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$') {
        $name = $Matches[1]; $value = $Matches[2].Trim('"', "'")
        if ($value -and -not [Environment]::GetEnvironmentVariable($name, 'Process')) {
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
}
