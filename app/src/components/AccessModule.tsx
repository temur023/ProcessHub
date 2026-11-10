import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { CircleHelp, Contact, History, ShieldCheck, UserRound, UsersRound } from 'lucide-react'
import { KnowledgeShell } from '@/components/KnowledgeShell'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { useAuth } from '@/lib/auth'

const sections = [
  { id: 'roles', path: '/access/roles', label: 'Роли', icon: UsersRound },
  { id: 'employees', path: '/access/employees', label: 'Сотрудники', icon: UserRound },
  { id: 'audit', path: '/access/audit', label: 'Журнал изменений', icon: History },
  { id: 'users', path: '/access/users', label: 'Пользователи', icon: Contact },
] as const

export function AccessModule({ activeSection, title, description, action, children }: {
  activeSection: 'roles' | 'employees' | 'audit' | 'users'; title: string; description: string; action?: ReactNode; children: ReactNode
}) {
  const { user } = useAuth()
  return <KnowledgeShell>
    <div className="mx-auto w-full max-w-6xl">
      <header className="pb-7">
        <div><p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Администрирование</p>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight">Управление доступом</h1>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">Настраивайте функции ролей, назначайте их сотрудникам и проверяйте изменения прав.</p>
        </div>
      </header>
      {user.isAdmin ? <>
        <nav aria-label="Разделы управления доступом" className="flex gap-7 border-b">{sections.map(section => <Link key={section.id} to={section.path} aria-current={activeSection === section.id ? 'page' : undefined} className={`inline-flex items-center gap-2 border-b-2 px-1 pb-3 text-sm font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${activeSection === section.id ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}`}><section.icon className="size-4" aria-hidden="true" />{section.label}</Link>)}</nav>
        <div className="mt-7 flex items-start justify-between gap-8"><div className="flex min-w-0 items-center gap-2"><h2 className="text-xl font-semibold tracking-tight">{title}</h2><span className="group relative inline-flex"><button type="button" aria-label={`Что это: ${title}`} aria-describedby="access-section-hint" className="rounded-full text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"><CircleHelp className="size-4" /></button><span id="access-section-hint" role="tooltip" className="invisible absolute left-0 top-full z-20 mt-2 w-80 rounded-lg border bg-popover p-3 text-sm leading-6 text-popover-foreground opacity-0 shadow-md transition-opacity group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100">{description}</span></span></div>{action && <div className="shrink-0">{action}</div>}</div>
        {children}
      </> : <div className="py-20 text-center">
        <ShieldCheck className="mx-auto size-9 text-muted-foreground" />
        <h2 className="mt-4 text-lg font-semibold">Раздел для администратора</h2>
        <p className="mt-2 text-sm text-muted-foreground">Управлять ролями и доступом может главный администратор.</p>
        <Button asChild variant="outline" className="mt-5"><Link to="/knowledge">Открыть документы</Link></Button>
      </div>}
    </div>
  </KnowledgeShell>
}

export function AccessLoading() {
  return <div role="status" className="flex items-center gap-2 py-10 text-sm text-muted-foreground"><Spinner className="size-4" />Загружаем данные…</div>
}

export function AccessError({ message, retry }: { message: string; retry: () => void }) {
  return <div role="alert" className="mt-6 rounded-xl border p-5 text-sm"><p>{message}</p><Button className="mt-3" size="sm" variant="outline" onClick={retry}>Повторить</Button></div>
}
