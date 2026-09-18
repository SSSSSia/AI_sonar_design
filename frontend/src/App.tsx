import { Navigate, Route, Routes } from 'react-router-dom'
import AppLayout from './components/AppLayout'
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import { SessionStubPage, StubPage } from './pages/StubPage'
import { useAuthStore } from './stores/authStore'

/** 骨架路由（阶段 4）。页面逐任务交付：T4.1 仪表盘 → T4.2 审阅 → T4.3 三维 → T4.4 知识库。 */
export default function App() {
  const token = useAuthStore((s) => s.token)
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={token ? <AppLayout /> : <Navigate to="/login" replace />}>
        <Route path="/" element={<DashboardPage />} />
        <Route
          path="/sessions/new"
          element={<StubPage title="需求输入" phase="T4.2" note="创建会话即提交 IR0（POST /workflow/sessions）" />}
        />
        <Route path="/sessions/:id" element={<SessionStubPage />} />
        <Route path="/kb" element={<StubPage title="知识库管理" phase="T4.4" note="后端 /api/v1/kb 全套 API 已就绪" />} />
        <Route path="/admin" element={<StubPage title="系统管理" phase="T4.4" />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
