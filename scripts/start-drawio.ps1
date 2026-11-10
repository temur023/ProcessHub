# Редактор draw.io без Docker (корпоративный компьютер): та же версия, что в
# docker-compose.yml (jgraph/drawio:32.0.2), на 127.0.0.1:8081 — туда Vite
# пересылает /drawio/ (app/vite.config.ts). Карты на diagrams.net не уходят.
# Запуск из корня проекта:  powershell -ExecutionPolicy Bypass -File scripts\start-drawio.ps1
$root = Split-Path $PSScriptRoot -Parent
$webapp = Join-Path $root 'drawio-webapp'
# Оборванная распаковка (например, путь длиннее 260 символов) не должна стать редактором.
$ErrorActionPreference = 'Stop'

if (-not (Test-Path (Join-Path $webapp 'index.html'))) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $bundle = Join-Path $root 'transfer\drawio-webapp.zip'
    $unpack = Join-Path $root 'drawio-webapp.tmp'
    if (Test-Path $unpack) { Remove-Item $unpack -Recurse -Force }
    if (Test-Path $bundle) {
        # Комплект переноса — копия редактора из Docker-образа, внутри папка drawio.
        Write-Host 'Первый запуск: редактор из transfer\drawio-webapp.zip...'
        [IO.Compression.ZipFile]::ExtractToDirectory($bundle, $unpack)
        Move-Item (Join-Path $unpack 'drawio') $webapp
    } else {
        Write-Host 'Первый запуск: скачивается draw.war 32.0.2 с GitHub (46 МБ)...'
        $war = "$unpack.war"
        # curl.exe, а не Invoke-WebRequest: тот идёт мимо прокси из HTTPS_PROXY,
        # и прокси банка отвечает «Cache Access Denied».
        curl.exe -fsSL -o $war 'https://github.com/jgraph/drawio/releases/download/v32.0.2/draw.war'
        if (-not (Test-Path $war) -or (Get-FileHash $war -Algorithm SHA256).Hash -ne '3CB8ABEC8E9BFC7504760C9CDC9194ECF7E8DE178AA2A1D668801C32ECF1A1A7') {
            Remove-Item $war -ErrorAction SilentlyContinue
            Write-Host 'draw.war не скачался или не совпал с выпуском 32.0.2 на GitHub. Положите комплект в transfer\drawio-webapp.zip.' -ForegroundColor Red
            exit 1
        }
        [IO.Compression.ZipFile]::ExtractToDirectory($war, $unpack)
        Remove-Item $war
        Move-Item $unpack $webapp
    }
    if (Test-Path $unpack) { Remove-Item $unpack -Recurse -Force }
}

& (Join-Path $root 'backend\.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'drawio_server.py') $webapp 8081
