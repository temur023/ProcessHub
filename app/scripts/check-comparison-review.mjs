import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

const source = await readFile(new URL('../src/lib/comparison-review.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { getComparisonReview } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
const path = process.argv[2]
assert.ok(path, 'Pass the reviewed JSON fixture path')
const reviewed = JSON.parse(await readFile(path, 'utf8'))
assert.ok(getComparisonReview({ comparison_review: reviewed }))
assert.equal(getComparisonReview(null), null)
assert.equal(getComparisonReview({ comparison_review: { ...reviewed, regulation_available: true } }), null, 'A partial review cannot claim full source verification')
assert.equal(getComparisonReview({ comparison_review: { ...reviewed, source_document_id: 'https://evil.invalid' } }), null, 'Only document UUIDs can become navigation links')
assert.equal(getComparisonReview({ comparison_review: { ...reviewed, source_hash: 'bad' } }), null)
assert.equal(getComparisonReview({ comparison_review: { ...reviewed, findings: [{ ...reviewed.findings[0], status: 'confirmed' }] } }), null, 'No bank-approved findings are inferred')
assert.equal(getComparisonReview({ comparison_review: { ...reviewed, findings: [reviewed.findings[0], reviewed.findings[0]] } }), null)
assert.equal(getComparisonReview({ comparison_review: { ...reviewed, findings: [{ ...reviewed.findings[0], origin: undefined }] } }), null)
assert.equal(getComparisonReview({ comparison_review: { ...reviewed, findings: [{ ...reviewed.findings[0], assessment: ['invalid'] }] } }), null)
assert.equal(getComparisonReview({ comparison_review: { ...reviewed, findings: [] } }), null)
assert.equal(getComparisonReview({ comparison_review: { ...reviewed, limitations: [] } }), null)
console.log('Comparison review checks passed: source identity, evidence status, origin and malformed metadata.')
