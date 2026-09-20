import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { App, Form, Input, Modal } from 'antd'
import { createProject, patchProject } from '../api/sessions'
import { errText } from '../api/client'
import type { Project } from '../api/types'

/** 新建项目弹窗（需求输入页 / 项目管理页共用）。 */
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
      message.error(errText(e))
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

/** 编辑项目弹窗（名称/代号/说明，局部更新；空代号/说明表示清空）。 */
export function EditProjectModal({ open, project, onClose }: {
  open: boolean
  project: Project | null
  onClose: () => void
}) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [form] = Form.useForm<{ name: string; code?: string; description?: string }>()
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open && project) {
      form.setFieldsValue({
        name: project.name,
        code: project.code ?? undefined,
        description: project.description ?? undefined,
      })
    }
  }, [open, project, form])

  async function save() {
    if (!project) return
    const values = await form.validateFields()
    setSaving(true)
    try {
      await patchProject(project.id, {
        name: values.name,
        code: values.code ?? '',
        description: values.description ?? '',
      })
      message.success(`项目「${values.name}」已保存`)
      void queryClient.invalidateQueries({ queryKey: ['projects'] })
      onClose()
    } catch (e) {
      message.error(errText(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal title="编辑项目" open={open} onCancel={onClose} onOk={save} confirmLoading={saving} okText="保存" destroyOnClose>
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="项目名称" rules={[{ required: true, message: '请输入名称' }]}>
          <Input maxLength={60} />
        </Form.Item>
        <Form.Item name="code" label="项目代号">
          <Input maxLength={30} placeholder="留空即清除代号" />
        </Form.Item>
        <Form.Item name="description" label="说明">
          <Input.TextArea rows={2} maxLength={200} placeholder="留空即清除说明" />
        </Form.Item>
      </Form>
    </Modal>
  )
}
