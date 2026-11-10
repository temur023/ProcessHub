# Word вместо LibreOffice (convert.py) — для компьютера без Docker.
# Открывает переданную копию без макросов и обновления связей и сохраняет PDF
# (или DOCX для подготовки текста); исходный файл не меняется.
param(
    [Parameter(Mandatory)][string]$Source,
    [Parameter(Mandatory)][string]$Output,
    [ValidateSet('pdf', 'docx')][string]$Target = 'pdf',
    [ValidateSet('markup', 'clean')][string]$Mode = 'markup',
    [Parameter(Mandatory)][string]$PidFile
)
$ErrorActionPreference = 'Stop'
$started = Get-Date
$word = New-Object -ComObject Word.Application
# Номер процесса — word_service.py завершает его, если Word завис.
Get-Process WINWORD | Where-Object { $_.StartTime -ge $started.AddSeconds(-1) } |
    Select-Object -First 1 -ExpandProperty Id | Set-Content -Path $PidFile
try {
    $word.Visible = $false
    $word.DisplayAlerts = 0           # wdAlertsNone
    $word.AutomationSecurity = 3      # msoAutomationSecurityForceDisable: макросы не выполняются
    $word.Options.UpdateLinksAtOpen = $false
    # Чистый вид правит только эту временную копию в памяти, поэтому её
    # открываем на запись; пароль-заглушка не даёт Word ждать ввода пароля.
    $document = $word.Documents.Open($Source, $false, ($Mode -ne 'clean'), $false, 'x', 'x', $false, 'x', 'x', 0)
    try {
        if ($Mode -eq 'clean') {
            # Как text_in_force в convert.py: отклонить предложенные правки,
            # снять выделение маркером и заливку символов.
            $document.TrackRevisions = $false
            $document.Revisions.RejectAll()
            if ($document.Revisions.Count -gt 0) { throw 'Tracked changes remain' }
            $document.Content.HighlightColorIndex = 0                           # wdNoHighlight
            $document.Content.Font.Shading.BackgroundPatternColor = -16777216   # wdColorAutomatic
        }
        if ($Target -eq 'docx') {
            # Источник для подготовки текста: правки не принимаем и не отклоняем.
            $document.SaveAs2($Output, 16)                                      # wdFormatDocumentDefault
        } else {
            $item = if ($Mode -eq 'clean') { 0 } else { 7 }                     # wdExportDocumentContent / WithMarkup
            $document.ExportAsFixedFormat($Output, 17, $false, 0, 0, 1, 1, $item, $true, $true, 1)  # PDF, закладки по заголовкам
        }
    } finally {
        $document.Close([ref]0)
    }
} finally {
    $word.Quit()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word)
}
