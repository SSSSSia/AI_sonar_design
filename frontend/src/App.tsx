import { Suspense, lazy } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { Result, Spin } from 'antd'
import AppLayout from './components/AppLayout'
import { useAuthStore } from './stores/authStore'

// T12.6（P2-8）路由级分割：three/d3 等重库随页面懒加载，主包只留框架与登录。
const LoginPage = lazy(() => import('./pages/LoginPage'))
const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const ProjectsPage = lazy(() => import('./pages/ProjectsPage'))
const SessionPage = lazy(() => import('./pages/session/SessionPage'))
const NewSessionPage = lazy(() => import('./pages/session/NewSessionPage'))
const KnowledgePage = lazy(() => import('./pages/kb/KnowledgePage'))
const AdminPage = lazy(() => import('./pages/AdminPage'))

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

/** 懒加载过渡态（T12.6）：居中 Spin，与登录后布局背景一致。 */
function PageFallback() {
  return (
    <div style={{ minHeight: '60vh', display: 'grid', placeItems: 'center' }}>
      <Spin size="large" />
    </div>
  )
}

/** 阶段 4 路由。 */
export default function App() {
  const token = useAuthStore((s) => s.token)
  const roles = useAuthStore((s) => s.roles)
  const isAdmin = roles.includes('admin')
  return (
    <Suspense fallback={<PageFallback />}>
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
    </Suspense>
  )
}
