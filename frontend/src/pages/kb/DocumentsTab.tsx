import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, App, Badge, Button, Card, Popconfirm, Select, Space, Table, Tag, Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { UploadOutlined } from '@ant-design/icons'
import {
  deleteDocument, getBuildTask, listDocuments, startBuild, startExtract,
  type KbDocument,
} from '../../api/kb'

const STATUS_META: Record<string, { color: string; text: string }> = {
  parsed: { color: 'default', text: 'parsed' },
  chunked: { color: 'processing', text: 'chunked' },
  extracting: { color: 'processing', text: 'extracting' },
  extracted: { color: 'processing', text: 'extracted' },
  indexed: { color: 'success', text: 'indexed' },
  failed: { color: 'error', text: 'failed' },
}

/** 文档库页签：上传（同步解析）、列表、删除级联、增量构建。 */
export function DocumentsTab() {
  const { message, modal } = App.useApp()
  const queryClient = useQueryClient()
  const [buildTaskId, setBuildTaskId] = useState<string | null>(null)

  const docsQuery = useQuery({ queryKey: ['kb-docs'], queryFn: listDocuments, refetchInterval: 10_000 })
  const docs: KbDocument[] = docsQuery.data?.documents ?? []

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
      const r = await import('../../api/kb').then((m) => m.uploadDocument(file))
      const res = r as { status?: string; ocr_pages?: number; ocr_pending?: number; note?: string; error?: string }
      if (res.status === 'failed') message.error(`解析失败：${res.error ?? ''}`)
      else message.success(`已解析${res.ocr_pages ? `，OCR 识别 ${res.ocr_pages} 页` : ''}${res.ocr_pending ? `，${res.ocr_pending} 页待补` : ''}`)
      void queryClient.invalidateQueries({ queryKey: ['kb-docs'] })
    } catch (e) {
      message.error('上传失败：' + String(e).slice(0, 120))
    }
    return false
  }

  async function remove(doc: KbDocument) {
    try {
      const r = await deleteDocument(doc.doc_id)
      const c = r.cleanup ?? {}
      modal.info({
        title: `已删除 ${doc.filename}`,
        content: `级联清理：MinIO ${JSON.stringify(c.minio ?? '—')} · 向量 ${JSON.stringify(c.vectors ?? '—')} · 图谱 ${JSON.stringify(c.graph ?? '—')}`,
      })
      void queryClient.invalidateQueries({ queryKey: ['kb-docs'] })
    } catch (e) {
      message.error('删除失败：' + String(e).slice(0, 120))
    }
  }

  const columns: ColumnsType<KbDocument> = [
    {
      title: '文档',
      dataIndex: 'filename',
      render: (v, r) => (
        <div>
          <div style={{ fontWeight: 500 }}>{v}</div>
          <div style={{ fontSize: 11, color: '#8b949e', fontFamily: 'monospace' }}>{r.doc_id} · {r.format}</div>
          {r.error && <div style={{ fontSize: 11, color: '#c92a2a' }}>{r.error}</div>}
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (v) => <Badge status={(STATUS_META[v]?.color as 'success') ?? 'default'} text={STATUS_META[v]?.text ?? v} />,
    },
    { title: '分块', dataIndex: 'chunks', width: 70 },
    {
      title: 'OCR',
      key: 'ocr',
      width: 110,
      render: (_, r) =>
        r.ocr_pending ? <Tag color="warning">待补 {r.ocr_pending}</Tag>
          : r.ocr_pages ? <Tag color="blue">识别 {r.ocr_pages} 页</Tag> : <span style={{ color: '#8b949e' }}>—</span>,
    },
    {
      title: '操作',
      key: 'act',
      width: 170,
      render: (_, r) => (
        <Space>
          {r.status !== 'indexed' && (
            <Button
              size="small"
              onClick={async () => {
                try {
                  const t = await startExtract(r.doc_id)
                  message.success('抽取任务已启动：' + t.task_id.slice(0, 12))
                  void queryClient.invalidateQueries({ queryKey: ['kb-docs'] })
                } catch (e) { message.error(String(e).slice(0, 120)) }
              }}
            >抽取</Button>
          )}
          <Popconfirm title="删除将级联清理向量与图谱来源" onConfirm={() => remove(r)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Card
      title="文档库"
      extra={
        <Space>
          <Upload beforeUpload={beforeUpload} showUploadList={false} multiple>
            <Button icon={<UploadOutlined />}>上传（解析保留位置）</Button>
          </Upload>
          <Button
            type="primary"
            loading={!!buildTaskId}
            onClick={async () => {
              try {
                const t = await startBuild()
                setBuildTaskId(t.task_id)
                message.info('增量构建已启动（只处理未 indexed 文档）')
              } catch (e) { message.error(String(e).slice(0, 120)) }
            }}
          >构建索引（增量）</Button>
        </Space>
      }
      styles={{ body: { padding: 0 } }}
    >
      <Alert
        type="info" showIcon style={{ margin: '10px 16px' }}
        message="扫描页自动 OCR（PaddleOCR-VL-1.5，预算内自动识别）；失败或超预算页如实计 ocr_pending，不静默丢弃。删除即级联清理。"
      />
      <Table rowKey="doc_id" size="small" loading={docsQuery.isLoading} dataSource={docs} columns={columns} pagination={false} />
    </Card>
  )
}

/** 抽取确认页签（独立导出，KnowledgePage 内用）。 */
export function ExtractionsTab() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [docId, setDocId] = useState<string | null>(null)
  const [status, setStatus] = useState<'pending' | 'confirmed' | 'rejected'>('pending')
  const docsQuery = useQuery({ queryKey: ['kb-docs'], queryFn: listDocuments })
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
    { title: '位置', key: 'loc', width: 150, render: (_, r) => <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#8b949e' }}>{String(r.location ?? '—')}</span> },
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
      {!docId ? <div style={{ padding: 32, textAlign: 'center', color: '#8b949e' }}>先选择文档</div>
        : <Table rowKey={(r) => String(r.extraction_id ?? r.id ?? Math.random())} size="small" loading={exQuery.isLoading} dataSource={rows} columns={columns} pagination={{ pageSize: 15 }} />}
    </Card>
  )
}
