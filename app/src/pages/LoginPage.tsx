import React, { useEffect, useRef, useState } from 'react'
import { ArrowRight, Eye, EyeOff, Files, LibraryBig, Loader2, ShieldCheck, Workflow, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { User } from '@/lib/auth'
import { demoLogin, fetchAuthOptions, login, registerUser } from '@/lib/api'

interface LoginPageProps {
  onSignedIn: (user: User) => void
  /** Сессия кончилась посреди работы — сказать, почему снова вход. */
  expired: boolean
}

type Mode = 'login' | 'register'

const MODES: { value: Mode; label: string }[] = [
  { value: 'login', label: 'Вход' },
  { value: 'register', label: 'Регистрация' },
]

/**
 * Вход и регистрация. Без входа платформа закрыта целиком: у каждой правки
 * карты должен быть автор. Адрес страницы не меняется — после входа
 * открывается то, что сотрудник открывал.
 */
export default function LoginPage({ onSignedIn, expired }: LoginPageProps) {
  const [mode, setMode] = useState<Mode>('login')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [demoEmail, setDemoEmail] = useState<string | null>(null)
  const tabs = useRef<Partial<Record<Mode, HTMLButtonElement | null>>>({})
  useEffect(() => { void fetchAuthOptions().then(options => setDemoEmail(options.demo ? options.demoEmail : null)) }, [])
  const registering = mode === 'register'

  const changeMode = (next: Mode) => {
    setMode(next)
    setError(null)
    setShowPassword(false)
  }

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const request = registering ? registerUser(name, email, password) : login(email, password)
    request
      .then(onSignedIn)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(false))
  }

  const quickSignIn = () => {
    setBusy(true)
    setError(null)
    demoLogin()
      .then(onSignedIn)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(false))
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <main className="flex min-h-screen items-center justify-center px-8 py-8">
        <div className="grid min-h-[640px] w-full max-w-[1080px] grid-cols-[0.9fr_1fr] overflow-hidden rounded-[24px] border bg-card shadow-[0_24px_80px_-32px_hsl(var(--foreground)/0.2)]">
          <section aria-label="ProcessHub" className="relative flex flex-col overflow-hidden bg-slate-950 px-10 py-10 text-white">
            <div aria-hidden="true" className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top_left,rgba(71,85,105,0.45),transparent_65%)]" />
            <div aria-hidden="true" className="pointer-events-none absolute -bottom-40 -left-32 size-[480px] rounded-full border border-white/[0.05]" />
            <div aria-hidden="true" className="pointer-events-none absolute -bottom-28 -left-20 size-[384px] rounded-full border border-white/[0.05]" />

            <div className="relative flex items-center gap-3">
              <span className="flex size-10 items-center justify-center rounded-xl border border-white/15 bg-white/10">
                <Workflow aria-hidden="true" className="size-5" strokeWidth={1.75} />
              </span>
              <span className="text-xl font-semibold tracking-tight">ProcessHub</span>
            </div>

            <div className="relative mt-14">
              <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-slate-400">Единая платформа</p>
              <h2 className="mt-4 max-w-[360px] text-[34px] font-semibold leading-[1.18] tracking-tight">Процессы, документы и команда.</h2>
              <p className="mt-4 max-w-[320px] text-sm leading-6 text-slate-400">Всё для работы — в одном пространстве.</p>
            </div>

            <div className="relative mt-9 space-y-2.5">
              {[
                { icon: LibraryBig, label: 'Библиотека процессов' },
                { icon: Files, label: 'Документы департаментов' },
                { icon: ShieldCheck, label: 'Управление доступом' },
              ].map(({ icon: Icon, label }) => (
                <div key={label} className="flex items-center gap-3 rounded-xl border border-white/[0.08] bg-white/[0.035] px-4 py-3.5">
                  <Icon aria-hidden="true" className="size-[18px] text-slate-400" strokeWidth={1.75} />
                  <span className="text-sm text-slate-200">{label}</span>
                </div>
              ))}
            </div>
            <p className="relative mt-auto pt-8 text-xs text-slate-500">Рабочее пространство команды</p>
          </section>

          <section aria-labelledby="auth-title" className="flex flex-col justify-center px-12 py-10">
            <div className="mx-auto w-full max-w-[384px]">
              <div role="tablist" aria-label="Вход или регистрация" className="grid grid-cols-2 gap-1 rounded-xl bg-muted/80 p-1">
                {MODES.map(({ value, label }) => (
                  <button
                    key={value}
                    ref={(element) => { tabs.current[value] = element }}
                    id={`auth-tab-${value}`}
                    type="button"
                    role="tab"
                    aria-selected={mode === value}
                    aria-controls="auth-panel"
                    tabIndex={mode === value ? 0 : -1}
                    disabled={busy}
                    onClick={() => changeMode(value)}
                    onKeyDown={(event) => {
                      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
                      event.preventDefault()
                      const next = event.key === 'Home' ? 'login' : event.key === 'End' ? 'register' : mode === 'login' ? 'register' : 'login'
                      changeMode(next)
                      tabs.current[next]?.focus()
                    }}
                    className={`rounded-lg py-2 text-sm font-medium transition-colors focus-visible:ring-foreground/30 disabled:cursor-wait ${
                      mode === value ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              <div id="auth-panel" role="tabpanel" aria-labelledby={`auth-tab-${mode}`} className="mt-8">
                <h1 id="auth-title" className="text-[28px] font-semibold leading-tight tracking-tight">{registering ? 'Создайте аккаунт' : 'Добро пожаловать'}</h1>
                <p className="mb-7 mt-2 text-sm leading-6 text-muted-foreground">{registering ? 'Используйте рабочую почту @sqb.uz.' : 'Войдите в рабочее пространство.'}</p>

                {expired && !error && (
                  <p role="status" className="mb-5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2.5 text-sm text-amber-800 dark:text-amber-200">
                    Сессия закончилась — войдите снова.
                  </p>
                )}

                {demoEmail && !registering && (
                  <div className="mb-6">
                    <Button type="button" variant="outline" className="h-12 w-full rounded-xl" disabled={busy} onClick={quickSignIn}>
                      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap aria-hidden="true" className="size-4" />}
                      Быстрый вход
                    </Button>
                    <p className="mt-2 text-center text-xs text-muted-foreground">Тестовый стенд · {demoEmail}</p>
                    <div className="mt-6 flex items-center gap-3 text-xs text-muted-foreground"><span className="h-px flex-1 bg-border" />или почта и пароль<span className="h-px flex-1 bg-border" /></div>
                  </div>
                )}

                <form onSubmit={submit} aria-busy={busy} className="space-y-5">
                  {registering && (
                    <div className="space-y-2">
                      <Label htmlFor="auth-name">Имя и фамилия</Label>
                      <Input
                        id="auth-name"
                        className="h-12 rounded-xl bg-background px-3.5 shadow-none"
                        autoComplete="name"
                        placeholder="Иван Петров"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        disabled={busy}
                        required
                      />
                    </div>
                  )}
                  <div className="space-y-2">
                    <Label htmlFor="auth-email">Рабочая почта</Label>
                    <Input
                      id="auth-email"
                      className="h-12 rounded-xl bg-background px-3.5 shadow-none"
                      type="email"
                      autoComplete="username"
                      placeholder="i.petrov@sqb.uz"
                      spellCheck={false}
                      autoCapitalize="none"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      disabled={busy}
                      required
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="auth-password">Пароль</Label>
                    <div className="relative">
                      <Input
                        id="auth-password"
                        className="h-12 rounded-xl bg-background pl-3.5 pr-12 shadow-none"
                        type={showPassword ? 'text' : 'password'}
                        autoComplete={registering ? 'new-password' : 'current-password'}
                        placeholder={registering ? 'Не менее 8 символов' : 'Введите пароль'}
                        aria-describedby={registering ? 'auth-password-help' : undefined}
                        minLength={registering ? 8 : undefined}
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        disabled={busy}
                        required
                      />
                      <button type="button" aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'} aria-pressed={showPassword} disabled={busy} onClick={() => setShowPassword((visible) => !visible)} className="absolute right-2 top-1/2 flex size-8 -translate-y-1/2 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-foreground/30">
                        {showPassword ? <EyeOff aria-hidden="true" className="size-[18px]" /> : <Eye aria-hidden="true" className="size-[18px]" />}
                      </button>
                    </div>
                    {registering && <p id="auth-password-help" className="text-xs text-muted-foreground">Минимум 8 символов.</p>}
                  </div>

                  {error && (
                    <p role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2.5 text-sm text-rose-700 dark:text-rose-300">
                      {error}
                    </p>
                  )}

                  <Button type="submit" className="h-12 w-full rounded-xl focus-visible:ring-foreground/30" disabled={busy}>
                    {busy && <Loader2 className="h-4 w-4 animate-spin" />}
                    {busy ? (registering ? 'Создаём аккаунт…' : 'Входим…') : (registering ? 'Зарегистрироваться' : 'Войти')}
                    {!busy && <ArrowRight aria-hidden="true" className="size-4" />}
                  </Button>
                </form>

                {registering && (
                  <p className="mt-5 text-xs leading-5 text-muted-foreground">
                    Доступ к документам назначает администратор.
                  </p>
                )}
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  )
}
