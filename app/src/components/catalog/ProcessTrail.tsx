import type React from 'react'
import { Inbox } from 'lucide-react'
import { Breadcrumbs, CrumbLink, CrumbSeparator } from '@/components/Breadcrumbs'
import { useCatalog } from '@/hooks/use-catalog'
import { itemHref, pathTo } from '@/lib/catalog'

/** Название открытой карты: номер — отдельной меткой, само название — заголовком экрана. */
const MapTitle: React.FC<{ number?: string | null; title: string }> = ({ number, title }) => (
  <span className="flex min-w-0 flex-1 items-center gap-0.5">
    <CrumbSeparator />
    <span className="flex min-w-0 flex-1 items-center gap-2 pl-1.5 pr-2" title={number ? `${number} ${title}` : title}>
      {number && (
        <span className="shrink-0 rounded-md bg-emerald-500/10 px-1.5 py-0.5 text-xs font-semibold tabular-nums text-emerald-700 dark:text-emerald-400">
          {number}
        </span>
      )}
      <span className="truncate text-sm font-semibold text-foreground">{title}</span>
    </span>
  </span>
)

/**
 * Путь к открытой карте: «⌂ › SANOATQURILISHBANK › 3 › 3.1 …» — каждое
 * звено ведёт назад. Стоит в шапке карты одной строкой: длинные звенья
 * обрезаются, полное название — в подсказке.
 */
export const ProcessTrail: React.FC<{ processId: string; name: string }> = ({ processId, name }) => {
  const { index } = useCatalog()
  const map = index?.byProcess.get(processId)
  const path = map && index ? pathTo(index, map.id) : []
  return (
    <Breadcrumbs label="Путь к карте">
      {/* Карта не лежит в пространстве: название берём у самой карты. */}
      {index && !map && (
        <>
          <CrumbSeparator />
          <span className="flex shrink-0 items-center gap-1.5 px-1.5 py-1" title="Без пространства">
            <Inbox className="h-3.5 w-3.5" /> <span className="hidden sm:inline">Без пространства</span>
          </span>
          <MapTitle title={name} />
        </>
      )}
      {path.map((step) =>
        step.kind === 'space' ? (
          <CrumbLink key={step.id} to={itemHref(step)} title={step.title} folder>
            {step.number || step.title}
          </CrumbLink>
        ) : (
          <MapTitle key={step.id} number={step.number} title={step.title} />
        ),
      )}
    </Breadcrumbs>
  )
}
