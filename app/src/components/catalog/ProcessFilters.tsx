import { useMemo } from 'react'
import { X } from 'lucide-react'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cn } from '@/lib/utils'
import {
  PROCESS_STATUS_LABELS, STAGE_FILTER_LABELS, STAGE_ORDER, STATUS_ORDER,
  filterCounts, filtersActive, resetProcessFilters, setProcessFilters, useProcessFilters,
  type StageFilter, type StatusFilter,
} from '@/lib/process-filters'
import type { CatalogNode } from '@/types/catalog'

function Option({ value, label, count }: { value: string; label: string; count: number }) {
  return (
    <SelectItem value={value} disabled={count === 0} className="text-xs">
      <span className="flex w-full min-w-36 items-center justify-between gap-4">
        <span>{label}</span>
        <span className="tabular-nums text-muted-foreground">{count}</span>
      </span>
    </SelectItem>
  )
}

/**
 * Статус и вид модели — два списка с числом карт у каждого варианта. Варианты
 * без карт видны, но недоступны: сразу понятно, что, например, утверждённых
 * TO-BE пока нет.
 */
export function ProcessFilters({ items, className }: { items: CatalogNode[]; className?: string }) {
  const filters = useProcessFilters()
  const counts = useMemo(() => filterCounts(items, filters), [items, filters])
  const active = filtersActive(filters)
  const trigger = (on: boolean) => cn('h-8 min-w-0 flex-1 text-xs', on && 'border-primary/60 bg-primary/5 font-medium text-foreground')

  return (
    <div role="group" aria-label="Фильтр карт" className={cn('flex items-center gap-1.5', className)}>
      <Select value={filters.status} onValueChange={(status) => setProcessFilters({ status: status as StatusFilter })}>
        <SelectTrigger size="sm" aria-label="Статус процесса" className={trigger(filters.status !== 'all')}>
          <SelectValue>{filters.status === 'all' ? 'Любой статус' : PROCESS_STATUS_LABELS[filters.status]}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <Option value="all" label="Любой статус" count={counts.statusTotal} />
          {STATUS_ORDER.map((status) => <Option key={status} value={status} label={PROCESS_STATUS_LABELS[status]} count={counts.status[status]} />)}
        </SelectContent>
      </Select>
      <Select value={filters.stage} onValueChange={(stage) => setProcessFilters({ stage: stage as StageFilter })}>
        <SelectTrigger size="sm" aria-label="Вид модели" className={trigger(filters.stage !== 'all')}>
          <SelectValue>{filters.stage === 'all' ? 'Любой вид' : STAGE_FILTER_LABELS[filters.stage]}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <Option value="all" label="Любой вид модели" count={counts.stageTotal} />
          {STAGE_ORDER.map((stage) => <Option key={stage} value={stage} label={STAGE_FILTER_LABELS[stage]} count={counts.stage[stage]} />)}
        </SelectContent>
      </Select>
      {active && (
        <button
          type="button"
          onClick={resetProcessFilters}
          aria-label="Сбросить фильтр"
          title="Сбросить фильтр"
          className="flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X className="size-4" />
        </button>
      )}
    </div>
  )
}
