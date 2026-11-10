import { useEffect, useRef, useState, type FormEvent } from 'react'
import { CheckCircle2, FilePlus2, FileText, LockKeyhole, Plus, Trash2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Spinner } from '@/components/ui/spinner'
import { formatFileSize } from '@/lib/presentation'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { KnowledgeRequestError, type Department } from '@/lib/knowledge'
import { changeDocumentLifecycle, endDocumentEditionValidity, getPositionLibrary, updateDocument, uploadDocument, uploadDocumentEdition, verifyDocumentEdition, type BusinessPosition, type DocumentDetail, type DocumentEdition, type DocumentProperties, type DocumentType, type DocumentUploadFile, type DocumentUploadResult, type NewPosition, type RegistryDocument } from '@/lib/documents'
import { randomUUID } from '@/lib/utils'

const message = (cause: unknown) => cause instanceof Error ? cause.message : 'Не удалось сохранить документ'
const textValue = (metadata: Record<string, unknown>, key: string) => typeof metadata[key] === 'string' ? metadata[key] as string : ''
const purposeLabels = { main: 'Основной текст', attachment: 'Приложение', approval: 'Подтверждение утверждения', supplement: 'Сопроводительный материал' } as const
const positionLabel = (position: BusinessPosition) => [position.display_name || position.name, position.unit_name, ...(position.historical_candidate ? ['Прежняя структура'] : [])].filter(Boolean).join(' · ')

function PropertiesFields({ value, onChange, types, departments, roles, positionsEnabled, ownerLocked, disabled }: {
  value: DocumentProperties
  onChange: (value: DocumentProperties) => void
  types: DocumentType[]
  departments: Department[]
  roles: BusinessPosition[]
  positionsEnabled: boolean
  ownerLocked: boolean
  disabled: boolean
}) {
  const change = (patch: Partial<DocumentProperties>) => onChange({ ...value, ...patch })
  const metadata = (key: string, text: string) => change({ metadata: { ...value.metadata, [key]: text } })
  const selectedPosition = roles.find(role => role.id === value.role_id)
  return <div className="min-w-0 space-y-5">
    <label className="block text-sm font-medium">Название документа<Input className="mt-2" required maxLength={300} value={value.title} disabled={disabled} autoFocus onChange={event => change({ title: event.target.value })} placeholder="Название из оригинала" /></label>
    <div className="min-w-0 space-y-2">
      <label id="document-owner-label" className="text-sm font-medium">Владелец{!ownerLocked && <span className="font-normal text-muted-foreground"> · необязательно</span>}</label>
      {ownerLocked ? <div role="textbox" aria-labelledby="document-owner-label" aria-readonly="true" tabIndex={0} className="flex min-h-10 w-full items-start gap-3 rounded-md border border-input bg-muted/30 px-3 py-2 text-sm shadow-xs outline-none focus-visible:border-foreground/35">
        <span className="min-w-0 flex-1 break-words leading-5 [overflow-wrap:anywhere]">{departments.find(department => department.id === value.owner_department_id)?.name || 'Выбранный департамент'}</span>
        <LockKeyhole aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      </div> : <Select value={value.owner_department_id || 'none'} disabled={disabled} onValueChange={id => change({ owner_department_id: id === 'none' ? null : id, role_id: null })}>
        <SelectTrigger className="w-full min-w-0 [&_[data-slot=select-value]]:min-w-0 [&_[data-slot=select-value]]:truncate" aria-labelledby="document-owner-label"><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value="none">Не установлен</SelectItem>{departments.map(department => <SelectItem key={department.id} value={department.id}>{department.name}</SelectItem>)}</SelectContent>
      </Select>}
    </div>
    <div className="space-y-2">
      <label id="document-type-label" className="text-sm font-medium">Вид документа</label>
      <Select value={value.document_type} disabled={disabled} onValueChange={document_type => change({ document_type, ...(document_type !== 'job_instruction' ? { role_id: null, new_position: null } : {}) })}>
        <SelectTrigger className="w-full min-w-0 [&_[data-slot=select-value]]:min-w-0 [&_[data-slot=select-value]]:truncate" aria-labelledby="document-type-label"><SelectValue /></SelectTrigger>
        <SelectContent>{types.map(type => <SelectItem key={type.id} value={type.id}>{type.label}</SelectItem>)}</SelectContent>
      </Select>
    </div>
    {value.document_type === 'job_instruction' && positionsEnabled && <PositionField value={value} roles={roles} disabled={disabled} onChange={change} selected={selectedPosition} />}
    <div className="grid grid-cols-2 gap-4"><label className="block text-sm font-medium">Банковский номер<Input className="mt-2" maxLength={100} value={value.bank_number || ''} disabled={disabled} placeholder="Если указан в документе" onChange={event => change({ bank_number: event.target.value || null })} /></label><label className="block text-sm font-medium">Дата документа<Input className="mt-2" type="date" value={value.document_date || ''} disabled={disabled} onChange={event => change({ document_date: event.target.value || null })} /></label></div>
    {value.document_type === 'analytics' && <div className="space-y-4"><label className="block text-sm font-medium">Период данных<Input className="mt-2" disabled={disabled} value={textValue(value.metadata, 'analysis_period')} maxLength={300} placeholder="Например, сентябрь 2026" onChange={event => metadata('analysis_period', event.target.value)} /></label><label className="block text-sm font-medium">Методика и источник показателей<Textarea className="mt-2" disabled={disabled} value={textValue(value.metadata, 'analysis_method')} maxLength={2000} placeholder="Как получены показатели и что они измеряют" onChange={event => metadata('analysis_method', event.target.value)} /></label></div>}
  </div>
}

const categoryLabels: Record<NewPosition['category'], string> = { management: 'Руководство', specialists: 'Специалисты', support: 'Обеспечение' }

/** Existing position, or a new one created together with the document. */
function PositionField({ value, roles, disabled, onChange, selected }: { value: DocumentProperties; roles: BusinessPosition[]; disabled: boolean; onChange: (patch: Partial<DocumentProperties>) => void; selected?: BusinessPosition }) {
  const position = value.new_position
  const units = [...new Set(roles.map(role => role.unit_name).filter(unit => unit && unit !== 'Служба не указана'))].sort((a, b) => a.localeCompare(b, 'ru'))
  const set = (patch: Partial<NewPosition>) => position && onChange({ new_position: { ...position, ...patch } })
  return <div className="space-y-2">
    <label id="document-role-label" className="text-sm font-medium">Должность <span className="font-normal text-muted-foreground">· необязательно</span></label>
    <Select value={position ? 'new' : value.role_id || 'none'} disabled={disabled} onValueChange={id => onChange(id === 'new' ? { role_id: null, new_position: { display_name: '', unit_name: '', category: 'specialists' } } : { role_id: id === 'none' ? null : id, new_position: null })}>
      <SelectTrigger className="w-full min-w-0 [&_[data-slot=select-value]]:min-w-0 [&_[data-slot=select-value]]:truncate" aria-labelledby="document-role-label" title={selected ? positionLabel(selected) : undefined}><SelectValue /></SelectTrigger>
      <SelectContent><SelectItem value="none">Уточнить после приёма</SelectItem><SelectItem value="new"><Plus className="size-3.5" />Новая должность</SelectItem>{roles.map(role => <SelectItem key={role.id} value={role.id} className="whitespace-normal break-words">{positionLabel(role)}</SelectItem>)}</SelectContent>
    </Select>
    {position && <div className="space-y-3 rounded-lg border bg-muted/20 p-3">
      <label className="block text-xs font-medium">Название должности<Input className="mt-1.5" required maxLength={200} disabled={disabled} value={position.display_name} placeholder="Например, Ведущий менеджер" onChange={event => set({ display_name: event.target.value })} /></label>
      <label className="block text-xs font-medium">Служба / группа<Input className="mt-1.5" required maxLength={300} list="position-units" disabled={disabled} value={position.unit_name} placeholder="Например, Валютные операции / Группа SWIFT" onChange={event => set({ unit_name: event.target.value })} /><datalist id="position-units">{units.map(unit => <option key={unit} value={unit} />)}</datalist><span className="mt-1 block font-normal text-muted-foreground">Вложенная группа — через «/». Такая же служба и название — та же должность, дубль не создаётся.</span></label>
      <div className="space-y-1.5"><span id="position-category-label" className="block text-xs font-medium">Категория</span><Select value={position.category} disabled={disabled} onValueChange={category => set({ category: category as NewPosition['category'] })}><SelectTrigger className="w-full" aria-labelledby="position-category-label"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(categoryLabels).map(([key, label]) => <SelectItem key={key} value={key}>{label}</SelectItem>)}</SelectContent></Select></div>
    </div>}
  </div>
}

interface DocumentEditorProps {
  mode: 'new' | 'edition' | 'metadata'
  document?: DocumentDetail
  departments: Department[]
  types: DocumentType[]
  departmentId?: string
  documentType?: string
  /** Position the new instruction is filed under, when it is uploaded from that position. */
  roleId?: string
  scope: 'department' | 'bank'
  onClose: () => void
  onSaved: (result: DocumentUploadResult | RegistryDocument) => void
  onBusy: (busy: boolean) => void
  onConflict: () => void
}
export function DocumentEditor({ mode, document: current, departments, types, departmentId, documentType, roleId, scope, onClose, onSaved, onBusy, onConflict }: DocumentEditorProps) {
  const [properties, setProperties] = useState<DocumentProperties>(() => current ? { title: current.title, document_type: current.document_type, owner_department_id: current.owner_department_id, role_id: current.role_id, bank_number: current.bank_number, document_date: current.document_date, metadata: current.metadata, applicability: current.applicability } : { title: '', document_type: documentType || (scope === 'bank' ? 'procedure' : 'job_instruction'), owner_department_id: departmentId || null, role_id: roleId || null, bank_number: null, document_date: null, metadata: {}, applicability: [] })
  const [roles, setRoles] = useState<BusinessPosition[]>([])
  const [rolesError, setRolesError] = useState('')
  const [files, setFiles] = useState<DocumentUploadFile[]>([])
  const [label, setLabel] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const request = useRef<AbortController | null>(null)
  const uploadId = useRef(randomUUID())
  const input = useRef<HTMLInputElement>(null)
  const ownerLocked = mode === 'new' && scope === 'department' && !!departmentId
  const roleDepartment = scope === 'department' ? departmentId : undefined
  const isMetadata = mode === 'metadata'
  const isInstruction = properties.document_type === 'job_instruction'
  useEffect(() => {
    const controller = new AbortController()
    if (mode !== 'edition' && roleDepartment) {
      void getPositionLibrary(roleDepartment, controller.signal).then(data => { if (!controller.signal.aborted) { setRoles(data.roles); setRolesError('') } }).catch(cause => { if (!controller.signal.aborted) { setRoles([]); setRolesError(message(cause)) } })
    }
    return () => controller.abort()
  }, [roleDepartment, mode])
  useEffect(() => () => request.current?.abort(), [])
  function edited() { uploadId.current = randomUUID(); setError('') }
  function addFiles(next: FileList | null) {
    if (!next) return
    const additions = Array.from(next)
    if (files.length + additions.length > 10) { setError(isInstruction ? 'В одной версии допустимо до 10 файлов' : 'В одной редакции допустимо до 10 файлов'); return }
    if (additions.some(file => file.size === 0 || file.size > 10 * 1024 * 1024)) { setError('Каждый файл должен быть непустым и не больше 10 МБ'); return }
    if (additions.some(file => !/\.(pdf|docx|doc|xlsx)$/i.test(file.name))) { setError('Поддерживаются PDF, DOCX, DOC и XLSX'); return }
    if ([...files.map(item => item.file), ...additions].reduce((sum, file) => sum + file.size, 0) > 40 * 1024 * 1024) { setError('Общий размер файлов не должен превышать 40 МБ'); return }
    setFiles(previous => [...previous, ...additions.map((file, index) => ({ file, purpose: (previous.length === 0 && index === 0 ? 'main' : 'attachment') as DocumentUploadFile['purpose'], source_metadata: {} }))])
    // The main file's name is usually the document's name; keep any typed title.
    if (mode !== 'edition' && !files.length && additions.length && !properties.title.trim()) setProperties(value => ({ ...value, title: additions[0].name.replace(/\.[^.]+$/, '').replace(/[_\s]+/g, ' ').trim().slice(0, 300) }))
    edited()
    if (input.current) input.current.value = ''
  }
  async function save(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    if (!isMetadata && (!files.length || files.filter(item => item.purpose === 'main').length !== 1)) { setError(isInstruction ? 'Добавьте файлы и назначьте один из них основным текстом версии' : 'Добавьте файлы и назначьте один из них основным текстом редакции'); return }
    if (properties.applicability.some(item => item.status === 'confirmed' && !item.evidence.trim())) { setError('Для подтверждённой применимости укажите основание'); return }
    if (isInstruction && properties.new_position && (!properties.new_position.display_name.trim() || !properties.new_position.unit_name.trim())) { setError('Укажите название новой должности и её службу или группу'); return }
    const controller = new AbortController()
    request.current = controller
    setBusy(true); onBusy(true); setError('')
    try {
      const result = isMetadata && current ? await updateDocument(current.id, { ...properties, title: properties.title.trim() }, controller.signal)
        : mode === 'edition' && current ? await uploadDocumentEdition(current.id, files, label, uploadId.current, controller.signal)
        : await uploadDocument({ ...properties, title: properties.title.trim(), ...(ownerLocked ? { owner_department_id: departmentId! } : {}) }, scope, departmentId, files, label, uploadId.current, controller.signal)
      if (!controller.signal.aborted) { onSaved(result); toast.success(isMetadata ? 'Реквизиты сохранены' : mode === 'edition' ? isInstruction ? 'Версия сохранена' : 'Редакция сохранена' : 'Документ сохранён') }
    } catch (cause) {
      if (!controller.signal.aborted) { setError(message(cause)); if (cause instanceof KnowledgeRequestError && cause.status === 409) onConflict() }
    } finally { if (!controller.signal.aborted) { setBusy(false); onBusy(false) } }
  }
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}><DialogContent className={`max-h-[90vh] overflow-y-auto p-7 ${mode === 'new' ? 'sm:max-w-5xl' : isMetadata ? 'sm:max-w-2xl' : 'sm:max-w-3xl'}`} showCloseButton={!busy} onEscapeKeyDown={event => { if (busy) event.preventDefault() }} onPointerDownOutside={event => { if (busy) event.preventDefault() }}>
    <DialogHeader className="pr-8 text-left"><DialogTitle className="text-xl">{mode === 'new' ? 'Добавить документ' : isMetadata ? 'Реквизиты документа' : isInstruction ? 'Добавить версию' : 'Добавить редакцию'}</DialogTitle><DialogDescription className="break-words leading-6">{current?.title || (scope === 'bank' ? 'Общебанковские документы' : departments.find(item => item.id === departmentId)?.name)}{mode === 'edition' && (isInstruction ? ' · загрузите полный комплект новой версии' : ' · загрузите полный комплект новой редакции')}</DialogDescription></DialogHeader>
    <form onSubmit={event => void save(event)} aria-busy={busy} className="min-w-0">
      <div className={mode === 'new' ? 'mt-2 grid min-w-0 grid-cols-[1fr_1fr] gap-8' : 'mt-2'}>
        {mode !== 'edition' && <div className="min-w-0"><PropertiesFields value={properties} onChange={value => { setProperties(value); edited() }} types={types} departments={departments} roles={roles} positionsEnabled={!!roleDepartment} ownerLocked={ownerLocked} disabled={busy} />{rolesError && properties.document_type === 'job_instruction' && <p role="alert" className="mt-3 text-xs leading-5 text-destructive">Не удалось загрузить должности: {rolesError}. Документ можно принять без должности.</p>}</div>}
        {!isMetadata && <div className="min-w-0 space-y-5">
          <div><h3 className="text-sm font-medium">{isInstruction ? 'Файлы версии' : 'Файлы редакции'}</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">PDF, DOCX, DOC, XLSX · до 10 МБ на файл, до 40 МБ на комплект</p></div>
          <input ref={input} tabIndex={-1} className="sr-only" type="file" accept=".pdf,.docx,.doc,.xlsx" multiple disabled={busy} aria-label={isInstruction ? 'Добавить файлы версии' : 'Добавить файлы редакции'} onChange={event => addFiles(event.target.files)} />
          {!files.length ? <button type="button" disabled={busy} className="flex w-full flex-col items-center justify-center rounded-xl border border-dashed bg-muted/20 px-5 py-9 outline-none transition-colors hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring" onClick={() => input.current?.click()}><FilePlus2 className="size-7 text-muted-foreground" /><span className="mt-3 text-sm font-medium">Выбрать файлы</span><span className="mt-1 text-xs text-muted-foreground">Основной текст, приложения и основания</span></button> : <div className="space-y-3">{files.map((item, index) => <div key={index} className="rounded-lg border bg-card p-4">
            <div className="flex items-start gap-3"><FileText className="mt-0.5 size-4 shrink-0 text-muted-foreground" /><div className="min-w-0 flex-1"><p className="break-words text-sm font-medium leading-5">{item.file.name}</p><p className="mt-1 text-xs text-muted-foreground">{formatFileSize(item.file.size)}</p></div><Button type="button" variant="ghost" size="icon-sm" disabled={busy} aria-label={`Убрать файл ${item.file.name}`} onClick={() => { setFiles(previous => previous.filter((_, position) => position !== index)); edited() }}><Trash2 className="size-4" /></Button></div>
            <Select value={item.purpose} disabled={busy} onValueChange={purpose => { setFiles(previous => previous.map((entry, position) => position === index ? { ...entry, purpose: purpose as DocumentUploadFile['purpose'] } : entry)); edited() }}><SelectTrigger className="mt-3 w-full text-xs" aria-label={`Назначение файла ${item.file.name}`}><SelectValue /></SelectTrigger><SelectContent>{Object.entries(purposeLabels).map(([value, name]) => <SelectItem key={value} value={value}>{name}</SelectItem>)}</SelectContent></Select>
            <details className="mt-3"><summary className="cursor-pointer text-xs text-muted-foreground">Источник файла</summary><Input className="mt-2 text-xs" aria-label={`Источник файла ${item.file.name}`} disabled={busy} value={textValue(item.source_metadata, 'source_reference')} placeholder="Название архива, письмо или источник" onChange={event => { setFiles(previous => previous.map((entry, position) => position === index ? { ...entry, source_metadata: { ...entry.source_metadata, source_reference: event.target.value } } : entry)); edited() }} /></details>
          </div>)}<Button type="button" className="w-full" variant="outline" disabled={busy || files.length === 10} onClick={() => input.current?.click()}><Plus className="size-4" />Добавить файлы</Button></div>}
          <label className="block text-sm font-medium">{isInstruction ? 'Обозначение версии' : 'Обозначение редакции'} <span className="font-normal text-muted-foreground">· необязательно</span><Input className="mt-2" disabled={busy} value={label} maxLength={200} placeholder={isInstruction ? 'Например, версия по приказу №…' : 'Например, редакция по приказу №…'} onChange={event => { setLabel(event.target.value); edited() }} /></label>
          <div className="rounded-lg bg-muted/40 p-4 text-xs leading-5 text-muted-foreground">{isInstruction ? 'Версия' : 'Редакция'} поступит на проверку. Дата принятия, даты действия и основание подтверждаются отдельно по документам. Загрузка файла не означает утверждение.</div>
        </div>}
      </div>
      {error && <p role="alert" className="mt-5 rounded-lg border border-destructive/30 p-3 text-sm leading-6 text-destructive">{error}</p>}
      <div className="mt-6 flex items-center justify-end gap-3 border-t pt-5"><Button type="button" variant="outline" disabled={busy} onClick={onClose}>Отмена</Button><Button type="submit" disabled={busy || (!isMetadata && !files.length) || (mode !== 'edition' && !properties.title.trim())}>{busy ? <Spinner className="size-4" /> : <Upload className="size-4" />}{busy ? 'Сохраняем…' : isMetadata ? 'Сохранить реквизиты' : mode === 'edition' ? isInstruction ? 'Сохранить версию' : 'Сохранить редакцию' : 'Добавить документ'}</Button></div>
    </form>
  </DialogContent></Dialog>
}

function dayBefore(value: string) {
  const day = new Date(`${value}T00:00:00Z`)
  day.setUTCDate(day.getUTCDate() - 1)
  return day.toISOString().slice(0, 10)
}
const formatDay = (value: string) => value.split('-').reverse().join('.')

export function EditionVerificationDialog({ document, edition, endValidity = false, onClose, onSaved, onBusy }: { document: DocumentDetail; edition: DocumentEdition; endValidity?: boolean; onClose: () => void; onSaved: () => void; onBusy: (busy: boolean) => void }) {
  const isInstruction = document.document_type === 'job_instruction'
  const [approvedOn, setApprovedOn] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [evidence, setEvidence] = useState('')
  const [closePrevious, setClosePrevious] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => request.current?.abort(), [])
  // The confirmed open period that this edition's start would overlap: the new
  // edition replaces it, so its last day is the day before the new start.
  const previous = !endValidity && from ? document.editions.find(item => item.id !== edition.id && item.verification_status === 'verified' && !item.ended_at && item.valid_from && item.valid_from < from && (!item.valid_to || item.valid_to >= from)) : undefined
  const previousEnd = previous ? dayBefore(from) : ''
  async function save(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    if ((!endValidity && !from) || (endValidity && !to) || (to && to < (endValidity ? edition.valid_from || '' : from)) || !evidence.trim()) { setError('Укажите проверенную дату, корректный период и основание'); return }
    const controller = new AbortController(); request.current = controller
    setBusy(true); onBusy(true); setError('')
    try { await (endValidity ? endDocumentEditionValidity(document.id, edition.id, to, evidence.trim(), controller.signal) : verifyDocumentEdition(document.id, edition.id, from, to, evidence.trim(), controller.signal, approvedOn, Boolean(previous) && closePrevious)); if (!controller.signal.aborted) { toast.success(endValidity ? isInstruction ? 'Окончание действия версии зафиксировано' : 'Окончание действия редакции зафиксировано' : isInstruction ? 'Версия подтверждена по источнику' : 'Редакция подтверждена по источнику'); onSaved() } }
    catch (cause) { if (!controller.signal.aborted) setError(message(cause)) }
    finally { if (!controller.signal.aborted) { setBusy(false); onBusy(false) } }
  }
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}>
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl" showCloseButton={!busy} onEscapeKeyDown={event => { if (busy) event.preventDefault() }} onPointerDownOutside={event => { if (busy) event.preventDefault() }}>
      <DialogHeader><DialogTitle>{endValidity ? 'Завершить период действия' : isInstruction ? 'Подтвердить версию' : 'Подтвердить редакцию'}</DialogTitle><DialogDescription className="break-words leading-6">{document.title} · {edition.label || `${isInstruction ? 'Версия' : 'Редакция'} ${edition.number}`}</DialogDescription></DialogHeader>
      <form className="space-y-5" onSubmit={event => void save(event)}>
        <p className="text-sm leading-6 text-muted-foreground">{endValidity ? `Зафиксируйте проверенный последний день действия ${isInstruction ? 'версии' : 'редакции'} и основание её замены. Первоначальное подтверждение и новый факт окончания сохранятся в истории.` : 'Проверьте основной текст, приложения и основание утверждения. Подтверждение в Hub фиксирует проверку источников и не создаёт банковский акт утверждения.'}</p>
        {!endValidity && <label className="block text-sm font-medium">Дата принятия / утверждения <span className="font-normal text-muted-foreground">· если подтверждена источником</span><Input className="mt-2" type="date" disabled={busy} value={approvedOn} onChange={event => setApprovedOn(event.target.value)} /><span className="mt-2 block text-xs font-normal leading-5 text-muted-foreground">Дата из банковского акта или оригинала. Не подставляйте дату загрузки либо проверки в Process Hub.</span></label>}
        <div className={endValidity ? '' : 'grid grid-cols-2 gap-4'}>
          {!endValidity && <label className="text-sm font-medium">Начало действия<Input className="mt-2" type="date" required disabled={busy} value={from} onChange={event => setFrom(event.target.value)} /></label>}
          <label className="text-sm font-medium">Окончание действия {!endValidity && <span className="font-normal text-muted-foreground">· если указано</span>}<Input className="mt-2" type="date" required={endValidity} min={(endValidity ? edition.valid_from : from) || undefined} disabled={busy} value={to} onChange={event => setTo(event.target.value)} />{endValidity && <span className="mt-2 block text-xs font-normal leading-5 text-muted-foreground">Последний день действия по подтверждённому источнику.</span>}</label>
        </div>
        <label className="block text-sm font-medium">Основание проверки<Textarea className="mt-2 min-h-28" required maxLength={2000} disabled={busy} value={evidence} onChange={event => setEvidence(event.target.value)} placeholder={endValidity ? `Реквизиты акта или новой ${isInstruction ? 'версии' : 'редакции'}, подтверждающие окончание действия` : `Реквизиты акта, файл и пункты, подтверждающие ${isInstruction ? 'версию' : 'редакцию'} и указанные даты`} /></label>
        <p className="text-xs leading-5 text-muted-foreground">{endValidity ? `Дата окончания фиксируется один раз. Следующая подтверждённая ${isInstruction ? 'версия' : 'редакция'} может действовать со следующего дня.` : `Подтверждение сохраняется в истории. Период не должен пересекаться с другими подтверждёнными ${isInstruction ? 'версиями' : 'редакциями'} документа.`}</p>
        {previous && <label className="flex items-start gap-3 rounded-lg border bg-muted/30 p-3 text-sm leading-6"><input type="checkbox" className="mt-1 size-4 shrink-0 accent-primary" disabled={busy} checked={closePrevious} onChange={event => setClosePrevious(event.target.checked)} /><span>Завершить {previous.label ? `«${previous.label}»` : `${isInstruction ? 'версию' : 'редакцию'} ${previous.number}`}: последний день действия — {formatDay(previousEnd)}<span className="block text-xs text-muted-foreground">Сейчас она действует без даты окончания. Окончание запишется отдельным фактом с этим же основанием.</span></span></label>}
        {error && <p role="alert" className="text-sm leading-6 text-destructive">{error}</p>}
        <div className="flex justify-end gap-3 border-t pt-4"><Button type="button" variant="outline" disabled={busy} onClick={onClose}>Отмена</Button><Button type="submit" disabled={busy || !(endValidity ? to : from) || !evidence.trim()}>{busy ? <Spinner className="size-4" /> : <CheckCircle2 className="size-4" />}{busy ? 'Сохраняем…' : endValidity ? 'Зафиксировать окончание' : 'Подтвердить'}</Button></div>
      </form>
    </DialogContent>
  </Dialog>
}
export function DocumentLifecycleDialog({ document, action, onClose, onSaved, onBusy }: { document: RegistryDocument; action: 'delete' | 'restore'; onClose: () => void; onSaved: () => void; onBusy: (busy: boolean) => void }) {
  const versionsLabel = document.document_type === 'job_instruction' ? 'версии' : 'редакции'
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => request.current?.abort(), [])
  async function save(event: FormEvent) {
    event.preventDefault()
    if (busy || !reason.trim()) return
    const controller = new AbortController(); request.current = controller
    setBusy(true); onBusy(true); setError('')
    try { await changeDocumentLifecycle(document.id, action, reason.trim(), controller.signal); if (!controller.signal.aborted) { toast.success(action === 'delete' ? 'Документ перемещён в архив' : 'Документ восстановлен'); onSaved() } }
    catch (cause) { if (!controller.signal.aborted) setError(message(cause)) }
    finally { if (!controller.signal.aborted) { setBusy(false); onBusy(false) } }
  }
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}><DialogContent className="sm:max-w-lg" showCloseButton={!busy} onEscapeKeyDown={event => { if (busy) event.preventDefault() }} onPointerDownOutside={event => { if (busy) event.preventDefault() }}><DialogHeader><DialogTitle>{action === 'delete' ? 'Переместить в архив' : 'Восстановить документ'}</DialogTitle><DialogDescription className="break-words leading-6">{document.title}</DialogDescription></DialogHeader><form onSubmit={event => void save(event)} className="space-y-4"><p className="text-sm leading-6 text-muted-foreground">{action === 'delete' ? `Документ будет перемещён из активной библиотеки в архив. Его ${versionsLabel}, файлы и история сохранятся для восстановления.` : `Документ вернётся в библиотеку со всеми ${document.document_type === 'job_instruction' ? 'версиями' : 'редакциями'} и файлами.`}</p><label className="block text-sm font-medium">Причина<Textarea required maxLength={2000} className="mt-2" value={reason} disabled={busy} onChange={event => setReason(event.target.value)} /></label>{error && <p role="alert" className="text-sm text-destructive">{error}</p>}<div className="flex justify-end gap-3 border-t pt-4"><Button type="button" variant="outline" disabled={busy} onClick={onClose}>Отмена</Button><Button type="submit" variant="default" disabled={busy || !reason.trim()}>{busy && <Spinner className="size-4" />}{action === 'delete' ? 'В архив' : 'Восстановить'}</Button></div></form></DialogContent></Dialog>
}
