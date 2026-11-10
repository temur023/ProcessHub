import type React from 'react'
import { Monitor, Moon, Sun } from 'lucide-react'
import { useTheme } from 'next-themes'

const MODES = [
  { value: 'light', label: 'Светлая', Icon: Sun },
  { value: 'dark', label: 'Тёмная', Icon: Moon },
  { value: 'system', label: 'Как в системе', Icon: Monitor },
] as const

/** Общая тема платформы; настройка находится в личных данных профиля. */
export const ThemeToggle: React.FC = () => {
  const { theme, setTheme } = useTheme()
  const selected = theme ?? 'system'

  return (
    <div
      role="radiogroup"
      aria-label="Тема оформления"
      className="inline-flex shrink-0 items-center gap-1 rounded-xl border bg-muted/50 p-1"
      onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return
        event.preventDefault()
        const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="radio"]'))
        const current = buttons.indexOf(document.activeElement as HTMLButtonElement)
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? MODES.length - 1
          : (current + (['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : -1) + MODES.length) % MODES.length
        setTheme(MODES[next].value)
        buttons[next]?.focus()
      }}
    >
      {MODES.map(({ value, label, Icon }) => {
        const active = selected === value
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            aria-label={label}
            title={label}
            onClick={() => setTheme(value)}
            className={`flex h-9 items-center justify-center gap-2 rounded-lg px-3.5 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-foreground/30 ${
              active
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            <Icon aria-hidden="true" className="size-4" />{label}
          </button>
        )
      })}
    </div>
  )
}
