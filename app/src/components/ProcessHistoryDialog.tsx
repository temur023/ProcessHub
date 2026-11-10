import React, { useEffect, useState } from 'react'
import { Eye, History, Loader2, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { ProcessVersion } from '@/types/process'
import { fetchProcessVersions } from '@/lib/api'
import { plural, VERSION_SOURCE_LABELS } from '@/lib/presentation'

interface ProcessHistoryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  processId: string
  /** Ревизия карты на экране: сменилась — список перечитывается. */
  revision?: number | null
  /** Вернуть карту к версии; обещание — пока сервер сохраняет. */
  onRestore: (version: number) => Promise<void>
  /** Показать версию на холсте только для чтения — до того, как её вернуть. */
  onPreview: (version: ProcessVersion) => void
}

const dateTime = (iso: string) =>
  new Date(iso).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

/**
 * История версий карты: что, когда, кто и откуда менял, и возврат к любой.
 *
 * Правки холста сохраняются каждые полсекунды, поэтому сервер склеивает их:
 * одна версия — правки одного сотрудника подряд в течение 10 минут.
 * Восстановление не стирает историю, а сохраняет старое содержимое новой версией.
 */
export const ProcessHistoryDialog: React.FC<ProcessHistoryDialogProps> = ({
  open,
  onOpenChange,
  processId,
  revision,
  onRestore,
  onPreview,
}) => {
  const [state, setState] = useState<{ key: string; versions: ProcessVersion[] | null; error: string | null } | null>(null)
  const [restoring, setRestoring] = useState<number | null>(null)
  const key = `${processId}:${revision ?? ''}`

  useEffect(() => {
    if (!open) return
    let alive = true
    fetchProcessVersions(processId)
      .then((versions) => alive && setState({ key, versions, error: null }))
      .catch((err) => alive && setState({ key, versions: null, error: err instanceof Error ? err.message : String(err) }))
    return () => {
      alive = false
    }
  }, [open, processId, key])

  const current = state?.key === key ? state : null

  const restore = (version: number) => {
    setRestoring(version)
    void onRestore(version).finally(() => setRestoring(null))
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base font-bold">
            <History className="h-4 w-4" />
            История версий
          </DialogTitle>
          <DialogDescription className="text-xs">
            Правки одного сотрудника подряд в течение 10 минут — одна версия. Восстановление не
            стирает историю: старая карта сохраняется новой версией.
          </DialogDescription>
        </DialogHeader>

        {!current ? (
          <p className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Загружаем историю…
          </p>
        ) : current.error ? (
          <p className="py-6 text-sm text-rose-600 dark:text-rose-400">История не загрузилась. {current.error}</p>
        ) : (
          <ol className="max-h-[60vh] divide-y overflow-y-auto rounded-lg border">
            {current.versions!.map((v, index) => (
              <li key={v.version} className="flex items-start gap-3 px-3 py-2.5">
                <span className="mt-0.5 w-8 shrink-0 text-xs font-semibold tabular-nums text-muted-foreground">
                  v{v.version}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">
                    {VERSION_SOURCE_LABELS[v.source] ?? v.source}
                    {v.note && <span className="font-normal text-muted-foreground"> · {v.note}</span>}
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {dateTime(v.createdAt)}
                    {v.savedAt !== v.createdAt && dateTime(v.savedAt) !== dateTime(v.createdAt) && ` — ${dateTime(v.savedAt)}`}
                    {v.author && (
                      <>
                        {' · '}
                        <span className="font-medium text-foreground/80" title={v.authorEmail ?? undefined}>
                          {v.author}
                        </span>
                      </>
                    )}
                    {' · '}
                    {plural(v.stepCount, 'шаг', 'шага', 'шагов')}, {plural(v.nodeCount, 'фигура', 'фигуры', 'фигур')}
                  </p>
                </div>
                {index === 0 ? (
                  <span className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                    текущая
                  </span>
                ) : (
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 gap-1 px-2 text-xs"
                      onClick={() => onPreview(v)}
                      title="Посмотреть карту этой версии — без изменений"
                    >
                      <Eye className="h-3 w-3" />
                      Посмотреть
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 gap-1 text-xs"
                      disabled={restoring !== null}
                      onClick={() => restore(v.version)}
                    >
                      {restoring === v.version ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
                      Вернуть
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ol>
        )}
      </DialogContent>
    </Dialog>
  )
}
