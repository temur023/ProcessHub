import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { ArrowUpRight, ChevronRight, FileSearch, FileText, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { reviewSourceTypes, reviewStatuses, type ComparisonReview, type ReviewStatus } from '@/lib/comparison-review'
import { getDocument, type DocumentDetail } from '@/lib/documents'
import { matchesSource } from '@/lib/document-reviews'
import { plural } from '@/lib/presentation'

const date = (value: string | null | undefined) => value ? value.split('-').reverse().join('.') : 'Не подтверждена'
const statusOrder: ReviewStatus[] = ['nova_error', 'unsupported', 'needs_evidence', 'instruction_supported']
const metricLabels: Record<ReviewStatus, string> = {
  nova_error: 'Ошибки ответа Nova', unsupported: 'Необоснованные выводы',
  needs_evidence: 'Вопросы к источникам', instruction_supported: 'Факты документа',
}
interface SourceState { key: string; document?: DocumentDetail; error?: string }

export function ComparisonReviewView({ review, departmentId }: { review: ComparisonReview; departmentId: string }) {
  const [filter, setFilter] = useState<ReviewStatus | 'all'>('all')
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [source, setSource] = useState<SourceState>()
  const [retry, setRetry] = useState(0)
  const sourceKey = `${review.source_document_id}:${review.source_edition_id}:${review.source_file_id}:${review.source_hash}:${retry}`
  useEffect(() => {
    const controller = new AbortController()
    void getDocument(review.source_document_id, controller.signal).then(document => {
      if (controller.signal.aborted) return
      if (!matchesSource(review, document)) throw new Error('Оригинал этой проверки не удалось подтвердить. Связь с другой версией не подставляется.')
      setSource({ key: sourceKey, document })
    }).catch(cause => {
      if (!controller.signal.aborted) setSource({ key: sourceKey, error: cause instanceof Error ? cause.message : 'Не удалось проверить исходную версию' })
    })
    return () => controller.abort()
  }, [review, sourceKey])
  const state = source?.key === sourceKey ? source : undefined
  const edition = state?.document?.editions.find(item => item.id === review.source_edition_id)
  const sourcePath = state?.document?.library_scope === 'bank' ? '/knowledge/bank' : `/knowledge/departments/${encodeURIComponent(state?.document?.department_id || departmentId)}`
  const sourceUrl = `${sourcePath}?document=${review.source_document_id}&edition=${review.source_edition_id}&file=${review.source_file_id}`
  const search = query.trim().toLocaleLowerCase('ru')
  const visible = review.findings.filter(item => (filter === 'all' || item.status === filter) &&
    [item.id, item.title, item.nova_claim, item.instruction_locator, item.instruction_quote, item.assessment, item.next_step].join(' ').toLocaleLowerCase('ru').includes(search))
  const selected = visible.find(item => item.id === selectedId) || visible[0]
  const counts = Object.fromEntries(statusOrder.map(status => [status, review.findings.filter(item => item.status === status).length])) as Record<ReviewStatus, number>
  return <section aria-label="Панель анализа и расхождений" className="space-y-6">
    <header className="rounded-xl border bg-card p-6">
      <div className="flex items-start justify-between gap-6"><div className="min-w-0"><p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Разбор переданного ответа Nova · {date(review.checked_on)}</p><h2 className="mt-3 text-xl font-semibold leading-8">Анализ и расхождения</h2><p className="mt-2 max-w-4xl whitespace-pre-wrap text-sm leading-7">{review.summary}</p></div><FileSearch aria-hidden className="mt-1 size-6 shrink-0 text-muted-foreground" /></div>
      <div className="mt-5 border-t pt-4"><p className="text-sm font-medium">Полное положение банка не предоставлено</p><p className="mt-2 text-xs leading-6 text-muted-foreground">Эта проверка разбирает ответ Nova и исходный документ. Подтверждённые противоречия двух документов ещё не установлены.</p><details className="mt-3"><summary className="cursor-pointer text-xs font-medium text-muted-foreground">Основания и границы проверки</summary><ul className="mt-3 space-y-2 text-xs leading-6 text-muted-foreground">{review.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul>{review.employee_check && <p className="mt-3 whitespace-pre-wrap text-xs leading-6 text-muted-foreground">{review.employee_check}</p>}</details></div>
    </header>
    <div className="grid grid-cols-4 gap-3" aria-label="Сводка результатов">{statusOrder.map(status => <button key={status} type="button" aria-pressed={filter === status} onClick={() => setFilter(current => current === status ? 'all' : status)} className={`min-w-0 rounded-xl border p-4 text-left outline-none transition-colors hover:bg-muted/30 focus-visible:ring-2 focus-visible:ring-ring ${filter === status ? 'border-foreground/40 bg-muted/40' : 'bg-card'}`}><span className="block text-2xl font-semibold tabular-nums">{counts[status]}</span><span className="mt-2 block text-xs leading-5 text-muted-foreground">{metricLabels[status]}</span></button>)}</div>
    <section aria-label="Документы и версии проверки" className="rounded-xl border bg-card p-5">
      <div className="grid grid-cols-2 gap-6"><div className="min-w-0"><h3 className="text-sm font-semibold">{reviewSourceTypes[review.source_type]} из Process Hub</h3><p className="mt-2 break-words text-sm leading-6">{review.source_filename}</p>{(review.position || review.unit) && <p className="mt-1 break-words text-xs leading-6 text-muted-foreground">{[review.position, review.unit].filter(Boolean).join(' · ')}</p>}{state?.error ? <div className="mt-3"><p role="alert" className="text-xs leading-6 text-destructive">{state.error}</p><Button variant="outline" size="sm" className="mt-2" onClick={() => setRetry(value => value + 1)}>Повторить</Button></div> : !state?.document ? <p role="status" className="mt-3 flex items-center gap-2 text-xs text-muted-foreground"><Spinner className="size-3.5" />Проверяем исходную версию…</p> : <Button asChild variant="outline" size="sm" className="mt-3"><Link to={sourceUrl}><FileText className="size-4" />Открыть исходную версию<ArrowUpRight className="size-3.5" /></Link></Button>}</div><div className="min-w-0"><h3 className="text-sm font-semibold">Положение банка из Nova</h3><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6">{review.regulation_reference}</p><p className="mt-3 text-xs leading-6 text-muted-foreground">Оригинал, версия и период действия положения не подтверждены. Отсутствие пункта в поисковой выдаче не доказывает его отсутствие в положении.</p></div></div>
      {edition && state?.document && <div className="mt-5 border-t pt-4"><p className="text-xs font-medium text-muted-foreground">Версия документа {edition.number}{edition.label && ` · ${edition.label}`} · {edition.verification_status === 'verified' ? edition.is_current ? 'Действующая подтверждённая' : 'Подтверждённая' : 'На проверке'}</p><dl className="mt-3 grid grid-cols-4 gap-4 text-xs leading-6">{[
        ['Дата документа', state.document.document_date ? date(state.document.document_date) : 'Не указана'],
        ['Принята / утверждена', edition.verification_status === 'verified' ? date(edition.approved_on) : 'Не подтверждена'],
        ['Начало действия', edition.verification_status === 'verified' ? date(edition.valid_from) : 'Не подтверждено'],
        ['Окончание действия', edition.verification_status === 'verified' ? edition.valid_to ? date(edition.valid_to) : 'Не установлено' : 'Не подтверждено'],
      ].map(([label, value]) => <div key={label}><dt className="text-muted-foreground">{label}</dt><dd className="mt-1 break-words font-medium">{value}</dd></div>)}</dl><p className="mt-3 text-xs leading-5 text-muted-foreground">Даты показаны по текущему реестру этой версии. Дата проверки не определяет принятие или период действия документа.</p></div>}
    </section>
    <section aria-label="Темы проверки" className="space-y-4">
      <div className="flex items-center justify-between gap-4"><div><h3 className="text-base font-semibold">Выводы, вопросы и предлагаемые решения</h3><p className="mt-1 text-xs leading-6 text-muted-foreground">{plural(visible.length, 'тема', 'темы', 'тем')} из {review.findings.length}. Выберите тему, чтобы прочитать полный разбор.</p></div><Button variant="ghost" size="sm" aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>Все темы</Button></div>
      <div className="relative max-w-2xl"><Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" /><Input className="pl-9" aria-label="Поиск в выводах проверки" placeholder="Тема, пункт документа, факт или решение" value={query} onChange={event => setQuery(event.target.value)} /></div>
      {filter !== 'all' && <p className="text-xs text-muted-foreground">Выбрано: {reviewStatuses[filter]}</p>}
      {!selected ? <div className="rounded-xl border py-14 text-center"><p className="text-sm font-medium">Темы не найдены</p><p className="mt-2 text-xs text-muted-foreground">Измените поиск или выбранную категорию.</p><Button variant="outline" size="sm" className="mt-4" onClick={() => { setFilter('all'); setQuery('') }}>Показать все темы</Button></div> : <div className="grid grid-cols-[minmax(220px,0.8fr)_minmax(0,2fr)] items-start gap-5">
        <nav aria-label="Список тем проверки" className="max-h-[640px] overflow-y-auto rounded-xl border bg-card"><ul className="divide-y">{visible.map(item => <li key={item.id}><button type="button" aria-current={selected.id === item.id ? 'true' : undefined} onClick={() => setSelectedId(item.id)} className={`flex w-full items-start gap-3 p-4 text-left outline-none transition-colors hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${selected.id === item.id ? 'bg-muted/60' : ''}`}><div className="min-w-0 flex-1"><p className="text-xs leading-5 text-muted-foreground">{item.id} · {reviewStatuses[item.status]}</p><p className="mt-2 break-words text-sm font-medium leading-6">{item.title}</p></div><ChevronRight aria-hidden className="mt-1 size-3.5 shrink-0 text-muted-foreground" /></button></li>)}</ul></nav>
        <article key={selected.id} aria-label={`Разбор: ${selected.title}`} className="min-w-0 overflow-hidden rounded-xl border bg-card">
          <header className="border-b p-6"><p className="text-xs font-medium leading-6 text-muted-foreground">{selected.id} · {reviewStatuses[selected.status]}</p><h4 className="mt-2 break-words text-lg font-semibold leading-8">{selected.title}</h4></header>
          <div className="space-y-6 p-6 text-sm leading-7">
            <section><h5 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{selected.origin === 'nova' ? 'Что утверждает Nova' : 'Наблюдение по документу'}</h5><p className="mt-3 whitespace-pre-wrap break-words">{selected.nova_claim || 'Отдельное утверждение не приведено.'}</p></section>
            <section className="rounded-lg border bg-muted/20 p-4"><h5 className="text-xs font-semibold text-muted-foreground">Факт из документа{selected.instruction_locator && ` · ${selected.instruction_locator}`}</h5>{selected.instruction_quote ? <blockquote className="mt-3 whitespace-pre-wrap break-words border-l-2 pl-4">{selected.instruction_quote}</blockquote> : <p className="mt-3 text-sm text-muted-foreground">Цитата не приведена. Требуется проверить полный текст источника.</p>}</section>
            <section><h5 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Оценка и проблема</h5><p className="mt-3 whitespace-pre-wrap break-words">{selected.assessment}</p></section>
            <section className="border-t pt-5"><h5 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Предлагаемое решение / следующий шаг</h5><p className="mt-3 whitespace-pre-wrap break-words">{selected.next_step || 'Решение ещё не предложено. Требуется рассмотрение специалистом.'}</p><p className="mt-4 text-xs leading-6 text-muted-foreground">Предложение требует проверки ответственным специалистом. Изменения исходных документов не вносятся автоматически.</p></section>
          </div>
        </article>
      </div>}
    </section>
  </section>
}
