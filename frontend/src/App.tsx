import { Navigate, Route, Routes } from 'react-router-dom'
import { Result } from 'antd'
import AppLayout from './components/AppLayout'
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import ProjectsPage from './pages/ProjectsPage'
import SessionPage from './pages/session/SessionPage'
import NewSessionPage from './pages/session/NewSessionPage'
import KnowledgePage from './pages/kb/KnowledgePage'
import AdminPage from './pages/AdminPage'
import { useAuthStore } from './stores/authStore'

/** 角色守卫（审查 P1-5）：非 admin 访问受控页显示 403，而非放行或静默重定向。 */
function Forbidden() {
  return (
    <Result
      status="403"
      title="无权限访问"
      subTitle="该页面仅管理员可见。如需系统管理权限，请联系管理员调整角色。"
    />
  )
}

/** 阶段 4 路由。 */
export default function App() {
  const token = useAuthStore((s) => s.token)
  const roles = useAuthStore((s) => s.roles)
  const isAdmin = roles.includes('admin')
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={token ? <AppLayout /> : <Navigate to="/login" replace />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/sessions/new" element={<NewSessionPage />} />
        <Route path="/sessions/:id" element={<SessionPage />} />
        <Route path="/kb" element={<KnowledgePage />} />
        <Route path="/admin" element={isAdmin ? <AdminPage /> : <Forbidden />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
