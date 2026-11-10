import { useId, useSyncExternalStore, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'

const defaultPreferenceKey = 'sqb.process-hub.catalog-sidebar.collapsed'
const preferences = new Map<string, ReturnType<typeof createPreference>>()
const narrow = () => typeof window !== 'undefined' && !!window.matchMedia?.('(max-width: 767px)').matches

function createPreference(key: string) {
  let initialized = false
  let collapsed = false
  const subscribers = new Set<() => void>()
  const notify = () => subscribers.forEach(listener => listener())
  function snapshot() {
    if (!initialized && typeof window !== 'undefined') {
      initialized = true
      // On a phone the open panel leaves no room for content: start collapsed.
      collapsed = narrow()
      try { collapsed = collapsed || window.localStorage.getItem(key) === 'true' }
      catch { /* Закрытое хранилище не мешает пользоваться панелью. */ }
    }
    return collapsed
  }
  function storageChanged(event: StorageEvent) {
    if (event.key !== key && event.key !== null) return
    try { if (event.storageArea !== window.localStorage) return }
    catch { return }
    collapsed = event.newValue === 'true'
    initialized = true
    notify()
  }
  return {
    snapshot,
    subscribe(listener: () => void) {
      subscribers.add(listener)
      if (subscribers.size === 1 && typeof window !== 'undefined') window.addEventListener('storage', storageChanged)
      return () => {
        subscribers.delete(listener)
        if (subscribers.size === 0 && typeof window !== 'undefined') window.removeEventListener('storage', storageChanged)
      }
    },
    setCollapsed(value: boolean) {
      collapsed = value
      initialized = true
      // A phone's choice should not reopen or close the panel on a desktop.
      if (!narrow()) try { window.localStorage.setItem(key, String(value)) }
      catch { /* Состояние сохраняется при переходах внутри приложения. */ }
      notify()
    },
  }
}
function preferenceFor(key: string) {
  let preference = preferences.get(key)
  if (!preference) { preference = createPreference(key); preferences.set(key, preference) }
  return preference
}

interface Props {
  children: ReactNode
  className?: string
  contentClassName?: string
  ariaLabel?: string
  preferenceKey?: string
  toggleLabel?: string
  onExpand?: () => void
}

/** Общая панель библиотеки: содержимое остаётся смонтировано при сворачивании. */
export function CatalogSidebar({ children, className, contentClassName, ariaLabel = 'Навигация библиотеки', preferenceKey = defaultPreferenceKey, toggleLabel = 'боковую панель библиотеки', onExpand }: Props) {
  const preference = preferenceFor(preferenceKey)
  const collapsed = useSyncExternalStore(preference.subscribe, preference.snapshot, () => false)
  const contentId = useId()
  const label = `${collapsed ? 'Развернуть' : 'Свернуть'} ${toggleLabel}`

  return <aside aria-label={ariaLabel} data-collapsed={collapsed} className={cn('relative h-full min-h-0 shrink-0 border-r bg-sidebar-background', collapsed ? 'w-6' : 'w-72', className)}>
    <div id={contentId} hidden={collapsed} inert={collapsed} aria-hidden={collapsed} className={cn('h-full min-h-0 w-72 flex-col overflow-hidden', collapsed ? 'hidden' : 'flex', contentClassName)}>{children}</div>
    <button type="button" aria-label={label} title={label} aria-expanded={!collapsed} aria-controls={contentId} onClick={() => { preference.setCollapsed(!collapsed); if (collapsed && onExpand) requestAnimationFrame(onExpand) }} className="absolute -right-3 top-1/2 z-20 flex h-12 w-6 -translate-y-1/2 items-center justify-center rounded-md border bg-background text-muted-foreground shadow-sm outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2">
      {collapsed ? <ChevronRight aria-hidden="true" className="size-4" /> : <ChevronLeft aria-hidden="true" className="size-4" />}
    </button>
  </aside>
}
