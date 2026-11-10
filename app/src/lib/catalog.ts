import type { CatalogNode } from '@/types/catalog'
import { plural } from '@/lib/presentation'

/** Дерево пространств, разобранное для быстрых переходов. */
export interface CatalogIndex {
  byId: Map<string, CatalogNode>
  /** Дети по родителю (`null` — пространства верхнего уровня), в порядке номеров. */
  children: Map<string | null, CatalogNode[]>
  /** Карта, в которой лежит процесс. */
  byProcess: Map<string, CatalogNode>
}

export function indexCatalog(items: CatalogNode[]): CatalogIndex {
  const byId = new Map<string, CatalogNode>()
  const children = new Map<string | null, CatalogNode[]>()
  const byProcess = new Map<string, CatalogNode>()
  // Бэкенд отдаёт дерево сверху вниз и соседей по порядку — порядок сохраняется.
  for (const item of items) {
    byId.set(item.id, item)
    const siblings = children.get(item.parentId)
    if (siblings) siblings.push(item)
    else children.set(item.parentId, [item])
    if (item.processId) byProcess.set(item.processId, item)
  }
  return { byId, children, byProcess }
}

/** Путь от пространства верхнего уровня до элемента включительно. */
export function pathTo(index: CatalogIndex, id: string): CatalogNode[] {
  const path: CatalogNode[] = []
  let node = index.byId.get(id)
  while (node) {
    path.unshift(node)
    node = node.parentId ? index.byId.get(node.parentId) : undefined
  }
  return path
}

/** Элемент и все его потомки — куда пространство переносить нельзя. */
export function subtreeIds(index: CatalogIndex, id: string): Set<string> {
  const ids = new Set<string>([id])
  const stack = [id]
  while (stack.length) {
    for (const child of index.children.get(stack.pop()!) ?? []) {
      ids.add(child.id)
      stack.push(child.id)
    }
  }
  return ids
}

/** «3.1 Название» — как в дереве PIX. */
export function itemLabel(node: Pick<CatalogNode, 'number' | 'title'>): string {
  return node.number ? `${node.number} ${node.title}` : node.title
}

/** Поиск по названию и по номеру: «3.1» находит 3.1 и всё внутри неё. */
export function matchesQuery(node: CatalogNode, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  if (/^\d+(\.\d+)*\.?$/.test(q)) {
    const number = q.replace(/\.$/, '')
    return node.number === number || node.number.startsWith(`${number}.`)
  }
  return node.title.toLowerCase().includes(q)
}

/** Ссылка на экран элемента: пространство — экран-папка, карта — экран карты. */
export function itemHref(node: Pick<CatalogNode, 'id' | 'kind' | 'processId' | 'hasProcess'>): string {
  if (node.kind === 'space') return `/space/${node.id}`
  return node.hasProcess && node.processId ? `/process/${node.processId}` : `/map/${node.id}`
}

/** «41 подпространство · 515 карт»; пустое пространство — «Пусто». */
export function contentsLabel(node: Pick<CatalogNode, 'spaceCount' | 'mapCount'>): string {
  const parts: string[] = []
  if (node.spaceCount) parts.push(plural(node.spaceCount, 'подпространство', 'подпространства', 'подпространств'))
  if (node.mapCount) parts.push(plural(node.mapCount, 'карта', 'карты', 'карт'))
  return parts.join(' · ') || 'Пусто'
}

export function formatDate(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('ru-RU')
}

