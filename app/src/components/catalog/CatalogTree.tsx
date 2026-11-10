import { useMemo, useState } from 'react'
import type React from 'react'
import { Link } from 'react-router'
import { ChevronDown, ChevronRight, Folder, Search, Star, Workflow, X } from 'lucide-react'
import type { CatalogNode } from '@/types/catalog'
import { type CatalogIndex, itemHref, matchesQuery, pathTo } from '@/lib/catalog'
import { CatalogSidebar } from '@/components/catalog/CatalogSidebar'
import { ProcessFilters } from '@/components/catalog/ProcessFilters'
import { filtersActive, matchesFilters, useProcessFilters } from '@/lib/process-filters'

/** Сколько найденного показывать: 515 карт одним списком — уже не поиск. */
const SEARCH_LIMIT = 100

interface CatalogTreeProps {
  index: CatalogIndex
  items: CatalogNode[]
  selectedId?: string
}

/**
 * Дерево пространств и карт с поиском по названию и номеру.
 * Номера «3.1» отображаются перед названием.
 * Раскрыта только ветка открытого элемента, поэтому 557 элементов не рисуются
 * разом.
 */
export const CatalogTree: React.FC<CatalogTreeProps> = ({ index, items, selectedId }) => {
  const roots = index.children.get(null) ?? []
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [revealed, setRevealed] = useState<string | undefined>(undefined)

  // Ветка открытого элемента раскрывается сама — и при переходе по карточкам.
  // Правка состояния прямо при отрисовке, а не в эффекте: так дерево не
  // мелькает свёрнутым перед раскрытием.
  if (selectedId !== revealed) {
    setRevealed(selectedId)
    if (selectedId) {
      const next = new Set(expanded)
      for (const node of pathTo(index, selectedId)) if (node.kind === 'space') next.add(node.id)
      setExpanded(next)
    }
  }

  const filters = useProcessFilters()
  const filtering = filtersActive(filters)
  // Поиск и фильтр сужают один список: дерево уступает место найденному.
  const found = useMemo(() => {
    if (!query.trim() && !filtering) return null
    return items.filter((node) => matchesFilters(node, filters) && matchesQuery(node, query))
  }, [items, query, filters, filtering])

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const row = (node: CatalogNode, depth: number, subtitle?: string) => {
    const kids = index.children.get(node.id) ?? []
    const open = expanded.has(node.id)
    const Icon = node.kind === 'space' ? Folder : Workflow
    return (
      <div
        key={node.id}
        className={`flex items-center gap-0.5 rounded-md text-xs ${
          node.id === selectedId ? 'bg-primary/10 font-medium text-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground'
        }`}
        style={{ paddingLeft: 4 + depth * 14 }}
      >
        {kids.length && !subtitle ? (
          <button
            type="button"
            aria-label={open ? 'Свернуть' : 'Развернуть'}
            aria-expanded={open}
            onClick={() => toggle(node.id)}
            className="flex h-5 w-5 shrink-0 items-center justify-center rounded hover:bg-background"
          >
            {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
        ) : (
          <span className="w-5 shrink-0" />
        )}
        <Link
          to={itemHref(node)}
          title={node.number ? `${node.number} ${node.title}` : node.title}
          aria-current={node.id === selectedId ? 'page' : undefined}
          className="flex min-w-0 flex-1 items-center gap-1.5 py-1 pr-2"
        >
          <Icon className={`h-3.5 w-3.5 shrink-0 ${node.kind === 'space' ? 'text-blue-950 dark:text-white' : node.hasProcess ? 'text-emerald-500' : ''}`} />
          <span className="min-w-0 flex-1">
            <span className="block truncate">
              {node.number && <span className="mr-1 tabular-nums opacity-70">{node.number}</span>}
              {node.title}
            </span>
            {subtitle && <span className="block truncate text-[10px] opacity-70">{subtitle}</span>}
          </span>
          {node.favorite && <Star className="h-3 w-3 shrink-0 fill-amber-400 text-amber-400" />}
        </Link>
      </div>
    )
  }

  const branch = (nodes: CatalogNode[], depth: number): React.ReactNode[] =>
    nodes.flatMap((node) => [
      row(node, depth),
      ...(expanded.has(node.id) ? branch(index.children.get(node.id) ?? [], depth + 1) : []),
    ])

  return (
    <CatalogSidebar ariaLabel="Дерево пространств и карт">
      <div className="shrink-0 border-b px-3 pb-3 pt-6">
        <label className="flex h-8 items-center gap-1.5 rounded-md border bg-background px-2 text-xs">
          <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Поиск процессов: название или номер"
            className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-muted-foreground"
          />
          {query && (
            <button type="button" aria-label="Очистить поиск" onClick={() => setQuery('')}>
              <X className="h-3.5 w-3.5 text-muted-foreground" />
            </button>
          )}
        </label>
        <ProcessFilters items={items} className="mt-2" />
      </div>
      <nav aria-label="Все пространства и карты" className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {found ? (
          <>
            {found.slice(0, SEARCH_LIMIT).map((node) =>
              row(node, 0, pathTo(index, node.id).slice(0, -1).map((p) => p.number || p.title).join(' › ')),
            )}
            <p className="px-2 py-1.5 text-[11px] text-muted-foreground">
              {found.length === 0
                ? filtering ? 'Нет карт с таким статусом и видом модели' : 'Ничего не найдено'
                : found.length > SEARCH_LIMIT
                  ? `Показаны первые ${SEARCH_LIMIT} из ${found.length} — уточните запрос`
                  : `Найдено: ${found.length}`}
            </p>
          </>
        ) : (
          branch(roots, 0)
        )}
      </nav>
    </CatalogSidebar>
  )
}
