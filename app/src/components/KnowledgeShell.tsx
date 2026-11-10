import type { ReactNode } from 'react'
import { CatalogShell } from '@/components/catalog/CatalogShell'

/** Содержимое модуля документов; общая навигация находится в PlatformShell. */
export function KnowledgeShell({ children }: { children: ReactNode }) {
  return <CatalogShell>
    <main className="min-h-0 min-w-0 flex-1 overflow-y-auto px-4 py-7 sm:px-8 sm:py-8">{children}</main>
  </CatalogShell>
}
