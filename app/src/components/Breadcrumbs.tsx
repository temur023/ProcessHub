import type React from 'react'
import { Link } from 'react-router'
import { ChevronRight, Folder, Home as HomeIcon } from 'lucide-react'

const CRUMB = 'flex min-w-0 items-center gap-1.5 rounded-md px-1.5 py-1 transition-colors hover:bg-background hover:text-foreground hover:shadow-sm'

/**
 * Путь от главной на светлой подложке: «⌂ › SANOATQURILISHBANK › 3 › …». Главная —
 * только иконкой (подпись в подсказке); звенья — внутри. Одна и та же подложка на
 * всех экранах: в шапке карты, в пространстве, в профиле.
 */
export const Breadcrumbs: React.FC<{ label: string; children?: React.ReactNode }> = ({ label, children }) => (
  <nav
    aria-label={label}
    className="flex h-9 w-fit min-w-0 max-w-full items-center gap-0.5 rounded-lg border bg-muted/50 px-0.5 text-xs text-muted-foreground"
  >
    <Link
      to="/"
      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md transition-colors hover:bg-background hover:text-foreground hover:shadow-sm"
      title="Главная"
      aria-label="Главная"
    >
      <HomeIcon className="h-4 w-4" />
    </Link>
    {children}
  </nav>
)

export const CrumbSeparator: React.FC = () => (
  <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/40" aria-hidden />
)

/** Звено пути — ссылка; у пространства — жёлтая папка (на телефоне без неё: места мало). */
export const CrumbLink: React.FC<{ to: string; title: string; folder?: boolean; children: React.ReactNode }> = ({
  to,
  title,
  folder,
  children,
}) => (
  <span className="flex min-w-0 shrink items-center gap-0.5">
    <CrumbSeparator />
    <Link to={to} className={CRUMB} title={title}>
      {folder && <Folder className="hidden h-3.5 w-3.5 shrink-0 text-blue-950 dark:text-white sm:block" />}
      <span className="max-w-[5rem] truncate sm:max-w-[10rem]">{children}</span>
    </Link>
  </span>
)

/** Последнее звено — где сотрудник сейчас; не ссылка. */
export const CrumbCurrent: React.FC<{ title?: string; children: React.ReactNode }> = ({ title, children }) => (
  <span className="flex min-w-0 shrink items-center gap-0.5">
    <CrumbSeparator />
    <span className="truncate px-1.5 py-1 pr-2 text-foreground" title={title}>{children}</span>
  </span>
)
