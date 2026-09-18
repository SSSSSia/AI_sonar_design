import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Button, Card, Input, Popconfirm, Select, Space, Switch, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  createRegistryRow, deleteRegistryRow, listRegistry, updateRegistryRow,
  type RegistryKind, type RegistryRow,
} from '../../api/kb'

const KIND_TAB: { value: RegistryKind; label: string }[] = [
  { value: 'formulas', label: '公式库' },
  { value: 'constraints', label: '约束库' },
  { value: 'standards', label: '军标库' },
]

/** 三库管理：公式/约束/军标 CRUD + 启停 + 可信度（A4 校验优先读库）。 */
export function RegistryTab() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [kind, setKind] = useState<RegistryKind>('formulas')
  const [adding, setAdding] = useState(false)
  const [newKey, setNewKey] = useState('')
  const [newName, setNewName] = useState('')
  const [newExpr, setNewExpr] = useState('')

  const rowsQuery = useQuery({ queryKey: ['kb-registry', kind], queryFn: () => listRegistry(kind) })
  const rows: RegistryRow[] = rowsQuery.data?.rows ?? []

  async function patch(row: RegistryRow, fields: Record<string, unknown>) {
    const id = String(row.row_id ?? row.id ?? row.key)
    try {
      await updateRegistryRow(kind, id, fields)
      void queryClient.invalidateQueries({ queryKey: ['kb-registry', kind] })
    } catch (e) { message.error(String(e).slice(0, 140)) }
  }

  const columns: ColumnsType<RegistryRow> = [
    { title: '编号/键', key: 'id', width: 170, render: (_, r) => <span style={{ fontFamily: 'monospace', fontSize: 11.5 }}>{String(r.row_id ?? r.id ?? r.key ?? '—')}</span> },
    { title: '名称', key: 'name', render: (_, r) => <span style={{ fontWeight: 500 }}>{String(r.name ?? r.label ?? r.key ?? '—')}</span> },
    {
      title: '表达式 / 内容',
      key: 'expr',
      render: (_, r) => {
        const e = (r.expr ?? r.expression ?? r.check ?? r.description ?? '') as string
        return <span style={{ fontFamily: 'monospace', fontSize: 11.5, color: '#57606a' }}>{String(e).slice(0, 120) || '—'}</span>
      },
    },
    {
      title: '可信度',
      dataIndex: 'confidence',
      width: 100,
      render: (v) => (v ? <Tag color={v === 'high' ? 'green' : 'default'}>{String(v)}</Tag> : '—'),
    },
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 80,
      render: (v, r) => (
        <Switch size="small" checked={!!v} onChange={(on) => patch(r, { enabled: on })} />
      ),
    },
    {
      title: '',
      key: 'del',
      width: 70,
      render: (_, r) => (
        <Popconfirm title="确认删除该条目？" onConfirm={async () => {
          try {
            await deleteRegistryRow(kind, String(r.row_id ?? r.id ?? r.key))
            void queryClient.invalidateQueries({ queryKey: ['kb-registry', kind] })
          } catch (e) { message.error(String(e).slice(0, 140)) }
        }}>
          <Button size="small" danger type="text">删除</Button>
        </Popconfirm>
      ),
    },
  ]

  return (
    <Card
      title={
        <Space>
          <Select value={kind} onChange={(v) => setKind(v)} style={{ width: 120 }}
            options={KIND_TAB.map((k) => ({ value: k.value, label: k.label }))} />
          <span style={{ fontSize: 12, color: '#8b949e', fontWeight: 400 }}>A4 校验优先读库（启停即时生效）</span>
        </Space>
      }
      extra={
        kind === 'standards' ? (
          <Button type="primary" onClick={() => setAdding(true)}>＋ 新增军标条目</Button>
        ) : (
          <Button type="primary" onClick={() => setAdding(true)}>＋ 新增条目</Button>
        )
      }
      styles={{ body: { padding: 0 } }}
    >
      {kind === 'standards' && rows.length === 0 && (
        <div style={{ margin: '10px 16px', borderLeft: '3px solid #d97706', background: '#fdf3e3', padding: '8px 12px', fontSize: 12, borderRadius: 4 }}>
          军标库空态（如实）：当前 0 条，A4 对 standard 参数相关检查如实标「待补」；录入后自动参与校验。
        </div>
      )}
      <Table rowKey={(r) => String(r.row_id ?? r.id ?? r.key)} size="small" loading={rowsQuery.isLoading} dataSource={rows} columns={columns} pagination={{ pageSize: 15 }} />

      {adding && (
        <Card size="small" style={{ margin: 12 }} title="新增条目">
          <Space direction="vertical" style={{ width: '100%' }}>
            <Input placeholder="编号/键（如 F-08 / GJB 585-91）" value={newKey} onChange={(e) => setNewKey(e.target.value)} />
            <Input placeholder="名称" value={newName} onChange={(e) => setNewName(e.target.value)} />
            <Input.TextArea placeholder={kind === 'formulas' ? '表达式（SymPy，如 d = c / (2*f_max)）' : '内容描述 / 检查逻辑说明'} value={newExpr} onChange={(e) => setNewExpr(e.target.value)} rows={2} />
            <Space style={{ justifyContent: 'flex-end', width: '100%' }}>
              <Button onClick={() => setAdding(false)}>取消</Button>
              <Button
                type="primary"
                onClick={async () => {
                  if (!newKey.trim() || !newName.trim()) { message.warning('键与名称必填'); return }
                  try {
                    const fields: Record<string, unknown> = { key: newKey.trim(), name: newName.trim(), enabled: true, confidence: 'high' }
                    if (newExpr.trim()) fields[kind === 'formulas' ? 'expr' : 'description'] = newExpr.trim()
                    if (kind === 'standards') fields.check = null
                    await createRegistryRow(kind, fields)
                    message.success('已新增')
                    setAdding(false); setNewKey(''); setNewName(''); setNewExpr('')
                    void queryClient.invalidateQueries({ queryKey: ['kb-registry', kind] })
                  } catch (e) { message.error(String(e).slice(0, 140)) }
                }}
              >保存</Button>
            </Space>
          </Space>
        </Card>
      )}
    </Card>
  )
}
