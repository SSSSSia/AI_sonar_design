import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { App, Button, Drawer, Form, Input, Select } from 'antd'
import { listProjects, createSession, fireEvent } from '../api/sessions'
import { listBases } from '../api/kb'
import { errText } from '../api/client'
import type { Project } from '../api/types'
import { NewProjectModal } from './ProjectModals'

/**
 * 新建设计会话抽屉（2026-09-20 工作台流程重塑）：仪表盘 / 项目卡片原地弹出，
 * 不再跳独立页面——创建动作留在用户当前上下文里。
 * presetProjectId 用于项目卡片「新建会话」与仪表盘项目筛选态的预选。
 * PHASE-006：知识库多选（不选 = Agent 根据需求自主圈定相关知识库）。
 */
export function NewSessionDrawer({ open, onClose, presetProjectId }: {
  open: boolean
  onClose: () => void
  presetProjectId?: string
}) {
  const navigate = useNavigate()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [form] = Form.useForm<{ projectId: string; title: string; requirement: string; kbIds?: string[] }>()
  const [submitting, setSubmitting] = useState(false)
  const [projectModal, setProjectModal] = useState(false)

  const projectsQuery = useQuery({
    queryKey: ['projects'],
    queryFn: () => listProjects({ page: 1, size: 100 }),
    enabled: open,
  })
  const projects: Project[] = projectsQuery.data?.records ?? []

  const basesQuery = useQuery({
    queryKey: ['kb-bases'],
    queryFn: listBases,
    enabled: open,
  })
  const bases = basesQuery.data?.bases ?? []

  useEffect(() => {
    if (!open) return
    form.resetFields()
    if (presetProjectId && projects.some((p) => p.id === presetProjectId)) {
      form.setFieldValue('projectId', presetProjectId)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, presetProjectId])

  async function submit(values: { projectId: string; title: string; requirement: string; kbIds?: string[] }) {
    setSubmitting(true)
    try {
      const kbIds = values.kbIds?.length ? values.kbIds : undefined
      const session = await createSession({ ...values, kbIds })
      await fireEvent(session.id, 'START')
      message.success('会话已创建，A1 需求解析已启动')
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      void queryClient.invalidateQueries({ queryKey: ['projects'] })
      onClose()
      navigate(`/sessions/${session.id}`)
    } catch (e) {
      message.error(errText(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <Drawer
        title="新建设计会话"
        open={open}
        onClose={onClose}
        width={560}
        destroyOnClose
      >
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
          <Form.Item name="title" label="会话标题" rules={[{ required: true, message: '请输入标题' }]}>
            <Input placeholder="如：某型拖曳线列阵声呐方案设计" maxLength={80} showCount />
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
            extra="提交后原文固化为 IR0 不可变快照（A-12），作为全程溯源起点；A2 未给出的约束会标注来源，由人工裁决。"
          >
            <Input.TextArea rows={9} placeholder={'设计一套拖曳线列阵声呐，主要要求如下：\n1. 工作深度…'} />
          </Form.Item>
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Button style={{ marginRight: 8 }} onClick={onClose}>取消</Button>
            <Button type="primary" htmlType="submit" loading={submitting}>提交并启动分析</Button>
          </div>
        </Form>
      </Drawer>
      <NewProjectModal
        open={projectModal}
        onClose={() => setProjectModal(false)}
        onCreated={(p) => form.setFieldValue('projectId', p.id)}
      />
    </>
  )
}
