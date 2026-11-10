import { useEffect, useState } from 'react'
import { Link, useLocation, useParams, useSearchParams } from 'react-router'
import { Archive, BarChart3, ChevronRight, FileText, Folder, Plus, RotateCcw, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { DocumentsShell } from '@/components/DocumentsShell'
import { getLibraryOverview, type DepartmentOverview } from '@/lib/analysis'
import { DocumentEditor, DocumentLifecycleDialog } from '@/components/DocumentDialogs'
import { DocumentDetailView } from '@/components/DocumentDetailView'
import { BusinessPositionLibrary, BusinessPositionView, type PositionLibraryNavigation } from '@/components/BusinessPositionLibrary'
import { getDepartments, getKnowledgeAccess, type Department } from '@/lib/knowledge'
import { getCatalogSpaces, groupDepartments, type CatalogSpace } from '@/lib/departmentGroups'
import { getDocuments, getDocumentTypes, type DocumentType, type DocumentsView, type DocumentUploadResult, type RegistryDocument } from '@/lib/documents'
import { loadComparisonReports, validateComparisonReports, type ComparisonReport } from '@/lib/document-reviews'
import { plural } from '@/lib/presentation'

const errorMessage = (cause: unknown) => cause instanceof Error ? cause.message : 'Не удалось загрузить библиотеку документов'
const date = (value: string | null) => value ? value.split('-').reverse().join('.') : '—'
function departmentName(department: Department) {
  const number = department.catalog_number
  if (!number) return department.name
  const escaped = number.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return department.name.replace(new RegExp(`^${escaped}[.\\s—–-]+`), '') || department.name
}
interface LibraryContext { departments: Department[]; types: DocumentType[]; isAdmin: boolean; spaces: CatalogSpace[] }
export default function DocumentsPage() {
  const { departmentId } = useParams()
  const { pathname } = useLocation()
  const bankWide = pathname === '/knowledge/bank'
  const [context, setContext] = useState<LibraryContext>()
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    void Promise.all([getDepartments(controller.signal), getKnowledgeAccess(controller.signal), getDocumentTypes(controller.signal), getCatalogSpaces()]).then(([departments, access, types, spaces]) => { if (!controller.signal.aborted) { setContext({ departments, isAdmin: access.is_platform_admin, types, spaces }); setError('') } }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
    return () => controller.abort()
  }, [retry])
  const department = context?.departments.find(item => item.id === departmentId)
  return <DocumentsShell departments={context?.departments} spaces={context?.spaces} departmentId={departmentId} bankWide={bankWide} busy={busy}>
    {error ? <div role="alert" className="space-y-3 rounded-lg border p-5 text-sm"><h1 className="text-xl font-semibold">Документы</h1><p>{error}</p><Button variant="outline" size="sm" onClick={() => setRetry(value => value + 1)}>Повторить</Button></div> : !context ? <div role="status" className="flex items-center gap-2 py-8 text-sm text-muted-foreground"><Spinner className="size-4" />Загружаем библиотеку…</div> : departmentId && !department ? <div className="max-w-xl py-10"><h1 className="text-2xl font-semibold">Департамент недоступен</h1><p className="mt-3 text-sm leading-6 text-muted-foreground">Проверьте назначение доступа у ответственного за Process Hub.</p><Button asChild variant="outline" className="mt-5"><Link to="/knowledge">К департаментам</Link></Button></div> : department || bankWide ? <DocumentLibrary key={departmentId || 'bank'} context={context} department={department} onBusy={setBusy} /> : <DepartmentsIndex context={context} />}
  </DocumentsShell>
}
type OverviewFilter = 'all' | 'awaiting' | 'pending' | 'empty'
const overviewFilters: Record<OverviewFilter, { label: string; test: (row?: DepartmentOverview) => boolean }> = {
  all: { label: 'Все департаменты', test: () => true },
  awaiting: { label: 'Ждут подтверждения версий', test: row => !!row?.awaiting },
  pending: { label: 'Есть нерассмотренные расхождения', test: row => !!row?.pending },
  empty: { label: 'Инструкции не загружены', test: row => !row?.instructions },
}
/** Library start: where work waits, rather than a second copy of the department menu. */
function DepartmentsIndex({ context }: { context: LibraryContext }) {
  const [overview, setOverview] = useState<Record<string, DepartmentOverview>>()
  const [filter, setFilter] = useState<OverviewFilter>('all')
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    void getLibraryOverview(controller.signal).then(rows => { if (!controller.signal.aborted) setOverview(Object.fromEntries(rows.map(row => [row.department_id, row]))) })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
    return () => controller.abort()
  }, [])
  const rows = context.departments.filter(item => overviewFilters[filter].test(overview?.[item.id]))
  const groups = groupDepartments(rows, context.spaces)
  const sections = groupDepartments(context.departments, context.spaces).length > 1
  const total = (key: keyof Omit<DepartmentOverview, 'department_id'>) => Object.values(overview || {}).reduce((sum, row) => sum + row[key], 0)
  return <>
    <header className="border-b pb-6"><p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Библиотека документов</p><h1 className="mt-3 text-3xl font-semibold tracking-tight">Сводка по департаментам</h1><p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">Где инструкции ждут подтверждения версий, анализа или решения по расхождениям. Департамент открывается из списка слева или из таблицы.</p></header>
    {context.departments.length === 0 ? <div className="py-20 text-center"><Folder className="mx-auto size-9 text-blue-950 dark:text-white" /><h2 className="mt-4 text-base font-medium">{context.isAdmin ? 'Папки департаментов ещё не перенесены' : 'Доступ к документам ещё не назначен'}</h2><p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted-foreground">{context.isAdmin ? 'Папки библиотеки берутся из существующего каталога Hub.' : 'Обратитесь к ответственному за Process Hub для назначения доступа.'}</p></div> : <>
      {overview && <dl className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-4">{([['instructions', 'Инструкций'], ['confirmed', 'Действующих подтверждённых'], ['awaiting', 'Ждут подтверждения'], ['pending', 'Нерассмотренных расхождений']] as const).map(([key, label]) => <div key={key} className="rounded-xl border bg-card px-4 py-3"><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1 text-2xl font-semibold tabular-nums">{total(key)}</dd></div>)}</dl>}
      <div className="mt-6 flex flex-wrap gap-2" role="tablist" aria-label="Фильтр департаментов">{Object.entries(overviewFilters).map(([key, entry]) => <button key={key} type="button" role="tab" aria-selected={filter === key} onClick={() => setFilter(key as OverviewFilter)} className={`rounded-full border px-3 py-1.5 text-xs transition-colors ${filter === key ? 'border-primary bg-primary text-primary-foreground' : 'hover:bg-muted/50'}`}>{entry.label}</button>)}</div>
      {error && <p role="alert" className="mt-4 text-sm text-muted-foreground">Сводка недоступна: {error}</p>}
      <div className="mt-4 overflow-x-auto rounded-xl border bg-card"><table className="w-full min-w-[640px] text-sm">
        <thead className="border-b bg-muted/30 text-xs text-muted-foreground"><tr><th className="px-5 py-3 text-left font-medium">Департамент</th><th className="w-28 px-3 py-3 text-right font-medium">Инструкций</th><th className="w-32 px-3 py-3 text-right font-medium">Подтверждено</th><th className="w-32 px-3 py-3 text-right font-medium">Проанализировано</th><th className="w-32 px-5 py-3 text-right font-medium" title="Расхождения последнего анализа без решения эксперта">К решению</th></tr></thead>
        {groups.map(group => <tbody key={group.id} className="divide-y border-b last:border-b-0">{sections && <tr className="bg-muted/20"><th colSpan={5} scope="rowgroup" className="px-5 py-2 text-left text-xs font-medium uppercase tracking-wider text-muted-foreground">{group.title} <span className="tabular-nums">· {group.departments.length}</span></th></tr>}{group.departments.map(item => { const row = overview?.[item.id]; return <tr key={item.id} className="hover:bg-muted/30">
          <td className="px-5 py-3"><Link to={`/knowledge/departments/${item.id}`} className="flex items-start gap-2 outline-none hover:underline focus-visible:underline">{item.catalog_number && <span className="w-6 shrink-0 text-xs tabular-nums text-muted-foreground">{item.catalog_number}</span>}<span className="break-words">{departmentName(item)}</span></Link></td>
          <td className="px-3 py-3 text-right tabular-nums">{row ? row.instructions || <span className="text-muted-foreground">—</span> : '…'}</td>
          <td className="px-3 py-3 text-right tabular-nums">{row?.instructions ? <>{row.confirmed}{row.awaiting > 0 && <span className="ml-2 rounded bg-amber-500/10 px-1.5 py-0.5 text-xs text-amber-800 dark:text-amber-300" title="Версии ждут подтверждения">+{row.awaiting}</span>}</> : <span className="text-muted-foreground">—</span>}</td>
          <td className="px-3 py-3 text-right tabular-nums">{row?.instructions ? row.analyzed : <span className="text-muted-foreground">—</span>}</td>
          <td className="px-5 py-3 text-right tabular-nums">{row?.pending ? <span className="rounded bg-amber-500/10 px-2 py-0.5 text-amber-800 dark:text-amber-300">{row.pending}</span> : <span className="text-muted-foreground">—</span>}</td>
        </tr> })}</tbody>)}</table>
        {!rows.length && <p className="py-10 text-center text-sm text-muted-foreground">Нет департаментов для этого фильтра.</p>}
      </div>
    </>}
  </>
}
function DocumentLibrary({ context, department, onBusy }: { context: LibraryContext; department?: Department; onBusy: (busy: boolean) => void }) {
  const [params, setParams] = useSearchParams()
  const [type, setType] = useState(department || params.get('position') ? 'job_instruction' : 'all')
  const [positionNavigation, setPositionNavigation] = useState<PositionLibraryNavigation>({ historical: false })
  const [viewsByType, setViewsByType] = useState<Record<string, DocumentsView>>({})
  const requestedView = params.get('view')
  const normalizedView = requestedView && ['current', 'unverified', 'historical'].includes(requestedView) ? 'analytics' : requestedView
  const chosenView: DocumentsView = type === 'all' ? 'all' : normalizedView && ['all', 'analytics', 'trash'].includes(normalizedView) ? normalizedView as DocumentsView : viewsByType[type] || 'all'
  // Instruction analysis lives inside each position; old analysis links open the positions.
  const view: DocumentsView = chosenView === 'analytics' && !!department && type === 'job_instruction' ? 'all' : chosenView
  useEffect(() => {
    if (requestedView !== normalizedView) setParams(previous => {
      const next = new URLSearchParams(previous)
      next.set('view', normalizedView || 'all')
      return next
    }, { replace: true })
  }, [requestedView, normalizedView, setParams])
  const [query, setQuery] = useState('')
  const [revision, setRevision] = useState(0)
  const [detailRevision, setDetailRevision] = useState(0)
  const [editor, setEditor] = useState(false)
  const [editorRole, setEditorRole] = useState<string>()
  const [busy, setBusy] = useState(false)
  useEffect(() => () => onBusy(false), [onBusy])
  const selectedId = params.get('document') || params.get('instruction')
  const positionId = params.get('position')
  const byPositions = !!department && type === 'job_instruction'
  const scope = department ? 'department' as const : 'bank' as const
  const allowed = (capability: string) => context.isAdmin || !!department?.capabilities.includes(capability as Department['capabilities'][number])
  const setSaving = (value: boolean) => { setBusy(value); onBusy(value) }
  const selected = (id?: string, editionId?: string, fileId?: string, section?: 'history' | 'review') => setParams(previous => {
    const next = new URLSearchParams(previous)
    for (const key of ['document', 'instruction', 'edition', 'version', 'fragment', 'file', 'analysis', 'analysisEdition', 'section']) next.delete(key)
    if (id) next.set('document', id)
    if (editionId) next.set('edition', editionId)
    if (fileId) next.set('file', fileId)
    if (section) next.set('section', section)
    return next
  })
  const views: { id: DocumentsView; label: string }[] = [{ id: 'all', label: 'Все' }, ...(byPositions ? [] : [{ id: 'analytics' as const, label: 'Анализ и расхождения' }]), ...(allowed('instructions.delete') || allowed('instructions.restore') ? [{ id: 'trash' as const, label: 'Архив' }] : [])]
  const categories = [{ id: 'job_instruction', label: 'Инструкции' }, { id: 'department_regulation', label: 'Положения' }, { id: 'procedure', label: 'Порядки и регламенты' }, { id: 'order', label: 'Распорядительные' }]
  const otherTypes = context.types.filter(item => !categories.some(category => category.id === item.id))
  const typeLabel = categories.find(item => item.id === type)?.label || context.types.find(item => item.id === type)?.label || 'Все документы'
  const selectType = (id: string) => { setType(id); setParams(previous => { const next = new URLSearchParams(previous); next.delete('view'); return next }) }
  const selectView = (id: DocumentsView) => { setViewsByType(previous => ({ ...previous, [type]: id })); setParams(previous => { const next = new URLSearchParams(previous); next.set('view', id); return next }) }
  function changed(id?: string, editionId?: string) { setSaving(false); selected(id, editionId); setRevision(value => value + 1); setDetailRevision(value => value + 1) }
  function saved(result: DocumentUploadResult | RegistryDocument) {
    setEditor(false); setEditorRole(undefined); setSaving(false)
    if ('document' in result) selected(result.document.id, result.edition.id); else selected(result.id)
    setRevision(value => value + 1); setDetailRevision(value => value + 1)
  }
  const openReport = (id: string, editionId: string) => setParams(previous => {
    const next = new URLSearchParams(previous)
    next.set('analysis', id); next.set('analysisEdition', editionId); next.set('section', 'review')
    return next
  })
  const closeReport = () => setParams(previous => {
    const next = new URLSearchParams(previous)
    next.delete('analysis'); next.delete('analysisEdition')
    return next
  })
  const reportId = params.get('analysis')
  const newDocumentEditor = editor && <DocumentEditor mode="new" departments={context.departments} types={context.types} departmentId={department?.id} documentType={editorRole ? 'job_instruction' : type === 'all' ? undefined : type} roleId={editorRole} scope={scope} onClose={() => { setEditor(false); setEditorRole(undefined) }} onSaved={saved} onBusy={setSaving} onConflict={() => setRevision(value => value + 1)} />
  const renderDocument = (id: string, embedded = false, defaultEditionId = '') => <DocumentDetailView key={`${id}:${detailRevision}`} id={id} editionId={params.get('edition') || params.get('version') || defaultEditionId} fileId={params.get('file') || params.get('source') || undefined} embedded={embedded} initialSection={params.get('section') === 'review' ? 'review' : params.get('section') === 'history' ? 'history' : undefined} onFile={file => selected(id, params.get('edition') || params.get('version') || defaultEditionId, file)} onEdition={edition => selected(id, edition)} onBack={() => selected()} onChanged={changed} onBusy={setSaving} departments={context.departments} types={context.types} isAdmin={context.isAdmin} />
  return reportId ? <DocumentDetailView key={`${reportId}:${detailRevision}`} id={reportId} editionId={params.get('analysisEdition') || ''} onEdition={edition => openReport(reportId, edition)} onBack={closeReport} backLabel={selectedId || positionId ? 'Назад к инструкции' : 'Назад к проверкам'} onChanged={(id, edition) => { setSaving(false); if (id) openReport(id, edition || ''); else closeReport(); setDetailRevision(value => value + 1); setRevision(value => value + 1) }} onBusy={setSaving} departments={context.departments} types={context.types} isAdmin={context.isAdmin} /> : positionId && department ? <><BusinessPositionView key={`${positionId}:${detailRevision}`} departmentId={department.id} positionId={positionId} sourceId={params.get('source') || ''} documentId={selectedId || undefined} canEdit={allowed('instructions.create')} canDelete={allowed('instructions.delete')} onUpload={() => { setEditorRole(positionId); setEditor(true) }} onDeleted={() => { setParams(previous => { const next = new URLSearchParams(previous); next.delete('position'); next.delete('document'); return next }); setRevision(value => value + 1) }} busy={busy} view={view} onBack={() => setParams(previous => { const next = new URLSearchParams(previous); for (const key of ['position', 'document', 'instruction', 'source', 'edition', 'version', 'file', 'section', 'analysis', 'analysisEdition']) next.delete(key); return next })} onDocument={selected} renderDocument={(document, edition) => renderDocument(document.id, true, edition)} />{newDocumentEditor}</> : selectedId ? renderDocument(selectedId) : <>
    <header className="border-b pb-6"><p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Документы · {department ? 'департамент' : 'общая область'}</p><div className="mt-3 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between sm:gap-6"><div className="min-w-0"><h1 className="break-words text-3xl font-semibold leading-10 tracking-tight">{department ? departmentName(department) : 'Общебанковские документы'}</h1><p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">{department ? 'Документы департамента и применимые общебанковские материалы.' : 'Единые документы банка. Применимость к подразделениям подтверждается по источникам.'}</p></div>{allowed('instructions.create') && <Button className="self-start sm:mt-1 sm:shrink-0" disabled={busy} onClick={() => setEditor(true)}><Plus className="size-4" />Добавить документ</Button>}</div></header>
    <nav aria-label="Виды документов" className="mt-6 flex items-start justify-between gap-5 border-b">
      <div className="flex min-w-0 flex-wrap gap-x-5 gap-y-2">{categories.map(item => <button key={item.id} type="button" disabled={busy} aria-pressed={type === item.id} onClick={() => selectType(item.id)} className={`-mb-px border-b-2 px-1 pb-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring ${type === item.id ? 'border-foreground font-medium text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>{item.label}</button>)}</div>
      <button type="button" disabled={busy} aria-pressed={type === 'all'} onClick={() => selectType('all')} className={`-mb-px shrink-0 border-b-2 px-1 pb-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring ${type === 'all' ? 'border-foreground font-medium text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>Все документы</button>
    </nav>
    {type !== 'all' && <section className="mt-5" aria-label={`Раздел: ${typeLabel}`}>
      <h2 className={categories.some(item => item.id === type) ? 'sr-only' : 'mb-3 text-base font-semibold'}>{typeLabel}</h2>
      <nav aria-label={`Разделы документов: ${typeLabel}`} className="flex flex-wrap gap-1 rounded-lg bg-muted/40 p-1">{views.map(item => <Button type="button" key={item.id} size="sm" variant="ghost" className={view === item.id ? 'bg-background font-medium shadow-xs hover:bg-background' : 'text-muted-foreground'} disabled={busy} aria-pressed={view === item.id} onClick={() => selectView(item.id)}>
        {item.id === 'trash' && <Archive className="size-3.5" />}{item.id === 'analytics' && <BarChart3 className="size-3.5" />}{item.label}
      </Button>)}</nav>
    </section>}
    <div className="mt-5 flex items-center gap-3">
      <div className="relative max-w-xl flex-1"><Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" /><Input value={query} disabled={busy} className="pl-9" aria-label={byPositions ? 'Поиск должностей и служб' : 'Поиск документов'} placeholder={byPositions && view !== 'analytics' && view !== 'trash' ? 'Должность, служба или название инструкции' : 'Название, номер или должность'} onChange={event => setQuery(event.target.value)} /></div>
      {!byPositions && otherTypes.length > 0 && <Select value={otherTypes.some(item => item.id === type) ? type : ''} disabled={busy} onValueChange={selectType}><SelectTrigger className="w-48 min-w-0 shrink-0 text-xs [&_[data-slot=select-value]]:min-w-0 [&_[data-slot=select-value]]:truncate" aria-label="Другие виды документов"><SelectValue placeholder="Другие виды" /></SelectTrigger><SelectContent>{otherTypes.map(item => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}</SelectContent></Select>}
    </div>
    {byPositions && view !== 'analytics' && view !== 'trash' ? <BusinessPositionLibrary key={revision} departmentId={department.id} query={query} view={view} navigation={positionNavigation} canCreate={allowed('instructions.create')} onNavigation={setPositionNavigation} onSelect={(id, documentId) => setParams(previous => { const next = new URLSearchParams(previous); for (const key of ['document', 'instruction', 'source', 'edition', 'version', 'file', 'section', 'analysis', 'analysisEdition']) next.delete(key); next.set('position', id); if (documentId) next.set('document', documentId); return next })} onDocument={selected} /> : view === 'analytics' ? <DocumentAnalytics key={`${type}:${revision}`} scope={scope} departmentId={department?.id} type={type} typeLabel={typeLabel} query={query} onSelect={openReport} /> : <DocumentList key={`${view}:${revision}`} scope={scope} departmentId={department?.id} view={view} type={type} query={query} types={context.types} onSelect={selected} onChanged={() => changed()} onBusy={setSaving} busy={busy} isAdmin={context.isAdmin} canCreate={allowed('instructions.create')} />}
    {newDocumentEditor}
  </>
}
function matchingDocuments(items: RegistryDocument[], type: string, query: string) {
  const search = query.trim().toLocaleLowerCase('ru')
  return items.filter(item => (type === 'all' || item.document_type === type) && `${item.title} ${item.bank_number || ''} ${item.role_name || ''} ${item.owner_department_name || ''}`.toLocaleLowerCase('ru').includes(search))
}
function DocumentAnalytics({ scope, departmentId, type, typeLabel, query, onSelect }: { scope: 'department' | 'bank'; departmentId?: string; type: string; typeLabel: string; query: string; onSelect: (id: string, editionId: string) => void }) {
  const [loaded, setLoaded] = useState<{ key: string; reports?: ComparisonReport[]; error?: string }>()
  const [retry, setRetry] = useState(0)
  const requestKey = `${scope}:${departmentId || ''}:${retry}`
  useEffect(() => {
    const controller = new AbortController()
    void loadComparisonReports(scope, departmentId, controller.signal).then(reports => validateComparisonReports(reports, controller.signal)).then(reports => {
      if (!controller.signal.aborted) setLoaded({ key: requestKey, reports })
    }).catch(cause => { if (!controller.signal.aborted) setLoaded({ key: requestKey, error: errorMessage(cause) }) })
    return () => controller.abort()
  }, [scope, departmentId, requestKey])
  const state = loaded?.key === requestKey ? loaded : undefined
  if (state?.error) return <section aria-label="Анализ и расхождения" className="mt-6 rounded-lg border p-5"><p role="alert" className="text-sm">{state.error}</p><Button variant="outline" size="sm" className="mt-3" onClick={() => setRetry(value => value + 1)}>Повторить</Button></section>
  if (!state?.reports) return <p role="status" className="mt-6 flex items-center gap-2 py-14 text-sm text-muted-foreground"><Spinner className="size-4" />Загружаем проверки документов…</p>
  const search = query.trim().toLocaleLowerCase('ru')
  const reports = state.reports.filter(({ document, review }) => (type === review.source_type || type === 'analytics' || type === 'all') && `${document.title} ${review.source_filename} ${review.position} ${review.unit}`.toLocaleLowerCase('ru').includes(search))
  return <section aria-label="Анализ и расхождения" className="mt-6 space-y-5">
    <div><h2 className="text-lg font-semibold">Анализ и расхождения</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">{typeLabel}: результаты проверки конкретных версий документов. Внутри каждой проверки — выводы, факты, вопросы и предлагаемые действия.</p></div>
    <p className="text-xs text-muted-foreground">{plural(reports.length, 'проверка', 'проверки', 'проверок')}{search && ' · по вашему запросу'}</p>
    {reports.length ? <ul className="divide-y overflow-hidden rounded-xl border bg-card">{reports.map(({ document, edition, review }) => <li key={`${document.id}:${edition.id}`}><button type="button" className="flex w-full items-start justify-between gap-8 p-6 text-left outline-none transition-colors hover:bg-muted/20 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" onClick={() => onSelect(document.id, edition.id)}><div className="min-w-0"><p className="text-xs leading-6 text-muted-foreground">Разбор переданного ответа Nova · {date(review.checked_on)} · {plural(review.findings.length, 'тема проверки', 'темы проверки', 'тем проверки')}</p><h3 className="mt-2 break-words text-base font-semibold leading-7">{document.title}</h3><p className="mt-2 max-w-3xl whitespace-pre-wrap text-sm leading-6 text-muted-foreground">{review.summary}</p>{(review.position || review.unit) && <p className="mt-3 break-words text-xs leading-6 text-muted-foreground">{[review.position, review.unit].filter(Boolean).join(' · ')}</p>}<p className="mt-1 break-words text-xs leading-6 text-muted-foreground">{edition.label || `Версия отчёта ${edition.number}`} · {review.source_filename}</p></div><ChevronRight className="mt-2 size-4 shrink-0 text-muted-foreground" /></button></li>)}</ul> : <div className="rounded-xl border bg-muted/20 px-6 py-14 text-center"><BarChart3 aria-hidden className="mx-auto size-8 text-muted-foreground" /><h3 className="mt-4 text-base font-medium">{search ? 'Проверки не найдены' : 'Проверок пока нет'}</h3><p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-muted-foreground">{search ? 'Измените запрос, чтобы найти нужную инструкцию или должность.' : 'Здесь появятся результаты сверки с положениями банка из Nova. Каждая проверка относится к определённой версии исходного документа.'}</p></div>}
  </section>
}

function DocumentList({ scope, departmentId, view, type, query, types, onSelect, onChanged, onBusy, busy, isAdmin, canCreate }: { scope: 'department' | 'bank'; departmentId?: string; view: DocumentsView; type: string; query: string; types: DocumentType[]; onSelect: (id: string) => void; onChanged: () => void; onBusy: (busy: boolean) => void; busy: boolean; isAdmin: boolean; canCreate: boolean }) {
  const [documents, setDocuments] = useState<RegistryDocument[]>()
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [restore, setRestore] = useState<RegistryDocument>()
  useEffect(() => {
    const controller = new AbortController()
    void getDocuments(scope, departmentId, view, controller.signal).then(items => { if (!controller.signal.aborted) { setDocuments(items); setError('') } }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
    return () => controller.abort()
  }, [scope, departmentId, view, retry])
  const visible = documents && matchingDocuments(documents, type, query)
  return <section className="mt-5 min-w-0" aria-label="Список документов">
    {error ? <div role="alert" className="rounded-lg border p-5 text-sm"><p>{error}</p><Button variant="outline" size="sm" className="mt-3" onClick={() => setRetry(value => value + 1)}>Повторить</Button></div> : !documents ? <div role="status" className="flex items-center gap-2 py-14 text-sm text-muted-foreground"><Spinner className="size-4" />Загружаем документы…</div> : <><p className="mb-4 text-xs text-muted-foreground">{plural(visible?.length || 0, 'документ', 'документа', 'документов')}{view === 'historical' && ' · с подтверждёнными прежними редакциями'}</p>{!visible?.length ? <div className="py-16 text-center"><FileText className="mx-auto size-8 text-muted-foreground" /><h2 className="mt-4 text-base font-medium">{query.trim() ? 'Документы не найдены' : view === 'trash' ? 'Архив пуст' : view === 'current' ? 'Нет действующих подтверждённых документов' : view === 'unverified' ? 'Нет материалов на проверке' : view === 'historical' ? 'Прежние редакции ещё не подтверждены' : 'Документов пока нет'}</h2><p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted-foreground">{query.trim() ? 'Измените поисковый запрос.' : view === 'trash' ? 'Здесь сохраняются документы, перемещённые из активной библиотеки. Их можно восстановить при наличии права доступа.' : view === 'current' ? 'Документы появятся здесь после проверки редакций и дат действия.' : view === 'all' ? canCreate ? 'Добавьте документ с основным текстом, приложениями и сведениями об источнике.' : 'Ответственный за документы добавит материалы департамента.' : 'Это представление показывает только подходящие материалы библиотеки.'}</p></div> : <div className="overflow-hidden rounded-xl border bg-card"><div className={`grid grid-cols-[minmax(0,1fr)_120px_24px] xl:grid-cols-[minmax(0,1fr)_140px_110px_170px_24px] gap-4 border-b bg-muted/30 py-3 pl-5 text-xs font-medium text-muted-foreground ${view === 'trash' ? 'pr-40' : 'pr-5'}`}><span>Документ</span><span>Вид</span><span className="hidden xl:block">Реквизиты</span><span className="hidden xl:block">Редакция</span><span /></div><ul className="divide-y">{visible.map(item => <li key={item.id} className="flex items-center"><button type="button" className="grid min-w-0 flex-1 grid-cols-[minmax(0,1fr)_120px_24px] xl:grid-cols-[minmax(0,1fr)_140px_110px_170px_24px] items-center gap-4 px-5 py-5 text-left outline-none transition-colors hover:bg-muted/30 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" disabled={busy || view === 'trash'} aria-label={`${view === 'trash' ? 'Документ в архиве' : 'Открыть документ'} ${item.title}`} onClick={() => onSelect(item.id)}><div className="min-w-0"><p className="break-words text-sm font-medium leading-6">{item.title}</p>{item.role_name && <p className="mt-1 break-words text-xs leading-5 text-muted-foreground">{item.role_name}</p>}{item.library_scope === 'bank' && scope === 'department' && <p className="mt-1 text-xs text-muted-foreground">Общебанковский документ</p>}{item.edition_count === 0 && <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">Комплект файлов ещё не добавлен</p>}</div><p className="min-w-0 break-words text-xs leading-5 text-muted-foreground">{types.find(entry => entry.id === item.document_type)?.label || 'Документ'}</p><div className="hidden min-w-0 text-xs leading-5 xl:block">{item.bank_number ? <p className="break-words">№ {item.bank_number}</p> : <p className="text-muted-foreground">—</p>}{item.document_date && <p className="mt-1 text-muted-foreground">{date(item.document_date)}</p>}</div><div className="hidden min-w-0 text-xs leading-5 xl:block">{item.document_type === 'other' ? <p className="text-muted-foreground">На классификации</p> : ['analytics', 'sop', 'correspondence'].includes(item.document_type) ? <p className="text-muted-foreground">Рабочий материал</p> : item.current_edition ? <><p className="break-words">{item.current_edition.label || `Редакция ${item.current_edition.number}`}</p><p className="mt-1 text-muted-foreground">с {date(item.current_edition.valid_from)}</p></> : <p className="text-muted-foreground" title="Нет действующей редакции с подтверждённым периодом">Не подтверждена</p>}</div><span>{view !== 'trash' && <ChevronRight className="size-4 text-muted-foreground" />}</span></button>{view === 'trash' && (isAdmin || item.capabilities.includes('instructions.restore')) && <Button variant="ghost" size="sm" className="mr-4" disabled={busy} onClick={() => setRestore(item)}><RotateCcw className="size-4" />Восстановить</Button>}</li>)}</ul></div>}</>}
    {restore && <DocumentLifecycleDialog document={restore} action="restore" onClose={() => setRestore(undefined)} onBusy={onBusy} onSaved={() => { setRestore(undefined); onChanged() }} />}
  </section>
}
