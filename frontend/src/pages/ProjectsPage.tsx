import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { App, Button, Card, Popconfirm, Space, Table, Tooltip, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { deleteProject, listProjects } from '../api/sessions'
import { errText } from '../api/client'
import type { Project } from '../api/types'
import { QueryEmpty } from '../components/QueryEmpty'
import { EditProjectModal, NewProjectModal } from '../components/ProjectModals'
import { hasAnyRole, useAuthStore } from '../stores/authStore'

/**
 * 项目管理页（2026-09-20 结构优化方案 A）：项目是一级实体，不再藏于需求输入页下拉。
 * 会话仍在仪表盘管理——「查看会话」带 ?project= 跳转过滤；删除保持「仅空项目可删」。
 */
export default function ProjectsPage() {
  const navigate = useNavigate()
  const { message } = App.useApp()
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

  async function remove(p: Project) {
    try {
      await deleteProject(p.id)
      message.success(`已删除项目「${p.name}」`)
      void queryClient.invalidateQueries({ queryKey: ['projects'] })
    } catch (e) {
      message.error(errText(e))
    }
  }

  const columns: ColumnsType<Project> = [
    {
      title: '项目', key: 'n',
      render: (_, p) => (
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 500 }}>{p.name}</div>
          <div style={{ fontSize: 11, color: '#8b949e', overflowWrap: 'anywhere' }}>
            {p.code ?? p.id.slice(0, 8)}{p.description ? ` · ${p.description}` : ''}
          </div>
        </div>
      ),
    },
    {
      title: '会话数', dataIndex: 'session_count', width: 84, align: 'center',
      render: (v: number | undefined) =>
        (v ?? 0) > 0 ? <Typography.Text>{v ?? 0}</Typography.Text> : <span style={{ color: '#8b949e' }}>0</span>,
    },
    {
      title: '创建时间', dataIndex: 'createdAt', width: 110,
      render: (v: string) => <span style={{ fontSize: 11.5, color: '#8b949e' }}>{v ? dayjs(v).format('MM-DD HH:mm') : '—'}</span>,
    },
    {
      title: '操作', key: 'a', width: 250,
      render: (_, p) => {
        const empty = (p.session_count ?? 0) === 0
        return (
          <Space size={4}>
            <Button size="small" autoInsertSpace={false} onClick={() => navigate(`/?project=${p.id}`)}>查看会话</Button>
            <Button size="small" autoInsertSpace={false} onClick={() => navigate(`/sessions/new?project=${p.id}`)}>新建会话</Button>
            {canManage && (
              <Button size="small" autoInsertSpace={false} onClick={() => setEditing(p)}>编辑</Button>
            )}
            {isAdmin && (
              <Tooltip title={empty ? '' : '项目下仍有会话——先「查看会话」到仪表盘删除，项目清空后可删'}>
                <Popconfirm title={`确认删除空项目「${p.name}」？`} onConfirm={() => remove(p)} disabled={!empty}>
                  <Button size="small" danger disabled={!empty}>删除</Button>
                </Popconfirm>
              </Tooltip>
            )}
          </Space>
        )
      },
    },
  ]

  return (
    <Card
      title="项目管理"
      extra={canManage && (
        <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setCreating(true)}>新建项目</Button>
      )}
      styles={{ body: { padding: 0 } }}
    >
      <Space direction="vertical" style={{ width: '100%' }} size={0}>
        {(q.isError || q.isLoading || rows.length === 0) && (
          <QueryEmpty
            query={q}
            subject="项目列表"
            emptyText="还没有项目——点击右上角「新建项目」，或在需求输入页快速创建"
          />
        )}
        {rows.length > 0 && (
          <Table
            rowKey="id" size="middle" columns={columns} dataSource={rows}
            pagination={rows.length > 12 ? { pageSize: 12, hideOnSinglePage: true } : false}
          />
        )}
      </Space>
      <NewProjectModal open={creating} onClose={() => setCreating(false)} onCreated={() => void queryClient.invalidateQueries({ queryKey: ['projects'] })} />
      <EditProjectModal open={!!editing} project={editing} onClose={() => setEditing(null)} />
    </Card>
  )
}
