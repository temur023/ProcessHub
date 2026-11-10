import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { Building2, FileText, Folder, Search } from 'lucide-react'
import { CatalogSidebar } from '@/components/catalog/CatalogSidebar'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import type { Department } from '@/lib/knowledge'
import { groupDepartments, type CatalogSpace } from '@/lib/departmentGroups'
import { useEffect, useRef, useState } from 'react'

interface DocumentsShellProps {
  children: ReactNode
  departments?: Department[]
  spaces?: CatalogSpace[]
  departmentId?: string
  bankWide?: boolean
  busy?: boolean
}

export function DocumentsShell({ children, departments, spaces = [], departmentId, bankWide, busy }: DocumentsShellProps) {
  const [query, setQuery] = useState('')
  const selectedDepartment = useRef<HTMLAnchorElement>(null)
  useEffect(() => { selectedDepartment.current?.scrollIntoView({ block: 'nearest' }) }, [departmentId, departments, query])
  const search = query.trim().toLocaleLowerCase('ru')
  const visible = departments?.filter(item => `${item.name} ${item.catalog_number}`.toLocaleLowerCase('ru').includes(search))
  const groups = groupDepartments(visible ?? [], spaces)
  const sections = groupDepartments(departments ?? [], spaces).length > 1
  const navItem = 'flex min-w-0 items-start gap-3 rounded-lg px-3 py-3 text-sm leading-5 transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring'
  return <div className="flex min-h-0 min-w-0 flex-1 overflow-hidden">
    <CatalogSidebar ariaLabel="Библиотека документов" preferenceKey="sqb.process-hub.documents-sidebar.collapsed" toggleLabel="панель департаментов" onExpand={() => selectedDepartment.current?.scrollIntoView({ block: 'nearest' })}>
      <div className="px-5 pb-5 pt-6">
        <Link to="/knowledge" aria-disabled={busy || undefined} onClick={event => { if (busy) event.preventDefault() }} className="flex items-center gap-2 text-lg font-semibold tracking-tight"><FileText className="size-5 text-primary" />Документы</Link>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">Единая библиотека банка</p>
      </div>
      <nav className="space-y-1 px-3" aria-label="Области документов">
        <Link to="/knowledge" onClick={event => { if (busy) event.preventDefault() }} aria-current={!departmentId && !bankWide ? 'page' : undefined} className={`${navItem} ${!departmentId && !bankWide ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'}`}><Folder className="mt-0.5 size-4 shrink-0 text-blue-950 dark:text-white" /><span>Департаменты</span></Link>
        <Link to="/knowledge/bank" onClick={event => { if (busy) event.preventDefault() }} aria-current={bankWide ? 'page' : undefined} className={`${navItem} ${bankWide ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'}`}><Building2 className="mt-0.5 size-4 shrink-0" /><span>Общебанковские документы</span></Link>
      </nav>
      <div className="mx-5 mt-5 border-t pt-5">
        <p className="mb-3 text-xs font-medium uppercase tracking-wider text-muted-foreground">{sections ? 'Департаменты и филиалы' : 'Департаменты'}</p>
        <div className="relative"><Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" /><Input aria-label="Найти департамент" placeholder="Название или номер" className="h-9 bg-background pl-9 text-xs" value={query} onChange={event => setQuery(event.target.value)} /></div>
      </div>
      <nav aria-label="Папки департаментов" className="mt-3 min-h-0 flex-1 overflow-y-auto px-3 pb-6">
        {!departments ? <p className="flex items-center gap-2 px-3 py-4 text-xs text-muted-foreground"><Spinner className="size-3.5" />Загружаем папки…</p> : visible?.length === 0 ? <p className="px-3 py-4 text-xs leading-5 text-muted-foreground">{query ? 'Департаменты не найдены' : 'Нет доступных департаментов'}</p> : groups.map(group => <div key={group.id} role="group" aria-label={sections ? group.title : undefined}>{sections && <p className="px-3 pb-1 pt-4 text-[11px] font-medium uppercase tracking-wider text-muted-foreground first:pt-1">{group.title} <span className="tabular-nums">· {group.departments.length}</span></p>}{group.departments.map(item => <Link key={item.id} ref={departmentId === item.id ? selectedDepartment : undefined} to={`/knowledge/departments/${item.id}`} onClick={event => { if (busy) event.preventDefault() }} aria-current={departmentId === item.id ? 'page' : undefined} className={`${navItem} ${departmentId === item.id ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'}`}><Folder className="mt-0.5 size-4 shrink-0 text-blue-950 dark:text-white" /><span className="min-w-0 break-words">{departmentId !== item.id && item.catalog_number && <span className="mr-1.5 text-muted-foreground">{item.catalog_number}</span>}{item.name}</span></Link>)}</div>)}
      </nav>
    </CatalogSidebar>
    <main className="min-h-0 min-w-0 flex-1 overflow-y-auto px-4 py-5 md:px-8 md:py-8">{children}</main>
  </div>
}
