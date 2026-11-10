import { useEffect, useMemo, useState } from 'react'
import type React from 'react'
import { Search } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Spinner } from '@/components/ui/spinner'
import { fetchRoles, setRoleRate, type Role } from '@/lib/api'
import { formatDuration, formatMoney, plural } from '@/lib/presentation'

interface RolesDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Ставку роли сохранили — цифры открытой карты надо пересчитать. */
  onSaved: () => void
}

/**
 * Справочник ролей: дорожки всех карт, сколько у роли карт, шагов и работы,
 * и её ставка часа — одна для всех карт. Ставку задают здесь или в разбивке
 * по дорожкам открытой карты.
 */
export const RolesDialog: React.FC<RolesDialogProps> = ({ open, onOpenChange, onSaved }) => (
  <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="flex max-h-[88dvh] flex-col sm:max-w-2xl">
      <RolesList onSaved={onSaved} />
    </DialogContent>
  </Dialog>
)

const RolesList: React.FC<{ onSaved: () => void }> = ({ onSaved }) => {
  const [data, setData] = useState<{ defaultRate: number; roles: Role[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')

  const load = () =>
    fetchRoles()
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))

  // Список собирается по всем картам — один раз при открытии окна.
  useEffect(() => void load(), [])

  const roles = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return (data?.roles ?? []).filter((r) => !needle || r.name.toLowerCase().includes(needle))
  }, [data, query])
  const withRate = data?.roles.filter((r) => r.rate !== null).length ?? 0

  return (
    <>
      <DialogHeader>
        <DialogTitle>Ставки ролей</DialogTitle>
        <DialogDescription>
          Роль — дорожка с одним названием на любых картах. Её ставка часа действует во всех картах; без ставки
          роль считается по ставке по умолчанию
          {data ? ` — ${formatMoney(data.defaultRate)}/ч` : ''}.
          {data ? ` Ставка задана у ${withRate} из ${data.roles.length} ролей.` : ''}
        </DialogDescription>
      </DialogHeader>
      <label className="flex h-9 shrink-0 items-center gap-1.5 rounded-md border px-2 text-sm">
        <Search className="h-4 w-4 text-muted-foreground" />
        <input
          autoFocus
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Название роли"
          className="min-w-0 flex-1 bg-transparent outline-none"
        />
      </label>
      <div className="min-h-0 flex-1 overflow-y-auto rounded-md border">
        {error ? (
          <p className="p-3 text-xs text-rose-600 dark:text-rose-400">{error}</p>
        ) : !data ? (
          <p className="flex items-center gap-2 p-3 text-xs text-muted-foreground">
            <Spinner className="h-3.5 w-3.5" /> Собираем роли со всех карт…
          </p>
        ) : (
          <ul className="divide-y">
            {roles.map((role) => (
              <RoleRow
                key={`${role.name}:${role.rate ?? ''}`}
                role={role}
                defaultRate={data.defaultRate}
                onSaved={() => {
                  void load()
                  onSaved()
                }}
              />
            ))}
            {roles.length === 0 && <li className="p-3 text-xs text-muted-foreground">Ничего не найдено</li>}
          </ul>
        )}
      </div>
    </>
  )
}

const RoleRow: React.FC<{ role: Role; defaultRate: number; onSaved: () => void }> = ({ role, defaultRate, onSaved }) => {
  const shown = role.rate === null ? '' : String(role.rate)
  const [draft, setDraft] = useState(shown)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const commit = () => {
    if (draft === shown) return
    const raw = draft.replace(/\s+/g, '')
    const rate = raw ? Math.round(Number(raw)) : null
    if (rate !== null && (!Number.isFinite(rate) || rate < 0)) {
      setDraft(shown)
      return
    }
    setSaving(true)
    setError(null)
    setRoleRate(role.name, rate)
      .then(onSaved)
      .catch((err) => {
        setDraft(shown)
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => setSaving(false))
  }

  return (
    <li className="flex items-center gap-3 px-3 py-2">
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium" title={role.name}>{role.name}</p>
        <p className="truncate text-[11px] text-muted-foreground">
          {[
            plural(role.maps, 'карта', 'карты', 'карт'),
            plural(role.steps, 'шаг', 'шага', 'шагов'),
            role.workMinutes ? `работа ${formatDuration(role.workMinutes)}` : '',
          ].filter(Boolean).join(' · ')}
        </p>
        {error && <p className="text-[11px] text-rose-600 dark:text-rose-400">{error}</p>}
      </div>
      <input
        type="number"
        min={0}
        step={1000}
        inputMode="numeric"
        value={draft}
        disabled={saving}
        placeholder={defaultRate.toLocaleString('ru-RU')}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
          if (e.key === 'Escape') setDraft(shown)
        }}
        aria-label={`Ставка часа роли «${role.name}», сум/ч`}
        title="Сум/ч, для всех карт. Пусто — ставка по умолчанию"
        className="h-8 w-32 shrink-0 rounded-md border bg-background px-2 text-right text-xs tabular-nums"
      />
    </li>
  )
}
