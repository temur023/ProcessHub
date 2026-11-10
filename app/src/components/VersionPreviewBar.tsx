import type React from 'react'
import { Eye, RotateCcw, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { plural, VERSION_SOURCE_LABELS } from '@/lib/presentation'
import { isTaskNode, type BusinessProcess, type ProcessVersion } from '@/types/process'

const dateTime = (iso: string) =>
  new Date(iso).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

/** Шаги всех страниц карты (основной и частей) по id. */
function stepIds(process: BusinessProcess): Set<string> {
  const pages = [process, ...(process.subProcesses ?? [])]
  return new Set(pages.flatMap((page) => page.nodes.filter((n) => isTaskNode(n.type)).map((n) => n.id)))
}

interface VersionPreviewBarProps {
  meta: ProcessVersion
  version: BusinessProcess
  current: BusinessProcess
  onRestore: () => void
  onClose: () => void
}

/**
 * Полоса над холстом, пока на нём версия из истории: что это за версия, чем
 * она отличается от текущей карты по шагам, и вернуть её или закрыть просмотр.
 * Раньше версию можно было только вернуть вслепую.
 */
export const VersionPreviewBar: React.FC<VersionPreviewBarProps> = ({ meta, version, current, onRestore, onClose }) => {
  const then = stepIds(version)
  const now = stepIds(current)
  const added = [...now].filter((id) => !then.has(id)).length
  const removed = [...then].filter((id) => !now.has(id)).length
  const diff = added || removed
    ? [
        added ? `с тех пор добавлено ${plural(added, 'шаг', 'шага', 'шагов')}` : '',
        removed ? `убрано ${plural(removed, 'шаг', 'шага', 'шагов')}` : '',
      ].filter(Boolean).join(', ')
    : 'шаги те же, что сейчас'
  return (
    <div
      role="status"
      className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-sky-500/40 bg-sky-500/5 px-3 py-2 text-xs"
    >
      <Eye className="h-4 w-4 shrink-0 text-sky-600 dark:text-sky-400" />
      <div className="min-w-0 flex-1">
        <p className="font-medium">
          Версия {meta.version} — только просмотр
          <span className="font-normal text-muted-foreground">
            {' · '}
            {[VERSION_SOURCE_LABELS[meta.source] ?? meta.source, dateTime(meta.savedAt), meta.author].filter(Boolean).join(' · ')}
          </span>
        </p>
        <p className="text-[11px] text-muted-foreground">
          {plural(then.size, 'шаг', 'шага', 'шагов')} в этой версии, {plural(now.size, 'шаг', 'шага', 'шагов')} сейчас: {diff}.
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <Button size="sm" className="h-7 gap-1 text-xs" onClick={onRestore}>
          <RotateCcw className="h-3 w-3" />
          Вернуть эту версию
        </Button>
        <Button variant="ghost" size="sm" className="h-7 gap-1 text-xs" onClick={onClose}>
          <X className="h-3 w-3" />
          Закрыть просмотр
        </Button>
      </div>
    </div>
  )
}
