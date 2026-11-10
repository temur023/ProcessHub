import { createContext, useContext } from 'react'

/** Вошедший сотрудник: под его именем правки видны в истории версий. */
export interface User {
  id: number
  email: string
  name: string
  isAdmin: boolean
}

export interface Auth {
  user: User
  signOut: () => void
  /** Профиль сменил имя — шапка показывает новое. */
  updateUser: (user: User) => void
}

export const AuthContext = createContext<Auth | null>(null)

/** Инициалы для кружка: «Иван Петров» → «ИП». */
export function initials(name: string): string {
  return name
    .split(' ')
    .slice(0, 2)
    .map((word) => word[0])
    .join('')
    .toUpperCase()
}

/** Кто вошёл и как выйти. Без входа приложение показывает только страницу входа. */
export function useAuth(): Auth {
  const auth = useContext(AuthContext)
  if (!auth) throw new Error('useAuth вызван без AuthContext')
  return auth
}

/** Карта, которую сотрудник правил. */
export interface MapActivity {
  id: string
  name: string
  /** Его версий на этой карте. */
  versions: number
  lastSavedAt: string
}

/** Профиль: сотрудник и то, что он сохранил на платформе (считает бэкенд). */
export interface Profile {
  user: User
  createdAt: string
  /** Версий, которые он начал, — всего и по источнику (edit, import, drawio, restore). */
  versions: number
  bySource: Record<string, number>
  /** Разных карт среди них. */
  maps: number
  lastSavedAt: string | null
  /** Его карты от свежих к старым, не больше 20. */
  recentMaps: MapActivity[]
}
