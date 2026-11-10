import type React from 'react'
import { Link } from 'react-router'
import { Folder, Workflow } from 'lucide-react'
import type { CatalogNode } from '@/types/catalog'
import { contentsLabel, formatDate, itemHref } from '@/lib/catalog'
import { BriefLine } from './BriefLine'
import { ItemMenu, type MenuAction } from './ItemMenu'

interface ItemCardProps {
  node: CatalogNode
  actions: MenuAction[]
}

/**
 * Карточка пространства или карты: открывается кликом, действия — в меню «⋯».
 * У карты со схемой внизу — сводка: статус, кто и когда менял.
 */
export const ItemCard: React.FC<ItemCardProps> = ({ node, actions }) => {
  const isSpace = node.kind === 'space'
  const showContents = isSpace && !node.parentId
  const Icon = isSpace ? Folder : Workflow
  return (
    <div className="group relative flex min-h-[104px] flex-col rounded-xl border bg-card p-3 transition-shadow hover:shadow-md">
      <Link to={itemHref(node)} className="flex min-w-0 flex-1 items-start gap-2.5 pr-7" title={node.title}>
        <Icon
          className={`mt-0.5 h-4 w-4 shrink-0 ${
            isSpace ? 'text-blue-950 dark:text-white' : node.hasProcess ? 'text-emerald-500' : 'text-muted-foreground'
          }`}
        />
        <span className="min-w-0">
          <span className="line-clamp-3 text-sm font-medium leading-snug">
            {node.number && <span className="mr-1.5 tabular-nums text-muted-foreground">{node.number}</span>}
            {node.title}
          </span>
        </span>
      </Link>
      {node.process ? (
        <div className="mt-2 pl-[26px] text-right text-[11px]">
          <BriefLine brief={node.process} />
        </div>
      ) : (
        <div className={`mt-2 flex items-center gap-2 pl-[26px] text-[11px] text-muted-foreground ${showContents ? 'justify-between' : 'justify-end'}`}>
          {showContents && <span className="truncate">{contentsLabel(node)}</span>}
          <span className="shrink-0 tabular-nums">{formatDate(node.createdAt)}</span>
        </div>
      )}
      <div className="absolute right-2 top-2 flex items-center">
        <ItemMenu actions={actions} />
      </div>
    </div>
  )
}
