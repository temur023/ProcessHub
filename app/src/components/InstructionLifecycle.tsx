import { useEffect, useState, type FormEvent } from 'react'
import { ArchiveRestore, FileText, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { changeInstructionLifecycle, getDeletedInstructions, KnowledgeRequestError } from '@/lib/knowledge'
import type { DeletedInstruction, Instruction } from '@/lib/knowledge'

export interface LifecycleAction { instruction: Instruction; action: 'delete' | 'restore' }
const message = (cause: unknown) => cause instanceof Error ? cause.message : 'Не удалось выполнить действие'

export function InstructionLifecycleDialog({ target, onClose, onChanged, onBusy }: {
  target: LifecycleAction; onClose: () => void; onChanged: (target: LifecycleAction) => void; onBusy: (busy: boolean) => void
}) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const deleting = target.action === 'delete'
  async function save(event: FormEvent) {
    event.preventDefault()
    if (busy || !reason.trim()) return
    setBusy(true); onBusy(true); setError('')
    try {
      await changeInstructionLifecycle(target.instruction.id, target.action, reason.trim())
      toast.success(deleting ? 'Инструкция перемещена в удалённые' : 'Инструкция восстановлена')
      onChanged(target)
    } catch (cause) { setError(message(cause)) }
    finally { setBusy(false); onBusy(false) }
  }
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}>
    <DialogContent className="sm:max-w-lg" showCloseButton={!busy} onEscapeKeyDown={event => { if (busy) event.preventDefault() }} onPointerDownOutside={event => { if (busy) event.preventDefault() }}>
      <DialogHeader><DialogTitle>{deleting ? 'Удалить инструкцию' : 'Восстановить инструкцию'}</DialogTitle><DialogDescription className="break-words leading-6">{target.instruction.title}</DialogDescription></DialogHeader>
      <form onSubmit={save} className="space-y-5">
        <p className="text-sm leading-6 text-muted-foreground">{deleting ? 'Инструкция уйдёт из рабочего реестра. Все редакции и оригиналы сохранятся; её можно будет вернуть из раздела «Удалённые».' : 'Инструкция вернётся в рабочий реестр со всеми сохранёнными редакциями. При необходимости подготовку текста можно запустить повторно.'}</p>
        <label className="block space-y-2 text-sm font-medium">{deleting ? 'Причина удаления' : 'Причина восстановления'}<Input required autoFocus maxLength={300} disabled={busy} value={reason} onChange={event => setReason(event.target.value)} placeholder="Основание для журнала действий" /></label>
        {error && <p role="alert" className="text-sm leading-6 text-destructive">{error}</p>}
        <DialogFooter><Button type="button" variant="outline" disabled={busy} onClick={onClose}>Отмена</Button><Button type="submit" variant={deleting ? 'destructive' : 'default'} disabled={busy || !reason.trim()}>{busy ? <Spinner className="size-4" /> : deleting ? <Trash2 className="size-4" /> : <ArchiveRestore className="size-4" />}{deleting ? 'Удалить инструкцию' : 'Восстановить'}</Button></DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
}

export function DeletedInstructions({ departmentId, revision, canRestore, onRestore }: {
  departmentId: string; revision: number; canRestore: boolean; onRestore: (instruction: Instruction) => void
}) {
  const [items, setItems] = useState<DeletedInstruction[]>()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    void getDeletedInstructions(departmentId, controller.signal).then(next => {
      if (!controller.signal.aborted) { setItems(next); setError('') }
    }).catch(cause => {
      if (!controller.signal.aborted) {
        setError(message(cause))
        if (cause instanceof KnowledgeRequestError && [401, 403, 404].includes(cause.status)) setItems(undefined)
      }
    })
    return () => controller.abort()
  }, [departmentId, revision, attempt])
  return <section className="mt-5" aria-label="Удалённые инструкции">
    <p className="text-sm leading-6 text-muted-foreground">Здесь сохраняются удалённые инструкции и все их редакции.{canRestore && ' Верните нужную инструкцию в рабочий реестр.'}</p>
    {error ? <div role="alert" className="mt-5 rounded-xl border p-5 text-sm"><p>{error}</p><Button className="mt-3" variant="outline" size="sm" onClick={() => setAttempt(value => value + 1)}>Повторить</Button></div>
      : !items ? <p role="status" className="mt-8 flex items-center gap-2 text-sm text-muted-foreground"><Spinner className="size-4" />Загружаем удалённые инструкции…</p>
      : !items.length ? <div className="py-16 text-center"><Trash2 className="mx-auto size-8 text-muted-foreground" /><h3 className="mt-4 text-base font-medium">Удалённых инструкций нет</h3><p className="mt-2 text-sm text-muted-foreground">Удалённые из реестра документы появятся здесь.</p></div>
      : <ul className="mt-5 divide-y overflow-hidden rounded-xl border bg-card">{items.map(item => <li key={item.id} className="flex items-start gap-3 px-5 py-5"><FileText className="mt-0.5 size-5 shrink-0 text-muted-foreground" /><div className="min-w-0 flex-1"><h3 className="break-words text-sm font-medium leading-6">{item.title}</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">Удалена {new Date(item.deleted_at).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' })} · Редакций: {item.version_count}</p></div>{canRestore && <Button variant="outline" size="sm" onClick={() => onRestore(item)} aria-label={`Восстановить ${item.title}`}><ArchiveRestore className="size-4" />Восстановить</Button>}</li>)}</ul>}
  </section>
}
