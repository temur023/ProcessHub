import { useEffect, useMemo, useState } from 'react'
import type React from 'react'
import { Link } from 'react-router'
import { Search, Workflow } from 'lucide-react'
import { Spinner } from '@/components/ui/spinner'
import { searchSteps, type SearchField, type StepHit } from '@/lib/api'
import { plural } from '@/lib/presentation'

/** Пауза после ввода: запрос уходит, когда сотрудник перестал печатать. */
const DELAY = 300

const FIELDS: Array<{ id: SearchField; label: string; placeholder: string }> = [
  { id: 'step', label: 'Шаг', placeholder: 'Название шага: «tekshirish», «договор»…' },
  { id: 'role', label: 'Роль', placeholder: 'Роль (дорожка): «MXD», «менеджер»…' },
  { id: 'system', label: 'ИТ-система', placeholder: 'Система: «EHA», «iABS», «CROBS»…' },
]

/**
 * Поиск по шагам всех карт: где встречается шаг, что делает роль во всех
 * процессах, где используется система. Найденный шаг открывает карту на нём.
 */
export const MapSearch: React.FC = () => {
  const [field, setField] = useState<SearchField>('step')
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<{ key: string; total: number; hits: StepHit[]; error: string | null } | null>(null)
  const needle = query.trim()
  const key = `${field}:${needle}`

  useEffect(() => {
    if (needle.length < 2) return
    const timer = window.setTimeout(() => {
      searchSteps(needle, field)
        .then((found) => setResult({ key, ...found, error: null }))
        .catch((err) => setResult({ key, total: 0, hits: [], error: err instanceof Error ? err.message : String(err) }))
    }, DELAY)
    return () => window.clearTimeout(timer)
  }, [needle, field, key])

  const current = result?.key === key ? result : null
  const groups = useMemo(() => {
    const byMap = new Map<string, { name: string; hits: StepHit[] }>()
    for (const hit of current?.hits ?? []) {
      const group = byMap.get(hit.processId) ?? { name: hit.processName, hits: [] }
      group.hits.push(hit)
      byMap.set(hit.processId, group)
    }
    return [...byMap.entries()]
  }, [current])
  const active = FIELDS.find((f) => f.id === field)!

  return (
    <section className="mt-5">
      <div className="border-b pb-3">
        <h2 className="text-sm font-semibold">Поиск по картам</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Шаги всех загруженных карт: где встречается шаг, что делает роль, где работает система.
        </p>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg border p-0.5" role="radiogroup" aria-label="Где искать">
          {FIELDS.map((f) => (
            <button
              key={f.id}
              type="button"
              role="radio"
              aria-checked={field === f.id}
              onClick={() => setField(f.id)}
              className={`rounded-md px-2.5 py-1 text-xs ${field === f.id ? 'bg-muted font-medium' : 'text-muted-foreground hover:text-foreground'}`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <label className="flex h-9 min-w-0 flex-1 items-center gap-1.5 rounded-md border bg-background px-2 text-sm sm:min-w-72">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={active.placeholder}
            aria-label={`Поиск: ${active.label}`}
            className="min-w-0 flex-1 bg-transparent outline-none"
          />
        </label>
      </div>

      <div className="mt-4">
        {needle.length < 2 ? (
          <p className="text-xs text-muted-foreground">Введите хотя бы две буквы.</p>
        ) : !current ? (
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <Spinner className="h-3.5 w-3.5" /> Ищем по всем картам…
          </p>
        ) : current.error ? (
          <p className="text-xs text-rose-600 dark:text-rose-400">{current.error}</p>
        ) : current.total === 0 ? (
          <p className="text-xs text-muted-foreground">Ничего не найдено.</p>
        ) : (
          <>
            <p className="mb-2 text-xs text-muted-foreground">
              {current.total > current.hits.length
                ? `Найдено ${plural(current.total, 'шаг', 'шага', 'шагов')} · показаны первые ${current.hits.length} (на ${plural(groups.length, 'карте', 'картах', 'картах')}) — уточните запрос`
                : `${plural(current.total, 'шаг', 'шага', 'шагов')} на ${plural(groups.length, 'карте', 'картах', 'картах')}`}
            </p>
            <ul className="space-y-3">
              {groups.map(([processId, group]) => (
                <li key={processId} className="rounded-lg border">
                  <div className="flex items-center gap-2 border-b bg-muted/30 px-3 py-1.5">
                    <Workflow className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
                    <Link to={`/process/${processId}`} className="min-w-0 flex-1 truncate text-xs font-medium hover:underline" title={group.name}>
                      {group.name}
                    </Link>
                    <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">{group.hits.length}</span>
                  </div>
                  <ul className="divide-y">
                    {group.hits.map((hit) => (
                      <li key={`${hit.pageId}:${hit.nodeId}`}>
                        <Link
                          to={`/process/${processId}?node=${encodeURIComponent(hit.nodeId)}${hit.pageId ? `&page=${encodeURIComponent(hit.pageId)}` : ''}`}
                          className="block px-3 py-1.5 hover:bg-muted/50"
                        >
                          <span className="block truncate text-xs">{hit.nodeName || 'Без названия'}</span>
                          <span className="block truncate text-[11px] text-muted-foreground">
                            {[hit.pageId ? `стр. «${hit.pageName}»` : '', hit.laneName, hit.system].filter(Boolean).join(' · ')}
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </section>
  )
}
