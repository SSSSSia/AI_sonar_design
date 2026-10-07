import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Button, Input, Modal, Popconfirm, Space, Table, Tag, Typography } from 'antd'
import { addProjectMember, listProjectMembers, removeProjectMember, type ProjectMember } from '../api/sessions'
import { errText } from '../api/client'

/**
 * 项目成员管理弹窗（T13.4，§9.4 项目级粒度）：owner/admin 增删成员；
 * 添加按用户名解析（role 固定 member，owner 由 projects.owner_id 承担，不可移除）。
 */
export function MemberManageModal({ projectId, projectName, open, onClose }: {
  projectId: string
  projectName: string
  open: boolean
  onClose: () => void
}) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [username, setUsername] = useState('')
  const [adding, setAdding] = useState(false)

  const q = useQuery({
    queryKey: ['project-members', projectId],
    queryFn: () => listProjectMembers(projectId),
    enabled: open,
  })
  const rows: ProjectMember[] = q.data ?? []

  const add = async () => {
    if (!username.trim()) return
    setAdding(true)
    try {
      await addProjectMember(projectId, username.trim())
      message.success(`已添加成员「${username.trim()}」`)
      setUsername('')
      await queryClient.invalidateQueries({ queryKey: ['project-members', projectId] })
    } catch (e) {
      message.error(errText(e))
    } finally {
      setAdding(false)
    }
  }

  const remove = async (m: ProjectMember) => {
    try {
      await removeProjectMember(projectId, m.user_id)
      message.success('已移除成员')
      await queryClient.invalidateQueries({ queryKey: ['project-members', projectId] })
    } catch (e) {
      message.error(errText(e))
    }
  }

  return (
    <Modal
      title={`成员管理：${projectName}`} open={open} onCancel={onClose} footer={null} width={560}
    >
      <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
        <Input
          placeholder="按用户名添加成员（如 zhang_san）" value={username}
          onChange={(e) => setUsername(e.target.value)}
          onPressEnter={() => void add()}
        />
        <Button type="primary" loading={adding} onClick={() => void add()}>添加</Button>
      </Space.Compact>
      <Table
        rowKey="user_id" size="small" loading={q.isLoading} dataSource={rows} pagination={false}
        columns={[
          { title: '用户名', dataIndex: 'username', width: 140, render: (v: string | null) => v || '—' },
          { title: '显示名', dataIndex: 'display_name', width: 140, render: (v: string | null) => v || '—' },
          {
            title: '角色', dataIndex: 'role', width: 90,
            render: (r: string) => <Tag color={r === 'owner' ? 'gold' : 'blue'}>{r === 'owner' ? '负责人' : '成员'}</Tag>,
          },
          {
            title: '操作', key: 'ops', width: 80,
            render: (_: unknown, m: ProjectMember) => m.role === 'owner'
              ? <Typography.Text type="secondary" style={{ fontSize: 12 }}>随项目</Typography.Text>
              : (
                <Popconfirm title="移除该成员？其将不再可见本项目与会话。" okButtonProps={{ danger: true }}
                  onConfirm={() => void remove(m)}>
                  <Button type="link" size="small" danger>移除</Button>
                </Popconfirm>
              ),
          },
        ]}
      />
      <Typography.Paragraph type="secondary" style={{ fontSize: 11.5, marginTop: 12, marginBottom: 0 }}>
        可见性口径（§9.4 项目级）：项目负责人与成员可见本项目及其全部会话；非关联用户不可见。审阅裁决仍按角色（reviewer/admin）执行，不受成员关系约束。
      </Typography.Paragraph>
    </Modal>
  )
}
