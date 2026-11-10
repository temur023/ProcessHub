import { apiFetch } from './api'
import type { DocumentCapability } from './knowledgeAccess'

export interface Department {
  id: string
  name: string
  catalog_item_id: string | null
  catalog_space_id: string | null
  catalog_number: string
  catalog_position: number
  permissions: Array<'reader' | 'owner' | 'reviewer'>
  capabilities: DocumentCapability[]
}
export interface KnowledgeRole { id: string; name: string }
export interface Instruction {
  id: string
  title: string
  role_id: string | null
  role_name: string | null
  version_count: number
}
export interface InstructionVersion {
  id: string
  number: number
  filename: string
  byte_size: number
  content_hash: string
  effective_from: string | null
  effective_to: string | null
  uploaded_at: string
}
export interface DeletedInstruction extends Instruction {
  deleted_at: string
  deleted_by: number
}
interface InstructionLifecycleResult { id: string; deleted_at: string | null; deleted_by: number | null }

const base = '/api/v1/knowledge'
export class KnowledgeRequestError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'KnowledgeRequestError'
    this.status = status
  }
}

async function responseError(response: Response): Promise<KnowledgeRequestError> {
  const body = await response.json().catch(() => null) as { detail?: unknown } | null
  const fallback = response.status === 403 ? 'Недостаточно прав для этого действия'
    : response.status === 404 ? 'Документ или департамент недоступен'
    : response.status === 413 ? 'Загрузите непустой файл до 10 МБ'
    : response.status === 422 ? 'Проверьте название, роль, файл и период действия документа'
    : response.status === 401 ? 'Войдите в Process Hub заново'
    : 'Не удалось выполнить действие. Повторите позже'
  return new KnowledgeRequestError(typeof body?.detail === 'string' ? body.detail : fallback, response.status)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await apiFetch(base + path, init)
  } catch (error) {
    if (init?.signal?.aborted) throw error
    throw new Error('Не удалось связаться с сервисом документов. Проверьте соединение и повторите')
  }
  if (!response.ok) throw await responseError(response)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
export const getDepartments = (signal?: AbortSignal) => request<Department[]>('/departments', { signal })
export const getKnowledgeAccess = (signal?: AbortSignal) => request<{ user_id: number; is_platform_admin: boolean }>('/access', { signal })
export const createKnowledgeRole = (departmentId: string, name: string) => request<KnowledgeRole>(`/departments/${departmentId}/roles`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
})
export const getRoles = (id: string, signal?: AbortSignal) => request<KnowledgeRole[]>(`/departments/${id}/roles`, { signal })
export const getInstructions = (id: string, signal?: AbortSignal) => request<Instruction[]>(`/departments/${id}/instructions`, { signal })
export const getDeletedInstructions = (id: string, signal?: AbortSignal) => request<DeletedInstruction[]>(`/departments/${id}/deleted-instructions`, { signal })
export const changeInstructionLifecycle = (id: string, action: 'delete' | 'restore', reason: string) => request<InstructionLifecycleResult>(`/instructions/${id}/${action}`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason }),
})
export const getVersions = (id: string, signal?: AbortSignal) => request<InstructionVersion[]>(`/instructions/${id}/versions`, { signal })
export interface InstructionUploadResult {
  instruction: Instruction
  version: InstructionVersion
  replayed?: boolean
}

export function uploadInstruction(departmentId: string, roleId: string, title: string, file: File, from: string, to: string, uploadId: string) {
  const data = versionFormData(file, from, to, uploadId)
  data.append('department_id', departmentId)
  if (roleId) data.append('role_id', roleId)
  data.append('title', title)
  return request<InstructionUploadResult>('/instructions/upload', { method: 'POST', body: data })
}

function versionFormData(file: File, from: string, to: string, uploadId?: string) {
  const data = new FormData()
  data.append('file', file)
  if (from) data.append('effective_from', from)
  if (to) data.append('effective_to', to)
  if (uploadId) data.append('upload_id', uploadId)
  return data
}

export function uploadVersion(id: string, file: File, from: string, to: string, uploadId?: string) {
  const data = versionFormData(file, from, to, uploadId)
  return request<InstructionVersion>(`/instructions/${id}/versions`, { method: 'POST', body: data })
}

export interface SourceBlock {
  id: string
  order: number
  kind: 'heading' | 'paragraph' | 'table_row'
  text: string
  locator: Record<string, string | number>
  label: string
}
export interface DocumentPreparationState {
  job: null | {
    id: string
    version_id: string
    status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
    attempts: number
    error_code: string | null
    message: string | null
  }
  source: null | {
    id: string
    version_id: string
    parser_version: string
    content_hash: string
    status: 'ready' | 'partial' | 'needs_ocr'
    blocks: SourceBlock[]
    warnings: Array<{ code: string; message: string; locator?: Record<string, string | number> }>
    stats: { units_total: number; units_with_text: number; blocks_total: number; characters: number }
  }
}
export const getPreparation = (id: string, signal?: AbortSignal) => request<DocumentPreparationState>(`/instruction-versions/${id}/preparation`, { signal })
export const startPreparation = (id: string, signal?: AbortSignal) => request<DocumentPreparationState>(`/instruction-versions/${id}/preparation`, { method: 'POST', signal })
export const cancelPreparation = (id: string, signal?: AbortSignal) => request<DocumentPreparationState>(`/instruction-versions/${id}/preparation/cancel`, { method: 'POST', signal })

export async function downloadOriginal(version: InstructionVersion) {
  let response: Response
  try {
    response = await apiFetch(`${base}/instruction-versions/${version.id}/original`)
  } catch {
    throw new Error('Не удалось связаться с сервисом документов. Повторите скачивание')
  }
  if (!response.ok) throw await responseError(response)
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = version.filename
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 30_000)
}
