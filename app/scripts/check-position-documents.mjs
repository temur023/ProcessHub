import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'
const source = ts.transpileModule(await readFile(new URL('../src/lib/position-documents.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { defaultInstructionEdition } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
const edition = (id, number, status, valid_to = null, is_current = false) => ({ id, number, verification_status: status, valid_to, is_current, files: [] })
const document = { editions: [edition('pending', 4, 'unverified'), edition('future', 3, 'verified', '2027-01-01'), edition('expired', 1, 'verified', '2026-09-30'), edition('current', 2, 'verified', '2026-12-31', true)] }
assert.equal(defaultInstructionEdition(document, 'all', '2026-10-09'), 'current')
assert.equal(defaultInstructionEdition(document, 'current', '2026-10-09'), 'current')
assert.equal(defaultInstructionEdition(document, 'unverified', '2026-10-09'), 'pending')
assert.equal(defaultInstructionEdition(document, 'historical', '2026-10-09'), 'expired')
assert.equal(defaultInstructionEdition({ editions: [edition('last-day', 2, 'verified', '2026-10-09'), edition('older', 1, 'verified', '2026-10-08')] }, 'historical', '2026-10-09'), 'older', 'Valid-to date remains inclusive')
assert.equal(defaultInstructionEdition({ editions: [edition('old-folder', 1, 'unverified', '2025-01-01')] }, 'historical', '2026-10-09'), undefined, 'Old classification cannot confirm history')
assert.equal(defaultInstructionEdition({ editions: [edition('pending', 1, 'unverified')] }, 'current'), undefined)
assert.equal(defaultInstructionEdition({ editions: [edition('two', 2, 'unverified'), edition('one', 1, 'unverified')] }, 'all'), 'two')
assert.equal(defaultInstructionEdition({ editions: [] }, 'all'), undefined)
assert.deepEqual(document.editions.map(item => item.id), ['pending', 'future', 'expired', 'current'], 'Selection cannot mutate stored edition ordering')
assert.equal(defaultInstructionEdition({ editions: [{ ...document.editions[2], files: [{ id: 'old-file' }] }, document.editions[3]] }, 'all', '2026-10-09', 'old-file'), 'expired', 'Legacy source links must select the actual source edition')
console.log('Position document checks passed: current, pending, expired, inclusive dates, unconfirmed old folders, empty history and immutable selection.')

const hierarchySource = ts.transpileModule(await readFile(new URL('../src/lib/position-hierarchy.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { buildPositionHierarchy, branchPositions, findPositionBranch } = await import(`data:text/javascript;base64,${Buffer.from(hierarchySource).toString('base64')}`)
const position = (id, title, unit_name) => ({ id, name: title, display_name: title, unit_name })
const positions = [
  position('group-a', 'Менеджер', 'Служба / Группа A'),
  position('separate', 'Менеджер', 'Самостоятельная группа'),
  position('director', 'Директор', 'Руководство департамента'),
  position('parent-manager', 'Главный менеджер', 'Служба'),
  position('group-b', 'Менеджер', 'Служба / Группа B'),
  position('deputy', 'Заместитель директора', 'Руководство / Направление'),
  position('literal-slash', 'Менеджер', 'Другая служба/группа'),
  position('nested', 'Специалист', 'Служба / Группа A / Сектор'),
]
const unchanged = JSON.stringify(positions)
const tree = buildPositionHierarchy(positions)
assert.equal(tree[0].positions[0].id, 'director', 'Department leadership is first')
assert.equal(tree[1].children[0].positions[0].id, 'deputy', 'Deputy remains inside the explicit direction path')
const service = findPositionBranch(tree, JSON.stringify(['Служба']))
assert.deepEqual(service.positions.map(role => role.id), ['parent-manager'], 'Parent retains its own position')
assert.deepEqual(new Set(branchPositions(service).map(role => role.id)), new Set(['parent-manager', 'group-a', 'group-b', 'nested']), 'Subtree includes own and nested positions once')
assert.equal(findPositionBranch(tree, JSON.stringify(['Служба', 'Группа A', 'Сектор'])).positions[0].id, 'nested')
assert.equal(tree.find(branch => branch.title === 'Самостоятельная группа').positions[0].id, 'separate', 'Flat group cannot acquire an inferred parent')
assert.equal(tree.find(branch => branch.title === 'Другая служба/группа').positions[0].id, 'literal-slash', 'Only explicit spaced path separators create hierarchy')
assert.deepEqual(new Set(tree.flatMap(branchPositions).map(role => role.id)), new Set(positions.map(role => role.id)), 'Hierarchy preserves every original position id')
assert.equal(findPositionBranch(tree, 'missing'), undefined)
assert.deepEqual(buildPositionHierarchy([]), [])
assert.equal(JSON.stringify(positions), unchanged, 'Hierarchy must not change stored names, units or source order')
const orderedUnits = buildPositionHierarchy([
  position('z-chief', 'Главный менеджер', 'Служба Я'),
  position('a-manager', 'Менеджер', 'Служба А'),
  position('group-10', 'Главный менеджер', 'Служба А / Группа 10'),
  position('group-2', 'Менеджер', 'Служба А / Группа 2'),
  position('junior', 'Младший менеджер', 'Служба Я'),
  position('senior', 'Старший менеджер', 'Служба Я'),
])
assert.deepEqual(orderedUnits.map(branch => branch.title), ['Служба А', 'Служба Я'], 'Unit order follows names, not the highest available job grade')
assert.deepEqual(orderedUnits[0].children.map(branch => branch.title), ['Группа 2', 'Группа 10'], 'Sibling groups use natural name order')
assert.deepEqual(orderedUnits[1].positions.map(role => role.id), ['z-chief', 'senior', 'junior'], 'Positions keep seniority order inside their actual unit')
console.log('Position hierarchy checks passed: explicit paths, leadership and natural unit order, parent and nested positions, stable identities and immutable inputs.')
