/** A review of an externally supplied answer; never a bank-approved finding. */
export const reviewStatuses = {
  nova_error: 'Ошибка ответа Nova',
  unsupported: 'Вывод не обоснован',
  needs_evidence: 'Нужны дополнительные источники',
  instruction_supported: 'Подтверждено только по документу',
} as const
export type ReviewStatus = keyof typeof reviewStatuses
/** Вид проверяемого документа: отчёт связывается только с документом этого вида. */
export const reviewSourceTypes = {
  job_instruction: 'Инструкция',
  procedure: 'Порядок',
  methodology: 'Методика',
} as const
export type ReviewSourceType = keyof typeof reviewSourceTypes
export interface ReviewFinding {
  id: string
  title: string
  status: ReviewStatus
  nova_claim: string
  instruction_locator: string
  instruction_quote: string
  assessment: string
  next_step: string
  origin: 'nova' | 'document'
}
export interface ComparisonReview {
  schema: 'nova-answer-review.v1'
  checked_on: string
  summary: string
  limitations: string[]
  source_document_id: string
  source_edition_id: string
  source_file_id: string
  source_hash: string
  source_filename: string
  source_type: ReviewSourceType
  /** Должность, служба и проверка сотрудника — только у инструкции; у порядка пустые. */
  position: string
  unit: string
  employee_check: string
  regulation_reference: string
  regulation_available: false
  findings: ReviewFinding[]
}
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
const text = (value: unknown, max = 4000): value is string => typeof value === 'string' && value.length <= max
const uuid = (value: unknown): value is string => text(value, 36) && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value)
export function getComparisonReview(metadata: unknown): ComparisonReview | null {
  if (!object(metadata)) return null
  const value = metadata.comparison_review
  if (!object(value) || value.schema !== 'nova-answer-review.v1' || value.regulation_available !== false) return null
  if (!text(value.source_type, 40) || !Object.hasOwn(reviewSourceTypes, value.source_type)) return null
  if (!text(value.checked_on, 10) || !/^\d{4}-\d{2}-\d{2}$/.test(value.checked_on)) return null
  if (!['source_document_id', 'source_edition_id', 'source_file_id'].every(key => uuid(value[key]))) return null
  if (!text(value.source_hash, 64) || !/^[a-f0-9]{64}$/.test(value.source_hash)) return null
  if (!['summary', 'source_filename', 'regulation_reference'].every(key => text(value[key]) && !!value[key])) return null
  const instruction = value.source_type === 'job_instruction'
  if (!['position', 'unit', 'employee_check'].every(key => text(value[key]) && (!instruction || !!value[key]))) return null
  if (!Array.isArray(value.limitations) || !value.limitations.length || value.limitations.length > 20 || !value.limitations.every(item => text(item))) return null
  if (!Array.isArray(value.findings) || !value.findings.length || value.findings.length > 100) return null
  const ids = new Set<string>()
  for (const finding of value.findings) {
    if (!object(finding) || !text(finding.id, 40) || !finding.id || ids.has(finding.id)) return null
    ids.add(finding.id)
    if (!text(finding.status, 40) || !Object.hasOwn(reviewStatuses, finding.status)) return null
    if (finding.origin !== 'nova' && finding.origin !== 'document') return null
    if (!['title', 'nova_claim', 'instruction_locator', 'instruction_quote', 'assessment', 'next_step'].every(key => text(finding[key]))) return null
    if (!finding.title || !finding.assessment) return null
  }
  return value as unknown as ComparisonReview
}
