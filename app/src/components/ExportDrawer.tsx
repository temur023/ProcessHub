import React, { useEffect, useState } from 'react'
import {
  Download,
  Copy,
  Check,
  FileSpreadsheet,
  Database,
  Sparkles,
  Package,
  PenTool,
  ShieldCheck,
  ShieldAlert,
  Loader2,
} from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import type { BusinessProcess } from '@/types/process'
import {
  triggerExportDownload,
  exportFileName,
  fetchExportCheck,
  fetchExportText,
  ExportUnavailableError,
} from '@/lib/api'
import type { ExportCheckReport, PmmLayout } from '@/lib/api'
import { toast } from 'sonner'

/** Выбор вида пакета .pmm: подпись кнопки и что за ней стоит. */
const PMM_LAYOUTS: { value: PmmLayout; label: string; hint: string }[] = [
  {
    value: 'auto',
    label: 'Авто',
    hint: 'Деревом карт, если в файле несколько страниц или больше 1000 фигур; иначе одной картой.',
  },
  {
    value: 'single',
    label: 'Одна карта',
    hint: 'Все страницы на одной карте, как раньше. Карту больше ~1000 фигур PIX может не открыть.',
  },
  {
    value: 'tree',
    label: 'Главная + дочерние',
    hint:
      'Главная карта со свёрнутыми подпроцессами; двойной клик открывает дочернюю карту — '
      + 'страницу draw.io или часть большой карты. Линии между частями идут через события «в часть N» / «из части N».',
  },
]

interface ExportDrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  process: BusinessProcess
}

/**
 * Вердикт проверки выгрузки — теми же правилами, по которым файл читает PIX.
 *
 * Студия отказывается открыть пакет целиком из-за одного дефекта и называет
 * только код ошибки («Notation element not found»), без указания фигуры. Здесь
 * тот же список проверок прогоняется заранее, и, если что-то не так, сотрудник
 * видит конкретную фигуру, не выходя из системы.
 */
const ExportCheckBanner: React.FC<{
  report: ExportCheckReport | null
  loading: boolean
  format: string
}> = ({ report, loading, format }) => {
  if (loading) {
    return (
      <div className="mt-3 flex items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
        Проверяем файлы правилами PIX…
      </div>
    )
  }
  if (!report) {
    return (
      <div className="mt-3 flex items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
        <ShieldAlert className="h-3.5 w-3.5 shrink-0" />
        Проверка выгрузки недоступна: она требует запущенного бэкенда FastAPI.
      </div>
    )
  }

  // Показываем замечания того формата, который сотрудник сейчас смотрит, а при
  // отсутствии такого — все: в PIX уезжает только пакет .pmm.
  //
  // Проверок на формат две, и обе относятся к одному файлу: «pmm» — по
  // формату пакета, «pmm/pix» — по строгому профилю студии. Поэтому
  // сравнение по префиксу, а не на равенство: иначе баннер покажет «замечаний
  // нет», умолчав именно о тех правилах, из-за которых студия и отказывает.
  const scoped = report.formats.filter(
    (f) => f.format === format || f.format.startsWith(`${format}/`)
  )
  const shown = scoped.length ? scoped : report.formats
  const problems = shown.flatMap((f) => f.problems.map((p) => ({ ...p, format: f.format })))
  // Один файл — одно имя в баннере, сколько бы наборов правил его ни проверяло.
  const shownNames = [...new Set(shown.map((f) => f.format.split('/')[0].toUpperCase()))]
  const errors = problems.filter((p) => p.level === 'error')

  if (!problems.length) {
    return (
      <div className="mt-3 flex items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-500/5 px-3 py-2 text-xs text-emerald-700 dark:text-emerald-400">
        <ShieldCheck className="h-3.5 w-3.5 shrink-0" />
        Проверено правилами PIX: {shownNames.join(' и ')} —
        замечаний нет, файл загрузится в Процессную студию.
      </div>
    )
  }

  return (
    <div
      className={`mt-3 max-h-32 overflow-auto rounded-lg border px-3 py-2 text-xs ${
        errors.length
          ? 'border-red-500/40 bg-red-500/5'
          : 'border-amber-500/40 bg-amber-500/5'
      }`}
    >
      <div className={`flex items-center gap-2 font-medium ${errors.length ? 'text-red-600 dark:text-red-400' : 'text-amber-600 dark:text-amber-400'}`}>
        <ShieldAlert className="h-3.5 w-3.5 shrink-0" />
        {errors.length
          ? `PIX не примет файл: ошибок ${errors.length}`
          : `Файл загрузится, но есть замечания: ${problems.length}`}
      </div>
      <ul className="mt-1 space-y-0.5">
        {problems.slice(0, 12).map((p, i) => (
          <li key={`${p.code}-${i}`} className="leading-snug text-muted-foreground">
            <span className="font-mono text-[10px] uppercase">{p.format}</span> · {p.message}
            {p.where && <span className="ml-1 font-mono text-[10px] opacity-70">({p.where})</span>}
          </li>
        ))}
        {problems.length > 12 && (
          <li className="text-muted-foreground/80">…и ещё {problems.length - 12}.</li>
        )}
      </ul>
    </div>
  )
}

export const ExportDrawer: React.FC<ExportDrawerProps> = ({
  open,
  onOpenChange,
  process,
}) => {
  // В PIX карта уезжает только пакетом .pmm: импорт BPMN студия перекладывает
  // по-своему. BPMN для Processet скачивается из раздела Processet.
  const [activeTab, setActiveTab] = useState<'pmm' | 'drawio' | 'logs' | 'pix' | 'excel'>('pmm')
  const [copied, setCopied] = useState(false)
  // Текстовые выгрузки берём с бэкенда: предпросмотр обязан совпадать с тем,
  // что скачается.
  const [texts, setTexts] = useState<Record<string, string | null>>({})
  // Проверка выгрузки правилами PIX: сотрудник должен видеть вердикт ДО того,
  // как понесёт файл в студию, — она отвергает пакет целиком и без адреса.
  // «Идёт проверка» выводим из того, чей ответ уже получен, а не отдельным
  // флагом: синхронный setState в эффекте гоняет лишние перерисовки.
  const [check, setCheck] = useState<ExportCheckReport | null>(null)
  const [checkedId, setCheckedId] = useState('')
  // Большую карту PIX одной не открывает — её пакет собирается деревом карт.
  const [layout, setLayout] = useState<PmmLayout>('auto')
  const checkKey = `${process.id}:${layout}`
  const checking = checkedId !== checkKey

  useEffect(() => {
    if (!open) return
    let cancelled = false
    fetchExportCheck(process.id, layout).then((report) => {
      if (cancelled) return
      setCheck(report)
      setCheckedId(`${process.id}:${layout}`)
    })
    return () => {
      cancelled = true
    }
  }, [open, process.id, layout])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    void Promise.all(
      (['event-log', 'regulation', 'pix-json'] as const).map((type) =>
        fetchExportText(process.id, type).then((text) => [type, text] as const),
      ),
    ).then((pairs) => {
      if (!cancelled) setTexts(Object.fromEntries(pairs))
    })
    return () => {
      cancelled = true
    }
  }, [open, process.id])

  const UNAVAILABLE = 'Файл собирается на бэкенде. Запустите backend/start.sh и откройте выгрузку снова.'
  const textOf = (type: string) =>
    type in texts ? (texts[type] ?? UNAVAILABLE) : 'Собираем файл на бэкенде…'
  const logsCsv = textOf('event-log')
  const pixJson = textOf('pix-json')
  const excelCsv = textOf('regulation')
  const pmmManifest = [
    exportFileName(process, 'pmm'),
    '├── main.xml',
    '├── pm/configuration.xml',
    ...(layout === 'single'
      ? [`└── pm/maps/{code}.xml`]
      : [
          `├── pm/maps/{code}.xml            главная карта: свёрнутые подпроцессы`,
          `└── pm/maps/{страница, часть}.xml  дочерние карты (parentProcessId)`,
        ]),
    '',
    'Нативный пакет PIX Процессной студии: Types + каталог нотаций + карта BPMN.',
    'Дорожки — horizontalRoad, шаги вложены с относительными координатами,',
    'переходы — connector type=step на уровне Map. Скачивается ZIP .pmm.',
    '',
    ...(layout === 'single'
      ? []
      : [
          'Карту больше 500 фигур пакет режет поперёк хода процесса там, где разрез',
          'пересекает меньше всего линий; каждая часть — со всеми дорожками.',
          '',
        ]),
    'Время шага рисуется мелким таймером у его нижней грани — как на карте draw.io,',
    'и то же время стоит в свойствах шага.',
  ].join('\n')

  const getCurrentContent = () => {
    switch (activeTab) {
      case 'pmm':
        return { content: pmmManifest, exportType: 'pmm' as const }
      case 'drawio':
        return { content: '', exportType: 'drawio' as const }
      case 'logs':
        return { content: logsCsv, exportType: 'event-log' as const }
      case 'pix':
        return { content: pixJson, exportType: 'pix-json' as const }
      case 'excel':
        return { content: excelCsv, exportType: 'regulation' as const }
    }
  }

  const handleCopy = () => {
    if (activeTab === 'pmm' || activeTab === 'drawio') return
    const { content } = getCurrentContent()
    navigator.clipboard.writeText(content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleDownload = async () => {
    const { exportType } = getCurrentContent()
    const filename = exportFileName(process, exportType)
    try {
      // Имя в уведомлении — то, под которым файл сохранён, а не угаданное
      // здесь: реестр назывался «…_PIX_Schema.json», скачивался «…_PIX_Registry.json».
      const saved = await triggerExportDownload(process, exportType, exportType === 'pmm' ? layout : undefined)
      toast.success(`Файл «${saved}» выгружен`)
    } catch (err) {
      // Раньше отказ экспорта .pmm терялся в console.warn, и по кнопке просто
      // ничего не происходило.
      const message =
        err instanceof ExportUnavailableError
          ? err.message
          : `Не удалось выгрузить «${filename}»: ${err instanceof Error ? err.message : String(err)}`
      toast.error(message, { duration: 8000 })
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[92dvh] max-w-4xl flex-col overflow-hidden p-4 sm:max-w-4xl sm:p-6">
        <DialogHeader>
          <div className="flex items-start gap-2.5 pr-6">
            <div className="shrink-0 rounded-lg bg-emerald-100 p-2 text-emerald-600 dark:bg-emerald-950/60">
              <Download className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <DialogTitle className="text-base font-bold sm:text-xl">Центр экспорта данных процесса</DialogTitle>
              <DialogDescription className="mt-0.5 text-xs text-muted-foreground">
                Карта процесса для PIX Процессной студии — нативный пакет .pmm. Также Processet и Excel.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as any)} className="flex-1 flex flex-col overflow-hidden">
          <TabsList className="grid h-auto w-full grid-cols-2 gap-1 p-1 sm:grid-cols-5">
            <TabsTrigger value="pmm" className="text-[11px]">
              <Package className="w-3.5 h-3.5 mr-1 text-sky-500" />
              PMM (PIX)
            </TabsTrigger>
            <TabsTrigger value="drawio" className="text-[11px]">
              <PenTool className="w-3.5 h-3.5 mr-1 text-orange-500" />
              draw.io
            </TabsTrigger>
            <TabsTrigger value="logs" className="text-[11px]">
              <Sparkles className="w-3.5 h-3.5 mr-1 text-purple-500" />
              Event Logs
            </TabsTrigger>
            <TabsTrigger value="pix" className="text-[11px]">
              <Database className="w-3.5 h-3.5 mr-1 text-emerald-500" />
              PIX Schema
            </TabsTrigger>
            <TabsTrigger value="excel" className="text-[11px]">
              <FileSpreadsheet className="w-3.5 h-3.5 mr-1 text-green-500" />
              Excel
            </TabsTrigger>
          </TabsList>

          <TabsContent value="pmm" className="flex-1 flex flex-col overflow-hidden mt-3 space-y-2">
            <div className="flex flex-col items-start justify-between gap-2 rounded-lg bg-muted/60 p-2.5 text-xs sm:flex-row sm:items-center">
              <span>
                Нативный пакет <strong>PIX Процессной студии</strong> (.pmm = ZIP из main.xml, configuration.xml и карты BPMN).
                Загружается в студию через «Импорт» и приходит с теми же линиями и временем шагов, что на карте.
              </span>
              <Badge className="bg-sky-600 text-white text-[10px] shrink-0">PMM ZIP</Badge>
            </div>
            <div className="flex flex-col gap-1.5 rounded-lg border p-2.5 text-xs">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="mr-1 text-muted-foreground">Вид пакета:</span>
                {PMM_LAYOUTS.map((option) => (
                  <Button
                    key={option.value}
                    type="button"
                    size="sm"
                    variant={layout === option.value ? 'default' : 'outline'}
                    className="h-7 px-2.5 text-[11px]"
                    aria-pressed={layout === option.value}
                    onClick={() => setLayout(option.value)}
                  >
                    {option.label}
                  </Button>
                ))}
              </div>
              <p className="leading-snug text-muted-foreground">
                {PMM_LAYOUTS.find((option) => option.value === layout)?.hint}
              </p>
            </div>
            <div className="max-h-[360px] flex-1 overflow-auto rounded-lg border bg-slate-50 p-3 font-mono text-[11px] text-slate-800 dark:bg-slate-950 dark:text-slate-200">
              <pre>{pmmManifest}</pre>
            </div>
          </TabsContent>

          {/* draw.io: карта для правки в draw.io и обратного импорта */}
          <TabsContent value="drawio" className="flex-1 flex flex-col overflow-hidden mt-3 space-y-2">
            <div className="flex flex-col items-start justify-between gap-2 rounded-lg bg-muted/60 p-2.5 text-xs sm:flex-row sm:items-center">
              <span>
                Карта в формате <strong>draw.io</strong> — так, как её нарисовали: все страницы, пулы и дорожки,
                шаги со своими стилями, линии с изломами и подписями, время шага — значками часов.
              </span>
              <Badge className="shrink-0 bg-orange-500 text-[10px] text-white">.drawio</Badge>
            </div>
            <div className="space-y-1.5 rounded-lg border p-3 text-xs leading-relaxed text-muted-foreground">
              <p>Файл открывается в draw.io (desktop или diagrams.net) и импортируется обратно той же картой.</p>
              <p>
                Роль, система, категория, код шага, потенциал RPA, цена запуска робота, описание и ставки
                дорожек едут свойствами фигур («Изменить данные» в draw.io) — там, где их не видно из рисунка.
              </p>
            </div>
          </TabsContent>

          {/* Tab 2: Event Logs */}
          <TabsContent value="logs" className="flex-1 flex flex-col overflow-hidden mt-3 space-y-2">
            <div className="flex flex-col items-start justify-between gap-2 rounded-lg bg-muted/60 p-2.5 text-xs sm:flex-row sm:items-center">
              <span>
                Журнал событий (Case ID, Activity, Timestamps, Resource, Cost) для Process Mining анализа в <strong>Processet</strong>.
              </span>
              <Badge className="shrink-0 bg-purple-600 text-[10px] text-white">CSV Log</Badge>
            </div>
            <div className="max-h-[360px] flex-1 overflow-auto rounded-lg border bg-slate-50 p-3 font-mono text-[11px] text-slate-800 dark:bg-slate-950 dark:text-slate-200">
              <pre>{logsCsv}</pre>
            </div>
          </TabsContent>

          {/* Tab 3: PIX JSON */}
          <TabsContent value="pix" className="flex-1 flex flex-col overflow-hidden mt-3 space-y-2">
            <div className="flex flex-col items-start justify-between gap-2 rounded-lg bg-muted/60 p-2.5 text-xs sm:flex-row sm:items-center">
              <span>
                Конфигурация процессов и реестров для прямого создания в <strong>PIX BPM & RPA</strong>.
              </span>
              <Badge className="shrink-0 bg-emerald-600 text-[10px] text-white">PIX JSON</Badge>
            </div>
            <div className="max-h-[360px] flex-1 overflow-auto rounded-lg border bg-slate-50 p-3 font-mono text-[11px] text-slate-800 dark:bg-slate-950 dark:text-slate-200">
              <pre>{pixJson}</pre>
            </div>
          </TabsContent>

          {/* Tab 4: Excel Regulation */}
          <TabsContent value="excel" className="flex-1 flex flex-col overflow-hidden mt-3 space-y-2">
            <div className="flex flex-col items-start justify-between gap-2 rounded-lg bg-muted/60 p-2.5 text-xs sm:flex-row sm:items-center">
              <span>
                Табличный регламент процесса с ролями, ИТ-системами, SLA и потенциалом роботизации для Excel.
              </span>
              <Badge className="shrink-0 bg-green-600 text-[10px] text-white">Excel CSV (BOM)</Badge>
            </div>
            <div className="max-h-[360px] flex-1 overflow-auto rounded-lg border bg-slate-50 p-3 font-mono text-[11px] text-slate-800 dark:bg-slate-950 dark:text-slate-200">
              <pre>{excelCsv}</pre>
            </div>
          </TabsContent>
        </Tabs>

        {/* Проверка выгрузки правилами PIX */}
        {activeTab !== 'drawio' && <ExportCheckBanner report={check} loading={checking} format={activeTab} />}

        {/* Footer Actions */}
        <div className="flex flex-col gap-2.5 border-t pt-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0 truncate text-xs text-muted-foreground">
            Файл: <strong className="text-foreground">{exportFileName(process, getCurrentContent().exportType)}</strong>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {activeTab !== 'pmm' && activeTab !== 'drawio' && (
            <Button variant="outline" size="sm" onClick={handleCopy} className="flex-1 gap-1.5 text-xs sm:flex-none">
              {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
              <span>{copied ? 'Скопировано!' : 'Копировать код'}</span>
            </Button>
            )}
            <Button
              size="sm"
              onClick={handleDownload}
              className="flex-1 gap-1.5 bg-emerald-600 text-xs text-white hover:bg-emerald-700 sm:flex-none"
            >
              <Download className="h-3.5 w-3.5" />
              <span>Скачать файл</span>
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
