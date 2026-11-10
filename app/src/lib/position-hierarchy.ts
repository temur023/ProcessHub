import type { BusinessPosition } from './documents'

export interface PositionBranch {
  key: string
  title: string
  path: string[]
  positions: BusinessPosition[]
  children: PositionBranch[]
}

export const positionName = (position: BusinessPosition) => position.display_name || position.name

// Each rank lists the Russian title and the original Uzbek titles of the HR register.
const ranks = [
  ['Директор', 'Departament direktori', 'Markaz direktori'],
  ['Заместитель директора', "Departament direktori o'rinbosari", "Markaz direktori o'rinbosari"],
  ['Xizmat rahbari'], ["Xizmat rahbari o'rinbosari"],
  ['Главный менеджер', 'Bosh menejer'], ['Старший менеджер', 'Katta menejer'], ['Ведущий менеджер', 'Yetakchi menejer'],
  ['Менеджер', 'Menejer'], ['Младший менеджер', 'Kichik menejer'], ['Делопроизводитель', 'Ish yurituvchi'],
]

const positionOrder = (position: BusinessPosition) => {
  const title = positionName(position)
  const index = ranks.findIndex(titles => titles.some(value => title === value || title.startsWith(value + ':') || title.startsWith(value + ' (')))
  return index < 0 ? ranks.length : index
}

export function branchPositions(branch: PositionBranch): BusinessPosition[] {
  return [...branch.positions, ...branch.children.flatMap(branchPositions)]
}

export function findPositionBranch(branches: PositionBranch[], key: string): PositionBranch | undefined {
  for (const branch of branches) {
    if (branch.key === key) return branch
    const nested = findPositionBranch(branch.children, key)
    if (nested) return nested
  }
}

/** Navigation follows only the explicit unit path; it does not infer reporting lines. */
export function buildPositionHierarchy(positions: BusinessPosition[]): PositionBranch[] {
  const roots: PositionBranch[] = []
  for (const position of positions) {
    const path = position.unit_name.split(' / ').map(part => part.trim()).filter(Boolean)
    if (!path.length) path.push('Подразделение не указано')
    let siblings = roots
    for (let index = 0; index < path.length; index++) {
      const segments = path.slice(0, index + 1)
      const key = JSON.stringify(segments)
      let branch = siblings.find(item => item.key === key)
      if (!branch) {
        branch = { key, title: path[index], path: segments, positions: [], children: [] }
        siblings.push(branch)
      }
      if (index === path.length - 1) branch.positions.push(position)
      siblings = branch.children
    }
  }
  const leadershipOrder = (branch: PositionBranch) => Math.min(2, ...branchPositions(branch).map(positionOrder))
  const sort = (branches: PositionBranch[], topLevel = false) => {
    for (const branch of branches) {
      branch.positions.sort((a, b) => positionOrder(a) - positionOrder(b) || positionName(a).localeCompare(positionName(b), 'ru'))
      sort(branch.children)
    }
    branches.sort((a, b) => (topLevel ? leadershipOrder(a) - leadershipOrder(b) : 0) || a.title.localeCompare(b.title, 'ru', { numeric: true }))
  }
  sort(roots, true)
  return roots
}
