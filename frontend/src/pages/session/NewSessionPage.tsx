import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { App, Button, Card, Form, Input, Select, Typography } from 'antd'
import { listProjects, createSession, fireEvent } from '../../api/sessions'
import { listBases } from '../../api/kb'
import { errText } from '../../api/client'
import type { Project } from '../../api/types'
import { PageHeader } from '../../components/PageHeader'
import { NewProjectModal } from '../../components/ProjectModals'

/**
 * 新建设计会话独立页（2026-09-24 用户反馈：侧边抽屉不够正式；替代原 NewSessionDrawer）。
 * 预选项目走 /sessions/new?project=<id>（项目卡片入口）。标题限 20 字（前后端同校验）。
 */
export default function NewSessionPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [form] = Form.useForm<{ projectId: string; title: string; requirement: string; kbIds?: string[] }>()
  const [submitting, setSubmitting] = useState(false)
  const [projectModal, setProjectModal] = useState(false)
  const presetProjectId = params.get('project') ?? undefined

  const projectsQuery = useQuery({
    queryKey: ['projects'],
    queryFn: () => listProjects({ page: 1, size: 100 }),
  })
  const projects: Project[] = projectsQuery.data?.records ?? []

  const basesQuery = useQuery({
    queryKey: ['kb-bases'],
    queryFn: listBases,
  })
  const bases = basesQuery.data?.bases ?? []

  useEffect(() => {
    form.resetFields()
    if (presetProjectId && projects.some((p) => p.id === presetProjectId)) {
      form.setFieldValue('projectId', presetProjectId)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetProjectId, projects.length])

  async function submit(values: { projectId: string; title: string; requirement: string; kbIds?: string[] }) {
    setSubmitting(true)
    try {
      const kbIds = values.kbIds?.length ? values.kbIds : undefined
      const session = await createSession({ ...values, kbIds })
      await fireEvent(session.id, 'START')
      message.success('会话已创建，A1 需求解析已启动')
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      void queryClient.invalidateQueries({ queryKey: ['projects'] })
      navigate(`/sessions/${session.id}`)
    } catch (e) {
      message.error(errText(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div style={{ maxWidth: 860, margin: '0 auto' }}>
      <PageHeader title="新建设计会话" backTo="/" />
      <Card>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 0 }}>
          提交后需求原文固化为 IR0 不可变快照（A-04），作为全程溯源起点；流程从 A1 需求解析开始。
        </Typography.Paragraph>
        <Form form={form} layout="vertical" onFinish={submit}>
          <Form.Item name="projectId" label="所属项目" rules={[{ required: true, message: '请选择项目' }]}>
            <Select
              placeholder="选择项目"
              options={projects.map((p) => ({ value: p.id, label: `${p.name}（${p.code ?? p.id.slice(0, 6)}）` }))}
              notFoundContent={projectsQuery.isLoading ? '项目加载中…' : '暂无项目——点击下方「＋ 新建项目」创建'}
              dropdownRender={(menu) => (
                <>
                  {menu}
                  <Button type="link" size="small" style={{ width: '100%' }} onClick={() => setProjectModal(true)}>
                    ＋ 新建项目
                  </Button>
                </>
              )}
            />
          </Form.Item>
          <Form.Item name="title" label="会话标题" rules={[{ required: true, message: '请输入标题' }]}
            extra="不超过 20 字，用于会话列表与总览展示">
            <Input placeholder="如：某型拖曳线列阵声呐方案设计" maxLength={20} showCount />
          </Form.Item>
          <Form.Item
            name="kbIds"
            label="知识库（可多选）"
            extra={bases.length === 0 ? undefined : '不选则由 Agent 根据需求自动圈定相关知识库，选择依据会记录在会话中'}
          >
            <Select
              mode="multiple"
              allowClear
              maxTagCount="responsive"
              placeholder="不选则由 Agent 自动选定相关知识库"
              loading={basesQuery.isLoading}
              options={bases.map((b) => ({ value: b.id, label: b.is_default ? `${b.name}（默认）` : b.name }))}
            />
          </Form.Item>
          <Form.Item
            name="requirement"
            label="设计需求（自然语言）"
            rules={[{ required: true, message: '请输入设计需求' }]}
            extra="A2 未给出的约束会标注来源，由人工裁决。"
          >
            <Input.TextArea rows={10} placeholder={'设计一套拖曳线列阵声呐，主要要求如下：\n1. 工作深度…'} />
          </Form.Item>
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Button style={{ marginRight: 8 }} onClick={() => navigate('/')}>取消</Button>
            <Button type="primary" htmlType="submit" loading={submitting}>提交并启动分析</Button>
          </div>
        </Form>
      </Card>
      <NewProjectModal
        open={projectModal}
        onClose={() => setProjectModal(false)}
        onCreated={(p) => form.setFieldValue('projectId', p.id)}
      />
    </div>
  )
}
