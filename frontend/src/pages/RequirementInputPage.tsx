import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Alert, App, Button, Card, Form, Input, Modal, Popconfirm, Select, Table, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { listProjects, createSession, createProject, deleteProject } from '../api/sessions'
import { errText } from '../api/client'
import type { Project } from '../api/types'
import { useAuthStore } from '../stores/authStore'

/** 需求输入页（T4.2）：提交即创建会话并固化为 IR0，自动启动 A1。 */
export default function RequirementInputPage() {
  const navigate = useNavigate()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [form] = Form.useForm<{ projectId: string; title: string; requirement: string }>()
  const [projectModal, setProjectModal] = useState(false)
  const [manageModal, setManageModal] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const isAdmin = useAuthStore((s) => s.roles.includes('admin'))

  const projectsQuery = useQuery({
    queryKey: ['projects'],
    queryFn: () => listProjects({ page: 1, size: 100 }),
  })
  const projects: Project[] = projectsQuery.data?.records ?? []

  async function onFinish(values: { projectId: string; title: string; requirement: string }) {
    setSubmitting(true)
    try {
      const session = await createSession(values)
      await createAndStart(session.id)
    } catch (e) {
      message.error(errText(e))
    } finally {
      setSubmitting(false)
    }

    async function createAndStart(sid: string) {
      const { fireEvent } = await import('../api/sessions')
      await fireEvent(sid, 'START')
      message.success('会话已创建，A1 需求解析已启动')
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      navigate(`/sessions/${sid}`, { replace: true })
    }
  }

  return (
    <div style={{ maxWidth: 980, margin: '0 auto' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,3fr) minmax(260px,2fr)', gap: 16 }}>
        <Card title="新建设计会话">
          <Form form={form} layout="vertical" onFinish={onFinish}>
            <Form.Item name="projectId" label="所属项目" rules={[{ required: true, message: '请选择项目' }]}>
              <Select
                placeholder="选择项目"
                options={projects.map((p) => ({ value: p.id, label: `${p.name}（${p.code ?? p.id.slice(0, 6)}）` }))}
                dropdownRender={(menu) => (
                  <>
                    {menu}
                    <Button type="link" size="small" style={{ width: '100%' }} onClick={() => setProjectModal(true)}>
                      ＋ 新建项目
                    </Button>
                    {isAdmin && (
                      <Button type="link" size="small" style={{ width: '100%' }} onClick={() => setManageModal(true)}>
                        管理项目…
                      </Button>
                    )}
                  </>
                )}
              />
            </Form.Item>
            <Form.Item name="title" label="会话标题" rules={[{ required: true, message: '请输入标题' }]}>
              <Input placeholder="如：某型拖曳线列阵声呐方案设计" maxLength={80} showCount />
            </Form.Item>
            <Form.Item
              name="requirement"
              label="设计需求（自然语言）"
              rules={[{ required: true, message: '请输入设计需求' }]}
              extra="提交后原文固化为 IR0 不可变快照（A-12），作为全程溯源起点。"
            >
              <Input.TextArea rows={9} placeholder={'设计一套拖曳线列阵声呐，主要要求如下：\n1. 工作深度…'} />
            </Form.Item>
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <Button type="primary" htmlType="submit" loading={submitting}>提交并启动分析</Button>
            </div>
          </Form>
        </Card>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card title="提交后会发生什么" size="small">
            <div style={{ fontSize: 12.5, color: '#57606a', lineHeight: 2 }}>
              <div>① 创建会话，需求固化为 <b>IR0</b> 快照</div>
              <div>② 自动投递 A1 需求解析任务</div>
              <div>③ 跳转工作台，实时查看进度</div>
              <div>④ A2 完成后在<b>审阅关①</b>暂停，等待裁决</div>
            </div>
          </Card>
          <Card title="能力边界（如实）" size="small">
            <div style={{ fontSize: 12, color: '#57606a', display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div style={{ borderLeft: '3px solid #2563eb', paddingLeft: 10 }}>A1 只做目标结构化与检索，缺失项如实列出。</div>
              <div style={{ borderLeft: '3px solid #d97706', paddingLeft: 10 }}>需求未给出的约束由 A2 提议并标注来源，人工裁决。</div>
            </div>
          </Card>
        </div>
      </div>

      <NewProjectModal
        open={projectModal}
        onClose={() => setProjectModal(false)}
        onCreated={(p) => {
          form.setFieldValue('projectId', p.id)
          void queryClient.invalidateQueries({ queryKey: ['projects'] })
        }}
      />
      <ManageProjectsModal open={manageModal} onClose={() => setManageModal(false)} />
    </div>
  )
}

export function NewProjectModal({ open, onClose, onCreated }: {
  open: boolean
  onClose: () => void
  onCreated: (p: Project) => void
}) {
  const { message } = App.useApp()
  const [form] = Form.useForm<{ name: string; code?: string; description?: string }>()
  const [saving, setSaving] = useState(false)

  async function save() {
    const values = await form.validateFields()
    setSaving(true)
    try {
      const p = await createProject(values)
      message.success(`项目「${p.name}」已创建`)
      form.resetFields()
      onCreated(p)
      onClose()
    } catch (e) {
      message.error(e instanceof Error ? e.message : '创建失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal title="新建项目" open={open} onCancel={onClose} onOk={save} confirmLoading={saving} okText="创建" destroyOnClose>
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="项目名称" rules={[{ required: true, message: '请输入名称' }]}>
          <Input placeholder="如：拖曳线列阵声呐预研" maxLength={60} />
        </Form.Item>
        <Form.Item name="code" label="项目代号">
          <Input placeholder="如：TLA-2026" maxLength={30} />
        </Form.Item>
        <Form.Item name="description" label="说明">
          <Input.TextArea rows={2} maxLength={200} />
        </Form.Item>
      </Form>
    </Modal>
  )
}

/** 项目管理（admin）：列出全部项目及会话数，仅允许删除无会话的空项目。 */
function ManageProjectsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const q = useQuery({
    queryKey: ['projects'],
    queryFn: () => listProjects({ page: 1, size: 100 }),
    enabled: open,
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
        <div>
          <div style={{ fontWeight: 500 }}>{p.name}</div>
          <div style={{ fontSize: 11, color: '#8b949e' }}>{p.code ?? p.id.slice(0, 8)}</div>
        </div>
      ),
    },
    { title: '会话数', dataIndex: 'session_count', width: 80, align: 'center', render: (v: number | undefined) => v ?? 0 },
    {
      title: '', key: 'a', width: 84,
      render: (_, p) => {
        const empty = (p.session_count ?? 0) === 0
        return (
          <Tooltip title={empty ? '' : '项目下仍有会话，请先在仪表盘删除这些会话'}>
            <Popconfirm title={`确认删除空项目「${p.name}」？`} onConfirm={() => remove(p)} disabled={!empty}>
              <Button size="small" danger disabled={!empty}>删除</Button>
            </Popconfirm>
          </Tooltip>
        )
      },
    },
  ]

  return (
    <Modal title="管理项目" open={open} onCancel={onClose} footer={null} width={560} destroyOnClose>
      <Alert
        type="info" showIcon style={{ marginBottom: 12 }}
        message="仅能删除「无会话」的空项目；仍有会话的项目请先在仪表盘删除其会话。删除项目不影响已沉淀入库的知识。"
      />
      <Table rowKey="id" size="small" pagination={false} loading={q.isLoading} dataSource={rows} columns={columns} />
    </Modal>
  )
}
