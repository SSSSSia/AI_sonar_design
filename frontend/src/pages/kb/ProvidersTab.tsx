import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Button, Card, Form, Input, InputNumber, Modal, Space, Switch, Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { createProvider, listProviders, updateProvider, type LlmProvider } from '../../api/kb'
import { QueryEmpty } from '../../components/QueryEmpty'
import { hasAnyRole, useAuthStore } from '../../stores/authStore'

/** LLM 供应商管理：多供应商热切换，api_key 只写不回显（保存后热重载）。
 *  写操作 admin-only（审查 P1-5：前端隐藏 + 后端 require_admin 双层）。 */
export function ProvidersTab() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const roles = useAuthStore((s) => s.roles)
  const isAdmin = hasAnyRole(roles, ['admin'])
  const [editing, setEditing] = useState<LlmProvider | null>(null)
  const [creating, setCreating] = useState(false)
  const [form] = Form.useForm<Record<string, unknown>>()

  const q = useQuery({ queryKey: ['kb-providers'], queryFn: listProviders })
  const rows = q.data?.providers ?? []

  async function toggle(row: LlmProvider, enabled: boolean) {
    try {
      await updateProvider(row.name, { enabled })
      void queryClient.invalidateQueries({ queryKey: ['kb-providers'] })
    } catch (e) { message.error(String(e).slice(0, 140)) }
  }

  const columns: ColumnsType<LlmProvider> = [
    { title: '名称', dataIndex: 'name', width: 130, render: (v) => <b>{v}</b> },
    { title: 'Base URL', dataIndex: 'base_url', render: (v) => <span style={{ fontFamily: 'monospace', fontSize: 11.5 }}>{v}</span> },
    { title: '模型', dataIndex: 'model', width: 220, render: (v) => <span style={{ fontFamily: 'monospace', fontSize: 11.5 }}>{v ?? '—'}</span> },
    { title: '优先级', dataIndex: 'priority', width: 80 },
    { title: 'Key', width: 120, render: (_, r) => <span style={{ fontFamily: 'monospace', fontSize: 11.5 }}>{(r as unknown as { api_key_masked?: string }).api_key_masked ?? '****'}</span> },
    { title: '启用', dataIndex: 'enabled', width: 80, render: (v, r) => <Switch size="small" checked={!!v} disabled={!isAdmin} onChange={(on) => toggle(r, on)} /> },
    {
      title: '', key: 'edit', width: 70,
      render: (_, r) => <Button size="small" type="text" disabled={!isAdmin} onClick={() => { setEditing(r); form.setFieldsValue({ ...r }) }}>编辑</Button>,
    },
  ]

  async function save() {
    const values = await form.validateFields()
    try {
      if (editing) {
        const fields: Record<string, unknown> = { ...values }
        if (!fields.api_key) delete fields.api_key
        await updateProvider(editing.name, fields)
        message.success('已保存并热重载')
      } else {
        await createProvider(values as { name: string; base_url: string; api_key: string })
        message.success('已新增并热重载')
      }
      setEditing(null); setCreating(false); form.resetFields()
      void queryClient.invalidateQueries({ queryKey: ['kb-providers'] })
    } catch (e) { message.error(String(e).slice(0, 160)) }
  }

  return (
    <Card
      title="LLM 供应商"
      extra={isAdmin ? <Button type="primary" onClick={() => { setCreating(true); form.resetFields() }}>＋ 新增供应商</Button> : <Typography.Text type="secondary" style={{ fontSize: 12 }}>仅管理员可修改</Typography.Text>}
      styles={{ body: { padding: 0 } }}
    >
      <Table
        rowKey="name" size="small" loading={q.isLoading} dataSource={rows} columns={columns} pagination={false}
        locale={{ emptyText: <QueryEmpty query={q} subject="供应商列表" /> }}
      />
      {!isAdmin && (
        <div style={{ padding: '6px 16px', borderTop: '1px solid #f0f1f3' }}>
          <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>当前角色无权修改供应商配置（涉及密钥写入，仅 admin）。</Typography.Text>
        </div>
      )}
      <div style={{ padding: '8px 16px', borderTop: '1px solid #f0f1f3' }}>
        <span style={{ fontSize: 11.5, color: '#8b949e' }}>api_key 只写不回显（列表仅显示掩码）；保存后适配器热重载，无需重启。</span>
      </div>

      <Modal
        title={editing ? `编辑：${editing.name}` : '新增供应商'}
        open={creating || !!editing}
        onCancel={() => { setEditing(null); setCreating(false) }}
        onOk={save}
        okText="保存（热重载）"
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: !editing, message: '必填' }]}>
            <Input disabled={!!editing} placeholder="如 deepseek" />
          </Form.Item>
          <Form.Item name="base_url" label="Base URL" rules={[{ required: true, message: '必填' }]}>
            <Input placeholder="https://api.deepseek.com" />
          </Form.Item>
          <Form.Item name="api_key" label={editing ? 'API Key（留空不改）' : 'API Key'} rules={[{ required: !editing, message: '必填' }]}>
            <Input.Password placeholder="sk-…" autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="model" label="默认模型">
            <Input placeholder="如 deepseek-chat / PaddleOCR-VL-1.5" />
          </Form.Item>
          <Space>
            <Form.Item name="priority" label="优先级" initialValue={2}>
              <InputNumber min={1} max={9} style={{ width: 90 }} />
            </Form.Item>
            <Form.Item name="timeout_s" label="超时(秒)" initialValue={300}>
              <InputNumber min={10} max={600} style={{ width: 110 }} />
            </Form.Item>
            <Form.Item name="enabled" label="启用" initialValue={true} valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </Card>
  )
}
