/** Пространства и карты — как `backend/app/models/catalog.py`. */
import type { ProcessStage, ProcessStatus } from './process'

/** `space` — пространство (без родителя) или подпространство, `map` — карта процесса. */
export type CatalogKind = 'space' | 'map'

/** Сводка о схеме для карточки: шаги, итоги проверки, кто менял последним. */
export interface ProcessBrief {
  stepCount: number
  errorCount: number
  warningCount: number
  updatedAt: string | null
  /** Автор последней версии; у версий до входа в систему — нет. */
  updatedBy: string | null
  /** Статус паспорта процесса. */
  status: ProcessStatus
  /** Вид модели: AS-IS, TO-BE или не указан. */
  stage: ProcessStage | null
}

export interface CatalogNode {
  id: string
  parentId: string | null
  kind: CatalogKind
  title: string
  position: number
  processId: string | null
  favorite: boolean
  createdAt: string
  pixId: string | null
  /** «3.1»; у пространства номера нет. */
  number: string
  /** Пространство — 1, его подпространства — 2, их карты — 3… */
  level: number
  /** Схема карты есть на сервере. */
  hasProcess: boolean
  spaceCount: number
  mapCount: number
  /** Сводка о схеме карты; у пространств и карт без схемы — нет. */
  process: ProcessBrief | null
}

/** Процесс на сервере, ещё не разложенный ни в одно пространство. */
export interface UnplacedProcess extends ProcessBrief {
  id: string
  name: string
  nodeCount: number
  updatedDate: string
}

export interface Catalog {
  /** Сверху вниз: родитель раньше детей, соседи — по порядку номеров. */
  items: CatalogNode[]
  unplaced: UnplacedProcess[]
}
