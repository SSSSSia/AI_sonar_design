import { Navigate, Route, Routes } from 'react-router-dom'
import AppLayout from './components/AppLayout'
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import RequirementInputPage from './pages/RequirementInputPage'
import SessionPage from './pages/session/SessionPage'
import KnowledgePage from './pages/kb/KnowledgePage'
import AdminPage from './pages/AdminPage'
import { useAuthStore } from './stores/authStore'

/** 阶段 4 路由。 */
export default function App() {
  const token = useAuthStore((s) => s.token)
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={token ? <AppLayout /> : <Navigate to="/login" replace />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/sessions/new" element={<RequirementInputPage />} />
        <Route path="/sessions/:id" element={<SessionPage />} />
        <Route path="/kb" element={<KnowledgePage />} />
        <Route path="/admin" element={<AdminPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
