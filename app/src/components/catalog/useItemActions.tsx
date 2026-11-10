import { useState } from 'react'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'
import { ExternalLink, FileCode2, FolderInput, Paperclip, Pencil, Star, Trash2 } from 'lucide-react'
import type { CatalogNode } from '@/types/catalog'
import { deleteCatalogItem, updateCatalogItem } from '@/lib/api'
import { type CatalogIndex, itemHref, subtreeIds } from '@/lib/catalog'
import type { MenuAction } from './ItemMenu'
import { ConfirmDialog, NameDialog, PlaceDialog } from './CatalogDialogs'
import { SourceFileDialog } from './SourceFileDialog'
import { MapMaterialsDialog } from '@/components/MapMaterialsDialog'

type Pending = { kind: 'rename' | 'move' | 'delete' | 'source' | 'materials'; node: CatalogNode } | null

/**
 * Действия над пространством или картой — одни и те же на главной и в
 * пространстве: открыть, переименовать, перенести, звезда, удалить.
 * После каждой правки дерево перечитывается с сервера: номера «3.1» считает он.
 */
export function useItemActions(index: CatalogIndex | null, reload: () => Promise<void>, onDeleted?: (node: CatalogNode) => void) {
  const navigate = useNavigate()
  const [pending, setPending] = useState<Pending>(null)

  const toggleStar = async (node: CatalogNode) => {
    try {
      await updateCatalogItem(node.id, { favorite: !node.favorite })
      await reload()
    } catch (err) {
      toast.error('Не удалось отметить', { description: err instanceof Error ? err.message : String(err) })
    }
  }

  const menuFor = (node: CatalogNode): MenuAction[] => [
    { label: 'Открыть', icon: ExternalLink, onSelect: () => navigate(itemHref(node)) },
    ...(node.kind === 'map' && node.processId
      ? [
          { label: 'Материалы карты', icon: Paperclip, onSelect: () => setPending({ kind: 'materials', node }) },
          { label: 'Исходный файл draw.io', icon: FileCode2, onSelect: () => setPending({ kind: 'source', node }) },
        ]
      : []),
    { label: 'Переименовать', icon: Pencil, onSelect: () => setPending({ kind: 'rename', node }) },
    { label: 'Перенести…', icon: FolderInput, onSelect: () => setPending({ kind: 'move', node }) },
    { label: node.favorite ? 'Убрать из избранного' : 'В избранное', icon: Star, onSelect: () => void toggleStar(node) },
    { label: 'Удалить', icon: Trash2, onSelect: () => setPending({ kind: 'delete', node }), danger: true },
  ]

  const close = (open: boolean) => {
    if (!open) setPending(null)
  }
  const node = pending?.node
  const what = node?.kind === 'map' ? 'карту' : node?.parentId ? 'подпространство' : 'пространство'

  const dialogs = node && index && (
    <>
      {pending?.kind === 'materials' && node.processId && (
        <MapMaterialsDialog processId={node.processId} title={node.title} onClose={() => setPending(null)} />
      )}
      {pending?.kind === 'source' && node.processId && (
        <SourceFileDialog processId={node.processId} title={node.title} onClose={() => setPending(null)} />
      )}
      <NameDialog
        open={pending?.kind === 'rename'}
        onOpenChange={close}
        title={`Переименовать ${what}`}
        confirmLabel="Сохранить"
        initial={node.title}
        onConfirm={async (title) => {
          await updateCatalogItem(node.id, { title })
          await reload()
        }}
      />
      <PlaceDialog
        open={pending?.kind === 'move'}
        onOpenChange={close}
        title={`Перенести ${what} «${node.title}»`}
        description="Номер элемента пересчитается: он встанет последним в выбранном пространстве."
        index={index}
        exclude={new Set([...(node.kind === 'space' ? subtreeIds(index, node.id) : [node.id]), ...(node.parentId ? [node.parentId] : [])])}
        allowTop={node.kind === 'space' && node.parentId !== null}
        onConfirm={async (parentId) => {
          await updateCatalogItem(node.id, { parentId })
          await reload()
          toast.success(`«${node.title}» перенесено`)
        }}
      />
      <ConfirmDialog
        open={pending?.kind === 'delete'}
        onOpenChange={close}
        title={`Удалить ${what} «${node.title}»?`}
        description={
          node.kind === 'map'
            ? node.hasProcess
              ? 'Карта уйдёт из пространства, а её схема останется на сервере — в разделе «Без пространства».'
              : 'Карта без схемы удалится.'
            : 'Удалить можно только пустое пространство: сначала перенесите или удалите его содержимое.'
        }
        confirmLabel="Удалить"
        onConfirm={async () => {
          await deleteCatalogItem(node.id)
          onDeleted?.(node)
          await reload()
          toast.success(`«${node.title}» удалено`)
        }}
      />
    </>
  )

  return { menuFor, dialogs }
}
