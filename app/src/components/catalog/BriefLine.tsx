import type React from 'react'
import { formatDate } from '@/lib/catalog'
import type { ProcessBrief } from '@/types/catalog'
import { PROCESS_STAGE_LABELS, PROCESS_STATUS_LABELS } from '@/types/process'

/**
 * Сводка о схеме: статус (кроме черновика — черновики почти все), кто и когда менял.
 */
export const BriefLine: React.FC<{ brief: ProcessBrief; className?: string }> = ({ brief, className = '' }) => {
  const changed = [brief.updatedBy, brief.updatedAt && formatDate(brief.updatedAt)].filter(Boolean).join(', ')
  const approved = brief.status && brief.status !== 'draft'
  return (
    <span className={`tabular-nums text-muted-foreground ${className}`}>
      {brief.stage && (
        <span className="mr-1 rounded border px-1 text-[10px] font-medium text-foreground/80">
          {PROCESS_STAGE_LABELS[brief.stage]}
        </span>
      )}
      {approved && (
        <span className="text-emerald-700 dark:text-emerald-400">{PROCESS_STATUS_LABELS[brief.status]}</span>
      )}
      {approved && changed && ' · '}
      {changed}
    </span>
  )
}
