import { useState } from 'react'
import type React from 'react'
import { ChevronDown } from 'lucide-react'
import {
  PROCESS_STATUS_LABELS,
  type ProcessPassport,
  type ProcessStage,
  type ProcessStatus,
} from '@/types/process'

interface PassportSectionProps {
  passport: ProcessPassport
  onChange: (patch: Partial<ProcessPassport>) => void
}

const STATUSES = Object.keys(PROCESS_STATUS_LABELS) as ProcessStatus[]

/** Заголовок раздела выписки. */
export const LedgerHeading: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <h3 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.07em] text-muted-foreground">{children}</h3>
)

/**
 * Строка выписки: название слева, значение справа, между ними — точечная
 * направляющая, как в банковской выписке. Глаз идёт по строке и не теряет,
 * чьё это значение.
 */
export const LedgerRow: React.FC<{ label: string; hint?: string; children: React.ReactNode }> = ({ label, hint, children }) => (
  <div className="flex min-w-0 items-baseline gap-2 py-[5px] text-[13px]" title={hint}>
    <dt className="shrink-0 text-muted-foreground">{label}</dt>
    <span aria-hidden className="min-w-3 flex-1 -translate-y-[3px] border-b border-dotted border-border" />
    <dd className="min-w-0 text-right font-medium tabular-nums">{children}</dd>
  </div>
)

const selectClass = 'max-w-full cursor-pointer appearance-none truncate rounded bg-transparent py-0.5 pl-1 pr-5 text-right font-medium outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring'

/**
 * Паспорт процесса: владелец, подразделение, статус и описание. На рисунке их
 * нет — заполняют здесь, прямо в строке выписки. Поле сохраняется, когда из
 * него уходят (или по Enter); правка паспорта отменяется Ctrl+Z на холсте.
 */
export const PassportSection: React.FC<PassportSectionProps> = ({ passport, onChange }) => (
  <section aria-label="Паспорт процесса">
    <LedgerHeading>Паспорт процесса</LedgerHeading>
    <dl>
      <LedgerRow label="Статус">
        <span className="relative inline-flex max-w-full items-center">
          <select aria-label="Статус процесса" value={passport.status}
            onChange={(e) => onChange({ status: e.target.value as ProcessStatus })} className={selectClass}>
            {STATUSES.map((status) => <option key={status} value={status}>{PROCESS_STATUS_LABELS[status]}</option>)}
          </select>
          <ChevronDown aria-hidden className="pointer-events-none absolute right-0.5 size-3.5 text-muted-foreground" />
        </span>
      </LedgerRow>
      <LedgerRow label="Вид модели" hint="Нагрузку ролей считают только по картам «как есть»">
        <span className="relative inline-flex max-w-full items-center">
          <select aria-label="Вид модели" value={passport.stage ?? ''}
            onChange={(e) => onChange({ stage: (e.target.value || null) as ProcessStage | null })}
            className={`${selectClass} ${passport.stage ? '' : 'font-normal text-muted-foreground'}`}>
            <option value="">Не указан</option>
            <option value="as_is">AS-IS — как идёт сейчас</option>
            <option value="to_be">TO-BE — каким должен стать</option>
          </select>
          <ChevronDown aria-hidden className="pointer-events-none absolute right-0.5 size-3.5 text-muted-foreground" />
        </span>
      </LedgerRow>
      <InlineField key={`owner:${passport.owner}`} label="Владелец" value={passport.owner}
        placeholder="не указан" onCommit={(owner) => onChange({ owner })} />
      <InlineField key={`dep:${passport.department}`} label="Подразделение" value={passport.department}
        placeholder="не указано" onCommit={(department) => onChange({ department })} />
    </dl>
    <Description key={`desc:${passport.description}`} value={passport.description}
      onCommit={(description) => onChange({ description })} />
  </section>
)

/** Значение строки, которое правится прямо в ней: черновик уходит в карту, когда из поля уходят. */
const InlineField: React.FC<{ label: string; value: string; placeholder: string; onCommit: (value: string) => void }> = ({ label, value, placeholder, onCommit }) => {
  const [draft, setDraft] = useState(value)
  const commit = () => {
    const next = draft.trim()
    if (next !== value) onCommit(next)
  }
  return (
    <LedgerRow label={label}>
      <input aria-label={label} value={draft} placeholder={placeholder} title={draft || undefined}
        onChange={(e) => setDraft(e.target.value)} onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
          if (e.key === 'Escape') setDraft(value)
        }}
        className="w-full min-w-24 truncate rounded bg-transparent px-1 py-0.5 text-right font-medium outline-none placeholder:font-normal placeholder:text-muted-foreground hover:bg-muted focus:bg-muted focus-visible:ring-2 focus-visible:ring-ring" />
    </LedgerRow>
  )
}

const Description: React.FC<{ value: string; onCommit: (value: string) => void }> = ({ value, onCommit }) => {
  const [draft, setDraft] = useState(value)
  return (
    <label className="mt-2 block">
      <span className="sr-only">Описание</span>
      <textarea aria-label="Описание" value={draft} rows={2} placeholder="Описание: цель процесса, начало и результат"
        onChange={(e) => setDraft(e.target.value)} onBlur={() => draft.trim() !== value && onCommit(draft.trim())}
        onKeyDown={(e) => e.key === 'Escape' && setDraft(value)}
        className="w-full resize-y rounded-md border border-transparent bg-muted/40 px-2 py-1.5 text-xs leading-5 outline-none placeholder:text-muted-foreground hover:border-border focus:border-border focus-visible:ring-2 focus-visible:ring-ring" />
    </label>
  )
}
