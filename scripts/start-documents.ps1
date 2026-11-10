# Модуль «Документы» (сверка инструкций с положениями через Nova) без Docker.
# Запускает процессы в отдельных окнах: API документов (порт 8010), подготовку
# файлов, анализ через Nova и, если установлен Word, просмотр Word (порт 8020).
# Сервер платформы (start-backend.ps1)
# ходит к API документов по SQB_KNOWLEDGE_URL с общим секретом из .env.
# Запуск из корня проекта:  powershell -ExecutionPolicy Bypass -File scripts\start-documents.ps1
# С -LogDir (scripts\start-all.ps1) — без окон, вывод процессов в журналы этой папки.
param([string]$LogDir)
. (Join-Path $PSScriptRoot 'env.ps1')

if (($env:SQB_KNOWLEDGE_SECRET + '').Length -lt 32) {
    Write-Host 'В .env нужен SQB_KNOWLEDGE_SECRET — не менее 32 случайных символов (образец в .env.example).' -ForegroundColor Red
    exit 1
}
# База документов — отдельная база knowledge в том же PostgreSQL (порт 5432).
if (-not $env:KNOWLEDGE_DATABASE_URL) {
    $password = if ($env:KNOWLEDGE_DB_PASSWORD) { $env:KNOWLEDGE_DB_PASSWORD } else { 'knowledge-local-only' }
    $env:KNOWLEDGE_DATABASE_URL = "postgresql://knowledge:$password@localhost:5432/knowledge"
}
if (-not ($env:NOVA_BASE_URL -and $env:NOVA_API_KEY -and $env:NOVA_COLLECTION)) {
    Write-Host 'Nova не настроена (NOVA_BASE_URL, NOVA_API_KEY, NOVA_COLLECTION): документы работают, анализ — нет.' -ForegroundColor Yellow
}

$dir = Join-Path (Split-Path $PSScriptRoot -Parent) 'knowledge'
$python = Join-Path $dir '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host 'Первый запуск: окружение Python для модуля документов...'
    Push-Location $dir
    py -3 -m venv .venv
    if (-not $?) { python -m venv .venv }
    & $python -m pip install -r requirements.txt
    Pop-Location
    if (-not (Test-Path $python)) { Write-Host 'Не удалось создать .venv: нужен Python 3.13.' -ForegroundColor Red; exit 1 }
}

$commands = @(
    @('API документов (8010)', '-m uvicorn knowledge_service.main:app --host 127.0.0.1 --port 8010', 'documents-api'),
    @('Подготовка файлов', '-m knowledge_service.worker', 'documents-worker'),
    @('Анализ через Nova', '-m knowledge_service.analysis_worker', 'documents-analysis')
)
# Просмотр Word: без Docker нет LibreOffice, его заменяет установленный Word
# (document-preview\word_service.py, порт 8020). Адрес задаётся до запуска
# окон, чтобы его получили API документов и анализ.
$word = Test-Path 'Registry::HKEY_CLASSES_ROOT\Word.Application'
if ($word -and -not $env:DOCUMENT_PREVIEW_URL) {
    $preview = Join-Path (Split-Path $PSScriptRoot -Parent) 'document-preview'
    $commands = @(, @('Просмотр Word (8020)', "-m uvicorn word_service:app --app-dir '$preview' --host 127.0.0.1 --port 8020 --limit-concurrency 4", 'word-preview')) + $commands
    $env:DOCUMENT_PREVIEW_URL = 'http://127.0.0.1:8020'
}
foreach ($c in $commands) {
    $title = $c[0]; $command = "`$Host.UI.RawUI.WindowTitle = '$title'; & '$python' $($c[1])"
    # Окна наследуют переменные этого окна, в том числе ключ Nova, — в командной строке его нет.
    if ($LogDir) {
        $log = Join-Path $LogDir $c[2]
        Start-Process powershell -WorkingDirectory $dir -NoNewWindow -ArgumentList '-Command', "`"$command`"" `
            -RedirectStandardOutput "$log.log" -RedirectStandardError "$log.err.log"
    } else {
        Start-Process powershell -WorkingDirectory $dir -ArgumentList '-NoExit', '-Command', $command
    }
    Write-Host "Запущено: $title"
}
if (-not $env:DOCUMENT_PREVIEW_URL) {
    Write-Host 'Просмотр Word внутри платформы недоступен: нет Word и Docker. Оригиналы открываются и скачиваются, анализ работает.'
}
Write-Host 'Старые файлы .doc без Docker не подготавливаются — сохраните их как .docx перед загрузкой.'
