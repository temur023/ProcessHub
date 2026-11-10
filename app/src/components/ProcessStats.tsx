import React, { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ChevronDown, Loader2, ServerOff } from 'lucide-react'
import { LedgerHeading, LedgerRow } from '@/components/PassportSection'
import type { BusinessProcess, LaneStats, ProcessStats as Stats } from '@/types/process'
import { fetchProcessStats, setRoleRate } from '@/lib/api'
import { formatDuration, formatMoney, plural } from '@/lib/presentation'

interface ProcessStatsProps {
  /** Страница, которую показывает холст, — вместе с несохранёнными правками. */
  page: BusinessProcess
  /** Своя ставка дорожки в этой карте; здесь её только сбрасывают (`null`) — дальше действует ставка роли. */
  onChangeLaneRate?: (laneId: string, rate: number | null) => void
  /** Меняется, когда сохранили ставку роли: цифры надо пересчитать. */
  ratesVersion?: number
  /** Ставку роли сохранили — пусть пересчитается всё, что от неё зависит. */
  onRoleRateSaved?: () => void
}

/** Пауза перед пересчётом: пока фигуру двигают, запросы не летят на каждую правку. */
const STATS_DELAY = 250

/**
 * Сколько процесс длится и во что обходится — блок боковой панели карты.
 *
 * Считает бэкенд (`POST /canvas/stats`), здесь только показ. Длительность —
 * самый долгий путь от старта до финиша: у развилки XOR проходит одна ветка,
 * и сумма всех шагов (трудоёмкость) длительность завышает. Деньги — время шага
 * × ставка часа его дорожки. Ставка задаётся роли (дорожке с тем же названием)
 * сразу для всех карт; своя ставка дорожки в карте, если была, важнее.
 */
export const ProcessStats: React.FC<ProcessStatsProps> = ({ page, onChangeLaneRate, ratesVersion = 0, onRoleRateSaved }) => {
  const [open, setOpen] = useState(false)
  const [result, setResult] = useState<{ key: string; stats: Stats | null; error: string | null } | null>(null)

  // Пересчёт — только когда меняется то, от чего зависят цифры.
  const key = useMemo(
    () =>
      JSON.stringify([
        page.nodes.map((n) => [n.id, n.type, n.category, n.laneId, n.slaMinutes, n.waitMinutes,
          n.slaText, n.slaMeasured, n.costPerExecution]),
        page.edges.map((e) => [e.kind, e.sourceId, e.targetId]),
        page.lanes.map((l) => [l.id, l.name, l.hourlyRate]),
        ratesVersion,
      ]),
    [page.nodes, page.edges, page.lanes, ratesVersion],
  )

  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      fetchProcessStats(page, controller.signal)
        .then((stats) => setResult({ key, stats, error: null }))
        .catch((err) => {
          if (controller.signal.aborted) return
          setResult({ key, stats: null, error: err instanceof Error ? err.message : String(err) })
        })
    }, STATS_DELAY)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
    // Страница-объект меняется чаще, чем цифры: ждём только смены ключа.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  // Пока считается новая правка, видны прежние цифры — блок не мигает.
  const stats = result?.stats ?? null
  const pending = result?.key !== key
  const error = result && !result.stats ? result.error : null
  const defaultLanes = stats ? stats.lanes.filter((l) => l.laneId && l.rateSource === 'default').length : 0

  return (
    <section aria-label="Время и стоимость">
      <LedgerHeading>
        Время и стоимость
        {stats && pending && <Loader2 className="h-3 w-3 animate-spin" aria-label="Пересчёт" />}
      </LedgerHeading>
      {error ? (
        <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
          <ServerOff className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          Время и стоимость не посчитаны. {error}
        </p>
      ) : !stats ? (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
          Считаем время и стоимость…
        </p>
      ) : (
        <>
          <dl>
            <LedgerRow label="Длительность"
              hint={`Самый долгий путь от старта до финиша: ${plural(stats.pathStepCount, 'шаг', 'шага', 'шагов')}. У развилки берётся самая долгая ветка.`}>
              {formatDuration(stats.pathMinutes) || '0 мин'}
            </LedgerRow>
            <LedgerRow label="Трудоёмкость"
              hint={`Сумма времени всех ${plural(stats.stepCount, 'шага', 'шагов', 'шагов')} (ST) — сколько работы в процессе.`}>
              {formatDuration(stats.workMinutes) || '0 мин'}
            </LedgerRow>
            <LedgerRow label="Ожидание" hint="Время ожидания шагов (WT) и таймеры, у которых время написано на карте.">
              {formatDuration(stats.waitMinutes) || '0 мин'}
            </LedgerRow>
            <LedgerRow label="Шагов в расчёте" hint="Шаги, у которых время взято с карты, из всех шагов регламента.">
              {stats.stepCount - stats.unmeasuredStepCount} из {stats.stepCount}
            </LedgerRow>
            <div className="mt-1.5 flex items-baseline justify-between gap-3 border-t pt-2.5"
              title="Один проход всех шагов: время шага × ставка часа его дорожки, робот — цена запуска.">
              <dt className="text-[13px] font-semibold">Себестоимость</dt>
              <dd className="text-lg font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">{formatMoney(stats.costUzs)}</dd>
            </div>
          </dl>
          {(stats.unmeasuredStepCount > 0 || stats.noSystemStepCount > 0 || defaultLanes > 0 || stats.guessedSystemStepCount > 0) && (
            <div className="mt-4">
              <LedgerHeading>Неполные данные</LedgerHeading>
              <ul className="space-y-1">
                {stats.noSystemStepCount > 0 && (
                  <Gap count={`${stats.noSystemStepCount} из ${stats.stepCount}`} text="шагов без ИТ-системы"
                    hint="У шага на карте нет связанного хранилища данных — система не указана. Укажите её в карточке шага." />
                )}
                {defaultLanes > 0 && (
                  <Gap count={`${defaultLanes} из ${stats.lanes.filter((l) => l.laneId).length}`} text="дорожек — по ставке по умолчанию"
                    hint={`Себестоимость этих дорожек посчитана по ставке по умолчанию ${formatMoney(stats.defaultHourlyRate)}/ч. Задайте ставку роли в разбивке по дорожкам.`} />
                )}
                {stats.unmeasuredStepCount > 0 && (
                  <Gap count={`${stats.unmeasuredStepCount} из ${stats.stepCount}`} text="шагов без времени"
                    hint="Время этих шагов импорт подставил по типу операции. Впишите настоящее в карточке шага." />
                )}
                {stats.guessedSystemStepCount > 0 && (
                  <Gap count={plural(stats.guessedSystemStepCount, 'шаг', 'шага', 'шагов')} text="с угаданной системой"
                    hint="Система подставлена по словам в названии шага, а не взята с карты. Проверьте её в карточке шага." />
                )}
              </ul>
            </div>
          )}

          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="-mx-2 mt-2 flex w-[calc(100%+1rem)] items-center justify-between rounded-md px-2 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            По дорожкам · {stats.lanes.length}
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? 'rotate-180' : ''}`} />
          </button>
          {open && (
            <ul className="mt-1 divide-y border-y">
              {stats.lanes.map((lane) => (
                <LaneRow
                  // Ставка в ключе: пришла новая с сервера (или отмена) — поле
                  // показывает её, а не прежний черновик.
                  key={`${lane.laneId ?? '—'}:${lane.rateSource}:${lane.hourlyRate}`}
                  lane={lane}
                  total={stats.costUzs}
                  defaultRate={stats.defaultHourlyRate}
                  onResetLaneRate={onChangeLaneRate && lane.laneId ? () => onChangeLaneRate(lane.laneId!, null) : undefined}
                  onRoleRateSaved={onRoleRateSaved}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  )
}

/** Пробел в данных карты: чего не хватает, чтобы цифрам можно было верить. */
const Gap: React.FC<{ count: string; text: string; hint: string }> = ({ count, text, hint }) => (
  <li className="flex items-start gap-2 text-xs leading-5 text-muted-foreground" title={hint}>
    <AlertTriangle aria-hidden className="mt-[3px] h-3.5 w-3.5 shrink-0 text-amber-500" />
    <span><b className="font-semibold tabular-nums text-foreground">{count}</b> {text}</span>
  </li>
)

const RATE_SOURCE_HINT: Record<LaneStats['rateSource'], string> = {
  lane: 'своя ставка в этой карте',
  role: 'ставка роли — для всех карт',
  default: 'по ставке по умолчанию',
}

/**
 * Дорожка: время, деньги и ставка. Поле задаёт ставку роли — она действует во
 * всех картах, где есть дорожка с тем же названием; применяется по Enter или
 * уходу из поля, пустое — убрать ставку роли.
 */
const LaneRow: React.FC<{
  lane: LaneStats
  total: number
  defaultRate: number
  onResetLaneRate?: () => void
  onRoleRateSaved?: () => void
}> = ({ lane, total, defaultRate, onResetLaneRate, onRoleRateSaved }) => {
  const shown = lane.rateSource === 'role' ? String(lane.hourlyRate) : ''
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
    setRoleRate(lane.laneName, rate)
      .then(() => onRoleRateSaved?.())
      .catch((err) => {
        setDraft(shown)
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => setSaving(false))
  }

  const share = total > 0 ? lane.costUzs / total : 0
  const times = [
    plural(lane.stepCount, 'шаг', 'шага', 'шагов'),
    lane.workMinutes ? `работа ${formatDuration(lane.workMinutes)}` : '',
    lane.waitMinutes ? `ожидание ${formatDuration(lane.waitMinutes)}` : '',
  ].filter(Boolean)
  const editable = Boolean(lane.laneId && lane.laneName.trim())
  return (
    <li className="space-y-1 py-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="min-w-0 truncate text-xs font-medium" title={lane.laneName}>{lane.laneName}</span>
        <span className="shrink-0 text-xs tabular-nums">{formatMoney(lane.costUzs)}</span>
      </div>
      <span className="block h-1 overflow-hidden rounded-full bg-muted" aria-hidden>
        <span className="block h-full rounded-full bg-emerald-500" style={{ width: `${Math.round(share * 100)}%` }} />
      </span>
      <div className="text-[11px] text-muted-foreground">{times.join(' · ')}</div>
      {editable && lane.rateSource === 'lane' ? (
        <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
          <span>
            Своя ставка в карте: <span className="tabular-nums text-foreground">{lane.hourlyRate.toLocaleString('ru-RU')}</span>
          </span>
          {onResetLaneRate && (
            <button
              type="button"
              onClick={onResetLaneRate}
              className="rounded px-1.5 py-0.5 text-[11px] text-primary hover:bg-muted"
              title="Убрать ставку этой карты — дорожка будет считаться по ставке роли"
            >
              Сбросить
            </button>
          )}
        </div>
      ) : editable ? (
        <label className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
          <span title="Ставка роли действует во всех картах, где есть дорожка с этим названием">
            Ставка роли, сум/ч
          </span>
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
            aria-label={`Ставка часа роли «${lane.laneName}», сум/ч`}
            title="Для всех карт с этой ролью. Пусто — ставка по умолчанию"
            className="h-7 w-28 rounded-md border bg-background px-2 text-right text-xs tabular-nums text-foreground"
          />
        </label>
      ) : (
        <div className="flex justify-between text-[11px] text-muted-foreground">
          <span>Ставка, сум/ч</span>
          <span className="tabular-nums">{lane.hourlyRate.toLocaleString('ru-RU')}</span>
        </div>
      )}
      {editable && lane.rateSource !== 'lane' && (
        <div className="text-[10px] text-muted-foreground/80">{RATE_SOURCE_HINT[lane.rateSource]}</div>
      )}
      {error && <p className="text-[11px] text-rose-600 dark:text-rose-400">{error}</p>}
    </li>
  )
}
