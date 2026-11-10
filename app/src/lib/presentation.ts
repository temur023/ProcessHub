import type { BusinessProcess, ProcessValidation } from '@/types/process'
import { isTaskNode } from '@/types/process'

/**
 * Мелочи представления, у которых нет и не должно быть двойника на бэкенде.
 *
 * Вся предметная логика платформы — разбор draw.io, раскладка, трассировка
 * связей, диагностика, сборка выгрузок — живёт в `backend/app/services`. Здесь
 * остаётся только то, что нужно самому интерфейсу: форматирование времени для
 * подписи, ключ строки отчёта, сохранение файла из браузера. Ничего из этого
 * сервер за нас не сделает, и повторением серверного алгоритма это не является.
 */

/** Размер оригинала: небольшие документы показываем в байтах и килобайтах. */
export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes)) return '—'
  const size = Math.max(0, Math.round(bytes))
  if (size < 1024) return `${size.toLocaleString('ru-RU')} Б`
  const value = size < 1024 * 1024 ? size / 1024 : size / (1024 * 1024)
  const unit = size < 1024 * 1024 ? 'КБ' : 'МБ'
  return `${value.toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ${unit}`
}

/** Человекочитаемая длительность: «45 мин», «2 ч», «1 ч 30 мин», «2 дн». */
export function formatDuration(minutes: number | undefined | null): string {
  const value = Math.round(minutes || 0)
  if (value <= 0) return ''
  if (value < 60) return `${value} мин`
  const hours = Math.floor(value / 60)
  const rest = value % 60
  if (rest) return `${hours} ч ${rest} мин`
  if (hours >= 24 && hours % 24 === 0) return `${hours / 24} дн`
  return `${hours} ч`
}

/** Сумма в сумах: «125 000 сум», крупные — «12,4 млн сум», «1,23 млрд сум». */
export function formatMoney(uzs: number): string {
  const value = Math.round(uzs || 0)
  const short = (n: number, digits: number) =>
    n.toLocaleString('ru-RU', { maximumFractionDigits: digits })
  if (Math.abs(value) >= 1e9) return `${short(value / 1e9, 2)} млрд сум`
  if (Math.abs(value) >= 1e6) return `${short(value / 1e6, Math.abs(value) < 1e7 ? 2 : 1)} млн сум`
  return `${value.toLocaleString('ru-RU')} сум`
}

/** «1 шаг», «2 шага», «5 шагов» — иначе число читается машинным. */
export function plural(count: number, one: string, few: string, many: string): string {
  const mod100 = count % 100
  const mod10 = count % 10
  if (mod100 >= 11 && mod100 <= 14) return `${count} ${many}`
  if (mod10 === 1) return `${count} ${one}`
  if (mod10 >= 2 && mod10 <= 4) return `${count} ${few}`
  return `${count} ${many}`
}

/**
 * Число шагов карты — операций регламента, а не всех её узлов.
 *
 * Уведомление об импорте писало «регламент на 164 шагов», считая шлюзы,
 * события и цилиндры: шагов в «Akkreditiv ochish» 67. Карточка шаблона — «11
 * шагов» при шести. Шаг — это то же, что считает регламент (`isTaskNode`).
 */
export function stepCount(process: Pick<BusinessProcess, 'nodes'>): number {
  return process.nodes.filter((n) => isTaskNode(n.type)).length
}

/**
 * Устойчивый ключ строки отчёта: у замечаний нет собственных id, а UI должен
 * помнить, какое из них подсвечено на карте прямо сейчас.
 */
export function issueKey(issue: ProcessValidation, index: number): string {
  return `${issue.code ?? issue.level}-${index}`
}

/** Фигуры замечания: одиночное `nodeId` — та же группа, только из одной фигуры. */
export function issueNodeIds(issue: ProcessValidation): string[] {
  if (issue.nodeIds?.length) return issue.nodeIds
  return issue.nodeId ? [issue.nodeId] : []
}

/** Имена, которые платформа придумывает сама (см. `fallback_node_name` в бэкенде). */
const GENERATED_NAME_PREFIXES = [
  'Операция ', 'Условие', 'Ожидание', 'Событие-сообщение', 'Старт',
  'Завершение', 'Подпроцесс', 'Примечание', 'Информационная система', 'Документ',
]

/**
 * Имя подставлено платформой, а не написано аналитиком.
 *
 * Холсту это нужно, чтобы не рисовать надпись, которой нет в эталоне: в draw.io
 * безымянный лист нарисован чистым, а подставленное «Документ» на карте
 * оказывается лишним текстом. Двойник по смыслу — `map_label` в бэкенде, но там
 * решение принимается при сборке выгрузки, а здесь при отрисовке.
 */
export function isGeneratedName(name: string): boolean {
  return GENERATED_NAME_PREFIXES.some((prefix) => (name || '').startsWith(prefix))
}

/** Как версия карты подписана в истории и в профиле: откуда она. */
export const VERSION_SOURCE_LABELS: Record<string, string> = {
  import: 'Импорт файла',
  edit: 'Правка карты',
  drawio: 'Правка в draw.io',
  restore: 'Восстановление',
  migration: 'Перенос из прежнего хранилища',
}

/**
 * Первая страница карты с частями — обзор всей карты, а не одна из частей.
 *
 * У банка обзор бывает двух видов: оглавление, где части стоят фигурами с
 * плюсом («Investit kredit» — 36 фигур на 39 частей), и сводная страница, где
 * процесс нарисован целиком («1.Ta'minot almashtirish» — 242 шага при самой
 * крупной части в 28, «JAMI» в «Xizmat avtotransportini…»). Но первой бывает
 * и обычная часть: «ЭЛЕКТРОН» в «Харид…» (176 шагов при частях до 524),
 * новая версия процесса рядом со старой в «Akkreditiv ochish». Правило сверено
 * на всех девяти многостраничных картах банка.
 */
export function isOverviewPage(process: Pick<BusinessProcess, 'nodes' | 'subProcesses'>): boolean {
  const parts = process.subProcesses ?? []
  if (!parts.length) return false
  const plus = process.nodes.filter((n) => n.type === 'subProcess').length
  if (plus * 2 >= parts.length) return true
  return stepCount(process) >= 2 * Math.max(...parts.map(stepCount))
}
