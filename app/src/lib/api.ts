import type { BusinessProcess, CanvasLayout, ProcessStats, ProcessVersion } from '@/types/process'
import type { Catalog, CatalogKind, CatalogNode } from '@/types/catalog'
import type { Profile, User } from '@/lib/auth'

const API_BASE = '/api/v1'

let signedOut: () => void = () => {}

/** Что делать, когда сервер ответил «войдите»: сессия кончилась или из неё вышли в другой вкладке. */
export function onSignedOut(handler: () => void): void {
  signedOut = handler
}

/** `fetch` к API: ответ 401 значит, что сессии больше нет, — приложение показывает вход. */
export async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const res = await fetch(input, init)
  if (res.status === 401) signedOut()
  return res
}

/**
 * Единственный источник истины — бэкенд.
 *
 * Разбор draw.io, раскладка, трассировка связей, диагностика и сборка всех
 * выгрузок живут в `backend/app/services`. Раньше рядом лежали их клиентские
 * двойники и молча подменяли сервер, когда он недоступен, — а результат при
 * этом отличался: у клиентского трассировщика, например, не было обхода фигур,
 * и на двенадцати картах банка он давал девяносто линий сквозь чужие шаги
 * против нуля у серверного. Двойники убраны: без бэкенда операция честно не
 * выполняется, и сотрудник об этом узнаёт сразу.
 */
const BACKEND_REQUIRED =
  'Нужен запущенный бэкенд FastAPI: разбор и сборка выгрузок выполняются только там. ' +
  'Запустите backend/start.sh и повторите.'

/** Экспорт невозможен без бэкенда — вызывающий код обязан показать это пользователю. */
export class ExportUnavailableError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ExportUnavailableError'
  }
}

/** Load process from backend store */
export async function loadProcessFromBackend(processId: string): Promise<BusinessProcess | null> {
  try {
    const res = await apiFetch(`${API_BASE}/processes/${encodeURIComponent(processId)}`)
    if (res.ok) {
      return await res.json()
    }
  } catch (err) {
    console.warn('Failed to load process from backend:', err)
  }
  return null
}

/**
 * Платформа отказалась принять карту: в исходнике дефект, из-за которого
 * PIX не откроет выгрузку. Это не сбой связи, а осознанный ответ бэкенда —
 * его текст нужно показать сотруднику целиком, вместе с адресом фигуры.
 */
export class SourceRejectedError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'SourceRejectedError'
  }
}

/**
 * Отличает «бэкенд отверг файл» от «бэкенда нет».
 *
 * Отказ по существу (4xx) обязан долететь до экрана вместе с текстом бэкенда:
 * там названа фигура, из-за которой PIX не откроет выгрузку. Сбой сервиса
 * (5xx, обрыв связи) — другое сообщение: чинить надо не схему, а бэкенд.
 */
async function rejectionFrom(res: Response): Promise<SourceRejectedError | null> {
  if (res.ok || res.status >= 500) return null
  let detail = ''
  try {
    const body = await res.json()
    detail = typeof body?.detail === 'string' ? body.detail : ''
  } catch {
    detail = ''
  }
  return new SourceRejectedError(
    detail || `Платформа не приняла файл (код ${res.status}).`,
  )
}

/** Карта с тем же названием, что у загружаемого файла. */
export interface SameNameMap {
  id: string
  name: string
  stepCount: number
  updatedAt: string | null
  updatedBy: string | null
}

/**
 * На платформе уже есть карта с таким названием: бэкенд не заводит вторую,
 * а спрашивает — обновить её из файла или сохранить отдельной картой.
 */
export class SameNameError extends Error {
  readonly existing: SameNameMap[]

  constructor(message: string, existing: SameNameMap[]) {
    super(message)
    this.name = 'SameNameError'
    this.existing = existing
  }
}

/**
 * Разбор файла .drawio / .xml / .bpmn — на бэкенде. `replace` — обновить эту
 * карту из файла (новая версия), `asNew` — отдельная карта при совпадении названия.
 */
export async function importDrawioFileApi(
  file: File,
  options: { replace?: string; asNew?: boolean } = {},
): Promise<BusinessProcess> {
  const formData = new FormData()
  formData.append('file', file)
  const query = new URLSearchParams()
  if (options.replace) query.set('replace', options.replace)
  if (options.asNew) query.set('asNew', 'true')

  let res: Response
  try {
    const search = query.toString()
    res = await apiFetch(`${API_BASE}/import/file${search ? `?${search}` : ''}`, { method: 'POST', body: formData })
  } catch {
    throw new ExportUnavailableError(BACKEND_REQUIRED)
  }

  if (res.status === 409) {
    const detail = (await res.clone().json().catch(() => null))?.detail
    if (detail?.code === 'same_name') throw new SameNameError(detail.message, detail.existing ?? [])
  }
  const rejected = await rejectionFrom(res)
  if (rejected) throw rejected
  if (!res.ok) throw new ExportUnavailableError(`Сервер вернул ${res.status}. ${BACKEND_REQUIRED}`)
  return await res.json()
}

/**
 * Пересчитывает метрики и замечания сохранённой карты и возвращает её заново.
 *
 * Считает бэкенд: у него полный набор правил проверки и та же реализация
 * conformance, что при импорте. Пересчёт записывается в хранилище.
 */
export async function recalculateOnBackend(processId: string): Promise<BusinessProcess | null> {
  try {
    const res = await apiFetch(`${API_BASE}/analytics/${encodeURIComponent(processId)}/mining/recalculate`, {
      method: 'POST',
    })
    if (!res.ok) return null
  } catch {
    return null
  }
  return loadProcessFromBackend(processId)
}

/**
 * Сохраняет карту на бэкенде и возвращает её уже проверенной.
 *
 * При сохранении бэкенд заново считает замечания по тому, что в карте сейчас.
 * `null` — бэкенд недоступен: карта не сохранена и не проверена, и говорить
 * о ней «замечаний нет» нельзя.
 */
/** Карту уже сохранили по более свежей копии: правка сделана по устаревшей. */
export class ProcessConflictError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ProcessConflictError'
  }
}

/**
 * Сохранение карты. `null` — бэкенд недоступен; `ProcessConflictError` —
 * на сервере карта новее той, что правили (её `revision` устарел).
 */
export async function saveProcessToBackend(process: BusinessProcess): Promise<BusinessProcess | null> {
  let res: Response
  try {
    res = await apiFetch(`${API_BASE}/processes/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(process),
    })
  } catch {
    return null
  }
  if (res.status === 409) {
    const body = await res.json().catch(() => null)
    throw new ProcessConflictError(body?.detail ?? 'Карту уже изменили')
  }
  return res.ok ? await res.json() : null
}

/** Карта такой, какой она была в версии истории, — для просмотра перед восстановлением. */
export function fetchProcessVersion(processId: string, version: number): Promise<BusinessProcess> {
  return catalogRequest<BusinessProcess>(
    `${API_BASE}/processes/${encodeURIComponent(processId)}/versions/${version}`,
  )
}

/** Карта в формате draw.io — то, что открывает встроенный редактор. */
export async function fetchDrawioXml(processId: string): Promise<string> {
  const res = await apiFetch(`${API_BASE}/import/${encodeURIComponent(processId)}/export/drawio`).catch(() => null)
  if (!res) throw new ExportUnavailableError(BACKEND_REQUIRED)
  if (!res.ok) throw new ExportUnavailableError(`Сервер вернул ${res.status}`)
  return await res.text()
}

/** Новая карта из рисунка draw.io: сервер разбирает его так же, как загруженный файл, и сохраняет. */
export async function importDrawioXml(xml: string, fileName: string): Promise<BusinessProcess> {
  const res = await apiFetch(`${API_BASE}/import/xml`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ xml, fileName, fromEditor: true }),
  }).catch(() => null)
  if (!res) throw new ExportUnavailableError(BACKEND_REQUIRED)
  const body = await res.json().catch(() => null)
  if (!res.ok) throw new ExportUnavailableError(typeof body?.detail === 'string' ? body.detail : `Сервер вернул ${res.status}`)
  return body
}

/**
 * Рисунок из встроенного draw.io — в ту же карту новой версией. `revision` —
 * ревизия карты, открытой в редакторе: по устаревшей сервер отвечает 409.
 */
export async function saveDrawioToBackend(
  processId: string,
  xml: string,
  revision: number | null | undefined,
): Promise<BusinessProcess> {
  const res = await apiFetch(`${API_BASE}/import/${encodeURIComponent(processId)}/drawio`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ xml, revision: revision ?? null }),
  }).catch(() => null)
  if (!res) throw new ExportUnavailableError(BACKEND_REQUIRED)
  const body = await res.json().catch(() => null)
  if (res.status === 409) throw new ProcessConflictError(body?.detail ?? 'Карту уже изменили')
  if (!res.ok) throw new ExportUnavailableError(typeof body?.detail === 'string' ? body.detail : `Сервер вернул ${res.status}`)
  return body
}

/** История версий карты, новые сверху. */
export async function fetchProcessVersions(processId: string): Promise<ProcessVersion[]> {
  const res = await apiFetch(`${API_BASE}/processes/${encodeURIComponent(processId)}/versions`).catch(() => null)
  if (!res) throw new ExportUnavailableError(BACKEND_REQUIRED)
  if (!res.ok) throw new ExportUnavailableError(`Сервер вернул ${res.status}`)
  return await res.json()
}

/** Вернуть карту к версии: сервер сохраняет её новой версией и отдаёт карту. */
export async function restoreProcessVersion(processId: string, version: number): Promise<BusinessProcess> {
  const res = await apiFetch(
    `${API_BASE}/processes/${encodeURIComponent(processId)}/versions/${version}/restore`,
    { method: 'POST' },
  ).catch(() => null)
  if (!res) throw new ExportUnavailableError(BACKEND_REQUIRED)
  if (!res.ok) throw new ExportUnavailableError(`Сервер вернул ${res.status}`)
  return await res.json()
}

/**
 * Страница карты — фигуры, связи и дорожки — на расчёт бэкенду, сжатая gzip:
 * большая страница весит мегабайты, а сжатая — сотню килобайт.
 */
async function postPage<T>(
  path: string,
  process: Pick<BusinessProcess, 'nodes' | 'edges' | 'lanes'>,
  signal?: AbortSignal,
): Promise<T> {
  const page = JSON.stringify({ nodes: process.nodes, edges: process.edges, lanes: process.lanes })
  let res: Response
  try {
    const compressed = typeof CompressionStream === 'function'
    const body = compressed
      ? await new Response(new Blob([page]).stream().pipeThrough(new CompressionStream('gzip'))).blob()
      : page
    res = await apiFetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: compressed
        ? { 'Content-Type': 'application/json', 'Content-Encoding': 'gzip' }
        : { 'Content-Type': 'application/json' },
      body,
      signal,
    })
  } catch (err) {
    if (signal?.aborted) throw err
    throw new ExportUnavailableError(BACKEND_REQUIRED)
  }
  if (!res.ok) throw new ExportUnavailableError(`Сервер вернул ${res.status}. ${BACKEND_REQUIRED}`)
  return await res.json()
}

/**
 * Линии и подписи страницы карты — тем же расчётом, что уходит в .bpmn.
 *
 * Своего трассировщика у холста больше нет: пока он был, форма линий на экране
 * и в выгрузке расходилась, по замеру приёмки, у 17–31 % связей. Страница
 * отправляется сама, а не по id: холст показывает и шаблоны, и страницы
 * файла, которых в хранилище нет отдельными записями.
 *
 * Уходят только фигуры, связи и дорожки, сжатые gzip. Карта целиком — это у
 * «Харид…» 22 МБ вместе с вложенными страницами, а прокси сервера разработки
 * задерживает тело больше мегабайта на пять секунд; сжатая страница — 60–120 КБ.
 */
export function fetchCanvasLayout(process: BusinessProcess, signal?: AbortSignal): Promise<CanvasLayout> {
  return postPage<CanvasLayout>('/canvas/layout', process, signal)
}

/** Длительность, трудоёмкость и себестоимость страницы — со всеми несохранёнными правками. */
export function fetchProcessStats(process: BusinessProcess, signal?: AbortSignal): Promise<ProcessStats> {
  return postPage<ProcessStats>('/canvas/stats', process, signal)
}

/** Одно замечание к файлу выгрузки — тем же языком, каким о нём скажет PIX. */
export interface ExportProblem {
  level: 'error' | 'warning'
  code: string
  message: string
  /** Идентификатор фигуры или связи в самом файле. */
  where?: string | null
}

export interface ExportFormatCheck {
  /** 'pmm' — по формату пакета; 'pmm/pix' — по профилю студии. */
  format: string
  ok: boolean
  errors: number
  warnings: number
  problems: ExportProblem[]
}

export interface ExportCheckReport {
  processId: string
  ok: boolean
  summary: string
  formats: ExportFormatCheck[]
}

/**
 * Проверка выгрузок правилами PIX — до того, как файл уедет в студию.
 *
 * Студия отвергает пакет целиком из-за одного дефекта и называет только код
 * ошибки; здесь те же проверки делаются заранее и с указанием фигуры. Без
 * бэкенда проверка недоступна: пакет .pmm собирает только он.
 */
export async function fetchExportCheck(
  processId: string,
  layout: PmmLayout = 'auto',
): Promise<ExportCheckReport | null> {
  try {
    const res = await apiFetch(
      `${API_BASE}/import/${encodeURIComponent(processId)}/export/check?layout=${layout}`,
    )
    if (res.ok) return await res.json()
  } catch {
    // офлайн-режим: вызывающий код покажет, что проверка недоступна
  }
  return null
}

/**
 * Текстовая выгрузка (журнал событий, регламент, реестр PIX) для предпросмотра.
 *
 * Берём тот же файл, который уедет по кнопке «Скачать»: собирают его те же
 * функции бэкенда. Клиентские генераторы, стоявшие здесь раньше, показывали
 * ДРУГОЙ текст — их и убрали вместе с остальными двойниками.
 */
export async function fetchExportText(
  processId: string,
  type: 'event-log' | 'regulation' | 'pix-json',
): Promise<string | null> {
  try {
    const res = await apiFetch(`${API_BASE}/import/${encodeURIComponent(processId)}/export/${type}`)
    if (res.ok) return await res.text()
  } catch {
    // вызывающий код покажет, что предпросмотр недоступен
  }
  return null
}

export type ExportType = 'bpmn' | 'pmm' | 'drawio' | 'event-log' | 'regulation' | 'pix-json'

/**
 * Как карта уходит в PIX. «auto» — деревом карт (главная со свёрнутыми
 * подпроцессами и дочерние карты), если в файле несколько страниц или карта
 * больше тысячи фигур: такую одной картой студия не открывает. «single» —
 * одной картой, «tree» — деревом всегда.
 */
export type PmmLayout = 'auto' | 'single' | 'tree'

/** Хвост имени файла каждой выгрузки — тот же, что ставит бэкенд. */
const EXPORT_SUFFIX: Record<ExportType, string> = {
  bpmn: '_Processet.bpmn',
  pmm: '_PIX_Map.pmm',
  drawio: '.drawio',
  'event-log': '_EventLog.csv',
  regulation: '_Regulation.csv',
  'pix-json': '_PIX_Registry.json',
}

/**
 * Имя файла выгрузки — одно на весь интерфейс и совпадающее с серверным.
 *
 * Имена жили в трёх местах: центр экспорта называл реестр «…_PIX_Schema.json»,
 * скачивание сохраняло «…_PIX_Registry.json», а журнал событий назывался в
 * интерфейсе «EventLogs», на сервере — «EventLog». Бэкенд чистит код от
 * символов, недопустимых в имени файла, — здесь то же правило.
 */
export function exportFileName(process: Pick<BusinessProcess, 'passport'>, type: ExportType): string {
  const code = (process.passport.code || '').replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^[._]+|[._]+$/g, '')
  return `${(code || 'export').slice(0, 120)}${EXPORT_SUFFIX[type]}`
}

/** Имя из `Content-Disposition`: так файл называет сам сервер. */
function serverFileName(res: Response): string | null {
  const header = res.headers.get('Content-Disposition') || ''
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1])
    } catch {
      // битая кодировка — ниже есть обычное имя
    }
  }
  const plain = /filename="([^"]+)"/i.exec(header)
  return plain ? plain[1] : null
}

/**
 * Скачивание готового файла выгрузки: собирает и отдаёт его бэкенд.
 * Возвращает имя, под которым файл сохранён, — его и показывает уведомление.
 */
export async function triggerExportDownload(
  process: BusinessProcess,
  type: ExportType,
  layout?: PmmLayout,
): Promise<string> {
  const query = type === 'pmm' && layout ? `?layout=${layout}` : ''
  const endpoint = `${API_BASE}/import/${encodeURIComponent(process.id)}/export/${type}${query}`

  let res: Response
  try {
    res = await apiFetch(endpoint)
  } catch {
    throw new ExportUnavailableError(BACKEND_REQUIRED)
  }
  if (!res.ok) {
    throw new ExportUnavailableError(`Сервер вернул ${res.status}. ${BACKEND_REQUIRED}`)
  }

  const fileName = serverFileName(res) || exportFileName(process, type)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = fileName
  a.click()
  URL.revokeObjectURL(url)
  return fileName
}

// ── Пространства и карты ────────────────────────────────────────────────────

/** Ответ бэкенда на правку дерева и профиля: при отказе — его текст («сначала уберите…»). */
async function catalogRequest<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await apiFetch(url, init)
  } catch {
    throw new Error(BACKEND_REQUIRED)
  }
  if (!res.ok) {
    let detail = ''
    try {
      const body = await res.json()
      detail = typeof body?.detail === 'string' ? body.detail : ''
    } catch {
      detail = ''
    }
    throw new Error(detail || `Сервер ответил кодом ${res.status}`)
  }
  return res.status === 204 ? (undefined as T) : await res.json()
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/** Всё дерево пространств и карт и процессы без места — одним запросом. */
export function fetchCatalog(): Promise<Catalog> {
  return catalogRequest<Catalog>(`${API_BASE}/catalog/`)
}

export function createCatalogItem(item: {
  parentId: string | null
  kind: CatalogKind
  title: string
  processId?: string | null
}): Promise<CatalogNode> {
  return catalogRequest<CatalogNode>(`${API_BASE}/catalog/items`, jsonInit('POST', item))
}

/** Меняется только переданное; `parentId: null` — пространство на верхний уровень. */
export function updateCatalogItem(
  id: string,
  changes: { title?: string; parentId?: string | null; favorite?: boolean; processId?: string | null },
): Promise<CatalogNode> {
  return catalogRequest<CatalogNode>(`${API_BASE}/catalog/items/${encodeURIComponent(id)}`, jsonInit('PATCH', changes))
}

export function deleteCatalogItem(id: string): Promise<void> {
  return catalogRequest<void>(`${API_BASE}/catalog/items/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

/** ИТ-система из справочника и сколько шагов всех карт в ней работает. */
export interface SystemUsage {
  name: string
  steps: number
}

/** Справочник ИТ-систем — подсказки для карточки шага. */
export function fetchSystems(): Promise<SystemUsage[]> {
  return catalogRequest<SystemUsage[]>(`${API_BASE}/systems/`)
}

/** Роль — дорожка карт: где работает и по какой ставке часа считается. */
export interface Role {
  name: string
  maps: number
  steps: number
  workMinutes: number
  /** Ставка роли, сум/ч; нет — по ставке по умолчанию. */
  rate: number | null
}

export function fetchRoles(): Promise<{ defaultRate: number; roles: Role[] }> {
  return catalogRequest(`${API_BASE}/roles/`)
}

/** Ставка роли для всех карт; `null` — убрать, роль снова по ставке по умолчанию. */
export function setRoleRate(name: string, rate: number | null): Promise<void> {
  return catalogRequest<void>(`${API_BASE}/roles/rate`, jsonInit('PUT', { name, rate }))
}

export type SearchField = 'step' | 'role' | 'system'

/** Шаг, найденный поиском по картам: карта, страница (пусто — основная), дорожка, система. */
export interface StepHit {
  processId: string
  processName: string
  pageId: string
  pageName: string
  nodeId: string
  nodeName: string
  laneName: string | null
  system: string | null
}

/** Шаги всех карт по названию, роли или ИТ-системе; `total` — сколько найдено всего. */
export function searchSteps(q: string, field: SearchField): Promise<{ total: number; hits: StepHit[] }> {
  const query = new URLSearchParams({ q, field })
  return catalogRequest(`${API_BASE}/search/?${query.toString()}`)
}

/** Удалить схему вместе с историей. Бэкенд удаляет только схему без пространства. */
export function deleteProcess(id: string): Promise<void> {
  return catalogRequest<void>(`${API_BASE}/processes/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

// ── Вход ────────────────────────────────────────────────────────────────────
// Здесь обычный `fetch`: 401 от входа — это «неверный пароль», а не конец сессии.

/** Вход или регистрация: сотрудник либо текст отказа сервера. */
async function authRequest(path: 'login' | 'register' | 'demo', body: object): Promise<User> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}/auth/${path}`, jsonInit('POST', body))
  } catch {
    throw new Error('Сервер не отвечает. Запустите бэкенд (backend/start.sh) и повторите.')
  }
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : `Сервер ответил кодом ${res.status}`)
  return data
}

/** Одноклик-вход включается только настройкой тестового стенда (SQB_DEMO_LOGIN). */
export async function fetchAuthOptions(): Promise<{ demo: boolean; demoEmail: string | null }> {
  const res = await fetch(`${API_BASE}/auth/options`).catch(() => null)
  return res?.ok ? await res.json() : { demo: false, demoEmail: null }
}

export function demoLogin(): Promise<User> {
  return authRequest('demo', {})
}

export function login(email: string, password: string): Promise<User> {
  return authRequest('login', { email, password })
}

export function registerUser(name: string, email: string, password: string): Promise<User> {
  return authRequest('register', { name, email, password })
}

/** Кто вошёл; `null` — никто (если сервер не отвечает, об этом скажет попытка входа). */
export async function fetchCurrentUser(): Promise<User | null> {
  const res = await fetch(`${API_BASE}/auth/me`).catch(() => null)
  return res?.ok ? await res.json() : null
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE}/auth/logout`, { method: 'POST' }).catch(() => null)
}

// ── Профиль ─────────────────────────────────────────────────────────────────

export function fetchProfile(): Promise<Profile> {
  return catalogRequest<Profile>(`${API_BASE}/profile`)
}

export function renameProfile(name: string): Promise<User> {
  return catalogRequest<User>(`${API_BASE}/profile`, jsonInit('PATCH', { name }))
}

/** Неверный текущий пароль — отказ с текстом сервера; остальные сессии закрываются. */
export function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  return catalogRequest<void>(`${API_BASE}/profile/password`, jsonInit('POST', { currentPassword, newPassword }))
}
