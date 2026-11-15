import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Button, Card, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Tooltip, Upload } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  confirmRefScript, getRefContent, importRefCatalog, listRefScripts, patchRefScript, uploadRefScript,
  type RefScriptRow,
} from '../../api/references'
import { QueryEmpty } from '../../components/QueryEmpty'
import { errText } from '../../api/client'
import { formatRefScope, importReportText, latestVersionIds, refSourceLabel, refStatusMeta, shortSha } from './refView'

/** 范例库管理（T16.1 B1，ADR-015）：启停/确认直接影响检索（REF_SOURCE=db 时装载 active 行）。
 * 状态机 pending→confirm→active（单活互斥）；无删除（回退不删历史版本）。写权限服务端 admin 门禁。 */
export function ReferencesTab() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [uploadOpen, setUploadOpen] = useState(false)
  const [importing, setImporting] = useState(false)
  const [viewRow, setViewRow] = useState<RefScriptRow | null>(null)
  const [viewContent, setViewContent] = useState<{ content: string; sha256_match: boolean } | null>(null)
  const [form, setForm] = useState({
    file: null as File | null, ref_id: '', title: '', description: '', domain: '', domain_rules: '',
    sonar_types: [] as string[], keywords: [] as string[],
  })

  const rowsQuery = useQuery({ queryKey: ['kb-ref-scripts'], queryFn: () => listRefScripts() })
  const rows: RefScriptRow[] = rowsQuery.data?.rows ?? []
  const latest = latestVersionIds(rows)

  async function patch(row: RefScriptRow, fields: Record<string, unknown>) {
    try {
      await patchRefScript(row.id, fields)
      void queryClient.invalidateQueries({ queryKey: ['kb-ref-scripts'] })
    } catch (e) { message.error(errText(e, 140)) }
  }

  async function confirm(row: RefScriptRow) {
    try {
      await confirmRefScript(row.id)
      message.success(`已确认激活 ${row.ref_id} v${row.version}（同范例旧版本已停用）`)
      void queryClient.invalidateQueries({ queryKey: ['kb-ref-scripts'] })
    } catch (e) { message.error(errText(e, 160)) }
  }

  async function importCatalog() {
    setImporting(true)
    try {
      const r = await importRefCatalog()
      message.success(importReportText(r.items, r.created, r.unchanged), 6)
      void queryClient.invalidateQueries({ queryKey: ['kb-ref-scripts'] })
    } catch (e) { message.error(errText(e, 180)) } finally { setImporting(false) }
  }

  async function saveUpload() {
    if (!form.file) { message.warning('请选择 .py 范例脚本'); return }
    if (!form.ref_id.trim()) { message.warning('范例 ID 必填（小写字母/数字/_/-）'); return }
    try {
      const r = await uploadRefScript(form.file, {
        ref_id: form.ref_id.trim(), title: form.title.trim() || undefined,
        description: form.description.trim() || undefined, domain: form.domain.trim() || undefined,
        domain_rules: form.domain_rules.trim() || undefined,
        sonar_types: form.sonar_types, keywords: form.keywords,
      })
      message.success(`已登记 ${r.row.ref_id} v${r.row.version}（待确认，确认后参与检索）`)
      setUploadOpen(false)
      setForm({ file: null, ref_id: '', title: '', description: '', domain: '', domain_rules: '', sonar_types: [], keywords: [] })
      void queryClient.invalidateQueries({ queryKey: ['kb-ref-scripts'] })
    } catch (e) { message.error(errText(e, 180)) }
  }

  async function openView(row: RefScriptRow) {
    setViewRow(row)
    setViewContent(null)
    try {
      const r = await getRefContent(row.id)
      setViewContent({ content: r.content, sha256_match: r.sha256_match })
    } catch (e) { message.error(errText(e, 160)); setViewRow(null) }
  }

  const columns: ColumnsType<RefScriptRow> = [
    {
      title: '范例', key: 'ref', width: 240,
      render: (_, r) => (
        <div>
          <div style={{ fontWeight: 500, fontFamily: 'monospace', fontSize: 12.5 }}>{r.ref_id}</div>
          <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>{r.file_name}</div>
        </div>
      ),
    },
    {
      title: '版本', dataIndex: 'version', width: 90,
      render: (v, r) => (
        <Space size={4}>
          <span style={{ fontFamily: 'monospace' }}>v{String(v)}</span>
          {latest.has(r.id) && <Tag style={{ fontSize: 10, lineHeight: '14px', marginInlineEnd: 0 }}>最新</Tag>}
        </Space>
      ),
    },
    {
      title: '适用范围', key: 'scope', width: 220,
      render: (_, r) => {
        const s = formatRefScope(r)
        return s ? <span style={{ fontSize: 12 }}>{s}</span> : <span style={{ color: 'var(--text-3)' }}>—</span>
      },
    },
    {
      title: '状态', dataIndex: 'status', width: 90,
      render: (v) => {
        const m = refStatusMeta(String(v))
        return <Tag color={m.color}>{m.label}</Tag>
      },
    },
    {
      title: <Tooltip title="sha256(脚本文本 utf-8)——与检索文件源同口径（ADR-015）">内容哈希 ⓘ</Tooltip>,
      key: 'sha', width: 130,
      render: (_, r) => <span style={{ fontFamily: 'monospace', fontSize: 11.5, color: 'var(--text-3)' }}>{shortSha(r.content_sha256)}</span>,
    },
    {
      title: '来源', key: 'source', width: 130,
      render: (_, r) => (
        <Tooltip title={r.source_note ?? ''}>
          <span style={{ fontSize: 12 }}>{refSourceLabel(String(r.source_type))}</span>
        </Tooltip>
      ),
    },
    {
      title: '启用', key: 'switch', width: 70,
      render: (_, r) => (
        <Switch
          size="small"
          checked={r.status === 'active'}
          disabled={r.status === 'pending'}
          onChange={(on) => void patch(r, { status: on ? 'active' : 'disabled' })}
        />
      ),
    },
    {
      title: '', key: 'ops', width: 150,
      render: (_, r) => (
        <Space size={0}>
          <Button size="small" type="text" onClick={() => void openView(r)}>查看</Button>
          {r.status === 'pending' && (
            <Popconfirm
              title={`确认激活 ${r.ref_id} v${r.version}？`}
              description="激活后该版本参与检索；同范例旧版本将自动停用。"
              onConfirm={() => void confirm(r)}
            >
              <Button size="small" type="link">确认</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <Card
      title="参考代码范例库"
      extra={
        <Space>
          <Popconfirm title="从当前文件源（catalog.yaml）幂等导入？" description="内容一致→跳过；变更→新版本（待确认）；不自动激活。"
            onConfirm={() => void importCatalog()}>
            <Button size="small" loading={importing}>导入文件源</Button>
          </Popconfirm>
          <Button type="primary" size="small" onClick={() => setUploadOpen(true)}>上传登记</Button>
        </Space>
      }
      styles={{ body: { padding: 0 } }}
    >
      <Table
        rowKey="id" size="small" dataSource={rows} columns={columns}
        loading={rowsQuery.isLoading} pagination={{ pageSize: 15 }}
        locale={{ emptyText: <QueryEmpty query={rowsQuery} subject="范例登记" emptyText="暂无范例登记——可从文件源导入或上传登记（登记为 pending，确认后参与检索）" /> }}
      />
      <Modal
        title="上传登记范例脚本" open={uploadOpen} okText="登记" onOk={() => void saveUpload()}
        onCancel={() => setUploadOpen(false)} width={560} destroyOnClose
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <Upload
            accept=".py" maxCount={1} beforeUpload={(f) => { setForm((s) => ({ ...s, file: f })); return false }}
            onRemove={() => setForm((s) => ({ ...s, file: null }))}
          >
            <Button>选择 .py 脚本</Button>
          </Upload>
          <Input addonBefore="范例 ID" placeholder="小写 slug，如 towed_array_system_twin（留空取文件名）"
            value={form.ref_id} onChange={(e) => setForm((s) => ({ ...s, ref_id: e.target.value }))} />
          <Input addonBefore="名称" placeholder="展示名（可空）" value={form.title}
            onChange={(e) => setForm((s) => ({ ...s, title: e.target.value }))} />
          <Input.TextArea placeholder="描述（可空）" value={form.description} autoSize={{ minRows: 1, maxRows: 3 }}
            onChange={(e) => setForm((s) => ({ ...s, description: e.target.value }))} />
          <Space size={8}>
            <Input addonBefore="领域" placeholder="towed_array" style={{ flex: 1 }} value={form.domain}
              onChange={(e) => setForm((s) => ({ ...s, domain: e.target.value }))} />
            <Input addonBefore="规则集" placeholder="towed_array_v1" style={{ flex: 1 }} value={form.domain_rules}
              onChange={(e) => setForm((s) => ({ ...s, domain_rules: e.target.value }))} />
          </Space>
          <Select mode="tags" placeholder="声呐类型标签（标题命中权威信号）" value={form.sonar_types}
            onChange={(v) => setForm((s) => ({ ...s, sonar_types: v }))} />
          <Select mode="tags" placeholder="关键词" value={form.keywords}
            onChange={(v) => setForm((s) => ({ ...s, keywords: v }))} />
        </div>
      </Modal>
      <Modal
        title={viewRow ? `${viewRow.ref_id} v${viewRow.version} · ${viewRow.file_name}` : ''}
        open={!!viewRow} footer={null} onCancel={() => setViewRow(null)} width={860} destroyOnClose
      >
        {viewContent && !viewContent.sha256_match && (
          <div style={{ marginBottom: 8 }}>
            <Tag color="red">内容哈希与登记不符（可能被篡改或半写）</Tag>
          </div>
        )}
        <pre style={{
          maxHeight: 480, overflow: 'auto', fontSize: 12, lineHeight: 1.5,
          background: 'var(--bg-2, #f6f7f9)', padding: 12, borderRadius: 6, margin: 0,
        }}>{viewContent?.content ?? '加载中…'}</pre>
      </Modal>
    </Card>
  )
}
