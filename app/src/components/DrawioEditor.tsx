import React, { useEffect, useRef, useState } from 'react'
import { Loader2, ServerOff, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { fetchDrawioXml } from '@/lib/api'

/**
 * Встроенный редактор draw.io.
 *
 * Сам draw.io развёрнут у себя (сервис ``drawio`` в docker-compose.yml) и
 * отдаётся по пути ``/drawio/`` того же адреса, что и платформа: в разработке —
 * прокси Vite, в Docker — nginx. Карты банка на diagrams.net не уходят.
 *
 * Разговор с редактором — протокол embed draw.io (``proto=json``): он
 * сообщает ``init``, мы отдаём ему выгрузку карты (``load``) с включённым
 * автосохранением. На каждую правку он присылает ``autosave`` с файлом, и через
 * ``AUTOSAVE_DELAY_MS`` тишины мы сохраняем его на сервере; «Сохранить»
 * присылает ``save``, выход — ``exit``. Ход сохранения видно в строке
 * состояния самого draw.io (``status``).
 */
const DRAWIO_URL = '/drawio/'
const EDITOR_PARAMS = new URLSearchParams({
  embed: '1',
  proto: 'json',
  spin: '1',
  lang: 'ru',
  saveAndExit: '1',
  // Ничего, что требует внешних сервисов diagrams.net.
  stealth: '1',
  libraries: '1',
})

/** Сколько ждать первого сообщения редактора, прежде чем сказать, что он не запущен. */
const INIT_TIMEOUT_MS = 20_000

/**
 * Пауза после последней правки, после которой рисунок уходит на сервер. Самая
 * большая карта («1.53», 1638 фигур) сохраняется за 3–5 с, поэтому сохранения
 * идут по одному, а правки, сделанные во время сохранения, уходят следующим.
 */
const AUTOSAVE_DELAY_MS = 2000

interface DrawioEditorProps {
  processId: string
  title: string
  /** Рисунок для открытия вместо карты с сервера — новая схема с нуля. */
  initialXml?: string
  /** Сохранить файл из редактора; ``explicit`` — нажали «Сохранить»; ``true`` — сохранён. */
  onSave: (xml: string, explicit: boolean) => Promise<boolean>
  onClose: () => void
}

type EditorState = 'starting' | 'ready' | 'unavailable'

export const DrawioEditor: React.FC<DrawioEditorProps> = ({ processId, title, initialXml, onSave, onClose }) => {
  const frameRef = useRef<HTMLIFrameElement>(null)
  const [state, setState] = useState<EditorState>('starting')
  const [problem, setProblem] = useState('')
  /** Последние обработчики — слушатель сообщений вешается один раз. */
  const handlers = useRef({ onSave, onClose })
  useEffect(() => {
    handlers.current = { onSave, onClose }
  }, [onSave, onClose])
  /** Закрыть редактор, сначала сохранив последнюю правку; задаётся вместе со слушателем. */
  const close = useRef<() => void>(() => onClose())

  useEffect(() => {
    const post = (message: object) =>
      frameRef.current?.contentWindow?.postMessage(JSON.stringify(message), window.location.origin)

    const timer = window.setTimeout(() => {
      setState((s) => (s === 'starting' ? 'unavailable' : s))
      setProblem((p) => p || 'Редактор draw.io не ответил.')
    }, INIT_TIMEOUT_MS)

    /** Рисунок, который редактор прислал, а сервер ещё не сохранил. */
    let pending: string | null = null
    let saving: Promise<boolean> | null = null
    /**
     * Сервер отказал (карту успели сохранить по более свежей копии, ошибка):
     * автосохранение встаёт — молча повторять бессмысленно, а перезаписывать
     * чужую правку нельзя. Сообщение об отказе показывает платформа.
     */
    let halted = false
    let autosaveTimer = 0

    const flush = async (explicit = false): Promise<boolean> => {
      window.clearTimeout(autosaveTimer)
      while (saving) await saving
      if (pending === null) return !halted
      const xml = pending
      pending = null
      post({ action: 'status', message: 'Сохраняем на платформе…', modified: true })
      saving = handlers.current.onSave(xml, explicit)
      const saved = await saving
      saving = null
      if (!saved) {
        halted = true
        pending ??= xml
      }
      post({
        action: 'status',
        message: saved ? 'Сохранено на платформе' : 'Не сохранено — см. сообщение платформы',
        modified: !saved || pending !== null,
      })
      return saved
    }

    /** Всё присланное — на сервер; ``false`` — что-то не сохранилось. */
    const settle = async (): Promise<boolean> => {
      while (!halted && (saving || pending !== null)) await flush()
      return !halted
    }

    const exit = async () => {
      if (saving || pending !== null) {
        post({ action: 'spinner', message: 'Сохраняем карту…', show: true })
        const saved = await settle()
        post({ action: 'spinner', show: false })
        if (!saved && !window.confirm('Последняя правка не сохранилась. Закрыть редактор без неё?')) return
      }
      handlers.current.onClose()
    }
    close.current = () => void exit()

    const onMessage = async (event: MessageEvent) => {
      if (event.source !== frameRef.current?.contentWindow || typeof event.data !== 'string') return
      let message: { event?: string; xml?: string; exit?: boolean; modified?: boolean }
      try {
        message = JSON.parse(event.data)
      } catch {
        return
      }
      if (message.event === 'init') {
        window.clearTimeout(timer)
        try {
          const xml = initialXml ?? await fetchDrawioXml(processId)
          post({ action: 'load', xml, autosave: 1, title, saveAndExit: '1' })
          setState('ready')
        } catch (err) {
          setState('unavailable')
          setProblem(`Карта не загрузилась в редактор. ${err instanceof Error ? err.message : String(err)}`)
        }
      } else if (message.event === 'autosave' && message.xml) {
        pending = message.xml
        window.clearTimeout(autosaveTimer)
        if (!halted) autosaveTimer = window.setTimeout(() => void flush(), AUTOSAVE_DELAY_MS)
      } else if (message.event === 'save' && message.xml) {
        // «Сохранить» — сразу, и это же повтор после отказа.
        pending = message.xml
        halted = false
        post({ action: 'spinner', message: 'Сохраняем карту…', show: true })
        const saved = (await flush(true)) && (await settle())
        post({ action: 'spinner', show: false })
        if (saved && message.exit) handlers.current.onClose()
      } else if (message.event === 'exit') {
        await exit()
      }
    }
    window.addEventListener('message', onMessage)
    return () => {
      window.clearTimeout(timer)
      window.clearTimeout(autosaveTimer)
      window.removeEventListener('message', onMessage)
    }
  }, [processId, title, initialXml])

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-background" role="dialog" aria-label={`Редактор draw.io — ${title}`}>
      <div className="flex shrink-0 items-center gap-2 border-b bg-muted/40 px-3 py-1.5">
        <span className="min-w-0 truncate text-xs font-semibold">draw.io · {title}</span>
        <span className="hidden truncate text-[11px] text-muted-foreground md:inline">
          Правки сохраняются в эту же карту сами, через пару секунд; роль, система и ставки дорожек
          едут свойствами фигур.
        </span>
        <Button variant="ghost" size="sm" className="ml-auto h-7 gap-1 px-2 text-xs" onClick={() => close.current()}
          title="Закрыть редактор: последняя правка сохранится перед закрытием">
          <X className="h-3.5 w-3.5" />
          Закрыть
        </Button>
      </div>
      <div className="relative min-h-0 flex-1">
        <iframe
          ref={frameRef}
          src={`${DRAWIO_URL}?${EDITOR_PARAMS}`}
          title="Редактор draw.io"
          className="absolute inset-0 h-full w-full border-0"
        />
        {state !== 'ready' && (
          <div className="absolute inset-0 flex items-center justify-center bg-background/90 p-6">
            {state === 'starting' ? (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> Открываем карту в draw.io…
              </p>
            ) : (
              <div className="max-w-md space-y-2 text-center text-sm">
                <ServerOff className="mx-auto h-6 w-6 text-muted-foreground" />
                <p>{problem}</p>
                <p className="text-xs text-muted-foreground">
                  Редактор поднимается вместе с платформой: <code>docker compose up -d drawio</code>.
                </p>
                <Button variant="outline" size="sm" onClick={onClose}>Закрыть</Button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
