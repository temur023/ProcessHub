import { lazy, Suspense, useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft, Download, Eye, FileSpreadsheet, FileText, Paperclip, Presentation, Trash2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Spinner } from '@/components/ui/spinner'
import { apiFetch } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { formatFileSize } from '@/lib/presentation'

/** Тот же просмотр страниц, что у документов: pdf.js, а не встроенный в браузер плагин. */
const PdfViewer = lazy(() => import('./DocumentPdfViewer'))

type Kind = 'approved' | 'regulation' | 'presentation' | 'other'
interface Attachment {
  id: string; kind: Kind; fileName: string; mediaType: string; size: number
  uploadedAt: string; uploadedBy: string | null; uploadedById: number | null
}

const KINDS: Array<{ id: Kind; label: string; hint: string }> = [
  { id: 'approved', label: 'Утверждённая версия', hint: 'Подписанный или утверждённый экземпляр, обычно PDF' },
  { id: 'regulation', label: 'Регламент и таблицы', hint: 'Excel: регламент, паспорт, KPI, расчёты' },
  { id: 'presentation', label: 'Презентации', hint: 'Защита проекта, обоснование, TO-BE' },
  { id: 'other', label: 'Другое', hint: 'Прочие сопроводительные документы' },
]
const ACCEPT = '.pdf,.xlsx,.xls,.pptx,.ppt,.docx,.doc'
/** Открываются в окне: PDF как есть, Excel и PowerPoint — PDF-копией от сервиса просмотра. */
const PREVIEWABLE = new Set(['pdf', 'xlsx', 'xls', 'pptx', 'ppt'])

const extension = (name: string) => name.split('.').pop()?.toLowerCase() ?? ''
/** Вид по расширению — подсказка, сотрудник может выбрать другой. */
function guessKind(name: string): Kind {
  const ext = extension(name)
  if (ext === 'pdf') return 'approved'
  if (ext === 'xlsx' || ext === 'xls') return 'regulation'
  if (ext === 'pptx' || ext === 'ppt') return 'presentation'
  return 'other'
}
function FileIcon({ name }: { name: string }) {
  const ext = extension(name)
  const Icon = ext.startsWith('xls') ? FileSpreadsheet : ext.startsWith('ppt') ? Presentation : FileText
  const tone = ext.startsWith('xls') ? 'text-emerald-500' : ext.startsWith('ppt') ? 'text-orange-500' : ext === 'pdf' ? 'text-rose-500' : 'text-sky-500'
  return <Icon aria-hidden="true" className={`size-5 shrink-0 ${tone}`} />
}
const when = (value: string) => new Date(value).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }).replace(',', '')

async function failure(res: Response, fallback: string) {
  const body = await res.json().catch(() => null) as { detail?: unknown } | null
  return typeof body?.detail === 'string' ? body.detail : fallback
}

/**
 * Материалы карты: утверждённый PDF, регламент в Excel, презентации. Хранятся
 * как есть и со всей историей: новая загрузка не стирает прежнюю, последняя по
 * каждому виду отмечена «Актуальная». PDF открывается здесь же, остальное —
 * скачиванием.
 */
export function MapMaterialsDialog({ processId, title, onClose }: { processId: string; title: string; onClose: () => void }) {
  const { user } = useAuth()
  const base = `/api/v1/processes/${encodeURIComponent(processId)}/attachments`
  const [items, setItems] = useState<Attachment[]>()
  const [error, setError] = useState('')
  const [viewing, setViewing] = useState<Attachment>()

  const [reload, setReload] = useState(0)
  const load = useCallback(() => setReload((value) => value + 1), [])
  useEffect(() => {
    const controller = new AbortController()
    const fetchList = async (): Promise<{ items?: Attachment[]; error?: string }> => {
      try {
        const res = await apiFetch(base, { signal: controller.signal })
        if (!res.ok) return { error: await failure(res, `Сервер вернул ${res.status}`) }
        return { items: await res.json() as Attachment[] }
      } catch { return { error: 'Не удалось связаться с сервером. Проверьте соединение и повторите.' } }
    }
    void fetchList().then((result) => {
      if (controller.signal.aborted) return
      if (result.items) { setItems(result.items); setError('') } else setError(result.error ?? '')
    })
    return () => controller.abort()
  }, [base, reload])

  const href = (item: Attachment) => `${base}/${encodeURIComponent(item.id)}`

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className={viewing ? 'flex h-[88vh] flex-col sm:max-w-6xl' : 'max-h-[88vh] overflow-y-auto sm:max-w-3xl'}>
        {viewing ? <>
          <DialogHeader className="pr-8">
            <DialogTitle className="break-words">{viewing.fileName}</DialogTitle>
            <DialogDescription>{title} · {when(viewing.uploadedAt)}{viewing.uploadedBy && ` · ${viewing.uploadedBy}`}</DialogDescription>
          </DialogHeader>
          <PdfPreview url={`${href(viewing)}/preview`} filename={viewing.fileName} />
          <div className="flex justify-between gap-2">
            <Button variant="ghost" onClick={() => setViewing(undefined)}><ArrowLeft className="size-4" />К материалам</Button>
            <Button variant="outline" asChild><a href={href(viewing)} download={viewing.fileName}><Download className="size-4" />Скачать</a></Button>
          </div>
        </> : <>
          <DialogHeader className="pr-8">
            <DialogTitle className="break-words">Материалы карты: {title}</DialogTitle>
            <DialogDescription>Утверждённая версия, регламент, презентации — файлы хранятся как есть, прежние загрузки не стираются.</DialogDescription>
          </DialogHeader>
          <UploadForm base={base} onUploaded={() => void load()} />
          {error ? <div role="alert" className="rounded-lg border p-4 text-sm text-muted-foreground">{error} <Button size="sm" variant="outline" className="ml-2" onClick={() => void load()}>Повторить</Button></div>
            : !items ? <div role="status" className="flex items-center gap-2 py-6 text-sm text-muted-foreground"><Spinner className="size-4" />Загружаем материалы…</div>
            : items.length === 0 ? <div className="rounded-xl border border-dashed py-10 text-center"><Paperclip className="mx-auto size-7 text-muted-foreground" /><p className="mt-3 text-sm font-medium">Материалов пока нет</p><p className="mt-1 text-xs text-muted-foreground">Прикрепите утверждённый PDF, регламент в Excel или презентацию.</p></div>
            : <div className="space-y-5">{KINDS.map((kind) => {
              const group = items.filter((item) => item.kind === kind.id)
              if (!group.length) return null
              return <section key={kind.id} aria-label={kind.label}>
                <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{kind.label} · {group.length}</h3>
                <ul className="mt-2 divide-y rounded-xl border bg-card">{group.map((item, index) => (
                  <MaterialRow key={item.id} item={item} latest={index === 0} href={href(item)}
                    canRemove={user.isAdmin || item.uploadedById === user.id}
                    onView={PREVIEWABLE.has(extension(item.fileName)) ? () => setViewing(item) : undefined}
                    onRemoved={() => void load()} />
                ))}</ul>
              </section>
            })}</div>}
        </>}
      </DialogContent>
    </Dialog>
  )
}

function MaterialRow({ item, latest, href, canRemove, onView, onRemoved }: {
  item: Attachment; latest: boolean; href: string; canRemove: boolean; onView?: () => void; onRemoved: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  async function remove() {
    setBusy(true)
    try {
      const res = await apiFetch(href, { method: 'DELETE' })
      if (!res.ok) { toast.error('Материал не удалён', { description: await failure(res, `Сервер вернул ${res.status}`) }); return }
      toast.success(`«${item.fileName}» удалён`)
      onRemoved()
    } catch { toast.error('Не удалось связаться с сервером') }
    finally { setBusy(false); setConfirming(false) }
  }
  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <FileIcon name={item.fileName} />
      <div className="min-w-0 flex-1">
        <p className="flex min-w-0 items-center gap-2 text-sm">
          <span className="truncate font-medium" title={item.fileName}>{item.fileName}</span>
          {latest && <span className="shrink-0 rounded border border-emerald-500/40 px-1.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">Актуальная</span>}
        </p>
        <p className="mt-0.5 truncate text-xs tabular-nums text-muted-foreground">{formatFileSize(item.size)} · {when(item.uploadedAt)}{item.uploadedBy && ` · ${item.uploadedBy}`}</p>
      </div>
      {confirming ? (
        <div className="flex shrink-0 items-center gap-1">
          <span className="mr-1 text-xs text-muted-foreground">Удалить?</span>
          <Button size="sm" variant="destructive" disabled={busy} onClick={() => void remove()}>{busy ? <Spinner className="size-4" /> : 'Да'}</Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirming(false)}>Нет</Button>
        </div>
      ) : (
        <div className="flex shrink-0 items-center gap-0.5">
          {onView && <Button size="sm" variant="ghost" onClick={onView} aria-label={`Открыть ${item.fileName}`}><Eye className="size-4" />Открыть</Button>}
          <Button size="sm" variant="ghost" asChild><a href={href} download={item.fileName} aria-label={`Скачать ${item.fileName}`} title="Скачать"><Download className="size-4" /></a></Button>
          {canRemove && <Button size="sm" variant="ghost" onClick={() => setConfirming(true)} aria-label={`Удалить ${item.fileName}`} title="Удалить" className="text-muted-foreground hover:text-destructive"><Trash2 className="size-4" /></Button>}
        </div>
      )}
    </li>
  )
}

function UploadForm({ base, onUploaded }: { base: string; onUploaded: () => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File>()
  const [kind, setKind] = useState<Kind>('approved')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function choose(next: File | undefined) {
    setFile(next); setError('')
    if (next) setKind(guessKind(next.name))
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!file || busy) return
    setBusy(true); setError('')
    const form = new FormData()
    form.append('kind', kind)
    form.append('file', file)
    try {
      const res = await apiFetch(base, { method: 'POST', body: form })
      if (!res.ok) { setError(await failure(res, `Сервер вернул ${res.status}`)); return }
      toast.success(`«${file.name}» прикреплён`)
      choose(undefined)
      if (input.current) input.current.value = ''
      onUploaded()
    } catch { setError('Не удалось связаться с сервером. Проверьте соединение и повторите.') }
    finally { setBusy(false) }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="rounded-xl border bg-muted/20 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <input ref={input} type="file" accept={ACCEPT} className="sr-only" id="map-material-file" onChange={(event) => choose(event.target.files?.[0])} />
        <Button type="button" variant="outline" size="sm" onClick={() => input.current?.click()}><Paperclip className="size-4" />{file ? 'Другой файл' : 'Выбрать файл'}</Button>
        <span className="min-w-0 flex-1 truncate text-sm" title={file?.name}>{file ? <>{file.name} <span className="text-xs text-muted-foreground">· {formatFileSize(file.size)}</span></> : <span className="text-xs text-muted-foreground">PDF, Excel, PowerPoint или Word, до 30 МБ</span>}</span>
        <Select value={kind} onValueChange={(value) => setKind(value as Kind)}>
          <SelectTrigger size="sm" aria-label="Вид материала" className="w-52 text-xs"><SelectValue>{KINDS.find((k) => k.id === kind)?.label}</SelectValue></SelectTrigger>
          <SelectContent>{KINDS.map((k) => <SelectItem key={k.id} value={k.id} className="text-xs"><span className="flex flex-col"><span>{k.label}</span><span className="text-[11px] text-muted-foreground">{k.hint}</span></span></SelectItem>)}</SelectContent>
        </Select>
        <Button type="submit" size="sm" disabled={!file || busy}>{busy ? <Spinner className="size-4" /> : <Upload className="size-4" />}Прикрепить</Button>
      </div>
      {error && <p role="alert" className="mt-2 text-xs text-destructive">{error}</p>}
    </form>
  )
}

function PdfPreview({ url, filename }: { url: string; filename: string }) {
  const [state, setState] = useState<{ url: string; blob?: Blob; error?: string }>({ url })
  useEffect(() => {
    const controller = new AbortController()
    const fetchBlob = async (): Promise<{ blob?: Blob; error?: string }> => {
      try {
        const res = await apiFetch(url, { signal: controller.signal })
        return res.ok ? { blob: await res.blob() } : { error: await failure(res, `Сервер вернул ${res.status}`) }
      } catch { return { error: 'Не удалось загрузить файл. Скачайте его кнопкой ниже.' } }
    }
    void fetchBlob().then((result) => { if (!controller.signal.aborted) setState({ url, ...result }) })
    return () => controller.abort()
  }, [url])
  const current = state.url === url ? state : { url }
  return (
    <div className="min-h-0 flex-1 overflow-hidden rounded-lg border bg-muted/20">
      {current.error ? <p role="alert" className="p-6 text-sm text-muted-foreground">{current.error}</p>
        : !current.blob ? <div role="status" className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground"><Spinner className="size-4" />Открываем файл…</div>
        : <Suspense fallback={<div className="p-12 text-center text-sm text-muted-foreground">Открываем просмотр…</div>}><PdfViewer blob={current.blob} filename={filename} expanded /></Suspense>}
    </div>
  )
}
