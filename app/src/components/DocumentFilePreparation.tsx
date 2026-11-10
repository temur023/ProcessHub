import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, FileSearch, RefreshCw, Search, Square } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { KnowledgeRequestError } from '@/lib/knowledge'
import { cancelFilePreparation, getFilePreparation, startFilePreparation } from '@/lib/documents'
import type { DocumentPreparationState } from '@/lib/knowledge'

const pageSize = 50
const message = (error: unknown) => error instanceof Error ? error.message : 'Не удалось загрузить подготовку документа'

export function DocumentFilePreparation({ fileId, owner, fragmentId }: { fileId: string; owner: boolean; fragmentId?: string }) {
  const [state, setState] = useState<DocumentPreparationState>()
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [action, setAction] = useState<'start' | 'cancel' | null>(null)
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const textTop = useRef<HTMLDivElement>(null)
  const bookmarkHandled = useRef(false)
  function changePage(next: number) {
    setPage(next)
    textTop.current?.scrollIntoView({ block: 'start' })
  }

  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function load() {
      try {
        const next = await getFilePreparation(fileId, controller.signal)
        if (controller.signal.aborted) return
        setState(next)
        if (!bookmarkHandled.current && fragmentId) {
          const index = next.source?.blocks.findIndex(block => block.id === fragmentId) ?? -1
          if (index >= 0) { setPage(Math.floor(index / pageSize)); bookmarkHandled.current = true }
        }
        setError('')
        const active = next.job?.status === 'queued' || next.job?.status === 'running'
        timer = setTimeout(() => { if (!document.hidden) void load(); else timer = setTimeout(() => void load(), 15_000) }, active ? 2000 : 30_000)
      } catch (cause) {
        if (controller.signal.aborted) return
        setError(message(cause))
        if (cause instanceof KnowledgeRequestError && [401, 403, 404].includes(cause.status)) setState(undefined)
      }
    }
    if (!action) void load()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [fileId, revision, action, fragmentId])

  useEffect(() => {
    if (!action) return
    const controller = new AbortController()
    async function mutate() {
      try {
        const next = await (action === 'cancel' ? cancelFilePreparation : startFilePreparation)(fileId, controller.signal)
        if (!controller.signal.aborted) { setState(next); setError('') }
      } catch (cause) {
        if (!controller.signal.aborted) {
          setError(message(cause))
          if (cause instanceof KnowledgeRequestError && [401, 403, 404].includes(cause.status)) setState(undefined)
        }
      } finally { if (!controller.signal.aborted) setAction(null) }
    }
    void mutate()
    return () => controller.abort()
  }, [action, fileId])

  const source = state?.source
  const active = state?.job?.status === 'queued' || state?.job?.status === 'running'
  const label = source?.status === 'ready' ? 'Текст подготовлен'
    : source?.status === 'partial' ? 'Требуется проверка частей документа'
    : source?.status === 'needs_ocr' ? 'Требуется распознавание текста'
    : state?.job?.status === 'running' ? 'Извлекаем текст'
    : state?.job?.status === 'queued' ? 'Ожидает обработки'
    : state?.job?.status === 'failed' ? 'Подготовка не завершена'
    : state?.job?.status === 'cancelled' ? 'Подготовка отменена' : 'Документ ещё не подготовлен'
  const filtered = source?.blocks.filter(block => `${block.label} ${block.text}`.toLocaleLowerCase('ru').includes(query.trim().toLocaleLowerCase('ru'))) ?? []
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const currentPage = Math.min(page, totalPages - 1)
  const sourceId = source?.id

  useEffect(() => {
    if (fragmentId && sourceId) document.getElementById(`source-${fragmentId}`)?.scrollIntoView({ block: 'center' })
  }, [fragmentId, sourceId, currentPage])

  return <section className="min-w-0" aria-label="Подготовка документа">
    <div className="flex items-start justify-between gap-4">
      <div><h3 className="text-base font-semibold">Текст документа</h3><p className="mt-1 text-sm leading-6 text-muted-foreground">Производный текст с указанием страниц, пунктов или ячеек. Проверяйте значимые сведения по оригиналу.</p></div>
      <div className="flex shrink-0 items-center gap-2"><Button variant="ghost" size="sm" disabled={!!action} aria-label="Обновить подготовку" onClick={() => setRevision(value => value + 1)}><RefreshCw className="size-4" /></Button>{owner && active && <Button variant="outline" size="sm" disabled={!!action} className="gap-2" onClick={() => setAction('cancel')}><Square className="size-3.5" />Отменить</Button>}</div>
    </div>
    {error && <div role="alert" className="mt-4 rounded-lg border border-destructive/30 p-4 text-sm"><p>{error}</p><Button variant="outline" size="sm" className="mt-3" disabled={!!action} onClick={() => setRevision(value => value + 1)}>Повторить загрузку</Button></div>}
    {!state && !error ? <div role="status" className="mt-6 flex items-center gap-2 text-sm text-muted-foreground"><Spinner className="size-4" />Проверяем подготовку…</div>
    : state && <>
      <div role="status" className="mt-5 flex items-start gap-3 rounded-lg bg-muted/40 px-4 py-3">
        {active || action ? <Spinner className="mt-0.5 size-4 shrink-0" /> : source?.status === 'ready' ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" /> : source ? <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600" /> : <FileSearch className="mt-0.5 size-4 shrink-0 text-muted-foreground" />}
        <div className="min-w-0 flex-1"><p className="text-sm font-medium">{label}</p><p className="mt-1 text-xs leading-5 text-muted-foreground">{(state.job?.error_code === 'instruction_deleted' ? 'Обработка была остановлена при удалении документа. Оригинал сохранён.' : state.job?.message) ?? (active ? 'Оригинал сохранён. Можно покинуть страницу — обработка продолжится.' : source?.status === 'needs_ocr' ? 'В файле не найден доступный текст. Для скана требуется распознавание; оригинал сохранён.' : source ? 'Извлечённый текст не заменяет оригинал и не подтверждает утверждение документа.' : 'Оригинал сохранён; подготовка выделит текст и его расположение в документе.')}</p></div>
        {owner && !active && !source && <Button size="sm" disabled={!!action} onClick={() => setAction('start')}>{action ? 'Запускаем…' : state.job ? 'Повторить обработку' : 'Подготовить текст'}</Button>}
      </div>
      {!!source?.warnings.length && <details className="mt-4 rounded-lg border px-4 py-3" open={source.status !== 'ready'}><summary className="cursor-pointer text-sm font-medium">Что проверить по оригиналу · {source.warnings.length}</summary><ul className="mt-3 space-y-2 text-sm leading-6 text-muted-foreground">{source.warnings.map((warning, index) => <li key={index} className="flex gap-2"><span aria-hidden="true">•</span><span>{typeof warning.locator?.page === 'number' && <strong className="font-medium">Страница {warning.locator.page}: </strong>}{warning.message}</span></li>)}</ul></details>}
      {!!source?.blocks.length && <>
        <div ref={textTop} className="mt-6 flex scroll-mt-6 items-center justify-between gap-4"><p className="shrink-0 text-sm text-muted-foreground">Фрагментов: {source.stats.blocks_total}</p><div className="relative w-80 min-w-0"><Search className="absolute left-3 top-2.5 size-4 text-muted-foreground" /><Input aria-label="Поиск по тексту документа" placeholder="Найти в тексте" className="pl-9" value={query} onChange={event => { setQuery(event.target.value); setPage(0) }} /></div></div>
        <ol className="mt-4 divide-y rounded-lg border px-5">{filtered.slice(currentPage * pageSize, (currentPage + 1) * pageSize).map(block => <li id={`source-${block.id}`} key={block.id} aria-current={block.id === fragmentId ? true : undefined} className={`scroll-mt-6 py-5 ${block.id === fragmentId ? 'rounded-lg bg-muted/40 px-4' : ''}`}><p className="text-xs font-medium text-muted-foreground">{block.label}</p><p className={`mt-2 whitespace-pre-wrap break-words text-sm leading-7 ${block.kind === 'heading' ? 'font-semibold' : ''}`}>{block.text}</p></li>)}</ol>
        {!filtered.length && <p className="mt-4 text-sm text-muted-foreground">По этому запросу фрагменты не найдены.</p>}
        {totalPages > 1 && <nav aria-label="Страницы извлечённого текста" className="mt-4 flex items-center justify-between"><p aria-live="polite" className="text-xs text-muted-foreground">{currentPage + 1} из {totalPages}</p><div className="flex gap-2"><Button variant="outline" size="sm" disabled={!currentPage} aria-label="Предыдущие фрагменты" onClick={() => changePage(currentPage - 1)}><ChevronLeft className="size-4" /></Button><Button variant="outline" size="sm" disabled={currentPage + 1 >= totalPages} aria-label="Следующие фрагменты" onClick={() => changePage(currentPage + 1)}><ChevronRight className="size-4" /></Button></div></nav>}
      </>}
    </>}
  </section>
}
