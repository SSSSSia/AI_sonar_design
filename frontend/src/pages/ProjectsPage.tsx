import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { App, Button, Checkbox, Dropdown, Pagination, Tooltip } from 'antd'
import { MoreOutlined, PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { deleteProject, deleteSessions, listProjects, listSessions } from '../api/sessions'
import { errText } from '../api/client'
import type { Project } from '../api/types'
import { QueryEmpty } from '../components/QueryEmpty'
import { EditProjectModal, NewProjectModal } from '../components/ProjectModals'
import { MemberManageModal } from '../components/MemberModal'
import { SonarMark } from '../components/AppLayout'
import { hasAnyRole, useAuthStore } from '../stores/authStore'

dayjs.extend(relativeTime)

/**
 * 项目管理页（2026-09-20 视觉重塑；2026-10-10 深色科技风改版 ADR-011）：
 * 声呐环形卡片网格，不再是通用 CRUD 表格。
 * 卡片信息层级：品牌环 + 名称/代号 → 说明 → 会话数 + 五态统计格 → 完成度发光进度条
 * → 最近活动 → 操作行（查看会话 / 新建会话 / 更多）。
 * 视觉约束（宪法 §7.3 + ADR-011）：交互青仅用于可操作元素；状态色沿用语义四色，
 * 光晕限定于运行中状态点与完成度进度条。
 */

const STATUS_DOT: Record<string, { color: string; label: string }> = {
  running: { color: '#06b6d4', label: '运行中' },
  paused: { color: '#f59e0b', label: '待审阅' },
  completed: { color: '#10b981', label: '已完成' },
  failed: { color: '#ef4444', label: '失败' },
  pending: { color: '#64748b', label: '待启动' },
}

/** 完成度发光进度条（ADR-011 装饰限定）：completed/total 派生，无会话时不渲染 */
function DoneProgress({ total, done }: { total: number; done: number }) {
  if (total === 0) return null
  const pct = Math.round((done / total) * 100)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div
        style={{
          flex: 1, height: 5, borderRadius: 3, background: 'rgba(255,255,255,.08)', overflow: 'hidden',
        }}
        role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="完成度"
      >
        <div
          style={{
            width: `${pct}%`, height: '100%', borderRadius: 3,
            background: 'linear-gradient(135deg, #0ea5e9, #06b6d4)',
            boxShadow: '0 0 8px rgba(6,182,212,.5)',
          }}
        />
      </div>
      <span style={{ fontSize: 11, color: 'var(--text-3)', fontVariantNumeric: 'tabular-nums', flex: 'none' }}>
        完成 {pct}%
      </span>
    </div>
  )
}

function ProjectCard({ p, canManage, isAdmin, onView, onNewSession, onEdit, onMembers, onDelete, selectable, selected, onToggleSelect }: {
  p: Project
  canManage: boolean
  isAdmin: boolean
  onView: () => void
  onNewSession: () => void
  onEdit: () => void
  onMembers: () => void
  onDelete: () => void
  /** 批量管理模式（U3.13）：显示选择框 */
  selectable?: boolean
  selected?: boolean
  onToggleSelect?: () => void
}) {
  const stats = p.session_stats ?? {}
  const total = p.session_count ?? 0
  const empty = total === 0

  return (
    <div
      style={{
        border: `1px solid ${selected ? 'rgba(6,182,212,.55)' : 'var(--border)'}`, borderRadius: 8, padding: '16px 18px 14px',
        display: 'flex', flexDirection: 'column', gap: 10, minWidth: 0,
        background: selected ? 'var(--accent-bg)' : 'var(--card-grad)', transition: 'border-color .18s',
        position: 'relative',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'rgba(6,182,212,.45)' }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = selected ? 'rgba(6,182,212,.55)' : 'var(--border)' }}
    >
      {selectable && (
        <div style={{ position: 'absolute', top: 10, right: 10, zIndex: 2 }} onClick={(e) => e.stopPropagation()}>
          <Checkbox checked={selected} onChange={() => onToggleSelect?.()} aria-label={`选择项目 ${p.name}`} />
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <SonarMark size={22} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {p.name}
          </div>
        </div>
        <span style={{ fontSize: 11.5, color: 'var(--text-3)', fontFamily: 'monospace', flex: 'none' }}>
          {p.code ?? p.id.slice(0, 8)}
        </span>
      </div>

      {p.description && (
        <div style={{ fontSize: 12.5, color: 'var(--text-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={p.description}>
          {p.description}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, padding: '2px 0' }}>
        <span style={{ fontSize: 22, fontWeight: 600, color: 'var(--text-1)', lineHeight: 1, fontVariantNumeric: 'tabular-nums' }}>{total}</span>
        <span style={{ fontSize: 12, color: 'var(--text-3)' }}>个会话</span>
      </div>

      {/* 五态统计格（session_stats 真实数据）：>0 高亮状态色，=0 弱化 */}
      {!empty && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 4 }}>
          {(['running', 'paused', 'completed', 'failed', 'pending'] as const).map((k) => {
            const n = stats[k] ?? 0
            const on = n > 0
            return (
              <div
                key={k}
                style={{
                  textAlign: 'center', padding: '6px 0 5px', borderRadius: 6,
                  background: on ? 'rgba(255,255,255,.04)' : 'transparent',
                  border: `1px solid ${on ? 'var(--border)' : 'transparent'}`,
                }}
              >
                <div
                  style={{
                    fontSize: 14, fontWeight: 600, lineHeight: 1.2, fontVariantNumeric: 'tabular-nums',
                    color: on ? STATUS_DOT[k].color : 'var(--text-4)',
                  }}
                >
                  {n}
                </div>
                <div style={{ fontSize: 10.5, color: 'var(--text-3)', marginTop: 1 }}>{STATUS_DOT[k].label}</div>
              </div>
            )
          })}
        </div>
      )}
      {empty && (
        <span style={{ fontSize: 12, color: 'var(--text-3)' }}>暂无会话——从本卡片「新建会话」开始</span>
      )}

      <DoneProgress total={total} done={stats.completed ?? 0} />

      <div style={{ display: 'flex', alignItems: 'center', borderTop: '1px solid var(--hairline)', paddingTop: 10, marginTop: 'auto' }}>
        <span style={{ fontSize: 11.5, color: 'var(--text-3)' }}>
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
                { key: 'members', label: '成员管理', onClick: onMembers },
                ...(isAdmin ? [{ key: 'del', label: '删除项目', danger: true, onClick: onDelete }] : []),
              ],
            }}
          >
            <Tooltip title={isAdmin && !empty ? '项目下仍有会话——确认框可选择连同会话一并清理' : ''}>
              <Button type="text" size="small" icon={<MoreOutlined />} style={{ color: 'var(--text-2)' }} />
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
  const [memberProject, setMemberProject] = useState<Project | null>(null)

  const [page, setPage] = useState(1)
  const PAGE_SIZE = 100
  const q = useQuery({
    queryKey: ['projects', page],
    queryFn: () => listProjects({ page, size: PAGE_SIZE }),
    refetchInterval: 30_000,
  })
  const rows: Project[] = q.data?.records ?? []
  const totalProjects = q.data?.total ?? rows.length

  // ---- U3.13 批量删除（admin）：项目 + 其全部会话级联清理（前端编排既有 API） ----
  const [batchMode, setBatchMode] = useState(false)
  const [picked, setPicked] = useState<Map<string, Project>>(new Map())
  const [purging, setPurging] = useState(false)

  function togglePick(p: Project) {
    setPicked((prev) => {
      const next = new Map(prev)
      if (next.has(p.id)) next.delete(p.id)
      else next.set(p.id, p)
      return next
    })
  }

  /** 跨页全选（U3.13）：逐页拉全量置入选择（供一次性级联清理全部残留项目）。 */
  async function selectAllProjects() {
    const all: Project[] = []
    for (let pg = 1; pg <= 50; pg++) {
      const r = await listProjects({ page: pg, size: 500 })
      all.push(...r.records)
      if (all.length >= (r.total ?? 0) || r.records.length === 0) break
    }
    setPicked(new Map(all.map((x) => [x.id, x])))
    message.success(`已全选 ${all.length} 个项目（跨页）`)
  }

  /** 级联清理单项目：分页取全其会话 → 批量删（force）→ 删项目。 */
  async function purgeProject(p: Project): Promise<number> {
    const ids: string[] = []
    for (let page = 1; page <= 20; page++) {
      const r = await listSessions({ projectId: p.id, page, size: 500 })
      ids.push(...r.records.map((s) => s.id))
      if (ids.length >= (r.total ?? 0) || r.records.length === 0) break
    }
    if (ids.length > 0) await deleteSessions(ids, true)
    await deleteProject(p.id)
    return ids.length
  }

  async function runPurge(list: Project[]) {
    if (list.length === 0) return
    setPurging(true)
    let okProjects = 0
    let okSessions = 0
    const failed: string[] = []
    for (let i = 0; i < list.length; i++) {
      message.open({ key: 'purge', type: 'loading', content: `批量清理中 ${i + 1}/${list.length}：${list[i].name}` })
      try {
        okSessions += await purgeProject(list[i])
        okProjects++
      } catch (e) {
        failed.push(`${list[i].name}：${errText(e, 60)}`)
      }
    }
    const summary = `已删除 ${okProjects} 个项目（含 ${okSessions} 个会话）${failed.length > 0 ? ` · 失败 ${failed.length} 个（${failed[0]}）` : ''}`
    message.open({ key: 'purge', type: failed.length ? 'warning' : 'success', content: summary, duration: 8 })
    void queryClient.invalidateQueries({ queryKey: ['projects'] })
    void queryClient.invalidateQueries({ queryKey: ['sessions'] })
    setPurging(false)
    setBatchMode(false)
    setPicked(new Map())
  }

  function confirmPurge(list: Project[]) {
    const totalSessions = list.reduce((acc, p) => acc + (p.session_count ?? 0), 0)
    modal.confirm({
      title: `删除所选 ${list.length} 个项目${totalSessions > 0 ? `（连同其 ${totalSessions} 个会话）` : ''}？`,
      content: '删除不可恢复：项目、会话及其快照/裁决/溯源/产物文件一并清除；已沉淀入库的知识保留。运行中/待审阅会话将被强制终止删除。',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '再想想',
      onOk: () => runPurge(list),
    })
  }

  function confirmDelete(p: Project) {
    const sessions = p.session_count ?? 0
    if (sessions === 0) {
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
      return
    }
    // U3.13：非空项目走级联清理（既有 API 编排；确认框交代全部后果）
    confirmPurge([p])
  }

  return (
    <div style={{ maxWidth: 1120, margin: '0 auto' }}>
      <div className="row" style={{ marginBottom: 18, flexWrap: 'wrap' }}>
        <div>
          <div className="page-head">项目管理</div>
          <div style={{ fontSize: 12.5, color: 'var(--text-3)', marginTop: 2 }}>
            {totalProjects > 0 ? `${totalProjects} 个项目 · 会话在仪表盘跟进，此处进入与发起` : '项目是会话的容器——按型号/课题建项目，会话在项目下迭代'}
          </div>
        </div>
        <span style={{ flex: 1 }} />
        {isAdmin && !batchMode && rows.length > 0 && (
          <Button
            danger
            onClick={() => { setBatchMode(true); setPicked(new Map()) }}
          >
            批量删除
          </Button>
        )}
        {batchMode && (
          <>
            <span style={{ fontSize: 12.5, color: 'var(--text-2)' }}>已选 {picked.size} 项</span>
            <Button size="small" onClick={() => void selectAllProjects()}>全选全部（{totalProjects}）</Button>
            <Button danger disabled={picked.size === 0} onClick={() => confirmPurge([...picked.values()])}>
              删除所选（含会话）
            </Button>
            <Button disabled={purging} onClick={() => { setBatchMode(false); setPicked(new Map()) }}>退出批量</Button>
          </>
        )}
        {canManage && !batchMode && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>新建项目</Button>
        )}
      </div>

      {(q.isError || q.isLoading || rows.length === 0) ? (
        <div style={{ border: '1px solid var(--border)', borderRadius: 8, background: 'var(--card)' }}>
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
              selectable={batchMode}
              selected={picked.has(r.id)}
              onToggleSelect={() => togglePick(r)}
              onView={() => navigate(`/?project=${r.id}`)}
              onNewSession={() => navigate(`/sessions/new?project=${r.id}`)}
              onEdit={() => setEditing(r)}
              onMembers={() => setMemberProject(r)}
              onDelete={() => confirmDelete(r)}
            />
          ))}
          {canManage && !batchMode && (
            <button
              onClick={() => setCreating(true)}
              style={{
                border: '1px dashed var(--border-strong)', borderRadius: 8, background: 'transparent',
                minHeight: 120, cursor: 'pointer', color: 'var(--text-2)', fontSize: 13,
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                transition: 'border-color .18s',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = '#06b6d4'; e.currentTarget.style.color = '#06b6d4' }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border-strong)'; e.currentTarget.style.color = 'var(--text-2)' }}
            >
              <PlusOutlined /> 新建项目
            </button>
          )}
        </div>
      )}

      {totalProjects > PAGE_SIZE && (
        <div style={{ display: 'flex', justifyContent: 'center', marginTop: 18 }}>
          <Pagination
            current={page}
            pageSize={PAGE_SIZE}
            total={totalProjects}
            showSizeChanger={false}
            onChange={(pg) => { setPage(pg); window.scrollTo({ top: 0 }) }}
          />
        </div>
      )}

      <NewProjectModal open={creating} onClose={() => setCreating(false)} onCreated={() => void queryClient.invalidateQueries({ queryKey: ['projects'] })} />
      <EditProjectModal open={!!editing} project={editing} onClose={() => setEditing(null)} />
      <MemberManageModal
        projectId={memberProject?.id ?? ''} projectName={memberProject?.name ?? ''}
        open={!!memberProject} onClose={() => setMemberProject(null)}
      />    </div>
  )
}
