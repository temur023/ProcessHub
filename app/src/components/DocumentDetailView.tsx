import { useEffect, useRef, useState } from 'react'
import { Archive, ArrowLeft, CheckCircle2, Download, ExternalLink, FileText, History, Info, Pencil, RotateCcw, ShieldCheck, Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { formatFileSize, plural } from '@/lib/presentation'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { DocumentOriginalView } from '@/components/DocumentOriginalView'
import { InstructionAnalysis } from '@/components/InstructionAnalysis'
import { ComparisonReviewView } from '@/components/ComparisonReviewView'
import { getComparisonReview } from '@/lib/comparison-review'
import { DocumentEditor, DocumentLifecycleDialog, EditionVerificationDialog } from '@/components/DocumentDialogs'
import type { Department } from '@/lib/knowledge'
import { getDocument, downloadDocumentFile, type DocumentDetail, type DocumentEdition, type DocumentFile, type DocumentType } from '@/lib/documents'

const date = (value: string | null | undefined) => value ? /^\d{4}-\d{2}-\d{2}$/.test(value) ? value.split('-').reverse().join('.') : new Date(value).toLocaleDateString('ru-RU') : 'Не указано'
const purpose = { main: 'Основной текст', attachment: 'Приложение', approval: 'Подтверждение утверждения', supplement: 'Сопроводительный материал' }
const message = (cause: unknown) => cause instanceof Error ? cause.message : 'Не удалось открыть документ'
const metadataText = (metadata: Record<string, unknown>, key: string) => typeof metadata[key] === 'string' ? metadata[key] as string : ''
function SourceMetadata({ metadata }: { metadata: Record<string, unknown> }) {
  const name = metadataText(metadata, 'source_name')
  const reference = metadataText(metadata, 'source_reference')
  const url = metadataText(metadata, 'source_url')
  const safeUrl = /^https?:\/\//i.test(url)
  return <div className="space-y-2 text-sm leading-6">{name && <p>{name}</p>}{reference && <p className="break-words text-muted-foreground">{reference}</p>}{url && (safeUrl ? <a href={url} target="_blank" rel="noopener noreferrer" className="inline-flex max-w-full items-start gap-1.5 break-all text-primary hover:underline">{url}<ExternalLink className="mt-1 size-3.5 shrink-0" /></a> : <p className="break-all text-muted-foreground">{url}</p>)}{!name && !reference && !url && <p className="text-muted-foreground">Источник ещё не указан</p>}</div>
}

function EditionDates({ edition, validity, technical = false }: { edition: DocumentEdition; validity: boolean; technical?: boolean }) {
  const confirmed = (value: string | null | undefined) => edition.verification_status === 'verified' ? date(value) : 'Не подтверждено'
  const entries = [
    ...(validity ? [
      ['Дата принятия / утверждения', confirmed(edition.approved_on)],
      ['Начало действия', confirmed(edition.valid_from)],
      ['Окончание действия', confirmed(edition.valid_to)],
    ] : []),
    ...(technical ? [
      ['Дата загрузки в Process Hub', date(edition.uploaded_at)],
      ...(validity ? [['Дата проверки в Process Hub', confirmed(edition.verified_at)]] : []),
    ] : []),
  ]
  return <dl className="grid grid-cols-3 gap-x-6 gap-y-4">{entries.map(([label, value]) => <div key={label} className="min-w-0"><dt className="text-xs leading-5 text-muted-foreground">{label}</dt><dd className="mt-1 text-sm leading-6">{value}</dd></div>)}</dl>
}

function FileDownload({ file, enabled }: { file: DocumentFile; enabled: boolean }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => request.current?.abort(), [])
  async function download() {
    if (busy) return
    const controller = new AbortController(); request.current = controller
    setBusy(true); setError('')
    try { await downloadDocumentFile(file, controller.signal) }
    catch (cause) { if (!controller.signal.aborted) setError(message(cause)) }
    finally { if (!controller.signal.aborted) setBusy(false) }
  }
  return <div className="shrink-0"><Button variant="outline" size="sm" disabled={!enabled || busy} title={!enabled ? 'Право скачивания не назначено' : undefined} aria-label={`Скачать оригинал ${file.filename}`} onClick={() => void download()}>{busy ? <Spinner className="size-4" /> : <Download className="size-4" />}Скачать оригинал</Button>{error && <p role="alert" className="mt-2 max-w-64 text-xs leading-5 text-destructive">{error}</p>}</div>
}

interface DocumentDetailViewProps {
  id: string
  editionId: string
  fileId?: string
  onFile?: (fileId: string) => void
  onEdition: (id: string) => void
  onBack: () => void
  onChanged: (selectedId?: string, selectedEditionId?: string) => void
  onBusy: (busy: boolean) => void
  departments: Department[]
  types: DocumentType[]
  isAdmin: boolean
  embedded?: boolean
  initialSection?: 'review' | 'history'
  backLabel?: string
}
export function DocumentDetailView({ id, editionId, fileId: initialFileId, onFile, onEdition, onBack, onChanged, onBusy, departments, types, isAdmin, embedded = false, initialSection, backLabel = 'Назад к документам' }: DocumentDetailViewProps) {
  const [document, setDocument] = useState<DocumentDetail>()
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [tab, setTab] = useState<'auto' | 'original' | 'history' | 'info' | 'review'>(initialSection || 'auto')
  const [fileId, setFileId] = useState(initialFileId || '')
  const [editor, setEditor] = useState<'edition' | 'metadata'>()
  const [verification, setVerification] = useState<{ edition: DocumentEdition; endValidity?: boolean }>()
  const [lifecycle, setLifecycle] = useState<'delete' | 'restore'>()
  const [busy, setBusy] = useState(false)
  const busyCallback = useRef(onBusy)
  useEffect(() => { busyCallback.current = onBusy }, [onBusy])
  useEffect(() => () => busyCallback.current(false), [])
  const setSaving = (value: boolean) => { setBusy(value); onBusy(value) }
  useEffect(() => {
    const controller = new AbortController()
    void getDocument(id, controller.signal).then(data => { if (!controller.signal.aborted) { setDocument(data); setError('') } }).catch(cause => { if (!controller.signal.aborted) setError(message(cause)) })
    return () => controller.abort()
  }, [id, retry])
  const allowed = (capability: string) => isAdmin || !!document?.capabilities.includes(capability)
  const edition = document?.editions.find(item => item.id === editionId) ?? document?.editions.find(item => item.is_current) ?? document?.editions[0]
  const file = edition?.files.find(item => item.id === fileId) ?? edition?.files.find(item => item.purpose === 'main') ?? edition?.files[0]
  const isInstruction = document?.document_type === 'job_instruction'
  const isAnalytics = document?.document_type === 'analytics'
  const review = isAnalytics && edition ? getComparisonReview(edition.metadata) : null
  const activeTab = tab === 'auto' ? review ? 'review' : 'original' : tab === 'review' && !review && !isInstruction ? 'original' : tab
  const isWorkingMaterial = ['analytics', 'sop', 'correspondence'].includes(document?.document_type || '')
  const type = types.find(item => item.id === document?.document_type)?.label || 'Документ'
  const editionName = (item: DocumentEdition) => `Версия ${item.number}${item.label ? ` · ${item.label}` : ''}`
  const editionStatus = isWorkingMaterial ? 'Рабочий материал' : document?.document_type === 'other' ? 'На классификации' : edition?.is_current ? 'Действующая подтверждённая' : edition?.verification_status === 'verified' ? 'Подтверждённая версия' : 'На проверке'
  const tabs = isInstruction
    ? [{ id: 'original' as const, label: 'Оригинал', icon: FileText }, { id: 'history' as const, label: 'Версии', icon: History }, { id: 'review' as const, label: 'Анализ и расхождения', icon: Info }, { id: 'info' as const, label: 'Сведения', icon: Info }]
    : [...(review ? [{ id: 'review' as const, label: 'Анализ и расхождения', icon: Info }] : []), { id: 'original' as const, label: 'Оригинал', icon: FileText }, { id: 'history' as const, label: 'Версии', icon: History }, { id: 'info' as const, label: 'Сведения', icon: Info }]
  return <div className="min-w-0">
    {!embedded && <Button variant="ghost" size="sm" className="-ml-2" disabled={busy} onClick={onBack}><ArrowLeft className="size-4" />{backLabel}</Button>}
    {error ? <div role="alert" className="mt-6 space-y-3 rounded-lg border p-5 text-sm"><p>{error}</p><Button variant="outline" size="sm" onClick={() => setRetry(value => value + 1)}>Повторить</Button></div> : !document ? <div role="status" className="flex items-center gap-2 py-10 text-sm text-muted-foreground"><Spinner className="size-4" />Открываем документ…</div> : <>
      <header className={`${embedded ? '' : 'mt-5 '}border-b pb-6`}><div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between sm:gap-6"><div className="min-w-0 flex-1"><p className="text-xs font-medium text-muted-foreground">{type}{document.library_scope === 'bank' && ' · общебанковский документ'}{document.deleted_at && ' · в архиве'}</p>{embedded ? <h2 className="mt-2 break-words text-lg font-semibold leading-7 tracking-tight">{document.title}</h2> : <h1 className="mt-3 break-words text-2xl font-semibold leading-9 tracking-tight">{document.title}</h1>}<div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-muted-foreground">{document.bank_number && <span>№ {document.bank_number}</span>}<span>Дата документа: {date(document.document_date)}</span>{!embedded && document.role_name && <span className="break-words">{document.role_name}</span>}</div><div aria-label="Открытая версия документа" className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">{edition ? <><span className="rounded-md bg-muted px-2 py-1 font-medium" title={editionName(edition)}>Версия {edition.number}</span><span className={`inline-flex items-center gap-1.5 ${edition.is_current ? 'text-emerald-700 dark:text-emerald-400' : 'text-muted-foreground'}`}>{edition.verification_status === 'verified' && <CheckCircle2 className="size-3.5" />}{editionStatus}</span>{isWorkingMaterial && <span className="text-muted-foreground">{metadataText(document.metadata, 'analysis_period') || 'Период данных не указан'}</span>}</> : <span className="text-muted-foreground">Версии ещё не добавлены</span>}</div></div><div className="flex shrink-0 items-center gap-2">{!document.deleted_at && allowed('versions.create') && <Button disabled={busy} onClick={() => setEditor('edition')}><Upload className="size-4" />Добавить версию</Button>}{document.deleted_at && allowed('instructions.restore') && <Button disabled={busy} onClick={() => setLifecycle('restore')}><RotateCcw className="size-4" />Восстановить</Button>}{!document.deleted_at && allowed('instructions.delete') && <Button variant="ghost" size="icon" disabled={busy} aria-label="Переместить документ в архив" title="Переместить в архив" onClick={() => setLifecycle('delete')}><Archive className="size-4" /></Button>}</div></div></header>
      {document.deleted_at && <p className="mt-5 rounded-lg bg-muted/50 p-4 text-sm leading-6 text-muted-foreground">Документ перемещён в архив {date(document.deleted_at)}. История и оригиналы сохранены.</p>}
      <nav aria-label="Разделы документа" className="mt-6 flex gap-7 border-b">{tabs.map(item => <button key={item.id} type="button" disabled={busy} onClick={() => setTab(item.id)} aria-current={activeTab === item.id ? 'page' : undefined} className={`-mb-px inline-flex items-center gap-2 border-b-2 px-1 pb-3 text-sm font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring ${activeTab === item.id ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}><item.icon className="size-4" />{item.label}{item.id === 'history' && <span className="text-xs">{document.edition_count}</span>}</button>)}</nav>
      <div className="pt-6">
        {activeTab === 'original' && (edition && file ? <><div className="mb-6 flex flex-col gap-3 rounded-lg border bg-card p-4 sm:flex-row sm:items-start sm:justify-between sm:gap-5"><div className="min-w-0 flex-1"><p className="text-xs text-muted-foreground">{purpose[file.purpose]}</p>{edition.files.length > 1 ? <Select value={file.id} onValueChange={id => { setFileId(id); onFile?.(id) }}><SelectTrigger className="mt-2 w-full max-w-xl" aria-label="Файл для просмотра"><SelectValue className="min-w-0 flex-1"><span className="min-w-0 flex-1 truncate text-left" title={file.filename}>{purpose[file.purpose]} · {file.filename}</span></SelectValue></SelectTrigger><SelectContent>{edition.files.map(item => <SelectItem key={item.id} value={item.id}>{purpose[item.purpose]} · {item.filename}</SelectItem>)}</SelectContent></Select> : <p className="mt-1 break-words text-sm font-medium leading-6">{file.filename}</p>}</div><FileDownload key={file.id} file={file} enabled={allowed('documents.download')} /></div><DocumentOriginalView key={file.id} file={file} enabled={!document.deleted_at && allowed('documents.download')} /></> : <p className="py-10 text-sm text-muted-foreground">{isInstruction ? 'Добавьте версию с основным файлом для просмотра инструкции.' : 'Добавьте версию с основным файлом для просмотра документа.'}</p>)}
        {activeTab === 'history' && <section aria-label={isInstruction ? 'История версий' : 'История версий'}>
          <div className="mb-5"><h2 className="text-base font-semibold">{isInstruction ? 'Версии инструкции' : 'Версии документа'}</h2><p className="mt-1 text-sm leading-6 text-muted-foreground">{isWorkingMaterial ? 'История комплектов рабочего материала. Данные используются с учётом указанного периода и источника.' : 'Дата принятия, период действия и загрузка хранятся отдельно. Действующая версия определяется по подтверждённому периоду.'}</p></div>
          <ol className="space-y-4">{document.editions.map(item => <li key={item.id} className={`rounded-xl border bg-card p-5 ${item.id === edition?.id ? 'border-primary/40' : ''}`}>
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0"><h3 className="break-words text-sm font-semibold">{editionName(item)}</h3><p className="mt-2 text-xs text-muted-foreground">{plural(item.files.length, 'файл', 'файла', 'файлов')}{item.is_current && ' · действует сейчас'}</p></div>
              <div className="flex shrink-0 gap-2"><Button variant="outline" size="sm" disabled={busy} onClick={() => { onEdition(item.id); setTab('original'); setFileId('') }}>Открыть</Button>{allowed('editions.verify') && !isAnalytics && !document.deleted_at && item.verification_status === 'unverified' && <Button size="sm" disabled={busy} onClick={() => setVerification({ edition: item })}><ShieldCheck className="size-4" />Подтвердить</Button>}{allowed('editions.verify') && !isAnalytics && !document.deleted_at && item.verification_status === 'verified' && !item.valid_to && <Button variant="outline" size="sm" disabled={busy} onClick={() => setVerification({ edition: item, endValidity: true })}>Завершить действие</Button>}</div>
            </div>
            <div className="mt-5"><EditionDates edition={item} validity={!isWorkingMaterial} technical /></div>
            {item.verification_status === 'verified' ? <div className="mt-4 border-t pt-3"><p className="text-xs font-medium text-muted-foreground">Основание подтверждения</p><p className="mt-1 whitespace-pre-wrap break-words text-sm leading-6">{item.verification_evidence}</p>{item.end_validity_evidence && <div className="mt-3 border-t pt-3"><p className="text-xs font-medium text-muted-foreground">Основание окончания действия</p><p className="mt-1 whitespace-pre-wrap break-words text-sm leading-6">{item.end_validity_evidence}</p><p className="mt-2 text-xs text-muted-foreground">Дата фиксации окончания в Process Hub: {date(item.ended_at)}</p></div>}</div> : <p className="mt-3 text-xs leading-5 text-muted-foreground">{isWorkingMaterial ? 'Период и методику данных проверяют по источникам.' : 'Даты и комплект требуют проверки по источникам.'}</p>}
          </li>)}</ol>
          {!document.editions.length && <p className="py-10 text-sm text-muted-foreground">История начнётся после добавления первого комплекта.</p>}
        </section>}
        {activeTab === 'review' && isInstruction && <InstructionAnalysis document={document} edition={edition} isAdmin={isAdmin} />}
        {activeTab === 'review' && review && <ComparisonReviewView review={review} departmentId={document.department_id || ''} />}
        {activeTab === 'info' && edition && <details className="mb-6 rounded-xl border p-5"><summary className="cursor-pointer text-sm font-medium">Файлы выбранной версии · {edition.files.length}</summary><ul className="mt-4 divide-y">{edition.files.map(item => <li key={item.id} className="py-4"><div className="flex items-start justify-between gap-5"><div className="min-w-0"><p className="break-words text-sm font-medium">{item.filename}</p><p className="mt-2 text-xs text-muted-foreground">{purpose[item.purpose]} · {formatFileSize(item.byte_size)}</p></div><div className="flex shrink-0 items-center gap-2"><Button variant="ghost" size="sm" onClick={() => { setFileId(item.id); onFile?.(item.id); setTab('original') }}>Просмотреть</Button><FileDownload file={item} enabled={allowed('documents.download')} /></div></div><div className="mt-3 text-xs"><SourceMetadata metadata={item.source_metadata} /><p className="mt-2 break-all font-mono text-muted-foreground">SHA-256: {item.content_hash}</p></div></li>)}</ul></details>}
        {activeTab === 'info' && <section aria-label="Сведения о документе" className="grid grid-cols-2 gap-x-10 gap-y-8">
          <div>
            <div className="flex items-center justify-between gap-3"><h2 className="text-base font-semibold">Реквизиты и происхождение</h2>{!document.deleted_at && allowed('instructions.create') && <Button variant="ghost" size="sm" disabled={busy} onClick={() => setEditor('metadata')}><Pencil className="size-3.5" />Изменить</Button>}</div>
            <dl className="mt-4 grid grid-cols-[140px_1fr] gap-x-5 gap-y-4 text-sm leading-6">
              <dt className="text-muted-foreground">Вид</dt><dd>{type}</dd>
              <dt className="text-muted-foreground">Владелец</dt><dd className="break-words">{document.owner_department_name || 'Не установлен'}</dd>
              <dt className="text-muted-foreground">Область хранения</dt><dd>{document.library_scope === 'bank' ? 'Общебанковские документы' : departments.find(item => item.id === document.department_id)?.name || 'Департамент'}</dd>
              <dt className="text-muted-foreground">Банковский номер</dt><dd>{document.bank_number || 'Не указан'}</dd>
              <dt className="text-muted-foreground">Дата документа</dt><dd>{date(document.document_date)}</dd>
              {isAnalytics && <><dt className="text-muted-foreground">Период данных</dt><dd>{metadataText(document.metadata, 'analysis_period') || 'Не указан'}</dd><dt className="text-muted-foreground">Методика данных</dt><dd className="whitespace-pre-wrap break-words">{metadataText(document.metadata, 'analysis_method') || 'Не указана'}</dd></>}
              {document.role_name && <><dt className="text-muted-foreground">Должность</dt><dd className="break-words">{document.role_name}</dd></>}
              {metadataText(document.metadata, 'employee_in_filename') && <><dt className="text-muted-foreground">Сотрудник</dt><dd className="break-words">{metadataText(document.metadata, 'employee_in_filename')}</dd></>}
              {metadataText(document.metadata, 'registry_check') && <><dt className="text-muted-foreground">Сверка с кадровым реестром</dt><dd className={`whitespace-pre-wrap break-words ${/повышен|переведен|переведён|нет в|не найден|нужна новая|нужно решение/i.test(metadataText(document.metadata, 'registry_check')) ? 'text-amber-700 dark:text-amber-400' : ''}`}>{metadataText(document.metadata, 'registry_check')}</dd></>}
            </dl>
            <div className="mt-5 border-t pt-4"><h3 className="mb-2 text-sm font-medium">Источник документа</h3><SourceMetadata metadata={document.metadata} /></div>
            {metadataText(document.metadata, 'description') && <p className="mt-4 whitespace-pre-wrap break-words text-sm leading-6 text-muted-foreground">{metadataText(document.metadata, 'description')}</p>}
            {document.document_type === 'department_regulation' && !metadataText(document.metadata, 'source_reference') && !metadataText(document.metadata, 'source_url') && <p className="mt-4 rounded-lg bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">Связь с подтверждённым полным внешним источником ещё не указана. Для использования в сверке нужны версия, происхождение и проверенный состав источника.</p>}
          </div>
          <div><h2 className="text-base font-semibold">Применимость</h2><p className="mt-2 text-xs leading-5 text-muted-foreground">Владелец, область действия и право чтения учитываются отдельно.</p>{document.applicability.length ? <ul className="mt-4 divide-y rounded-lg border">{document.applicability.map(item => <li key={item.department_id} className="p-4"><p className="break-words text-sm font-medium leading-6">{item.department_name || departments.find(department => department.id === item.department_id)?.name || 'Департамент'}</p><p className={`mt-1 text-xs ${item.status === 'confirmed' ? 'text-emerald-700 dark:text-emerald-400' : 'text-muted-foreground'}`}>{item.status === 'confirmed' ? 'Подтверждена по источнику' : 'Требует подтверждения'}</p>{item.evidence && <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-muted-foreground">{item.evidence}</p>}</li>)}</ul> : <p className="mt-4 text-sm leading-6 text-muted-foreground">Связи применимости ещё не установлены.</p>}</div>
        </section>}
      </div>
      {editor && <DocumentEditor key={editor} mode={editor} document={document} types={types} departments={departments} departmentId={document.department_id || undefined} scope={document.library_scope} onClose={() => setEditor(undefined)} onBusy={setSaving} onConflict={() => setRetry(value => value + 1)} onSaved={result => { setEditor(undefined); onChanged(document.id, 'edition' in result ? result.edition.id : edition?.id) }} />}
      {verification && <EditionVerificationDialog document={document} edition={verification.edition} endValidity={verification.endValidity} onClose={() => setVerification(undefined)} onBusy={setSaving} onSaved={() => { setVerification(undefined); onChanged(document.id, verification.edition.id) }} />}
      {lifecycle && <DocumentLifecycleDialog document={document} action={lifecycle} onClose={() => setLifecycle(undefined)} onBusy={setSaving} onSaved={() => { setLifecycle(undefined); onChanged() }} />}
    </>}
  </div>
}
