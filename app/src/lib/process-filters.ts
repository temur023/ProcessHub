import { useSyncExternalStore } from 'react'
import type { CatalogNode } from '@/types/catalog'
import { PROCESS_STAGE_LABELS, PROCESS_STATUS_LABELS, type ProcessStage, type ProcessStatus } from '@/types/process'

/**
 * Фильтр карт библиотеки по статусу паспорта и виду модели.
 *
 * Один на всю библиотеку: выбранный в дереве фильтр действует и на экране
 * пространства и не сбрасывается при переходе к найденной карте. Живёт до
 * закрытия вкладки (sessionStorage): завтра сотрудник начинает с полного списка.
 */
export type StatusFilter = 'all' | ProcessStatus
export type StageFilter = 'all' | ProcessStage | 'unset'
export interface ProcessFilters { status: StatusFilter; stage: StageFilter }

export const STATUS_ORDER: ProcessStatus[] = ['draft', 'in_review', 'approved', 'active', 'archived']
export const STAGE_ORDER: Array<ProcessStage | 'unset'> = ['as_is', 'to_be', 'unset']
export const STAGE_FILTER_LABELS: Record<ProcessStage | 'unset', string> = { ...PROCESS_STAGE_LABELS, unset: 'Не указан' }
export { PROCESS_STATUS_LABELS }

const KEY = 'sqb.process-hub.catalog-filters'
const EMPTY: ProcessFilters = { status: 'all', stage: 'all' }
const subscribers = new Set<() => void>()
let current: ProcessFilters | null = null

function read(): ProcessFilters {
  if (current) return current
  current = EMPTY
  try {
    const saved = JSON.parse(window.sessionStorage.getItem(KEY) ?? 'null') as Partial<ProcessFilters> | null
    if (saved) {
      current = {
        status: saved.status && (saved.status === 'all' || STATUS_ORDER.includes(saved.status)) ? saved.status : 'all',
        stage: saved.stage && (saved.stage === 'all' || STAGE_ORDER.includes(saved.stage)) ? saved.stage : 'all',
      }
    }
  } catch { /* Без хранилища фильтр просто не переживёт перезагрузку. */ }
  return current
}

export function setProcessFilters(next: Partial<ProcessFilters>) {
  current = { ...read(), ...next }
  try { window.sessionStorage.setItem(KEY, JSON.stringify(current)) } catch { /* см. read */ }
  subscribers.forEach((listener) => listener())
}

export const resetProcessFilters = () => setProcessFilters(EMPTY)

export function useProcessFilters(): ProcessFilters {
  return useSyncExternalStore(
    (listener) => { subscribers.add(listener); return () => { subscribers.delete(listener) } },
    read,
    () => EMPTY,
  )
}

export const filtersActive = (filters: ProcessFilters) => filters.status !== 'all' || filters.stage !== 'all'

/**
 * Подходит ли карта под фильтр. Пока фильтр не выбран, подходит всё; выбранный
 * фильтр оставляет только карты со схемой: у карты без схемы нет ни статуса,
 * ни вида модели.
 */
export function matchesFilters(node: CatalogNode, filters: ProcessFilters): boolean {
  if (!filtersActive(filters)) return true
  const brief = node.kind === 'map' ? node.process : null
  if (!brief) return false
  if (filters.status !== 'all' && brief.status !== filters.status) return false
  if (filters.stage === 'unset') return !brief.stage
  return filters.stage === 'all' || brief.stage === filters.stage
}

/**
 * Сколько карт даст каждый вариант при уже выбранном другом фильтре — числа
 * в списках, чтобы не выбирать вслепую вариант с нулём карт.
 */
export function filterCounts(items: CatalogNode[], filters: ProcessFilters) {
  const status = Object.fromEntries(STATUS_ORDER.map((s) => [s, 0])) as Record<ProcessStatus, number>
  const stage = { as_is: 0, to_be: 0, unset: 0 } as Record<ProcessStage | 'unset', number>
  let statusTotal = 0
  let stageTotal = 0
  for (const node of items) {
    const brief = node.kind === 'map' ? node.process : null
    if (!brief) continue
    const stageKey = brief.stage ?? 'unset'
    if (filters.stage === 'all' || filters.stage === stageKey) { status[brief.status] += 1; statusTotal += 1 }
    if (filters.status === 'all' || filters.status === brief.status) { stage[stageKey] += 1; stageTotal += 1 }
  }
  return { status, stage, statusTotal, stageTotal }
}
