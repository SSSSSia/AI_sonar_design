import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Alert, Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message,
} from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import type { KbStatus } from '../api/kb'
import { searchLogs, type LogRow } from '../api/logs'
import { searchAuditLogs, type AuditLogRow } from '../api/audit'
import { probeGet } from '../api/client'
import { errText } from '../api/client'
import {
  createUser, deleteUser, listUsers, patchUser, resetUserPassword, setUserRoles, type ManagedUser,
} from '../api/auth'
import { useAuthStore } from '../stores/authStore'

/** 服务探测：经网关调用各服务真实接口（5s 短超时，P0-2），200 即视为可达（含 DB/MQ 链路）。 */
function useProbe() {
  const auth = useQuery({ queryKey: ['probe-auth'], queryFn: () => probeGet('/auth/me'), retry: false, refetchInterval: 30_000 })
  const workflow = useQuery({ queryKey: ['probe-workflow'], queryFn: () => probeGet('/workflow/sessions', { page: 1, size: 1 }), retry: false, refetchInterval: 30_000 })
  const biz = useQuery({ queryKey: ['probe-biz'], queryFn: () => probeGet('/projects', { page: 1, size: 1 }), retry: false, refetchInterval: 30_000 })
  const py = useQuery({ queryKey: ['probe-py'], queryFn: () => probeGet('/kb/status'), retry: false, refetchInterval: 30_000 })
  return {
    auth: auth.isError ? 'down' : auth.data ? 'up' : 'checking',
    workflow: workflow.isError ? 'down' : workflow.data ? 'up' : 'checking',
    biz: biz.isError ? 'down' : biz.data ? 'up' : 'checking',
    pyengine: py.isError ? 'down' : py.data ? 'up' : 'checking',
    kb: py.data,
  }
}

const DOT: Record<string, string> = { up: '#2b8a3e', down: '#c92a2a', checking: '#d1d5db' }
const TXT: Record<string, string> = { up: '可达', down: '不可达', checking: '检测中…' }

const ROLE_OPTIONS = ['admin', 'designer', 'reviewer', 'viewer'].map((r) => ({ value: r, label: r }))
const ROLE_COLOR: Record<string, string> = { admin: 'gold', designer: 'blue', reviewer: 'green', viewer: 'default' }

/** 日志中心（T12.5）：跨会话运行遥测检索（admin），/logs/search 过滤 + 分页。 */
function LogCenter() {
  const [sid, setSid] = useState('')
  const [level, setLevel] = useState<string | undefined>(undefined)
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(0)
  const PAGE = 50
  const query = useQuery({
    queryKey: ['log-center', sid, level, keyword, page],
    queryFn: () => searchLogs({ session_id: sid || undefined, level, keyword: keyword || undefined, limit: PAGE, offset: page * PAGE }),
    refetchInterval: 60_000,
  })
  const rows: LogRow[] = query.data?.logs ?? []
  const total = query.data?.total ?? 0
  const LEVEL_COLOR: Record<string, string> = { error: 'red', warn: 'orange', warning: 'orange', ok: 'green', info: 'blue', debug: 'default' }
  return (
    <Card
      title="日志中心（跨会话运行遥测）"
      extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>GET /api/v1/logs/search · 60s 刷新 · 共 {total} 条</Typography.Text>}
      styles={{ body: { padding: 0 } }}
    >
      <div style={{ padding: '10px 12px', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Input allowClear placeholder="会话 ID（前缀）" style={{ width: 280 }} value={sid} onChange={(e) => { setSid(e.target.value); setPage(0) }} />
        <Select allowClear placeholder="级别" style={{ width: 120 }} value={level}
          onChange={(v) => { setLevel(v); setPage(0) }}
          options={['error', 'warn', 'info', 'ok', 'debug'].map((l) => ({ value: l, label: l }))} />
        <Input.Search allowClear placeholder="关键字（正文包含）" style={{ width: 260 }} enterButton
          onSearch={(v) => { setKeyword(v); setPage(0) }} />
      </div>
      <Table
        rowKey="id" size="small"
        loading={query.isLoading}
        dataSource={rows}
        pagination={{ pageSize: PAGE, current: page + 1, total, showSizeChanger: false,
          onChange: (p) => setPage(p - 1) }}
        columns={[
          { title: '时间', dataIndex: 'created_at', width: 170, render: (v: string) => (v ?? '').replace('T', ' ').slice(0, 19) },
          { title: '会话', dataIndex: 'session_id', width: 200, render: (v: string) => <Typography.Text code style={{ fontSize: 11 }}>{(v ?? '').slice(0, 13)}</Typography.Text> },
          { title: '级别', dataIndex: 'level', width: 80, render: (v: string) => <Tag color={LEVEL_COLOR[v] ?? 'default'}>{v}</Tag> },
          { title: '内容', dataIndex: 'text', ellipsis: true, render: (v: string) => <span style={{ fontSize: 12 }}>{v}</span> },
        ]}
      />
    </Card>
  )
}

/** 审计中心（T13.1）：§9.4 审计留痕检索（admin），/audit/logs 过滤 + 分页。 */
function AuditCenter() {
  const [username, setUsername] = useState('')
  const [action, setAction] = useState('')
  const [resourceType, setResourceType] = useState<string | undefined>(undefined)
  const [hours, setHours] = useState<number | undefined>(undefined)
  const [page, setPage] = useState(0)
  const PAGE = 50
  const query = useQuery({
    queryKey: ['audit-center', username, action, resourceType, hours, page],
    queryFn: () => searchAuditLogs({
      username: username || undefined, action: action || undefined, resource_type: resourceType,
      hours, limit: PAGE, offset: page * PAGE,
    }),
    refetchInterval: 60_000,
  })
  const rows: AuditLogRow[] = query.data?.logs ?? []
  const total = query.data?.total ?? 0
  const detailText = (d: AuditLogRow['detail']) => {
    if (d == null) return '—'
    if (typeof d === 'string') return d
    return JSON.stringify(d)
  }
  return (
    <Card
      title="审计中心（§9.4 审计留痕）"
      extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>GET /api/v1/audit/logs · 60s 刷新 · 共 {total} 条</Typography.Text>}
      styles={{ body: { padding: 0 } }}
    >
      <div style={{ padding: '10px 12px', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Input allowClear placeholder="操作者（用户名，模糊）" style={{ width: 200 }} value={username}
          onChange={(e) => { setUsername(e.target.value); setPage(0) }} />
        <Input allowClear placeholder="动作（如 auth. / kb. / export.）" style={{ width: 220 }} value={action}
          onChange={(e) => { setAction(e.target.value); setPage(0) }} />
        <Select allowClear placeholder="资源类型" style={{ width: 150 }} value={resourceType}
          onChange={(v) => { setResourceType(v); setPage(0) }}
          options={['user', 'project', 'session', 'kb_document', 'kb_base', 'kb_registry', 'kb_extraction', 'llm_provider'].map((t) => ({ value: t, label: t }))} />
        <Select allowClear placeholder="时间窗" style={{ width: 130 }} value={hours}
          onChange={(v) => { setHours(v); setPage(0) }}
          options={[1, 24, 168, 720].map((h) => ({ value: h, label: `近 ${h} 小时` }))} />
      </div>
      <Table
        rowKey="id" size="small"
        loading={query.isLoading}
        dataSource={rows}
        pagination={{ pageSize: PAGE, current: page + 1, total, showSizeChanger: false,
          onChange: (p) => setPage(p - 1) }}
        columns={[
          { title: '时间', dataIndex: 'created_at', width: 165, render: (v: string) => (v ?? '').replace('T', ' ').slice(0, 19) },
          { title: '操作者', dataIndex: 'username', width: 110, render: (v: string | null) => v || <Typography.Text type="secondary">系统</Typography.Text> },
          { title: '动作', dataIndex: 'action', width: 170, render: (v: string) => <Typography.Text code style={{ fontSize: 11.5 }}>{v}</Typography.Text> },
          { title: '资源', key: 'res', width: 190, ellipsis: true, render: (_: unknown, r: AuditLogRow) => (
            <span style={{ fontSize: 11.5 }}>
              {r.resource_type ? <Tag style={{ marginRight: 4 }}>{r.resource_type}</Tag> : null}
              <Typography.Text code style={{ fontSize: 11 }}>{(r.resource_id ?? '').slice(0, 14)}</Typography.Text>
            </span>
          ) },
          { title: '详情', key: 'detail', ellipsis: true, render: (_: unknown, r: AuditLogRow) => <span style={{ fontSize: 12 }}>{detailText(r.detail)}</span> },
          { title: 'IP', dataIndex: 'ip', width: 110, render: (v: string | null) => <span style={{ fontSize: 11.5 }}>{v || '—'}</span> },
        ]}
      />
      <div style={{ padding: '8px 16px' }}>
        <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
          留痕范围（§9.4 口径）：登录/用户管理/项目与会话写操作/审阅裁决/知识库写/导出——Agent 调用、参数变更、知识回写由溯源体系专表承担（不重复留痕）。
        </Typography.Text>
      </div>
    </Card>
  )
}

/** 用户管理（T11.2）：auth /api/v1/auth/users（admin-only），创建/启停/重置密码/角色分配/删除。 */
function UserManagement() {
  const me = useAuthStore((s) => s.username)
  const [messageApi, contextHolder] = message.useMessage()
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<ManagedUser | null>(null)
  const [pwdOpen, setPwdOpen] = useState<ManagedUser | null>(null)
  const [createForm] = Form.useForm()
  const [roleForm] = Form.useForm()
  const [pwdForm] = Form.useForm()
  const users = useQuery({
    queryKey: ['auth-users'],
    queryFn: listUsers,
    refetchInterval: 60_000,
  })

  const run = async (action: () => Promise<unknown>, okMsg: string) => {
    try {
      await action()
      messageApi.success(okMsg)
      await users.refetch()
      return true
    } catch (e) {
      messageApi.error(errText(e))
      return false
    }
  }

  const columns = [
    { title: '用户名', dataIndex: 'username', width: 140 },
    { title: '显示名', dataIndex: 'displayName', width: 140, render: (v: string | null) => v || '—' },
    {
      title: '角色', dataIndex: 'roles', width: 220,
      render: (roles: string[]) => (
        <Space size={4} wrap>
          {roles.map((r) => <Tag key={r} color={ROLE_COLOR[r] ?? 'default'}>{r}</Tag>)}
        </Space>
      ),
    },
    {
      title: '状态', dataIndex: 'status', width: 90,
      render: (s: string) => <Tag color={s === 'active' ? 'success' : 'default'}>{s === 'active' ? '启用' : '禁用'}</Tag>,
    },
    { title: '邮箱', dataIndex: 'email', render: (v: string | null) => v || '—' },
    {
      title: '操作', key: 'ops', width: 300,
      render: (_: unknown, u: ManagedUser) => {
        const isSelf = u.username === me
        return (
          <Space size={4}>
            <Button type="link" size="small" onClick={() => { setEditing(u); roleForm.setFieldsValue({ roles: u.roles }) }}>
              角色分配
            </Button>
            <Button type="link" size="small" onClick={() => setPwdOpen(u)}>重置密码</Button>
            {u.status === 'active' ? (
              <Popconfirm title={`停用 ${u.username}？`} okButtonProps={{ danger: true }}
                disabled={isSelf}
                onConfirm={() => void run(() => patchUser(u.id, { status: 'disabled' }), '已停用')}>
                <Button type="link" size="small" danger disabled={isSelf}>停用</Button>
              </Popconfirm>
            ) : (
              <Button type="link" size="small"
                onClick={() => void run(() => patchUser(u.id, { status: 'active' }), '已启用')}>启用</Button>
            )}
            <Popconfirm title={`删除 ${u.username}？该操作不可恢复。`} okButtonProps={{ danger: true }}
              disabled={isSelf}
              onConfirm={() => void run(() => deleteUser(u.id), '已删除')}>
              <Button type="link" size="small" danger disabled={isSelf}>删除</Button>
            </Popconfirm>
          </Space>
        )
      },
    },
  ]

  return (
    <Card
      title="用户与角色管理"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} size="small" onClick={() => void users.refetch()}>刷新</Button>
          <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建用户</Button>
        </Space>
      }
      styles={{ body: { padding: 0 } }}
    >
      {contextHolder}
      <Table
        rowKey="id" size="small" pagination={false} loading={users.isLoading}
        dataSource={users.data ?? []} columns={columns}
      />
      <div style={{ padding: '8px 16px' }}>
        <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
          角色语义：admin=全部权限（含本页）· designer=项目/会话/KB 读 + 建模 · reviewer=审阅关裁决 · viewer=只读。知识库写操作自 T11.2 起仅 admin。
        </Typography.Text>
      </div>

      <Modal
        title="新建用户" open={createOpen} onCancel={() => setCreateOpen(false)} okText="创建"
        onOk={async () => {
          const values = await createForm.validateFields()
          const ok = await run(() => createUser({
            username: values.username, password: values.password,
            displayName: values.displayName, email: values.email, roles: values.roles ?? ['designer'],
          }), '用户已创建')
          if (ok) { setCreateOpen(false); createForm.resetFields() }
        }}
      >
        <Form form={createForm} layout="vertical" initialValues={{ roles: ['designer'] }}>
          <Form.Item name="username" label="用户名" rules={[
            { required: true }, { pattern: /^[a-zA-Z0-9_-]{3,32}$/, message: '3-32 位字母/数字/下划线/中划线' },
          ]}>
            <Input placeholder="如 zhang_san" />
          </Form.Item>
          <Form.Item name="displayName" label="显示名">
            <Input />
          </Form.Item>
          <Form.Item name="email" label="邮箱">
            <Input />
          </Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{ required: true }, { min: 6, message: '至少 6 位' }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="roles" label="角色" rules={[{ required: true }]}>
            <Select mode="multiple" options={ROLE_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`角色分配：${editing?.username ?? ''}`} open={!!editing} onCancel={() => setEditing(null)} okText="保存"
        onOk={async () => {
          const values = await roleForm.validateFields()
          const ok = await run(() => setUserRoles(editing!.id, values.roles), '角色已更新')
          if (ok) setEditing(null)
        }}
      >
        <Form form={roleForm} layout="vertical">
          <Form.Item name="roles" label="角色（替换式：保存后以所选为准）" rules={[{ required: true }]}>
            <Select mode="multiple" options={ROLE_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`重置密码：${pwdOpen?.username ?? ''}`} open={!!pwdOpen} onCancel={() => setPwdOpen(null)} okText="重置"
        onOk={async () => {
          const values = await pwdForm.validateFields()
          const ok = await run(() => resetUserPassword(pwdOpen!.id, values.newPassword), '密码已重置')
          if (ok) { setPwdOpen(null); pwdForm.resetFields() }
        }}
      >
        <Form form={pwdForm} layout="vertical">
          <Form.Item name="newPassword" label="新密码" rules={[{ required: true }, { min: 6, message: '至少 6 位' }]}>
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

/** 系统管理基础（T4.4 → T11.2 升级）：真实探测 + KB 现状 + 用户管理；日志检索仍如实「待接」（A-16）。 */
export default function AdminPage() {
  const probe = useProbe()
  const kb = (probe.kb ?? {}) as KbStatus

  const rows = [
    { key: 'auth', name: 'auth（登录/自省/用户管理）', via: 'GET /api/v1/auth/me' },
    { key: 'workflow', name: 'workflow（会话/审阅/状态机）', via: 'GET /api/v1/workflow/sessions' },
    { key: 'biz', name: 'biz（项目域）', via: 'GET /api/v1/projects' },
    { key: 'pyengine', name: 'py-engine（A1–A5 / KB / CAD）', via: 'GET /api/v1/kb/status' },
  ].map((r) => ({ ...r, status: probe[r.key as 'auth' | 'workflow' | 'biz' | 'pyengine'] }))

  return (
    <div style={{ maxWidth: 1320, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card title="服务可达性" extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>经网关真实调用各服务接口 · 30s 刷新</Typography.Text>} styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="key" size="small" pagination={false} dataSource={rows}
          columns={[
            { title: '服务', dataIndex: 'name' },
            { title: '探测方式', dataIndex: 'via', render: (v) => <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#8b949e' }}>{v}</span> },
            { title: '状态', key: 'st', width: 110, render: (_, r) => <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 4, background: DOT[r.status], marginRight: 6 }} />{TXT[r.status]}</span> },
          ]}
        />
        <div style={{ padding: '8px 16px' }}>
          <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
            gateway / notify / worker 等基础设施容器健康以 deploy 编排健康检查为准；日志中心与审计中心见下方卡片；备份恢复已交付（T11.7）：deploy/backup/backup.sh + restore.sh，演练证据见 docs/reviews/。
          </Typography.Text>
        </div>
      </Card>

      <Card title="知识库现状" extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>GET /api/v1/kb/status</Typography.Text>}>
        <Table
          rowKey="k" size="small" pagination={false}
          dataSource={[
            { k: '图谱级检索', v: kb.neo4j_available === undefined ? '—' : kb.neo4j_available },
            { k: '向量级检索（Milvus）', v: kb.milvus_available === undefined ? '—' : kb.milvus_available },
            { k: 'GraphRAG SDK（可选重路径）', v: kb.graphrag_sdk === undefined ? '—' : kb.graphrag_sdk },
            { k: '动态语料（文本级）', v: `${kb.dynamic_corpus_chunks ?? '—'} 块` },
            { k: '知识文档', v: `${kb.documents ?? '—'} 个` },
            { k: '分块', v: `${kb.chunks ?? '—'} 块` },
            { k: '抽取（已确认）', v: `${kb.extractions_confirmed ?? '—'} 条` },
            { k: '抽取（待审）', v: `${kb.extractions_pending ?? '—'} 条` },
          ].map((r) => ({ ...r, key: r.k }))}
          columns={[
            { title: '项', dataIndex: 'k' },
            {
              title: '现状', dataIndex: 'v', width: 200,
              render: (v) => typeof v === 'boolean'
                ? <Tag color={v ? 'success' : 'default'}>{v ? '可用' : '不可用'}</Tag>
                : <b>{String(v)}</b>,
            },
          ]}
        />
        <Alert
          type="info" showIcon style={{ marginTop: 10 }}
          message="本页数字一律来自后端真实接口（A-16）；日志中心（T12.5）与审计中心（T13.1）见下方卡片；备份恢复已交付（T11.7）：deploy/backup/backup.sh + restore.sh，演练证据见 docs/reviews/。"
        />
      </Card>

      <LogCenter />

      <AuditCenter />

      <UserManagement />
    </div>
  )
}
