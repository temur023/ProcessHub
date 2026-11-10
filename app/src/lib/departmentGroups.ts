import { fetchCatalog } from './api'
import type { Department } from './knowledge'

/** Пространство верхнего уровня каталога Hub: из него перенесены папки библиотеки (департаменты, филиалы). */
export interface CatalogSpace { id: string; title: string; position: number }
export interface DepartmentGroup { id: string; title: string; departments: Department[] }

/** Пространства верхнего уровня; без каталога библиотека работает как раньше — одним списком. */
export async function getCatalogSpaces(): Promise<CatalogSpace[]> {
  try {
    const catalog = await fetchCatalog()
    return catalog.items.filter(item => item.parentId === null && item.kind === 'space').map(item => ({ id: item.id, title: item.title, position: item.position }))
  } catch {
    return []
  }
}

/** Папки по пространствам в порядке каталога; внутри пространства порядок папок сохраняется. */
export function groupDepartments(departments: Department[], spaces: CatalogSpace[]): DepartmentGroup[] {
  const known = new Map(spaces.map(space => [space.id, space]))
  const groups = new Map<string, DepartmentGroup>()
  for (const department of departments) {
    const id = department.catalog_space_id ?? ''
    const group = groups.get(id) ?? { id, title: known.get(id)?.title ?? 'Другие папки', departments: [] }
    group.departments.push(department)
    groups.set(id, group)
  }
  const rank = (id: string) => known.get(id)?.position ?? Number.MAX_SAFE_INTEGER
  return [...groups.values()].sort((a, b) => rank(a.id) - rank(b.id))
}
