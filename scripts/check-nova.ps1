# Проверка подключения к Nova RAG Gateway с этого компьютера — без платформы.
# Запуск из корня проекта:  powershell -ExecutionPolicy Bypass -File scripts\check-nova.ps1
# Ключ берётся из .env и никуда не выводится.
. (Join-Path $PSScriptRoot 'env.ps1')

$base = ($env:NOVA_BASE_URL + '').Trim().TrimEnd('/')
$key = ($env:NOVA_API_KEY + '').Trim()
$collection = if ($env:NOVA_COLLECTION) { $env:NOVA_COLLECTION.Trim() } else { 'ops_policies' }

if (-not $base) { Write-Host 'В .env не задан NOVA_BASE_URL (например http://nova-gateway.internal:11450/api/v1/services/rag).' -ForegroundColor Red; exit 1 }
if (-not $key) { Write-Host 'В .env не задан NOVA_API_KEY.' -ForegroundColor Red; exit 1 }
if ($base -match '/v1$') { Write-Host 'NOVA_BASE_URL указывается без /v1 на конце — уберите его.' -ForegroundColor Red; exit 1 }

function Call($path, $auth) {
    $headers = @{ 'X-Request-ID' = 'sqb-check-' + [guid]::NewGuid().ToString('N').Substring(0, 12) }
    if ($auth) { $headers['Authorization'] = "Bearer $key" }
    try {
        $r = Invoke-WebRequest -Uri ($base + $path) -Headers $headers -UseBasicParsing -TimeoutSec 30
        return @{ ok = $true; status = [int]$r.StatusCode; body = $r.Content; id = $headers['X-Request-ID'] }
    } catch [System.Net.WebException] {
        $resp = $_.Exception.Response
        if ($resp) {
            $text = (New-Object System.IO.StreamReader($resp.GetResponseStream())).ReadToEnd()
            return @{ ok = $false; status = [int]$resp.StatusCode; body = $text; id = $headers['X-Request-ID'] }
        }
        return @{ ok = $false; status = 0; body = $_.Exception.Message; id = $headers['X-Request-ID'] }
    } catch {
        return @{ ok = $false; status = 0; body = $_.Exception.Message; id = $headers['X-Request-ID'] }
    }
}

Write-Host "Gateway: $base"
$h = Call '/health' $false
if ($h.status -eq 0) {
    Write-Host "1. Связь: НЕТ — $($h.body)" -ForegroundColor Red
    Write-Host '   Проверьте адрес и порт и попросите ИБ открыть доступ с этого компьютера к Gateway.'
    exit 1
}
Write-Host "1. Связь: есть (GET /health -> $($h.status))" -ForegroundColor Green

$m = Call '/v1/models' $true
switch ($m.status) {
    200 { }
    401 { Write-Host "2. Ключ: НЕ ПРИНЯТ (401) — проверьте NOVA_API_KEY. X-Request-ID $($m.id)" -ForegroundColor Red; exit 1 }
    403 { Write-Host "2. Ключ: нет доступа (403) — запросите доступ в ДИИ. X-Request-ID $($m.id)" -ForegroundColor Red; exit 1 }
    default { Write-Host "2. Ключ: ответ $($m.status) — $($m.body). X-Request-ID $($m.id)" -ForegroundColor Red; exit 1 }
}
Write-Host '2. Ключ: принят' -ForegroundColor Green

$ids = @((ConvertFrom-Json $m.body).data | ForEach-Object { $_.id })
Write-Host "   Базы знаний ключа: $($ids -join ', ')"
if ($ids -contains $collection) {
    Write-Host "3. База знаний ${collection}: доступна" -ForegroundColor Green
} else {
    Write-Host "3. База знаний ${collection}: НЕ ВИДНА ключу — укажите в NOVA_COLLECTION одну из списка выше или запросите доступ в ДИИ." -ForegroundColor Red
    exit 1
}
Write-Host 'Готово: Nova подключается. Запустите сервер платформы: scripts\start-backend.ps1'
