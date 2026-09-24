import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Alert, App, Button, Card, Form, Input, Modal, Select, Typography } from 'antd'
import dayjs from 'dayjs'
import { listProjects, createSession, fireEvent } from '../../api/sessions'
import { listBases } from '../../api/kb'
import { errText } from '../../api/client'
import type { Project } from '../../api/types'
import { PageHeader } from '../../components/PageHeader'
import { NewProjectModal } from '../../components/ProjectModals'
import { checkNavGuard, setNavGuard } from '../../lib/navGuard'
import { clearDraft, isDraftMeaningful, loadDraft, saveDraft, type SessionDraft } from '../../lib/sessionDraft'

interface FormValues {
  projectId: string
  title: string
  requirement: string
  kbIds?: string[]
}

/**
 * 新建设计会话独立页（2026-09-24 用户反馈：侧边抽屉不够正式；替代原 NewSessionDrawer）。
 * 预选项目走 /sessions/new?project=<id>（项目卡片入口）。标题限 20 字（前后端同校验）。
 * 草稿：打字即存 localStorage（防刷新/误退），提交成功即清；站内跳转时二次确认
 * （保存并离开/放弃/取消，2026-09-24 用户要求）。浏览器前进/后退拦不住——兜底是草稿已落盘。
 */
export default function NewSessionPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [form] = Form.useForm<FormValues>()
  const [submitting, setSubmitting] = useState(false)
  const [projectModal, setProjectModal] = useState(false)
  const [restored, setRestored] = useState<SessionDraft | null>(null)
  const [confirmNav, setConfirmNav] = useState<string | null>(null)
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
    const d = loadDraft()
    if (d) {
      form.setFieldsValue({ projectId: d.projectId, title: d.title, kbIds: d.kbIds, requirement: d.requirement })
      if (isDraftMeaningful(d)) setRestored(d)
    }
    // 显式入口的项目预选优先于草稿里的项目
    if (presetProjectId && projects.some((p) => p.id === presetProjectId)) {
      form.setFieldValue('projectId', presetProjectId)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetProjectId, projects.length])

  // 站内跳转守卫：表单有实质内容时弹确认，取消本次跳转
  useEffect(() => {
    setNavGuard((target) => {
      const v = form.getFieldsValue()
      if (!isDraftMeaningful({ projectId: v.projectId, title: v.title, kbIds: v.kbIds, requirement: v.requirement })) return true
      setConfirmNav(target)
      return false
    })
    return () => setNavGuard(null)
  }, [form])

  function tryLeave(target: string) {
    if (checkNavGuard(target)) navigate(target)
  }

  function leaveWith(mode: 'save' | 'discard') {
    if (mode === 'save') {
      const v = form.getFieldsValue()
      saveDraft({ projectId: v.projectId, title: v.title, kbIds: v.kbIds, requirement: v.requirement })
    } else {
      clearDraft()
    }
    setConfirmNav(null)
    setNavGuard(null)
    navigate(confirmNav ?? '/')
  }

  async function submit(values: FormValues) {
    setSubmitting(true)
    try {
      const kbIds = values.kbIds?.length ? values.kbIds : undefined
      const session = await createSession({ ...values, kbIds })
      await fireEvent(session.id, 'START')
      message.success('会话已创建，A1 需求解析已启动')
      clearDraft()
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
      <PageHeader title="新建设计会话" backTo="/" onBack={() => tryLeave('/')} />
      {restored && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={`已恢复上次未提交的草稿（保存于 ${dayjs(restored.savedAt).format('HH:mm')}）`}
          action={
            <Button size="small" danger onClick={() => { clearDraft(); form.resetFields(); setRestored(null) }}>
              清除
            </Button>
          }
        />
      )}
      <Card>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 0 }}>
          提交后需求原文固化为 IR0 不可变快照（A-04），作为全程溯源起点；流程从 A1 需求解析开始。
        </Typography.Paragraph>
        <Form<FormValues>
          form={form}
          layout="vertical"
          onFinish={submit}
          onValuesChange={(_, allValues) => saveDraft(allValues)}
        >
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
            <Button style={{ marginRight: 8 }} onClick={() => tryLeave('/')}>取消</Button>
            <Button type="primary" htmlType="submit" loading={submitting}>提交并启动分析</Button>
          </div>
        </Form>
      </Card>
      <NewProjectModal
        open={projectModal}
        onClose={() => setProjectModal(false)}
        onCreated={(p) => form.setFieldValue('projectId', p.id)}
      />
      <Modal
        open={!!confirmNav}
        title="草稿尚未提交"
        onCancel={() => setConfirmNav(null)}
        footer={[
          <Button key="discard" onClick={() => leaveWith('discard')}>放弃草稿</Button>,
          <Button key="cancel" onClick={() => setConfirmNav(null)}>取消</Button>,
          <Button key="save" type="primary" onClick={() => leaveWith('save')}>保存草稿并离开</Button>,
        ]}
      >
        是否保存当前草稿？保存后下次进入本页可继续填写；放弃则清空。
      </Modal>
    </div>
  )
}
