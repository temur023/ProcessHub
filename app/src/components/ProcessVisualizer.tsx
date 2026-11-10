import React, { useState, useMemo, useRef, useEffect, useCallback, useImperativeHandle } from 'react'
import {
  ZoomIn, ZoomOut, Maximize2, Minimize2,
  AlertTriangle, Info, Search, Grid, X,
  ChevronLeft, ChevronRight, Crosshair, XCircle, Loader2, ServerOff,
  Pencil, PenTool, Undo2, Redo2, Trash2, Square, Diamond, Circle, CircleDot, RotateCcw,
  type LucideIcon,
} from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { IssueQuickFix } from '@/components/IssueQuickFix'
import { useIsDark } from '@/hooks/use-dark-mode'
import type {
  BusinessProcess, CanvasLayout, LabelBox, NodeType, ProcessEdgePoint, ProcessNode, ProcessValidation,
} from '@/types/process'
import { NODE_TYPE_LABELS, isGatewayNode, isTaskNode } from '@/types/process'
import { fetchCanvasLayout } from '@/lib/api'
import { formatDuration, isGeneratedName } from '@/lib/presentation'
import {
  type NewShape, addEdge, addNode, deleteEdge, deleteNode, moveNode, setEdgePoints, snap,
} from '@/lib/map-edit'
import { downloadMapImage } from '@/lib/map-image'

/**
 * Наводка холста на фигуры, о которых говорит замечание проверки импорта.
 *
 * Замечание без адресата бесполезно: «Фигуры расширены под подпись: 226» не
 * подсказывает сотруднику, где смотреть. Клик по строке отчёта отправляет сюда
 * список фигур, холст подводит к ним камеру и подсвечивает их до тех пор, пока
 * сотрудник сам не закроет подсказку.
 */
export interface CanvasFocus {
  /** Фигуры замечания в порядке обхода. */
  nodeIds: string[]
  /**
   * Растёт при каждом клике по строке отчёта. Без него повторный клик по уже
   * наведённому замечанию ничего не делал бы: список фигур тот же самый.
   */
  nonce: number
  /** Само замечание — его текст показывается карточкой над холстом. */
  issue?: ProcessValidation
}

/** Что холст делает по просьбе снаружи: «Скачать» в шапке карты сохраняет картинку. */
export interface ProcessVisualizerHandle {
  downloadImage: (format: 'svg' | 'png') => void
}

interface ProcessVisualizerProps {
  ref?: React.Ref<ProcessVisualizerHandle>
  process: BusinessProcess
  onSelectNode: (node: ProcessNode) => void
  selectedNodeId?: string
  /** Замечание, на фигуры которого надо навести карту. */
  focus?: CanvasFocus
  /** Сотрудник закрыл подсказку: подсветку снимаем. */
  onClearFocus?: () => void
  /**
   * Правка страницы на холсте: сдвиг фигуры, изломы линии, новые и удалённые
   * фигуры и связи. Без него холст только показывает карту.
   */
  onEdit?: (page: BusinessProcess) => void
  onUndo?: () => void
  onRedo?: () => void
  canUndo?: boolean
  canRedo?: boolean
  /** Открыть карту во встроенном draw.io — кнопка рядом с «Редактировать». */
  onOpenDrawio?: () => void
}

/** Что выделено в режиме правки: фигура или линия. */
type EditSelection = { kind: 'node' | 'edge'; id: string }

/** Палитра режима правки: что можно поставить на карту. */
const NEW_SHAPE_BUTTONS: { shape: NewShape; icon: LucideIcon; label: string }[] = [
  { shape: 'task', icon: Square, label: 'Шаг' },
  { shape: 'gateway', icon: Diamond, label: 'Шлюз' },
  { shape: 'start', icon: Circle, label: 'Старт' },
  { shape: 'end', icon: CircleDot, label: 'Финиш' },
]

const GRID_MINOR = 10
const GRID_MAJOR = 100
const MIN_ZOOM = 0.08
const MAX_ZOOM = 3.0
const LANE_HEAD_DEFAULT = 44
const FONT = '"Helvetica Neue", Helvetica, Arial, sans-serif'
/** Шаг от двух часов и дольше подсвечивается как узкое место. */
const SLOW_STEP_MINUTES = 120

function laneHeaderWidth(lane: ProcessNode): number {
  const m = parseStyleMap(lane.style || '')
  const raw = m['startsize']
  if (raw) {
    const n = Number(raw)
    if (Number.isFinite(n) && n > 0) return Math.max(18, Math.min(80, Math.round(n)))
  }
  // эвристика: узбекские swimlane обычно 26-80
  const low = (lane.style || '').toLowerCase()
  if (low.includes('swimlane')) return LANE_HEAD_DEFAULT
  return LANE_HEAD_DEFAULT
}

/**
 * Палитра холста.
 *
 * Фигуры рисуются атрибутами SVG, а не классами Tailwind, поэтому `dark:`
 * здесь не работает и цвет приходится выбирать в коде. Раньше палитра была
 * одна — всегда тёмная: в светлой теме карта оставалась чёрной плитой посреди
 * белого интерфейса, и на печати регламента получалась заливка на весь лист.
 *
 * Роли в обеих темах совпадают, различаются только значения, поэтому вся
 * остальная отрисовка ничего не знает о теме и просто берёт `C.<роль>`.
 */
type CanvasPalette = {
  canvas: string
  gridMinor: string
  gridMajor: string
  laneLine: string
  laneHead: string
  laneText: string
  taskFill: string
  taskStroke: string
  taskText: string
  rpaFill: string
  rpaStroke: string
  badFill: string
  badStroke: string
  edge: string
  /** Пунктир без собственного цвета и подписи шлюзов — тише основного потока. */
  edgeSoft: string
  edgeHi: string
  labelBg: string
  labelStroke: string
  gwStroke: string
  start: string
  endOk: string
  endNo: string
  /** Подпись под событием. */
  caption: string
  storeFill: string
  storeStroke: string
  docFill: string
  docStroke: string
  timerStroke: string
  noteStroke: string
  /** Кольцо вокруг фигуры, на которую навела проверка импорта. */
  focusRing: string
}

const DARK_CANVAS: CanvasPalette = {
  canvas: '#1a1a1a',
  gridMinor: 'rgba(255,255,255,0.05)',
  gridMajor: 'rgba(255,255,255,0.12)',
  laneLine: 'rgba(255,255,255,0.42)',
  laneHead: '#0f0f0f',
  laneText: '#f3f3f3',
  taskFill: '#141414',
  taskStroke: '#f2f2f2',
  taskText: '#f7f7f7',
  rpaFill: '#101c16',
  rpaStroke: '#3dd68c',
  badFill: '#231c0c',
  badStroke: '#e8b84a',
  edge: '#dedede',
  edgeSoft: '#e8e8e8',
  edgeHi: '#7db7ff',
  labelBg: '#1a1a1a',
  labelStroke: '#3a3a3a',
  gwStroke: '#e6b422',
  start: '#f3f3f3',
  endOk: '#5ee08a',
  endNo: '#ff6b6b',
  caption: '#d8d8d8',
  // Артефакты и промежуточные события (2-ILOVA)
  storeFill: '#10201c',
  storeStroke: '#4fd1c5',
  docFill: '#1b1a10',
  docStroke: '#d7c56a',
  timerStroke: '#c9a227',
  noteStroke: '#9aa0a6',
  focusRing: '#ff9f0a',
}

/**
 * Светлый холст держится ближе к тому, как та же схема выглядит в draw.io и
 * в PIX Процессной студии: белый лист, тёмный контур, цветом выделены только
 * роботы, узкие места и артефакты.
 */
const LIGHT_CANVAS: CanvasPalette = {
  canvas: '#ffffff',
  gridMinor: 'rgba(15,23,42,0.055)',
  gridMajor: 'rgba(15,23,42,0.13)',
  laneLine: '#94a3b8',
  laneHead: '#f1f5f9',
  laneText: '#0f172a',
  taskFill: '#ffffff',
  taskStroke: '#334155',
  taskText: '#0f172a',
  rpaFill: '#ecfdf5',
  rpaStroke: '#059669',
  badFill: '#fffbeb',
  badStroke: '#d97706',
  edge: '#475569',
  edgeSoft: '#64748b',
  edgeHi: '#2563eb',
  labelBg: '#ffffff',
  labelStroke: '#cbd5e1',
  gwStroke: '#b45309',
  start: '#0f172a',
  endOk: '#059669',
  endNo: '#dc2626',
  caption: '#334155',
  storeFill: '#ecfeff',
  storeStroke: '#0e7490',
  docFill: '#fefce8',
  docStroke: '#a16207',
  timerStroke: '#b45309',
  noteStroke: '#64748b',
  focusRing: '#ea580c',
}

type Box = { x: number; y: number; w: number; h: number; cx: number; cy: number }

function rawBox(n: ProcessNode): Box {
  const w = Math.max(n.geometry.width || 8, 8)
  const h = Math.max(n.geometry.height || 8, 8)
  return {
    x: n.geometry.x,
    y: n.geometry.y,
    w,
    h,
    cx: n.geometry.x + w / 2,
    cy: n.geometry.y + h / 2,
  }
}

function parseStyleMap(style: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const part of (style || '').split(';')) {
    if (!part) continue
    const eq = part.indexOf('=')
    if (eq < 0) {
      out[part.trim().toLowerCase()] = '1'
      continue
    }
    out[part.slice(0, eq).trim().toLowerCase()] = part.slice(eq + 1).trim()
  }
  return out
}

function styleNum(map: Record<string, string>, key: string, fallback = 0): number {
  const n = Number(map[key])
  return Number.isFinite(n) ? n : fallback
}

function wrapText(text: string, maxChars: number): string[] {
  if (!text) return []
  const words = text.split(/\s+/)
  const lines: string[] = []
  let cur = ''
  for (const w of words) {
    if ((cur + ' ' + w).trim().length <= maxChars) {
      cur = (cur + ' ' + w).trim()
    } else {
      if (cur) lines.push(cur)
      cur = w.length > maxChars ? w.slice(0, maxChars - 1) + '\u2026' : w
    }
  }
  if (cur) lines.push(cur)
  return lines
}

function fitBoxText(text: string, width: number, height: number, maxLines = 3): { lines: string[]; fontSize: number } {
  const innerW = Math.max(20, width)
  const innerH = Math.max(14, height)
  for (let fs = 12; fs >= 8; fs--) {
    const maxChars = Math.max(6, Math.floor(innerW / (fs * 0.56)))
    const lines = wrapText(text, maxChars).slice(0, maxLines)
    if (lines.length * (fs + 3) <= innerH) return { lines, fontSize: fs }
  }
  const fs = 8
  const maxChars = Math.max(6, Math.floor(innerW / (fs * 0.56)))
  return { lines: wrapText(text, maxChars).slice(0, maxLines), fontSize: fs }
}

/**
 * Примечание, которое в draw.io нарисовано прямоугольной рамкой, а не скобкой,
 * листом или выноской. Чаще всего это название процесса над картой: рамка во
 * всю ширину пула, кегль 72, жирный. Простой текст (`text;`) рамки не имеет,
 * если ему не задан цвет обводки.
 */
function isFramedNote(st: Record<string, string>): boolean {
  if (st.image) return false
  if (st.text) return Boolean(st.strokecolor) && st.strokecolor !== 'none'
  return (!st.shape || st.shape === 'mxgraph.bpmn.task2') && st.strokecolor !== 'none'
}

/**
 * Текст рамки кеглем из стиля draw.io; не влез — кегль уменьшается, но не
 * мельче 8. Подгонка шагов (`fitBoxText`) кегль выше 12 не берёт, и название
 * процесса кеглем 72 печаталось мелкой строкой посреди пустой рамки.
 */
function fitStyledText(text: string, width: number, height: number, fontSize: number, bold: boolean): { lines: string[]; fontSize: number } {
  let fs = Math.max(8, fontSize)
  for (;;) {
    const maxChars = Math.max(6, Math.floor(width / (fs * (bold ? 0.6 : 0.56))))
    const lines = wrapText(text, maxChars)
    if (lines.length * fs * 1.2 <= height || fs === 8) return { lines, fontSize: fs }
    fs = Math.max(8, Math.floor(fs * 0.85))
  }
}

/**
 * Подпись фигуры на холсте: имя аналитика, но не выдумка платформы.
 *
 * Безымянному листу импорт даёт имя «Документ», пустому цилиндру —
 * «Информационная система», пустому ромбу — «Условие». В регламенте и в
 * отчётах такое имя нужно: по нему фигуру находят в списке. На карте оно
 * лишнее — в draw.io лист нарисован чистым, и надписи там нет. Выгрузки это
 * уже соблюдают (`map_label` в бэкенде), холст расходился с ними.
 *
 * У задач имя остаётся любым: пустой прямоугольник хуже условного названия.
 */
function shapeCaption(node: ProcessNode): string {
  const name = (node.name || '').trim()
  return isGeneratedName(name) ? '' : name
}

/** Кегль выносных подписей на холсте, px. */
const CAPTION_FONT = 10
/** Межстрочный интервал выносных подписей, px. */
const CAPTION_LINE = CAPTION_FONT + 2

/**
 * Строки подписи, уложенные в ширину её рамки — все до одной: высоту рамки
 * бэкенд уже посчитал под весь текст, и обрезать подпись незачем.
 */
function captionLines(text: string, width: number): string[] {
  return wrapText(text, Math.max(6, Math.floor((width - 4) / (CAPTION_FONT * 0.56))))
}

/** Рамка, которую текст подписи занимает внутри своей рамки (центр совпадает). */
function captionExtent(lines: string[], box: LabelBox): { x: number; y: number; w: number; h: number } {
  const longest = Math.max(0, ...lines.map((l) => l.length))
  const w = Math.min(box.width, longest * CAPTION_FONT * 0.58 + 8)
  const h = lines.length * CAPTION_LINE + 4
  return { x: box.x + box.width / 2 - w / 2, y: box.y + box.height / 2 - h / 2, w, h }
}

function fitLaneLabel(name: string, laneHeight: number): { lines: string[]; fontSize: number } {
  const full = name.replace(/\s+/g, ' ').trim()
  const avail = Math.max(48, laneHeight - 20)
  for (let fs = 13; fs >= 7; fs--) {
    if (full.length * fs * 0.56 <= avail) return { lines: [full], fontSize: fs }
  }
  const fs = 7
  const maxChars = Math.max(8, Math.floor(avail / (fs * 0.56)))
  const lines = wrapText(full, maxChars).slice(0, 2)
  return { lines, fontSize: fs }
}

/**
 * Длительность у значка часов. Формат общий с выгрузкой (`bpmn-export`):
 * холст и файл, открытый в Процессной студии, обязаны показывать шагу одно
 * и то же время одними и теми же словами.
 */
function slaLabel(mins?: number): string {
  return formatDuration(mins)
}

/**
 * Значок внутри ромба шлюза — тот же, что рисует draw.io и ждёт bpmn.io.
 *
 * Раньше всё, кроме параллельного, помечалось крестиком, а сложный шлюз вообще
 * приходил на холст плюсом: аналитик рисовал звёздочку, а видел «И». Значок —
 * это и есть тип шлюза, подменять его нельзя.
 */
function gatewayMarker(type: NodeType, cx: number, cy: number, s: number, color: string): React.ReactNode {
  const r = s * 0.52
  const w = Math.max(1.6, s * 0.13)
  if (type === 'inclusiveGateway') {
    return <circle cx={cx} cy={cy} r={r} fill="none" stroke={color} strokeWidth={w * 1.2} />
  }
  if (type === 'complexGateway') {
    // Шестилучевая звёздочка: три отрезка через центр под 0°, 60° и 120°.
    return (
      <g stroke={color} strokeWidth={w} strokeLinecap="round">
        {[0, 60, 120].map((deg) => {
          const a = (deg * Math.PI) / 180
          const dx = Math.cos(a) * r
          const dy = Math.sin(a) * r
          return <line key={deg} x1={cx - dx} y1={cy - dy} x2={cx + dx} y2={cy + dy} />
        })}
      </g>
    )
  }
  if (type === 'parallelGateway') {
    return (
      <g stroke={color} strokeWidth={w} strokeLinecap="round">
        <line x1={cx - r} y1={cy} x2={cx + r} y2={cy} />
        <line x1={cx} y1={cy - r} x2={cx} y2={cy + r} />
      </g>
    )
  }
  const d = r * 0.78
  return (
    <g stroke={color} strokeWidth={w} strokeLinecap="round">
      <line x1={cx - d} y1={cy - d} x2={cx + d} y2={cy + d} />
      <line x1={cx - d} y1={cy + d} x2={cx + d} y2={cy - d} />
    </g>
  )
}

/** Значок и цвет уровня замечания в карточке наводки. */
const FOCUS_LEVEL_STYLE: Record<ProcessValidation['level'], { text: string; icon: React.ReactNode }> = {
  error:   { text: 'text-red-600 dark:text-red-400',   icon: <XCircle className="h-4 w-4" /> },
  warning: { text: 'text-amber-600 dark:text-amber-400', icon: <AlertTriangle className="h-4 w-4" /> },
  info:    { text: 'text-sky-600 dark:text-sky-400',   icon: <Info className="h-4 w-4" /> },
}

/** Максимальный масштаб, до которого холст «подъезжает» к фигуре замечания. */
const FOCUS_MAX_ZOOM = 1.1
/** Поля вокруг подсвеченной группы при наводке камеры, px экрана. */
const FOCUS_PADDING = 90
/** Больше этой доли холста карточка замечания занять не может. */
const FOCUS_CARD_MAX_SHARE = 0.45

/**
 * Раскладки страниц, уже полученные за этот сеанс, — по ключу раскладки.
 *
 * Вернулись на карту — линии на месте сразу, без запроса. Бэкенд и сам
 * хранит посчитанное (``canvas_cache``), но и готовый ответ — это мегабайты
 * страницы туда и сотни килобайт обратно. Держим немного: страница в 1600
 * фигур — это заметная память.
 */
const LAYOUT_CACHE_SIZE = 12
const layoutCache = new Map<string, CanvasLayout>()

function rememberLayout(key: string, layout: CanvasLayout) {
  layoutCache.delete(key)
  layoutCache.set(key, layout)
  while (layoutCache.size > LAYOUT_CACHE_SIZE) layoutCache.delete(layoutCache.keys().next().value!)
}

export const ProcessVisualizer: React.FC<ProcessVisualizerProps> = ({
  process,
  onSelectNode,
  selectedNodeId,
  focus,
  onClearFocus,
  onEdit,
  onUndo,
  onRedo,
  canUndo = false,
  canRedo = false,
  onOpenDrawio,
  ref,
}) => {
  const [zoom, setZoom]                 = useState(0.7)
  const [showGrid, setShowGrid]         = useState(true)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [searchQuery, setSearchQuery]   = useState('')
  const [panPos, setPanPos]             = useState({ x: 24, y: 24 })
  const [isPanning, setIsPanning]       = useState(false)
  /** Какая по счёту фигура замечания сейчас в центре: по ней листает карточка. */
  const [focusIndex, setFocusIndex]     = useState(0)
  /** Сколько холста снизу занимает карточка замечания — меряем, а не угадываем. */
  const [cardHeight, setCardHeight]     = useState(0)
  const cardRef  = useRef<HTMLDivElement>(null)
  /** Рамка, к которой сейчас подведена камера: нужна при изменении размера. */
  const focusBoxRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null)
  const panRef   = useRef({ ox: 0, oy: 0, px: 0, py: 0 })
  const wrapRef  = useRef<HTMLDivElement>(null)
  /**
   * Синхронная копия положения холста. Зум «к точке» должен пересчитать сдвиг
   * по тому масштабу, что виден прямо сейчас, а `zoom` из состояния во время
   * серии событий колеса отстаёт на кадр — карта уезжала бы из-под курсора.
   */
  const viewRef  = useRef({ zoom, x: panPos.x, y: panPos.y })
  /** Активные пальцы на холсте — для панорамы одним и щипка двумя. */
  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const pinchRef = useRef<{ dist: number; zoom: number } | null>(null)

  // ── Режим правки ────────────────────────────────────────────────────────
  const [editMode, setEditMode]         = useState(false)
  /** Правка включена и холсту есть куда её отдать. */
  const editing = Boolean(onEdit) && editMode
  const [editSel, setEditSel]           = useState<EditSelection | null>(null)
  /** Фигуру тянут: рамка выделения прячется, пока её не отпустят. */
  const [isDraggingNode, setIsDraggingNode] = useState(false)
  /** Изломы линии, которую сейчас тянут, — рисуются поверх, пока не отпустили. */
  const [bendDrag, setBendDrag] = useState<{
    edgeId: string; points: ProcessEdgePoint[]; index: number; moved: boolean
    /**
     * Тянут не излом, а отрезок между двумя изломами: он ходит поперёк себя
     * целиком, и линия остаётся из прямых углов, как её рисует draw.io.
     */
    segment?: { axis: 'x' | 'y' }
  } | null>(null)
  /** Новая связь, которую тянут от фигуры: конец идёт за указателем. */
  const [connectDrag, setConnectDrag] = useState<{ sourceId: string; x: number; y: number } | null>(null)
  /**
   * Сдвиг фигуры идёт мимо React: смещение пишется прямо в `transform` её
   * группы. Иначе каждый шаг мыши пересобирал бы всю карту — на «1.53» это
   * 200 мс на кадр. В модель сдвиг попадает один раз, когда фигуру отпустили.
   */
  const nodeDragRef = useRef<{
    id: string; el: Element; ox: number; oy: number; dx: number; dy: number; moved: boolean
  } | null>(null)
  /** Указатель, который держит правку (сдвиг, излом, связь), — не панораму. */
  const editPointer = useRef<number | null>(null)
  /**
   * На чём нажали. Указатель захвачен холстом, и двойной клик приходит уже
   * на холст, а не на ручку или фигуру под ним.
   */
  const pressedRef = useRef<Element | null>(null)
  const sceneSvgRef = useRef<SVGSVGElement>(null)

  const isDark = useIsDark()
  const C = isDark ? DARK_CANVAS : LIGHT_CANVAS

  /** Единственная точка, через которую меняется положение холста. */
  const setView = useCallback((next: { zoom: number; x: number; y: number }) => {
    viewRef.current = next
    setZoom(next.zoom)
    setPanPos({ x: next.x, y: next.y })
  }, [])

  /**
   * Масштабирование с якорем: точка под курсором (или под серединой щипка)
   * остаётся на месте. Без якоря карта при каждом повороте колеса уползает
   * к левому верхнему углу, и на большой схеме нужную область приходится
   * догонять перетаскиванием.
   */
  const zoomTo = useCallback(
    (nextZoom: number, clientX?: number, clientY?: number) => {
      const { zoom: z, x, y } = viewRef.current
      const next = +Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoom)).toFixed(3)
      if (next === z) return
      const rect = wrapRef.current?.getBoundingClientRect()
      if (!rect || clientX == null || clientY == null) {
        // Без якоря сохраняем центр видимой области.
        const cx = (rect?.width ?? 0) / 2
        const cy = (rect?.height ?? 0) / 2
        setView({ zoom: next, x: cx - ((cx - x) * next) / z, y: cy - ((cy - y) * next) / z })
        return
      }
      const px = clientX - rect.left
      const py = clientY - rect.top
      setView({ zoom: next, x: px - ((px - x) * next) / z, y: py - ((py - y) * next) / z })
    },
    [setView],
  )

  const bounds = useMemo(() => {
    let minX = Infinity
    let minY = Infinity
    let maxX = -Infinity
    let maxY = -Infinity
    const bump = (x: number, y: number, w: number, h: number) => {
      minX = Math.min(minX, x)
      minY = Math.min(minY, y)
      maxX = Math.max(maxX, x + w)
      maxY = Math.max(maxY, y + h)
    }
    // Ланес и ноды уже в абсолютных координатах (с учетом startSize)
    for (const l of process.lanes) bump(l.geometry.x - 8, l.geometry.y - 8, l.geometry.width + 16, l.geometry.height + 16)
    for (const n of process.nodes) {
      const padY = n.type === 'startEvent' || n.type === 'endEvent' ? 22 : 18
      const padW = 16
      bump(n.geometry.x - 8, n.geometry.y - padY, (n.geometry.width || 140) + padW, (n.geometry.height || 60) + padY * 2)
    }
    // Учитываем заголовки дорожек и внешние отступы для huge диаграмм (4700x2000)
    for (const e of process.edges) {
      for (const p of e.points) bump(p.x - 4, p.y - 4, 8, 8)
    }
    if (!Number.isFinite(minX)) {
      minX = 0
      minY = 0
      maxX = 800
      maxY = 500
    }
    // Минимальные размеры и padding для центрирования
    const w = Math.max(maxX - minX + 24, 400)
    const h = Math.max(maxY - minY + 24, 300)
    return { minX: minX - 12, minY: minY - 12, w, h }
  }, [process])

  const laneLabels = useMemo(() => {
    const map = new Map<string, { lines: string[]; fontSize: number }>()
    for (const lane of process.lanes) {
      map.set(lane.id, fitLaneLabel(lane.name, lane.geometry.height))
    }
    return map
  }, [process.lanes])

  const nodeBoxes = useMemo(() => {
    const map = new Map<string, Box>()
    for (const n of process.nodes) map.set(n.id, rawBox(n))
    return map
  }, [process.nodes])

  /**
   * Линии и подписи карты — с бэкенда, тем же расчётом, что уходит в .bpmn.
   *
   * Своего трассировщика у холста больше нет. Пока он был, карта на экране
   * расходилась с выгрузкой: по замеру приёмки форма линий отличалась у
   * 17–31 % связей, а подписи веток шлюза ложились друг на друга. Ключ — всё,
   * от чего зависит раскладка: правка описания шага её не меняет, и линии не
   * пропадают с холста на время нового запроса.
   */
  const layoutKey = useMemo(
    () =>
      JSON.stringify([
        process.id,
        process.nodes.map((n) => [
          n.id, n.type, n.name, n.style, n.laneId, n.geometry,
          n.slaMeasured, n.slaMinutes, n.waitMinutes,
        ]),
        process.edges,
        process.lanes.map((l) => [l.id, l.name, l.style, l.geometry]),
      ]),
    [process.id, process.nodes, process.edges, process.lanes],
  )
  const [layoutState, setLayoutState] = useState<{
    key: string
    processId: string
    layout: CanvasLayout | null
    error: string | null
  } | null>(null)
  const cachedLayout = layoutCache.get(layoutKey) ?? null
  useEffect(() => {
    if (layoutCache.has(layoutKey)) return
    const controller = new AbortController()
    const key = layoutKey
    const processId = process.id
    fetchCanvasLayout(process, controller.signal)
      .then((layout) => {
        rememberLayout(key, layout)
        setLayoutState({ key, processId, layout, error: null })
      })
      .catch((err) => {
        if (controller.signal.aborted) return
        setLayoutState({ key, processId, layout: null, error: err instanceof Error ? err.message : String(err) })
      })
    return () => controller.abort()
    // Раскладка зависит только от геометрии и подписей (layoutKey), а не от
    // каждого нового объекта процесса.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutKey])
  const currentLayout = layoutState?.key === layoutKey ? layoutState : null
  // Пока бэкенд пересчитывает ту же страницу после правки (переименовали
  // фигуру), на холсте остаётся прежняя раскладка: иначе все линии карты
  // пропадали бы на время запроса. Чужой странице прежняя раскладка не годится.
  const layout =
    currentLayout?.layout ??
    cachedLayout ??
    (!currentLayout && layoutState?.processId === process.id ? layoutState.layout : null)
  const layoutError = cachedLayout ? null : currentLayout?.error ?? null
  /** На холсте прежние линии, а новые (после правки) ещё считаются. */
  const layoutPending = !cachedLayout && !currentLayout

  const visibleIds = useMemo(() => {
    let list = process.nodes
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      list = list.filter(n =>
        n.name.toLowerCase().includes(q) ||
        (n.code   && n.code .toLowerCase().includes(q)) ||
        (n.role   && n.role .toLowerCase().includes(q)) ||
        (n.system && n.system.toLowerCase().includes(q)),
      )
    }
    return new Set(list.map(n => n.id))
  }, [process.nodes, searchQuery])

  const fitToScreen = useCallback(() => {
    if (!wrapRef.current) return
    const { width, height } = wrapRef.current.getBoundingClientRect()
    if (width < 40 || height < 40) return
    const pad = 12
    const s = Math.min((width - pad * 2) / bounds.w, (height - pad * 2) / bounds.h, 1)
    const z = +Math.max(s, MIN_ZOOM).toFixed(2)
    setView({
      zoom: z,
      x: (width  - bounds.w * z) / 2 - bounds.minX * z,
      y: (height - bounds.h * z) / 2 - bounds.minY * z,
    })
  }, [bounds, setView])

  // ── Наводка на фигуры замечания ─────────────────────────────────────────
  /** Фигуры замечания, которые действительно есть на карте. */
  const focusNodes = useMemo(() => {
    if (!focus?.nodeIds?.length) return [] as ProcessNode[]
    const byId = new Map(process.nodes.map((n) => [n.id, n]))
    return focus.nodeIds.map((id) => byId.get(id)).filter((n): n is ProcessNode => Boolean(n))
  }, [focus, process.nodes])

  const focusIds = useMemo(() => new Set(focusNodes.map((n) => n.id)), [focusNodes])

  /**
   * Сама карта — дорожки, линии, фигуры и подписи — собирается один раз и
   * не пересобирается, пока её двигают и масштабируют.
   *
   * Раньше каждое движение мыши перерисовывало всё: на «1.53» (1600 фигур,
   * 1900 линий) это 200 мс на шаг панорамы — пять кадров в секунду. Сдвиг и
   * масштаб теперь задаёт слой над картой (см. ниже), а сцена зависит только
   * от того, что на ней нарисовано.
   */
  const scene = useMemo(() => {
    /** Линия видна, пока видны её концы; свободный конец и дорожка видны всегда. */
    const edgeVisible = (sourceId?: string, targetId?: string) => {
      if (!searchQuery.trim()) return true
      const endVisible = (id?: string) =>
        !id || visibleIds.has(id) || process.lanes.some((l) => l.id === id)
      return endVisible(sourceId) && endVisible(targetId)
    }

    /**
     * Общие свойства группы фигуры. В режиме правки клик фигуру не открывает —
     * её выделяют и тянут (карточка открывается двойным кликом), поэтому и
     * курсор другой.
     */
    const nodeProps = (node: ProcessNode) => ({
      'data-node-id': node.id,
      className: 'nb',
      style: { cursor: editing ? 'move' : 'pointer' },
      onClick: editing ? undefined : () => onSelectNode(node),
    })

    /**
     * Выносная подпись фигуры — на месте из общего расчёта с выгрузкой.
     *
     * Фон под подписью — как в draw.io: у шлюзов и событий карт банка стоит
     * `labelBackgroundColor`, и линия, проходящая под подписью, её не
     * перечёркивает. Холст печатал вопрос шлюза всегда над ромбом и без фона —
     * входящая сверху стрелка шла прямо сквозь текст.
     */
    const renderCaption = (node: ProcessNode, color: string): React.ReactNode => {
      const box = layout?.nodeLabels[node.id]
      if (!box || !box.text.trim()) return null
      const lines = captionLines(box.text, box.width)
      const extent = captionExtent(lines, box)
      const background = (parseStyleMap(node.style).labelbackgroundcolor || 'none').toLowerCase()
      // Подпись ИТ-системы и документа стоит ровно под фигурой, а пунктир к
      // шагу выходит из середины её низа — сквозь подпись. Фон под текстом
      // держит буквы читаемыми, не уводя подпись от фигуры.
      const isData = node.type === 'dataStore' || node.type === 'dataObject'
      return (
        <g pointerEvents="none">
          {(background !== 'none' || isData) && (
            <rect x={extent.x} y={extent.y} width={extent.w} height={extent.h} fill={C.canvas} />
          )}
          {lines.map((line, i) => (
            <text key={i}
              x={box.x + box.width / 2}
              y={extent.y + 2 + CAPTION_LINE / 2 + i * CAPTION_LINE}
              textAnchor="middle" dominantBaseline="central"
              fontSize={CAPTION_FONT} fill={color}
              style={{ userSelect: 'none', fontFamily: FONT }}>
              {line}
            </text>
          ))}
        </g>
      )
    }

    return (
      <>
        <defs>
          <marker id="arr" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
            <polygon points="0 0,8 3,0 6" fill={C.edge} />
          </marker>
          <marker id="arr-hi" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
            <polygon points="0 0,8 3,0 6" fill={C.edgeHi} />
          </marker>
          <marker id="arr-dashed" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
            <polygon points="0 0,8 3,0 6" fill={C.edgeSoft} />
          </marker>
          <marker id="arr-dashed-red" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
            <polygon points="0 0,8 3,0 6" fill={C.endNo} />
          </marker>
          {process.edges.map(edge => {
            // динамический маркер под цвет ребра (зелёные/красные/серые пунктиры)
            const col = (edge.strokeColor || '').trim()
            if (!col) return null
            // нормализуем #fff -> #ffffff уже есть, просто используем как есть
            const safeId = `arr-${edge.id}`
            return (
              <marker key={safeId} id={safeId} markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
                <polygon points="0 0,8 3,0 6" fill={col} />
              </marker>
            )
          })}
          {process.nodes.map((node) => {
            const b = nodeBoxes.get(node.id) || rawBox(node)
            const st = parseStyleMap(node.style)
            const padL = 6 + styleNum(st, 'spacing') + styleNum(st, 'spacingleft')
            const padT = 5 + styleNum(st, 'spacing') + styleNum(st, 'spacingtop')
            const padR = 6 + styleNum(st, 'spacing') + styleNum(st, 'spacingright')
            const padB = 5 + styleNum(st, 'spacing') + styleNum(st, 'spacingbottom')
            return (
              <clipPath key={`clip-${node.id}`} id={`clip-${node.id}`}>
                <rect
                  x={b.x + padL}
                  y={b.y + padT}
                  width={Math.max(8, b.w - padL - padR)}
                  height={Math.max(8, b.h - padT - padB)}
                  rx={6}
                />
              </clipPath>
            )
          })}
        </defs>
        {process.lanes.map((lane) => (
          <rect
            key={`body-${lane.id}`}
            x={lane.geometry.x} y={lane.geometry.y}
            width={lane.geometry.width} height={lane.geometry.height}
            fill="none" stroke={C.laneLine} strokeWidth="1"
          />
        ))}

        {/* Рёбра — только линии, лейблы отдельным слоем поверх узлов */}
        {process.edges.map(edge => {
          const route = layout?.routes[edge.id]
          if (!route || route.length < 2) return null
          if (!edgeVisible(edge.sourceId, edge.targetId)) return null
          const d = route.map(([px, py], i) => `${i === 0 ? 'M' : 'L'}${px},${py}`).join(' ')
          const hi = Boolean(selectedNodeId && (edge.sourceId === selectedNodeId || edge.targetId === selectedNodeId))
          const isDashed = edge.dashed || Boolean(edge.dashPattern)
          const sw = isDashed ? 1.7 : (edge.strokeWidth ? Math.max(1, Math.min(3.5, edge.strokeWidth)) : (hi ? 2 : 1.35))
          // Узор пунктира в draw.io задан В ТОЛЩИНАХ ЛИНИИ: mxGraph умножает
          // `dashPattern` на `strokeWidth`, и «1 4» при толщине 1 — это точка
          // в пиксель через четыре. Мы подставляли те же числа как пиксели, но
          // рисовали линию в 1.7 px: с круглым концом точка растягивалась почти
          // на весь просвет, и вместо ровного пунктира «листочек ↔ шаг» выходила
          // рябь, которую на карте едва видно. Множим на толщину, как редактор.
          //
          // Узор по умолчанию у mxGraph — «3 3», и он тоже в толщинах. Пока
          // здесь стояло «9 9» пикселями, подводка к цилиндру системы —
          // отрезок в полтора десятка пикселей — укладывалась в ОДИН штрих
          // и читалась на карте сплошной чёрточкой: пунктира, которым эта
          // связь отличается от потока, видно не было.
          const DASH_UNIT = 3
          const defaultDash = `${DASH_UNIT * sw} ${DASH_UNIT * sw}`
          let dashArray: string | undefined
          if (edge.dashPattern) {
            const parts = edge.dashPattern.replace(/;/g, ' ').trim().split(/\s+/)
              .map(Number).filter((n) => Number.isFinite(n) && n > 0)
            dashArray = parts.length ? parts.map((n) => n * sw).join(' ') : defaultDash
          } else if (isDashed) dashArray = defaultDash
          // Ассоциация к артефакту красится в цвет артефактов, а
          // оформительские разделители этапов приглушаются — иначе они
          // спорят с потоком управления за внимание.
          const isDecoration = edge.kind === 'annotationLine'
          const isAssociation = edge.kind === 'association'
          const baseColor = edge.strokeColor
            ? edge.strokeColor
            : isAssociation
            ? C.storeStroke
            : isDashed
            ? C.edgeSoft
            : C.edge
          const strokeColor = hi ? C.edgeHi : baseColor
          // Связь с хранилищем или примечанием — не поток, а пометка «шаг
          // работает с этими данными», и направления у неё нет: аналитик
          // рисует её в draw.io пунктиром с endArrow=none. Наконечник
          // превращал пометку в ещё одну стрелку потока.
          let markerId: string | undefined
          if (isAssociation) markerId = undefined
          else if (hi) markerId = 'url(#arr-hi)'
          else if (isDashed && edge.strokeColor === '#ff6b6b') markerId = 'url(#arr-dashed-red)'
          else if (isDashed) markerId = edge.strokeColor ? `url(#arr-${edge.id})` : 'url(#arr-dashed)'
          else if (edge.strokeColor) markerId = `url(#arr-${edge.id})`
          else markerId = 'url(#arr)'
          return (
            <React.Fragment key={edge.id}>
              <path d={d} fill="none"
                stroke={strokeColor}
                strokeWidth={sw}
                strokeLinejoin="round"
                strokeLinecap="round"
                strokeDasharray={dashArray}
                opacity={isDecoration ? 0.32 : 1}
                markerEnd={markerId} />
              {/* Тонкую линию мышью не поймать: в режиме правки под ней
                  лежит невидимая широкая полоса, за которую её и выделяют.
                  Ширина — из переменной слоя (edgeHit): на экране 10–14 px
                  при любом масштабе, а сцена от масштаба не пересобирается. */}
              {editing && (
                <path d={d} fill="none" stroke="transparent"
                  pointerEvents="stroke" data-edge-id={edge.id} data-export-skip
                  style={{ cursor: 'pointer', strokeWidth: 'var(--edge-hit)' }} />
              )}
            </React.Fragment>
          )
        })}

        {process.nodes.map(node => {
          // Подсвеченная фигура не гаснет при поиске: иначе замечание
          // приводит сотрудника к пустому месту на холсте.
          const faded = !visibleIds.has(node.id) && !focusIds.has(node.id)
          const sel   = selectedNodeId === node.id
          const isRpa = node.category === 'rpa_bot'
          const isBad = (node.slaMinutes || 0) >= SLOW_STEP_MINUTES
          const isRej = node.id.toLowerCase().includes('reject') ||
                        node.name.toLowerCase().includes('отказ') ||
                        node.name.toLowerCase().includes('rad etildi')
          const b = nodeBoxes.get(node.id) || rawBox(node)
          const { x, y, w, h, cx, cy } = b
          const st = parseStyleMap(node.style)
          const align = (st.align || 'center').toLowerCase()
          const valign = (st.verticalalign || 'middle').toLowerCase()

          if (node.type === 'startEvent') {
            const r = Math.max(8, Math.min(w, h) / 2 - 1)
            return (
              <g key={node.id} opacity={faded ? 0.18 : 1}
                {...nodeProps(node)}>
                {sel && <circle cx={cx} cy={cy} r={r + 6} fill="none" stroke={C.edgeHi} strokeWidth="1.2" strokeDasharray="4 3" />}
                <circle cx={cx} cy={cy} r={r} fill={C.canvas} stroke={sel ? C.edgeHi : C.start} strokeWidth={1.8} />
                {renderCaption(node, C.caption)}
              </g>
            )
          }

          if (node.type === 'endEvent') {
            const r = Math.max(8, Math.min(w, h) / 2 - 1)
            const sc = isRej ? C.endNo : C.endOk
            return (
              <g key={node.id} opacity={faded ? 0.18 : 1}
                {...nodeProps(node)}>
                {sel && <circle cx={cx} cy={cy} r={r + 6} fill="none" stroke={C.edgeHi} strokeWidth="1.2" strokeDasharray="4 3" />}
                <circle cx={cx} cy={cy} r={r} fill={C.canvas} stroke={sel ? C.edgeHi : sc} strokeWidth={3.4} />
                <circle cx={cx} cy={cy} r={Math.max(4, r - 6)} fill="none" stroke={sel ? C.edgeHi : sc} strokeWidth="1.4" />
                {renderCaption(node, sc)}
              </g>
            )
          }

          // Промежуточное событие (2-ILOVA: Oraliq hodisalar) — двойной круг
          // с маркером: таймер «Kutish vaqti» или конверт сообщения.
          if (node.type === 'intermediateTimerEvent' || node.type === 'intermediateMessageEvent') {
            const r = Math.max(8, Math.min(w, h) / 2 - 1)
            const isTimer = node.type === 'intermediateTimerEvent'
            // Сторону подписи выбрал аналитик (у часов в draw.io стоит
            // `labelPosition=left`), и общий расчёт подписей ставит её туда же.
            return (
              <g key={node.id} opacity={faded ? 0.18 : 1}
                {...nodeProps(node)}>
                {sel && <circle cx={cx} cy={cy} r={r + 6} fill="none" stroke={C.edgeHi} strokeWidth="1.2" strokeDasharray="4 3" />}
                <circle cx={cx} cy={cy} r={r} fill={C.canvas} stroke={sel ? C.edgeHi : C.timerStroke} strokeWidth={1.6} />
                <circle cx={cx} cy={cy} r={Math.max(4, r - 3.5)} fill="none" stroke={sel ? C.edgeHi : C.timerStroke} strokeWidth={1.2} />
                {isTimer ? (
                  <g stroke={C.timerStroke} strokeWidth={1.3} strokeLinecap="round" fill="none">
                    <line x1={cx} y1={cy - r * 0.45} x2={cx} y2={cy} />
                    <line x1={cx} y1={cy} x2={cx + r * 0.32} y2={cy + r * 0.22} />
                  </g>
                ) : (
                  <g stroke={C.timerStroke} strokeWidth={1.2} fill="none">
                    <rect x={cx - r * 0.45} y={cy - r * 0.3} width={r * 0.9} height={r * 0.6} />
                    <path d={`M${cx - r * 0.45},${cy - r * 0.3} L${cx},${cy + r * 0.05} L${cx + r * 0.45},${cy - r * 0.3}`} />
                  </g>
                )}
                {renderCaption(node, C.timerStroke)}
              </g>
            )
          }

          // Хранилище данных (IABS, EHA, EDO) — цилиндр, как в draw.io и PIX.
          if (node.type === 'dataStore') {
            const ry = Math.max(3, Math.min(8, h * 0.16))
            return (
              <g key={node.id} opacity={faded ? 0.18 : 1}
                {...nodeProps(node)}>
                <path
                  d={`M${x},${y + ry} a${w / 2},${ry} 0 0 1 ${w},0 L${x + w},${y + h - ry} a${w / 2},${ry} 0 0 1 ${-w},0 Z`}
                  fill={C.storeFill} stroke={sel ? C.edgeHi : C.storeStroke} strokeWidth={sel ? 2 : 1.4} />
                <path d={`M${x},${y + ry} a${w / 2},${ry} 0 0 0 ${w},0`}
                  fill="none" stroke={sel ? C.edgeHi : C.storeStroke} strokeWidth={1.2} />
                {renderCaption(node, C.storeStroke)}
              </g>
            )
          }

          // Объект данных (Dalolatnoma, Yig'ma jild) — лист с загнутым углом.
          if (node.type === 'dataObject') {
            const fold = Math.max(6, Math.min(14, w * 0.22))
            return (
              <g key={node.id} opacity={faded ? 0.18 : 1}
                {...nodeProps(node)}>
                <path
                  d={`M${x},${y} L${x + w - fold},${y} L${x + w},${y + fold} L${x + w},${y + h} L${x},${y + h} Z`}
                  fill={C.docFill} stroke={sel ? C.edgeHi : C.docStroke} strokeWidth={sel ? 2 : 1.4} />
                <path d={`M${x + w - fold},${y} L${x + w - fold},${y + fold} L${x + w},${y + fold}`}
                  fill="none" stroke={sel ? C.edgeHi : C.docStroke} strokeWidth={1.2} />
                {renderCaption(node, C.docStroke)}
              </g>
            )
          }

          // Рамка-примечание — как в draw.io: прямоугольник по стилю фигуры
          // (скругление, штрих-пунктир), текст кеглем и жирностью из стиля.
          // Так нарисовано название процесса над картой: рамка во всю ширину
          // пула и кегль 72. Скобкой с мелким серым текстом оно терялось.
          if (node.type === 'textAnnotation' && isFramedNote(st)) {
            const bold = (styleNum(st, 'fontstyle') & 1) === 1
            const cap = fitStyledText(shapeCaption(node), w - 16, h - 8, styleNum(st, 'fontsize', 12), bold)
            const lineH = cap.fontSize * 1.2
            const ty0 = cy - (cap.lines.length - 1) * lineH / 2
            const textAnchor = align === 'left' ? 'start' : align === 'right' ? 'end' : 'middle'
            const tx = align === 'left' ? x + 8 : align === 'right' ? x + w - 8 : cx
            const dash = st.dashed === '1'
              ? (st.dashpattern || '3 3').split(/\s+/).map((v) => Number(v) * 1.5).join(' ')
              : undefined
            return (
              <g key={node.id} opacity={faded ? 0.18 : 1}
                {...nodeProps(node)}>
                <rect x={x} y={y} width={w} height={h}
                  rx={st.rounded === '1' ? Math.min(w, h) * 0.15 : 0}
                  // Без fillColor draw.io заливает рамку белым — плашка, а не контур поверх сетки.
                  fill={st.fillcolor === 'none' ? 'none' : C.taskFill}
                  stroke={sel ? C.edgeHi : C.taskStroke} strokeWidth={1.5} strokeDasharray={dash} />
                {cap.lines.map((line, i) => (
                  <text key={i} x={tx} y={ty0 + i * lineH}
                    textAnchor={textAnchor} dominantBaseline="central"
                    fontSize={cap.fontSize} fontWeight={bold ? 700 : 400} fill={C.taskText}
                    style={{ userSelect: 'none', fontFamily: FONT }}>
                    {line}
                  </text>
                ))}
              </g>
            )
          }

          // Текстовое примечание — скобка слева, как в BPMN.
          //
          // Текст раскладываем по рамке самой фигуры, а не по колонке
          // фиксированной ширины: в draw.io примечание нарисовано своим
          // прямоугольником, и подпись занимает его целиком. Пока колонка
          // была 110 px, заголовок карты (в августовской партии это рамка
          // 2880…37080 px во всю ширину пула) сжимался в узкий столбик у
          // левого края и на холсте читался как обрывок, а не как название
          // процесса. Подгонка — та же, что у шагов: карта не должна
          // укладывать текст двумя разными способами.
          if (node.type === 'textAnnotation') {
            const cap = fitBoxText(shapeCaption(node), w - 24, h - 12, 6)
            const lineH = cap.fontSize + 3
            const ty0 = cy - (cap.lines.length - 1) * lineH / 2
            return (
              <g key={node.id} opacity={faded ? 0.18 : 1}
                {...nodeProps(node)}>
                <path d={`M${x + 8},${y} L${x},${y} L${x},${y + h} L${x + 8},${y + h}`}
                  fill="none" stroke={sel ? C.edgeHi : C.noteStroke} strokeWidth={1.4} />
                {cap.lines.map((line, i) => (
                  <text key={i} x={cx} y={ty0 + i * lineH}
                    textAnchor="middle" dominantBaseline="central"
                    fontSize={cap.fontSize} fill={C.noteStroke}
                    style={{ userSelect: 'none', fontFamily: FONT }}>
                    {line}
                  </text>
                ))}
              </g>
            )
          }

          if (isGatewayNode(node.type)) {
            const s = Math.max(12, Math.min(w, h) / 2)
            return (
              <g key={node.id} opacity={faded ? 0.18 : 1}
                {...nodeProps(node)}>
                {sel && <polygon points={`${cx},${cy-s-6} ${cx+s+6},${cy} ${cx},${cy+s+6} ${cx-s-6},${cy}`}
                  fill="none" stroke={C.edgeHi} strokeWidth="1.2" strokeDasharray="4 3" />}
                <polygon points={`${cx},${cy-s} ${cx+s},${cy} ${cx},${cy+s} ${cx-s},${cy}`}
                  fill={C.canvas} stroke={sel ? C.edgeHi : C.gwStroke} strokeWidth={1.8} />
                {gatewayMarker(node.type, cx, cy, s, C.gwStroke)}
                {renderCaption(node, C.caption)}
              </g>
            )
          }

          const fill = isRpa ? C.rpaFill : isBad ? C.badFill : C.taskFill
          const stroke = sel ? C.edgeHi : isRpa ? C.rpaStroke : isBad ? C.badStroke : C.taskStroke
          const padL = 6 + styleNum(st, 'spacing') + styleNum(st, 'spacingleft')
          const padT = 5 + styleNum(st, 'spacing') + styleNum(st, 'spacingtop')
          const padR = 6 + styleNum(st, 'spacing') + styleNum(st, 'spacingright')
          const padB = 5 + styleNum(st, 'spacing') + styleNum(st, 'spacingbottom')
          const innerW = Math.max(12, w - padL - padR)
          const innerH = Math.max(12, h - padT - padB)
          const fitted = fitBoxText(node.name, innerW, innerH, 4)
          // Время у часов — словами карты draw.io и тем же текстом, что в
          // выгрузке: холст печатал «1 ч» там, где на карте «60 min».
          const sla = layout?.durations[node.id] ?? ''
          const textAnchor = align === 'left' ? 'start' : align === 'right' ? 'end' : 'middle'
          const tx = align === 'left' ? x + padL : align === 'right' ? x + w - padR : cx
          const lineH = fitted.fontSize + 3
          const blockH = fitted.lines.length * lineH
          let ty0: number
          if (valign === 'top') ty0 = y + padT + fitted.fontSize / 2
          else if (valign === 'bottom') ty0 = y + h - padB - blockH + fitted.fontSize / 2
          else ty0 = cy - (fitted.lines.length - 1) * lineH / 2

          return (
            <g key={node.id} opacity={faded ? 0.18 : 1}
              {...nodeProps(node)}>
              <rect x={x} y={y} width={w} height={h} rx={8} ry={8}
                fill={fill} stroke={stroke} strokeWidth={sel ? 2 : 1.5} />
              {node.type === 'subProcess' && (() => {
                // Квадратик с плюсом внизу по центру — так BPMN метит
                // свёрнутый подпроцесс, и так же рисует его draw.io. Без
                // маркера шаг, за которым стоит целый процесс, на карте
                // ничем не отличается от обычного.
                const side = Math.max(8, Math.min(14, h * 0.18))
                const mx = cx - side / 2
                const my = y + h - side - 3
                const mid = side / 2
                return (
                  <g pointerEvents="none" stroke={stroke} strokeWidth={1.2} fill="none">
                    <rect x={mx} y={my} width={side} height={side} />
                    <line x1={mx + mid} y1={my + 2.5} x2={mx + mid} y2={my + side - 2.5} />
                    <line x1={mx + 2.5} y1={my + mid} x2={mx + side - 2.5} y2={my + mid} />
                  </g>
                )
              })()}
              <g clipPath={`url(#clip-${node.id})`}>
                {fitted.lines.map((line, i) => (
                  <text key={i}
                    x={tx}
                    y={ty0 + i * lineH}
                    textAnchor={textAnchor} dominantBaseline="central"
                    fontSize={fitted.fontSize} fill={C.taskText}
                    style={{ userSelect: 'none', fontFamily: FONT }}>
                    {line}
                  </text>
                ))}
              </g>
              {!!sla && (() => {
                // Бейдж длительности — как в draw.io: часы в правом нижнем
                // углу шага и время под ними. Сама фигура-таймер в модель не
                // попадает (её время уходит в ST шага), и без бейджа на карте
                // терялось, сколько шаг занимает.
                const bx = x + w - 15
                const by = y + h - 15
                return (
                  <g pointerEvents="none">
                    <circle cx={bx} cy={by} r={8} fill={C.canvas}
                      stroke={C.timerStroke} strokeWidth={1.2} />
                    <circle cx={bx} cy={by} r={5.6} fill="none"
                      stroke={C.timerStroke} strokeWidth={0.9} />
                    <path d={`M${bx},${by - 3.4} L${bx},${by} L${bx + 2.8},${by + 1.6}`}
                      fill="none" stroke={C.timerStroke} strokeWidth={1.1}
                      strokeLinecap="round" strokeLinejoin="round" />
                    <text x={bx} y={y + h + 12} textAnchor="middle"
                      fontSize="9.5" fill={C.timerStroke}
                      style={{ userSelect: 'none', fontFamily: FONT }}>
                      {sla}
                    </text>
                  </g>
                )
              })()}
            </g>
          )
        })}

        {/* Подписи связей — поверх узлов, на местах из общего расчёта с
            выгрузкой: там они уже разведены между собой, с фигурами и с
            чужими линиями. Фон под подписью — как у edgeLabel в draw.io. */}
        {layout && process.edges.map((edge) => {
          const box = layout.edgeLabels[edge.id]
          if (!box || !box.text.trim()) return null
          if (!edgeVisible(edge.sourceId, edge.targetId)) return null
          const lines = captionLines(box.text, box.width)
          const extent = captionExtent(lines, box)
          return (
            <g key={`lbl-${edge.id}`} pointerEvents="none">
              <rect x={extent.x} y={extent.y} width={extent.w} height={extent.h} rx={3}
                fill={C.labelBg} stroke={C.labelStroke} strokeWidth="0.7" />
              {lines.map((line, i) => (
                <text key={i}
                  x={box.x + box.width / 2}
                  y={extent.y + 2 + CAPTION_LINE / 2 + i * CAPTION_LINE}
                  textAnchor="middle" dominantBaseline="central"
                  fontSize={CAPTION_FONT} fill={C.taskText}
                  style={{ userSelect: 'none', fontFamily: FONT }}>
                  {line}
                </text>
              ))}
            </g>
          )
        })}

        {process.lanes.map((lane) => {
          const label = laneLabels.get(lane.id) || { lines: [lane.name], fontSize: 11 }
          const hw = laneHeaderWidth(lane)
          const hx = lane.geometry.x + hw / 2
          const hy = lane.geometry.y + lane.geometry.height / 2
          const lineH = label.fontSize + 3
          return (
            <g key={`head-${lane.id}`}>
              <rect
                x={lane.geometry.x} y={lane.geometry.y}
                width={hw} height={lane.geometry.height}
                fill={C.laneHead} stroke={C.laneLine} strokeWidth="1"
              />
              <g transform={`rotate(-90, ${hx}, ${hy})`}>
                {label.lines.map((line, i) => (
                  <text
                    key={i}
                    x={hx}
                    y={hy - ((label.lines.length - 1) * lineH) / 2 + i * lineH}
                    textAnchor="middle"
                    dominantBaseline="middle"
                    fontSize={label.fontSize} fontWeight="600" fill={C.laneText}
                    style={{ userSelect: 'none', fontFamily: FONT }}>
                    {line}
                  </text>
                ))}
              </g>
            </g>
          )
        })}
      </>
    )
    // Сцена зависит только от содержимого карты и выбора, не от положения камеры.
  }, [process, layout, C, nodeBoxes, laneLabels, visibleIds, focusIds, searchQuery, selectedNodeId, onSelectNode, editing])

  /**
   * Подводит камеру к прямоугольнику карты, не приближая сильнее обычного
   * чтения. Рамку запоминаем: когда панель отчёта схлопнется и холст станет
   * выше, наводку надо повторить — иначе фигура останется у самого края.
   */
  const centerOn = useCallback(
    (box: { x: number; y: number; w: number; h: number }) => {
      focusBoxRef.current = box
      const el = wrapRef.current
      if (!el) return
      const { width, height } = el.getBoundingClientRect()
      if (width < 40 || height < 40) return
      // Карточка замечания занимает низ холста: центрируем фигуру в том, что
      // от холста остаётся, иначе подсказка закрывает ровно то, на что навела.
      // Больше 45 % холста карточке не отдаём — на низком окне от него иначе
      // не остаётся ничего, и «подъезд» к фигуре превращался в отъезд.
      const usable = Math.max(height - cardHeight, height * (1 - FOCUS_CARD_MAX_SHARE))
      // Поля тоже пропорциональные: фиксированные 90 px на невысоком холсте
      // съедали всю доступную высоту, и масштаб схлопывался до минимума.
      const padX = Math.min(FOCUS_PADDING, width * 0.15)
      const padY = Math.min(FOCUS_PADDING, usable * 0.15)
      const fit = Math.min(
        (width - padX * 2) / Math.max(box.w, 1),
        (usable - padY * 2) / Math.max(box.h, 1),
      )
      const z = +Math.min(FOCUS_MAX_ZOOM, Math.max(MIN_ZOOM, fit)).toFixed(3)
      setView({
        zoom: z,
        x: width / 2 - (box.x + box.w / 2) * z,
        y: usable / 2 - (box.y + box.h / 2) * z,
      })
    },
    [setView, cardHeight],
  )

  /** Общая рамка нескольких фигур. */
  const unionBox = useCallback(
    (nodes: ProcessNode[]) => {
      if (!nodes.length) return null
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
      for (const n of nodes) {
        const b = nodeBoxes.get(n.id) ?? rawBox(n)
        minX = Math.min(minX, b.x)
        minY = Math.min(minY, b.y)
        maxX = Math.max(maxX, b.x + b.w)
        maxY = Math.max(maxY, b.y + b.h)
      }
      return { x: minX, y: minY, w: maxX - minX, h: maxY - minY }
    },
    [nodeBoxes],
  )

  // Новое замечание: снимаем поиск (иначе нужная фигура останется погашенной)
  // и показываем всю группу целиком — сотрудник сперва видит масштаб проблемы,
  // а потом листает фигуры по одной.
  useEffect(() => {
    if (!focus || !focusNodes.length) return
    setSearchQuery('')
    setFocusIndex(0)
    const box = unionBox(focusNodes)
    if (box) centerOn(box)
    wrapRef.current?.focus({ preventScroll: true })
    // Наводка привязана к клику (nonce), а не к составу группы: повторный клик
    // по той же строке отчёта обязан вернуть камеру на место.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.nonce])

  // Панель отчёта схлопывается в тот же клик, что и наводка: холст становится
  // выше уже после того, как камера встала. Пересчитываем наводку по факту
  // изменения размеров — заодно это чинит и обычное изменение окна.
  useEffect(() => {
    const el = wrapRef.current
    if (!el || !focus) {
      focusBoxRef.current = null
      return
    }
    const ro = new ResizeObserver(() => {
      const box = focusBoxRef.current
      if (box) centerOn(box)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [focus, centerOn])

  // Высота карточки зависит от длины замечания, поэтому её меряем.
  useEffect(() => {
    const el = cardRef.current
    if (!el) {
      setCardHeight(0)
      return
    }
    const ro = new ResizeObserver(() => setCardHeight(el.offsetHeight + 24))
    ro.observe(el)
    setCardHeight(el.offsetHeight + 24)
    return () => ro.disconnect()
  }, [focus, focusIndex])

  /** Показать одну фигуру группы крупным планом. */
  const goToFocusNode = useCallback(
    (index: number) => {
      const node = focusNodes[index]
      if (!node) return
      setFocusIndex(index)
      const b = nodeBoxes.get(node.id) ?? rawBox(node)
      centerOn(b)
    },
    [focusNodes, nodeBoxes, centerOn],
  )

  /** Расстояние между двумя пальцами — база для щипка. */
  const pinchDistance = () => {
    const [a, b] = [...pointers.current.values()]
    return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0
  }
  const pinchCenter = () => {
    const [a, b] = [...pointers.current.values()]
    return a && b ? { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } : null
  }

  /** Точка экрана → точка карты. */
  const toMap = (clientX: number, clientY: number) => {
    const rect = wrapRef.current?.getBoundingClientRect()
    const { zoom: z, x, y } = viewRef.current
    return { x: (clientX - (rect?.left ?? 0) - x) / z, y: (clientY - (rect?.top ?? 0) - y) / z }
  }

  /** Фигура под точкой карты — самая маленькая из накрывающих. */
  const nodeAt = (x: number, y: number, except?: string): ProcessNode | undefined => {
    let best: ProcessNode | undefined
    let bestArea = Infinity
    for (const n of process.nodes) {
      if (n.id === except) continue
      const b = nodeBoxes.get(n.id) ?? rawBox(n)
      if (x < b.x || x > b.x + b.w || y < b.y || y > b.y + b.h) continue
      if (b.w * b.h < bestArea) {
        best = n
        bestArea = b.w * b.h
      }
    }
    return best
  }

  const selectedEdge = editing && editSel?.kind === 'edge'
    ? process.edges.find((e) => e.id === editSel.id) ?? null
    : null
  const selectedEdgeRoute = selectedEdge ? layout?.routes[selectedEdge.id] ?? null : null
  /**
   * Изломы выделенной линии. Пока своих нет, ручками служат углы той ломаной,
   * что проложил бэкенд: сотрудник тянет то, что видит, а не невидимые точки.
   */
  const selectedBends: ProcessEdgePoint[] = !selectedEdge || !selectedEdgeRoute
    ? []
    : bendDrag?.edgeId === selectedEdge.id
    ? bendDrag.points
    : selectedEdge.points.length
    ? selectedEdge.points
    : selectedEdgeRoute.slice(1, -1).map(([x, y]) => ({ x, y }))
  const selectedNode = editing && editSel?.kind === 'node'
    ? process.nodes.find((n) => n.id === editSel.id) ?? null
    : null

  /** Начало правки указателем: фигура, ручка излома, ручка связи или линия. */
  const startEditGesture = (e: React.PointerEvent<HTMLDivElement>): boolean => {
    const target = e.target as Element
    const handle = target.closest('[data-edit-handle]')
    const capture = () => {
      // Без этого браузер тянет за указателем выделение текста — на панели
      // правки подсвечивались «Шаг» и «Шлюз».
      e.preventDefault()
      e.currentTarget.setPointerCapture(e.pointerId)
      editPointer.current = e.pointerId
      wrapRef.current?.focus({ preventScroll: true })
    }
    if (handle && selectedEdge) {
      const kind = handle.getAttribute('data-edit-handle')
      const index = Number(handle.getAttribute('data-index'))
      if (kind === 'bend') {
        setBendDrag({ edgeId: selectedEdge.id, points: [...selectedBends], index, moved: false })
        capture()
        return true
      }
      if (kind === 'mid') {
        const route = selectedEdgeRoute!
        const chain = [{ x: route[0][0], y: route[0][1] }, ...selectedBends,
          { x: route[route.length - 1][0], y: route[route.length - 1][1] }]
        const a = chain[index]
        const b = chain[index + 1]
        if (index > 0 && index < chain.length - 2) {
          // Отрезок между двумя изломами ходит поперёк себя: горизонтальный —
          // вверх-вниз, вертикальный — влево-вправо. Новый излом посреди
          // прямого отрезка дал бы разворот линии назад, а такой путь бэкенд
          // справедливо спрямляет — и правка пропадала бы.
          const axis = Math.abs(b.y - a.y) <= Math.abs(b.x - a.x) ? 'y' : 'x'
          setBendDrag({ edgeId: selectedEdge.id, points: [...selectedBends], index: index - 1, moved: false, segment: { axis } })
          capture()
          return true
        }
        // Крайний отрезок упирается в фигуру: в его середине появляется излом.
        const points = [...selectedBends]
        points.splice(index, 0, { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 })
        setBendDrag({ edgeId: selectedEdge.id, points, index, moved: false })
        capture()
        return true
      }
    }
    if (handle && selectedNode && handle.getAttribute('data-edit-handle') === 'connect') {
      const at = toMap(e.clientX, e.clientY)
      setConnectDrag({ sourceId: selectedNode.id, x: at.x, y: at.y })
      capture()
      return true
    }
    const nodeEl = target.closest('[data-node-id]')
    if (nodeEl) {
      const id = nodeEl.getAttribute('data-node-id')!
      nodeDragRef.current = { id, el: nodeEl, ox: e.clientX, oy: e.clientY, dx: 0, dy: 0, moved: false }
      setEditSel({ kind: 'node', id })
      capture()
      return true
    }
    const edgeEl = target.closest('[data-edge-id]')
    if (edgeEl) {
      setEditSel({ kind: 'edge', id: edgeEl.getAttribute('data-edge-id')! })
      wrapRef.current?.focus({ preventScroll: true })
      return true
    }
    return false
  }

  /** Ход правки: сдвиг фигуры, излома или конца новой связи. */
  const moveEditGesture = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = nodeDragRef.current
    if (drag) {
      if (!drag.moved && Math.hypot(e.clientX - drag.ox, e.clientY - drag.oy) < 4) return
      if (!drag.moved) setIsDraggingNode(true)
      const z = viewRef.current.zoom
      drag.moved = true
      drag.dx = snap((e.clientX - drag.ox) / z)
      drag.dy = snap((e.clientY - drag.oy) / z)
      drag.el.setAttribute('transform', `translate(${drag.dx},${drag.dy})`)
      return
    }
    const at = toMap(e.clientX, e.clientY)
    if (bendDrag) {
      setBendDrag((prev) => {
        if (!prev) return prev
        const axis = prev.segment?.axis
        const points = prev.points.map((p, i) => {
          if (axis) return i === prev.index || i === prev.index + 1 ? { ...p, [axis]: snap(at[axis]) } : p
          return i === prev.index ? { x: snap(at.x), y: snap(at.y) } : p
        })
        return { ...prev, moved: true, points }
      })
      return
    }
    if (connectDrag) setConnectDrag((prev) => prev && { ...prev, x: at.x, y: at.y })
  }

  /** Конец правки: в модель уходит одна правка на весь жест. */
  const endEditGesture = (e: React.PointerEvent<HTMLDivElement>) => {
    editPointer.current = null
    const drag = nodeDragRef.current
    if (drag) {
      nodeDragRef.current = null
      setIsDraggingNode(false)
      // Смещение снимаем до перерисовки: фигура встанет на новое место уже
      // по своим координатам, а не по двойному сдвигу.
      drag.el.removeAttribute('transform')
      if (drag.moved && (drag.dx || drag.dy)) onEdit?.(moveNode(process, drag.id, drag.dx, drag.dy))
      return
    }
    if (bendDrag) {
      if (bendDrag.moved) onEdit?.(setEdgePoints(process, bendDrag.edgeId, bendDrag.points))
      setBendDrag(null)
      return
    }
    if (connectDrag) {
      const at = toMap(e.clientX, e.clientY)
      const target = nodeAt(at.x, at.y, connectDrag.sourceId)
      setConnectDrag(null)
      if (!target) return
      const added = addEdge(process, connectDrag.sourceId, target.id)
      if (!added) {
        toast.info('Такая связь уже есть')
        return
      }
      onEdit?.(added.page)
      setEditSel({ kind: 'edge', id: added.edge.id })
    }
  }

  /** Двойной клик: по излому — убрать его, по фигуре — открыть её карточку. */
  const onCanvasDoubleClick = () => {
    if (!editing) return
    const target = pressedRef.current
    if (!target) return
    const handle = target.closest('[data-edit-handle="bend"]')
    if (handle && selectedEdge) {
      const index = Number(handle.getAttribute('data-index'))
      onEdit?.(setEdgePoints(process, selectedEdge.id, selectedBends.filter((_, i) => i !== index)))
      return
    }
    const nodeEl = target.closest('[data-node-id]')
    const node = nodeEl && process.nodes.find((n) => n.id === nodeEl.getAttribute('data-node-id'))
    if (node) onSelectNode(node)
  }

  const deleteSelection = () => {
    if (!onEdit || !editSel) return
    if (editSel.kind === 'node') onEdit(deleteNode(process, editSel.id))
    else onEdit(deleteEdge(process, editSel.id))
    setEditSel(null)
  }

  /** Новая фигура — в середине видимой части холста; дальше её двигают. */
  const placeShape = (shape: NewShape) => {
    if (!onEdit) return
    const rect = wrapRef.current?.getBoundingClientRect()
    const center = toMap((rect?.left ?? 0) + (rect?.width ?? 0) / 2, (rect?.top ?? 0) + (rect?.height ?? 0) / 2)
    const added = addNode(process, shape, center.x, center.y)
    onEdit(added.page)
    setEditSel({ kind: 'node', id: added.node.id })
    wrapRef.current?.focus({ preventScroll: true })
  }

  const downloadImage = (format: 'svg' | 'png') => {
    const svg = sceneSvgRef.current
    if (!svg) return
    // Рамка картинки — вся карта вместе с линиями, которые проложил бэкенд.
    let { minX, minY } = bounds
    let maxX = bounds.minX + bounds.w
    let maxY = bounds.minY + bounds.h
    for (const route of Object.values(layout?.routes ?? {})) {
      for (const [px, py] of route) {
        minX = Math.min(minX, px); minY = Math.min(minY, py)
        maxX = Math.max(maxX, px); maxY = Math.max(maxY, py)
      }
    }
    const pad = 20
    const name = (process.passport?.code || process.name || 'map').replace(/[\\/:*?"<>|]+/g, '_')
    downloadMapImage(svg, { x: minX - pad, y: minY - pad, w: maxX - minX + pad * 2, h: maxY - minY + pad * 2 },
      C.canvas, format, name)
      .catch((err) => toast.error('Картинка не сохранилась', { description: err instanceof Error ? err.message : String(err) }))
  }

  useImperativeHandle(ref, () => ({ downloadImage }))

  // Указатели вместо мыши: те же обработчики обслуживают и мышь, и палец, и
  // перо. На планшете карту нельзя было ни сдвинуть, ни масштабировать —
  // `onMouseDown` о касаниях не знает.
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    // Панели поверх холста (карточка замечания, «ничего не найдено») живут
    // внутри той же области, что и карта. Без этой проверки холст забирает
    // указатель себе через setPointerCapture, и клик по кнопке панели до неё
    // просто не доходит.
    if ((e.target as HTMLElement).closest('[data-canvas-overlay]')) return
    pressedRef.current = e.target as Element
    if (editing && (e.pointerType !== 'mouse' || e.button === 0) && pointers.current.size === 0) {
      if (startEditGesture(e)) return
      // Клик по пустому месту снимает выделение; карта при этом двигается как обычно.
      setEditSel(null)
    }
    if ((e.target as HTMLElement).closest('.nb')) return
    if (e.pointerType === 'mouse' && e.button !== 0) return
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    e.currentTarget.setPointerCapture(e.pointerId)
    if (pointers.current.size === 2) {
      pinchRef.current = { dist: pinchDistance(), zoom: viewRef.current.zoom }
      setIsPanning(false)
      return
    }
    setIsPanning(true)
    panRef.current = { ox: e.clientX, oy: e.clientY, px: viewRef.current.x, py: viewRef.current.y }
  }

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (editPointer.current === e.pointerId) {
      moveEditGesture(e)
      return
    }
    if (!pointers.current.has(e.pointerId)) return
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })

    if (pointers.current.size >= 2 && pinchRef.current) {
      const dist = pinchDistance()
      const center = pinchCenter()
      if (dist > 0 && pinchRef.current.dist > 0) {
        zoomTo(pinchRef.current.zoom * (dist / pinchRef.current.dist), center?.x, center?.y)
      }
      return
    }
    if (!isPanning) return
    setView({
      zoom: viewRef.current.zoom,
      x: panRef.current.px + e.clientX - panRef.current.ox,
      y: panRef.current.py + e.clientY - panRef.current.oy,
    })
  }

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (editPointer.current === e.pointerId) {
      endEditGesture(e)
      return
    }
    pointers.current.delete(e.pointerId)
    if (pointers.current.size < 2) pinchRef.current = null
    if (pointers.current.size === 0) setIsPanning(false)
  }

  /**
   * Клавиатура работает только когда холст в фокусе: тот же компонент открыт
   * и внутри окна просмотра выгрузки, и глобальный обработчик перехватывал бы
   * стрелки у остальной страницы.
   */
  const onCanvasKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const key = e.key.toLowerCase()
    if ((e.ctrlKey || e.metaKey) && (key === 'z' || key === 'y')) {
      if (key === 'y' || e.shiftKey) onRedo?.()
      else onUndo?.()
      e.preventDefault()
      return
    }
    if (editing && editSel && (e.key === 'Delete' || e.key === 'Backspace')) {
      deleteSelection()
      e.preventDefault()
      return
    }
    const step = e.shiftKey ? 120 : 40
    const { zoom: z, x, y } = viewRef.current
    switch (e.key) {
      case '+':
      case '=':
        zoomTo(z + 0.15); break
      case '-':
      case '_':
        zoomTo(z - 0.15); break
      case '0':
        fitToScreen(); break
      case 'ArrowLeft':
        setView({ zoom: z, x: x + step, y }); break
      case 'ArrowRight':
        setView({ zoom: z, x: x - step, y }); break
      case 'ArrowUp':
        setView({ zoom: z, x, y: y + step }); break
      case 'ArrowDown':
        setView({ zoom: z, x, y: y - step }); break
      default:
        return
    }
    e.preventDefault()
  }

  useEffect(() => {
    const fn = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      // Escape снимает сначала выделение и подсветку, и только потом выходит
      // из полного экрана: иначе сотрудник теряет весь контекст одним нажатием.
      if (editSel) setEditSel(null)
      else if (focus && onClearFocus) onClearFocus()
      else setIsFullscreen(false)
    }
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [focus, onClearFocus, editSel])

  /**
   * Вписывать карту — только когда открыли другую карту или полный экран.
   * Правка меняет рамку карты, и пока эффект зависел от неё, после каждого
   * сдвига шага камера отъезжала на всю карту: работать вблизи было нельзя.
   */
  const fitRef = useRef(fitToScreen)
  useEffect(() => {
    fitRef.current = fitToScreen
  }, [fitToScreen])

  useEffect(() => {
    let fitted = false
    const tryFit = () => {
      if (fitted || !wrapRef.current) return
      const { width, height } = wrapRef.current.getBoundingClientRect()
      if (width < 40 || height < 40) return
      fitRef.current()
      fitted = true
    }
    const raf = requestAnimationFrame(tryFit)
    const el = wrapRef.current
    const ro = el ? new ResizeObserver(tryFit) : null
    if (el && ro) ro.observe(el)
    return () => {
      cancelAnimationFrame(raf)
      ro?.disconnect()
    }
  }, [process.id, isFullscreen])

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const handler = (e: WheelEvent) => {
      e.preventDefault()
      // Множитель, а не слагаемое: на 15 % шаг колеса ощущается одинаково и на
      // мелком, и на крупном масштабе, тогда как фиксированные 0.08 у нижней
      // границы перепрыгивали половину диапазона.
      const factor = e.deltaY > 0 ? 1 / 1.12 : 1.12
      zoomTo(viewRef.current.zoom * factor, e.clientX, e.clientY)
    }
    el.addEventListener('wheel', handler, { passive: false })
    return () => el.removeEventListener('wheel', handler)
  }, [zoomTo])

  /**
   * Ширина полосы, за которую в правке ловят линию, — 10–14 px на экране.
   * Меняется ступенями ×√2, а не на каждом шаге зума: переменную наследуют все
   * элементы сцены, и её смена пересчитывает стили всей карты — на «1.53»
   * (19 тыс. элементов) 120 мс на шаг зума. Вне правки полос нет, переменной тоже.
   */
  const edgeHit = editing ? 12 / 2 ** (Math.round(Math.log2(zoom) * 2) / 2) : null

  const patternOffset = {
    x: ((panPos.x % (GRID_MAJOR * zoom)) + GRID_MAJOR * zoom) % (GRID_MAJOR * zoom),
    y: ((panPos.y % (GRID_MAJOR * zoom)) + GRID_MAJOR * zoom) % (GRID_MAJOR * zoom),
  }

  return (
    <div className={`flex flex-col overflow-hidden rounded-xl border bg-card shadow-sm transition-all duration-200 ${
      isFullscreen ? 'fixed inset-0 z-50 rounded-none border-none' : 'flex-1 min-h-0 h-full'
    }`}>
      {/* Панель холста.
          Собрана лентами, а не одной строкой с `flex-wrap`: на ноутбуке в один
          ряд помещается всё, ниже `md` сводка и управление расходятся на две
          строки, и ни одна кнопка не выпадает за край. */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b bg-muted/40 px-2.5 py-2">
        <div className="no-scrollbar -mx-0.5 flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto px-0.5">
          <span className="hidden shrink-0 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground 2xl:inline">
            Карта
          </span>
          <span className="hidden shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] tabular-nums text-muted-foreground sm:inline">
            {process.nodes.length} эл. · {process.lanes.length} дорожек
          </span>
        </div>

        <div className="flex w-full shrink-0 items-center gap-1.5 md:w-auto">
          <div className="relative min-w-0 flex-1 md:w-40 md:flex-none 2xl:w-52">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Поиск по шагам…"
              aria-label="Поиск по шагам карты"
              className="h-8 pl-8 pr-7 text-xs"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                aria-label="Очистить поиск"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          {onEdit && (
            <Button
              variant={editMode ? 'default' : 'outline'}
              size="sm"
              className="h-8 shrink-0 gap-1.5 px-2 text-xs"
              onClick={() => {
                setEditMode((v) => !v)
                setEditSel(null)
              }}
              aria-pressed={editMode}
              title={editMode ? 'Выйти из режима правки' : 'Двигать шаги и линии, добавлять и удалять фигуры'}
            >
              <Pencil className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">{editMode ? 'Готово' : 'Редактировать'}</span>
            </Button>
          )}
          {/* На телефоне кнопки нет: draw.io на экране в 390 px не поправить,
              а место в ряду нужно поиску. */}
          {onOpenDrawio && (
            <Button
              variant="outline"
              size="sm"
              className="hidden h-8 shrink-0 gap-1.5 px-2 text-xs sm:inline-flex"
              onClick={onOpenDrawio}
              title="Открыть карту в draw.io: всё, что умеет редактор, прямо на платформе"
            >
              <PenTool className="h-3.5 w-3.5" />
              Изменить в draw.io
            </Button>
          )}

          {/* Зум собран одним сегментом: три соседние кнопки без общей рамки
              читались как три независимых действия. Процент — кнопка «вписать
              в экран», а не подпись: возвращать масштаб приходится чаще всего. */}
          <div className="flex shrink-0 items-center rounded-lg border bg-background p-0.5">
            <Button
              variant="ghost" size="icon" className="h-7 w-7"
              onClick={() => zoomTo(viewRef.current.zoom - 0.15)}
              title="Отдалить (−)" aria-label="Отдалить"
            >
              <ZoomOut className="h-3.5 w-3.5" />
            </Button>
            <button
              type="button"
              onClick={fitToScreen}
              title="Вписать карту в экран (0)"
              aria-label="Вписать карту в экран"
              className="w-11 rounded px-1 py-1 text-[11px] font-medium tabular-nums text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {Math.round(zoom * 100)}%
            </button>
            <Button
              variant="ghost" size="icon" className="h-7 w-7"
              onClick={() => zoomTo(viewRef.current.zoom + 0.15)}
              title="Приблизить (+)" aria-label="Приблизить"
            >
              <ZoomIn className="h-3.5 w-3.5" />
            </Button>
          </div>

          <Button
            variant="ghost" size="icon"
            className={`h-8 w-8 shrink-0 ${showGrid ? 'bg-muted text-foreground' : 'text-muted-foreground'}`}
            onClick={() => setShowGrid((v) => !v)}
            aria-pressed={showGrid}
            title="Сетка 10 px" aria-label="Показать сетку"
          >
            <Grid className="h-3.5 w-3.5" />
          </Button>

          <Button
            variant={isFullscreen ? 'secondary' : 'ghost'}
            size="sm"
            className="h-8 shrink-0 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setIsFullscreen((v) => !v)}
            title={isFullscreen ? 'Свернуть (Esc)' : 'Развернуть карту на весь экран'}
            aria-label={isFullscreen ? 'Свернуть карту' : 'Развернуть карту на весь экран'}
          >
            {isFullscreen ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
            {/* Подпись — только на очень широком экране: рядом с картой бывает
                открыта панель сведений, и ряд кнопок не помещался. */}
            <span className="hidden 2xl:inline">{isFullscreen ? 'Свернуть' : 'На весь экран'}</span>
          </Button>
        </div>
      </div>

      <div
        ref={wrapRef}
        tabIndex={0}
        role="application"
        aria-label="Холст карты процесса: перетаскивание — панорама, колесо — масштаб, стрелки — сдвиг"
        // touch-none: без этого браузер забирает жест себе и страница
        // прокручивается вместо того, чтобы двигать карту.
        className={`relative flex-1 min-h-0 touch-none overflow-hidden outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${
          isPanning ? 'cursor-grabbing' : 'cursor-grab'
        }`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={onCanvasDoubleClick}
        onKeyDown={onCanvasKeyDown}
      >
        <svg
          width="100%"
          height="100%"
          className="absolute inset-0 block"
        >
          <defs>
            <pattern id="g-minor" width={GRID_MINOR * zoom} height={GRID_MINOR * zoom} patternUnits="userSpaceOnUse"
              x={patternOffset.x} y={patternOffset.y}>
              <path d={`M ${GRID_MINOR * zoom} 0 L 0 0 0 ${GRID_MINOR * zoom}`}
                fill="none" stroke={C.gridMinor} strokeWidth="1" />
            </pattern>
            <pattern id="g-major" width={GRID_MAJOR * zoom} height={GRID_MAJOR * zoom} patternUnits="userSpaceOnUse"
              x={patternOffset.x} y={patternOffset.y}>
              <rect width={GRID_MAJOR * zoom} height={GRID_MAJOR * zoom} fill="url(#g-minor)" />
              <path d={`M ${GRID_MAJOR * zoom} 0 L 0 0 0 ${GRID_MAJOR * zoom}`}
                fill="none" stroke={C.gridMajor} strokeWidth="1" />
            </pattern>
          </defs>

          <rect width="100%" height="100%" fill={C.canvas} />
          {showGrid && <rect width="100%" height="100%" fill="url(#g-major)" />}
        </svg>

        {/* Слой карты двигается свойством transform: React не пересобирает
            тысячи фигур, меняется одно свойство. Без will-change: с ним Chrome
            на каждом шаге панорамы заново раскладывал слои всей сцены — на
            «1.53» (19 тыс. элементов) 450 мс на кадр, 2 кадра в секунду; без
            него тот же шаг — около 17 мс. */}
        <div
          className="absolute left-0 top-0"
          style={{
            transform: `translate(${panPos.x}px, ${panPos.y}px) scale(${zoom})`,
            transformOrigin: '0 0',
            ...(edgeHit != null && { ['--edge-hit' as string]: `${edgeHit}px` }),
          }}
        >
          <svg ref={sceneSvgRef} width={1} height={1} className="block overflow-visible" style={{ fontFamily: FONT }}>
            {scene}
            {/* Кольца вокруг фигур замечания. Рисуются последними: подсветка
                должна лежать поверх шага, а не под ним. Пульсация задана одной
                анимацией на группе — 200 отдельных SMIL-анимаций подвешивали
                вкладку на больших картах. */}
            {focusNodes.length > 0 && (
              <g className="sqb-focus-pulse" pointerEvents="none" data-export-skip>
                {focusNodes.map((node, index) => {
                  const b = nodeBoxes.get(node.id) ?? rawBox(node)
                  const pad = 7
                  // Толщина задаётся в единицах карты, а группа масштабируется:
                  // делим на zoom, иначе на мелком масштабе кольцо исчезает.
                  const w = Math.max(1.5, 3 / zoom)
                  return (
                    <rect
                      key={node.id}
                      x={b.x - pad}
                      y={b.y - pad}
                      width={b.w + pad * 2}
                      height={b.h + pad * 2}
                      rx={Math.max(4, 8 / zoom)}
                      fill="none"
                      stroke={C.focusRing}
                      strokeWidth={index === focusIndex ? w * 1.8 : w}
                      strokeDasharray={index === focusIndex ? undefined : `${10 / zoom} ${6 / zoom}`}
                    />
                  )
                })}
              </g>
            )}
            {/* Ручки режима правки. Размеры делятся на масштаб: на экране
                ручка одного размера при любом приближении. */}
            {editing && (
              <g data-export-skip>
                {selectedNode && !isDraggingNode && (() => {
                  const b = nodeBoxes.get(selectedNode.id) ?? rawBox(selectedNode)
                  const pad = 6
                  const r = 8 / zoom
                  const hx = b.x + b.w + pad + r + 4 / zoom
                  const hy = b.cy
                  return (
                    <>
                      <rect x={b.x - pad} y={b.y - pad} width={b.w + pad * 2} height={b.h + pad * 2}
                        fill="none" stroke={C.edgeHi} strokeWidth={1.5 / zoom}
                        strokeDasharray={`${5 / zoom} ${3 / zoom}`} pointerEvents="none" />
                      <g data-edit-handle="connect" style={{ cursor: 'crosshair' }}>
                        <title>Потяните к другой фигуре — появится связь</title>
                        <circle cx={hx} cy={hy} r={r} fill={C.edgeHi} />
                        <path
                          d={`M${hx - r * 0.45},${hy} L${hx + r * 0.45},${hy} M${hx + r * 0.05},${hy - r * 0.4} L${hx + r * 0.45},${hy} L${hx + r * 0.05},${hy + r * 0.4}`}
                          fill="none" stroke="#ffffff" strokeWidth={1.6 / zoom} strokeLinecap="round" strokeLinejoin="round"
                          pointerEvents="none" />
                      </g>
                    </>
                  )
                })()}
                {connectDrag && (() => {
                  const source = process.nodes.find((n) => n.id === connectDrag.sourceId)
                  if (!source) return null
                  const b = nodeBoxes.get(source.id) ?? rawBox(source)
                  const target = nodeAt(connectDrag.x, connectDrag.y, source.id)
                  const t = target && (nodeBoxes.get(target.id) ?? rawBox(target))
                  return (
                    <g pointerEvents="none">
                      {t && (
                        <rect x={t.x - 5} y={t.y - 5} width={t.w + 10} height={t.h + 10}
                          fill="none" stroke={C.edgeHi} strokeWidth={2.5 / zoom} rx={6} />
                      )}
                      <line x1={b.cx} y1={b.cy} x2={connectDrag.x} y2={connectDrag.y}
                        stroke={C.edgeHi} strokeWidth={2 / zoom} strokeDasharray={`${6 / zoom} ${4 / zoom}`}
                        markerEnd="url(#arr-hi)" />
                    </g>
                  )
                })()}
                {selectedEdge && selectedEdgeRoute && selectedEdgeRoute.length >= 2 && (() => {
                  const route = selectedEdgeRoute
                  const start = { x: route[0][0], y: route[0][1] }
                  const end = { x: route[route.length - 1][0], y: route[route.length - 1][1] }
                  const chain = [start, ...selectedBends, end]
                  const r = 5 / zoom
                  return (
                    <>
                      <path d={route.map(([px, py], i) => `${i ? 'L' : 'M'}${px},${py}`).join(' ')}
                        fill="none" stroke={C.edgeHi} strokeWidth={4 / zoom} strokeLinecap="round"
                        strokeLinejoin="round" opacity={bendDrag ? 0.2 : 0.45} pointerEvents="none" />
                      {bendDrag && (
                        <polyline points={chain.map((pt) => `${pt.x},${pt.y}`).join(' ')}
                          fill="none" stroke={C.edgeHi} strokeWidth={2 / zoom}
                          strokeDasharray={`${6 / zoom} ${4 / zoom}`} pointerEvents="none" />
                      )}
                      {!bendDrag && chain.slice(0, -1).map((a, i) => {
                        const b = chain[i + 1]
                        // Косой участок бэкенд рисует коленом: середина
                        // отрезка висела бы в стороне от линии.
                        if (Math.abs(a.x - b.x) > 1 && Math.abs(a.y - b.y) > 1) return null
                        return (
                          <circle key={`mid-${i}`} data-edit-handle="mid" data-index={i}
                            cx={(a.x + b.x) / 2} cy={(a.y + b.y) / 2} r={r * 0.85}
                            fill={C.canvas} stroke={C.edgeHi} strokeWidth={1.5 / zoom}
                            style={{ cursor: 'copy' }}>
                            <title>Потяните — новый излом</title>
                          </circle>
                        )
                      })}
                      {selectedBends.map((pt, i) => (
                        <rect key={`bend-${i}`} data-edit-handle="bend" data-index={i}
                          x={pt.x - r} y={pt.y - r} width={r * 2} height={r * 2}
                          fill={C.edgeHi} stroke={C.canvas} strokeWidth={1 / zoom} style={{ cursor: 'move' }}>
                          <title>Тяните — сдвинуть излом, двойной клик — убрать</title>
                        </rect>
                      ))}
                    </>
                  )
                })()}
              </g>
            )}
          </svg>
        </div>

        {/* Панель режима правки: что поставить на карту, отмена, удаление. */}
        {editing && (
          <div className="pointer-events-none absolute inset-x-0 top-2 flex justify-center px-3">
            <div
              data-canvas-overlay
              className="no-scrollbar pointer-events-auto flex max-w-full items-center gap-0.5 overflow-x-auto rounded-xl border bg-card/95 p-1 shadow-lg backdrop-blur"
            >
              {NEW_SHAPE_BUTTONS.map(({ shape, icon: ShapeIcon, label }) => (
                <Button key={shape} variant="ghost" size="sm" className="h-7 shrink-0 gap-1 px-2 text-xs"
                  onClick={() => placeShape(shape)} title={`Добавить: ${label.toLowerCase()}`}>
                  <ShapeIcon className="h-3.5 w-3.5" />
                  {label}
                </Button>
              ))}
              <span className="mx-1 h-4 w-px shrink-0 bg-border" />
              <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0" disabled={!canUndo}
                onClick={onUndo} title="Отменить (Ctrl+Z)" aria-label="Отменить">
                <Undo2 className="h-3.5 w-3.5" />
              </Button>
              <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0" disabled={!canRedo}
                onClick={onRedo} title="Вернуть (Ctrl+Shift+Z)" aria-label="Вернуть">
                <Redo2 className="h-3.5 w-3.5" />
              </Button>
              {selectedEdge && selectedEdge.points.length > 0 && (
                <Button variant="ghost" size="sm" className="h-7 shrink-0 gap-1 px-2 text-xs"
                  onClick={() => onEdit?.(setEdgePoints(process, selectedEdge.id, []))}
                  title="Убрать изломы — линию проложит платформа">
                  <RotateCcw className="h-3.5 w-3.5" />
                  Авто-линия
                </Button>
              )}
              <Button variant="ghost" size="sm"
                className="h-7 shrink-0 gap-1 px-2 text-xs text-rose-600 hover:text-rose-700 dark:text-rose-400"
                disabled={!editSel} onClick={deleteSelection}
                title={editSel?.kind === 'edge' ? 'Удалить линию (Delete)' : 'Удалить фигуру с её линиями (Delete)'}>
                <Trash2 className="h-3.5 w-3.5" />
                Удалить
              </Button>
              <span
                className="flex h-7 w-7 shrink-0 items-center justify-center text-sky-500"
                title="Тяните фигуру — сдвиг · кружок справа — связь · двойной клик — карточка · Delete — удалить"
                aria-label="Как править карту"
              >
                <Info className="h-3.5 w-3.5" />
              </span>
            </div>
          </div>
        )}

        {/* Линии и подписи раскладывает бэкенд: пока их нет, об этом сказано
            прямо, а не показана карта без связей. */}
        {(!layout || layoutPending) && (
          <div className={`pointer-events-none absolute left-3 ${editing ? 'bottom-3' : 'top-3'} flex max-w-[calc(100%-1.5rem)] items-center gap-1.5 rounded-lg border bg-card/95 px-2.5 py-1.5 text-[11px] text-muted-foreground shadow-sm backdrop-blur`}>
            {layoutError
              ? <ServerOff className="h-3.5 w-3.5 shrink-0" />
              : <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />}
            <span className="truncate">
              {layoutError ? `Линии и подписи не показаны. ${layoutError}` : 'Прокладываем линии и подписи…'}
            </span>
          </div>
        )}

        {/* Карточка замечания: что не так, с какой фигурой и что делать.
            Держится над холстом, пока сотрудник её не закроет — уведомление в
            углу исчезает раньше, чем он успевает найти фигуру глазами. */}
        {focus && focusNodes.length > 0 && (
          <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center px-3">
            <div
              ref={cardRef}
              data-canvas-overlay
              role="status"
              className="pointer-events-auto w-full max-w-xl rounded-xl border border-orange-500/40 bg-card/95 p-3 shadow-xl backdrop-blur"
            >
              <div className="flex items-start gap-2">
                <span className={`mt-0.5 shrink-0 ${FOCUS_LEVEL_STYLE[focus.issue?.level ?? 'info'].text}`}>
                  {FOCUS_LEVEL_STYLE[focus.issue?.level ?? 'info'].icon}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-semibold leading-snug">
                    {focus.issue?.message ?? 'Фигуры замечания на карте'}
                  </p>
                  {focus.issue?.hint && (
                    <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
                      {focus.issue.hint}
                    </p>
                  )}
                  <div className="mt-2 rounded-lg border bg-muted/40 px-2 py-1.5">
                    <div className="flex items-center gap-1.5">
                      <Crosshair className="h-3.5 w-3.5 shrink-0 text-orange-500" />
                      <span className="min-w-0 truncate text-xs font-medium">
                        {focusNodes[focusIndex]?.name}
                      </span>
                      {focusNodes[focusIndex]?.code && (
                        <span className="shrink-0 rounded bg-background px-1 py-0.5 text-[10px] tabular-nums text-muted-foreground">
                          {focusNodes[focusIndex].code}
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                      {[
                        NODE_TYPE_LABELS[focusNodes[focusIndex].type],
                        focusNodes[focusIndex].laneName || focusNodes[focusIndex].role,
                        focusNodes[focusIndex].system,
                        isTaskNode(focusNodes[focusIndex].type)
                          ? slaLabel(focusNodes[focusIndex].slaMinutes)
                          : '',
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </p>
                  </div>
                  {onEdit && focusNodes[focusIndex] && (
                    <IssueQuickFix
                      // Новая фигура замечания — поля исправления чистые.
                      key={`${focus.nonce}:${focusNodes[focusIndex].id}`}
                      issue={focus.issue}
                      node={focusNodes[focusIndex]}
                      page={process}
                      onEdit={(page, done) => {
                        onEdit(page)
                        if (!done) return
                        // Исправили фигуру — к следующей фигуре замечания, последнюю — закрыть.
                        if (focusIndex < focusNodes.length - 1) goToFocusNode(focusIndex + 1)
                        else onClearFocus?.()
                      }}
                      onConnect={(nodeId) => {
                        setEditMode(true)
                        setEditSel({ kind: 'node', id: nodeId })
                        onClearFocus?.()
                      }}
                    />
                  )}
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <Button
                      size="sm"
                      variant={onEdit ? 'outline' : 'default'}
                      className="h-7 text-xs"
                      onClick={() => onSelectNode(focusNodes[focusIndex])}
                    >
                      Открыть карточку шага
                    </Button>
                    {focusNodes.length > 1 && (
                      <>
                        <Button
                          variant="outline" size="sm" className="h-7 w-7 p-0"
                          aria-label="Предыдущая фигура замечания"
                          onClick={() => goToFocusNode((focusIndex - 1 + focusNodes.length) % focusNodes.length)}
                        >
                          <ChevronLeft className="h-3.5 w-3.5" />
                        </Button>
                        <span className="text-[11px] tabular-nums text-muted-foreground">
                          {focusIndex + 1} из {focusNodes.length}
                        </span>
                        <Button
                          variant="outline" size="sm" className="h-7 w-7 p-0"
                          aria-label="Следующая фигура замечания"
                          onClick={() => goToFocusNode((focusIndex + 1) % focusNodes.length)}
                        >
                          <ChevronRight className="h-3.5 w-3.5" />
                        </Button>
                      </>
                    )}
                  </div>
                </div>
                <Button
                  variant="ghost" size="sm" className="h-7 w-7 shrink-0 p-0"
                  aria-label="Снять подсветку"
                  onClick={onClearFocus}
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          </div>
        )}

        {/* Ничего не найдено — иначе после неудачного поиска остаётся пустой
            холст без объяснения, почему все фигуры погасли. */}
        {searchQuery.trim() && visibleIds.size === 0 && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6">
            <div data-canvas-overlay className="pointer-events-auto max-w-xs rounded-xl border bg-card/95 p-4 text-center shadow-lg backdrop-blur">
              <Search className="mx-auto h-5 w-5 text-muted-foreground" />
              <p className="mt-2 text-sm font-medium">Шаги не найдены</p>
              <p className="mt-1 text-xs text-muted-foreground">
                По этому запросу на карте нет ни одной фигуры.
              </p>
              <Button
                variant="outline" size="sm" className="mt-3 h-7 text-xs"
                onClick={() => setSearchQuery('')}
              >
                Сбросить поиск
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
