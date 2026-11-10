import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { Check, Folder, Pencil, Plus, Search, ShieldCheck, Trash2, UsersRound } from 'lucide-react'
import { toast } from 'sonner'
import { AccessError, AccessLoading, AccessModule } from '@/components/AccessModule'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { useAuth } from '@/lib/auth'
import { getDepartments, KnowledgeRequestError, type Department } from '@/lib/knowledge'
import { accessFailure, capabilityDescriptions, capabilityLabels, documentCapabilities, getAccessRoles, saveAccessRole, type AccessRole, type DocumentCapability, type DocumentPermission } from '@/lib/knowledgeAccess'
import { plural } from '@/lib/presentation'

interface RoleData { roles: AccessRole[]; departments: Department[] }
const capabilityGroups: { title: string; description: string; items: DocumentCapability[] }[] = [
  { title: 'Просмотр', description: 'Открытие и получение исходных документов.', items: ['documents.read', 'documents.download'] },
  { title: 'Работа с документами', description: 'Загрузка файлов и подготовка исходного текста.', items: ['instructions.create', 'versions.create', 'preparation.manage'] },
  { title: 'Проверка и анализ', description: 'Подтверждение версий и анализ инструкций с положением.', items: ['editions.verify', 'comparisons.manage'] },
  { title: 'Удаление и восстановление', description: 'Отдельные права на изменение списка инструкций.', items: ['instructions.delete', 'instructions.restore'] },
]

export default function AccessRolesPage() {
  const { user } = useAuth()
  const [data, setData] = useState<RoleData>()
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [editor, setEditor] = useState<{ role?: AccessRole }>()
  useEffect(() => {
    if (!user.isAdmin) return
    const controller = new AbortController()
    void Promise.all([getAccessRoles(controller.signal), getDepartments(controller.signal)])
      .then(([roles, departments]) => { if (!controller.signal.aborted) { setData({ roles, departments }); setError('') } })
      .catch(cause => { if (!controller.signal.aborted) setError(accessFailure(cause)) })
    return () => controller.abort()
  }, [reload, user.isAdmin])

  return <AccessModule activeSection="roles" title="Роли" description="Определите функции роли и доступные папки департаментов. Назначение выполняется в разделе «Сотрудники»."
    action={<Button disabled={!data || Boolean(error)} onClick={() => setEditor({})}><Plus className="size-4" />Добавить роль</Button>}>
    <p className="mt-5 flex items-start gap-2 text-xs leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-4 shrink-0" />Главный администратор имеет все функции во всех папках. Эти роли определяют доступ остальных сотрудников.</p>
    {error ? <AccessError message={error} retry={() => { setError(''); setReload(value => value + 1) }} />
      : !data ? <AccessLoading />
      : data.roles.length === 0 ? <div className="py-20 text-center">
        <UsersRound className="mx-auto size-9 text-muted-foreground" /><h2 className="mt-4 text-lg font-semibold">Создайте первую роль</h2>
        <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted-foreground">Например, для ответственного за инструкции. Папки можно выбрать сразу или добавить к роли позже.</p>
        <Button variant="outline" className="mt-6" onClick={() => setEditor({})}><Plus className="size-4" />Добавить роль</Button>
      </div>
      : <section aria-label="Роли доступа" className="mt-7">
        <div className="mb-4 flex items-center justify-between text-sm"><p className="text-muted-foreground">{plural(data.roles.length, 'роль', 'роли', 'ролей')}</p><Link className="font-medium hover:underline" to="/access/employees">Назначить сотрудникам →</Link></div>
        <ul className="space-y-3">{data.roles.map(role => {
          const names = role.department_ids.map(id => data.departments.find(item => item.id === id)?.name ?? 'Папка недоступна')
          const capabilities = role.capabilities ?? []
          return <li key={role.id} className="rounded-xl border bg-card px-4 py-3">
            <div className="flex items-start justify-between gap-4">
              <div className="flex min-w-0 items-start gap-3"><span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted"><UsersRound className="size-4 text-muted-foreground" /></span>
                <div className="min-w-0"><h2 className="break-words text-sm font-semibold">{role.name}</h2><p className="mt-0.5 text-xs text-muted-foreground">{plural(capabilities.length, 'функция', 'функции', 'функций')} <span className="mx-1.5">·</span>{plural(role.member_count, 'сотрудник', 'сотрудника', 'сотрудников')}</p></div>
              </div>
              <Button size="sm" variant="ghost" aria-label={`Изменить роль ${role.name}`} onClick={() => setEditor({ role })}><Pencil className="size-4" />Изменить</Button>
            </div>
            <ul aria-label={`Функции роли ${role.name}`} className="ml-11 mt-2 flex flex-wrap gap-1.5">{documentCapabilities.filter(item => capabilities.includes(item)).map(item => <li key={item} className={`inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs ${item === 'instructions.delete' ? 'bg-destructive/10 text-destructive' : 'bg-muted text-muted-foreground'}`}>
              {item === 'instructions.delete' && <Trash2 className="size-3.5" aria-hidden="true" />}{capabilityLabels[item]}
            </li>)}</ul>
            <div className="ml-11 mt-2 flex items-start gap-1.5 text-xs leading-5 text-muted-foreground"><Folder className="mt-0.5 size-3.5 shrink-0" /><p title={names.join('; ')} className="line-clamp-2">{names.length ? names.join(' · ') : 'Папки ещё не выбраны — роль пока не открывает документы'}</p></div>
          </li>
        })}</ul>
      </section>}
    {data && editor && <RoleEditor key={editor.role ? `${editor.role.id}:${editor.role.updated_at}` : 'new'} role={editor.role} departments={data.departments} onClose={() => setEditor(undefined)} onSaved={() => { setEditor(undefined); setData(undefined); setReload(value => value + 1) }} />}
  </AccessModule>
}

function RoleEditor({ role, departments, onClose, onSaved }: { role?: AccessRole; departments: Department[]; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(role?.name ?? '')
  const [capabilities, setCapabilities] = useState<DocumentCapability[]>(role?.capabilities ?? ['documents.read', 'documents.download'])
  const [selected, setSelected] = useState<string[]>(role?.department_ids ?? [])
  const [search, setSearch] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState(false)
  const query = search.trim().toLocaleLowerCase('ru')
  const visible = departments.filter(item => `${item.catalog_number} ${item.name}`.toLocaleLowerCase('ru').includes(query))
  function toggleCapability(capability: DocumentCapability) {
    if (busy || capability === 'documents.read') return
    setCapabilities(current => current.includes(capability)
      ? current.filter(item => item !== capability)
      : documentCapabilities.filter(item => current.includes(item) || item === 'documents.read' || item === capability))
  }
  async function save(event: FormEvent) {
    event.preventDefault()
    if (busy || conflict || !name.trim() || !reason.trim() || !capabilities.includes('documents.read')) return
    setBusy(true); setError(''); setConflict(false)
    const permission: DocumentPermission = capabilities.some(item => item !== 'documents.read' && item !== 'documents.download')
      ? 'owner' : role?.permission === 'reviewer' ? 'reviewer' : 'reader'
    try { await saveAccessRole(role, name.trim(), permission, selected, reason.trim(), documentCapabilities.filter(item => capabilities.includes(item))); toast.success(role ? 'Роль обновлена' : 'Роль создана'); onSaved() }
    catch (cause) { setError(accessFailure(cause)); setConflict(cause instanceof KnowledgeRequestError && cause.status === 409 && cause.message.includes('другим администратором')) }
    finally { setBusy(false) }
  }
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}>
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl" showCloseButton={!busy} onEscapeKeyDown={event => { if (busy) event.preventDefault() }} onPointerDownOutside={event => { if (busy) event.preventDefault() }}>
      <DialogHeader><DialogTitle>{role ? 'Изменить роль' : 'Новая роль'}</DialogTitle><DialogDescription>Права действуют во всех выбранных папках. Должность сотрудника указывается отдельно в его инструкции.</DialogDescription></DialogHeader>
      <form onSubmit={save} className="space-y-6">
        <label className="block space-y-2 text-sm font-medium">Название роли<Input value={name} onChange={event => setName(event.target.value)} maxLength={200} disabled={busy} placeholder="Например, ответственный за инструкции" required /></label>
        <fieldset disabled={busy} className="space-y-3"><legend className="text-sm font-medium">Функции роли <span className="ml-1 font-normal text-muted-foreground">{capabilities.length} из {documentCapabilities.length}</span></legend>
          <p id="role-functions-help" className="text-xs leading-5 text-muted-foreground">Просмотр обязателен для любой роли. Остальные функции выбираются отдельно и действуют только в отмеченных папках.</p>
          <div className="grid grid-cols-2 gap-3">{capabilityGroups.map(group => <section key={group.title} className="min-w-0 rounded-xl border p-3" aria-label={group.title}>
            <h3 className="px-2 pt-1 text-sm font-semibold">{group.title}</h3><p className="min-h-10 px-2 pb-2 pt-1 text-xs leading-5 text-muted-foreground">{group.description}</p>
            <div className="space-y-1">{group.items.map(item => {
              const checked = capabilities.includes(item)
              const required = item === 'documents.read'
              return <button key={item} type="button" role="checkbox" aria-checked={checked} aria-label={capabilityLabels[item]} aria-describedby={`function-${item}-description`} disabled={busy || required} onClick={() => toggleCapability(item)} className={`flex w-full items-start gap-2.5 rounded-lg p-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring ${required ? 'cursor-default' : 'hover:bg-muted'} ${checked ? 'bg-primary/5' : ''} ${busy ? 'opacity-60' : ''}`}>
                <span aria-hidden="true" className={`mt-0.5 flex size-4 shrink-0 items-center justify-center rounded border ${checked ? 'border-primary bg-primary text-primary-foreground' : 'border-input'}`}>{checked && <Check className="size-3" />}</span>
                <span className="min-w-0"><span className={`block text-sm font-medium leading-5 ${item === 'instructions.delete' ? 'text-destructive' : ''}`}>{capabilityLabels[item]}</span>{required && <span className="mt-0.5 block text-[11px] text-muted-foreground">Обязательная функция</span>}<span id={`function-${item}-description`} className="mt-1 block text-xs leading-5 text-muted-foreground">{capabilityDescriptions[item]}</span></span>
              </button>
            })}</div>
          </section>)}</div>
          {capabilities.includes('instructions.delete') && <p className="flex items-start gap-2 rounded-lg bg-destructive/5 px-3 py-2.5 text-xs leading-5 text-destructive"><Trash2 className="mt-0.5 size-4 shrink-0" />Роль разрешает удалять инструкции во всех выбранных папках. Восстановление выдаётся отдельной функцией.</p>}
        </fieldset>
        <fieldset disabled={busy} className="space-y-3"><legend className="text-sm font-medium">Папки департаментов <span className="ml-1 font-normal text-muted-foreground">{selected.length} выбрано</span></legend>
          <div className="overflow-hidden rounded-xl border">
            <div className="relative border-b"><Search className="pointer-events-none absolute left-3 top-3 size-4 text-muted-foreground" /><Input aria-label="Поиск папок департаментов" className="h-10 rounded-none border-0 pl-9 shadow-none" value={search} onChange={event => setSearch(event.target.value)} placeholder="Название или номер департамента" /></div>
            <div className="max-h-52 overflow-y-auto p-1.5" role="group" aria-label="Доступные папки">
              {visible.length ? visible.map(item => {
                const checked = selected.includes(item.id)
                return <button key={item.id} type="button" role="checkbox" aria-checked={checked} aria-label={`${item.catalog_number} ${item.name}`.trim()} disabled={busy} onClick={() => setSelected(current => checked ? current.filter(id => id !== item.id) : [...current, item.id])} className={`flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left text-sm leading-5 outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring ${checked ? 'bg-primary/5' : ''}`}>
                  <span aria-hidden="true" className={`mt-0.5 flex size-4 shrink-0 items-center justify-center rounded border ${checked ? 'border-primary bg-primary text-primary-foreground' : 'border-input'}`}>{checked && <Check className="size-3" />}</span>
                  <span>{item.catalog_number && <span className="mr-2 text-muted-foreground">{item.catalog_number}</span>}{item.name}</span>
                </button>
              }) : <p className="p-5 text-center text-sm text-muted-foreground">Папки не найдены</p>}
            </div>
          </div>
          {!selected.length && <p className="text-xs leading-5 text-muted-foreground">Можно сохранить роль без папок и выбрать их позже. До этого роль не даёт доступа к документам.</p>}
        </fieldset>
        <label className="block space-y-2 text-sm font-medium">Основание изменения<Input value={reason} onChange={event => setReason(event.target.value)} maxLength={300} disabled={busy} placeholder="Причина для журнала изменений" required /></label>
        {error && <p role="alert" className="text-sm leading-6 text-destructive">{error}</p>}
        {conflict && <Button type="button" variant="outline" onClick={onSaved}>Закрыть форму и обновить список</Button>}
        <DialogFooter><Button type="button" variant="outline" disabled={busy} onClick={onClose}>Отмена</Button><Button type="submit" disabled={busy || conflict || !name.trim() || !reason.trim() || !capabilities.includes('documents.read')}>{busy && <Spinner className="size-4" />}{role ? 'Сохранить изменения' : 'Создать роль'}</Button></DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
}
