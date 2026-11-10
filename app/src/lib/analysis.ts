import { knowledgeRequest as request } from './documents'

export type FunctionStatus = 'covered' | 'partial' | 'conflict' | 'not_found' | 'unresolved'
export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
export type ReviewDecision = 'confirmed' | 'rejected' | 'clarify'
export interface AnalysisQuote { clause_id: string; quote: string; verified: boolean }
export interface AnalysisClause { id: string; text: string; score: number | null; document: string | null; chapter?: string; clause?: string; version?: string }
export interface AnalysisFunction {
  id: string
  text: string
  label?: string | null
  status: FunctionStatus
  clause_ids: string[]
  quotes: AnalysisQuote[]
  explanation: string
  recommendation: string
  decided_by: 'model' | 'search' | 'none'
}
export interface AnalysisMissing { id: string; clause_id: string; quote: string; quote_verified: boolean; related_function_ids: string[]; explanation: string; recommendation: string }
export interface AnalysisCounts { covered: number; partial: number; conflict: number; not_found: number; unresolved: number; missing: number }
export interface AnalysisResult {
  method_version: string
  position: string | null
  department: string | null
  regulation: { source: 'nova'; binding_document: string | null; documents: Array<{ name: string; clauses: number }>; clauses_found: number; complete: false }
  counts: AnalysisCounts
  functions: AnalysisFunction[]
  missing: AnalysisMissing[]
  clauses: Record<string, AnalysisClause>
  summary: { text: string; key_points: string[]; recommendations: string[]; generated_by: 'model' | 'rules' }
  warnings: string[]
  requests: { search: number; model: number }
  instruction?: { title: string; filename: string; content_hash: string }
}
export interface AnalysisReview { finding_id: string; decision: ReviewDecision; comment: string; reviewed_by: number; reviewed_at: string }
export interface AnalysisRun {
  id: string
  document_id: string
  edition_id: string
  file_id: string
  edition_number: number
  edition_label: string | null
  status: RunStatus
  stage: string | null
  progress: Record<string, { done: number; total: number }>
  stages: Array<{ id: string; label: string }>
  method_version: string
  collection: string
  error_code: string | null
  error_message: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  counts?: AnalysisCounts | null
  result?: AnalysisResult | null
  reviews?: Record<string, AnalysisReview>
  capabilities?: string[]
}
export interface NovaStatus { configured: boolean; collection: string | null }

const json = (body: unknown) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
export const getNovaStatus = (signal?: AbortSignal) => request<NovaStatus>('/nova/status', { signal })
export const getAnalysisRuns = (documentId: string, signal?: AbortSignal) => request<{ items: AnalysisRun[]; capabilities: string[] }>(`/documents/${encodeURIComponent(documentId)}/analysis-runs`, { signal })
export const getAnalysisRun = (runId: string, signal?: AbortSignal) => request<AnalysisRun>(`/analysis-runs/${encodeURIComponent(runId)}`, { signal })
export const startAnalysis = (documentId: string, editionId: string) => request<AnalysisRun>(`/documents/${encodeURIComponent(documentId)}/analysis-runs`, json({ edition_id: editionId }))
export const cancelAnalysis = (runId: string) => request<AnalysisRun>(`/analysis-runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' })
export const reviewFinding = (runId: string, findingId: string, decision: ReviewDecision, comment: string) => request<AnalysisReview>(`/analysis-runs/${encodeURIComponent(runId)}/reviews`, json({ finding_id: findingId, decision, comment }))
export const bindRegulation = (departmentId: string, document: string | null) => request<{ id: string; nova_regulation_document: string | null }>(`/departments/${encodeURIComponent(departmentId)}/nova-binding`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document }) })

export const functionStatusLabels: Record<FunctionStatus, string> = {
  covered: 'Соответствует', partial: 'Частично', conflict: 'Противоречит', not_found: 'Нет основания', unresolved: 'Не определено',
}
export const decisionLabels: Record<ReviewDecision, string> = { confirmed: 'Подтверждено', rejected: 'Отклонено', clarify: 'Требует уточнения' }

export interface DepartmentOverview { department_id: string; instructions: number; confirmed: number; awaiting: number; analyzed: number; pending: number }
export const getLibraryOverview = (signal?: AbortSignal) => request<DepartmentOverview[]>('/library/overview', { signal })

/** Latest automatic analysis of a registry item's edition. */
export interface RegistryAnalysis { id: string; status: RunStatus; created_at: string; finished_at: string | null; error_message: string | null; counts: AnalysisCounts | null; findings?: number; reviewed: number }
export interface AnalysisRegistryItem {
  document_id: string
  title: string
  department_id: string
  role_id: string | null
  role_name: string | null
  role_unit: string | null
  edition_id: string | null
  edition_number: number | null
  edition_label: string | null
  valid_from: string | null
  valid_to: string | null
  approved_on: string | null
  verification_status: 'verified' | 'unverified' | null
  is_current: boolean
  eligibility: 'ready' | 'unverified' | 'not_current' | 'missing_edition'
  analysis: RegistryAnalysis | null
  capabilities: string[]
}
export const getAnalysisRegistry = (departmentId: string, signal?: AbortSignal) => request<{ items: AnalysisRegistryItem[] }>(`/departments/${encodeURIComponent(departmentId)}/analysis-registry`, { signal })
