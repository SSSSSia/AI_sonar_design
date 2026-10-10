import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Alert, Button, Card, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Tooltip, Upload } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  createRegistryRow, deleteRegistryRow, importRegistryFile, listRegistry, updateRegistryRow,
  type RegistryKind, type RegistryRow,
} from '../../api/kb'
import { QueryEmpty } from '../../components/QueryEmpty'
import { errText } from '../../api/client'

const KIND_TAB: { value: RegistryKind; label: string }[] = [
  { value: 'formulas', label: '公式库' },
  { value: 'constraints', label: '约束库' },
  { value: 'standards', label: '军标库' },
]

const EXPR_HELP = 'SymPy 布尔表达式（如 outer_diameter <= 325）。符号取 IR2 参数 derivation 记录的「符号=数值」+ value（命中参数登记值）；留空 = 仅登记提示性核对'

interface StdFormState {
  visible: boolean
  editId?: string
  code: string
  name: string
  appliesTo: string
  requirement: string
  expr: string
  confidence: string
}

const STD_FORM_EMPTY: StdFormState = {
  visible: false, code: '', name: '', appliesTo: '', requirement: '', expr: '', confidence: 'low',
}

/** 三库管理：公式/约束/军标 CRUD + 启停 + 可信度（A4 校验优先读库）。
 * 军标库（T9.3）：完整字段管理（编号/适用对象/条款/判定表达式），供内部人员自行录入补充。 */
export function RegistryTab() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [kind, setKind] = useState<RegistryKind>('formulas')
  const [adding, setAdding] = useState(false)
  const [newKey, setNewKey] = useState('')
  const [newName, setNewName] = useState('')
  const [newExpr, setNewExpr] = useState('')
  const [stdForm, setStdForm] = useState<StdFormState>(STD_FORM_EMPTY)
  // T11.4 批量导入：xlsx/csv 上传 → dry_run 预览（问题如实带行号）→ 确认落库
  const [importOpen, setImportOpen] = useState(false)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<{ rows: Record<string, unknown>[]; problems: { row: number; error: string }[] } | null>(null)
  const [importing, setImporting] = useState(false)

  const rowsQuery = useQuery({ queryKey: ['kb-registry', kind], queryFn: () => listRegistry(kind) })
  const rows: RegistryRow[] = rowsQuery.data?.rows ?? []

  async function patch(row: RegistryRow, fields: Record<string, unknown>) {
    const id = String(row.row_id ?? row.id ?? row.key)
    try {
      await updateRegistryRow(kind, id, fields)
      void queryClient.invalidateQueries({ queryKey: ['kb-registry', kind] })
    } catch (e) { message.error(errText(e, 140)) }
  }

  function openStdNew() {
    setStdForm({ ...STD_FORM_EMPTY, visible: true, confidence: 'low' })
  }

  function openStdEdit(r: RegistryRow) {
    setStdForm({
      visible: true,
      editId: String(r.row_id ?? r.id ?? ''),
      code: String(r.code ?? ''),
      name: String(r.name ?? ''),
      appliesTo: String(r.applies_to ?? ''),
      requirement: String(r.requirement ?? ''),
      expr: String(r.expr ?? ''),
      confidence: String(r.confidence ?? 'low'),
    })
  }

  async function saveStd() {
    const f = stdForm
    if (!f.code.trim() || !f.name.trim() || !f.appliesTo.trim()) {
      message.warning('编号、名称、适用对象必填')
      return
    }
    const fields: Record<string, unknown> = {
      code: f.code.trim(), name: f.name.trim(), applies_to: f.appliesTo.trim(),
      requirement: f.requirement.trim(), expr: f.expr.trim() || null,
      confidence: f.confidence,
    }
    try {
      if (f.editId) await updateRegistryRow('standards', f.editId, fields)
      else await createRegistryRow('standards', { ...fields, enabled: true })
      message.success(f.editId ? '已保存' : '已新增（启停即时生效）')
      setStdForm(STD_FORM_EMPTY)
      void queryClient.invalidateQueries({ queryKey: ['kb-registry', 'standards'] })
    } catch (e) { message.error(errText(e, 180)) }
  }

  const columns: ColumnsType<RegistryRow> =
    kind === 'standards'
      ? [
        {
          title: '编号', key: 'code', width: 130,
          render: (_, r) => <span style={{ fontFamily: 'monospace', fontSize: 11.5 }}>{String(r.code ?? r.row_id ?? '—')}</span>,
        },
        { title: '名称', key: 'name', render: (_, r) => <span style={{ fontWeight: 500 }}>{String(r.name ?? '—')}</span> },
        {
          title: '适用对象', key: 'applies_to', width: 110,
          render: (_, r) => {
            const a = String(r.applies_to ?? '')
            return a ? <Tag>{a}</Tag> : <span style={{ color: '#8b949e' }}>—</span>
          },
        },
        {
          title: <Tooltip title={EXPR_HELP}>判定表达式 ⓘ</Tooltip>,
          key: 'expr',
          render: (_, r) => {
            const e = String(r.expr ?? '').trim()
            return e
              ? <span style={{ fontFamily: 'monospace', fontSize: 11.5, color: '#1f6feb' }}>{e}</span>
              : <span style={{ color: '#8b949e' }}>仅登记（提示性）</span>
          },
        },
        {
          title: '可信度', dataIndex: 'confidence', width: 90,
          render: (v) => (v ? <Tag color={v === 'high' ? 'green' : v === 'low' ? 'orange' : 'default'}>{String(v)}</Tag> : '—'),
        },
        {
          title: '启用', dataIndex: 'enabled', width: 70,
          render: (v, r) => <Switch size="small" checked={!!v} onChange={(on) => patch(r, { enabled: on })} />,
        },
        {
          title: '', key: 'ops', width: 110,
          render: (_, r) => (
            <Space size={0}>
              <Button size="small" type="text" onClick={() => openStdEdit(r)}>编辑</Button>
              <Popconfirm title="确认删除该条目？" onConfirm={async () => {
                try {
                  await deleteRegistryRow(kind, String(r.row_id ?? r.id))
                  void queryClient.invalidateQueries({ queryKey: ['kb-registry', kind] })
                } catch (e) { message.error(errText(e, 140)) }
              }}>
                <Button size="small" danger type="text">删除</Button>
              </Popconfirm>
            </Space>
          ),
        },
      ]
      : [
        {
          title: '编号/键', key: 'id', width: 130,
          render: (_, r) => <span style={{ fontFamily: 'monospace', fontSize: 11.5 }}>{String(r.key ?? r.row_id ?? r.id ?? '—')}</span>,
        },
        { title: '名称', key: 'name', render: (_, r) => <span style={{ fontWeight: 500 }}>{String(r.name ?? r.label ?? r.key ?? '—')}</span> },
        ...(kind === 'formulas' ? [{
          title: '单位', key: 'unit', width: 70,
          render: (_: unknown, r: RegistryRow) => {
            const u = String(r.unit ?? '').trim()
            return u && u !== '-' ? <span style={{ fontFamily: 'monospace', fontSize: 11.5 }}>{u}</span> : <span style={{ color: '#8b949e' }}>—</span>
          },
        }] : []),
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
              } catch (e) { message.error(errText(e, 140)) }
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
          <Space>
            <Button onClick={() => { setImportOpen(true); setPreview(null); setImportFile(null) }}>批量导入</Button>
            <Button type="primary" onClick={openStdNew}>＋ 新增军标条目</Button>
          </Space>
        ) : (
          <Button type="primary" onClick={() => setAdding(true)}>＋ 新增条目</Button>
        )
      }
      styles={{ body: { padding: 0 } }}
    >
      {kind === 'standards' && rows.length === 0 && (
        <div style={{ margin: '10px 16px', borderLeft: '3px solid #d97706', background: '#fdf3e3', padding: '8px 12px', fontSize: 12, borderRadius: 4 }}>
          军标库空态（如实）：当前 0 条，A4 对 standard 参数相关检查如实标「待补」；录入带判定表达式的条目后自动参与校验（首批演示条目见 deploy/seed/demo_standards.sql）。
        </div>
      )}
      <Table
        rowKey={(r) => String(r.row_id ?? r.id ?? r.key)} size="small" loading={rowsQuery.isLoading} dataSource={rows} columns={columns} pagination={{ pageSize: 15 }}
        locale={{ emptyText: <QueryEmpty query={rowsQuery} subject="三库条目" emptyText={kind === 'standards' ? '军标库暂无条目——录入后自动参与 A4 校验' : '暂无条目——右上角新增'} /> }}
      />

      {kind !== 'standards' && adding && (
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
                    await createRegistryRow(kind, fields)
                    message.success('已新增')
                    setAdding(false); setNewKey(''); setNewName(''); setNewExpr('')
                    void queryClient.invalidateQueries({ queryKey: ['kb-registry', kind] })
                  } catch (e) { message.error(errText(e, 140)) }
                }}
              >保存</Button>
            </Space>
          </Space>
        </Card>
      )}

      <Modal
        title={stdForm.editId ? '编辑军标条目' : '新增军标条目'}
        open={stdForm.visible}
        onCancel={() => setStdForm(STD_FORM_EMPTY)}
        onOk={saveStd}
        okText="保存"
        cancelText="取消"
        width={560}
      >
        <Space direction="vertical" style={{ width: '100%', marginTop: 12 }}>
          <Input placeholder="编号（如 GJB xxx-xx 或 DEMO-STD-00x）" value={stdForm.code}
            onChange={(e) => setStdForm({ ...stdForm, code: e.target.value })} />
          <Input placeholder="名称（如 阵元间距下限）" value={stdForm.name}
            onChange={(e) => setStdForm({ ...stdForm, name: e.target.value })} />
          <Input placeholder="适用对象（参数名包含匹配，如：阵元间距 / 灵敏度）" value={stdForm.appliesTo}
            onChange={(e) => setStdForm({ ...stdForm, appliesTo: e.target.value })} />
          <Input.TextArea placeholder="条款要求描述（来源与条款号，供审阅关人工核对）" value={stdForm.requirement}
            onChange={(e) => setStdForm({ ...stdForm, requirement: e.target.value })} rows={2} />
          <Input.TextArea placeholder="判定表达式（SymPy 布尔式，如 outer_diameter <= 325；留空 = 仅登记）" value={stdForm.expr}
            onChange={(e) => setStdForm({ ...stdForm, expr: e.target.value })} rows={2} />
          <Tooltip title={EXPR_HELP} placement="topLeft">
            <div style={{ fontSize: 12, color: '#8b949e' }}>{EXPR_HELP}</div>
          </Tooltip>
          <Select
            value={stdForm.confidence}
            onChange={(v) => setStdForm({ ...stdForm, confidence: v })}
            style={{ width: 200 }}
            options={[
              { value: 'high', label: '可信度 high（授权原文）' },
              { value: 'medium', label: '可信度 medium' },
              { value: 'low', label: '可信度 low（演示/待核）' },
            ]}
          />
        </Space>
      </Modal>

      <Modal
        title={`批量导入（${KIND_TAB.find((k) => k.value === kind)?.label ?? kind}）`}
        open={importOpen}
        onCancel={() => setImportOpen(false)}
        width={680}
        footer={[
          <Button key="cancel" onClick={() => setImportOpen(false)}>关闭</Button>,
          <Button key="do" type="primary" loading={importing} disabled={!preview || (preview?.problems.length ?? 0) > 0}
            onClick={async () => {
              if (!importFile) return
              setImporting(true)
              try {
                const r = await importRegistryFile(kind, importFile, false)
                message.success(`导入完成：新增 ${r.inserted ?? 0}，更新 ${r.updated ?? 0}，失败 ${(r.errors ?? []).length}`)
                if ((r.errors ?? []).length > 0) {
                  message.warning(r.errors!.slice(0, 3).map((e) => `第${e.row}行: ${e.error}`).join('；'))
                }
                setImportOpen(false)
                void queryClient.invalidateQueries({ queryKey: ['kb-registry', kind] })
              } catch (e) { message.error(errText(e, 200)) } finally { setImporting(false) }
            }}>
            {preview ? `确认导入（${preview.rows.length} 行）` : '先上传文件预览'}
          </Button>,
        ]}
      >
        <Space direction="vertical" style={{ width: '100%', marginTop: 12 }} size={12}>
          <Alert type="info" showIcon
            message="上传 xlsx / csv（首行为列头，直接用字段名：code / name / applies_to / requirement / expr / confidence / enabled；enabled 支持 是/否/启用/停用/true/false）。按编号（code）upsert：已存在则更新，新增则插入。真实 GJB 标准文本经授权后由此入口规模化录入。" />
          <Upload.Dragger
            maxCount={1}
            accept=".xlsx,.csv"
            beforeUpload={(f) => {
              setImportFile(f); setPreview(null)
              importRegistryFile(kind, f, true)
                .then((r) => setPreview({ rows: r.rows, problems: r.problems }))
                .catch((e) => message.error(errText(e, 200)))
              return false
            }}
            onRemove={() => { setImportFile(null); setPreview(null) }}
            fileList={importFile ? [{ uid: '1', name: importFile.name } as never] : []}
          >
            <p className="ant-upload-text" style={{ fontSize: 13 }}>点击或拖拽 xlsx / csv 到此区域</p>
            <p className="ant-upload-hint" style={{ fontSize: 12 }}>单批上限 500 行；预览通过后点「确认导入」</p>
          </Upload.Dragger>
          {preview && (
            <Alert
              type={(preview.problems.length ?? 0) > 0 ? 'warning' : 'success'}
              showIcon
              message={`解析 ${preview.rows.length} 行，问题 ${preview.problems.length} 个`}
              description={(preview.problems.length ?? 0) > 0
                ? preview.problems.slice(0, 5).map((p) => `第${p.row}行: ${p.error}`).join('；')
                : '校验通过，可确认导入'}
            />
          )}
        </Space>
      </Modal>
    </Card>
  )
}
