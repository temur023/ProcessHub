import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

const compile = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const moduleUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
const validatorUrl = moduleUrl(compile(await readFile(new URL('../src/lib/comparison-review.ts', import.meta.url), 'utf8')))
const apiUrl = moduleUrl('export const getDocuments = (...args) => globalThis.__reviewApi.getDocuments(...args); export const getDocument = (...args) => globalThis.__reviewApi.getDocument(...args); export class KnowledgeRequestError extends Error { constructor(message, status) { super(message); this.status = status; } }')
const { KnowledgeRequestError } = await import(apiUrl)
const source = compile(await readFile(new URL('../src/lib/document-reviews.ts', import.meta.url), 'utf8'))
const { matchesSource, comparisonReportsFromDocument, loadComparisonReports, validateComparisonReports } = await import(moduleUrl(source.replace("'./comparison-review'", JSON.stringify(validatorUrl)).replace("'./documents'", JSON.stringify(apiUrl)).replace("'./knowledge'", JSON.stringify(apiUrl))))

const id = number => `00000000-0000-4000-8000-${number.toString(16).padStart(12, '0')}`
const hash = 'a'.repeat(64)
const original = { id: id(4), edition_id: id(2), content_hash: hash, filename: 'instruction.docx' }
const edition = { id: id(2), document_id: id(1), number: 2, files: [original] }
const oldEdition = { id: id(3), document_id: id(1), number: 1, files: [{ ...original, id: id(5), edition_id: id(3), content_hash: 'b'.repeat(64) }] }
const document = { id: id(1), document_type: 'job_instruction', deleted_at: null, editions: [edition, oldEdition] }
const review = {
  schema: 'nova-answer-review.v1', checked_on: '2026-10-09', summary: 'Требуется полный источник', limitations: ['Положение не получено'],
  source_document_id: document.id, source_edition_id: edition.id, source_file_id: original.id, source_hash: hash,
  source_filename: original.filename, source_type: 'job_instruction', position: 'Специалист', unit: 'Служба',
  employee_check: 'Учётная запись не подтверждена', regulation_reference: 'Положение из ответа Nova', regulation_available: false,
  findings: [{ id: 'A01', title: 'Уточнить полномочия', status: 'needs_evidence', nova_claim: 'Недостаточно данных', instruction_locator: 'Пункт 7', instruction_quote: 'Исходная формулировка', assessment: 'Проверить распределение', next_step: 'Получить положение', origin: 'nova' }],
}
assert.ok(matchesSource(review, document, edition))
assert.ok(matchesSource(review, document))
assert.equal(matchesSource(review, document, oldEdition), false, 'A review of another edition is not current for this selection')
for (const changes of [{ source_document_id: id(8) }, { source_edition_id: id(8) }, { source_file_id: id(8) }, { source_hash: 'b'.repeat(64) }, { source_hash: 'invalid' }, { source_document_id: 'https://external.invalid' }]) {
  assert.equal(matchesSource({ ...review, ...changes }, document, edition), false)
}
assert.equal(matchesSource(review, { ...document, editions: [{ ...edition, files: [{ ...original, edition_id: oldEdition.id }] }] }), false, 'The file must belong to the real edition')
assert.equal(matchesSource(review, { ...document, editions: [{ ...edition, document_id: id(8) }] }), false)
assert.equal(matchesSource(review, { ...document, document_type: 'procedure' }), false)
const procedureReview = { ...review, source_type: 'procedure', position: '', unit: '', employee_check: '' }
assert.ok(matchesSource(procedureReview, { ...document, document_type: 'procedure' }), 'A procedure review links to the same procedure file')
assert.equal(matchesSource(procedureReview, document), false, 'The source kind must match the document kind')
assert.equal(matchesSource(review, { ...document, deleted_at: '2026-10-09' }), false)
assert.equal(matchesSource(review, document, { ...edition, document_id: id(8) }), false)
const oldReview = { ...review, source_edition_id: oldEdition.id, source_file_id: oldEdition.files[0].id, source_hash: oldEdition.files[0].content_hash }
assert.ok(matchesSource(oldReview, document, oldEdition))
assert.equal(matchesSource(oldReview, document, edition), false)

const report = number => ({
  id: id(number), document_type: 'analytics', deleted_at: null, metadata: { comparison_review: { ...review, source_hash: 'f'.repeat(64) } },
  editions: [{ id: id(number + 100), document_id: id(number), number: 1, uploaded_at: '2026-10-09T12:00:00', metadata: { comparison_review: review } }],
})
const extracted = comparisonReportsFromDocument(report(10))
assert.equal(extracted.length, 1)
assert.equal(extracted[0].edition.id, id(110))
assert.equal(extracted[0].review.source_hash, hash, 'Editable catalogue metadata cannot replace immutable edition evidence')
assert.equal(comparisonReportsFromDocument({ ...report(10), editions: [{ ...report(10).editions[0], metadata: {} }] }).length, 0, 'Top-level metadata alone is not a report')
assert.equal(comparisonReportsFromDocument({ ...report(10), editions: [{ ...report(10).editions[0], document_id: id(999) }] }).length, 0)
assert.equal(comparisonReportsFromDocument({ ...report(10), deleted_at: '2026-10-09' }).length, 0)

let active = 0
let peak = 0
const calls = []
const controller = new AbortController()
globalThis.__reviewApi = {
  getDocuments: async (scope, departmentId, view, signal) => {
    assert.equal(scope, 'department'); assert.equal(departmentId, id(50)); assert.equal(view, 'all'); assert.equal(signal, controller.signal)
    return [...Array.from({ length: 9 }, (_, index) => report(index + 10)), { id: id(60), document_type: 'procedure' }]
  },
  getDocument: async (documentId, signal) => {
    assert.equal(signal, controller.signal)
    calls.push(documentId); active++; peak = Math.max(peak, active)
    await new Promise(resolve => setTimeout(resolve, 5))
    active--
    return report(parseInt(documentId.slice(-12), 16))
  },
}
const loaded = await loadComparisonReports('department', id(50), controller.signal)
assert.equal(loaded.length, 9)
assert.equal(calls.length, 9)
assert.equal(peak, 4, 'Only four detail requests may run at once')
assert.ok(loaded.every(item => item.review.source_hash === hash && item.edition.document_id === item.document.id))

const aborted = new AbortController()
aborted.abort()
globalThis.__reviewApi = { getDocuments: async () => { throw new Error('Aborted requests must not read the API') } }
await assert.rejects(loadComparisonReports('department', id(50), aborted.signal), { name: 'AbortError' })
const inFlight = new AbortController()
let started = 0
globalThis.__reviewApi = {
  getDocuments: async () => Array.from({ length: 9 }, (_, index) => report(index + 10)),
  getDocument: async (documentId, signal) => {
    started++
    await new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, 100)
      signal.addEventListener('abort', () => { clearTimeout(timer); reject(signal.reason) }, { once: true })
    })
    return report(parseInt(documentId.slice(-12), 16))
  },
}
const pending = loadComparisonReports('department', id(50), inFlight.signal)
await new Promise(resolve => setTimeout(resolve, 0))
inFlight.abort()
await assert.rejects(pending, { name: 'AbortError' })
assert.equal(started, 4, 'Cancellation must stop the remaining queue')

globalThis.__reviewApi = { getDocuments: async () => [report(10)], getDocument: async () => { throw new Error('Access changed') } }
await assert.rejects(loadComparisonReports('department', id(50), new AbortController().signal), /Access changed/, 'Incomplete reads must not look like an empty analysis')
globalThis.__reviewApi = { getDocuments: async () => [report(10)], getDocument: async () => report(11) }
await assert.rejects(loadComparisonReports('department', id(50), new AbortController().signal), /Изменилась связь/, 'A changed identity cannot become an unrelated report')

const repeated = [extracted[0], { ...extracted[0], edition: { ...extracted[0].edition, id: id(111), number: 2 } }]
let sourceCalls = 0
globalThis.__reviewApi = { getDocument: async () => { sourceCalls++; return document } }
const validated = await validateComparisonReports(repeated, new AbortController().signal)
assert.equal(sourceCalls, 1, 'Each distinct source document is fetched once')
assert.deepEqual(validated, repeated)
assert.equal(validated[1].edition.id, id(111), 'Validation preserves the exact report edition')
const wrongSource = { ...extracted[0], review: { ...review, source_hash: 'f'.repeat(64) } }
assert.deepEqual(await validateComparisonReports([wrongSource], new AbortController().signal), [], 'An existing source with a different hash is not a verified link')
for (const status of [403, 404]) {
  globalThis.__reviewApi = { getDocument: async () => { throw new KnowledgeRequestError('Source unavailable', status) } }
  assert.deepEqual(await validateComparisonReports(repeated, new AbortController().signal), [], 'Missing/inaccessible sources do not become links')
}
globalThis.__reviewApi = { getDocument: async () => { throw new KnowledgeRequestError('Server unavailable', 503) } }
await assert.rejects(validateComparisonReports(repeated, new AbortController().signal), /Server unavailable/, 'Transport/service failure cannot silently remove a report')

active = 0; peak = 0; sourceCalls = 0
const manySources = Array.from({ length: 9 }, (_, index) => ({ ...extracted[0], review: { ...review, source_document_id: id(index + 200) } }))
globalThis.__reviewApi = { getDocument: async sourceId => {
  sourceCalls++; active++; peak = Math.max(peak, active)
  await new Promise(resolve => setTimeout(resolve, 5))
  active--
  return { ...document, id: sourceId, editions: [{ ...edition, document_id: sourceId }] }
} }
assert.equal((await validateComparisonReports(manySources, new AbortController().signal)).length, 9)
assert.equal(sourceCalls, 9)
assert.equal(peak, 4, 'Source validation is also limited to four concurrent reads')

const sourceAbort = new AbortController()
started = 0
globalThis.__reviewApi = { getDocument: async (sourceId, signal) => {
  started++
  await new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, 100)
    signal.addEventListener('abort', () => { clearTimeout(timer); reject(signal.reason) }, { once: true })
  })
  return { ...document, id: sourceId }
} }
const sourcePending = validateComparisonReports(manySources, sourceAbort.signal)
await new Promise(resolve => setTimeout(resolve, 0))
sourceAbort.abort()
await assert.rejects(sourcePending, { name: 'AbortError' })
assert.equal(started, 4)
globalThis.__reviewApi = { getDocument: async () => { throw new Error('No API read after abort') } }
await assert.rejects(validateComparisonReports(repeated, sourceAbort.signal), { name: 'AbortError' })
delete globalThis.__reviewApi
console.log('Document review checks passed: exact source identity/hash, old editions, immutable metadata, source deduplication, concurrency limit, abort, unavailable source and partial-read failure.')
