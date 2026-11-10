import { useId, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react'
import { Link, useLocation } from 'react-router'
import { LogOut, UserRound } from 'lucide-react'
import { initials, useAuth } from '@/lib/auth'

/** Нативный popover закрывается по Escape и клику снаружи, не обрезаясь панелью. */
export function AccountMenu({ rail = false }: { rail?: boolean }) {
  const { user, signOut } = useAuth()
  const { pathname } = useLocation()
  const menuId = useId()
  const trigger = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<CSSProperties>({})
  const itemClass = 'flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm outline-none hover:bg-muted focus-visible:bg-muted'

  function navigateMenu(event: KeyboardEvent<HTMLDivElement>) {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]'))
    const current = items.indexOf(document.activeElement as HTMLElement)
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
      : (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
    items[next]?.focus()
  }

  return <>
    <button ref={trigger} type="button" popoverTarget={menuId} aria-label={`Меню профиля: ${user.name}`} aria-haspopup="menu" aria-expanded={open} aria-controls={menuId}
      title="Меню профиля"
      onClick={event => {
        const rect = event.currentTarget.getBoundingClientRect()
        setPosition(rail ? { left: rect.right + 12, bottom: Math.max(12, window.innerHeight - rect.bottom) }
          : { right: Math.max(12, window.innerWidth - rect.right), top: rect.bottom + 8 })
      }}
      className={`flex shrink-0 items-center justify-center rounded-xl outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${rail ? 'size-11' : 'size-9'} ${open || pathname === '/profile' ? 'bg-muted' : 'hover:bg-muted/60'}`}>
      <span aria-hidden="true" className="flex size-8 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">{initials(user.name)}</span>
    </button>
    <div ref={menu} id={menuId} popover="auto" role="menu" aria-label="Меню профиля" style={{ inset: 'auto', ...position }}
      className="fixed m-0 w-64 rounded-xl border bg-popover p-1.5 text-popover-foreground shadow-lg"
      onToggle={event => {
        const visible = event.newState === 'open'
        setOpen(visible)
        if (visible) menu.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus()
      }}
      onKeyDown={navigateMenu}
      onBlur={event => { if (event.relatedTarget && event.relatedTarget !== trigger.current && !event.currentTarget.contains(event.relatedTarget as Node)) menu.current?.hidePopover() }}>
      <div className="border-b px-3 py-3">
        <p className="break-words text-sm font-semibold leading-5">{user.name}</p>
        <p className="mt-1 break-all text-xs leading-5 text-muted-foreground">{user.email}</p>
      </div>
      <div className="pt-1.5">
        <Link to="/profile" role="menuitem" className={itemClass} onClick={() => menu.current?.hidePopover()}><UserRound aria-hidden="true" className="size-4 text-muted-foreground" />Профиль</Link>
        <button type="button" role="menuitem" className={itemClass} onClick={() => { menu.current?.hidePopover(); signOut() }}><LogOut aria-hidden="true" className="size-4 text-muted-foreground" />Выйти</button>
      </div>
    </div>
  </>
}
