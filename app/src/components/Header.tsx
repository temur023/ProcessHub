import React, { useEffect, useRef, useState } from 'react'
import { ChevronDown, Download, FileSpreadsheet, ImageIcon, PanelRightClose, PanelRightOpen, Paperclip, UploadCloud, type LucideIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { UserMenu } from '@/components/UserMenu'
import { ProcessTrail } from '@/components/catalog/ProcessTrail'
import type { BusinessProcess } from '@/types/process'

interface HeaderProps {
  currentProcess: BusinessProcess
  onOpenImport: () => void
  onOpenExport: () => void
  onExportExcel: () => void
  /** Материалы карты: утверждённый PDF, регламент, презентации. */
  onOpenMaterials: () => void
  /** Картинка открытой страницы карты — её рисует холст. */
  onDownloadImage: (format: 'png' | 'svg') => void
  /** Открыта ли правая панель карты: время и стоимость, версии, другие карты. */
  panelOpen: boolean
  onTogglePanel: () => void
}

/**
 * Шапка карты: слева путь от главной до открытой карты, справа действия с ней.
 *
 * «Загрузить» и «Скачать» подписаны словами: среди сотрудников банка есть те,
 * кому одни иконки ни о чём не говорят. Подписанных кнопок здесь только две —
 * правка карты в draw.io стоит у холста, рядом с «Редактировать», — поэтому
 * они помещаются в одну строку с путём. «Сведения» — иконкой с подсказкой.
 */
export const Header: React.FC<HeaderProps> = ({
  currentProcess,
  onOpenImport,
  onOpenExport,
  onExportExcel,
  onOpenMaterials,
  onDownloadImage,
  panelOpen,
  onTogglePanel,
}) => (
  <header className="sticky top-0 z-40 border-b bg-card/85 backdrop-blur supports-[backdrop-filter]:bg-card/70">
    {/* На телефоне путь занимает свою строку, кнопки — вторую: в одном ряду
        с ними от названия карты оставалось три буквы. */}
    <div className="flex w-full flex-wrap items-center gap-2 px-3 py-2 sm:h-14 sm:flex-nowrap sm:gap-3 sm:px-4 sm:py-0">
      <div className="w-full min-w-0 sm:w-auto sm:flex-1">
        <ProcessTrail processId={currentProcess.id} name={currentProcess.name} />
      </div>

      <div className="flex flex-1 shrink-0 items-center gap-1.5 sm:flex-none">
        <Button
          size="sm"
          variant="outline"
          onClick={onOpenImport}
          title="Загрузить карту процесса из файла draw.io"
          className="h-9 gap-1.5 px-3"
        >
          <UploadCloud className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
          Загрузить
        </Button>
        <DownloadMenu onExportExcel={onExportExcel} onDownloadImage={onDownloadImage} onOpenExport={onOpenExport} />
        <Button
          size="sm"
          variant="outline"
          onClick={onOpenMaterials}
          title="Материалы карты: утверждённый PDF, регламент в Excel, презентации"
          className="h-9 gap-1.5 px-3"
        >
          <Paperclip className="h-4 w-4 shrink-0" />
          Материалы
        </Button>
        <Button
          size="sm"
          variant={panelOpen ? 'secondary' : 'outline'}
          onClick={onTogglePanel}
          aria-pressed={panelOpen}
          title={panelOpen ? 'Скрыть панель сведений' : 'Показать панель: время и стоимость, история версий, процессы'}
          aria-label={panelOpen ? 'Скрыть панель сведений' : 'Показать панель сведений'}
          className="h-9 w-9 p-0"
        >
          {panelOpen ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
        </Button>
      </div>

      <span className="h-6 w-px shrink-0 bg-border" />
      <UserMenu />
    </div>
  </header>
)

/**
 * «Скачать» — одна кнопка на все выгрузки: регламент, картинка карты и файлы
 * для других систем. Раньше это были разные иконки в разных местах, и чем
 * «Регламент» отличается от «Экспорта», без подсказки было не понять.
 */
const DownloadMenu: React.FC<{
  onExportExcel: () => void
  onDownloadImage: (format: 'png' | 'svg') => void
  onOpenExport: () => void
}> = ({ onExportExcel, onDownloadImage, onOpenExport }) => {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const close = (e: PointerEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('pointerdown', close)
      document.removeEventListener('keydown', close)
    }
  }, [open])

  const items: { icon: LucideIcon; iconClass: string; label: string; hint: string; onClick: () => void }[] = [
    { icon: FileSpreadsheet, iconClass: 'text-green-600 dark:text-green-400', label: 'Регламент', hint: 'Таблица шагов процесса в Excel', onClick: onExportExcel },
    { icon: ImageIcon, iconClass: 'text-sky-600 dark:text-sky-400', label: 'Картинка карты (PNG)', hint: 'Открытая страница — для регламента и писем', onClick: () => onDownloadImage('png') },
    { icon: ImageIcon, iconClass: 'text-sky-600 dark:text-sky-400', label: 'Картинка карты (SVG)', hint: 'Чёткая при любом увеличении', onClick: () => onDownloadImage('svg') },
    { icon: Download, iconClass: 'text-muted-foreground', label: 'Файл для других систем', hint: 'PIX (.pmm), Processet и другие форматы', onClick: onOpenExport },
  ]

  return (
    <div ref={ref} className="relative">
      <Button
        size="sm"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        title="Скачать: регламент, картинку карты или файл для других систем"
        className="h-9 gap-1.5 bg-gradient-to-r from-emerald-600 to-teal-600 px-3 text-white shadow-sm hover:from-emerald-700 hover:to-teal-700"
      >
        <Download className="h-4 w-4 shrink-0" />
        Скачать
        <ChevronDown className="h-3.5 w-3.5" />
      </Button>
      {open && (
        <div role="menu" className="absolute right-0 top-11 z-50 w-72 rounded-lg border bg-popover p-1 text-popover-foreground shadow-lg">
          {items.map(({ icon: Icon, iconClass, label, hint, onClick }) => (
            <button key={label} type="button" role="menuitem"
              onClick={() => {
                setOpen(false)
                onClick()
              }}
              className="flex w-full items-start gap-2.5 rounded-md px-2.5 py-2 text-left hover:bg-muted">
              <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${iconClass}`} />
              <span>
                <span className="block text-sm font-medium">{label}</span>
                <span className="block text-xs text-muted-foreground">{hint}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
