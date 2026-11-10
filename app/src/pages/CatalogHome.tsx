import { useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'
import { ChevronDown, ChevronRight, Folder, FolderInput, Inbox, Plus, Search, Trash2, Workflow } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { UserMenu } from '@/components/UserMenu'
import { CatalogShell } from '@/components/catalog/CatalogShell'
import { CatalogSidebar } from '@/components/catalog/CatalogSidebar'
import { BriefLine } from '@/components/catalog/BriefLine'
import { MapSearch } from '@/components/catalog/MapSearch'
import { ItemCard } from '@/components/catalog/ItemCard'
import { ConfirmDialog, NameDialog, PlaceDialog } from '@/components/catalog/CatalogDialogs'
import { useItemActions } from '@/components/catalog/useItemActions'
import { useCatalog } from '@/hooks/use-catalog'
import { createCatalogItem, deleteProcess } from '@/lib/api'
import { itemHref, itemLabel } from '@/lib/catalog'
import type { UnplacedProcess } from '@/types/catalog'

type Section = 'spaces' | 'unplaced' | 'search'

/**
 * Главный экран библиотеки: слева разделы и «Избранное», в центре —
 * пространства и их создание. «Без пространства» содержит процессы,
 * которые ещё не размещены в каталоге.
 */
export default function CatalogHome() {
  const { catalog, index, error, reload } = useCatalog()
  const [section, setSection] = useState<Section>('spaces')
  const [favoritesOpen, setFavoritesOpen] = useState(true)
  const [creating, setCreating] = useState(false)
  const [placing, setPlacing] = useState<UnplacedProcess | null>(null)
  const [deleting, setDeleting] = useState<UnplacedProcess | null>(null)
  const { menuFor, dialogs } = useItemActions(index, reload)

  const spaces = [...(index?.children.get(null) ?? [])].sort((a, b) => b.createdAt.localeCompare(a.createdAt))
  const favorites = catalog?.items.filter((n) => n.favorite) ?? []
  const unplaced = catalog?.unplaced ?? []

  if (!catalog || !index) {
    return (
      <CatalogShell>
        <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
          {error ? (
            <div className="max-w-md space-y-3 text-center">
              <p>Пространства не загрузились: {error}</p>
              <Button size="sm" variant="outline" onClick={() => void reload()}>
                Повторить
              </Button>
            </div>
          ) : (
            <>
              <Spinner className="h-4 w-4" /> Загружаем пространства…
            </>
          )}
        </div>
      </CatalogShell>
    )
  }

  const navItem = (id: Section, Icon: typeof Folder, label: string, count?: number) => (
    <button
      type="button"
      aria-current={section === id ? 'page' : undefined}
      onClick={() => setSection(id)}
      className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm ${
        section === id ? 'bg-muted font-medium' : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'
      }`}
    >
      <Icon className="h-4 w-4 shrink-0" />
      <span className="flex-1">{label}</span>
      {count !== undefined && <span className="tabular-nums text-xs">{count}</span>}
    </button>
  )

  return (
    <CatalogShell>
      <CatalogSidebar className="hidden md:block" contentClassName="gap-3 overflow-y-auto p-3 pt-6">
        <nav className="space-y-0.5" aria-label="Разделы">
          {navItem('spaces', Folder, 'Пространства', index.children.get(null)?.length ?? 0)}
          {navItem('unplaced', Inbox, 'Без пространства', unplaced.length)}
          {navItem('search', Search, 'Поиск по картам')}
        </nav>
        <div className="border-t pt-2">
          <button
            type="button"
            onClick={() => setFavoritesOpen((v) => !v)}
            className="flex w-full items-center gap-1.5 px-1 py-1 text-xs font-medium text-muted-foreground"
          >
            {favoritesOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            <span className="flex-1 text-left">Избранное</span>
            <span className="tabular-nums">{favorites.length}</span>
          </button>
          {favoritesOpen && (
            <ul className="mt-1 space-y-0.5">
              {favorites.map((node) => (
                <li key={node.id}>
                  <Link
                    to={itemHref(node)}
                    title={itemLabel(node)}
                    className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs hover:bg-muted"
                  >
                    {node.kind === 'space' ? (
                      <Folder className="h-3.5 w-3.5 shrink-0 text-blue-950 dark:text-white" />
                    ) : (
                      <Workflow className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
                    )}
                    <span className="truncate">{itemLabel(node)}</span>
                  </Link>
                </li>
              ))}
              {favorites.length === 0 && (
                <li className="px-2 py-1 text-[11px] text-muted-foreground">Добавляйте в избранное через меню «⋯»</li>
              )}
            </ul>
          )}
        </div>
      </CatalogSidebar>

      <main className="min-w-0 flex-1 overflow-y-auto p-4 pt-7 sm:p-6 sm:pt-8 lg:px-8">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <h1 className="min-w-0 text-xl font-semibold tracking-tight sm:text-2xl">Библиотека процессов</h1>
          <div className="flex items-center justify-between gap-3 lg:shrink-0 lg:justify-end">
            <Button size="sm" className="gap-1.5 text-xs" onClick={() => setCreating(true)}>
              <Plus className="h-4 w-4" /> Создать пространство
            </Button>
            <div className="flex shrink-0 items-center gap-3">
              <UserMenu />
            </div>
          </div>
        </div>
        {/* На узком экране левой колонки нет — разделы доступны здесь. */}
        <div className="mt-3 flex flex-wrap gap-2 md:hidden">
          <Button size="sm" variant={section === 'spaces' ? 'default' : 'outline'} onClick={() => setSection('spaces')}>
            Пространства
          </Button>
          <Button size="sm" variant={section === 'unplaced' ? 'default' : 'outline'} onClick={() => setSection('unplaced')}>
            Без пространства · {unplaced.length}
          </Button>
          <Button size="sm" variant={section === 'search' ? 'default' : 'outline'} onClick={() => setSection('search')}>
            Поиск
          </Button>
        </div>

        {section === 'search' ? (
          <MapSearch />
        ) : section === 'spaces' ? (
          <section className="mt-5">
            <div className="border-b pb-3">
              <h2 className="text-sm font-semibold">Пространства</h2>
            </div>
            <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
              {spaces.map((space) => (
                <ItemCard key={space.id} node={space} actions={menuFor(space)} />
              ))}
            </div>
            {spaces.length === 0 && (
              <p className="mt-6 text-sm text-muted-foreground">
                Пространств пока нет — создайте первое.
              </p>
            )}
          </section>
        ) : (
          <section className="mt-5">
            <div className="border-b pb-3">
              <h2 className="text-sm font-semibold">Без пространства</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Процессы на сервере, которые ещё не лежат ни в одном пространстве. Положите каждый на своё место.
              </p>
            </div>
            <ul className="mt-2 divide-y">
              {unplaced.map((process) => (
                <li key={process.id} className="flex items-center gap-2 py-2.5">
                  <Workflow className="h-4 w-4 shrink-0 self-start text-emerald-500 sm:self-center" />
                  <div className="min-w-0 flex-1">
                    <Link to={`/process/${process.id}`} className="block truncate text-sm hover:underline" title={process.name}>
                      {process.name}
                    </Link>
                    <BriefLine brief={process} className="block truncate text-[11px]" />
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 shrink-0 gap-1 text-xs"
                    onClick={() => setPlacing(process)}
                    title="Положить в пространство"
                  >
                    <FolderInput className="h-3.5 w-3.5" /> <span className="hidden sm:inline">В пространство…</span>
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 w-7 shrink-0 p-0 text-muted-foreground hover:text-rose-600"
                    onClick={() => setDeleting(process)}
                    title="Удалить схему — например, дубль"
                    aria-label={`Удалить схему «${process.name}»`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </li>
              ))}
            </ul>
            {unplaced.length === 0 && <p className="mt-6 text-sm text-muted-foreground">Все процессы разложены по пространствам.</p>}
          </section>
        )}
      </main>

      <NameDialog
        open={creating}
        onOpenChange={setCreating}
        title="Новое пространство"
        confirmLabel="Создать"
        onConfirm={async (title) => {
          await createCatalogItem({ parentId: null, kind: 'space', title })
          await reload()
          toast.success(`Пространство «${title}» создано`)
        }}
      />
      {placing && (
        <PlaceDialog
          open
          onOpenChange={(open) => !open && setPlacing(null)}
          title={`Положить «${placing.name}» в пространство`}
          description="Процесс станет картой выбранного пространства и получит в нём номер."
          index={index}
          onConfirm={async (parentId) => {
            if (!parentId) return
            await createCatalogItem({ parentId, kind: 'map', title: placing.name, processId: placing.id })
            await reload()
            toast.success(`«${placing.name}» теперь в «${index.byId.get(parentId)?.title ?? ''}»`)
          }}
        />
      )}
      {deleting && (
        <ConfirmDialog
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          title={`Удалить схему «${deleting.name}»?`}
          description="Схема и вся её история версий удаляются без возврата. Удаляйте дубли и ненужные копии; нужную схему лучше положить в пространство."
          confirmLabel="Удалить"
          onConfirm={async () => {
            await deleteProcess(deleting.id)
            await reload()
            toast.success(`Схема «${deleting.name}» удалена`)
          }}
        />
      )}
      {dialogs}
    </CatalogShell>
  )
}
