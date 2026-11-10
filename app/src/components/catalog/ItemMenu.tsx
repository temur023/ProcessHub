import { useEffect, useRef, useState } from 'react'
import type React from 'react'
import { MoreHorizontal, type LucideIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'

export interface MenuAction {
  label: string
  icon: LucideIcon
  onSelect: () => void
  danger?: boolean
}

/** Меню действий с кнопкой «⋯» или именованным действием. */
export const ItemMenu: React.FC<{ actions: MenuAction[]; label?: string; trigger?: React.ReactNode }> = ({ actions, label = 'Действия', trigger }) => {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !box.current?.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', close)
    }
  }, [open])

  return (
    <div ref={box} className="relative" onClick={(event) => event.stopPropagation()}>
      <Button
        type="button"
        size={trigger ? 'sm' : 'icon-sm'}
        variant={trigger ? 'default' : 'ghost'}
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={trigger ? 'gap-1.5 text-xs' : 'h-7 w-7 text-muted-foreground hover:text-foreground'}
      >
        {trigger ?? <MoreHorizontal className="h-4 w-4" />}
      </Button>
      {open && (
        <div role="menu" className="absolute right-0 top-8 z-30 min-w-[190px] rounded-lg border bg-popover p-1 shadow-lg">
          {actions.map(({ label: text, icon: Icon, onSelect, danger }) => (
            <button
              key={text}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false)
                onSelect()
              }}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted ${
                danger ? 'text-rose-600 dark:text-rose-400' : ''
              }`}
            >
              <Icon className="h-3.5 w-3.5 shrink-0" />
              {text}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
