import { getComparisonReview, type ComparisonReview } from './comparison-review'
import { getDocument, getDocuments, type DocumentDetail, type DocumentEdition } from './documents'
import { KnowledgeRequestError } from './knowledge'

export interface ComparisonReport {
  document: DocumentDetail
  edition: DocumentEdition
  review: ComparisonReview
}

const validId = (value: string) => /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value)
const validHash = (value: string) => /^[a-f0-9]{64}$/.test(value)

/** Match an actual source file, never a role, filename or mutable document metadata. */
export function matchesSource(review: ComparisonReview, document: DocumentDetail, selectedEdition?: DocumentEdition): boolean {
  if (document.document_type !== review.source_type || document.deleted_at || review.source_document_id !== document.id) return false
  if (![review.source_document_id, review.source_edition_id, review.source_file_id].every(validId) || !validHash(review.source_hash)) return false
  if (selectedEdition && (selectedEdition.document_id !== document.id || selectedEdition.id !== review.source_edition_id)) return false
  const edition = document.editions.find(item => item.id === review.source_edition_id && item.document_id === document.id)
  const file = edition?.files.find(item => item.id === review.source_file_id && item.edition_id === edition.id)
  return !!file && validHash(file.content_hash) && file.content_hash === review.source_hash
}

/** The edition is immutable; top-level metadata is only editable catalogue information. */
export function comparisonReportsFromDocument(document: DocumentDetail): ComparisonReport[] {
  if (document.document_type !== 'analytics' || document.deleted_at || !validId(document.id)) return []
  return document.editions.flatMap(edition => {
    const review = getComparisonReview(edition.metadata)
    return review && validId(edition.id) && edition.document_id === document.id ? [{ document, edition, review }] : []
  })
}

export async function loadComparisonReports(scope: 'department' | 'bank', departmentId: string | undefined, signal: AbortSignal): Promise<ComparisonReport[]> {
  signal.throwIfAborted()
  const items = await getDocuments(scope, departmentId, 'all', signal)
  signal.throwIfAborted()
  const candidates = items.filter(item => item.document_type === 'analytics' && !item.deleted_at)
  const results: ComparisonReport[][] = new Array(candidates.length)
  let cursor = 0
  let failed = false
  async function worker() {
    while (!failed && cursor < candidates.length) {
      signal.throwIfAborted()
      const index = cursor++
      try {
        const document = await getDocument(candidates[index].id, signal)
        signal.throwIfAborted()
        // A stale list or a changed identity must not become an unrelated report.
        if (document.id !== candidates[index].id) throw new Error('Изменилась связь отчёта. Повторите загрузку.')
        results[index] = comparisonReportsFromDocument(document)
      } catch (cause) {
        failed = true
        throw cause
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(4, candidates.length) }, worker))
  signal.throwIfAborted()
  return results.flat().sort((a, b) => b.review.checked_on.localeCompare(a.review.checked_on)
    || b.edition.uploaded_at.localeCompare(a.edition.uploaded_at) || b.edition.number - a.edition.number
    || a.document.id.localeCompare(b.document.id))
}

/** Resolve each distinct real source before a catalogue describes a report as linked. */
export async function validateComparisonReports(reports: ComparisonReport[], signal: AbortSignal): Promise<ComparisonReport[]> {
  signal.throwIfAborted()
  const sourceIds = [...new Set(reports.map(report => report.review.source_document_id).filter(validId))]
  const sources = new Map<string, DocumentDetail>()
  let cursor = 0
  let failed = false
  async function worker() {
    while (!failed && cursor < sourceIds.length) {
      signal.throwIfAborted()
      const sourceId = sourceIds[cursor++]
      try {
        const document = await getDocument(sourceId, signal)
        signal.throwIfAborted()
        sources.set(sourceId, document)
      } catch (cause) {
        if (cause instanceof KnowledgeRequestError && (cause.status === 403 || cause.status === 404)) continue
        failed = true
        throw cause
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(4, sourceIds.length) }, worker))
  signal.throwIfAborted()
  return reports.filter(report => {
    const source = sources.get(report.review.source_document_id)
    return !!source && matchesSource(report.review, source)
  })
}
