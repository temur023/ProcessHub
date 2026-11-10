import { apiFetch } from './api'
import { KnowledgeRequestError } from './knowledge'

export type DocumentPermission = 'reader' | 'owner' | 'reviewer'
export const documentCapabilities = [
  'documents.read',
  'documents.download',
  'instructions.create',
  'versions.create',
  'preparation.manage',
  'instructions.delete',
  'instructions.restore',
  'comparisons.manage',
  'editions.verify',
] as const
export type DocumentCapability = typeof documentCapabilities[number]
export const capabilityLabels: Record<DocumentCapability, string> = {
  'documents.read': 'Просмотр документов',
  'documents.download': 'Скачивание оригиналов',
  'instructions.create': 'Добавление документов',
  'versions.create': 'Добавление редакций',
  'preparation.manage': 'Подготовка документа',
  'instructions.delete': 'Перемещение документов в архив',
  'instructions.restore': 'Восстановление из архива',
  'comparisons.manage': 'Анализ инструкций',
  'editions.verify': 'Подтверждение версий',
}
export const capabilityDescriptions: Record<DocumentCapability, string> = {
  'documents.read': 'Открывать документы, историю редакций и извлечённый текст.',
  'documents.download': 'Скачивать исходные файлы любой редакции.',
  'instructions.create': 'Создавать документ и загружать его первый комплект файлов.',
  'versions.create': 'Добавлять комплекты новых редакций существующего документа.',
  'preparation.manage': 'Запускать, повторять и отменять извлечение текста из файла.',
  'instructions.delete': 'Перемещать документ в архив. Редакции, оригиналы и история сохраняются.',
  'instructions.restore': 'Возвращать документ из архива в активный список.',
  'comparisons.manage': 'Запускать анализ инструкций с положением в Nova и принимать решения по расхождениям.',
  'editions.verify': 'Подтверждать дату принятия и период действия версии по источнику, завершать действие прежней версии.',
}
export const permissionLabels: Record<DocumentPermission, string> = {
  reader: 'Чтение', owner: 'Работа с документами', reviewer: 'Проверка документов',
}
export const permissionDescriptions: Record<DocumentPermission, string> = {
  reader: 'Просмотр документов, истории редакций и скачивание оригиналов.',
  owner: 'Чтение, добавление документов и редакций, подготовка текста.',
  reviewer: 'Просмотр документов, истории редакций и скачивание оригиналов.',
}
export const accessFailure = (cause: unknown) => cause instanceof Error ? cause.message : 'Не удалось загрузить данные'
export interface AccessUser { id: number; name: string; email: string; isAdmin: boolean }
export interface AccessRecord { user_id: number; user: AccessUser | null; permissions: DocumentPermission[]; granted_at: string }
export interface AuditRecord { id: number; actor_id: number; actor: AccessUser | null; action: string; created_at: string; subject?: AccessUser | null; details: { user_id?: number; permission?: DocumentPermission; reason?: string } }
export interface AccessRole { id: string; name: string; permission: DocumentPermission; capabilities: DocumentCapability[]; department_ids: string[]; member_count: number; created_at: string; updated_at: string }
export interface RoleMembership { role_id: string; user_id: number; user: AccessUser | null; role_name: string; permission: DocumentPermission; capabilities: DocumentCapability[]; department_ids: string[]; assigned_at: string }
export interface RoleAuditDetails {
  readonly reason?: string
  readonly name?: string
  readonly permission?: DocumentPermission
  readonly capabilities?: readonly DocumentCapability[]
  readonly department_ids?: readonly string[]
  readonly previous_name?: string
  readonly previous_permission?: DocumentPermission
  readonly previous_capabilities?: readonly DocumentCapability[]
  readonly added?: readonly string[]
  readonly removed?: readonly string[]
}
export interface RoleAuditRecord { id: number; actor_id: number; actor: AccessUser | null; role_id: string; user_id: number | null; user: AccessUser | null; role_name: string; action: string; details: RoleAuditDetails; created_at: string }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try { response = await apiFetch('/api/v1/knowledge' + path, init) }
  catch (cause) {
    if (init?.signal?.aborted) throw cause
    throw new Error('Не удалось связаться с сервисом. Проверьте соединение и повторите')
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null
    throw new KnowledgeRequestError(typeof body?.detail === 'string' ? body.detail : 'Не удалось выполнить действие. Повторите позже', response.status)
  }
  return response.json() as Promise<T>
}
export const lookupAccessUser = (email: string) => request<AccessUser>('/admin/users/lookup', {
  method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ email }),
})
export const getAccessRecords = (departmentId: string, signal?: AbortSignal) => request<AccessRecord[]>(`/departments/${departmentId}/access`, { signal })
export const getDepartmentAudit = (departmentId: string, signal?: AbortSignal) => request<AuditRecord[]>(`/departments/${departmentId}/audit`, { signal })
export const changeDocumentAccess = (departmentId: string, userId: number, permission: DocumentPermission | 'all', enabled: boolean, reason: string) => request<{ changed: number }>(`/departments/${departmentId}/access`, {
  method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({user_id:userId,permission,enabled,reason}),
})
export const getAccessRoles = (signal?: AbortSignal) => request<AccessRole[]>('/access-roles', { signal })
export const saveAccessRole = (role: AccessRole | undefined, name: string, permission: DocumentPermission, departmentIds: string[], reason: string, capabilities: DocumentCapability[]) => request<AccessRole>(role ? `/access-roles/${role.id}` : '/access-roles', {
  method:role?'PATCH':'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name,permission,capabilities,department_ids:departmentIds,reason,...(role ? {expected_updated_at:role.updated_at} : {})}),
})
export const getRoleMemberships = (signal?: AbortSignal) => request<RoleMembership[]>('/access-role-memberships', { signal })
export const setRoleMembership = (roleId: string, userId: number, enabled: boolean, reason: string) => request<{ changed: boolean; assigned: boolean }>('/access-role-memberships', {
  method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({role_id:roleId,user_id:userId,enabled,reason}),
})
export const getRoleAudit = (signal?: AbortSignal) => request<RoleAuditRecord[]>('/access-role-audit', { signal })
