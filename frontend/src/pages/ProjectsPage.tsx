import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { App, Button, Dropdown, Tooltip } from 'antd'
import { MoreOutlined, PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { deleteProject, listProjects } from '../api/sessions'
import { errText } from '../api/client'
import type { Project } from '../api/types'
import { QueryEmpty } from '../components/QueryEmpty'
import { EditProjectModal, NewProjectModal } from '../components/ProjectModals'
import { SonarMark } from '../components/AppLayout'
import { hasAnyRole, useAuthStore } from '../stores/authStore'

dayjs.extend(relativeTime)

/**
 * 项目管理页（2026-09-20 视觉重塑）：声呐环形卡片网格，不再是通用 CRUD 表格。
 * 卡片信息层级：品牌环 + 名称/代号 → 说明 → 会话分布（延续仪表盘状态色语义）
 * → 最近活动 → 操作行（查看会话 / 新建会话 / 更多）。
 * 视觉约束（宪法）：唯一强调色 = 交互蓝；状态点沿用仪表盘徽标语义色，装饰一律石墨。
 */

const STATUS_DOT: Record<string, { color: string; label: string }> = {
  running: { color: '#2563eb', label: '运行中' },
  paused: { color: '#d97706', label: '待审阅' },
  completed: { color: '#52c41a', label: '已完成' },
  failed: { color: '#c92a2a', label: '失败' },
  pending: { color: '#8b949e', label: '待启动' },
}

function ProjectCard({ p, canManage, isAdmin, onView, onNewSession, onEdit, onDelete }: {
  p: Project
  canManage: boolean
  isAdmin: boolean
  onView: () => void
  onNewSession: () => void
  onEdit: () => void
  onDelete: () => void
}) {
  const stats = p.session_stats ?? {}
  const total = p.session_count ?? 0
  const dots = (['running', 'paused', 'completed', 'failed', 'pending'] as const)
    .map((k) => ({ key: k, n: stats[k] ?? 0 }))
    .filter((x) => x.n > 0)
  const empty = total === 0

  return (
    <div
      style={{
        border: '1px solid #e5e7eb', borderRadius: 8, padding: '16px 18px 14px',
        display: 'flex', flexDirection: 'column', gap: 10, minWidth: 0,
        background: '#fff', transition: 'border-color .18s',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = '#9aa4b2' }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = '#e5e7eb' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <SonarMark size={22} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: '#1f2329', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {p.name}
          </div>
        </div>
        <span style={{ fontSize: 11.5, color: '#8b949e', fontFamily: 'monospace', flex: 'none' }}>
          {p.code ?? p.id.slice(0, 8)}
        </span>
      </div>

      {p.description && (
        <div style={{ fontSize: 12.5, color: '#57606a', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={p.description}>
          {p.description}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, padding: '2px 0' }}>
        <span style={{ fontSize: 22, fontWeight: 600, color: '#1f2329', lineHeight: 1 }}>{total}</span>
        <span style={{ fontSize: 12, color: '#8b949e' }}>个会话</span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', minHeight: 18, alignItems: 'center' }}>
        {dots.length === 0
          ? <span style={{ fontSize: 12, color: '#8b949e' }}>暂无会话——从本卡片「新建会话」开始</span>
          : dots.map((d) => (
            <span key={d.key} style={{ fontSize: 12, color: '#57606a', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: STATUS_DOT[d.key].color, display: 'inline-block' }} />
              {STATUS_DOT[d.key].label} {d.n}
            </span>
          ))}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', borderTop: '1px solid #f0f1f3', paddingTop: 10, marginTop: 'auto' }}>
        <span style={{ fontSize: 11.5, color: '#8b949e' }}>
          {empty
            ? `创建于 ${dayjs(p.createdAt).fromNow()}`
            : `最近活动 ${dayjs(p.last_active_at ?? p.createdAt).fromNow()}`}
        </span>
        <span style={{ flex: 1 }} />
        <a onClick={onView} style={{ marginRight: 12 }}>查看会话</a>
        <a onClick={onNewSession} style={{ marginRight: 4 }}>新建会话</a>
        {canManage && (
          <Dropdown
            trigger={['click']}
            menu={{
              items: [
                { key: 'edit', label: '编辑项目', onClick: onEdit },
                ...(isAdmin ? [{ key: 'del', label: '删除项目', danger: true, disabled: !empty, onClick: onDelete }] : []),
              ],
            }}
          >
            <Tooltip title={isAdmin && !empty ? '项目下仍有会话——先「查看会话」到仪表盘删除，清空后可删' : ''}>
              <Button type="text" size="small" icon={<MoreOutlined />} style={{ color: '#57606a' }} />
            </Tooltip>
          </Dropdown>
        )}
      </div>
    </div>
  )
}

export default function ProjectsPage() {
  const navigate = useNavigate()
  const { message, modal } = App.useApp()
  const queryClient = useQueryClient()
  const roles = useAuthStore((s) => s.roles)
  const isAdmin = roles.includes('admin')
  const canManage = hasAnyRole(roles, ['admin', 'designer'])
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Project | null>(null)

  const q = useQuery({
    queryKey: ['projects'],
    queryFn: () => listProjects({ page: 1, size: 100 }),
    refetchInterval: 30_000,
  })
  const rows: Project[] = q.data?.records ?? []

  function confirmDelete(p: Project) {
    modal.confirm({
      title: `确认删除空项目「${p.name}」？`,
      content: '删除不可恢复；已沉淀入库的知识不受影响。',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '再想想',
      onOk: async () => {
        try {
          await deleteProject(p.id)
          message.success(`已删除项目「${p.name}」`)
          void queryClient.invalidateQueries({ queryKey: ['projects'] })
        } catch (e) {
          message.error(errText(e))
        }
      },
    })
  }

  return (
    <div style={{ maxWidth: 1120, margin: '0 auto' }}>
      <div className="row" style={{ marginBottom: 18, flexWrap: 'wrap' }}>
        <div>
          <div className="page-head">项目管理</div>
          <div style={{ fontSize: 12.5, color: '#8b949e', marginTop: 2 }}>
            {rows.length > 0 ? `${rows.length} 个项目 · 会话在仪表盘跟进，此处进入与发起` : '项目是会话的容器——按型号/课题建项目，会话在项目下迭代'}
          </div>
        </div>
        <span style={{ flex: 1 }} />
        {canManage && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>新建项目</Button>
        )}
      </div>

      {(q.isError || q.isLoading || rows.length === 0) ? (
        <div style={{ border: '1px solid #e5e7eb', borderRadius: 8, background: '#fff' }}>
          <QueryEmpty
            query={q}
            subject="项目列表"
            emptyText="还没有项目——点击右上角「新建项目」开始"
          />
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(310px, 1fr))', gap: 16 }}>
          {rows.map((r) => (
            <ProjectCard
              key={r.id}
              p={r}
              canManage={canManage}
              isAdmin={isAdmin}
              onView={() => navigate(`/?project=${r.id}`)}
              onNewSession={() => navigate(`/sessions/new?project=${r.id}`)}
              onEdit={() => setEditing(r)}
              onDelete={() => confirmDelete(r)}
            />
          ))}
          {canManage && (
            <button
              onClick={() => setCreating(true)}
              style={{
                border: '1px dashed #c9d1d9', borderRadius: 8, background: '#fafbfc',
                minHeight: 120, cursor: 'pointer', color: '#57606a', fontSize: 13,
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                transition: 'border-color .18s',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = '#2563eb'; e.currentTarget.style.color = '#2563eb' }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = '#c9d1d9'; e.currentTarget.style.color = '#57606a' }}
            >
              <PlusOutlined /> 新建项目
            </button>
          )}
        </div>
      )}

      <NewProjectModal open={creating} onClose={() => setCreating(false)} onCreated={() => void queryClient.invalidateQueries({ queryKey: ['projects'] })} />
      <EditProjectModal open={!!editing} project={editing} onClose={() => setEditing(null)} />    </div>
  )
}
