import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, CircleDashed, Download, Loader2, Play, RotateCcw, ShieldAlert, Sparkles, X } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'
import {
  bindRegulation, cancelAnalysis, decisionLabels, functionStatusLabels, getAnalysisRun, getAnalysisRuns, getNovaStatus, reviewFinding, startAnalysis,
  type AnalysisClause, type AnalysisFunction, type AnalysisMissing, type AnalysisResult, type AnalysisReview, type AnalysisRun, type FunctionStatus, type NovaStatus, type ReviewDecision,
} from '@/lib/analysis'
import { getFilePreparation, startFilePreparation, type DocumentDetail, type DocumentEdition } from '@/lib/documents'
import type { DocumentPreparationState } from '@/lib/knowledge'
import { plural } from '@/lib/presentation'

const statusTone: Record<FunctionStatus | 'missing', string> = {
  covered: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
  partial: 'bg-amber-500/10 text-amber-700 dark:text-amber-400',
  conflict: 'bg-rose-500/10 text-rose-700 dark:text-rose-400',
  not_found: 'bg-slate-500/10 text-slate-700 dark:text-slate-300',
  unresolved: 'bg-zinc-500/10 text-zinc-600 dark:text-zinc-400',
  missing: 'bg-violet-500/10 text-violet-700 dark:text-violet-400',
}
type Filter = 'all' | 'issues' | FunctionStatus | 'missing'
const errorText = (cause: unknown) => cause instanceof Error ? cause.message : 'Не удалось выполнить действие'
const dateTime = (value: string) => new Date(value).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
const active = (run?: AnalysisRun) => run?.status === 'queued' || run?.status === 'running'

interface Props { document: DocumentDetail; edition: DocumentEdition | undefined; isAdmin: boolean }

/** Automatic check of the selected instruction version against the department regulation in Nova. */
export function InstructionAnalysis({ document, edition, isAdmin }: Props) {
  const [nova, setNova] = useState<NovaStatus>()
  const [runs, setRuns] = useState<AnalysisRun[]>()
  const [capabilities, setCapabilities] = useState<string[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [run, setRun] = useState<AnalysisRun>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    void Promise.all([getNovaStatus(controller.signal), getAnalysisRuns(document.id, controller.signal)]).then(([status, list]) => {
      if (controller.signal.aborted) return
      setNova(status); setRuns(list.items); setCapabilities(list.capabilities); setError('')
      setSelectedId(previous => previous && list.items.some(item => item.id === previous) ? previous
        : (list.items.find(item => item.edition_id === edition?.id) || list.items[0])?.id || '')
    }).catch(cause => { if (!controller.signal.aborted) setError(errorText(cause)) })
    return () => controller.abort()
  }, [document.id, edition?.id, reload])

  // Full run (with the report) for the selected history entry; poll while active.
  useEffect(() => {
    if (!selectedId) { setRun(undefined); return }
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = () => void getAnalysisRun(selectedId, controller.signal).then(next => {
      if (controller.signal.aborted) return
      setRun(next)
      setRuns(previous => previous?.map(item => item.id === next.id ? { ...item, status: next.status, stage: next.stage, progress: next.progress, counts: next.result?.counts ?? item.counts } : item))
      if (active(next)) timer = setTimeout(load, 2000)
      else if (next.status === 'completed' && active(run)) toast.success('Анализ завершён')
    }).catch(cause => { if (!controller.signal.aborted) setError(errorText(cause)) })
    load()
    return () => { controller.abort(); if (timer) clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `run` is only read to announce the transition
  }, [selectedId])

  const mayRun = isAdmin || capabilities.includes('comparisons.manage')
  const ready = edition?.verification_status === 'verified' && edition.is_current
  const blocker = !edition ? 'Добавьте версию инструкции.'
    : !ready ? edition.verification_status === 'verified' ? 'Выбранная версия сейчас не действует. Анализ выполняется по действующей подтверждённой версии.' : 'Сначала подтвердите актуальность версии во вкладке «Версии»: дату и период действия.'
      : !mayRun ? 'Запуск анализа доступен сотрудникам с правом «Анализ инструкций».' : ''

  async function start() {
    if (!edition || busy) return
    setBusy(true)
    try {
      const created = await startAnalysis(document.id, edition.id)
      setRuns(previous => [created, ...(previous || []).filter(item => item.id !== created.id)])
      setSelectedId(created.id); setRun(created)
    } catch (cause) { toast.error(errorText(cause)) } finally { setBusy(false) }
  }
  async function cancel() {
    if (!run) return
    try { setRun(await cancelAnalysis(run.id)); setReload(value => value + 1) } catch (cause) { toast.error(errorText(cause)) }
  }

  if (error && !runs) return <section className="rounded-xl border bg-card p-6"><p role="alert" className="text-sm">{error}</p><Button variant="outline" size="sm" className="mt-3" onClick={() => setReload(value => value + 1)}>Повторить</Button></section>
  if (!runs || !nova) return <p role="status" className="flex items-center gap-2 py-10 text-sm text-muted-foreground"><Spinner className="size-4" />Загружаем анализ…</p>

  const latestForEdition = runs.find(item => item.edition_id === edition?.id)
  return <section aria-label="Автоматический анализ" className="space-y-5">
    <div className="rounded-xl border bg-card p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h3 className="flex items-center gap-2 text-base font-semibold"><Sparkles className="size-4 text-primary" />Сверка с Положением о департаменте</h3>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">Каждая функция инструкции ищется в базе положений Nova, затем модель банка сопоставляет её с найденными пунктами. Отдельно ищутся функции положения, не отражённые в инструкции. Цитаты проверяются по тексту пунктов.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {run?.status === 'completed' && run.result && <Button variant="outline" onClick={() => downloadRun(run, document.title)}><Download className="size-4" />Скачать сверку</Button>}
          {active(run) ? <Button variant="outline" onClick={() => void cancel()} disabled={!mayRun}><X className="size-4" />Остановить</Button>
            : <Button onClick={() => void start()} disabled={busy || !nova.configured || !!blocker}>{busy ? <Loader2 className="size-4 animate-spin" /> : latestForEdition ? <RotateCcw className="size-4" /> : <Play className="size-4" />}{latestForEdition ? 'Проанализировать заново' : 'Проанализировать'}</Button>}
        </div>
      </div>
      {!nova.configured && <div className="mt-4 flex gap-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 text-sm leading-6"><ShieldAlert className="mt-0.5 size-4 shrink-0 text-amber-600" /><div><p className="font-medium">Nova ещё не подключена</p><p className="text-muted-foreground">Нужны сетевой доступ сервера к Nova RAG Gateway (служба ИБ) и API-ключ с базой положений (ДИИ, ai@sqb.uz). После этого администратор указывает адрес, ключ и базу знаний в настройках сервера.</p></div></div>}
      {nova.configured && blocker && <p className="mt-4 rounded-lg bg-muted/40 px-4 py-3 text-sm leading-6 text-muted-foreground">{blocker}</p>}
      {runs.length > 1 && <label className="mt-4 flex items-center gap-3 text-xs text-muted-foreground">История запусков
        <select className="h-8 rounded-md border bg-background px-2 text-xs text-foreground" value={selectedId} onChange={event => setSelectedId(event.target.value)}>
          {runs.map(item => <option key={item.id} value={item.id}>{dateTime(item.created_at)} · версия {item.edition_number} · {runLabel(item)}</option>)}
        </select></label>}
    </div>
    {run && active(run) && <Progress run={run} />}
    {run?.status === 'failed' && <div role="alert" className="flex gap-3 rounded-xl border border-rose-500/30 bg-rose-500/5 p-5 text-sm leading-6"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-rose-600" /><div><p className="font-medium">Анализ не завершён</p><p className="text-muted-foreground">{run.error_message}</p>{run.error_code === 'not_prepared' && <PrepareText fileId={run.file_id} />}</div></div>}
    {run?.status === 'cancelled' && <p className="rounded-xl border bg-muted/30 p-5 text-sm text-muted-foreground">Анализ остановлен {run.finished_at ? dateTime(run.finished_at) : ''}.</p>}
    {run?.status === 'completed' && run.result && <Report key={run.id} run={run} result={run.result} canReview={mayRun} isAdmin={isAdmin} departmentId={document.department_id || ''} otherEdition={run.edition_id !== edition?.id} onBound={() => setReload(value => value + 1)} />}
    {!runs.length && nova.configured && !blocker && <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">Анализ этой инструкции ещё не запускался.</p>}
  </section>
}

function runLabel(run: AnalysisRun) {
  if (run.status === 'completed' && run.counts) {
    const issues = run.counts.partial + run.counts.conflict + run.counts.not_found + run.counts.missing
    return issues ? plural(issues, 'расхождение', 'расхождения', 'расхождений') : 'без расхождений'
  }
  return { queued: 'в очереди', running: 'выполняется', completed: 'завершён', failed: 'ошибка', cancelled: 'остановлен' }[run.status]
}

/** Подготовка текста файла для анализа: запуск и ожидание результата. */
function PrepareText({ fileId }: { fileId: string }) {
  const [state, setState] = useState<DocumentPreparationState>()
  const [error, setError] = useState('')
  const [started, setStarted] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = () => void getFilePreparation(fileId, controller.signal).then(next => {
      if (controller.signal.aborted) return
      setState(next); setError('')
      if (next.job?.status === 'queued' || next.job?.status === 'running') timer = setTimeout(load, 3000)
    }).catch(cause => { if (!controller.signal.aborted) setError(errorText(cause)) })
    load()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [fileId, started])
  async function start() {
    setError('')
    try { setState(await startFilePreparation(fileId)); setStarted(value => !value) } catch (cause) { setError(errorText(cause)) }
  }
  const job = state?.job?.status
  const ready = state?.source?.status === 'ready' || state?.source?.status === 'partial'
  return <div className="mt-3 space-y-2">
    {ready ? <p className="flex items-center gap-2 text-emerald-600"><CheckCircle2 className="size-4" />Текст подготовлен. Нажмите «Проанализировать заново».</p>
      : job === 'queued' || job === 'running' ? <p className="flex items-center gap-2 text-muted-foreground"><Spinner className="size-4" />Готовим текст инструкции…</p>
      : <>{state?.source?.status === 'needs_ocr' && <p className="text-muted-foreground">В файле нет текстового слоя (скан). Загрузите версию в DOCX или PDF с текстом.</p>}
        {job === 'failed' && state?.job?.message && <p className="text-muted-foreground">Подготовка не удалась: {state.job.message}</p>}
        <Button size="sm" variant="outline" disabled={!state} onClick={() => void start()}><Play className="size-3.5" />Подготовить текст</Button></>}
    {error && <p className="text-rose-600">{error}</p>}
  </div>
}

function Progress({ run }: { run: AnalysisRun }) {
  const current = run.stages.findIndex(stage => stage.id === run.stage)
  return <ol aria-label="Ход анализа" className="space-y-3 rounded-xl border bg-card p-6">
    {run.status === 'queued' && <li className="text-sm text-muted-foreground">Анализ в очереди. Запуски выполняются по одному: у ключа Nova ограничено число одновременных запросов.</li>}
    {run.stages.map((stage, index) => {
      const progress = run.progress[stage.id]
      const done = run.status === 'running' && (index < current || (index === current && progress && progress.total > 0 && progress.done >= progress.total && stage.id !== 'summary'))
      const running = run.status === 'running' && index === current && !done
      return <li key={stage.id} className="flex items-center gap-3 text-sm">
        {done ? <CheckCircle2 className="size-4 text-emerald-600" /> : running ? <Loader2 className="size-4 animate-spin text-primary" /> : <CircleDashed className="size-4 text-muted-foreground" />}
        <span className={running ? 'font-medium' : done ? '' : 'text-muted-foreground'}>{stage.label}</span>
        {progress && progress.total > 0 && (running || done) && <span className="text-xs tabular-nums text-muted-foreground">{progress.done} из {progress.total}</span>}
        {running && progress && progress.total > 0 && <span className="ml-auto h-1.5 w-40 overflow-hidden rounded-full bg-muted"><span className="block h-full bg-primary transition-all" style={{ width: `${Math.round(100 * progress.done / progress.total)}%` }} /></span>}
      </li>
    })}
  </ol>
}

interface ReportProps { run: AnalysisRun; result: AnalysisResult; canReview: boolean; isAdmin: boolean; departmentId: string; otherEdition: boolean; onBound: () => void }

function Report({ run, result, canReview, isAdmin, departmentId, otherEdition, onBound }: ReportProps) {
  const [filter, setFilter] = useState<Filter>('issues')
  const [reviews, setReviews] = useState<Record<string, AnalysisReview>>(run.reviews || {})
  const counts = result.counts
  const issues = counts.partial + counts.conflict + counts.not_found + counts.unresolved
  const functions = useMemo(() => result.functions.filter(item => filter === 'all' || (filter === 'issues' ? item.status !== 'covered' : item.status === filter)), [result.functions, filter])
  const showMissing = filter === 'all' || filter === 'issues' || filter === 'missing'
  const chips: Array<[Filter, string, number]> = [
    ['issues', 'Расхождения', issues + counts.missing], ['all', 'Все функции', result.functions.length],
    ['covered', functionStatusLabels.covered, counts.covered], ['partial', functionStatusLabels.partial, counts.partial],
    ['conflict', functionStatusLabels.conflict, counts.conflict], ['not_found', functionStatusLabels.not_found, counts.not_found],
    ['missing', 'Нет в инструкции', counts.missing], ['unresolved', functionStatusLabels.unresolved, counts.unresolved],
  ]
  const onReviewed = useCallback((review: AnalysisReview) => setReviews(previous => ({ ...previous, [review.finding_id]: review })), [])
  const reviewed = Object.keys(reviews).length
  const findingsTotal = result.functions.filter(item => item.status !== 'covered').length + result.missing.length
  return <div className="space-y-5">
    <article className="rounded-xl border bg-card p-6">
      <p className="text-xs text-muted-foreground">Версия {run.edition_number} · {run.finished_at && dateTime(run.finished_at)} · база Nova «{run.collection}» · методика {result.method_version}</p>
      {otherEdition && <p className="mt-3 rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">Этот анализ выполнен для другой версии инструкции.</p>}
      <dl className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-5">
        {([['covered', counts.covered], ['partial', counts.partial], ['conflict', counts.conflict], ['not_found', counts.not_found], ['missing', counts.missing]] as Array<[FunctionStatus | 'missing', number]>).map(([key, value]) =>
          <div key={key} className={`rounded-lg px-4 py-3 ${statusTone[key]}`}><dt className="text-xs">{key === 'missing' ? 'Нет в инструкции' : functionStatusLabels[key]}</dt><dd className="mt-1 text-2xl font-semibold tabular-nums">{value}</dd></div>)}
      </dl>
      <p className="mt-5 whitespace-pre-wrap text-sm leading-7">{result.summary.text}</p>
      {(result.summary.key_points.length > 0 || result.summary.recommendations.length > 0) && <div className="mt-4 grid gap-5 md:grid-cols-2">
        {result.summary.key_points.length > 0 && <div><h4 className="text-xs font-medium text-muted-foreground">Главное</h4><ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-6">{result.summary.key_points.map(point => <li key={point}>{point}</li>)}</ul></div>}
        {result.summary.recommendations.length > 0 && <div><h4 className="text-xs font-medium text-muted-foreground">Что сделать</h4><ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-6">{result.summary.recommendations.map(point => <li key={point}>{point}</li>)}</ul></div>}
      </div>}
      {result.summary.generated_by === 'rules' && <p className="mt-3 text-xs text-muted-foreground">Заключение составлено по подсчёту результатов: модель не вернула проверяемый текст заключения.</p>}
      <p className="mt-5 text-xs leading-5 text-muted-foreground">Nova возвращает до пяти пунктов на запрос, поэтому сверка охватывает найденные пункты, а не всё положение целиком. Выводы модели требуют решения эксперта{findingsTotal ? ` · рассмотрено ${reviewed} из ${findingsTotal}` : ''}.</p>
      {result.warnings.length > 0 && <ul className="mt-4 space-y-2">{result.warnings.map(warning => <li key={warning} className="flex gap-2 rounded-lg bg-amber-500/10 px-3 py-2 text-xs leading-5 text-amber-900 dark:text-amber-200"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" />{warning}</li>)}</ul>}
      <Sources result={result} isAdmin={isAdmin} departmentId={departmentId} onBound={onBound} />
    </article>
    <div role="tablist" aria-label="Фильтр результатов" className="flex flex-wrap gap-2">
      {chips.filter(([key, , value]) => value > 0 || key === 'issues' || key === 'all').map(([key, label, value]) =>
        <button key={key} type="button" role="tab" aria-selected={filter === key} onClick={() => setFilter(key)} className={`rounded-full border px-3 py-1.5 text-xs transition-colors ${filter === key ? 'border-primary bg-primary text-primary-foreground' : 'hover:bg-muted/50'}`}>{label} <span className="tabular-nums opacity-70">{value}</span></button>)}
    </div>
    {functions.length === 0 && !(showMissing && result.missing.length) && <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">{filter === 'issues' ? 'Расхождений не найдено: все функции инструкции предусмотрены найденными пунктами положения.' : 'Нет функций с таким статусом.'}</p>}
    {functions.length > 0 && <ul className="space-y-3">{functions.map(item => <li key={item.id}><FunctionCard item={item} clauses={result.clauses} runId={run.id} review={reviews[item.id]} canReview={canReview && item.status !== 'covered'} onReviewed={onReviewed} /></li>)}</ul>}
    {showMissing && result.missing.length > 0 && <section aria-label="Функции положения, не отражённые в инструкции" className="space-y-3">
      <h4 className="pt-2 text-sm font-semibold">В положении есть, в инструкции нет</h4>
      <ul className="space-y-3">{result.missing.map(item => <li key={item.id}><MissingCard item={item} clause={result.clauses[item.clause_id]} runId={run.id} review={reviews[item.id]} canReview={canReview} onReviewed={onReviewed} /></li>)}</ul>
    </section>}
  </div>
}

function Sources({ result, isAdmin, departmentId, onBound }: { result: AnalysisResult; isAdmin: boolean; departmentId: string; onBound: () => void }) {
  const [busy, setBusy] = useState(false)
  const documents = result.regulation.documents
  if (!documents.length) return null
  async function bind(name: string | null) {
    setBusy(true)
    try { await bindRegulation(departmentId, name); toast.success(name ? 'Положение закреплено. Повторите анализ, чтобы учесть только его' : 'Закрепление снято'); onBound() }
    catch (cause) { toast.error(errorText(cause)) } finally { setBusy(false) }
  }
  return <details className="mt-4 rounded-lg border px-4 py-3 text-xs">
    <summary className="cursor-pointer text-muted-foreground">Источники в Nova: {plural(result.regulation.clauses_found, 'пункт', 'пункта', 'пунктов')} · {result.regulation.binding_document ? 'положение закреплено' : plural(documents.length, 'документ', 'документа', 'документов')}</summary>
    <ul className="mt-3 space-y-2">{documents.map(item => <li key={item.name} className="flex items-center justify-between gap-4"><span className="min-w-0 break-words">{item.name} <span className="text-muted-foreground">· {plural(item.clauses, 'совпадение', 'совпадения', 'совпадений')}</span></span>
      {isAdmin && !result.regulation.binding_document && item.name !== 'Документ не указан' && <Button size="sm" variant="outline" disabled={busy} onClick={() => void bind(item.name)}>Это положение департамента</Button>}</li>)}</ul>
    {isAdmin && result.regulation.binding_document && <Button size="sm" variant="ghost" className="mt-2" disabled={busy} onClick={() => void bind(null)}>Снять закрепление</Button>}
  </details>
}

function StatusBadge({ status }: { status: FunctionStatus | 'missing' }) {
  return <span className={`inline-flex shrink-0 items-center rounded-full px-2.5 py-1 text-xs font-medium ${statusTone[status]}`}>{status === 'missing' ? 'Нет в инструкции' : functionStatusLabels[status]}</span>
}

function ClauseQuote({ clause, quote, verified }: { clause?: AnalysisClause; quote?: string; verified?: boolean }) {
  if (!clause) return null
  const reference = [clause.document, clause.chapter, clause.clause].filter(Boolean).join(' · ') || 'Пункт положения'
  return <div className="rounded-lg border-l-2 border-primary/40 bg-muted/30 px-4 py-3">
    <p className="text-xs font-medium text-muted-foreground">{reference}</p>
    {quote && <blockquote className="mt-2 text-sm leading-6">«{quote}»</blockquote>}
    {quote && <p className={`mt-1 text-xs ${verified ? 'text-emerald-700 dark:text-emerald-400' : 'text-amber-700 dark:text-amber-400'}`}>{verified ? 'Цитата найдена в тексте пункта' : 'Цитата не найдена дословно — сверьте с пунктом'}</p>}
    <details className="mt-2"><summary className="cursor-pointer text-xs text-muted-foreground">Полный текст пункта</summary><p className="mt-2 whitespace-pre-wrap text-xs leading-6">{clause.text}</p></details>
  </div>
}

function FunctionCard({ item, clauses, runId, review, canReview, onReviewed }: { item: AnalysisFunction; clauses: Record<string, AnalysisClause>; runId: string; review?: AnalysisReview; canReview: boolean; onReviewed: (review: AnalysisReview) => void }) {
  const quoted = new Set(item.quotes.map(quote => quote.clause_id))
  return <article className="rounded-xl border bg-card p-5">
    <div className="flex items-start gap-3"><StatusBadge status={item.status} /><p className="min-w-0 flex-1 break-words text-sm font-medium leading-6">{item.text}</p></div>
    {item.label && <p className="mt-1 pl-1 text-xs text-muted-foreground">{item.label}</p>}
    {item.explanation && <p className="mt-3 text-sm leading-6 text-muted-foreground">{item.explanation}</p>}
    {(item.quotes.length > 0 || item.clause_ids.length > 0) && <div className="mt-3 space-y-2">
      {item.quotes.map(quote => <ClauseQuote key={quote.clause_id + quote.quote} clause={clauses[quote.clause_id]} quote={quote.quote} verified={quote.verified} />)}
      {item.clause_ids.filter(id => !quoted.has(id)).map(id => <ClauseQuote key={id} clause={clauses[id]} />)}
    </div>}
    {item.recommendation && <p className="mt-3 text-sm leading-6"><span className="font-medium">Рекомендация: </span>{item.recommendation}</p>}
    <Decision runId={runId} findingId={item.id} review={review} canReview={canReview} onReviewed={onReviewed} />
  </article>
}

function MissingCard({ item, clause, runId, review, canReview, onReviewed }: { item: AnalysisMissing; clause?: AnalysisClause; runId: string; review?: AnalysisReview; canReview: boolean; onReviewed: (review: AnalysisReview) => void }) {
  return <article className="rounded-xl border bg-card p-5">
    <div className="flex items-start gap-3"><StatusBadge status="missing" /><p className="min-w-0 flex-1 text-sm leading-6 text-muted-foreground">{item.explanation || 'Функция положения не отражена в инструкции.'}</p></div>
    <div className="mt-3"><ClauseQuote clause={clause} quote={item.quote} verified={item.quote_verified} /></div>
    {item.recommendation && <p className="mt-3 text-sm leading-6"><span className="font-medium">Рекомендация: </span>{item.recommendation}</p>}
    <Decision runId={runId} findingId={item.id} review={review} canReview={canReview} onReviewed={onReviewed} />
  </article>
}

function Decision({ runId, findingId, review, canReview, onReviewed }: { runId: string; findingId: string; review?: AnalysisReview; canReview: boolean; onReviewed: (review: AnalysisReview) => void }) {
  const [pending, setPending] = useState<ReviewDecision>()
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  if (!canReview && !review) return null
  async function save(decision: ReviewDecision) {
    if (decision !== 'confirmed' && !comment.trim()) { setPending(decision); return }
    setBusy(true)
    try { onReviewed(await reviewFinding(runId, findingId, decision, comment.trim())); setPending(undefined); setComment('') }
    catch (cause) { toast.error(errorText(cause)) } finally { setBusy(false) }
  }
  return <div className="mt-4 border-t pt-3">
    <div className="flex flex-wrap items-center gap-2">
      {review && <span className="mr-2 text-xs"><span className="font-medium">{decisionLabels[review.decision]}</span>{review.comment && <span className="text-muted-foreground"> · {review.comment}</span>}</span>}
      {canReview && <><Button size="sm" variant={review?.decision === 'confirmed' ? 'secondary' : 'outline'} disabled={busy} onClick={() => void save('confirmed')}>Подтвердить</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => setPending('rejected')}>Отклонить</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => setPending('clarify')}>Уточнить</Button></>}
    </div>
    {pending && <div className="mt-3 space-y-2"><Textarea autoFocus value={comment} maxLength={2000} onChange={event => setComment(event.target.value)} placeholder={pending === 'rejected' ? 'Почему замечание неверно' : 'Что нужно уточнить и у кого'} className="min-h-20 text-sm" />
      <div className="flex gap-2"><Button size="sm" disabled={busy || !comment.trim()} onClick={() => void save(pending)}>{pending === 'rejected' ? 'Отклонить замечание' : 'Отправить на уточнение'}</Button><Button size="sm" variant="ghost" onClick={() => { setPending(undefined); setComment('') }}>Отмена</Button></div></div>}
  </div>
}

/** The whole run as JSON: instruction functions, regulation clauses with text, quotes, verdicts and expert reviews. */
function downloadRun(run: AnalysisRun, title: string) {
  const blob = new Blob([JSON.stringify(run, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = window.document.createElement('a')
  link.href = url
  link.download = `Сверка — ${title.replace(/[\\/:*?"<>|]+/g, ' ').trim()} — ${(run.finished_at || run.created_at).slice(0, 10)}.json`
  link.click()
  URL.revokeObjectURL(url)
}
