import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Table, Tag, Typography } from 'antd'
import type { KbStatus } from '../api/kb'
import { probeGet } from '../api/client'

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

/** 系统管理基础（T4.4）：真实探测 + KB 现状；无后端支撑的能力如实「待接」（A-16）。 */
export default function AdminPage() {
  const probe = useProbe()
  const kb = (probe.kb ?? {}) as KbStatus

  const rows = [
    { key: 'auth', name: 'auth（登录/自省）', via: 'GET /api/v1/auth/me' },
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
            gateway / notify / worker 等基础设施容器健康以 deploy 编排健康检查为准；日志检索与备份恢复能力后端未建，如实「待接」。
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
          message="本页数字一律来自后端真实接口（A-16）；日志中心、备份恢复、用户管理 API 就绪后自动升级为可操作界面。"
        />
      </Card>

      <Card title="运维基础" styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="k" size="small" pagination={false}
          dataSource={[
            { k: '指标总览', s: '可用', d: '本页服务可达性 + 知识库现状（来源真实接口）' },
            { k: '日志检索', s: '待接', d: '后端日志中心未建；工作台可下载单会话执行记录' },
            { k: '备份恢复', s: '待接', d: 'PG/Neo4j/MinIO 备份策略属阶段 5 发布事项' },
            { k: '用户管理', s: '待接', d: 'auth 服务暂无用户管理 API；账号经 SQL 维护' },
          ].map((r) => ({ ...r, key: r.k }))}
          columns={[
            { title: '能力', dataIndex: 'k', width: 140 },
            { title: '状态', dataIndex: 's', width: 90, render: (v) => <Tag color={v === '可用' ? 'success' : 'default'}>{v}</Tag> },
            { title: '说明', dataIndex: 'd', render: (v) => <span style={{ fontSize: 12, color: '#57606a' }}>{v}</span> },
          ]}
        />
      </Card>
    </div>
  )
}
