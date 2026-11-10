import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { ArrowLeft, ChevronRight, Download, FileText, Folder, Plus, RefreshCw, Search, Trash2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { UserMenu } from '@/components/UserMenu'
import { KnowledgeShell } from '@/components/KnowledgeShell'
import { DocumentPreparation } from '@/components/DocumentPreparation'
import { DeletedInstructions, InstructionLifecycleDialog, type LifecycleAction } from '@/components/InstructionLifecycle'
import { downloadOriginal, getDepartments, getInstructions, getKnowledgeAccess, getRoles, getVersions, KnowledgeRequestError, uploadInstruction, uploadVersion } from '@/lib/knowledge'
import type { Department, Instruction, InstructionVersion, KnowledgeRole } from '@/lib/knowledge'
import type { DocumentCapability } from '@/lib/knowledgeAccess'
import { randomUUID } from '@/lib/utils'

const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'Не удалось выполнить действие'
const dateLabel = (date: string) => /^\d{4}-\d{2}-\d{2}$/.test(date) ? date.split('-').reverse().join('.') : new Date(date).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' })

interface Registry {
  department: Department
  isAdmin: boolean
  roles: KnowledgeRole[]
  instructions: Instruction[]
}

export default function KnowledgePage() {
  const { departmentId = '' } = useParams()
  return <KnowledgeDepartment key={departmentId} departmentId={departmentId} />
}

function KnowledgeDepartment({ departmentId }: { departmentId: string }) {
  const [registry, setRegistry] = useState<Registry>()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [params, setParams] = useSearchParams()
  const selectedId = params.get('instruction')
  const setSelectedId = (id: string | null) => setParams(id ? { instruction: id } : {})
  const [form, setForm] = useState<'new' | 'version' | null>(null)
  const [saving, setSaving] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [listError, setListError] = useState('')
  const [historyRevision, setHistoryRevision] = useState(0)
  const [query, setQuery] = useState('')
  const listRequest = useRef<AbortController | null>(null)
  const [view, setView] = useState<'active' | 'deleted'>('active')
  const [lifecycle, setLifecycle] = useState<LifecycleAction>()
  const [trashRevision, setTrashRevision] = useState(0)
  const allowed = (capability: DocumentCapability) => Boolean(registry?.isAdmin || registry?.department.capabilities.includes(capability))
  const canCreate = allowed('instructions.create')
  const canVersion = allowed('versions.create')
  const canDelete = allowed('instructions.delete')
  const canRestore = allowed('instructions.restore')
  const selected = registry?.instructions.find(item => item.id === selectedId)
  const search = query.trim().toLocaleLowerCase('ru')
  const visibleInstructions = registry?.instructions.filter(item => `${item.title} ${item.role_name ?? ''}`.toLocaleLowerCase('ru').includes(search)) ?? []

  useEffect(() => {
    const controller = new AbortController()
    void Promise.all([getDepartments(controller.signal), getKnowledgeAccess(controller.signal)])
      .then(async ([departments, access]) => {
        const department = departments.find(item => item.id === departmentId)
        if (!department) throw new Error('Департамент недоступен. Проверьте назначение доступа у администратора')
        const [instructions, roles] = await Promise.all([getInstructions(departmentId, controller.signal), getRoles(departmentId, controller.signal)])
        if (!controller.signal.aborted) setRegistry({ department, isAdmin: access.is_platform_admin, instructions, roles })
      }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
    return () => { controller.abort(); listRequest.current?.abort() }
  }, [departmentId, attempt])

  async function refreshInstructions() {
    listRequest.current?.abort()
    const controller = new AbortController()
    listRequest.current = controller
    setRefreshing(true)
    setListError('')
    try {
      const instructions = await getInstructions(departmentId, controller.signal)
      if (!controller.signal.aborted) setRegistry(previous => previous ? { ...previous, instructions } : previous)
    } catch (cause) {
      if (!controller.signal.aborted) setListError(errorMessage(cause))
    } finally { if (!controller.signal.aborted) setRefreshing(false) }
  }

  function onSaved(instruction: Instruction, version: InstructionVersion) {
    const saved = { ...instruction, version_count: Math.max(instruction.version_count, version.number) }
    setRegistry(previous => previous ? { ...previous, instructions: previous.instructions.some(item => item.id === instruction.id)
      ? previous.instructions.map(item => item.id === instruction.id ? saved : item)
      : [saved, ...previous.instructions] } : previous)
    setParams({ instruction: instruction.id, version: version.id })
    setForm(null)
    setHistoryRevision(value => value + 1)
    void refreshInstructions()
  }

  function onLifecycleChanged(target: LifecycleAction) {
    setLifecycle(undefined)
    setTrashRevision(value => value + 1)
    if (target.action === 'delete') {
      setRegistry(previous => previous ? { ...previous, instructions: previous.instructions.filter(item => item.id !== target.instruction.id) } : previous)
      setSelectedId(null)
    }
    void refreshInstructions()
  }

  return <KnowledgeShell>
    <div className="flex items-center justify-between gap-3">
      <Link to="/knowledge" aria-disabled={saving || undefined} onClick={event => { if (saving) event.preventDefault() }} className="inline-flex min-w-0 items-center gap-2 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4 shrink-0" /><span>Документы департаментов</span></Link>
      <UserMenu />
    </div>
    <header className="mt-7 border-b pb-6">
      <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Документы департаментов</p>
      <div className="mt-3 flex items-start gap-3"><Folder className="mt-1 size-7 shrink-0 text-blue-950 dark:text-white" /><h1 className="min-w-0 break-words text-2xl font-semibold tracking-tight sm:text-3xl">{registry?.department.name ?? 'Должностные инструкции'}</h1></div>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">Должностные инструкции департамента и история их редакций.</p>
    </header>
    {error ? <div role="alert" className="mt-6 space-y-3 rounded-lg border p-4 text-sm"><p>{error}</p><Button variant="outline" size="sm" onClick={() => { setError(''); setRegistry(undefined); setAttempt(value => value + 1) }}>Повторить</Button></div>
    : !registry ? <div role="status" className="mt-8 flex items-center gap-2 text-sm text-muted-foreground"><Spinner className="size-4" /> Загружаем документы…</div>
    : <Dialog open={form !== null} onOpenChange={open => { if (!open && !saving) setForm(null) }}>
      {listError && <div role="alert" className="mt-6 space-y-2 rounded-lg border p-4 text-sm"><p>Не удалось обновить реестр: {listError}</p><Button size="sm" variant="outline" disabled={refreshing || saving} onClick={() => void refreshInstructions()}>Повторить</Button></div>}
      {selected ? <section className="mt-6 min-w-0" aria-label="Инструкция">
        <Button type="button" variant="ghost" size="sm" disabled={saving} className="-ml-2 gap-2" onClick={() => setSelectedId(null)}><ArrowLeft className="size-4" /> Назад к инструкциям</Button>
        <article className="mt-4 overflow-hidden rounded-xl border bg-card">
          <div className="flex flex-wrap items-start justify-between gap-4 border-b p-5 sm:p-6">
            <div className="min-w-0 flex-1"><h2 className="break-words text-xl font-semibold leading-7">{selected.title}</h2>{selected.role_name && <p className="mt-2 break-words text-sm text-muted-foreground">{selected.role_name}</p>}</div>
            <div className="flex shrink-0 items-center gap-2">{canVersion && <DialogTrigger asChild><Button disabled={saving} onClick={() => setForm('version')} className="gap-2"><Upload className="size-4" />{selected.version_count === 0 ? 'Загрузить оригинал' : 'Добавить редакцию'}</Button></DialogTrigger>}{canDelete && <Button variant="ghost" disabled={saving} aria-label={`Удалить инструкцию ${selected.title}`} onClick={() => setLifecycle({ instruction: selected, action: 'delete' })}><Trash2 className="size-4" />Удалить</Button>}</div>
          </div>
          <div className="p-5 sm:p-6"><InstructionHistory key={`${selected.id}:${selected.version_count}:${historyRevision}`} instructionId={selected.id} canVersion={canVersion} canDownload={allowed('documents.download')} canPrepare={allowed('preparation.manage')} selectedVersion={params.get('version') ?? ''} setSelectedVersion={id => setParams({ instruction: selected.id, version: id })} /></div>
        </article>
      </section> : <section aria-label="Инструкции ролей" aria-busy={refreshing} className="mt-6 min-w-0">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">{canDelete || canRestore ? <nav aria-label="Раздел реестра" className="flex rounded-lg bg-muted/60 p-1"><Button variant="ghost" size="sm" aria-pressed={view === 'active'} className={view === 'active' ? 'bg-background shadow-sm' : ''} disabled={saving} onClick={() => setView('active')}>Инструкции</Button><Button variant="ghost" size="sm" aria-pressed={view === 'deleted'} className={view === 'deleted' ? 'bg-background shadow-sm' : ''} disabled={saving} onClick={() => setView('deleted')}><Trash2 className="size-3.5" />Удалённые</Button></nav> : <h2 className="text-lg font-semibold">Инструкции</h2>}</div>
          {view === 'active' && <div className="flex flex-wrap items-center gap-2"><Button type="button" variant="ghost" size="sm" disabled={saving || refreshing} onClick={() => void refreshInstructions()} className="gap-1.5">{refreshing ? <Spinner className="size-3.5" /> : <RefreshCw className="size-3.5" />} Обновить</Button>{canCreate && <DialogTrigger asChild><Button disabled={saving} onClick={() => setForm('new')} className="gap-2"><Plus className="size-4" /> Добавить инструкцию</Button></DialogTrigger>}</div>}
        </div>
        {view === 'active' && (registry.instructions.length >= 6 || query) && <div className="relative mt-4 max-w-lg"><Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" /><Input aria-label="Поиск инструкций" placeholder="Найти инструкцию или роль" className="pl-9" value={query} onChange={event => setQuery(event.target.value)} /></div>}
        {view === 'deleted' ? <DeletedInstructions departmentId={departmentId} revision={trashRevision} canRestore={canRestore} onRestore={instruction => setLifecycle({ instruction, action: 'restore' })} /> : registry.instructions.length === 0 ? <div className="px-4 py-16 text-center sm:py-20">
          <FileText className="mx-auto size-9 text-muted-foreground" />
          <h3 className="mt-4 text-base font-medium">Инструкций пока нет</h3>
          <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted-foreground">{canCreate ? 'Загрузите первый документ департамента. Должностную роль указывать необязательно.' : 'Ответственный за документы добавит инструкции департамента.'}</p>
        </div> : visibleInstructions.length === 0 ? <div className="py-12 text-center"><p className="text-sm text-muted-foreground">Инструкции не найдены</p><Button type="button" variant="ghost" size="sm" className="mt-3" onClick={() => setQuery('')}>Сбросить поиск</Button></div>
        : <ul className="mt-4 overflow-hidden rounded-xl border bg-card divide-y">{visibleInstructions.map(item => <li key={item.id} className="flex items-center pr-3"><button type="button" disabled={saving} className="flex min-w-0 flex-1 items-start gap-3 px-4 py-5 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:opacity-60 sm:px-5" onClick={() => setSelectedId(item.id)}><FileText className="mt-0.5 size-5 shrink-0 text-muted-foreground" /><span className="min-w-0 flex-1"><span className="block break-words text-sm font-medium leading-6">{item.title}</span><span className="mt-1 block break-words text-xs leading-5 text-muted-foreground">{item.role_name}</span></span><ChevronRight className="mt-1 size-4 shrink-0 text-muted-foreground" /></button>{canDelete && <Button variant="ghost" size="icon" disabled={saving} aria-label={`Удалить инструкцию ${item.title}`} onClick={() => setLifecycle({ instruction: item, action: 'delete' })}><Trash2 className="size-4" /></Button>}</li>)}</ul>}
      </section>}
      <DialogContent className="max-h-[85vh] max-w-[calc(100%_-_2rem)] overflow-y-auto p-5 sm:max-w-xl sm:p-6" showCloseButton={!saving} onEscapeKeyDown={event => { if (saving) event.preventDefault() }} onPointerDownOutside={event => { if (saving) event.preventDefault() }}>
        <DialogHeader className="min-w-0 pr-5 text-left"><DialogTitle>{form === 'new' ? 'Новая инструкция' : selected?.version_count === 0 ? 'Оригинал инструкции' : 'Новая редакция'}</DialogTitle><DialogDescription className="break-words leading-6">{form === 'new' ? registry.department.name : selected?.title}</DialogDescription></DialogHeader>
        {form && (form === 'new' || selected) && <InstructionUploadForm key={form === 'new' ? 'new' : selected?.id} departmentId={departmentId} roles={registry.roles} instruction={form === 'version' ? selected : undefined} onCancel={() => setForm(null)} onBusy={setSaving} onSaved={onSaved} onConflict={() => { void refreshInstructions(); if (form === 'version') setHistoryRevision(value => value + 1) }} />}
      </DialogContent>
    </Dialog>}
    {lifecycle && <InstructionLifecycleDialog key={`${lifecycle.action}:${lifecycle.instruction.id}`} target={lifecycle} onClose={() => setLifecycle(undefined)} onChanged={onLifecycleChanged} onBusy={setSaving} />}
  </KnowledgeShell>
}

interface UploadFormProps {
  departmentId: string
  roles: KnowledgeRole[]
  instruction?: Instruction
  onCancel: () => void
  onBusy: (value: boolean) => void
  onSaved: (instruction: Instruction, version: InstructionVersion) => void
  onConflict: () => void
}

function InstructionUploadForm({ departmentId, roles, instruction, onCancel, onBusy, onSaved, onConflict }: UploadFormProps) {
  const [title, setTitle] = useState('')
  const [roleId, setRoleId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const uploadId = useRef(randomUUID())
  const creating = !instruction

  function edited() { uploadId.current = randomUUID(); setError('') }

  async function save(event: FormEvent) {
    event.preventDefault()
    if (busy || !file) return
    if (file.size === 0 || file.size > 10 * 1024 * 1024) { setError('Загрузите непустой файл до 10 МБ'); return }
    if (!/\.(pdf|docx)$/i.test(file.name)) { setError('Выберите файл в формате PDF или DOCX'); return }
    if (creating && (!title.trim() || (roleId && !roles.some(role => role.id === roleId)))) { setError('Укажите название и проверьте должностную роль'); return }
    if (to && (!from || to < from)) { setError('Укажите дату начала действия. Дата окончания должна быть не раньше даты начала'); return }
    setBusy(true)
    onBusy(true)
    setError('')
    try {
      if (instruction) {
        const version = await uploadVersion(instruction.id, file, from, to, uploadId.current)
        onSaved(instruction, version)
      } else {
        const result = await uploadInstruction(departmentId, roleId, title.trim(), file, from, to, uploadId.current)
        onSaved(result.instruction, result.version)
      }
      toast.success(creating ? 'Инструкция и её оригинал сохранены' : 'Новая редакция сохранена')
    } catch (cause) {
      setError(errorMessage(cause))
      if (cause instanceof KnowledgeRequestError && cause.status === 409) onConflict()
    } finally { setBusy(false); onBusy(false) }
  }

  return <form onSubmit={event => void save(event)} aria-busy={busy} className="min-w-0 space-y-5">
    {creating && <><label className="block text-sm font-medium">Название инструкции<Input className="mt-2" required maxLength={300} placeholder="Например, инструкция кредитного специалиста" value={title} disabled={busy} autoFocus onChange={event => { setTitle(event.target.value); edited() }} /></label>{roles.length > 0 && <div className="space-y-2"><p id="instruction-job-role" className="text-sm font-medium">Должностная роль <span className="font-normal text-muted-foreground">· необязательно</span></p><Select value={roleId || 'none'} disabled={busy} onValueChange={value => { setRoleId(value === 'none' ? '' : value); edited() }}><SelectTrigger aria-labelledby="instruction-job-role" className="w-full"><SelectValue /></SelectTrigger><SelectContent position="popper"><SelectItem value="none">Без указания должностной роли</SelectItem>{roles.map(role => <SelectItem key={role.id} value={role.id}>{role.name}</SelectItem>)}</SelectContent></Select></div>}</>}
    <div className="rounded-xl border border-dashed bg-muted/30 p-4">
      <label className="block text-sm font-medium"><span className="flex items-center gap-2"><FileText className="size-4 text-muted-foreground" />{creating || instruction?.version_count === 0 ? 'Файл инструкции' : 'Файл новой редакции'}</span><span className="mt-1 block text-xs font-normal leading-5 text-muted-foreground">PDF или DOCX, до 10 МБ</span><Input className="mt-3 h-auto bg-background py-2 font-normal" type="file" accept=".pdf,.docx" required disabled={busy} onChange={event => { setFile(event.target.files?.[0] ?? null); edited() }} /></label>
    </div>
    <fieldset className="min-w-0 space-y-4"><legend className="mb-2 text-sm font-medium">Период действия <span className="font-normal text-muted-foreground">· необязательно</span></legend><label className="block text-sm">Действует с<Input className="mt-2" type="date" value={from} disabled={busy} onChange={event => { setFrom(event.target.value); edited() }} /></label><label className="block text-sm">Действует до<Input className="mt-2" type="date" min={from || undefined} value={to} disabled={busy} onChange={event => { setTo(event.target.value); edited() }} /></label></fieldset>
    <p className="text-xs leading-5 text-muted-foreground">Оставьте даты пустыми, если период неизвестен. Загрузка сохраняет оригинал и историю, но не означает утверждение документа.</p>
    {error && <p role="alert" className="text-sm leading-6 text-destructive">{error}</p>}
    <div className="flex flex-col-reverse gap-2 border-t pt-4 sm:flex-row sm:justify-end"><Button type="button" variant="outline" disabled={busy} onClick={onCancel}>Отмена</Button><Button type="submit" disabled={busy || !file || (creating && !title.trim())} className="gap-2">{busy ? <Spinner className="size-4" /> : <Upload className="size-4" />}{busy ? 'Сохраняем…' : creating ? 'Сохранить инструкцию' : instruction?.version_count === 0 ? 'Сохранить оригинал' : 'Сохранить редакцию'}</Button></div>
  </form>
}

function InstructionHistory({ instructionId, canVersion, canDownload, canPrepare, selectedVersion, setSelectedVersion }: { instructionId: string; canVersion: boolean; canDownload: boolean; canPrepare: boolean; selectedVersion: string; setSelectedVersion: (id: string) => void }) {
  const [versions, setVersions] = useState<InstructionVersion[]>()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    void getVersions(instructionId, controller.signal).then(data => { if (!controller.signal.aborted) setVersions(data) })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
    return () => controller.abort()
  }, [instructionId, attempt])
  return <div>
    {error ? <div role="alert" className="mt-3 space-y-3 rounded-lg border p-3 text-sm"><p>{error}</p><Button size="sm" variant="outline" onClick={() => { setError(''); setVersions(undefined); setAttempt(value => value + 1) }}>Повторить загрузку истории</Button></div>
    : !versions ? <div role="status" className="mt-4 flex items-center gap-2 text-sm text-muted-foreground"><Spinner className="size-4" /> Загружаем редакции…</div>
    : versions.length === 0 ? <p className="mt-3 text-sm leading-6 text-muted-foreground">{canVersion ? 'Оригинал ещё не добавлен. Нажмите «Загрузить оригинал».' : 'Оригинал ещё не добавлен. Ответственный за документы загрузит инструкцию.'}</p>
    : <>
      <div className="max-w-xl"><Select value={versions.some(item => item.id === selectedVersion) ? selectedVersion : versions[0].id} onValueChange={setSelectedVersion}><SelectTrigger aria-label="Редакция для просмотра"><SelectValue /></SelectTrigger><SelectContent>{versions.map(version => <SelectItem key={version.id} value={version.id}>Редакция {version.number} · {dateLabel(version.uploaded_at)}</SelectItem>)}</SelectContent></Select></div>
      {(() => { const version = versions.find(item => item.id === selectedVersion) ?? versions[0]; return <><ol><VersionRow key={version.id} version={version} canDownload={canDownload} compact /></ol><DocumentPreparation key={version.id} versionId={version.id} owner={canPrepare} /></> })()}
      {versions.length > 1 && <details className="mt-6 border-t pt-4"><summary className="cursor-pointer text-sm font-medium">Все редакции · {versions.length}</summary><ol className="mt-3 divide-y">{versions.map(version => <VersionRow key={version.id} version={version} canDownload={canDownload} />)}</ol></details>}
    </>}
  </div>
}

function VersionRow({ version, canDownload, compact = false }: { version: InstructionVersion; canDownload: boolean; compact?: boolean }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function download() {
    if (busy) return
    setBusy(true)
    setError('')
    try { await downloadOriginal(version) }
    catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy(false) }
  }
  return <li className={compact ? 'pt-4' : 'py-5'}><div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0 flex-1">{!compact && <p className="text-sm font-medium">Редакция {version.number}</p>}<p className="break-words text-sm text-muted-foreground">{version.filename} · {Math.max(1, Math.ceil(version.byte_size / 1024))} КБ</p>{!compact && <p className="mt-2 text-xs leading-5 text-muted-foreground">Добавлена {dateLabel(version.uploaded_at)}</p>}<p className="mt-1 text-xs leading-5 text-muted-foreground">{version.effective_from ? `Действует с ${dateLabel(version.effective_from)}${version.effective_to ? ` до ${dateLabel(version.effective_to)}` : ''}` : 'Период действия не указан'}</p></div>{canDownload && <Button type="button" variant="outline" size="sm" disabled={busy} aria-label={`Скачать оригинал редакции ${version.number}`} className="shrink-0 gap-1.5" onClick={() => void download()}>{busy ? <Spinner className="size-4" /> : <Download className="size-4" />}<span>{busy ? 'Скачиваем…' : 'Оригинал'}</span></Button>}</div>{error && <p role="alert" className="mt-2 text-xs leading-5 text-destructive">{error}. Повторите скачивание кнопкой «Оригинал».</p>}</li>
}
