import type React from 'react'

/** Рабочая область модуля внутри общей панели платформы. */
export const CatalogShell: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="flex h-full min-h-0 min-w-0 w-full flex-1 flex-col overflow-hidden bg-background text-foreground">
    <div className="flex min-h-0 min-w-0 flex-1">{children}</div>
  </div>
)
