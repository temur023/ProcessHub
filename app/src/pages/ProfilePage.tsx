import React, { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { toast } from 'sonner'
import { Activity, CalendarClock, ChevronRight, History, Loader2, ShieldCheck, UserRound, Workflow } from 'lucide-react'
import { Breadcrumbs, CrumbCurrent } from '@/components/Breadcrumbs'
import { CatalogShell } from '@/components/catalog/CatalogShell'
import { StatTile } from '@/components/StatTile'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Spinner } from '@/components/ui/spinner'
import { initials, useAuth, type Profile, type User } from '@/lib/auth'
import { changePassword, fetchProfile, renameProfile } from '@/lib/api'
import { plural, VERSION_SOURCE_LABELS } from '@/lib/presentation'

/** Виды версий в профиле. Перенос из прежнего хранилища сюда не попадает: у него нет автора. */
const SOURCES = ['edit', 'drawio', 'import', 'restore']

const day = (iso: string) => new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })
const dateTime = (iso: string) =>
  new Date(iso).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
const message = (err: unknown) => (err instanceof Error ? err.message : String(err))

const ERROR = 'rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2.5 text-sm text-rose-700 dark:text-rose-300'
const FIELD = 'h-11 rounded-lg bg-background px-3.5 shadow-none'
const SECTIONS = [
  { id: 'personal', path: '/profile', label: 'Личные данные', icon: UserRound },
  { id: 'security', path: '/profile?section=security', label: 'Безопасность', icon: ShieldCheck },
  { id: 'activity', path: '/profile?section=activity', label: 'Активность', icon: Activity },
] as const

/**
 * Профиль сотрудника: что он сохранил на платформе и на каких картах, смена
 * имени и пароля. Статистику считает бэкенд по истории версий — только его
 * версии, чужие правки на тех же картах не в счёт.
 */
export default function ProfilePage() {
  const { user, updateUser } = useAuth()
  const [profile, setProfile] = useState<Profile | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [searchParams] = useSearchParams()
  const requestedSection = searchParams.get('section')
  const activeSection = SECTIONS.find((section) => section.id === requestedSection)?.id ?? 'personal'

  useEffect(() => {
    let alive = true
    fetchProfile()
      .then((loaded) => alive && setProfile(loaded))
      .catch((err) => alive && setError(message(err)))
    return () => {
      alive = false
    }
  }, [reloadKey])

  return (
    <CatalogShell>
      <main className="min-w-0 flex-1 overflow-y-auto px-4 py-5 md:px-8 md:py-8">
        <div className="mx-auto w-full max-w-6xl">
          {/* Общей шапки у каталога нет — обратно на главную ведёт путь, как на страницах пространств. */}
          <Breadcrumbs label="Путь">
            <CrumbCurrent>Профиль</CrumbCurrent>
          </Breadcrumbs>

          <header className="mt-7">
            <h1 className="text-3xl font-semibold tracking-tight">Профиль</h1>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">Настройки аккаунта и история работы в ProcessHub.</p>
          </header>

          <section aria-label="Учётная запись" className="mt-7 flex items-center gap-5 rounded-2xl border bg-card p-6">
            <span aria-hidden="true" className="flex size-16 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-xl font-semibold text-primary">
              {initials(user.name)}
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="text-xl font-semibold leading-snug tracking-tight [overflow-wrap:anywhere]">{user.name}</h2>
              <p className="mt-1 break-all text-sm text-muted-foreground">{user.email}</p>
              {profile && <p className="mt-2 text-xs text-muted-foreground">На платформе с <time dateTime={profile.createdAt}>{day(profile.createdAt)}</time></p>}
            </div>
            {user.isAdmin && <span className="inline-flex shrink-0 items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2 text-xs font-medium"><ShieldCheck aria-hidden="true" className="size-4 text-muted-foreground" />Главный администратор</span>}
          </section>

          <nav aria-label="Разделы профиля" className="mt-7 flex gap-7 border-b">
            {SECTIONS.map(({ id, path, label, icon: Icon }) => <Link key={id} to={path} id={`profile-section-${id}`} aria-current={activeSection === id ? 'page' : undefined} className={`inline-flex items-center gap-2 border-b-2 px-1 pb-3 text-sm font-medium transition-colors focus-visible:ring-foreground/30 ${activeSection === id ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>
              <Icon aria-hidden="true" className="size-4" />{label}
            </Link>)}
          </nav>

          {/* Формы остаются смонтированы: переход между разделами сохраняет введённые значения. */}
          <div hidden={activeSection !== 'personal'} className="mt-7">
            <NameForm name={user.name} email={user.email} onSaved={updateUser} />
          </div>
          <div hidden={activeSection !== 'security'} className="mt-7">
            <PasswordForm email={user.email} />
          </div>
          <section hidden={activeSection !== 'activity'} aria-labelledby="profile-activity-title" className="mt-7 space-y-6">
            <div>
              <h2 id="profile-activity-title" className="text-xl font-semibold tracking-tight">Активность в процессах</h2>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">Ваши сохранённые версии и последние карты библиотеки процессов.</p>
            </div>
            {!profile ? (
              error ? <div role="alert" className="rounded-xl border bg-card p-5 text-sm">
                <p>Не удалось загрузить активность: {error}</p>
                <Button type="button" variant="outline" size="sm" className="mt-4" onClick={() => { setError(null); setReloadKey((key) => key + 1) }}>Повторить</Button>
              </div> : <div role="status" className="flex items-center gap-2 py-10 text-sm text-muted-foreground"><Spinner className="size-4" />Загружаем активность…</div>
            ) : <>
              <div className="grid grid-cols-3 gap-4">
                <StatTile label="Сохранено версий" value={profile.versions} icon={History} />
                <StatTile label="Карт с моими правками" value={profile.maps} icon={Workflow} />
                <StatTile label="Последняя правка" value={profile.lastSavedAt ? dateTime(profile.lastSavedAt) : '—'} icon={CalendarClock} />
              </div>
              <ul className="flex flex-wrap gap-2" aria-label="Версии по видам">
                {SOURCES.map((source) => <li key={source} className="rounded-lg border bg-card px-3 py-1.5 text-xs text-muted-foreground">
                  {VERSION_SOURCE_LABELS[source]} <strong className="ml-1 tabular-nums text-foreground">{profile.bySource[source] ?? 0}</strong>
                </li>)}
              </ul>

              <div className="overflow-hidden rounded-2xl border bg-card">
                <div className="border-b px-6 py-5">
                  <h3 className="font-semibold">Последние карты</h3>
                  <p className="mt-1 text-sm text-muted-foreground">В порядке последних изменений.</p>
                </div>
                {profile.recentMaps.length ? <ul className="divide-y">
                  {profile.recentMaps.map((map) => <li key={map.id}>
                    <Link to={`/process/${encodeURIComponent(map.id)}`} className="group flex items-center gap-4 px-6 py-4 transition-colors hover:bg-muted/50">
                      <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground"><Workflow aria-hidden="true" className="size-[18px]" /></span>
                      <span className="min-w-0 flex-1 text-sm font-medium leading-6 [overflow-wrap:anywhere]" title={map.name}>{map.name}</span>
                      <span className="shrink-0 text-right text-xs leading-5 tabular-nums text-muted-foreground">
                        <span className="block">{plural(map.versions, 'версия', 'версии', 'версий')}</span>
                        <time dateTime={map.lastSavedAt}>{dateTime(map.lastSavedAt)}</time>
                      </span>
                      <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
                    </Link>
                  </li>)}
                </ul> : <div className="px-6 py-12 text-center">
                  <History aria-hidden="true" className="mx-auto size-7 text-muted-foreground" />
                  <h3 className="mt-4 text-sm font-medium">Сохранённых правок пока нет</h3>
                  <p className="mt-2 text-sm text-muted-foreground">Карты появятся здесь после первого сохранения.</p>
                  <Button asChild variant="outline" className="mt-5"><Link to="/">Открыть библиотеку процессов</Link></Button>
                </div>}
                {profile.maps > profile.recentMaps.length && <p className="border-t px-6 py-3 text-xs text-muted-foreground">Показаны последние {profile.recentMaps.length} из {profile.maps}.</p>}
              </div>
            </>}
          </section>
        </div>
      </main>
    </CatalogShell>
  )
}

function NameForm({ name, email, onSaved }: { name: string; email: string; onSaved: (user: User) => void }) {
  const [value, setValue] = useState(name)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const unchanged = value.trim().replace(/\s+/g, ' ') === name

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    if (busy || unchanged || !value.trim()) return
    setBusy(true)
    setError(null)
    renameProfile(value)
      .then((user) => {
        onSaved(user)
        setValue(user.name)
        toast.success('Имя сохранено')
      })
      .catch((err) => setError(message(err)))
      .finally(() => setBusy(false))
  }

  return (
    <form onSubmit={submit} aria-labelledby="profile-personal-title" aria-busy={busy} className="overflow-hidden rounded-2xl border bg-card">
      <div className="border-b px-6 py-5">
        <h2 id="profile-personal-title" className="text-lg font-semibold tracking-tight">Личные данные</h2>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">Ваше имя отображается в профиле и истории изменений.</p>
      </div>
      <div className="space-y-5 px-6 py-6">
        <div className="grid max-w-4xl grid-cols-2 gap-6">
          <div className="space-y-2">
            <Label htmlFor="profile-name">Имя и фамилия</Label>
            <Input id="profile-name" className={FIELD} autoComplete="name" value={value} onChange={(e) => setValue(e.target.value)} disabled={busy} required />
          </div>
          <div className="space-y-2">
            <Label htmlFor="profile-email">Рабочая почта</Label>
            <Input id="profile-email" className={`${FIELD} bg-muted/40 text-muted-foreground`} type="email" value={email} readOnly aria-describedby="profile-email-help" />
            <p id="profile-email-help" className="text-xs text-muted-foreground">Используется для входа в ProcessHub.</p>
          </div>
        </div>
        {error && <p role="alert" className={ERROR}>{error}</p>}
      </div>
      <div className="flex items-center justify-between gap-6 border-t px-6 py-5">
        <div><h3 className="text-sm font-medium">Тема интерфейса</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">Применяется сразу и сохраняется в этом браузере.</p></div>
        <ThemeToggle />
      </div>
      <div className="flex items-center gap-4 border-t bg-muted/20 px-6 py-4">
        <Button type="submit" className="h-10 rounded-lg focus-visible:ring-foreground/30" disabled={busy || unchanged || !value.trim()}>
          {busy && <Loader2 aria-hidden="true" className="size-4 animate-spin" />}
          {busy ? 'Сохраняем…' : 'Сохранить изменения'}
        </Button>
      </div>
    </form>
  )
}

function PasswordForm({ email }: { email: string }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    if (busy) return
    if (next !== repeat) {
      setError('Новый пароль и повтор не совпадают')
      return
    }
    setBusy(true)
    setError(null)
    changePassword(current, next)
      .then(() => {
        setCurrent('')
        setNext('')
        setRepeat('')
        toast.success('Пароль изменён', { description: 'На других устройствах нужно войти заново.' })
      })
      .catch((err) => setError(message(err)))
      .finally(() => setBusy(false))
  }

  return (
    <form onSubmit={submit} aria-labelledby="profile-security-title" aria-busy={busy} className="overflow-hidden rounded-2xl border bg-card">
      <div className="border-b px-6 py-5">
        <h2 id="profile-security-title" className="text-lg font-semibold tracking-tight">Смена пароля</h2>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">Для изменения подтвердите текущий пароль.</p>
      </div>
      {/* Менеджер паролей запоминает новый пароль для этой почты. */}
      <input type="text" autoComplete="username" value={email} readOnly hidden />
      <div className="space-y-5 px-6 py-6">
        <div className="max-w-2xl space-y-5">
          <div className="space-y-2">
            <Label htmlFor="profile-current">Текущий пароль</Label>
            <Input
              id="profile-current"
              className={FIELD}
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              disabled={busy}
              required
            />
          </div>
          <div className="grid grid-cols-2 gap-5">
            <div className="space-y-2">
              <Label htmlFor="profile-new">Новый пароль</Label>
              <Input
                id="profile-new"
                className={FIELD}
                type="password"
                autoComplete="new-password"
                value={next}
                onChange={(e) => setNext(e.target.value)}
                minLength={8}
                aria-describedby="profile-password-help"
                disabled={busy}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="profile-repeat">Повторите новый пароль</Label>
              <Input
                id="profile-repeat"
                className={FIELD}
                type="password"
                autoComplete="new-password"
                value={repeat}
                onChange={(e) => setRepeat(e.target.value)}
                minLength={8}
                aria-describedby="profile-password-help"
                disabled={busy}
                required
              />
            </div>
          </div>
          <p id="profile-password-help" className="text-xs text-muted-foreground">Минимум 8 символов.</p>
        </div>
        <div className="flex items-start gap-3 rounded-xl bg-muted/50 p-4 text-sm leading-6 text-muted-foreground">
          <ShieldCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <p>После смены пароля другие сеансы завершатся. На этом устройстве вход сохранится.</p>
        </div>
        {error && <p role="alert" className={ERROR}>{error}</p>}
      </div>
      <div className="border-t bg-muted/20 px-6 py-4">
        <Button type="submit" className="h-10 rounded-lg focus-visible:ring-foreground/30" disabled={busy}>
          {busy && <Loader2 aria-hidden="true" className="size-4 animate-spin" />}
          {busy ? 'Меняем пароль…' : 'Сменить пароль'}
        </Button>
      </div>
    </form>
  )
}
