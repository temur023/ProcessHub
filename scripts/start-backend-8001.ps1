# Запуск сервера платформы без Docker (корпоративный компьютер) с настройками из .env.
# Запуск из корня проекта:  powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1
. (Join-Path $PSScriptRoot 'env.ps1')

# База — установленный PostgreSQL (порт 5432), если адрес не задан в .env явно.
if (-not $env:SQB_DATABASE_URL) {
    $password = if ($env:SQB_DB_PASSWORD) { $env:SQB_DB_PASSWORD } else { 'sqb' }
    $env:SQB_DATABASE_URL = "postgresql://sqb:$password@localhost:5432/sqb"
}
# Модуль «Документы» без Docker — scripts\start-documents.ps1, порт 8010.
if (-not $env:SQB_KNOWLEDGE_URL) { $env:SQB_KNOWLEDGE_URL = 'http://127.0.0.1:8010' }
Write-Host ('Nova: ' + $(if ($env:NOVA_BASE_URL -and $env:NOVA_API_KEY) { $env:NOVA_BASE_URL } else { 'не настроена' }))

Set-Location (Join-Path (Split-Path $PSScriptRoot -Parent) 'backend')
$env:PYTHONPATH = '.'
& .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
