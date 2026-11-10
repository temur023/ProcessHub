import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { FileText, Folder } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { KnowledgeShell } from '@/components/KnowledgeShell'
import { getDepartments, getKnowledgeAccess, type Department } from '@/lib/knowledge'
import { plural } from '@/lib/presentation'

/** Папки документов используют названия, номера и порядок пространства Hub. */
export default function KnowledgeDepartmentsPage() {
  const [departments, setDepartments] = useState<Department[]>()
  const [isAdmin, setIsAdmin] = useState(false)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let active = true
    void Promise.all([getDepartments(), getKnowledgeAccess()]).then(([items, access]) => {
      if (active) { setDepartments(items); setIsAdmin(access.is_platform_admin); setError('') }
    }).catch(cause => { if (active) setError(cause instanceof Error ? cause.message : 'Не удалось загрузить департаменты') })
    return () => { active = false }
  }, [reload])
  return <KnowledgeShell>
    <header className="border-b pb-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Документы департаментов</h1>
      </div>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">Выберите департамент, чтобы открыть должностные инструкции и их редакции.</p>
    </header>
    {error ? <div role="alert" className="mt-6 space-y-3 rounded-lg border p-4 text-sm"><p>{error}</p><Button variant="outline" size="sm" onClick={() => setReload(value => value + 1)}>Повторить</Button></div>
      : !departments ? <div className="mt-8 flex items-center gap-2 text-sm text-muted-foreground"><Spinner className="size-4" /> Загружаем департаменты…</div>
      : departments.length === 0 ? <div className="py-16 text-center"><FileText className="mx-auto size-9 text-muted-foreground" /><h2 className="mt-4 text-lg font-medium">{isAdmin ? 'Папки департаментов ещё не перенесены' : 'Доступ к документам ещё не назначен'}</h2><p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted-foreground">{isAdmin ? 'Департаменты берутся из существующего пространства библиотеки процессов.' : 'Для доступа к инструкциям нужно назначение в вашем департаменте. Обратитесь к ответственному за Process Hub.'}</p></div>
      : <section aria-label="Департаменты" className="mt-6">
        <p className="mb-4 text-sm text-muted-foreground">{plural(departments.length, 'департамент', 'департамента', 'департаментов')}</p>
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{departments.map(department => <li key={department.id}>
          <Link to={`/knowledge/departments/${department.id}`} className="flex h-full min-h-28 items-start gap-3 rounded-2xl border bg-card p-5 transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <Folder className="mt-0.5 size-5 shrink-0 text-blue-950 dark:text-white" />
            <span className="min-w-0 break-words text-sm font-medium leading-6">{department.catalog_number && <span className="mr-2 text-muted-foreground">{department.catalog_number}{' '}</span>}{department.name}</span>
          </Link>
        </li>)}</ul>
      </section>}
  </KnowledgeShell>
}
