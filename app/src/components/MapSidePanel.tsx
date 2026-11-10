import { useState } from 'react'
import type React from 'react'
import { History, Users, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { PassportSection } from '@/components/PassportSection'
import { ProcessStats } from '@/components/ProcessStats'
import { RolesDialog } from '@/components/RolesDialog'
import type { BusinessProcess, ProcessPassport } from '@/types/process'

interface MapSidePanelProps {
  /** Страница, которую показывает холст. */
  page: BusinessProcess
  /** Паспорт всей карты — у её частей паспорт свой, но правят паспорт карты. */
  passport: ProcessPassport
  onChangePassport: (patch: Partial<ProcessPassport>) => void
  onChangeLaneRate: (laneId: string, rate: number | null) => void
  onOpenHistory: () => void
  onClose: () => void
}

/**
 * Правая панель карты: паспорт, время и стоимость, история версий. Сведения
 * о карте нужны не всё время, поэтому панель скрывается — холст тогда занимает
 * всю ширину. На узком экране панель ложится поверх холста.
 */
export const MapSidePanel: React.FC<MapSidePanelProps> = ({
  page,
  passport,
  onChangePassport,
  onChangeLaneRate,
  onOpenHistory,
  onClose,
}) => {
  /** Растёт с каждой сохранённой ставкой роли: время и стоимость пересчитываются. */
  const [ratesVersion, setRatesVersion] = useState(0)
  const [rolesOpen, setRolesOpen] = useState(false)
  const ratesSaved = () => setRatesVersion((v) => v + 1)
  return (
    <aside
      aria-label="Сведения о карте"
      className="absolute inset-y-0 right-0 z-30 flex w-full max-w-80 flex-col rounded-xl border bg-card shadow-lg lg:static lg:z-auto lg:w-80 lg:shrink-0 lg:shadow-sm"
    >
      <div className="flex shrink-0 items-start justify-between gap-2 border-b px-4 pb-3 pt-3.5">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">Выписка по карте</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">Паспорт, время и стоимость — как в регламенте</p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
          onClick={onClose}
          title="Скрыть панель"
          aria-label="Скрыть панель"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4">
        <PassportSection passport={passport} onChange={onChangePassport} />

        <div className="border-t pt-4">
          <ProcessStats
            page={page}
            onChangeLaneRate={onChangeLaneRate}
            ratesVersion={ratesVersion}
            onRoleRateSaved={ratesSaved}
          />
          <Button
            variant="ghost"
            size="sm"
            className="-mx-2 mt-1 h-7 w-[calc(100%+1rem)] justify-start gap-2 px-2 text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setRolesOpen(true)}
            title="Ставки часа всех ролей — для всех карт"
          >
            <Users className="h-3.5 w-3.5" />
            Ставки всех ролей…
          </Button>
        </div>

        <section aria-label="Версии" className="border-t pt-4">
          <Button
            variant="outline"
            size="sm"
            className="w-full justify-start gap-2 text-xs"
            onClick={onOpenHistory}
            title="Что и когда менялось на карте; возврат к любой версии"
          >
            <History className="h-3.5 w-3.5" />
            История версий
          </Button>
        </section>
      </div>
      {rolesOpen && <RolesDialog open onOpenChange={setRolesOpen} onSaved={ratesSaved} />}
    </aside>
  )
}
