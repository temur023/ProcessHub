import { lazy, Suspense, useEffect, useState } from 'react'
import { Maximize2, FileText } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Spinner } from '@/components/ui/spinner'
import { getDocumentPreview, type DocumentFile } from '@/lib/documents'

const PdfViewer = lazy(() => import('./DocumentPdfViewer'))

export function DocumentOriginalView({ file, enabled }: { file: DocumentFile; enabled: boolean }) {
  const [blob, setBlob] = useState<Blob>()
  const [expanded, setExpanded] = useState(false)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [markup, setMarkup] = useState(false)
  const revised = !!file.preparation?.source?.warnings.some(warning => warning.code === 'tracked_changes')
  const supported = /\.(pdf|docx?)$/i.test(file.filename)
  useEffect(() => {
    if (!enabled || !supported) return
    const controller = new AbortController()
    void getDocumentPreview(file.id, controller.signal, markup ? 'markup' : 'clean').then(blob => {
      if (controller.signal.aborted) return
      setBlob(blob); setError('')
    }).catch(cause => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Не удалось открыть документ')
    })
    return () => controller.abort()
  }, [file.id, retry, enabled, supported, markup])
  const pdf = file.media_type === 'application/pdf'
  if (!enabled || !supported) return <section className="flex min-h-72 flex-col items-center justify-center rounded-xl border bg-muted/20 p-8 text-center"><FileText className="size-9 text-muted-foreground" /><h2 className="mt-4 text-base font-medium">{enabled ? 'Открыть в исходном приложении' : 'Просмотр файла не назначен'}</h2><p className="mt-2 max-w-lg text-sm leading-6 text-muted-foreground">{enabled ? 'Скачайте оригинал и откройте его в Excel. Книга сохранена со всеми листами, формулами и оформлением.' : 'Для просмотра исходного файла требуется право работы с оригиналами документов.'}</p></section>
  if (error) return <section role="alert" className="rounded-xl border p-8"><h2 className="font-medium">Просмотр документа недоступен</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">{error}</p><Button className="mt-4" variant="outline" onClick={() => { setBlob(undefined); setError(''); setRetry(value => value + 1) }}>Повторить</Button><p className="mt-4 text-xs text-muted-foreground">Оригинал доступен через кнопку скачивания над документом.</p></section>
  if (!blob) return <div role="status" className="flex min-h-96 items-center justify-center gap-2 rounded-xl border bg-muted/20 text-sm text-muted-foreground"><Spinner className="size-4" />Открываем документ…</div>
  return <section aria-label="Просмотр исходного документа" className="overflow-hidden rounded-xl border bg-muted/30">
    <div className="flex flex-col gap-2 border-b bg-card px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-5"><p className="text-xs leading-5 text-muted-foreground">{pdf ? 'Исходный PDF · все страницы документа' : markup ? 'Word с непринятыми исправлениями и выделениями, как в оригинале.' : revised ? 'Действующий текст: непринятые исправления Word не применены, выделения скрыты. Оригинал — в кнопке «Скачать оригинал».' : 'Просмотр Word · PDF-копия для чтения. Исходный DOC / DOCX — в кнопке «Скачать оригинал».'}</p><div className="flex shrink-0 items-center gap-1">{!pdf && revised && <Button variant="ghost" size="sm" aria-pressed={markup} onClick={() => { setBlob(undefined); setMarkup(value => !value) }}>{markup ? 'Скрыть исправления' : 'Показать исправления'}</Button>}<Button variant="ghost" size="sm" onClick={() => setExpanded(true)}>Развернуть<Maximize2 className="size-3.5" /></Button></div></div>
    <Suspense fallback={<div className="p-12 text-center text-sm text-muted-foreground">Открываем просмотр…</div>}><PdfViewer blob={blob} filename={file.filename} /></Suspense>
    <Dialog open={expanded} onOpenChange={setExpanded}><DialogContent className="flex h-dvh w-screen max-w-none flex-col gap-3 rounded-none border-0 p-4 sm:max-w-none"><DialogHeader><DialogTitle className="break-words pr-8 text-base">{file.filename}</DialogTitle><DialogDescription className="sr-only">{pdf ? 'Исходный PDF' : 'PDF-копия Word для чтения'}. Просмотр страниц и изменение масштаба.</DialogDescription></DialogHeader>{expanded && <Suspense fallback={<Spinner className="size-5" />}><PdfViewer blob={blob} filename={file.filename} expanded /></Suspense>}</DialogContent></Dialog>
  </section>
}
