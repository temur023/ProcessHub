import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router'
import type { BusinessProcess, ProcessNode, ProcessVersion } from '@/types/process'
import { Header } from '@/components/Header'
import { ProcessVisualizer } from '@/components/ProcessVisualizer'
import { ProcessImportModal } from '@/components/ProcessImportModal'
import { ImportReport } from '@/components/ImportReport'
import type { CheckStatus } from '@/components/ImportReport'
import type { CanvasFocus, ProcessVisualizerHandle } from '@/components/ProcessVisualizer'
import { NodeDetailDrawer } from '@/components/NodeDetailDrawer'
import { ExportDrawer } from '@/components/ExportDrawer'
import { MapSidePanel } from '@/components/MapSidePanel'
import { ProcessHistoryDialog } from '@/components/ProcessHistoryDialog'
import { DrawioEditor } from '@/components/DrawioEditor'
import { MapMaterialsDialog } from '@/components/MapMaterialsDialog'
import { replacePage, setLaneRate } from '@/lib/map-edit'

import {
  saveProcessToBackend,
  recalculateOnBackend,
  restoreProcessVersion,
  saveDrawioToBackend,
  triggerExportDownload,
  ProcessConflictError,
  fetchProcessVersion,
} from '@/lib/api'
import { handOverProcess } from '@/lib/process-handoff'
import { toast } from 'sonner'
import { isTaskNode } from '@/types/process'
import { isOverviewPage, plural, stepCount } from '@/lib/presentation'
import { VersionPreviewBar } from '@/components/VersionPreviewBar'

/** Сколько правок помнит отмена (Ctrl+Z). */
const HISTORY_LIMIT = 50
/** Пауза перед сохранением правки холста: серия правок уходит одним запросом. */
const SAVE_DELAY = 500
type ToastPosition = NonNullable<Parameters<typeof toast.success>[1]>['position']
/** Уведомления встроенного редактора — внизу: вверху справа они закрывали его «Сохранить и выйти». */
const EDITOR_TOASTS: ToastPosition = 'bottom-center'
/**
 * Правая панель «Сведения» открыта при каждом открытии карты: время,
 * стоимость и паспорт процесса сотрудник видит сразу, а не ищет кнопку.
 * Закрыть её можно, но следующая карта снова откроется с ней. На узком
 * экране панель легла бы поверх холста — там она скрыта.
 */
function initialPanelOpen(): boolean {
  return window.matchMedia('(min-width: 1024px)').matches
}

/**
 * Редактор карты. Карту передаёт экран `/process/:processId` (ProcessPage):
 * загруженную с сервера — уже проверенной, шаблон — ещё не проверенным.
 */
export default function Home({ initialProcess, isTemplate }: { initialProcess: BusinessProcess; isTemplate: boolean }) {
  const navigate = useNavigate()
  const [currentProcess, setCurrentProcess] = useState<BusinessProcess>(initialProcess)
  const [isImportOpen, setIsImportOpen] = useState(false)
  const [isExportOpen, setIsExportOpen] = useState(false)
  const [selectedNode, setSelectedNode] = useState<ProcessNode | null>(null)
  /** Замечание, на фигуры которого сейчас наведена карта. */
  const [canvasFocus, setCanvasFocus] = useState<CanvasFocus | null>(() => {
    // Карта открыта из «Поиска по картам»: сразу на найденном шаге.
    const node = new URLSearchParams(window.location.search).get('node')
    return node ? { nodeIds: [node], issue: { level: 'info', message: 'Шаг найден поиском по картам' }, nonce: 1 } : null
  })
  const [activeIssueKey, setActiveIssueKey] = useState<string>('')
  /** Открытая страница файла draw.io; пустая строка — основная карта. */
  const [openedPageId, setOpenedPageId] = useState<string>(() => {
    const page = new URLSearchParams(window.location.search).get('page') ?? ''
    const known = [...(initialProcess.subProcesses ?? []), ...(initialProcess.variants ?? [])].some((p) => p.id === page)
    return known ? page : ''
  })
  /** Какая карта и с каким итогом прошла проверку бэкенда. */
  const [check, setCheck] = useState<{ id: string; status: CheckStatus }>(() => ({
    id: currentProcess.id,
    status: isTemplate ? 'checking' : 'checked',
  }))
  const checkStatus: CheckStatus = check.id === currentProcess.id ? check.status : 'checking'
  /** Карты до последних правок (отмена) и отменённые правки (возврат). */
  const [past, setPast] = useState<BusinessProcess[]>([])
  const [future, setFuture] = useState<BusinessProcess[]>([])
  const saveTimer = useRef<number | undefined>(undefined)
  /** Номер последней правки: ответ сервера на устаревшую правку не применяется. */
  const editVersion = useRef(0)
  /** Правка, ещё не ушедшая на сервер, — её отправляют и при уходе с карты. */
  const pendingSave = useRef<BusinessProcess | null>(null)
  /** Карта до сеанса в draw.io уже лежит в отмене: сеанс отменяется одним Ctrl+Z. */
  const drawioUndoSet = useRef(false)
  /** Редактор открыт: ответ сервера после ухода с карты ничего не трогает — ни экран, ни адрес. */
  const alive = useRef(true)
  /**
   * Последняя ревизия карты, известная вкладке. С ней уходит каждое
   * сохранение — а не ревизия из снимка отмены: тот старше, и Ctrl+Z после
   * автосохранения выглядел бы для сервера правкой по чужой копии.
   */
  const known = useRef<{ id: string; revision: number | null }>({
    id: initialProcess.id,
    revision: initialProcess.revision ?? null,
  })
  /**
   * Сохранения вкладки идут по одному: следующее уходит с ревизией, которую
   * вернуло предыдущее. Два одновременных запроса одной вкладки сервер
   * принял бы за чужую правку.
   */
  const saveQueue = useRef<Promise<unknown>>(Promise.resolve())
  /** Сервер отказал по ревизии: об этом уже сказано, «бэкенд недоступен» — неправда. */
  const conflicted = useRef(false)
  const [isHistoryOpen, setIsHistoryOpen] = useState(false)
  const [isDrawioOpen, setIsDrawioOpen] = useState(false)
  const [isMaterialsOpen, setIsMaterialsOpen] = useState(false)
  const [panelOpen, setPanelOpen] = useState(initialPanelOpen)
  /** Холст карты: «Скачать» в шапке просит его сохранить картинку. */
  const canvasRef = useRef<ProcessVisualizerHandle>(null)

  const remember = (p: BusinessProcess) => {
    if (p.revision == null) return
    const k = known.current
    known.current = { id: p.id, revision: k.id === p.id ? Math.max(k.revision ?? 0, p.revision) : p.revision }
  }

  // Карта пришла с сервера (сохранение, пересчёт, восстановление) — её ревизия
  // теперь последняя известная.
  useEffect(() => remember(currentProcess), [currentProcess])

  /** Запрос, меняющий карту на сервере, — в очередь вкладки. */
  const enqueue = <T,>(task: () => Promise<T>): Promise<T> => {
    const run = saveQueue.current.then(task)
    saveQueue.current = run.catch(() => null)
    return run
  }

  /** Сохранение через очередь вкладки и с последней известной ревизией. */
  const persist = (process: BusinessProcess): Promise<BusinessProcess | null> =>
    enqueue(async () => {
      const k = known.current
      try {
        const saved = await saveProcessToBackend({
          ...process,
          revision: k.id === process.id ? k.revision : process.revision,
        })
        if (saved) remember(saved)
        return saved
      } catch (err) {
        if (!(err instanceof ProcessConflictError)) throw err
        announceConflict(err)
        return null
      }
    })

  /** Сервер отказал по ревизии: правки этой вкладки дальше не сохраняются. */
  const announceConflict = (err: ProcessConflictError, position?: ToastPosition) => {
    conflicted.current = true
    toast.error('Карту уже изменили в другой вкладке или другой сотрудник', {
      id: 'process-conflict',
      position,
      description: err.message,
      duration: Infinity,
      action: { label: 'Обновить', onClick: () => window.location.reload() },
    })
  }

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      // Ушли на другую карту раньше, чем истекла пауза, — правку не теряем.
      window.clearTimeout(saveTimer.current)
      if (pendingSave.current) void persist(pendingSave.current)
    }
    // `persist` держит всё нужное в ref'ах: годится и функция первого рендера.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Страница, которую сейчас показывает холст. Выгрузка и регламент работают
  // с процессом целиком: они описывают его, а не отдельный лист схемы.
  const pages = [...(currentProcess.subProcesses ?? []), ...(currentProcess.variants ?? [])]
  const openedProcess = (openedPageId && pages.find((p) => p.id === openedPageId)) || currentProcess
  /** Версия из истории, открытая на холсте только для чтения. */
  const [preview, setPreview] = useState<{ meta: ProcessVersion; process: BusinessProcess } | null>(null)
  /** Что показывает холст: просматриваемая версия или текущая карта. */
  const shown = preview?.process ?? currentProcess
  const shownPages = [...(shown.subProcesses ?? []), ...(shown.variants ?? [])]
  const shownOpened = preview
    ? (openedPageId && shownPages.find((p) => p.id === openedPageId)) || shown
    : openedProcess

  useEffect(() => {
    // Карта с сервера уже проверена; пересохранять её при открытии незачем.
    if (!isTemplate) return
    // Шаблон собран вручную: проверку он проходит при сохранении на бэкенде —
    // там же, где её проходит любая импортированная карта.
    const template = currentProcess
    void persist(template).then((saved) => {
      setCheck({ id: template.id, status: saved ? 'checked' : 'unavailable' })
      if (saved) setCurrentProcess((prev) => (prev.id === saved.id ? saved : prev))
    })
    // Subsequent edits save explicitly from handlers below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** Уведомление о загруженной карте — по её проверенным замечаниям. */
  const announce = (process: BusinessProcess) => {
    const issues = process.validation ?? []
    const errors = issues.filter((i) => i.level === 'error').length
    const warnings = issues.filter((i) => i.level === 'warning').length
    // Шаги — операции регламента, а не все узлы карты: «регламент на 164
    // шагов» у карты «Akkreditiv ochish» считал шлюзы, события и цилиндры.
    const summary = `Создан регламент на ${plural(stepCount(process), 'шаг', 'шага', 'шагов')} и реестр PIX.`
    // Замечания к карте показываем сразу: молча импортировать неполную схему
    // нельзя — ошибка дойдёт до регламента и до выгрузки в PIX.
    if (errors || warnings) {
      toast.warning(`Процесс «${process.name}» загружен с замечаниями`, {
        description:
          `${summary} Ошибок: ${errors}, предупреждений: ${warnings}. ` +
          'Подробности — в панели «Проверка импорта» над картой.',
        duration: 8000,
      })
    } else {
      toast.success(`Бизнес-процесс «${process.name}» успешно загружен!`, {
        description: summary,
      })
    }
  }

  const handleProcessLoaded = (newProcess: BusinessProcess, { checked }: { checked: boolean }) => {
    // Файл мог лечь в эту же карту новой версией: её ревизия — уже последняя,
    // иначе пересохранение ниже ушло бы со старой и получило отказ.
    remember(newProcess)
    setCurrentProcess(newProcess)
    // Отмена не должна возвращать прежнюю карту вместо новой.
    setPast([])
    setFuture([])
    setSelectedNode(null)
    // Страницы прошлой карты на новой ничего не значат.
    setOpenedPageId('')
    // Подсветка привязана к фигурам прошлой карты: на новой её id ничего не значат.
    setCanvasFocus(null)
    setActiveIssueKey('')
    // Сохранённая карта переезжает на свой адрес — иначе в адресной строке
    // осталась бы прежняя карта, и обновление страницы вернуло бы её.
    if (checked) {
      setCheck({ id: newProcess.id, status: 'checked' })
      void persist(newProcess).then((saved) => {
        if (saved) navigate(handOverProcess(saved))
      })
      announce(newProcess)
      return
    }
    // Шаблон проверку ещё не проходил: говорить о его замечаниях можно только
    // после ответа бэкенда, иначе пустой список выдаёт себя за «замечаний нет».
    setCheck({ id: newProcess.id, status: 'checking' })
    void persist(newProcess).then((saved) => {
      setCheck({ id: newProcess.id, status: saved ? 'checked' : 'unavailable' })
      if (!saved) {
        toast.warning(`Процесс «${newProcess.name}» открыт без проверки`, {
          description: 'Проверку карты и регламент выполняет бэкенд, а он недоступен.',
          duration: 8000,
        })
        return
      }
      setCurrentProcess((prev) => (prev.id === saved.id ? saved : prev))
      announce(saved)
      navigate(handOverProcess(saved))
    })
  }

  /**
   * Правка с холста уходит на сервер не сразу: фигуру двигают сериями, и
   * каждая отдельная правка — лишний запрос с картой целиком. С сервера
   * возвращается та же карта с пересчитанной себестоимостью и замечаниями.
   */
  const scheduleSave = (process: BusinessProcess) => {
    const version = ++editVersion.current
    window.clearTimeout(saveTimer.current)
    pendingSave.current = process
    setCheck({ id: process.id, status: 'checking' })
    saveTimer.current = window.setTimeout(() => {
      pendingSave.current = null
      void persist(process).then((saved) => {
        // За это время пришла новая правка — сохранит и проверит её она.
        if (!alive.current || version !== editVersion.current) return
        // Отказ по ревизии — не «бэкенд недоступен»: о нём уже сказано отдельно,
        // а замечания на экране — те, что сервер дал последней сохранённой карте.
        if (!saved && conflicted.current) {
          setCheck({ id: process.id, status: 'checked' })
          return
        }
        setCheck({ id: saved?.id ?? process.id, status: saved ? 'checked' : 'unavailable' })
        if (!saved) {
          toast.error('Правка карты не сохранилась', {
            description: 'Бэкенд недоступен: правка видна только здесь, до обновления страницы.',
            duration: 8000,
          })
          return
        }
        setCurrentProcess(saved)
        // Адрес — сохранённой карты: шаблон открыт по `/process/sample`, и
        // обновление страницы вернуло бы чистый шаблон вместо правленого, а с
        // новой фигурой бэкенд сохраняет шаблон ещё и под новым id. Меняем
        // адрес мимо роутера: через него карта открылась бы заново, и история
        // правок пропала бы.
        const path = `/process/${saved.id}`
        if (window.location.pathname !== path) window.history.replaceState(window.history.state, '', path)
      })
    }, SAVE_DELAY)
  }

  /** Правка страницы, открытой на холсте: в историю, на экран и на сервер. */
  const commitPage = (page: BusinessProcess) => {
    const next = replacePage(currentProcess, openedPageId, page)
    setPast((list) => [...list.slice(-(HISTORY_LIMIT - 1)), currentProcess])
    setFuture([])
    setCurrentProcess(next)
    // Удалённую фигуру карточка показывать не должна.
    if (selectedNode && !page.nodes.some((n) => n.id === selectedNode.id) && openedProcess.nodes.some((n) => n.id === selectedNode.id)) {
      setSelectedNode(null)
    }
    scheduleSave(next)
  }

  /** Открыть версию из истории на холсте — посмотреть, прежде чем возвращать. */
  const openPreview = async (meta: ProcessVersion) => {
    try {
      const process = await fetchProcessVersion(currentProcess.id, meta.version)
      setIsHistoryOpen(false)
      setSelectedNode(null)
      setCanvasFocus(null)
      setPreview({ meta, process })
    } catch (err) {
      toast.error(`Версия ${meta.version} не открылась`, { description: err instanceof Error ? err.message : String(err) })
    }
  }

  /** Правка паспорта карты: в историю отмены, на экран и на сервер — как правка холста. */
  const changePassport = (patch: Partial<BusinessProcess['passport']>) => {
    const next = { ...currentProcess, passport: { ...currentProcess.passport, ...patch } }
    setPast((list) => [...list.slice(-(HISTORY_LIMIT - 1)), currentProcess])
    setFuture([])
    setCurrentProcess(next)
    scheduleSave(next)
  }

  const undo = () => {
    const previous = past[past.length - 1]
    if (!previous) return
    setPast(past.slice(0, -1))
    setFuture([currentProcess, ...future])
    setCurrentProcess(previous)
    scheduleSave(previous)
  }

  const redo = () => {
    const next = future[0]
    if (!next) return
    setFuture(future.slice(1))
    setPast([...past, currentProcess])
    setCurrentProcess(next)
    scheduleSave(next)
  }

  /**
   * Встроенный draw.io открывает карту с сервера — поэтому правка холста, ещё
   * ждущая паузы, уходит на сервер до открытия, иначе её бы в редакторе не было.
   */
  const openDrawio = async () => {
    window.clearTimeout(saveTimer.current)
    const pending = pendingSave.current
    pendingSave.current = null
    if (pending) await persist(pending)
    else await saveQueue.current
    drawioUndoSet.current = false
    setIsDrawioOpen(true)
  }

  /**
   * Рисунок из встроенного draw.io — в эту же карту. Редактор сохраняет сам
   * через пару секунд после правки (``explicit`` — нажали «Сохранить»), сервер
   * склеивает такие сохранения в одну версию. В отмену уходит карта до начала
   * сеанса: Ctrl+Z на холсте откатывает весь сеанс, а не последние две секунды.
   */
  const saveFromDrawio = async (xml: string, explicit: boolean): Promise<boolean> => {
    const before = currentProcess
    try {
      const saved = await enqueue(async () => {
        const k = known.current
        const result = await saveDrawioToBackend(before.id, xml, k.id === before.id ? k.revision : before.revision)
        remember(result)
        return result
      })
      editVersion.current++
      if (!drawioUndoSet.current) {
        drawioUndoSet.current = true
        setPast((list) => [...list.slice(-(HISTORY_LIMIT - 1)), before])
        setFuture([])
      }
      setCurrentProcess(saved)
      setSelectedNode(null)
      setCanvasFocus(null)
      const savedPages = [...(saved.subProcesses ?? []), ...(saved.variants ?? [])]
      if (openedPageId && !savedPages.some((p) => p.id === openedPageId)) setOpenedPageId('')
      setCheck({ id: saved.id, status: 'checked' })
      // Автосохранение видно в строке состояния самого draw.io — уведомлять о
      // каждом раз в пару секунд незачем.
      if (explicit) {
        toast.success('Карта сохранена из draw.io', { description: 'Правка — в истории версий карты.', position: EDITOR_TOASTS })
      }
      return true
    } catch (err) {
      if (err instanceof ProcessConflictError) announceConflict(err, EDITOR_TOASTS)
      else {
        toast.error('Правка из draw.io не сохранилась', {
          description: err instanceof Error ? err.message : String(err),
          position: EDITOR_TOASTS,
        })
      }
      return false
    }
  }

  /**
   * Возврат к версии из истории. Сервер сохраняет её новой версией, а
   * прежняя карта уходит в отмену: Ctrl+Z вернёт её, как любую правку.
   */
  const restoreVersion = async (version: number) => {
    // Отложенная правка холста не должна лечь поверх восстановленной карты.
    window.clearTimeout(saveTimer.current)
    pendingSave.current = null
    editVersion.current++
    const before = currentProcess
    try {
      const restored = await enqueue(async () => {
        const result = await restoreProcessVersion(before.id, version)
        remember(result)
        return result
      })
      setPast((list) => [...list.slice(-(HISTORY_LIMIT - 1)), before])
      setFuture([])
      setCurrentProcess(restored)
      setSelectedNode(null)
      setCanvasFocus(null)
      const restoredPages = [...(restored.subProcesses ?? []), ...(restored.variants ?? [])]
      if (openedPageId && !restoredPages.some((p) => p.id === openedPageId)) setOpenedPageId('')
      setCheck({ id: restored.id, status: 'checked' })
      setIsHistoryOpen(false)
      toast.success(`Карта возвращена к версии ${version}`, {
        description: 'Сохранено новой версией. Ctrl+Z на холсте вернёт карту, какой она была.',
      })
    } catch (err) {
      toast.error('Версия не восстановлена', { description: err instanceof Error ? err.message : String(err) })
    }
  }

  /**
   * Правка шага: сохраняем на бэкенде и берём оттуда же пересчитанную карту.
   *
   * Метрики conformance и замечания к карте считает бэкенд — здесь их
   * повторяли своей, более грубой реализацией: местная проверка знала только
   * «нет старта» и «шаг без переходов», а серверная — ещё три десятка правил.
   * Из-за этого стоило поправить один шаг, как список замечаний схлопывался
   * до двух строк, и найденное при импорте пропадало с экрана.
   */
  const handleSaveNode = (updatedNode: ProcessNode) => {
    // Шаг мог быть открыт на странице-подпроцессе или на странице-варианте:
    // правим ту карту, где он лежит, иначе правка молча не применилась бы ни к чему.
    const onSubPage = openedPageId && openedProcess.id === openedPageId
    const sourceNodes = onSubPage ? openedProcess.nodes : currentProcess.nodes
    const updatedNodes = sourceNodes.map((n) => (n.id === updatedNode.id ? updatedNode : n))
    const totalSlaMin = updatedNodes
      .filter((n) => isTaskNode(n.type))
      .reduce((acc, n) => acc + (n.slaMinutes || 0), 0)
    const newTargetHours = Math.max(1, Math.round((totalSlaMin / 60) * 10) / 10)
    const withPage = (list?: BusinessProcess[]) =>
      list?.map((page) =>
        page.id === openedPageId
          ? {
              ...page,
              nodes: updatedNodes,
              passport: { ...page.passport, targetSlaHours: newTargetHours },
            }
          : page,
      )
    const updated: BusinessProcess = onSubPage
      ? {
          ...currentProcess,
          subProcesses: withPage(currentProcess.subProcesses),
          variants: withPage(currentProcess.variants),
        }
      : {
          ...currentProcess,
          nodes: updatedNodes,
          passport: {
            ...currentProcess.passport,
            targetSlaHours: newTargetHours,
            updatedDate: new Date().toISOString().split('T')[0],
          },
        }
    setPast((list) => [...list.slice(-(HISTORY_LIMIT - 1)), currentProcess])
    setFuture([])
    // Отложенное сохранение правки холста не должно перезаписать эту.
    window.clearTimeout(saveTimer.current)
    pendingSave.current = null
    editVersion.current++
    setCurrentProcess(updated)
    setSelectedNode(null)
    toast.success(
      isTaskNode(updatedNode.type)
        ? `Шаг «${updatedNode.name}» обновлён`
        : `Фигура «${updatedNode.name}» обновлена`,
    )
    setCheck({ id: updated.id, status: 'checking' })
    void persist(updated)
      // Пересчёт тоже сохраняет карту на сервере (новая ревизия) — и тоже в
      // очереди, с ревизией, запомненной до следующего сохранения.
      .then((saved) => (saved ? enqueue(async () => {
        const fresh = await recalculateOnBackend(updated.id)
        if (fresh) remember(fresh)
        return fresh
      }) : null))
      .then((fresh) => {
        setCheck({ id: updated.id, status: fresh ? 'checked' : 'unavailable' })
        if (fresh) setCurrentProcess((prev) => (prev.id === fresh.id ? fresh : prev))
      })
  }

  /** Открыть страницу файла: вкладкой над картой или ссылкой шага-подпроцесса. */
  const openPage = (pageId: string) => {
    setOpenedPageId(pageId)
    setSelectedNode(null)
    setCanvasFocus(null)
  }

  const handleExportExcel = () => {
    void triggerExportDownload(currentProcess, 'regulation')
      .then(() => toast.success('Таблица регламента скачана в формате Excel/CSV'))
      .catch((err) => {
        toast.error('Регламент недоступен', {
          description: err instanceof Error ? err.message : String(err),
          duration: 8000,
        })
      })
  }

  return (
    <div className="h-screen overflow-hidden bg-background text-foreground flex flex-col">
      <Header
        currentProcess={currentProcess}
        onOpenImport={() => setIsImportOpen(true)}
        onOpenExport={() => setIsExportOpen(true)}
        onExportExcel={handleExportExcel}
        onOpenMaterials={() => setIsMaterialsOpen(true)}
        onDownloadImage={(format) => canvasRef.current?.downloadImage(format)}
        panelOpen={panelOpen}
        onTogglePanel={() => setPanelOpen(!panelOpen)}
      />

      {/* Карта занимает всю доступную высоту и ширину окна и прокручивается
          внутри себя: с ограничением ширины на широком экране по бокам
          оставались пустые поля, а карта — мельче. */}
      <main className="flex min-h-0 w-full flex-1 flex-col overflow-hidden px-3 py-3 sm:px-4 sm:py-4">
        <div className="relative flex min-h-0 flex-1 gap-3">
          <div className="flex min-w-0 flex-1 flex-col">
            {/* Страницы файла draw.io — как вкладки в самом редакторе.
                Многостраничная карта раньше показывалась одной сводной
                страницей, а её части — покупка, страхование, ремонт — лежали
                в модели и на холст не выводились. */}
            {preview && (
              <VersionPreviewBar
                meta={preview.meta}
                version={preview.process}
                current={currentProcess}
                onRestore={() => void restoreVersion(preview.meta.version).then(() => setPreview(null))}
                onClose={() => setPreview(null)}
              />
            )}
            {shownPages.length > 0 && (
              <div className="mb-2 flex flex-wrap items-center gap-1.5">
                <span className="mr-1 text-[11px] text-muted-foreground">Страницы:</span>
                {[
                  // Обзорная первая страница — «Общая карта»: на ней видна вся
                  // карта, а части раскрываются на своих страницах. Если первая —
                  // обычная часть, вкладка носит её собственное имя.
                  isOverviewPage(shown)
                    ? { id: '', name: 'Общая карта', title: shown.mainPageName || shown.name, variant: false }
                    : { id: '', name: shown.mainPageName || shown.name, variant: false },
                  ...(shown.subProcesses ?? []).map((p) => ({ id: p.id, name: p.name, variant: false })),
                  // Вариант (AS IS / TO BE) — другое состояние процесса: его
                  // видно, но в выгрузку этой карты он не идёт.
                  ...(shown.variants ?? []).map((p) => ({ id: p.id, name: p.name, variant: true })),
                ].map((page) => (
                  <button
                    key={page.id || 'main'}
                    title={
                      page.variant
                        ? 'Вариант процесса: открывается здесь, но в выгрузку этой карты не идёт'
                        : 'title' in page ? page.title : undefined
                    }
                    onClick={() => openPage(page.id)}
                    className={`rounded-md border px-2 py-1 text-[11px] transition-colors ${
                      openedPageId === page.id
                        ? 'border-primary bg-primary text-primary-foreground'
                        : 'bg-card text-muted-foreground hover:text-foreground'
                    } ${page.variant ? 'border-dashed' : ''}`}
                  >
                    {page.name}
                    {page.variant && <span className="ml-1 opacity-70">· вариант</span>}
                  </button>
                ))}
              </div>
            )}
            <ProcessVisualizer
              ref={canvasRef}
              process={shownOpened}
              // Просмотр версии — только чтение: ни правки, ни карточки шага.
              onEdit={preview ? undefined : commitPage}
              onUndo={undo}
              onRedo={redo}
              canUndo={past.length > 0}
              canRedo={future.length > 0}
              onOpenDrawio={preview ? undefined : () => void openDrawio()}
              onSelectNode={(node) => !preview && setSelectedNode(node)}
              selectedNodeId={selectedNode?.id}
              focus={canvasFocus ?? undefined}
              onClearFocus={() => {
                setCanvasFocus(null)
                setActiveIssueKey('')
              }}
            />
          </div>
          {panelOpen && !preview && (
            <MapSidePanel
              page={openedProcess}
              passport={currentProcess.passport}
              onChangePassport={changePassport}
              onChangeLaneRate={(laneId, rate) => commitPage(setLaneRate(openedProcess, laneId, rate))}
              onOpenHistory={() => setIsHistoryOpen(true)}
              onClose={() => setPanelOpen(false)}
            />
          )}
        </div>
      </main>

      {/* Отчёт о качестве импортированной карты — под картой. Клик по замечанию не открывает
          карточку шага сразу: сотруднику сначала надо увидеть, ГДЕ на карте
          проблема, а карточка закрыла бы половину холста. */}
      <ImportReport
        process={currentProcess}
        status={checkStatus}
        activeIssueKey={activeIssueKey}
        onFocusIssue={(issue, nodeIds, key) => {
          setSelectedNode(null)
          setActiveIssueKey(key)
          setCanvasFocus((prev) => ({
            nodeIds,
            issue,
            nonce: (prev?.nonce ?? 0) + 1,
          }))
        }}
      />

      {/* Modals and Drawers */}
      <ProcessImportModal
        open={isImportOpen}
        onOpenChange={setIsImportOpen}
        onProcessLoaded={handleProcessLoaded}
      />

      <NodeDetailDrawer
        node={selectedNode}
        process={currentProcess}
        onClose={() => setSelectedNode(null)}
        onSaveNode={handleSaveNode}
        onOpenPage={openPage}
      />

      <ExportDrawer
        open={isExportOpen}
        onOpenChange={setIsExportOpen}
        process={currentProcess}
      />

      {isMaterialsOpen && (
        <MapMaterialsDialog processId={currentProcess.id} title={currentProcess.name} onClose={() => setIsMaterialsOpen(false)} />
      )}

      {isDrawioOpen && (
        <DrawioEditor
          processId={currentProcess.id}
          title={currentProcess.name}
          onSave={saveFromDrawio}
          onClose={() => setIsDrawioOpen(false)}
        />
      )}

      <ProcessHistoryDialog
        open={isHistoryOpen}
        onOpenChange={setIsHistoryOpen}
        processId={currentProcess.id}
        revision={currentProcess.revision}
        onRestore={restoreVersion}
        onPreview={(meta) => void openPreview(meta)}
      />
    </div>
  )
}
