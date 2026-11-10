import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { Check, Folder, Plus, Search, ShieldCheck, Trash2, UserRound, X } from 'lucide-react'
import { toast } from 'sonner'
import { AccessError, AccessLoading, AccessModule } from '@/components/AccessModule'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useAuth } from '@/lib/auth'
import { getDepartments, type Department } from '@/lib/knowledge'
import { accessFailure, capabilityDescriptions, capabilityLabels, documentCapabilities, getAccessRoles, getRoleMemberships, lookupAccessUser, setRoleMembership, type AccessRole, type AccessUser, type RoleMembership } from '@/lib/knowledgeAccess'
import { plural } from '@/lib/presentation'

interface AccessData { roles: AccessRole[]; departments: Department[]; memberships: RoleMembership[] }
const identity = (user: AccessUser | null, id: number) => user?.name || user?.email || `Сотрудник #${id}`

export default function AccessAssignmentsPage() {
  const { user } = useAuth()
  const [data, setData] = useState<AccessData>()
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [query, setQuery] = useState('')
  const [assign, setAssign] = useState(false)
  const [revoke, setRevoke] = useState<RoleMembership>()
  useEffect(() => {
    if (!user.isAdmin) return
    const controller = new AbortController()
    void Promise.all([getAccessRoles(controller.signal), getDepartments(controller.signal), getRoleMemberships(controller.signal)])
      .then(([roles, departments, memberships]) => { if (!controller.signal.aborted) { setData({ roles, departments, memberships }); setError('') } })
      .catch(cause => { if (!controller.signal.aborted) setError(accessFailure(cause)) })
    return () => controller.abort()
  }, [reload, user.isAdmin])
  const search = query.trim().toLocaleLowerCase('ru')
  const memberships = data?.memberships.filter(item => `${item.user?.name ?? ''} ${item.user?.email ?? ''} ${item.role_name}`.toLocaleLowerCase('ru').includes(search)) ?? []
  function saved() { setAssign(false); setRevoke(undefined); setData(undefined); setReload(value => value + 1) }

  return <AccessModule activeSection="employees" title="Сотрудники" description="Назначайте сотрудникам готовые роли. Папки и права берутся из роли; несколько назначений объединяют доступ сотрудника."
    action={<Button disabled={!data?.roles.length || Boolean(error)} onClick={() => setAssign(true)}><Plus className="size-4" />Назначить роль</Button>}>
    {error ? <AccessError message={error} retry={() => { setError(''); setReload(value => value + 1) }} />
      : !data ? <AccessLoading />
      : <>
        <p className="mt-5 text-xs leading-5 text-muted-foreground">Главный администратор имеет все функции во всех папках. Назначать ему роль не требуется.</p>
        {!data.roles.length ? <div className="py-16 text-center"><ShieldCheck className="mx-auto size-9 text-muted-foreground" /><h2 className="mt-4 text-lg font-semibold">Сначала создайте роль</h2><p className="mt-2 text-sm text-muted-foreground">Определите права и папки, затем назначьте роль сотруднику.</p><Button asChild variant="outline" className="mt-6"><Link to="/access/roles">Перейти к ролям</Link></Button></div>
          : !data.memberships.length ? <div className="py-16 text-center"><UserRound className="mx-auto size-9 text-muted-foreground" /><h2 className="mt-4 text-lg font-semibold">Назначений пока нет</h2><p className="mt-2 text-sm text-muted-foreground">Выберите зарегистрированного сотрудника по рабочей почте.</p><Button className="mt-6" variant="outline" onClick={() => setAssign(true)}><Plus className="size-4" />Назначить роль</Button></div>
          : <section aria-label="Назначения сотрудников" className="mt-7">
            <div className="mb-5 flex items-center justify-between gap-6"><p className="text-sm text-muted-foreground">{plural(data.memberships.length, 'назначение', 'назначения', 'назначений')}</p><div className="relative w-80"><Search className="absolute left-3 top-3 size-4 text-muted-foreground" /><Input aria-label="Поиск назначений" className="pl-9" value={query} onChange={event => setQuery(event.target.value)} placeholder="Сотрудник, почта или роль" /></div></div>
            {memberships.length ? <ul className="divide-y rounded-xl border bg-card px-6">{memberships.map(item => <li key={`${item.user_id}:${item.role_id}`} className="grid grid-cols-[minmax(200px,1fr)_minmax(200px,1fr)_auto] items-start gap-8 py-5">
              <div className="flex min-w-0 gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-muted"><UserRound className="size-4 text-muted-foreground" /></span><div className="min-w-0"><p className="break-words text-sm font-semibold">{identity(item.user, item.user_id)}</p><p className="mt-1 break-all text-xs text-muted-foreground">{item.user?.email ?? 'Аккаунт недоступен'}</p></div></div>
              <div className="min-w-0"><p className="break-words text-sm font-medium">{item.role_name}</p><p className="mt-1 text-xs leading-5 text-muted-foreground" title={(item.capabilities ?? []).map(capability => capabilityLabels[capability]).join(' · ')}>{plural(item.capabilities?.length ?? 0, 'функция', 'функции', 'функций')} · {plural(item.department_ids.length, 'папка', 'папки', 'папок')}</p>{item.capabilities?.includes('instructions.delete') && <p className="mt-1 inline-flex items-center gap-1.5 text-xs text-destructive"><Trash2 className="size-3.5" aria-hidden="true" />Разрешено удаление инструкций</p>}</div>
              <Button variant="ghost" size="sm" aria-label={`Отозвать роль ${item.role_name} у ${identity(item.user, item.user_id)}`} onClick={() => setRevoke(item)}><X className="size-4" />Отозвать</Button>
            </li>)}</ul> : <div className="rounded-xl border p-10 text-center text-sm text-muted-foreground">Назначения не найдены</div>}
          </section>}
        {assign && <AssignDialog data={data} onClose={() => setAssign(false)} onSaved={saved} />}
        {revoke && <RevokeDialog key={`${revoke.user_id}:${revoke.role_id}`} membership={revoke} onClose={() => setRevoke(undefined)} onSaved={saved} />}
      </>}
  </AccessModule>
}

function AssignDialog({ data, onClose, onSaved }: { data: AccessData; onClose: () => void; onSaved: () => void }) {
  const [email, setEmail] = useState('')
  const [account, setAccount] = useState<AccessUser>()
  const [roleId, setRoleId] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [finding, setFinding] = useState(false)
  const [error, setError] = useState('')
  const locked = busy || finding
  const role = data.roles.find(item => item.id === roleId)
  const available = data.roles.filter(item => !data.memberships.some(member => member.user_id === account?.id && member.role_id === item.id))
  async function find() {
    if (locked || !email.trim()) return
    setFinding(true); setError(''); setAccount(undefined); setRoleId('')
    try { setAccount(await lookupAccessUser(email.trim())) }
    catch (cause) { setError(accessFailure(cause)) }
    finally { setFinding(false) }
  }
  async function save(event: FormEvent) {
    event.preventDefault()
    if (locked || !account || account.isAdmin || !role || !reason.trim()) return
    setBusy(true); setError('')
    try { await setRoleMembership(role.id, account.id, true, reason.trim()); toast.success('Роль назначена сотруднику'); onSaved() }
    catch (cause) { setError(accessFailure(cause)) }
    finally { setBusy(false) }
  }
  return <Dialog open onOpenChange={open => { if (!open && !locked) onClose() }}><DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl" showCloseButton={!locked} onEscapeKeyDown={event => { if (locked) event.preventDefault() }} onPointerDownOutside={event => { if (locked) event.preventDefault() }}>
    <DialogHeader><DialogTitle>Назначить роль</DialogTitle><DialogDescription>Найдите сотрудника по точной почте его аккаунта в Process Hub.</DialogDescription></DialogHeader>
    <form onSubmit={save} className="space-y-5">
      <div><label htmlFor="member-email" className="text-sm font-medium">Рабочая почта</label><div className="mt-2 flex gap-2"><Input id="member-email" type="email" value={email} disabled={locked} placeholder="name@sqb.uz" onChange={event => { setEmail(event.target.value); setAccount(undefined); setRoleId(''); setError('') }} onKeyDown={event => { if (event.key === 'Enter' && !account) { event.preventDefault(); void find() } }} /><Button type="button" variant="outline" disabled={locked || !email.trim()} onClick={() => void find()}>{finding ? <Spinner className="size-4" /> : <Search className="size-4" />}Найти</Button></div></div>
      {account && <div className="rounded-xl border bg-muted/30 p-4"><p className="text-sm font-semibold">{account.name}</p><p className="mt-1 text-xs text-muted-foreground">{account.email}</p>{account.isAdmin && <p className="mt-3 text-sm leading-6 text-muted-foreground">Главный администратор уже имеет полный доступ. Дополнительное назначение не требуется.</p>}</div>}
      {account && !account.isAdmin && <>
        {available.length ? <div className="space-y-2"><label htmlFor="member-role" className="text-sm font-medium">Роль</label><Select disabled={locked} value={roleId} onValueChange={setRoleId}><SelectTrigger id="member-role" className="w-full"><SelectValue placeholder="Выберите роль" /></SelectTrigger><SelectContent>{available.map(item => <SelectItem value={item.id} key={item.id}>{item.name}</SelectItem>)}</SelectContent></Select></div> : <p className="text-sm text-muted-foreground">Сотруднику уже назначены все созданные роли.</p>}
        {role && <div className="rounded-xl border p-4">
          <p className="text-sm font-medium">Функции роли <span className="ml-1 font-normal text-muted-foreground">{role.capabilities?.length ?? 0}</span></p>
          <ul className="mt-3 grid grid-cols-2 gap-x-5 gap-y-2">{documentCapabilities.filter(item => role.capabilities?.includes(item)).map(item => <li key={item} title={capabilityDescriptions[item]} className={`flex items-start gap-2 text-sm leading-5 ${item === 'instructions.delete' ? 'text-destructive' : ''}`}><Check className="mt-0.5 size-4 shrink-0" /><span>{capabilityLabels[item]}</span></li>)}</ul>
          {role.capabilities?.includes('instructions.delete') && <p className="mt-3 text-xs leading-5 text-destructive">Сотрудник сможет удалять инструкции в перечисленных ниже папках.</p>}
          <div className="mt-4 border-t pt-4"><p className="text-sm font-medium">Доступные папки <span className="ml-1 font-normal text-muted-foreground">{role.department_ids.length}</span></p>{role.department_ids.length ? <ul className="mt-3 max-h-40 space-y-2 overflow-y-auto">{role.department_ids.map(id => <li key={id} className="flex items-start gap-2 text-sm leading-5"><Folder className="mt-0.5 size-4 shrink-0 text-blue-950 dark:text-white" /><span>{data.departments.find(item => item.id === id)?.name ?? 'Папка недоступна'}</span></li>)}</ul> : <p className="mt-2 text-sm leading-6 text-muted-foreground">В роли пока нет папок. Назначение сохранится, но доступ к документам появится после выбора папок в разделе «Роли».</p>}</div>
        </div>}
        <label className="block space-y-2 text-sm font-medium">Основание назначения<Input value={reason} disabled={locked} onChange={event => setReason(event.target.value)} maxLength={300} placeholder="Причина для журнала изменений" required /></label>
      </>}
      {error && <p role="alert" className="text-sm leading-6 text-destructive">{error}</p>}
      <DialogFooter><Button type="button" variant="outline" disabled={locked} onClick={onClose}>Отмена</Button><Button type="submit" disabled={locked || !account || account.isAdmin || !role || !reason.trim()}>{busy && <Spinner className="size-4" />}Назначить роль</Button></DialogFooter>
    </form>
  </DialogContent></Dialog>
}

function RevokeDialog({ membership, onClose, onSaved }: { membership: RoleMembership; onClose: () => void; onSaved: () => void }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function revoke(event: FormEvent) {
    event.preventDefault()
    if (busy || !reason.trim()) return
    setBusy(true); setError('')
    try { await setRoleMembership(membership.role_id, membership.user_id, false, reason.trim()); toast.success('Назначение отозвано'); onSaved() }
    catch (cause) { setError(accessFailure(cause)) }
    finally { setBusy(false) }
  }
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}><DialogContent showCloseButton={!busy} onEscapeKeyDown={event => { if (busy) event.preventDefault() }} onPointerDownOutside={event => { if (busy) event.preventDefault() }}>
    <DialogHeader><DialogTitle>Отозвать роль</DialogTitle><DialogDescription>{identity(membership.user, membership.user_id)} · {membership.role_name}</DialogDescription></DialogHeader>
    <form onSubmit={revoke} className="space-y-5"><p className="text-sm leading-6 text-muted-foreground">Это назначение перестанет открывать папки сотруднику. Доступ через другие роли сохранится.</p><label className="block space-y-2 text-sm font-medium">Основание отзыва<Input disabled={busy} value={reason} maxLength={300} onChange={event => setReason(event.target.value)} placeholder="Причина для журнала изменений" required /></label>{error && <p role="alert" className="text-sm text-destructive">{error}</p>}<DialogFooter><Button type="button" variant="outline" disabled={busy} onClick={onClose}>Отмена</Button><Button type="submit" variant="destructive" disabled={busy || !reason.trim()}>{busy && <Spinner className="size-4" />}Отозвать роль</Button></DialogFooter></form>
  </DialogContent></Dialog>
}
