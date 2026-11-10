import { useMemo, useState } from 'react'
import type React from 'react'
import { Folder, Search, Workflow } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { type CatalogIndex, itemLabel, matchesQuery, pathTo } from '@/lib/catalog'
import { BriefLine } from './BriefLine'
import type { UnplacedProcess } from '@/types/catalog'

/*
 * Состояние каждого диалога живёт внутри DialogContent: закрытый диалог
 * убирает содержимое, и при следующем открытии поле, выбор и ошибка — чистые.
 */

/** Действие диалога: пока идёт — кнопка занята, отказ сервера — текстом в самом диалоге. */
function useRunner(onOpenChange: (open: boolean) => void) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, run }
}

const ErrorText: React.FC<{ error: string | null }> = ({ error }) =>
  error ? <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p> : null

interface NameDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  confirmLabel: string
  initial?: string
  onConfirm: (name: string) => Promise<void>
}

/** Название нового пространства, подпространства или карты — или новое имя. */
export const NameDialog: React.FC<NameDialogProps> = (props) => (
  <Dialog open={props.open} onOpenChange={props.onOpenChange}>
    <DialogContent className="sm:max-w-md">
      <NameForm {...props} />
    </DialogContent>
  </Dialog>
)

const NameForm: React.FC<NameDialogProps> = ({ onOpenChange, title, confirmLabel, initial = '', onConfirm }) => {
  const [name, setName] = useState(initial)
  const { busy, error, run } = useRunner(onOpenChange)
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        if (name.trim()) void run(() => onConfirm(name.trim()))
      }}
      className="space-y-4"
    >
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
      </DialogHeader>
      <Input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="Название" maxLength={300} />
      <ErrorText error={error} />
      <DialogFooter>
        <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
          Отмена
        </Button>
        <Button type="submit" disabled={busy || !name.trim()}>
          {confirmLabel}
        </Button>
      </DialogFooter>
    </form>
  )
}

interface PlaceDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  index: CatalogIndex
  /** Куда класть нельзя: сам элемент и его потомки, нынешний родитель. */
  exclude?: Set<string>
  /** Можно ли вынести на верхний уровень (только для пространства). */
  allowTop?: boolean
  onConfirm: (spaceId: string | null) => Promise<void>
}

/** Выбор пространства: куда перенести элемент или положить процесс. */
export const PlaceDialog: React.FC<PlaceDialogProps> = (props) => (
  <Dialog open={props.open} onOpenChange={props.onOpenChange}>
    <DialogContent className="sm:max-w-lg">
      <PlaceForm {...props} />
    </DialogContent>
  </Dialog>
)

const PlaceForm: React.FC<PlaceDialogProps> = ({ onOpenChange, title, description, index, exclude, allowTop = false, onConfirm }) => {
  const [query, setQuery] = useState('')
  /** `undefined` — ещё не выбрано, `null` — верхний уровень. */
  const [picked, setPicked] = useState<string | null | undefined>(undefined)
  const { busy, error, run } = useRunner(onOpenChange)

  const spaces = useMemo(
    () => [...index.byId.values()].filter((n) => n.kind === 'space' && !exclude?.has(n.id) && matchesQuery(n, query)),
    [index, exclude, query],
  )
  const option = (key: string, selected: boolean, onSelect: () => void, children: React.ReactNode) => (
    <button
      key={key}
      type="button"
      role="option"
      aria-selected={selected}
      onClick={onSelect}
      className={`flex w-full items-start gap-2 rounded px-2 py-1.5 text-left text-xs ${selected ? 'bg-primary/10' : 'hover:bg-muted'}`}
    >
      <Folder className="mt-px h-3.5 w-3.5 shrink-0 text-blue-950 dark:text-white" />
      {children}
    </button>
  )

  return (
    <>
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        {description && <DialogDescription>{description}</DialogDescription>}
      </DialogHeader>
      <label className="flex h-9 items-center gap-1.5 rounded-md border px-2 text-sm">
        <Search className="h-4 w-4 text-muted-foreground" />
        <input
          autoFocus
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Пространство: название или номер"
          className="min-w-0 flex-1 bg-transparent outline-none"
        />
      </label>
      <div role="listbox" aria-label="Пространства" className="max-h-72 overflow-y-auto rounded-md border p-1">
        {allowTop && option('top', picked === null, () => setPicked(null), 'Верхний уровень — отдельное пространство')}
        {spaces.map((space) =>
          option(
            space.id,
            picked === space.id,
            () => setPicked(space.id),
            <span className="min-w-0">
              <span className="block truncate">{itemLabel(space)}</span>
              {space.parentId && (
                <span className="block truncate text-[10px] text-muted-foreground">
                  {pathTo(index, space.id).slice(0, -1).map(itemLabel).join(' › ')}
                </span>
              )}
            </span>,
          ),
        )}
        {spaces.length === 0 && !allowTop && <p className="px-2 py-1.5 text-xs text-muted-foreground">Ничего не найдено</p>}
      </div>
      <ErrorText error={error} />
      <DialogFooter>
        <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
          Отмена
        </Button>
        <Button type="button" disabled={busy || picked === undefined} onClick={() => void run(() => onConfirm(picked ?? null))}>
          Готово
        </Button>
      </DialogFooter>
    </>
  )
}

interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmLabel: string
  onConfirm: () => Promise<void>
}

export const ConfirmDialog: React.FC<ConfirmDialogProps> = (props) => (
  <Dialog open={props.open} onOpenChange={props.onOpenChange}>
    <DialogContent className="sm:max-w-md">
      <ConfirmForm {...props} />
    </DialogContent>
  </Dialog>
)

const ConfirmForm: React.FC<ConfirmDialogProps> = ({ onOpenChange, title, description, confirmLabel, onConfirm }) => {
  const { busy, error, run } = useRunner(onOpenChange)
  return (
    <>
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{description}</DialogDescription>
      </DialogHeader>
      <ErrorText error={error} />
      <DialogFooter>
        <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
          Отмена
        </Button>
        <Button type="button" variant="destructive" disabled={busy} onClick={() => void run(onConfirm)}>
          {confirmLabel}
        </Button>
      </DialogFooter>
    </>
  )
}

interface PickProcessDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Название карты каталога: схемы с похожим названием — первыми. */
  mapTitle: string
  processes: UnplacedProcess[]
  onConfirm: (processId: string) => Promise<void>
}

/** Схема для пустой карты — из уже загруженных на сервер и ещё не разложенных. */
export const PickProcessDialog: React.FC<PickProcessDialogProps> = (props) => (
  <Dialog open={props.open} onOpenChange={props.onOpenChange}>
    <DialogContent className="sm:max-w-lg">
      <PickProcessForm {...props} />
    </DialogContent>
  </Dialog>
)

/** Слова названия длиннее двух букв — по ним ищутся похожие схемы. */
const words = (text: string) => new Set(text.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((w) => w.length > 2))

const PickProcessForm: React.FC<PickProcessDialogProps> = ({ onOpenChange, mapTitle, processes, onConfirm }) => {
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<string | null>(null)
  const { busy, error, run } = useRunner(onOpenChange)

  const sorted = useMemo(() => {
    const mine = words(mapTitle)
    const score = (name: string) => [...words(name)].filter((w) => mine.has(w)).length
    const needle = query.trim().toLowerCase()
    return processes
      .filter((p) => !needle || p.name.toLowerCase().includes(needle))
      .map((p) => ({ p, score: score(p.name) }))
      .sort((a, b) => b.score - a.score || a.p.name.localeCompare(b.p.name, 'ru'))
      .map(({ p }) => p)
  }, [processes, mapTitle, query])

  return (
    <>
      <DialogHeader>
        <DialogTitle>Схема для «{mapTitle}»</DialogTitle>
        <DialogDescription>
          Схемы, которые уже загружены на платформу и ещё не лежат ни в одной карте. Похожие по названию — сверху.
        </DialogDescription>
      </DialogHeader>
      <label className="flex h-9 items-center gap-1.5 rounded-md border px-2 text-sm">
        <Search className="h-4 w-4 text-muted-foreground" />
        <input
          autoFocus
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Название схемы"
          className="min-w-0 flex-1 bg-transparent outline-none"
        />
      </label>
      <div role="listbox" aria-label="Загруженные схемы" className="max-h-72 overflow-y-auto rounded-md border p-1">
        {sorted.map((process) => (
          <button
            key={process.id}
            type="button"
            role="option"
            aria-selected={picked === process.id}
            onClick={() => setPicked(process.id)}
            onDoubleClick={() => void run(() => onConfirm(process.id))}
            className={`flex w-full items-start gap-2 rounded px-2 py-1.5 text-left text-xs ${
              picked === process.id ? 'bg-primary/10' : 'hover:bg-muted'
            }`}
          >
            <Workflow className="mt-px h-3.5 w-3.5 shrink-0 text-emerald-500" />
            <span className="min-w-0">
              <span className="block truncate" title={process.name}>{process.name}</span>
              <BriefLine brief={process} className="block truncate text-[10px]" />
            </span>
          </button>
        ))}
        {sorted.length === 0 && (
          <p className="px-2 py-1.5 text-xs text-muted-foreground">
            {processes.length ? 'Ничего не найдено' : 'Все загруженные схемы уже лежат в картах'}
          </p>
        )}
      </div>
      <ErrorText error={error} />
      <DialogFooter>
        <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
          Отмена
        </Button>
        <Button type="button" disabled={busy || !picked} onClick={() => picked && void run(() => onConfirm(picked))}>
          Положить в карту
        </Button>
      </DialogFooter>
    </>
  )
}
