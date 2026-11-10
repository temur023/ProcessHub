import { useEffect, useState, type ReactNode } from 'react'
import { ArrowLeft, BriefcaseBusiness, ChevronRight, CircleAlert, CircleCheck, FileText, Pencil, Plus, Trash2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Spinner } from '@/components/ui/spinner'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ConfirmDialog } from '@/components/catalog/CatalogDialogs'
import { createPosition, deletePosition, getPositionLibrary, getPosition, getDocuments, updatePosition, type PositionCategory, type PositionLibrary, type PositionDetail, type BusinessPosition, type DocumentDetail, type DocumentsView, type RegistryDocument } from '@/lib/documents'
import { getAnalysisRegistry } from '@/lib/analysis'
import { defaultInstructionEdition } from '@/lib/position-documents'
import { branchPositions, buildPositionHierarchy, positionName as name, type PositionBranch } from '@/lib/position-hierarchy'
import { plural } from '@/lib/presentation'

const CATEGORIES: Array<{ id: PositionCategory; label: string }> = [
  { id: 'management', label: 'Руководящие должности' }, { id: 'specialists', label: 'Специалисты' },
  { id: 'support', label: 'Сопровождение' }, { id: 'other', label: 'На уточнении' },
]
const errorText = (error: unknown) => error instanceof Error ? error.message : 'Не удалось открыть библиотеку должностей'

export interface PositionLibraryNavigation { historical: boolean }
interface PositionLibraryProps {
  departmentId: string; query: string; view: DocumentsView; navigation: PositionLibraryNavigation; canCreate: boolean
  onNavigation: (navigation: PositionLibraryNavigation) => void
  onSelect: (id: string, documentId?: string) => void; onDocument: (id: string) => void
}

function PositionCard({ role, instructions, searching, caption, onSelect }: {
  role: BusinessPosition; instructions: RegistryDocument[]; searching: string; caption?: string
  onSelect: (id: string, documentId?: string) => void
}) {
  const matched = searching ? instructions.find(document => `${document.title} ${document.bank_number || ''} ${document.role_name || ''}`.toLocaleLowerCase('ru').includes(searching)) : undefined
  return <button type="button" onClick={() => onSelect(role.id, matched?.id)} className="group flex h-full min-h-20 w-full items-start gap-3 rounded-xl border bg-card p-3 text-left outline-none transition-colors hover:bg-muted/30 focus-visible:ring-2 focus-visible:ring-ring">
    <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted/70"><BriefcaseBusiness aria-hidden="true" className="size-4 text-muted-foreground" /></span>
    <span className="min-w-0 flex-1">{caption && <span className="mb-1 block text-xs text-muted-foreground">{caption}</span>}<span className="block break-words text-sm font-medium leading-5">{name(role)}</span><span className="mt-1 block text-xs text-muted-foreground">{instructions.length ? plural(instructions.length, 'инструкция', 'инструкции', 'инструкций') : 'Нет инструкции'}</span>{role.category === 'other' && <span className="mt-1 block text-xs text-amber-700 dark:text-amber-400">Название требует уточнения</span>}</span>
    <span className="flex shrink-0 flex-col items-end justify-between gap-3 self-stretch"><ChevronRight aria-hidden="true" className="mt-2 size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" /><PositionStatus instructions={instructions} /></span>
  </button>
}

/** Version confirmation and expert work at a glance, in the card's corner. */
function PositionStatus({ instructions }: { instructions: RegistryDocument[] }) {
  if (!instructions.length) return null
  const confirmed = instructions.every(document => document.current_edition)
  const pending = instructions.reduce((sum, document) => sum + (document.pending_findings || 0), 0)
  const versionLabel = confirmed ? 'Версия подтверждена и действует' : 'Версия не подтверждена или не действует'
  return <span className="flex items-center gap-1.5">
    {pending > 0 && <span title={`${plural(pending, 'расхождение ждёт', 'расхождения ждут', 'расхождений ждут')} решения эксперта`} className="rounded-full bg-amber-500/15 px-1.5 py-px text-[11px] font-semibold tabular-nums text-amber-700 dark:text-amber-400">{pending}</span>}
    <span role="img" aria-label={versionLabel} title={versionLabel}>{confirmed ? <CircleCheck aria-hidden="true" className="size-4 text-emerald-600 dark:text-emerald-500" /> : <CircleAlert aria-hidden="true" className="size-4 text-amber-500" />}</span>
  </span>
}

function PositionNodes({ branches, searching, documentsFor, onSelect }: {
  branches: PositionBranch[]; searching: string
  documentsFor: (role: BusinessPosition) => RegistryDocument[]
  onSelect: (id: string, documentId?: string) => void
}) {
  const gridColumns = branches.length === 1 ? 'grid-cols-1' : branches.length === 2 ? 'grid-cols-1 sm:grid-cols-2' : 'grid-cols-1 sm:grid-cols-2 xl:grid-cols-3'
  const positionRows = Math.max(1, ...branches.filter(branch => branch.children.length === 0).map(branch => branch.positions.length))
  const branchRows = { gridRow: `span ${positionRows + 1}` }
  return <ul className={`grid gap-x-6 gap-y-2 ${gridColumns}`}>{branches.map(branch => branch.children.length > 0 ? <li key={branch.key} className="col-span-full min-w-0 py-3">
    <section aria-label={branch.path.join(' / ')}>
      <h3 className="break-words text-sm font-semibold leading-6">{branch.title}</h3>
      {branch.positions.length > 0 && <ul className="mt-3 grid max-w-sm gap-2">{branch.positions.map(role => <li key={role.id}><PositionCard role={role} instructions={documentsFor(role)} searching={searching} onSelect={onSelect} /></li>)}</ul>}
      <div className="mt-5"><PositionNodes branches={branch.children} searching={searching} documentsFor={documentsFor} onSelect={onSelect} /></div>
    </section>
  </li> : <li key={branch.key} style={branchRows} className="grid min-w-0 grid-rows-subgrid pb-5">
    <section aria-label={branch.path.join(' / ')} style={branchRows} className="grid grid-rows-subgrid">
      <h3 className="break-words text-xs font-semibold leading-5 text-muted-foreground">{branch.title}</h3>
      <ul style={{ gridRow: `span ${positionRows}` }} className={`grid grid-rows-subgrid ${branches.length === 1 ? 'max-w-sm' : ''}`}>{branch.positions.map(role => <li key={role.id}><PositionCard role={role} instructions={documentsFor(role)} searching={searching} onSelect={onSelect} /></li>)}</ul>
    </section>
  </li>)}</ul>
}

export function BusinessPositionLibrary({ departmentId, query, view, navigation, canCreate, onNavigation, onSelect, onDocument }: PositionLibraryProps) {
  const [loaded, setLoaded] = useState<{ key: string; library: PositionLibrary; documents: RegistryDocument[] }>()
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const { historical } = navigation
  const requestKey = `${departmentId}:${view}:${retry}`
  useEffect(() => {
    const controller = new AbortController()
    // The analysis registry only adds status marks; the library works without it.
    void Promise.all([getPositionLibrary(departmentId, controller.signal), getDocuments('department', departmentId, view, controller.signal), getAnalysisRegistry(departmentId, controller.signal).catch(() => ({ items: [] }))]).then(([library, documents, registry]) => {
      const pending = new Map(registry.items.map(item => [item.document_id, item.analysis?.status === 'completed' ? Math.max(0, (item.analysis.findings || 0) - item.analysis.reviewed) : 0]))
      if (!controller.signal.aborted) { setLoaded({ key: requestKey, library, documents: documents.filter(item => item.document_type === 'job_instruction').map(item => ({ ...item, pending_findings: pending.get(item.id) || 0 })) }); setError('') }
    }).catch(cause => { if (!controller.signal.aborted) setError(errorText(cause)) })
    return () => controller.abort()
  }, [departmentId, view, requestKey])
  if (error) return <div role="alert" className="mt-6 rounded-xl border p-5"><p className="text-sm">{error}</p><Button variant="outline" size="sm" className="mt-3" onClick={() => { setError(''); setRetry(value => value + 1) }}>Повторить</Button></div>
  if (!loaded || loaded.key !== requestKey) return <div role="status" className="mt-6 flex items-center gap-2 py-12 text-sm text-muted-foreground"><Spinner className="size-4" />Загружаем должности…</div>
  const { library, documents } = loaded
  const historicalCount = library.roles.filter(role => role.historical_candidate).length
  const search = query.trim().toLocaleLowerCase('ru')
  const matches = (document: RegistryDocument) => `${document.title} ${document.bank_number || ''} ${document.role_name || ''}`.toLocaleLowerCase('ru').includes(search)
  const documentsFor = (role: BusinessPosition) => documents.filter(document => document.department_id === departmentId && document.role_id === role.id)
  // A staffed position without any instruction stays visible in «Все»: the gap is the information.
  const base = library.roles.filter(role => role.historical_candidate === historical && (documentsFor(role).length > 0 || (view === 'all' && !role.document_count)))
  const visible = base.filter(role => `${name(role)} ${role.unit_name}`.toLocaleLowerCase('ru').includes(search) || documentsFor(role).some(matches))
  const branches = buildPositionHierarchy(visible)
  const directorBranch = branches.find(branch => branch.title === 'Руководство департамента' && branch.positions.length === 1 && name(branch.positions[0]) === 'Директор' && branch.children.length === 0)
  const managementBranch = branches.find(branch => branch.title === 'Руководство' && branch.positions.length === 0 && branch.children.length > 0 && branchPositions(branch).every(role => role.category === 'management'))
  const treeBranches = branches.filter(branch => branch !== directorBranch && branch !== managementBranch)
  const units = new Set(visible.map(role => role.unit_name))
  const unassigned = documents.filter(document => document.department_id === departmentId && !library.roles.some(role => role.id === document.role_id) && matches(document))
  const applicable = documents.filter(document => document.library_scope === 'bank' && matches(document))
  return <section className="mt-5" aria-label="Библиотека должностей">
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-5">
      <div><h2 className="text-lg font-semibold tracking-tight">Должностные инструкции</h2><p className="mt-1 text-xs leading-5 text-muted-foreground">Руководство департамента, направления, службы и группы.</p></div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {canCreate && !historical && <Button type="button" variant="outline" size="sm" onClick={() => setCreating(true)}><Plus className="size-3.5" />Новая должность</Button>}
        {(historical || historicalCount > 0) && <button type="button" className={`shrink-0 rounded-lg border px-3 py-2 text-xs transition-colors ${historical ? 'bg-muted font-medium' : 'text-muted-foreground hover:bg-muted/40'}`} aria-pressed={historical} onClick={() => onNavigation({ historical: !historical })}>{historical ? 'Вернуться к основной структуре' : `Прежняя структура · ${historicalCount}`}</button>}
      </div>
    </div>
    {creating && <PositionEditor departmentId={departmentId} onClose={() => setCreating(false)} onSaved={role => { setCreating(false); onSelect(role.id) }} />}
    <p className="mt-4 text-xs text-muted-foreground">{plural(visible.length, 'должность', 'должности', 'должностей')} · {plural(units.size, 'служба или группа', 'службы или группы', 'служб и групп')}{historical && ' · из прежних папок'}</p>
    {!visible.length && !search && unassigned.length > 0 ? <p className="mt-4 rounded-lg border bg-muted/30 px-4 py-3 text-sm leading-6 text-muted-foreground">Должности в департаменте ещё не заведены. Загруженные инструкции — в списке ниже.</p> : !visible.length ? <div className="py-12 text-center"><BriefcaseBusiness className="mx-auto size-8 text-muted-foreground" /><h3 className="mt-4 font-medium">{search ? 'Должности не найдены' : view === 'current' ? 'Нет инструкций с подтверждённой действующей версией' : view === 'historical' ? 'Нет инструкций с подтверждёнными прежними версиями' : 'В этом разделе пока нет инструкций по должностям'}</h3><p className="mt-2 text-sm text-muted-foreground">{search ? 'Измените поисковый запрос.' : 'Состояние определяется по документам и подтверждённым датам действия.'}</p></div> : <div className="mt-5">
      {directorBranch && <div className="mx-auto max-w-sm"><PositionCard role={directorBranch.positions[0]} instructions={documentsFor(directorBranch.positions[0])} searching={search} caption={directorBranch.title} onSelect={onSelect} /></div>}
      {managementBranch && <section aria-label={managementBranch.title} className={directorBranch ? 'pt-6' : ''}>{directorBranch && <div aria-hidden="true" className="mx-auto -mt-6 h-6 w-px bg-border" />}<div className="border-t pt-5"><PositionNodes branches={managementBranch.children} searching={search} documentsFor={documentsFor} onSelect={onSelect} /></div></section>}
      {treeBranches.length > 0 && <div className={directorBranch || managementBranch ? 'mt-7 border-t pt-6' : ''}><PositionNodes branches={treeBranches} searching={search} documentsFor={documentsFor} onSelect={onSelect} /></div>}
    </div>}
    {[{ title: 'Инструкции без должности', items: unassigned }, { title: 'Применимые общебанковские инструкции', items: applicable }].filter(group => group.items.length > 0).map(group => <details key={group.title} open={search || !visible.length ? true : undefined} className="mt-5 rounded-xl border bg-card" aria-label={group.title}><summary className="cursor-pointer px-4 py-3 text-sm font-medium">{group.title}<span className="ml-2 text-xs text-muted-foreground">{group.items.length}</span></summary><ul className="max-h-80 divide-y overflow-y-auto border-t">{group.items.map(document => <li key={document.id}><button type="button" onClick={() => onDocument(document.id)} className="flex w-full items-start gap-3 p-4 text-left outline-none hover:bg-muted/30 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"><FileText className="mt-1 size-4 shrink-0 text-muted-foreground" /><span className="min-w-0 flex-1 break-words text-sm leading-6">{document.title}</span><ChevronRight className="mt-1 size-4 shrink-0 text-muted-foreground" /></button></li>)}</ul></details>)}
  </section>
}

interface PositionViewProps {
  departmentId: string; positionId: string; sourceId: string; documentId?: string; canEdit: boolean; canDelete: boolean; busy: boolean; view: DocumentsView
  onBack: () => void; onDocument: (documentId: string) => void; onUpload: () => void; onDeleted: () => void
  renderDocument: (document: DocumentDetail, editionId?: string) => ReactNode
}
export function BusinessPositionView({ departmentId, positionId, sourceId, documentId, canEdit, canDelete, busy, view, onBack, onDocument, onUpload, onDeleted, renderDocument }: PositionViewProps) {
  const [detail, setDetail] = useState<PositionDetail>()
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [editing, setEditing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    void Promise.all([getPosition(departmentId, positionId, controller.signal), getDocuments('department', departmentId, view, controller.signal)]).then(([data, documents]) => { if (!controller.signal.aborted) { const ids = new Set(documents.map(document => document.id)); setDetail({ ...data, documents: data.documents.filter(document => ids.has(document.id)) }); setError('') } }).catch(cause => { if (!controller.signal.aborted) setError(errorText(cause)) })
    return () => controller.abort()
  }, [departmentId, positionId, view, retry])
  if (error) return <div role="alert"><Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft className="size-4" />К должностям</Button><p className="mt-5 text-sm">{error}</p><Button variant="outline" className="mt-3" onClick={() => setRetry(value => value + 1)}>Повторить</Button></div>
  if (!detail) return <div role="status" className="flex items-center gap-2 py-12 text-sm text-muted-foreground"><Spinner className="size-4" />Открываем должность…</div>
  const selected = detail.documents.find(document => document.id === documentId) || detail.documents.find(document => document.editions.some(edition => edition.files.some(file => file.id === sourceId))) || detail.documents[0]
  return <>
    <button type="button" disabled={busy} className="inline-flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50" onClick={onBack}><ArrowLeft className="size-3.5" />Должности департамента</button>
    <header className="mt-6 border-b pb-6"><div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between xl:gap-6"><div className="min-w-0 flex-1"><p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{CATEGORIES.find(item => item.id === detail.role.category)?.label}</p><h1 className="mt-3 break-words text-3xl font-semibold leading-10 tracking-tight">{name(detail.role)}</h1><p className="mt-3 text-sm leading-6 text-muted-foreground">{detail.role.unit_name}</p></div>{(canEdit || canDelete) && <div className="flex flex-wrap gap-2 xl:mt-1 xl:shrink-0 xl:justify-end">
      {canEdit && <Button size="sm" disabled={busy} onClick={onUpload}><Upload className="size-3.5" />Загрузить инструкцию</Button>}
      {canEdit && <Button variant="outline" size="sm" disabled={busy} onClick={() => setEditing(true)}><Pencil className="size-3.5" />Переименовать или перенести</Button>}
      {canDelete && !detail.documents.length && <Button variant="outline" size="sm" disabled={busy} onClick={() => setDeleting(true)}><Trash2 className="size-3.5" />Удалить должность</Button>}
    </div>}</div>{detail.role.historical_candidate && <p className="mt-4 text-xs leading-5 text-muted-foreground">Документы из папки прежних редакций. Актуальность должности и документов не подтверждена.</p>}</header>
    {detail.documents.length > 1 && <section className="mt-6" aria-label="Выбор инструкции должности"><h2 className="mb-3 text-sm font-semibold">Инструкции должности · {detail.documents.length}</h2><div className="flex flex-wrap gap-2">{detail.documents.map(document => <Button key={document.id} type="button" disabled={busy} variant="outline" aria-pressed={selected?.id === document.id} className={`h-auto max-w-full whitespace-normal px-4 py-3 text-left text-xs leading-5 ${selected?.id === document.id ? 'bg-muted' : ''}`} onClick={() => onDocument(document.id)}><FileText className="size-4 shrink-0" />{document.title}</Button>)}</div></section>}
    {selected ? <section className="mt-6" aria-label="Инструкция должности">{renderDocument(selected, defaultInstructionEdition(selected, view, undefined, sourceId))}</section> : <div className="py-16 text-center"><FileText className="mx-auto size-8 text-muted-foreground" /><p className="mt-4 text-sm">У должности пока нет доступных инструкций.</p></div>}
    {editing && <PositionEditor position={detail.role} departmentId={departmentId} onClose={() => setEditing(false)} onSaved={role => { setDetail({ ...detail, role }); setEditing(false) }} />}
    <ConfirmDialog open={deleting} onOpenChange={setDeleting} title={`Удалить должность «${name(detail.role)}»?`}
      description={`${detail.role.unit_name}. Должность без инструкций будет удалена из структуры департамента; запись останется в журнале.`}
      confirmLabel="Удалить" onConfirm={async () => { await deletePosition(departmentId, detail.role.id); toast.success('Должность удалена'); onDeleted() }} />
  </>
}

/** Edits a position, or creates one in the department when no position is given. */
function PositionEditor({ position, departmentId, onClose, onSaved }: { position?: BusinessPosition; departmentId: string; onClose: () => void; onSaved: (position: BusinessPosition) => void }) {
  const [units, setUnits] = useState<string[]>([])
  useEffect(() => {
    // Existing services of the department, to move the position into one of them.
    const controller = new AbortController()
    void getPositionLibrary(departmentId, controller.signal).then(library => setUnits([...new Set(library.roles.map(role => role.unit_name))].filter(unit => unit !== 'Служба не указана').sort((a, b) => a.localeCompare(b, 'ru')))).catch(() => undefined)
    return () => controller.abort()
  }, [departmentId])
  const [displayName, setDisplayName] = useState(position ? name(position) : '')
  const [unit, setUnit] = useState(position?.unit_name || '')
  const [category, setCategory] = useState<PositionCategory>(position?.category || 'specialists')
  const [evidence, setEvidence] = useState(position?.classification_evidence || '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(event: React.FormEvent) {
    event.preventDefault(); if (busy) return
    setBusy(true); setError('')
    try {
      const data = { display_name: displayName, unit_name: unit, category, classification_evidence: evidence, historical_candidate: position?.historical_candidate || false }
      onSaved(await (position ? updatePosition(departmentId, position.id, data) : createPosition(departmentId, data))); toast.success(position ? 'Должность сохранена' : 'Должность создана')
    }
    catch (cause) { setError(errorText(cause)) }
    finally { setBusy(false) }
  }
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}><DialogContent className="max-w-xl" onEscapeKeyDown={event => { if (busy) event.preventDefault() }} onInteractOutside={event => { if (busy) event.preventDefault() }}><DialogHeader><DialogTitle>{position ? 'Переименовать или перенести должность' : 'Новая должность'}</DialogTitle><DialogDescription>{position ? 'Название и служба определяют место в дереве. Инструкции остаются за должностью; права доступа сотрудников не меняются.' : 'Должность появится в структуре департамента; инструкцию можно загрузить сразу или позже. Права доступа сотрудников не меняются.'}</DialogDescription></DialogHeader><form onSubmit={event => void submit(event)} className="mt-3 space-y-5"><div className="space-y-2"><Label htmlFor="position-name">Должность</Label><Input id="position-name" value={displayName} onChange={event => setDisplayName(event.target.value)} maxLength={300} required disabled={busy} /></div><div className="space-y-2"><Label htmlFor="position-unit">Служба или группа</Label><Input id="position-unit" list="position-editor-units" value={unit} onChange={event => setUnit(event.target.value)} maxLength={300} required disabled={busy} /><datalist id="position-editor-units">{units.map(item => <option key={item} value={item} />)}</datalist><p className="text-xs leading-5 text-muted-foreground">Чтобы перенести, выберите существующую службу из подсказок или введите новую. Вложенная группа — через «/».</p></div><div className="space-y-2"><Label>Категория</Label><Select value={category} disabled={busy} onValueChange={value => setCategory(value as PositionCategory)}><SelectTrigger aria-label="Категория должности"><SelectValue /></SelectTrigger><SelectContent>{CATEGORIES.map(item => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}</SelectContent></Select></div><div className="space-y-2"><Label htmlFor="position-evidence">Основание</Label><textarea id="position-evidence" placeholder="Например, штатное расписание или приказ о структуре" value={evidence} onChange={event => setEvidence(event.target.value)} maxLength={2000} required disabled={busy} className="min-h-24 w-full rounded-lg border bg-background px-3 py-2 text-sm leading-6 outline-none focus-visible:ring-2 focus-visible:ring-foreground/30" /></div>{error && <p role="alert" className="text-sm text-destructive">{error}</p>}<div className="flex justify-end gap-3 border-t pt-4"><Button type="button" variant="outline" disabled={busy} onClick={onClose}>Отмена</Button><Button type="submit" disabled={busy || !displayName.trim() || !unit.trim() || !evidence.trim()}>{busy && <Spinner className="size-4" />}{position ? 'Сохранить' : 'Создать'}</Button></div></form></DialogContent></Dialog>
}
