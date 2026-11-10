import type { DocumentDetail, DocumentsView } from './documents'

/** A folder classified as old is not evidence of an expired, verified edition. */
export function defaultInstructionEdition(document: DocumentDetail, view: DocumentsView, today = new Date().toISOString().slice(0, 10), sourceId?: string): string | undefined {
  const source = sourceId && document.editions.find(edition => edition.files.some(file => file.id === sourceId))
  if (source) return source.id
  const editions = [...document.editions].sort((a, b) => b.number - a.number)
  if (view === 'current') return editions.find(edition => edition.is_current)?.id
  if (view === 'unverified') return editions.find(edition => edition.verification_status === 'unverified')?.id
  if (view === 'historical') return editions.find(edition => edition.verification_status === 'verified' && edition.valid_to && edition.valid_to < today)?.id
  // Without a confirmed edition, open the one with the latest document date: earlier editions
  // uploaded later as history must not hide the newest text. The first edition carries the document's date.
  const dated = (edition: DocumentDetail['editions'][number]) => {
    const value = (edition.metadata as Record<string, unknown> | undefined)?.document_date
    return typeof value === 'string' ? value : edition.number === 1 ? document.document_date || '' : ''
  }
  const latest = [...editions].sort((a, b) => dated(b).localeCompare(dated(a)) || b.number - a.number)
  return editions.find(edition => edition.is_current)?.id || latest[0]?.id
}
