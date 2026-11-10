import { useEffect, useState } from 'react'
import { RefreshCw, Search, ShieldCheck, UserRound, X } from 'lucide-react'
import { AccessError, AccessLoading, AccessModule } from '@/components/AccessModule'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { apiFetch } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { accessFailure, getRoleMemberships, type RoleMembership } from '@/lib/knowledgeAccess'

interface PlatformUser {
  id: number; email: string; name: string; isAdmin: boolean
  createdAt: string
  versions: number
}
interface UsersData { users: PlatformUser[]; memberships: RoleMembership[]; rolesUnavailable: boolean }

async function getPlatformUsers(signal: AbortSignal): Promise<PlatformUser[]> {
  let response: Response
  try { response = await apiFetch('/api/v1/admin/users', { signal }) }
  catch (cause) {
    if (signal.aborted) throw cause
    throw new Error('Не удалось связаться с сервисом. Проверьте соединение и повторите')
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null
    throw new Error(typeof body?.detail === 'string' ? body.detail : 'Не удалось загрузить пользователей. Повторите позже')
  }
  return response.json() as Promise<PlatformUser[]>
}

const date = (value: string) => new Date(value).toLocaleDateString('ru-RU', { day: '2-digit', month: 'short', year: 'numeric' })

export default function AccessUsersPage() {
  const { user } = useAuth()
  const [data, setData] = useState<UsersData>()
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [query, setQuery] = useState('')

  useEffect(() => {
    if (!user.isAdmin) return
    const controller = new AbortController()
    void Promise.allSettled([getPlatformUsers(controller.signal), getRoleMemberships(controller.signal)])
      .then(([users, memberships]) => {
        if (controller.signal.aborted) return
        if (users.status === 'rejected') { setError(accessFailure(users.reason)); return }
        setData({ users: users.value, memberships: memberships.status === 'fulfilled' ? memberships.value : [], rolesUnavailable: memberships.status === 'rejected' })
        setError('')
      })
    return () => controller.abort()
  }, [reload, user.isAdmin])

  function refresh() { setError(''); setData(undefined); setReload(value => value + 1) }
  function clearFilters() { setQuery('') }
  const rolesOf = (id: number) => data?.memberships.filter(item => item.user_id === id).map(item => item.role_name) ?? []
  const search = query.trim().toLocaleLowerCase('ru')
  const filtered = Boolean(search)
  const users = data?.users.filter(item => {
    const text = [item.name, item.email, item.isAdmin ? 'Главный администратор' : 'Сотрудник', ...rolesOf(item.id)].join(' ').toLocaleLowerCase('ru')
    return text.includes(search)
  }) ?? []
  const admins = data?.users.filter(item => item.isAdmin).length ?? 0

  return <AccessModule activeSection="users" title="Пользователи" description="Все зарегистрированные пользователи платформы: роль и дата регистрации. Видно только главному администратору."
    action={<Button variant="outline" disabled={!data && !error} onClick={refresh}><RefreshCw className="size-4" />Обновить</Button>}>
    {error ? <AccessError message={error} retry={refresh} />
      : !data ? <AccessLoading />
      : <section className="mt-6" aria-label="Пользователи платформы">
        <dl className="grid grid-cols-2 gap-4">
          <Stat label="Всего пользователей" value={data.users.length} />
          <Stat label="Администраторов" value={admins} />
        </dl>
        <div className="mt-6 flex items-center gap-3">
          <div className="relative min-w-0 flex-1"><Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" /><Input aria-label="Поиск пользователей" className="pl-9" value={query} onChange={event => setQuery(event.target.value)} placeholder="Имя, почта или роль" /></div>
          {filtered && <Button variant="ghost" onClick={clearFilters}><X className="size-4" />Сбросить</Button>}
        </div>
        <p className="mt-3 text-xs leading-5 text-muted-foreground">{filtered && <span role="status">Найдено: {users.length} из {data.users.length}.</span>}</p>
        {data.rolesUnavailable && <p className="mt-2 text-xs leading-5 text-muted-foreground">Роли доступа к документам сейчас недоступны: сервис документов не ответил. Показана только роль на платформе.</p>}
        {users.length ? <>
          <div aria-hidden="true" className="mt-6 grid grid-cols-[minmax(0,1.5fr)_minmax(0,1.5fr)_140px_100px] gap-5 px-6 text-xs font-medium text-muted-foreground"><span>Пользователь</span><span>Роль</span><span>Регистрация</span><span className="text-right">Правок карт</span></div>
          <ul className="mt-3 divide-y rounded-xl border bg-card px-6">{users.map(item => <li key={item.id} className="grid grid-cols-[minmax(0,1.5fr)_minmax(0,1.5fr)_140px_100px] items-start gap-5 py-4">
            <div className="flex min-w-0 gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-muted"><UserRound className="size-4 text-muted-foreground" /></span><div className="min-w-0"><p className="text-sm font-semibold [overflow-wrap:anywhere]">{item.name}{item.id === user.id && <span className="ml-2 text-xs font-normal text-muted-foreground">это вы</span>}</p><p className="mt-1 break-all text-xs text-muted-foreground">{item.email}</p></div></div>
            <div className="flex min-w-0 flex-wrap gap-1.5">{item.isAdmin ? <Badge className="max-w-full whitespace-normal text-left"><ShieldCheck />Главный администратор</Badge>
              : rolesOf(item.id).length ? rolesOf(item.id).map(name => <Badge key={name} variant="outline" className="max-w-full whitespace-normal text-left">{name}</Badge>)
              : <span className="text-sm text-muted-foreground">Сотрудник, без роли</span>}</div>
            <p className="text-sm tabular-nums">{date(item.createdAt)}</p>
            <p className="text-right text-sm tabular-nums">{item.versions}</p>
          </li>)}</ul>
        </> : <div className="mt-6 rounded-xl border py-14 text-center"><h3 className="text-base font-semibold">Пользователи не найдены</h3><p className="mt-2 text-sm text-muted-foreground">Измените запрос.</p><Button variant="outline" className="mt-5" onClick={clearFilters}>Сбросить поиск</Button></div>}
      </section>}
  </AccessModule>
}

function Stat({ label, value }: { label: string; value: number }) {
  return <div className="rounded-xl border bg-card px-5 py-4"><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1 text-2xl font-semibold tabular-nums">{value}</dd></div>
}
