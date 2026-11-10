import type { BusinessProcess, Geometry, ProcessEdge, ProcessEdgePoint, ProcessNode } from '@/types/process'
import { isArtifactNode } from '@/types/process'

/**
 * Правки карты на холсте: сдвиг фигуры, изломы линии, новые и удалённые
 * фигуры и связи, ставка дорожки.
 *
 * Каждая правка возвращает новую страницу и ничего не меняет на месте: на
 * этом стоит отмена (Ctrl+Z хранит прежние страницы целиком). Линии заново
 * прокладывает бэкенд — здесь меняются только координаты и связи модели.
 */

/** Шаг сетки холста: фигуры и изломы встают на неё, как в draw.io. */
export const SNAP = 10

export const snap = (value: number): number => Math.round(value / SNAP) * SNAP

/** Дорожка под точкой карты: самая маленькая из содержащих — дорожка внутри пула. */
export function laneAt(lanes: ProcessNode[], x: number, y: number): ProcessNode | undefined {
  let best: ProcessNode | undefined
  for (const lane of lanes) {
    const g = lane.geometry
    if (x < g.x || x > g.x + g.width || y < g.y || y > g.y + g.height) continue
    if (!best || g.width * g.height < best.geometry.width * best.geometry.height) best = lane
  }
  return best
}

const shift = (g: Geometry, dx: number, dy: number): Geometry => ({ ...g, x: g.x + dx, y: g.y + dy })

/**
 * Сдвиг фигуры. Рамка draw.io едет вместе с ней — карта PIX строится по той
 * рамке, и без этого сдвиг остался бы только на холсте. Брошенная в другую
 * дорожку фигура переходит в неё: дорожка — это исполнитель шага.
 */
export function moveNode(page: BusinessProcess, nodeId: string, dx: number, dy: number): BusinessProcess {
  return {
    ...page,
    nodes: page.nodes.map((n) => {
      if (n.id !== nodeId) return n
      const geometry = shift(n.geometry, dx, dy)
      const lane = laneAt(page.lanes, geometry.x + geometry.width / 2, geometry.y + geometry.height / 2)
      return {
        ...n,
        geometry,
        drawioGeometry: n.drawioGeometry ? shift(n.drawioGeometry, dx, dy) : n.drawioGeometry,
        ...(lane ? { laneId: lane.id, laneName: lane.name } : {}),
      }
    }),
  }
}

/**
 * Изломы линии. Пустой список — линию прокладывает бэкенд сам. Изломы draw.io
 * снимаются: иначе карта PIX рисовала бы линию по старым.
 */
export function setEdgePoints(page: BusinessProcess, edgeId: string, points: ProcessEdgePoint[]): BusinessProcess {
  return {
    ...page,
    edges: page.edges.map((e) =>
      e.id === edgeId
        ? { ...e, points: points.map((p) => ({ x: snap(p.x), y: snap(p.y) })), drawioPoints: null }
        : e,
    ),
  }
}

export type NewShape = 'task' | 'gateway' | 'start' | 'end'

/** Что ставит палитра: тип, имя, размер и стиль — как у таких же фигур шаблона. */
const NEW_SHAPES: Record<NewShape, Pick<ProcessNode, 'type' | 'name' | 'style'> & { w: number; h: number }> = {
  task: { type: 'userTask', name: 'Новый шаг', style: 'rounded=1;whiteSpace=wrap;html=1;', w: 140, h: 70 },
  gateway: { type: 'exclusiveGateway', name: 'Условие', style: 'rhombus;', w: 50, h: 50 },
  start: { type: 'startEvent', name: 'Старт', style: 'ellipse;', w: 40, h: 40 },
  end: { type: 'endEvent', name: 'Завершение', style: 'ellipse;', w: 40, h: 40 },
}

const newId = (prefix: string) => `${prefix}-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`

/** Новая фигура с центром в точке карты. */
export function addNode(
  page: BusinessProcess,
  shape: NewShape,
  cx: number,
  cy: number,
): { page: BusinessProcess; node: ProcessNode } {
  const spec = NEW_SHAPES[shape]
  const geometry = { x: snap(cx - spec.w / 2), y: snap(cy - spec.h / 2), width: spec.w, height: spec.h }
  const lane = laneAt(page.lanes, cx, cy)
  const node: ProcessNode = {
    id: newId('node'),
    name: spec.name,
    type: spec.type,
    style: spec.style,
    geometry,
    laneId: lane?.id,
    laneName: lane?.name,
    ...(shape === 'task'
      ? {
          category: 'manual' as const,
          // Время нового шага — условное, пока его не впишут в карточке: на
          // карте часы с цифрой не рисуются, как и у шага без времени из draw.io.
          slaMinutes: 30,
          slaMeasured: false,
          waitMinutes: 0,
          automationPotential: 50,
        }
      : {}),
  }
  return { page: { ...page, nodes: [...page.nodes, node] }, node }
}

/**
 * Связь от фигуры к фигуре. С артефактом (система, документ, примечание) —
 * ассоциация, иначе — поток управления. Повтор той же связи не добавляется.
 */
export function addEdge(
  page: BusinessProcess,
  sourceId: string,
  targetId: string,
): { page: BusinessProcess; edge: ProcessEdge } | null {
  if (sourceId === targetId) return null
  if (page.edges.some((e) => e.sourceId === sourceId && e.targetId === targetId)) return null
  const byId = new Map(page.nodes.map((n) => [n.id, n]))
  const source = byId.get(sourceId)
  const target = byId.get(targetId)
  if (!source || !target) return null
  const artifact = isArtifactNode(source.type) || isArtifactNode(target.type)
  const edge: ProcessEdge = {
    id: newId('edge'),
    name: '',
    kind: artifact ? 'association' : 'sequenceFlow',
    sourceId,
    targetId,
    points: [],
    ...(artifact ? { dashed: true } : {}),
  }
  return { page: { ...page, edges: [...page.edges, edge] }, edge }
}

/** Удаление фигуры вместе с её линиями: связь без конца карта не нарисует. */
export function deleteNode(page: BusinessProcess, nodeId: string): BusinessProcess {
  return {
    ...page,
    nodes: page.nodes.filter((n) => n.id !== nodeId),
    edges: page.edges.filter((e) => e.sourceId !== nodeId && e.targetId !== nodeId),
  }
}

export function deleteEdge(page: BusinessProcess, edgeId: string): BusinessProcess {
  return { ...page, edges: page.edges.filter((e) => e.id !== edgeId) }
}

/** Подпись связи — у ветки шлюза это условие перехода («Ha», «Yo'q»). */
export function setEdgeName(page: BusinessProcess, edgeId: string, name: string): BusinessProcess {
  return { ...page, edges: page.edges.map((e) => (e.id === edgeId ? { ...e, name } : e)) }
}

/** Поля фигуры — из быстрого исправления замечания или карточки. */
export function updateNode(page: BusinessProcess, nodeId: string, patch: Partial<ProcessNode>): BusinessProcess {
  return { ...page, nodes: page.nodes.map((n) => (n.id === nodeId ? { ...n, ...patch } : n)) }
}

/** Ставка часа дорожки; `null` — вернуть ставку по умолчанию. */
export function setLaneRate(page: BusinessProcess, laneId: string, rate: number | null): BusinessProcess {
  return {
    ...page,
    lanes: page.lanes.map((l) => (l.id === laneId ? { ...l, hourlyRate: rate } : l)),
  }
}

/**
 * Процесс с заменённой страницей. Пустой `pageId` — основная карта, иначе —
 * страница-подпроцесс или страница-вариант с этим id.
 */
export function replacePage(process: BusinessProcess, pageId: string, page: BusinessProcess): BusinessProcess {
  if (!pageId) return page
  const swap = (list?: BusinessProcess[]) => list?.map((p) => (p.id === pageId ? page : p))
  return { ...process, subProcesses: swap(process.subProcesses), variants: swap(process.variants) }
}
