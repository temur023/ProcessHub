import React, { useState, useRef } from 'react'
import { AlertCircle, Copy, RefreshCw, UploadCloud, X } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { importDrawioFileApi, SameNameError, type SameNameMap } from '@/lib/api'
import { plural } from '@/lib/presentation'
import type { BusinessProcess } from '@/types/process'

/** Карты банка бывают по 12 МБ («Харид бизнес жараёнлар», 12 страниц); предел тот же, что у бэкенда. */
const MAX_FILE_MB = 30

interface ProcessImportModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Карта разобрана бэкендом — значит, уже прошла его проверку. */
  onProcessLoaded: (process: BusinessProcess, options: { checked: boolean }) => void
}

/**
 * Импорт карты из файла: перетащить в окно или выбрать с диска. Разбор,
 * регламент и проверка — на бэкенде; окно только принимает файл.
 *
 * Если карта с таким названием уже есть, бэкенд не заводит вторую, а
 * спрашивает: окно предлагает обновить её из файла (новая версия в истории)
 * или сохранить отдельной картой. Так на платформе и копились дубли.
 */
export const ProcessImportModal: React.FC<ProcessImportModalProps> = ({
  open,
  onOpenChange,
  onProcessLoaded,
}) => {
  const [isProcessing, setIsProcessing] = useState(false)
  const [isDragging, setIsDragging] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  /** Файл, для которого нашлась карта с тем же названием: ждёт выбора сотрудника. */
  const [sameName, setSameName] = useState<{ file: File; existing: SameNameMap[] } | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const close = (next: boolean) => {
    if (!next) {
      setSameName(null)
      setErrorMsg(null)
    }
    onOpenChange(next)
  }

  const handleFileUpload = async (file: File, options: { replace?: string; asNew?: boolean } = {}) => {
    if (file.size > MAX_FILE_MB * 1024 * 1024) {
      setErrorMsg(`Файл больше ${MAX_FILE_MB} МБ`)
      return
    }
    setIsProcessing(true)
    setErrorMsg(null)
    try {
      const process = await importDrawioFileApi(file, options)
      onProcessLoaded(process, { checked: true })
      close(false)
    } catch (err) {
      if (err instanceof SameNameError) {
        setSameName({ file, existing: err.existing })
        return
      }
      console.error('Import error:', err)
      setErrorMsg(err instanceof Error && err.message ? err.message : 'Не удалось разобрать файл')
    } finally {
      setIsProcessing(false)
    }
  }

  const pickFile = () => {
    if (!isProcessing) fileInputRef.current?.click()
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-h-[92dvh] max-w-lg overflow-y-auto p-5 sm:max-w-lg sm:p-6">
        <DialogHeader className="space-y-1 text-left">
          <DialogTitle className="text-lg font-semibold tracking-tight">Импорт карты</DialogTitle>
          <DialogDescription className="text-sm text-muted-foreground">
            Загрузите схему из draw.io — платформа соберёт по ней карту, регламент и проверку.
          </DialogDescription>
        </DialogHeader>

        <input
          type="file"
          ref={fileInputRef}
          accept=".drawio,.xml,.bpmn,.txt"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) void handleFileUpload(file)
            e.target.value = ''
          }}
        />
        {sameName ? (
          <SameNameChoice
            fileName={sameName.file.name}
            existing={sameName.existing}
            busy={isProcessing}
            onReplace={(id) => void handleFileUpload(sameName.file, { replace: id })}
            onAsNew={() => void handleFileUpload(sameName.file, { asNew: true })}
            onCancel={() => setSameName(null)}
          />
        ) : (
          <div
            role="button"
            tabIndex={isProcessing ? -1 : 0}
            aria-disabled={isProcessing}
            aria-label="Выбрать файл схемы с диска"
            onDragOver={(e) => {
              e.preventDefault()
              if (!isProcessing) setIsDragging(true)
            }}
            onDragLeave={(e) => {
              // Переход на дочерний элемент — не уход из области.
              if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setIsDragging(false)
            }}
            onDrop={(e) => {
              e.preventDefault()
              setIsDragging(false)
              const file = e.dataTransfer.files[0]
              if (file && !isProcessing) void handleFileUpload(file)
            }}
            onClick={pickFile}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                pickFile()
              }
            }}
            className={`mt-1 flex flex-col items-center justify-center rounded-xl border border-dashed px-6 py-10 text-center outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring ${
              isProcessing
                ? 'cursor-wait border-muted-foreground/25 bg-muted/30'
                : isDragging
                ? 'cursor-copy border-emerald-500 bg-emerald-500/5'
                : 'cursor-pointer border-muted-foreground/25 bg-muted/30 hover:border-muted-foreground/50 hover:bg-muted/50'
            }`}
          >
            <div
              className={`flex h-11 w-11 items-center justify-center rounded-full border bg-background transition-colors ${
                isDragging ? 'border-emerald-500/40 text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'
              }`}
            >
              {isProcessing ? <Spinner className="h-5 w-5" /> : <UploadCloud className="h-5 w-5" />}
            </div>

            {isProcessing ? (
              <>
                <p className="mt-4 text-sm font-medium">Разбираем схему…</p>
                <p className="mt-1 text-xs text-muted-foreground">Большие карты разбираются до минуты.</p>
              </>
            ) : (
              <>
                <p className="mt-4 text-sm font-medium">
                  {isDragging ? 'Отпустите файл' : 'Перетащите файл сюда'}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  .drawio, .xml или .bpmn · до {MAX_FILE_MB} МБ
                </p>
                <Button size="sm" variant="outline" className="mt-5" tabIndex={-1}>
                  Выбрать файл
                </Button>
              </>
            )}
          </div>
        )}

        {errorMsg && (
          <div role="alert" className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            {/* whitespace-pre-line: предпроверка перечисляет находки
                построчно, и без сохранения переносов список склеивается
                в одну строку, из которой ничего не вычитать. */}
            <p className="min-w-0 flex-1 whitespace-pre-line break-words">{errorMsg}</p>
            <button
              type="button"
              onClick={() => setErrorMsg(null)}
              aria-label="Скрыть ошибку"
              className="shrink-0 rounded p-0.5 hover:bg-destructive/10"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

const dateTime = (iso: string) =>
  new Date(iso).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

/** Выбор, когда карта с таким названием уже есть: обновить её или сохранить отдельной. */
const SameNameChoice: React.FC<{
  fileName: string
  existing: SameNameMap[]
  busy: boolean
  onReplace: (id: string) => void
  onAsNew: () => void
  onCancel: () => void
}> = ({ fileName, existing, busy, onReplace, onAsNew, onCancel }) => (
  <div className="mt-1 space-y-4 rounded-xl border bg-muted/30 p-4">
    <div>
      <p className="text-sm font-medium">
        {existing.length > 1 ? 'Карты с таким названием уже есть' : 'Карта с таким названием уже есть'}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        Файл «{fileName}». Обновите существующую карту — файл ляжет в неё новой версией, прежнюю можно вернуть
        из истории. Отдельная карта нужна, только если это другой процесс.
      </p>
    </div>
    <ul className="space-y-2">
      {existing.map((map) => (
        <li key={map.id} className="flex items-center gap-3 rounded-lg border bg-background px-3 py-2">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium" title={map.name}>{map.name}</p>
            <p className="truncate text-[11px] text-muted-foreground">
              {[
                plural(map.stepCount, 'шаг', 'шага', 'шагов'),
                map.updatedAt && `изменена ${dateTime(map.updatedAt)}`,
                map.updatedBy,
              ].filter(Boolean).join(' · ')}
            </p>
          </div>
          <Button size="sm" className="shrink-0 gap-1.5" disabled={busy} onClick={() => onReplace(map.id)}>
            {busy ? <Spinner className="h-3.5 w-3.5" /> : <RefreshCw className="h-3.5 w-3.5" />}
            Обновить
          </Button>
        </li>
      ))}
    </ul>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
        Выбрать другой файл
      </Button>
      <Button size="sm" variant="outline" className="gap-1.5" disabled={busy} onClick={onAsNew}>
        <Copy className="h-3.5 w-3.5" />
        Сохранить отдельной картой
      </Button>
    </div>
  </div>
)
