import { useCallback, useRef, useState } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'
import { ChevronDown, Folder, FolderPlus, ListChecks, PenLine, Plus, UploadCloud, Workflow } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Breadcrumbs, CrumbCurrent, CrumbLink } from '@/components/Breadcrumbs'
import { Spinner } from '@/components/ui/spinner'
import { CatalogShell } from '@/components/catalog/CatalogShell'
import { CatalogTree } from '@/components/catalog/CatalogTree'
import { ItemCard } from '@/components/catalog/ItemCard'
import { ProcessFilters } from '@/components/catalog/ProcessFilters'
import { ItemMenu } from '@/components/catalog/ItemMenu'
import { NameDialog, PickProcessDialog } from '@/components/catalog/CatalogDialogs'
import { useItemActions } from '@/components/catalog/useItemActions'
import { DrawioEditor } from '@/components/DrawioEditor'
import { ProcessImportModal } from '@/components/ProcessImportModal'
import { useCatalog } from '@/hooks/use-catalog'
import { createCatalogItem, fetchCatalog, importDrawioXml, saveDrawioToBackend, saveProcessToBackend, updateCatalogItem } from '@/lib/api'
import { contentsLabel, formatDate, itemHref, itemLabel, pathTo } from '@/lib/catalog'
import { handOverProcess } from '@/lib/process-handoff'
import { filtersActive, matchesFilters, resetProcessFilters, useProcessFilters } from '@/lib/process-filters'
import { plural } from '@/lib/presentation'
import type { BusinessProcess } from '@/types/process'

/**
 * Экран пространства или подпространства: дерево слева, в центре — путь от
 * главной, содержимое карточками и создание. Карта без схемы (их 515 пришло
 * из PIX одними названиями) открывается здесь же — с кнопкой загрузки; карта
 * со схемой сразу уходит в редактор.
 */
export default function CatalogPage() {
  const { itemId = '' } = useParams()
  const navigate = useNavigate()
  const { catalog, index, error, reload } = useCatalog()
  const [creating, setCreating] = useState<'space' | 'map' | null>(null)
  const [importMapId, setImportMapId] = useState<string | null>(null)
  const [picking, setPicking] = useState(false)
  const [drawing, setDrawing] = useState(false)
  const filters = useProcessFilters()
  const { menuFor, dialogs } = useItemActions(index, reload, (deleted) => {
    if (deleted.id === itemId) navigate(deleted.parentId ? `/space/${deleted.parentId}` : '/')
  })
  const node = index?.byId.get(itemId)

  if (!catalog || !index || !node) {
    return (
      <CatalogShell>
        <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
          {error || (catalog && !node) ? (
            <div className="max-w-md space-y-3 text-center">
              <p>{error ? `Пространства не загрузились: ${error}` : 'Такого пространства или карты нет — возможно, их удалили.'}</p>
              <Button size="sm" variant="outline" asChild>
                <Link to="/">На главную</Link>
              </Button>
            </div>
          ) : (
            <>
              <Spinner className="h-4 w-4" /> Загружаем…
            </>
          )}
        </div>
      </CatalogShell>
    )
  }
  if (node.kind === 'map' && node.hasProcess && node.processId) {
    return <Navigate to={`/process/${node.processId}`} replace />
  }

  const path = pathTo(index, node.id)
  const children = index.children.get(node.id) ?? []
  const isSpace = node.kind === 'space'
  // Фильтр сужает карты; подпространства остаются — в них свои карты.
  const filtering = filtersActive(filters)
  const maps = children.filter((child) => child.kind === 'map')
  const shownMaps = maps.filter((child) => matchesFilters(child, filters))
  const shown = children.filter((child) => child.kind === 'space' || matchesFilters(child, filters))
  const what = isSpace ? (node.parentId ? 'Подпространство' : 'Пространство') : 'Карта'

  const handleImported = async (process: BusinessProcess) => {
    const mapId = importMapId
    if (!mapId) return
    const saved = await saveProcessToBackend(process)
    if (!saved) {
      toast.error('Схема не сохранилась', { description: 'Бэкенд недоступен — повторите, когда он запустится.' })
      return
    }
    try {
      await updateCatalogItem(mapId, { processId: saved.id })
      await reload()
      navigate(handOverProcess(saved))
    } catch (err) {
      // Тот же файл уже загружали: у схемы тот же номер процесса, и она уже
      // лежит в своей карте — туда и ведём, а не плодим вторую.
      const holder = (await fetchCatalog().catch(() => null))?.items.find((i) => i.processId === saved.id)
      if (holder) {
        toast.info(`Эта схема уже лежит в карте «${itemLabel(holder)}» — открываю её`)
        await reload()
        navigate(handOverProcess(saved))
        return
      }
      toast.error('Схема сохранена, но не прикрепилась к карте', {
        description: `${err instanceof Error ? err.message : String(err)} Она — в разделе «Без пространства» на главной.`,
      })
    }
  }

  return (
    <CatalogShell>
      <div className="hidden md:flex">
        <CatalogTree index={index} items={catalog.items} selectedId={node.id} />
      </div>
      <main className="flex min-w-0 flex-1 flex-col overflow-y-auto p-4 pt-7 sm:p-6 sm:pt-8 lg:px-8">
        <Breadcrumbs label="Путь">
          {path.map((step) =>
            step.id === node.id ? (
              <CrumbCurrent key={step.id} title={step.title}>{step.number || step.title}</CrumbCurrent>
            ) : (
              <CrumbLink key={step.id} to={itemHref(step)} title={step.title} folder={step.kind === 'space'}>
                {step.number || step.title}
              </CrumbLink>
            ),
          )}
        </Breadcrumbs>

        <div className="mt-4 grid shrink-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-3">
          <div className={`flex min-w-0 items-start gap-3 ${isSpace ? 'col-span-2 sm:col-span-1' : ''}`}>
            {isSpace ? (
              <Folder className="mt-1 h-6 w-6 shrink-0 text-blue-950 dark:text-white" />
            ) : (
              <Workflow className="mt-1 h-6 w-6 shrink-0 text-muted-foreground" />
            )}
            <div className="min-w-0 flex-1">
              <h1 className="break-words text-xl font-semibold leading-snug tracking-tight">
                {node.number && <span className="mr-2 tabular-nums text-muted-foreground">{node.number}</span>}
                {node.title}
              </h1>
              <p className="mt-1 text-xs text-muted-foreground">
                {isSpace ? contentsLabel(node) : `${what} · создано ${formatDate(node.createdAt)}`}
              </p>
            </div>
          </div>
          <div className={`flex items-center gap-2 ${isSpace ? 'col-span-2 justify-self-end sm:col-span-1' : ''}`}>
            {isSpace && (
              <ItemMenu
                label="Добавить"
                trigger={<><Plus className="h-3.5 w-3.5" /> Добавить <ChevronDown className="h-3.5 w-3.5" /></>}
                actions={[
                  { label: 'Подпространство', icon: FolderPlus, onSelect: () => setCreating('space') },
                  { label: 'Карта', icon: Workflow, onSelect: () => setCreating('map') },
                ]}
              />
            )}
            <ItemMenu actions={menuFor(node).filter((a) => a.label !== 'Открыть')} />
          </div>
        </div>

        {isSpace ? (
          <section className="mt-6 border-t pt-5">
            {(filtering ? maps.length > 0 : maps.some((child) => child.process)) && (
              <div className="mb-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                <p className="text-xs tabular-nums text-muted-foreground" role="status">
                  {filtering
                    ? `Показано ${shownMaps.length} из ${plural(maps.length, 'карты', 'карт', 'карт')}`
                    : plural(maps.length, 'карта', 'карты', 'карт')}
                </p>
                <ProcessFilters items={maps} className="w-full sm:w-[22rem]" />
              </div>
            )}
            {filtering && maps.length > 0 && shownMaps.length === 0 && (
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed px-4 py-3 text-sm text-muted-foreground">
                <span>В этом пространстве нет карт с выбранным статусом и видом модели.</span>
                <Button size="sm" variant="outline" onClick={resetProcessFilters}>Сбросить фильтр</Button>
              </div>
            )}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
              {shown.map((child) => (
                <ItemCard key={child.id} node={child} actions={menuFor(child)} />
              ))}
            </div>
            {children.length === 0 && (
              <p className="mt-6 text-sm text-muted-foreground">
                Здесь пока пусто. Создайте подпространство или карту через «Добавить».
              </p>
            )}
          </section>
        ) : (
          <section aria-label="Добавление схемы" className="flex min-h-[320px] flex-1 items-center justify-center py-12 text-center">
            <div className="flex w-full max-w-sm flex-col items-center">
              <Workflow className="h-12 w-12 text-muted-foreground/60" />
              <h2 className="mt-5 text-lg font-semibold tracking-tight">Схема ещё не добавлена</h2>
              <p className="mt-2 max-w-xs text-sm leading-relaxed text-muted-foreground">
                Нарисуйте схему с нуля в draw.io, загрузите готовый файл или выберите схему, которая уже есть на платформе.
              </p>
              <Button className="mt-6 w-64 gap-2" onClick={() => setDrawing(true)}>
                <PenLine className="h-4 w-4" /> Создать в draw.io
              </Button>
              <Button variant="outline" className="mt-2 w-64 gap-2" onClick={() => setImportMapId(node.id)}>
                <UploadCloud className="h-4 w-4" /> Загрузить файл draw.io
              </Button>
              {catalog.unplaced.length > 0 && (
                <Button variant="outline" className="mt-2 w-64 gap-2" onClick={() => setPicking(true)}>
                  <ListChecks className="h-4 w-4" /> Выбрать из загруженных · {catalog.unplaced.length}
                </Button>
              )}
            </div>
          </section>
        )}
      </main>

      <NameDialog
        open={creating !== null}
        onOpenChange={(open) => !open && setCreating(null)}
        title={creating === 'map' ? `Новая карта в «${node.title}»` : `Новое подпространство в «${node.title}»`}
        confirmLabel="Создать"
        onConfirm={async (title) => {
          await createCatalogItem({ parentId: node.id, kind: creating ?? 'space', title })
          await reload()
          toast.success(creating === 'map' ? `Карта «${title}» создана` : `Подпространство «${title}» создано`)
        }}
      />
      {!isSpace && picking && (
        <PickProcessDialog
          open
          onOpenChange={setPicking}
          mapTitle={node.title}
          processes={catalog.unplaced}
          onConfirm={async (processId) => {
            await updateCatalogItem(node.id, { processId })
            await reload()
            navigate(`/process/${processId}`)
          }}
        />
      )}
      {!isSpace && (
        <ProcessImportModal
          open={importMapId !== null}
          onOpenChange={(open) => !open && setImportMapId(null)}
          onProcessLoaded={(process) => void handleImported(process)}
        />
      )}
      {!isSpace && drawing && (
        <DrawNewMap
          mapId={node.id}
          title={node.title}
          onDone={async (process) => {
            setDrawing(false)
            if (!process) return
            await reload()
            navigate(handOverProcess(process))
          }}
        />
      )}
      {dialogs}
    </CatalogShell>
  )
}

const escapeXml = (text: string) =>
  text.replace(/[<>&"']/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' })[c] ?? c)

/** Пустой лист draw.io; страница названа картой — так называется и разобранный процесс. */
const blankDrawio = (title: string) =>
  `<mxfile><diagram id="page-1" name="${escapeXml(title)}"><mxGraphModel><root>` +
  '<mxCell id="0"/><mxCell id="1" parent="0"/></root></mxGraphModel></diagram></mxfile>'

/** На листе ещё нет ни одной фигуры: сервер такой файл не примет, сохранять нечего. */
const isBlankDrawio = (xml: string) => xml.includes('<mxGraphModel') && !xml.includes('vertex="1"')

/**
 * Новая схема с нуля во встроенном draw.io. Карта на сервере появляется при
 * первом сохранении с фигурой: файл разбирается тем же импортом, что и
 * загруженный, и прикрепляется к этой карте; дальше правки идут её версиями.
 * Закрыли пустой лист — ничего не создаётся.
 */
function DrawNewMap({ mapId, title, onDone }: {
  mapId: string
  title: string
  onDone: (process: BusinessProcess | null) => void
}) {
  const created = useRef<BusinessProcess | null>(null)
  const linked = useRef(false)

  const save = useCallback(async (xml: string, explicit: boolean): Promise<boolean> => {
    if (!created.current && isBlankDrawio(xml)) return true
    try {
      created.current = created.current
        ? await saveDrawioToBackend(created.current.id, xml, created.current.revision)
        : await importDrawioXml(xml, `${title}.drawio`)
      if (!linked.current) {
        await updateCatalogItem(mapId, { processId: created.current.id })
        linked.current = true
      }
      if (explicit) toast.success('Схема сохранена', { description: 'Правка — в истории версий карты.' })
      return true
    } catch (err) {
      toast.error('Схема не сохранилась', { description: err instanceof Error ? err.message : String(err) })
      return false
    }
  }, [mapId, title])

  return (
    <DrawioEditor
      processId=""
      title={title}
      initialXml={blankDrawio(title)}
      onSave={save}
      onClose={() => onDone(linked.current ? created.current : null)}
    />
  )
}
