import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchCatalog } from '@/lib/api'
import { indexCatalog } from '@/lib/catalog'
import type { Catalog } from '@/types/catalog'

/**
 * Последнее дерево с сервера — общее на все экраны.
 *
 * Переход «пространство → подпространство → карта» не ждёт сети: экран
 * рисуется по последнему дереву сразу, а свежее приходит следом. В PIX
 * каждый такой переход — загрузка новой карты на холст.
 */
let cached: Catalog | null = null
const listeners = new Set<(catalog: Catalog) => void>()

/** Свежее дерево — всем открытым экранам. */
async function refresh(): Promise<void> {
  const fresh = await fetchCatalog()
  cached = fresh
  listeners.forEach((listener) => listener(fresh))
}

const message = (err: unknown) => (err instanceof Error ? err.message : String(err))

export function useCatalog() {
  const [catalog, setCatalog] = useState<Catalog | null>(cached)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      await refresh()
      setError(null)
    } catch (err) {
      setError(message(err))
    }
  }, [])

  useEffect(() => {
    let alive = true
    listeners.add(setCatalog)
    refresh().then(
      () => alive && setError(null),
      (err) => alive && setError(message(err)),
    )
    return () => {
      alive = false
      listeners.delete(setCatalog)
    }
  }, [])

  const index = useMemo(() => (catalog ? indexCatalog(catalog.items) : null), [catalog])
  return { catalog, index, error, reload }
}
