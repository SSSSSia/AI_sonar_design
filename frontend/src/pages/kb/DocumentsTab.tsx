import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, App, Badge, Button, Card, Modal, Popconfirm, Select, Space, Table, Tag, Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { UploadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import {
  deleteDocument, getBuildTask, listBases, listDocuments, startBuild, startExtract,
  removeBaseDocs,
  type KbBase, type KbDocument,
} from '../../api/kb'
import { errText } from '../../api/client'
import { QueryEmpty } from '../../components/QueryEmpty'

const STATUS_META: Record<string, { color: string; text: string }> = {
  parsed: { color: 'default', text: '已解析' },
  chunked: { color: 'processing', text: '已分块' },
  extracting: { color: 'processing', text: '抽取中' },
  extracted: { color: 'processing', text: '已抽取' },
  indexed: { color: 'success', text: '已入索引' },
  failed: { color: 'error', text: '失败' },
}

/** 级联清理结果 → 人话（true/数量=已清；skipped=未启用；其余为后端 failed: 字符串）。 */
function cleanupPart(label: string, v: unknown): string {
  if (v === true) return `${label}已清理`
  if (typeof v === 'number') return `${label}已清理 ${v} 项`
  if (v === false || v == null) return `${label}未执行`
  if (v === 'skipped') return `${label}未启用，跳过`
  return `${label}清理失败（已记录日志）`
}

/** 文档行「所属库」标签（多归属 Tag 列表；PHASE-006 多对多）。 */
export function BaseTags({ doc, bases }: { doc: KbDocument; bases?: KbBase[] }) {
  const tags = doc.bases ?? []
  if (tags.length === 0) return <Tag>未归库</Tag>
  return (
    <>
      {tags.map((t) => (
        <Tag key={t.base_id} color={bases?.find((b) => b.id === t.base_id)?.is_default ? 'blue' : undefined}>
          {t.base_name}
        </Tag>
      ))}
    </>
  )
}

/** 管理页文档表（PHASE-006）：baseId 传库 id=库内视图（可移出）；'pool'=文档池全量；undefined=旧全局视图。 */
export function DocumentsTab({ baseId, pool }: { baseId?: string; pool?: boolean }) {
  const { message, modal } = App.useApp()
  const queryClient = useQueryClient()
  const [buildTaskId, setBuildTaskId] = useState<string | null>(null)

  const docsQuery = useQuery({
    queryKey: ['kb-docs', baseId ?? (pool ? 'pool' : 'all')],
    queryFn: () => listDocuments(baseId ? { baseId } : pool ? { unassigned: true } : undefined),
    refetchInterval: 10_000,
  })
  const docs: KbDocument[] = docsQuery.data?.documents ?? []
  const basesQuery = useQuery({ queryKey: ['kb-bases'], queryFn: listBases })
  const bases: KbBase[] = basesQuery.data?.bases ?? []

  const buildQuery = useQuery({
    queryKey: ['kb-build', buildTaskId],
    queryFn: () => getBuildTask(buildTaskId!),
    enabled: !!buildTaskId,
    refetchInterval: 3000,
  })
  useEffect(() => {
    const st = buildQuery.data?.state
    if (st === 'SUCCESS') {
      message.success('索引构建完成')
      setBuildTaskId(null)
      void queryClient.invalidateQueries({ queryKey: ['kb-docs'] })
    } else if (st === 'FAILURE') {
      message.error('索引构建失败：' + String(buildQuery.data?.error ?? '').slice(0, 160))
      setBuildTaskId(null)
    }
  }, [buildQuery.data?.state])

  async function beforeUpload(file: File) {
    try {
      const r = await import('../../api/kb').then((m) => m.uploadDocument(file, baseId))
      const res = r as { status?: string; ocr_pages?: number; ocr_pending?: number; note?: string; error?: string }
      if (res.status === 'failed') message.error(`解析失败：${res.error ?? ''}`)
      else message.success(`已解析${res.ocr_pages ? `，OCR 识别 ${res.ocr_pages} 页` : ''}${res.ocr_pending ? `，${res.ocr_pending} 页待补` : ''}`)
      void queryClient.invalidateQueries({ queryKey: ['kb-docs'] })
      void queryClient.invalidateQueries({ queryKey: ['kb-bases'] })
    } catch (e) {
      message.error('上传失败：' + errText(e, 120))
    }
    return false
  }

  async function remove(doc: KbDocument) {
    try {
      const r = await deleteDocument(doc.doc_id)
      const c = r.cleanup ?? {}
      modal.info({
        title: `已删除 ${doc.filename}`,
        content: `已同步清理：${cleanupPart('原件', c.minio)} · ${cleanupPart('向量索引', c.vectors)} · ${cleanupPart('图谱引用', c.graph)}`,
      })
      void queryClient.invalidateQueries({ queryKey: ['kb-docs'] })
      void queryClient.invalidateQueries({ queryKey: ['kb-bases'] })
    } catch (e) {
      message.error('删除失败：' + errText(e, 120))
    }
  }

  async function moveOut(doc: KbDocument) {
    if (!baseId) return
    try {
      const r = await removeBaseDocs(baseId, [doc.doc_id])
      message.success(`已移出本库（${r.removed} 条）；文档与其索引保留，可在「文档池」重新归库`)
      void queryClient.invalidateQueries({ queryKey: ['kb-docs'] })
      void queryClient.invalidateQueries({ queryKey: ['kb-bases'] })
    } catch (e) {
      message.error('移出失败：' + errText(e, 120))
    }
  }

  const columns: ColumnsType<KbDocument> = [
    {
      title: '文档',
      dataIndex: 'filename',
      render: (v, r) => (
        <div>
          <div style={{ fontWeight: 500 }}>{v}</div>
          <div style={{ fontSize: 11, color: 'var(--text-3)', fontFamily: 'monospace' }}>{r.doc_id} · {r.format}</div>
          {r.error && <div style={{ fontSize: 11, color: '#ef4444' }}>{r.error}</div>}
        </div>
      ),
    },
    ...(pool !== undefined
      ? [{
          title: '所属库', key: 'bases', width: 200,
          render: (_: unknown, r: KbDocument) => <BaseTags doc={r} bases={bases} />,
        } as ColumnsType<KbDocument>[number]]
      : []),
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (v) => <Badge status={(STATUS_META[v]?.color as 'success') ?? 'default'} text={STATUS_META[v]?.text ?? v} />,
    },
    { title: '分块', dataIndex: 'chunks', width: 70, render: (v) => (v ? v : <span style={{ color: 'var(--text-3)' }}>—</span>) },
    {
      title: '上传时间', dataIndex: 'uploaded_at', width: 110,
      render: (v) => <span style={{ fontSize: 11.5, color: 'var(--text-3)' }}>{v ? dayjs(v).format('MM-DD HH:mm') : '—'}</span>,
    },
    {
      title: 'OCR',
      key: 'ocr',
      width: 110,
      render: (_, r) =>
        r.ocr_pending ? <Tag color="warning">待补 {r.ocr_pending}</Tag>
          : r.ocr_pages ? <Tag color="blue">识别 {r.ocr_pages} 页</Tag> : <span style={{ color: 'var(--text-3)' }}>—</span>,
    },
    {
      title: '操作',
      key: 'act',
      width: 190,
      render: (_, r) => (
        <Space>
          {r.status !== 'indexed' && (
            <Button
              size="small"
              onClick={async () => {
                try {
                  await startExtract(r.doc_id)
                  message.success('抽取任务已启动，完成后状态自动更新')
                  void queryClient.invalidateQueries({ queryKey: ['kb-docs'] })
                } catch (e) { message.error(errText(e, 120)) }
              }}
            >抽取</Button>
          )}
          {baseId && (
            <Popconfirm title="移出本库？文档与索引保留，仅退出本库检索范围" onConfirm={() => moveOut(r)}>
              <Button size="small">移出</Button>
            </Popconfirm>
          )}
          <Popconfirm title="删除将级联清理向量与图谱来源" onConfirm={() => remove(r)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const uploadTitle = baseId ? '上传到本库' : '上传（解析保留位置）'
  return (
    <Card
      title={pool ? '文档池' : '文档'}
      extra={
        <Space>
          <Upload beforeUpload={beforeUpload} showUploadList={false} multiple>
            <Button icon={<UploadOutlined />}>{uploadTitle}</Button>
          </Upload>
          <Button
            type="primary"
            loading={!!buildTaskId}
            onClick={async () => {
              try {
                const t = await startBuild()
                setBuildTaskId(t.task_id)
                message.info('增量构建已启动（只处理尚未入索引的文档）')
              } catch (e) { message.error(errText(e, 120)) }
            }}
          >构建索引（增量）</Button>
        </Space>
      }
      styles={{ body: { padding: 0 } }}
    >
      <Alert
        type="info" showIcon style={{ margin: '10px 16px' }}
        message={
          pool
            ? '文档池收存全部上传文档（含未归库）。未归库文档可查看、预览，但暂不参与任何会话的知识检索；归入知识库即恢复。'
            : baseId
              ? '拖拽或点击「上传到本库」即建档解析并自动归属当前库；删除即级联清理，「移出」仅解除本库归属。'
              : '拖拽或点击上方「上传」即建档解析（扫描页自动 OCR）；失败或超预算页如实计 ocr_pending，不静默丢弃。删除即级联清理。'
        }
      />
      {docs.length === 0 && !docsQuery.isLoading && !docsQuery.isError ? (
        <Upload.Dragger
          beforeUpload={beforeUpload} showUploadList={false} multiple
          style={{ margin: '0 16px 14px', padding: '18px 0', background: 'var(--bg-deep)' }}
        >
          <p style={{ marginBottom: 4, color: 'var(--text-2)', fontSize: 13 }}>暂无文档——拖拽文件到此处，或点击选择</p>
          <p style={{ marginBottom: 0, color: 'var(--text-3)', fontSize: 11.5 }}>上传即建档解析（PDF / TXT / MD），解析失败如实回显</p>
        </Upload.Dragger>
      ) : (
        <Table
          rowKey="doc_id" size="small" loading={docsQuery.isLoading} dataSource={docs} columns={columns} pagination={false}
          locale={{ emptyText: <QueryEmpty query={docsQuery} subject="文档列表" /> }}
        />
      )}
    </Card>
  )
}

/** 抽取确认页签（PHASE-006：可按库过滤文档；独立导出，KnowledgePage 内用）。 */
export function ExtractionsTab({ baseId }: { baseId?: string }) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [docId, setDocId] = useState<string | null>(null)
  const [status, setStatus] = useState<'pending' | 'confirmed' | 'rejected'>('pending')
  const docsQuery = useQuery({
    queryKey: ['kb-docs', baseId ?? 'all'],
    queryFn: () => listDocuments(baseId ? { baseId } : undefined),
  })
  const docs = docsQuery.data?.documents ?? []

  const exQuery = useQuery({
    queryKey: ['kb-ex', docId, status],
    queryFn: () => import('../../api/kb').then((m) => m.listExtractions(docId!, status)),
    enabled: !!docId,
  })
  const rows = exQuery.data?.extractions ?? []

  async function decide(id: string, decision: 'confirmed' | 'rejected') {
    try {
      const { decideExtraction } = await import('../../api/kb')
      await decideExtraction(id, decision)
      void queryClient.invalidateQueries({ queryKey: ['kb-ex'] })
    } catch (e) { message.error(String(e).slice(0, 120)) }
  }

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: '类型', key: 'kind', width: 110, render: (_, r) => <Tag color="blue">{String(r.kind ?? r.type ?? '实体')}</Tag> },
    {
      title: '内容',
      key: 'content',
      render: (_, r) => <span style={{ fontSize: 12.5 }}>{String(r.label ?? r.text ?? r.summary ?? JSON.stringify(r.payload ?? r)).slice(0, 180)}</span>,
    },
    { title: '位置', key: 'loc', width: 150, render: (_, r) => <span style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--text-3)' }}>{String(r.location ?? '—')}</span> },
    {
      title: '操作',
      key: 'act',
      width: 150,
      render: (_, r) =>
        status === 'pending' ? (
          <Space>
            <Button size="small" type="primary" onClick={() => decide(String(r.extraction_id ?? r.id), 'confirmed')}>确认</Button>
            <Button size="small" danger onClick={() => decide(String(r.extraction_id ?? r.id), 'rejected')}>驳回</Button>
          </Space>
        ) : <Tag>{status}</Tag>,
    },
  ]

  return (
    <Card
      title="抽取确认"
      extra={
        <Space>
          <Select
            style={{ minWidth: 260 }} placeholder="选择文档"
            value={docId ?? undefined}
            onChange={(v) => setDocId(v)}
            options={docs.map((d) => ({ value: d.doc_id, label: d.filename }))}
          />
          <Select style={{ width: 110 }} value={status} onChange={(v) => setStatus(v)} options={[
            { value: 'pending', label: '待审' }, { value: 'confirmed', label: '已确认' }, { value: 'rejected', label: '已驳回' },
          ]} />
        </Space>
      }
      styles={{ body: { padding: 0 } }}
    >
      <Alert type="info" showIcon style={{ margin: '10px 16px' }} message="确认后的抽取才参与图谱构建（与增量构建联动）；为空如实显示。" />
      {!docId ? <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-3)' }}>先选择文档</div>
        : <Table
            rowKey={(r) => String(r.extraction_id ?? r.id ?? Math.random())} size="small" loading={exQuery.isLoading} dataSource={rows} columns={columns} pagination={{ pageSize: 15 }}
            locale={{ emptyText: exQuery.isError ? <QueryEmpty query={exQuery} subject="抽取记录" /> : undefined }}
          />}
    </Card>
  )
}

/** 新建库向导（PHASE-006 D6-10）：名称/描述 + 从文档池勾选（多归属，互不影响来源库）。 */
export function NewBaseWizard({ open, onClose, onCreated }: {
  open: boolean
  onClose: () => void
  onCreated: (b: KbBase) => void
}) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [selected, setSelected] = useState<React.Key[]>([])
  const [creating, setCreating] = useState(false)

  const docsQuery = useQuery({
    queryKey: ['kb-docs', 'wizard'],
    queryFn: () => listDocuments({}),
    enabled: open,
  })
  const docs = docsQuery.data?.documents ?? []

  function reset() {
    setName('')
    setDescription('')
    setSelected([])
  }

  async function create() {
    if (!name.trim()) {
      message.warning('请输入知识库名称')
      return
    }
    setCreating(true)
    try {
      const { createBase } = await import('../../api/kb')
      const r = await createBase({ name: name.trim(), description: description.trim() || undefined, initial_doc_ids: selected.map(String) })
      message.success(`知识库「${r.base.name}」已创建${r.initial_docs ? `，已归入 ${r.initial_docs} 份文档` : ''}`)
      void queryClient.invalidateQueries({ queryKey: ['kb-bases'] })
      void queryClient.invalidateQueries({ queryKey: ['kb-docs'] })
      reset()
      onCreated(r.base)
    } catch (e) {
      message.error('创建失败：' + errText(e, 120))
    } finally {
      setCreating(false)
    }
  }

  function handleClose() {
    reset()
    onClose()
  }

  return (
    <Modal
      title="新建知识库"
      open={open}
      onOk={create}
      onCancel={handleClose}
      okText="创建"
      confirmLoading={creating}
      okButtonProps={{ disabled: !name.trim() }}
      width={640}
      destroyOnClose
    >
      <div style={{ display: 'grid', gap: 12, paddingTop: 4 }}>
        <div>
          <div style={{ fontSize: 12.5, marginBottom: 4 }}>名称 <span style={{ color: '#ef4444' }}>*</span></div>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={128}
            placeholder="如：拖曳阵总体设计资料"
            style={{ width: '100%', border: '1px solid #d0d7de', borderRadius: 6, padding: '6px 10px', fontSize: 13 }}
          />
        </div>
        <div>
          <div style={{ fontSize: 12.5, marginBottom: 4 }}>描述</div>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={512}
            placeholder="一句话说明该库收存什么（可空）"
            style={{ width: '100%', border: '1px solid #d0d7de', borderRadius: 6, padding: '6px 10px', fontSize: 13 }}
          />
        </div>
        <div>
          <div style={{ fontSize: 12.5, marginBottom: 4 }}>
            归入文档 <span style={{ color: 'var(--text-3)', fontSize: 11 }}>（可多选；同一文档可同时属于多个库，互不影响）</span>
          </div>
          <div style={{ maxHeight: 240, overflow: 'auto', border: '1px solid var(--border)', borderRadius: 6 }}>
            {docs.length === 0 ? (
              <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-3)', fontSize: 12 }}>
                暂无已上传文档——创建后可在库内直接上传
              </div>
            ) : (
              docs.map((d) => (
                <label
                  key={d.doc_id}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', fontSize: 12.5, borderBottom: '1px solid var(--hairline)', cursor: 'pointer' }}
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(d.doc_id)}
                    onChange={(e) => setSelected((prev) => e.target.checked ? [...prev, d.doc_id] : prev.filter((k) => k !== d.doc_id))}
                  />
                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.filename}</span>
                  <span style={{ color: 'var(--text-3)', fontSize: 11 }}>{d.bases?.length ? `已属 ${d.bases.length} 个库` : '未归库'}</span>
                </label>
              ))
            )}
          </div>
        </div>
      </div>
    </Modal>
  )
}
