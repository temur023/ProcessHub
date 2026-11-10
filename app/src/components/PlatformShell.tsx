import type { ReactNode } from 'react'
import { Link, useLocation } from 'react-router'
import { Files, LibraryBig, ShieldCheck, type LucideIcon } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { AccountMenu } from '@/components/AccountMenu'
import { PlatformShellContext } from '@/components/platform-shell-context'

interface ModuleLink {
  path: string
  label: string
  icon: LucideIcon
  active: boolean
}

function RailTooltip({ children }: { children: ReactNode }) {
  return <span aria-hidden="true" className="pointer-events-none invisible absolute left-full top-1/2 z-50 ml-3 -translate-y-1/2 whitespace-nowrap rounded-md border bg-popover px-3 py-2 text-xs font-medium text-popover-foreground opacity-0 shadow-md transition-opacity group-hover:visible group-hover:opacity-100 group-focus-visible:visible group-focus-visible:opacity-100">{children}</span>
}

/** Одна панель модулей и профиля для всех авторизованных экранов Hub. */
export function PlatformShell({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const { pathname } = useLocation()
  const modules: ModuleLink[] = [
    { path: '/', label: 'Библиотека процессов', icon: LibraryBig, active: pathname === '/' || /^\/(space|map|process)(\/|$)/.test(pathname) },
    { path: '/knowledge', label: 'Документы', icon: Files, active: pathname === '/knowledge' || pathname === '/knowledge/bank' || pathname.startsWith('/knowledge/departments/') },
    ...(user.isAdmin ? [
      { path: '/access', label: 'Управление доступом', icon: ShieldCheck, active: pathname === '/access' || pathname.startsWith('/access/') || pathname === '/roles' || pathname === '/knowledge/manage' },
    ] : []),
  ]
  const railItem = 'group relative flex size-11 items-center justify-center rounded-xl outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar-background'

  return <PlatformShellContext.Provider value={true}>
    <div className="flex h-dvh min-w-0 overflow-hidden bg-background text-foreground">
      <aside aria-label="Панель платформы" className="z-50 flex w-[72px] shrink-0 flex-col items-center border-r bg-sidebar-background px-3 py-5">
        <Link to="/" aria-label="Process Hub — главная" className={`${railItem} text-primary hover:bg-muted/60`}>
          <span className="flex size-9 items-center justify-center rounded-xl border border-primary/10 bg-primary/10 text-[10px] font-semibold tracking-tight">SQB</span>
          <RailTooltip>Process Hub</RailTooltip>
        </Link>
        <nav aria-label="Модули Process Hub" className="mt-7 flex flex-col gap-2">
          {modules.map(module => <Link key={module.path} to={module.path} aria-label={module.label} aria-current={module.active ? 'page' : undefined} className={`${railItem} ${module.active ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'}`}>
            <module.icon aria-hidden="true" className="size-5" strokeWidth={1.75} />
            <RailTooltip>{module.label}</RailTooltip>
          </Link>)}
        </nav>
        <div className="mt-auto border-t pt-4">
          <AccountMenu rail />
        </div>
      </aside>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">{children}</div>
    </div>
  </PlatformShellContext.Provider>
}
