import { apiFetch } from './api'
import { KnowledgeRequestError, type DocumentPreparationState } from './knowledge'

export interface DocumentType { id: string; label: string; category: string }
export interface DocumentApplicability { department_id: string; department_name?: string; status: 'unconfirmed' | 'confirmed'; evidence: string }
export interface DocumentFile {
  id: string
  edition_id: string
  filename: string
  media_type: string
  content_hash: string
  byte_size: number
  uploaded_by: number
  uploaded_at: string
  purpose: 'main' | 'attachment' | 'approval' | 'supplement'
  source_metadata: Record<string, unknown>
  preparation?: DocumentPreparationState
}
export interface DocumentEdition {
  id: string
  document_id: string
  number: number
  label: string | null
  metadata: Record<string, unknown>
  uploaded_by: number
  uploaded_at: string
  verification_status: 'unverified' | 'verified'
  approved_on?: string | null
  valid_from: string | null
  valid_to: string | null
  verification_evidence: string | null
  verified_by: number | null
  verified_at: string | null
  is_current: boolean
  end_validity_evidence?: string | null
  ended_by?: number | null
  ended_at?: string | null
  files: DocumentFile[]
}
export interface RegistryDocument {
  id: string
  title: string
  document_type: string
  library_scope: 'department' | 'bank'
  department_id: string | null
  owner_department_id: string | null
  owner_department_name: string | null
  role_id: string | null
  role_name: string | null
  bank_number: string | null
  document_date: string | null
  metadata: Record<string, unknown>
  applicability: DocumentApplicability[]
  edition_count: number
  current_edition: DocumentEdition | null
  created_at: string
  deleted_at: string | null
  capabilities: string[]
  /** Client-side: findings of the latest analysis still waiting for an expert decision. */
  pending_findings?: number
}
export interface DocumentDetail extends RegistryDocument { editions: DocumentEdition[] }
export type PositionCategory = 'management' | 'specialists' | 'support' | 'other'
export interface BusinessPosition {
  id: string
  department_id: string
  name: string
  display_name: string | null
  unit_name: string
  category: PositionCategory
  historical_candidate: boolean
  classification_evidence: string
  document_count?: number
  file_count?: number
}
export interface PositionLibrary { roles: BusinessPosition[]; unassigned_documents: number }
export interface PositionDetail { role: BusinessPosition; documents: DocumentDetail[] }
export interface InstructionTextRun { text: string; change?: 'inserted' | 'deleted'; bold?: boolean; italic?: boolean; strike?: boolean }
export interface InstructionSection {
  id: string
  kind: string
  title: string
  source_heading: string | null
  blocks: Array<{ id: string; source_block_id: string; text: string; runs?: InstructionTextRun[]; label: string; locator: Record<string, string | number>; line_offset: number }>
}
export interface InstructionStructure {
  structure_version: string
  source_id: string
  content_hash: string
  parser_version: string
  status: 'ready' | 'partial' | 'needs_ocr'
  warnings: NonNullable<DocumentPreparationState['source']>['warnings']
  sections: InstructionSection[]
  chapter_count: number
  review_required: boolean
  has_revisions?: boolean
}
export interface InstructionStructureState { job: DocumentPreparationState['job']; structure: InstructionStructure | null }
export type PositionProperties = Pick<BusinessPosition, 'display_name' | 'unit_name' | 'category' | 'historical_candidate' | 'classification_evidence'>
export type DocumentsView = 'all' | 'current' | 'unverified' | 'historical' | 'analytics' | 'trash'
/** A position named while filing an instruction; the server reuses an existing one. */
export interface NewPosition { display_name: string; unit_name: string; category: 'management' | 'specialists' | 'support' }
export interface DocumentProperties {
  title: string
  document_type: string
  owner_department_id: string | null
  role_id: string | null
  bank_number: string | null
  document_date: string | null
  metadata: Record<string, unknown>
  applicability: DocumentApplicability[]
  new_position?: NewPosition | null
}
export interface DocumentUploadFile { file: File; purpose: DocumentFile['purpose']; source_metadata: Record<string, unknown> }
export interface DocumentUploadResult { document: RegistryDocument; edition: DocumentEdition; replayed?: boolean }

const base = '/api/v1/knowledge'
async function responseError(response: Response) {
  const body = await response.json().catch(() => null) as { detail?: unknown } | null
  const fallback = response.status === 403 ? 'Недостаточно прав для этого действия'
    : response.status === 404 ? 'Документ недоступен или был удалён'
    : response.status === 409 ? 'Данные изменились. Обновите документ и повторите'
    : response.status === 413 ? 'Допустимо до 10 файлов, до 10 МБ каждый и до 40 МБ вместе'
    : response.status === 422 ? 'Проверьте реквизиты, файлы и даты документа'
    : response.status === 401 ? 'Войдите в Process Hub заново'
    : 'Не удалось выполнить действие. Повторите позже'
  return new KnowledgeRequestError(typeof body?.detail === 'string' ? body.detail : fallback, response.status)
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try { response = await apiFetch(base + path, init) }
  catch (cause) {
    if (init?.signal?.aborted) throw cause
    throw new Error('Не удалось связаться с сервисом документов. Проверьте соединение и повторите')
  }
  if (!response.ok) throw await responseError(response)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
export const getDocumentTypes = (signal?: AbortSignal) => request<DocumentType[]>('/documents/types', { signal })
export const getPositionLibrary = (departmentId: string, signal?: AbortSignal) => request<PositionLibrary>(`/departments/${encodeURIComponent(departmentId)}/role-library`, { signal })
export const getPosition = (departmentId: string, positionId: string, signal?: AbortSignal) => request<PositionDetail>(`/departments/${encodeURIComponent(departmentId)}/role-library/${encodeURIComponent(positionId)}`, { signal })
export const updatePosition = (departmentId: string, positionId: string, data: PositionProperties) => request<BusinessPosition>(`/departments/${encodeURIComponent(departmentId)}/role-library/${encodeURIComponent(positionId)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })
export const createPosition = (departmentId: string, data: PositionProperties) => request<BusinessPosition>(`/departments/${encodeURIComponent(departmentId)}/role-library`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })
export const deletePosition = (departmentId: string, positionId: string) => request<void>(`/departments/${encodeURIComponent(departmentId)}/role-library/${encodeURIComponent(positionId)}`, { method: 'DELETE' })
export const getInstructionStructure =(fileId: string, signal?: AbortSignal) => request<InstructionStructureState>(`/documents/files/${encodeURIComponent(fileId)}/structure`, { signal })
export function getDocuments(scope: 'department' | 'bank', departmentId: string | undefined, view: DocumentsView, signal?: AbortSignal) {
  const params = new URLSearchParams({ library_scope: scope, view })
  if (departmentId) params.set('department_id', departmentId)
  return request<RegistryDocument[]>(`/documents?${params}`, { signal })
}
export const getDocument = (id: string, signal?: AbortSignal) => request<DocumentDetail>(`/documents/${encodeURIComponent(id)}`, { signal })
function applicabilityData(items: DocumentApplicability[]) {
  return items.map(({ department_id, status, evidence }) => ({ department_id, status, evidence }))
}
export const updateDocument = (id: string, properties: DocumentProperties, signal?: AbortSignal) => request<RegistryDocument>(`/documents/${encodeURIComponent(id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...properties, applicability: applicabilityData(properties.applicability) }), signal })

export function uploadDocument(properties: DocumentProperties, scope: 'department' | 'bank', departmentId: string | undefined, files: DocumentUploadFile[], label: string, uploadId: string, signal?: AbortSignal) {
  const data = editionData(files, label, uploadId)
  data.append('library_scope', scope)
  if (departmentId) data.append('department_id', departmentId)
  for (const key of ['title', 'document_type', 'owner_department_id', 'role_id', 'bank_number', 'document_date'] as const) {
    const value = properties[key]
    if (value) data.append(key, value)
  }
  data.append('metadata', JSON.stringify(properties.metadata))
  data.append('applicability', JSON.stringify(applicabilityData(properties.applicability)))
  if (properties.new_position) data.append('new_position', JSON.stringify(properties.new_position))
  return request<DocumentUploadResult>('/documents/upload', { method: 'POST', body: data, signal })
}
function editionData(files: DocumentUploadFile[], label: string, uploadId: string) {
  const data = new FormData()
  files.forEach(item => data.append('files', item.file))
  data.append('file_metadata', JSON.stringify(files.map(item => ({ purpose: item.purpose, source_metadata: item.source_metadata }))))
  if (label.trim()) data.append('edition_label', label.trim())
  data.append('upload_id', uploadId)
  return data
}
export const uploadDocumentEdition = (id: string, files: DocumentUploadFile[], label: string, uploadId: string, signal?: AbortSignal) => request<DocumentUploadResult>(`/documents/${encodeURIComponent(id)}/editions`, { method: 'POST', body: editionData(files, label, uploadId), signal })
export const verifyDocumentEdition = (id: string, editionId: string, from: string, to: string, evidence: string, signal?: AbortSignal, approvedOn?: string, closePrevious = false) => request<DocumentEdition>(`/documents/${encodeURIComponent(id)}/editions/${encodeURIComponent(editionId)}/verify`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ valid_from: from, valid_to: to || null, approved_on: approvedOn || null, evidence, close_previous: closePrevious }), signal })
export const endDocumentEditionValidity = (id: string, editionId: string, to: string, evidence: string, signal?: AbortSignal) => request<DocumentEdition>(`/documents/${encodeURIComponent(id)}/editions/${encodeURIComponent(editionId)}/end-validity`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ valid_to: to, evidence }), signal })
export const changeDocumentLifecycle = (id: string, action: 'delete' | 'restore', reason: string, signal?: AbortSignal) => request<RegistryDocument>(`/documents/${encodeURIComponent(id)}/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason }), signal })
export const getFilePreparation = (id: string, signal?: AbortSignal) => request<DocumentPreparationState>(`/documents/files/${encodeURIComponent(id)}/preparation`, { signal })
export const startFilePreparation = (id: string, signal?: AbortSignal) => request<DocumentPreparationState>(`/documents/files/${encodeURIComponent(id)}/preparation`, { method: 'POST', signal })
export const cancelFilePreparation = (id: string, signal?: AbortSignal) => request<DocumentPreparationState>(`/documents/files/${encodeURIComponent(id)}/preparation`, { method: 'DELETE', signal })
/** `clean`: the Word text in force, without unaccepted tracked changes or highlighter marks. */
export async function getDocumentPreview(id: string, signal?: AbortSignal, view: 'clean' | 'markup' = 'clean'): Promise<Blob> {
  const url = `${base}/documents/files/${encodeURIComponent(id)}/preview?view=${view}`
  let response = await apiFetch(url, { signal })
  // The Word converter handles one file at a time and answers 429 while busy.
  for (let attempt = 1; response.status === 429 && attempt <= 4; attempt++) {
    await new Promise(resolve => setTimeout(resolve, attempt * 1500))
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    response = await apiFetch(url, { signal })
  }
  if (!response.ok) throw await responseError(response)
  if (!response.headers.get('content-type')?.startsWith('application/pdf')) throw new Error('Сервис не вернул документ для просмотра')
  return response.blob()
}
export async function downloadDocumentFile(file: DocumentFile, signal?: AbortSignal) {
  let response: Response
  try { response = await apiFetch(`${base}/documents/files/${encodeURIComponent(file.id)}/original`, { signal }) }
  catch (cause) { if (signal?.aborted) throw cause; throw new Error('Не удалось связаться с сервисом документов. Повторите скачивание') }
  if (!response.ok) throw await responseError(response)
  const blob = await response.blob()
  if (signal?.aborted) return
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = file.filename
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 30_000)
}
export { request as knowledgeRequest }
