import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { loadProcessFromBackend } from '@/lib/api'
import { handedOverProcess } from '@/lib/process-handoff'
import { cloneSampleProcess, sqbCreditProcess } from '@/lib/sample-processes'
import type { BusinessProcess } from '@/types/process'
import Home from './Home'

/** Адрес шаблона: карта, собранная вручную, а не из хранилища. */
const SAMPLE_PROCESS_ID = 'sample'

/**
 * Карта процесса по адресу `/process/:processId`: грузит её с сервера и
 * открывает редактор. Смена адреса — новая карта целиком (ключ сбрасывает
 * состояние редактора), как раньше при выборе процесса из списка.
 */
export default function ProcessPage() {
  const { processId = '' } = useParams()
  return <ProcessLoader key={processId} processId={processId} />
}

function ProcessLoader({ processId }: { processId: string }) {
  const isTemplate = processId === SAMPLE_PROCESS_ID
  const [process, setProcess] = useState<BusinessProcess | null>(() =>
    isTemplate ? cloneSampleProcess(sqbCreditProcess) : handedOverProcess(processId),
  )
  const [missing, setMissing] = useState(false)

  useEffect(() => {
    if (isTemplate || process) return
    let alive = true
    void loadProcessFromBackend(processId).then((loaded) => {
      if (!alive) return
      if (loaded) setProcess(loaded)
      else setMissing(true)
    })
    return () => {
      alive = false
    }
    // Карта грузится один раз на адрес; дальше её ведёт редактор.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isTemplate, processId])

  if (process) return <Home initialProcess={process} isTemplate={isTemplate} />
  return (
    <div className="flex h-screen items-center justify-center gap-2 bg-background text-sm text-muted-foreground">
      {missing ? (
        <div className="max-w-md space-y-3 text-center">
          <p>Карта не открылась: на сервере её нет или бэкенд недоступен.</p>
          <Button size="sm" variant="outline" asChild>
            <Link to="/">На главную</Link>
          </Button>
        </div>
      ) : (
        <>
          <Spinner className="h-4 w-4" /> Открываем карту…
        </>
      )}
    </div>
  )
}
