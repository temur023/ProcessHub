import { useEffect, useRef, useState } from 'react'
import { Download, FileX2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Spinner } from '@/components/ui/spinner'
import { apiFetch } from '@/lib/api'

/** Встроенный draw.io без панелей правки (``chrome=0``): только просмотр, со страницами и масштабом. */
const VIEWER_URL = '/drawio/?' + new URLSearchParams({ embed: '1', proto: 'json', chrome: '0', lang: 'ru', stealth: '1', spin: '1' })

interface Source { blob: Blob; fileName: string; uploadedAt: string | null; uploadedBy: string | null; xml: string | null }
type State = { status: 'loading' } | { status: 'missing'; message: string } | { status: 'failed'; message: string } | { status: 'ready'; source: Source }

function fileNameFrom(header: string | null, fallback: string): string {
  const encoded = header?.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (encoded) {
    try { return decodeURIComponent(encoded) } catch { /* имя без кодировки — ниже */ }
  }
  return header?.match(/filename="([^"]+)"/i)?.[1] ?? fallback
}

async function loadSource(processId: string, title: string): Promise<State> {
  let res: Response
  try { res = await apiFetch(`/api/v1/import/${encodeURIComponent(processId)}/source`) }
  catch { return { status: 'failed', message: 'Не удалось связаться с сервером. Проверьте соединение и повторите.' } }
  if (res.status === 404) {
    const body = await res.json().catch(() => null) as { detail?: unknown } | null
    return { status: 'missing', message: typeof body?.detail === 'string' ? body.detail : 'Оригинал файла не сохранён.' }
  }
  if (!res.ok) return { status: 'failed', message: `Сервер вернул ${res.status}. Повторите позже.` }
  const blob = await res.blob()
  const text = await blob.text()
  const by = res.headers.get('x-uploaded-by')
  return {
    status: 'ready',
    source: {
      blob,
      fileName: fileNameFrom(res.headers.get('content-disposition'), `${title}.drawio`),
      uploadedAt: res.headers.get('x-uploaded-at'),
      uploadedBy: by ? decodeURIComponent(by) : null,
      xml: /<mxfile[\s>]|<mxGraphModel[\s>]/.test(text) ? text : null,
    },
  }
}

function download(source: Source) {
  const url = URL.createObjectURL(source.blob)
  const link = document.createElement('a')
  link.href = url
  link.download = source.fileName
  link.click()
  URL.revokeObjectURL(url)
}

/** Файл, из которого карту загрузили последним, — как есть, без пересборки платформой. */
export function SourceFileDialog({ processId, title, onClose }: { processId: string; title: string; onClose: () => void }) {
  const [state, setState] = useState<State>({ status: 'loading' })
  useEffect(() => {
    let alive = true
    void loadSource(processId, title).then((next) => { if (alive) setState(next) })
    return () => { alive = false }
  }, [processId, title])

  const source = state.status === 'ready' ? state.source : null
  const uploaded = source?.uploadedAt
    ? `Загружен ${new Date(source.uploadedAt).toLocaleString('ru-RU', { day: '2-digit', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' })}${source.uploadedBy ? ` · ${source.uploadedBy}` : ''}`
    : null

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className={source?.xml ? 'flex h-[85vh] flex-col sm:max-w-6xl' : 'sm:max-w-md'}>
        <DialogHeader className="pr-8">
          <DialogTitle className="break-words">Исходный файл: {title}</DialogTitle>
          <DialogDescription>
            {source ? <>{source.fileName}{uploaded && <> · {uploaded}</>}</> : 'Файл, из которого карту загрузили последним, — без правок платформы.'}
          </DialogDescription>
        </DialogHeader>
        {state.status === 'loading' && <div role="status" className="flex items-center gap-2 py-8 text-sm text-muted-foreground"><Spinner className="size-4" />Загружаем файл…</div>}
        {(state.status === 'missing' || state.status === 'failed') && (
          <div role={state.status === 'failed' ? 'alert' : undefined} className="flex items-start gap-3 rounded-lg border p-4 text-sm leading-6 text-muted-foreground">
            <FileX2 className="mt-0.5 size-4 shrink-0" />{state.message}
          </div>
        )}
        {source && (source.xml
          ? <DrawioViewer xml={source.xml} title={source.fileName} />
          : <p className="text-sm leading-6 text-muted-foreground">Это не файл draw.io, поэтому просмотр здесь недоступен. Скачайте файл и откройте его в программе, где его создавали.</p>)}
        {source && (
          <div className="flex justify-end">
            <Button variant="outline" onClick={() => download(source)}><Download className="size-4" />Скачать оригинал</Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function DrawioViewer({ xml, title }: { xml: string; title: string }) {
  const frameRef = useRef<HTMLIFrameElement>(null)
  const [ready, setReady] = useState(false)
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frameRef.current?.contentWindow || typeof event.data !== 'string') return
      let message: { event?: string }
      try { message = JSON.parse(event.data) } catch { return }
      if (message.event === 'init') {
        frameRef.current?.contentWindow?.postMessage(JSON.stringify({ action: 'load', xml, title }), window.location.origin)
      } else if (message.event === 'load') {
        setReady(true)
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [xml, title])

  return (
    <div className="relative min-h-0 flex-1 overflow-hidden rounded-lg border bg-white">
      {!ready && <div role="status" className="absolute inset-0 flex items-center justify-center gap-2 text-sm text-slate-500"><Spinner className="size-4" />Открываем в draw.io…</div>}
      <iframe ref={frameRef} src={VIEWER_URL} title={`Просмотр ${title}`} className="size-full border-0" />
    </div>
  )
}
