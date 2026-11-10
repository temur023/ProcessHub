import type { BusinessProcess } from '@/types/process'

/**
 * Карта, только что импортированная и сохранённая, — для экрана карты.
 *
 * После импорта экран переходит на адрес новой карты; без передачи он заново
 * скачал бы с сервера то, что у него уже есть (у «Харид…» это 12 МБ). Ячейка
 * одна и при чтении не очищается: StrictMode вызывает инициализатор состояния
 * дважды, и второй вызов не должен остаться без карты.
 */
let handedOver: BusinessProcess | null = null

export function handOverProcess(process: BusinessProcess): string {
  handedOver = process
  return `/process/${process.id}`
}

export function handedOverProcess(processId: string): BusinessProcess | null {
  return handedOver?.id === processId ? handedOver : null
}
