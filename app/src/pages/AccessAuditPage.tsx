import { useEffect, useState } from 'react'
import { ChevronDown, Folder, History, RefreshCw, Search, Trash2, X } from 'lucide-react'
import { AccessError, AccessLoading, AccessModule } from '@/components/AccessModule'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useAuth } from '@/lib/auth'
import { getDepartments, type Department } from '@/lib/knowledge'
import { accessFailure, capabilityLabels, getRoleAudit, type AccessUser, type DocumentCapability, type RoleAuditRecord } from '@/lib/knowledgeAccess'

const actionLabels: Record<string, string> = {
  'access_role.created': 'Создана роль',
  'access_role.updated': 'Изменена роль',
  'access_role.scope.changed': 'Изменены папки роли',
  'access_role.member.assigned': 'Назначена роль',
  'access_role.member.revoked': 'Отозвана роль',
}
interface AuditData { records: RoleAuditRecord[]; departments: Department[]; foldersUnavailable: boolean }
const identity = (user: AccessUser | null, id: number) => user?.name || user?.email || `Сотрудник #${id}`
const roleName = (record: RoleAuditRecord) => record.details.name || record.role_name
const date = (value: string) => new Date(value).toLocaleDateString('ru-RU', { day: '2-digit', month: 'long', year: 'numeric' })
const time = (value: string) => new Date(value).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
const shortDate = (value: string) => new Date(value).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }).replace(',', '')

export default function AccessAuditPage() {
  const { user } = useAuth()
  const [data, setData] = useState<AuditData>()
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [query, setQuery] = useState('')
  const [action, setAction] = useState('all')

  useEffect(() => {
    if (!user.isAdmin) return
    const controller = new AbortController()
    void Promise.allSettled([getRoleAudit(controller.signal), getDepartments(controller.signal)])
      .then(([audit, departments]) => {
        if (controller.signal.aborted) return
        if (audit.status === 'rejected') { setError(accessFailure(audit.reason)); return }
        setData({ records: audit.value, departments: departments.status === 'fulfilled' ? departments.value : [], foldersUnavailable: departments.status === 'rejected' })
        setError('')
      })
    return () => controller.abort()
  }, [reload, user.isAdmin])

  function refresh() { setError(''); setData(undefined); setReload(value => value + 1) }
  function clearFilters() { setQuery(''); setAction('all') }
  const search = query.trim().toLocaleLowerCase('ru')
  const filtered = Boolean(search) || action !== 'all'
  const records = data?.records.filter(record => {
    const text = [actionLabels[record.action], record.action, roleName(record), record.details.previous_name,
      identity(record.actor, record.actor_id), record.actor?.email,
      record.user_id !== null ? identity(record.user, record.user_id) : '', record.user?.email, record.details.reason,
      ...(record.details.capabilities ?? []).map(item => capabilityLabels[item]),
      ...(record.details.previous_capabilities ?? []).map(item => capabilityLabels[item]),
    ].join(' ').toLocaleLowerCase('ru')
    return (action === 'all' || record.action === action) && text.includes(search)
  }) ?? []

  return <AccessModule activeSection="audit" title="Журнал изменений" description="Кто, когда и на каком основании изменял роли, функции, папки департаментов и назначения сотрудников."
    action={<Button variant="outline" disabled={!data && !error} onClick={refresh}><RefreshCw className="size-4" />Обновить</Button>}>
    {error ? <AccessError message={error} retry={refresh} />
      : !data ? <AccessLoading />
      : data.records.length === 0 ? <div className="py-20 text-center">
        <History className="mx-auto size-9 text-muted-foreground" /><h3 className="mt-4 text-lg font-semibold">Изменений пока нет</h3>
        <p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-muted-foreground">Здесь появятся записи после создания или изменения роли, выбора папок и назначения доступа сотрудникам.</p>
      </div>
      : <section className="mt-6" aria-label="История управления доступом">
        <div className="flex items-center gap-3">
          <div className="relative min-w-0 flex-1"><Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" /><Input aria-label="Поиск в журнале изменений" className="pl-9" value={query} onChange={event => setQuery(event.target.value)} placeholder="Роль, сотрудник, почта или основание" /></div>
          <Select value={action} onValueChange={setAction}><SelectTrigger className="w-64" aria-label="Фильтр по действию"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Все действия</SelectItem>{Object.entries(actionLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>
          {filtered && <Button variant="ghost" onClick={clearFilters}><X className="size-4" />Сбросить</Button>}
        </div>
        <p className="mt-3 text-xs leading-5 text-muted-foreground">Журнал хранит неизменяемые записи. Здесь доступны до 100 последних событий; поиск и фильтр работают в этом списке.{filtered && <span role="status"> Найдено: {records.length} из {data.records.length}.</span>}</p>
        {data.foldersUnavailable && <p className="mt-3 text-xs leading-5 text-muted-foreground">Названия папок сейчас недоступны. Записи журнала загружены, в подробностях сохранены идентификаторы папок.</p>}
        {records.length ? <>
          <div aria-hidden="true" className="mt-6 grid grid-cols-[136px_minmax(0,1.4fr)_minmax(0,1fr)_16px] gap-6 px-6 text-xs font-medium text-muted-foreground"><span>Когда</span><span>Действие и роль</span><span>Сотрудник</span><span /></div>
          <ol className="mt-3 divide-y rounded-xl border bg-card px-6">{records.map(record => <AuditEntry key={record.id} record={record} departments={data.departments} />)}</ol>
        </> : <div className="mt-6 rounded-xl border py-14 text-center"><h3 className="text-base font-semibold">Записи не найдены</h3><p className="mt-2 text-sm text-muted-foreground">Измените запрос или сбросьте выбранное действие.</p><Button variant="outline" className="mt-5" onClick={clearFilters}>Сбросить фильтры</Button></div>}
      </section>}
  </AccessModule>
}

function AuditEntry({ record, departments }: { record: RoleAuditRecord; departments: Department[] }) {
  const details = record.details
  const capabilitiesChanged = details.previous_capabilities !== undefined && details.capabilities !== undefined
    && (details.previous_capabilities.length !== details.capabilities.length || details.previous_capabilities.some(item => !details.capabilities?.includes(item)))
  const renamed = details.previous_name !== undefined && details.name !== undefined && details.previous_name !== details.name
  const folderChanges = Boolean(details.added?.length) || Boolean(details.removed?.length)

  const action = actionLabels[record.action] ?? 'Изменение доступа'
  return <li>
    <details className="group">
      <summary aria-label={`${action}: ${roleName(record)}, ${date(record.created_at)} ${time(record.created_at)}. Подробности`} className="grid cursor-pointer list-none grid-cols-[136px_minmax(0,1.4fr)_minmax(0,1fr)_16px] items-center gap-6 py-3 outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        <time dateTime={record.created_at} title={new Date(record.created_at).toLocaleString('ru-RU', { timeZoneName: 'short' })} className="text-xs tabular-nums text-muted-foreground">{shortDate(record.created_at)}</time>
        <p className="min-w-0 truncate text-sm" title={`${action} · ${roleName(record)}`}><span className="font-semibold">{action}</span><span className="text-muted-foreground"> · </span>{roleName(record)}</p>
        <p className="min-w-0 truncate text-sm" title={record.user?.email}>{record.user_id !== null ? <>{identity(record.user, record.user_id)}{record.user?.email && <span className="ml-2 text-xs text-muted-foreground">{record.user.email}</span>}</> : <span className="text-muted-foreground">—</span>}</p>
        <ChevronDown aria-hidden="true" className="size-4 text-muted-foreground transition-transform group-open:rotate-180" />
      </summary>
      <div className="mb-4 space-y-5 rounded-lg bg-muted/30 p-4">
        <dl className="grid grid-cols-2 gap-6">
          <div className="min-w-0"><dt className="text-xs text-muted-foreground">Кто изменил</dt><dd className="mt-1 text-sm leading-5 [overflow-wrap:anywhere]">{identity(record.actor, record.actor_id)}{record.actor?.email && <span className="mt-0.5 block break-all text-xs text-muted-foreground">{record.actor.email}</span>}</dd></div>
          <div className="min-w-0"><dt className="text-xs text-muted-foreground">Основание</dt><dd className="mt-1 text-sm leading-6 [overflow-wrap:anywhere]">{details.reason || <span className="text-muted-foreground">Не указано в записи</span>}</dd></div>        </dl>
        {renamed && <div><h4 className="text-sm font-medium">Название роли</h4><div className="mt-3 grid grid-cols-2 gap-6"><div><p className="text-xs text-muted-foreground">Было</p><p className="mt-1 text-sm leading-6 [overflow-wrap:anywhere]">{details.previous_name}</p></div><div><p className="text-xs text-muted-foreground">Стало</p><p className="mt-1 text-sm leading-6 [overflow-wrap:anywhere]">{details.name}</p></div></div></div>}
        {capabilitiesChanged ? <div><h4 className="text-sm font-medium">Функции роли</h4><div className="mt-3 grid grid-cols-2 gap-6"><CapabilitySnapshot title="Было" capabilities={details.previous_capabilities!} /><CapabilitySnapshot title="Стало" capabilities={details.capabilities!} /></div></div>
          : details.capabilities !== undefined && <CapabilitySnapshot title="Функции роли на момент события" capabilities={details.capabilities} />}
        {folderChanges ? <div className="grid grid-cols-2 gap-6">{Boolean(details.added?.length) && <FolderSnapshot title="Добавлены папки" ids={details.added!} departments={departments} />}{Boolean(details.removed?.length) && <FolderSnapshot title="Убраны папки" ids={details.removed!} departments={departments} />}</div>
          : details.department_ids !== undefined && <FolderSnapshot title="Папки роли на момент события" ids={details.department_ids} departments={departments} />}
        {(folderChanges || Boolean(details.department_ids?.length)) && <p className="text-xs leading-5 text-muted-foreground">Названия папок показаны по текущей структуре департаментов; состав взят из сохранённой записи.</p>}
      </div>
    </details>
  </li>
}

function CapabilitySnapshot({ title, capabilities }: { title: string; capabilities: readonly DocumentCapability[] }) {
  return <div><h4 className="text-xs font-medium text-muted-foreground">{title}</h4>{capabilities.length ? <ul className="mt-2 flex flex-wrap gap-2">{capabilities.map(item => <li key={item} className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs leading-5 ${item === 'instructions.delete' ? 'border-destructive/20 text-destructive' : 'text-foreground'}`}>{item === 'instructions.delete' && <Trash2 className="size-3.5 shrink-0" aria-hidden="true" />}{capabilityLabels[item] ?? item}</li>)}</ul> : <p className="mt-2 text-sm text-muted-foreground">Нет функций</p>}</div>
}

function FolderSnapshot({ title, ids, departments }: { title: string; ids: readonly string[]; departments: Department[] }) {
  return <div><h4 className="text-xs font-medium text-muted-foreground">{title}</h4>{ids.length ? <ul className="mt-2 space-y-2">{ids.map(id => {
    const department = departments.find(item => item.id === id)
    return <li key={id} className="flex min-w-0 items-start gap-2 text-sm leading-6"><Folder className="mt-1 size-4 shrink-0 text-muted-foreground" /><div className="min-w-0">{department ? <p className="[overflow-wrap:anywhere]">{department.catalog_number && <span className="mr-2 text-muted-foreground">{department.catalog_number}</span>}{department.name}</p> : <><p className="text-muted-foreground">Название папки недоступно</p><p className="break-all text-xs text-muted-foreground">{id}</p></>}</div></li>
  })}</ul> : <p className="mt-2 text-sm text-muted-foreground">Папки не выбраны</p>}</div>
}
