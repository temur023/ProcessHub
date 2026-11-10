import { useEffect, useMemo, useState } from 'react'
import { Navigate, Routes, Route, useNavigate } from 'react-router'
import { Toaster } from '@/components/ui/sonner'
import { Spinner } from '@/components/ui/spinner'
import { AuthContext, type User } from '@/lib/auth'
import { fetchCurrentUser, logout, onSignedOut } from '@/lib/api'
import CatalogHome from './pages/CatalogHome'
import CatalogPage from './pages/CatalogPage'
import LoginPage from './pages/LoginPage'
import ProcessPage from './pages/ProcessPage'
import ProfilePage from './pages/ProfilePage'
import DocumentsPage from './pages/DocumentsPage'
import AccessRolesPage from './pages/AccessRolesPage'
import AccessAssignmentsPage from './pages/AccessAssignmentsPage'
import AccessAuditPage from './pages/AccessAuditPage'
import AccessUsersPage from './pages/AccessUsersPage'
import { PlatformShell } from './components/PlatformShell'

/** Кто вошёл; `expired` — вход снова, потому что сессия кончилась посреди работы. */
interface Session {
  user: User | null
  expired?: boolean
}

export default function App() {
  // undefined — ещё не знаем, вошёл ли сотрудник.
  const [session, setSession] = useState<Session>()
  const navigate = useNavigate()

  useEffect(() => {
    // 401 посреди работы — сессия кончилась. После «Выйти» запоздалые ответы
    // 401 тоже приходят, но это уже не новость.
    onSignedOut(() => setSession((prev) => (prev?.user ? { user: null, expired: true } : prev)))
    void fetchCurrentUser().then((user) => setSession({ user }))
  }, [])

  const auth = useMemo(
    () =>
      session?.user && {
        user: session.user,
        signOut: () => {
          setSession({ user: null })
          void logout()
        },
        updateUser: (user: User) => setSession({ user }),
      },
    [session],
  )

  return (
    <>
      {/* closeButton: у сообщения в углу должен быть крестик — часть
          уведомлений живёт до 8 секунд, и закрыть их было нечем. Тостер один
          на всё приложение: уведомление переживает переход между экранами. */}
      <Toaster position="top-right" richColors closeButton />
      {auth ? (
        <AuthContext.Provider value={auth}>
          <PlatformShell>
          <Routes>
            <Route path="/" element={<CatalogHome />} />
            <Route path="/space/:itemId" element={<CatalogPage />} />
            <Route path="/map/:itemId" element={<CatalogPage />} />
            <Route path="/process/:processId" element={<ProcessPage />} />
            <Route path="/profile" element={<ProfilePage />} />
            <Route path="/knowledge" element={<DocumentsPage />} />
            <Route path="/knowledge/bank" element={<DocumentsPage />} />
            <Route path="/knowledge/manage" element={<Navigate to="/access/roles" replace />} />
            <Route path="/knowledge/departments/:departmentId" element={<DocumentsPage />} />
            <Route path="/roles" element={<Navigate to="/access/roles" replace />} />
            <Route path="/access" element={<Navigate to="/access/roles" replace />} />
            <Route path="/access/roles" element={<AccessRolesPage />} />
            <Route path="/access/employees" element={<AccessAssignmentsPage />} />
            <Route path="/access/audit" element={<AccessAuditPage />} />
            <Route path="/access/users" element={<AccessUsersPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          </PlatformShell>
        </AuthContext.Provider>
      ) : session ? (
        <LoginPage
          expired={Boolean(session.expired)}
          onSignedIn={(user) => {
            // После входа — всегда на главную, с какого бы адреса ни входили.
            setSession({ user })
            navigate('/', { replace: true })
          }}
        />
      ) : (
        <div className="flex h-screen items-center justify-center bg-background">
          <Spinner className="size-6 text-muted-foreground" />
        </div>
      )}
    </>
  )
}
