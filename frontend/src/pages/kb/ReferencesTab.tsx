import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Button, Card, Empty, Image, Input, Modal, Popconfirm, Select, Space, Spin, Switch, Table, Tag, Tooltip, Upload } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  confirmRefScript, depositFromRecord, getRefContent, getRefViewsUrl, importRefCatalog,
  listDepositCandidates, listRefScripts, patchRefScript, searchRefByImage, uploadRefScript,
  type DepositCandidate, type DepositBlocked, type RefImageMatch, type RefScriptRow,
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
  const [depositOpen, setDepositOpen] = useState(false)
  const [depositScanning, setDepositScanning] = useState(false)
  const [depositing, setDepositting] = useState<string | null>(null) // 正在沉淀的 record_id
  const [depositData, setDepositData] = useState<{
    candidates: DepositCandidate[]; blocked: DepositBlocked[]
  } | null>(null)
  const [viewRow, setViewRow] = useState<RefScriptRow | null>(null)
  const [viewContent, setViewContent] = useState<{ content: string; sha256_match: boolean } | null>(null)
  // T17.6（ADR-016 §5）：图片检索（首个图片组件；caption 回显=人工核对入口）
  const [imageOpen, setImageOpen] = useState(false)
  const [imageFile, setImageFile] = useState<File | null>(null)
  const [imagePreview, setImagePreview] = useState<string | null>(null)
  const [imageSearching, setImageSearching] = useState(false)
  const [imageResult, setImageResult] = useState<{ matches: RefImageMatch[]; caption: string } | null>(null)
  const [form, setForm] = useState({
    file: null as File | null, ref_id: '', title: '', description: '', domain: '', domain_rules: '',
    sonar_types: [] as string[], keywords: [] as string[],
  })

  const rowsQuery = useQuery({ queryKey: ['kb-ref-scripts'], queryFn: () => listRefScripts() })
  const rows: RefScriptRow[] = rowsQuery.data?.rows ?? []
  const latest = latestVersionIds(rows)

  // T17.6 三视图（ADR-016 §5 读侧出口）：随查看 Modal 拉取；null=暂无（404 如实呈现）
  const viewsQuery = useQuery({
    queryKey: ['kb-ref-views', viewRow?.id],
    queryFn: () => getRefViewsUrl(viewRow!.id),
    enabled: !!viewRow,
    staleTime: 5 * 60_000,
    retry: false,
  })

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

  /** B3 候选沉淀（T16.3，ADR-015 v1.1 §9）：扫描→逐条沉淀（真沙箱重放在服务端）→pending 待确认。 */
  async function openDeposit() {
    setDepositOpen(true)
    setDepositScanning(true)
    setDepositData(null)
    try {
      setDepositData(await listDepositCandidates())
    } catch (e) { message.error(errText(e, 180)); setDepositOpen(false) } finally { setDepositScanning(false) }
  }

  async function doDeposit(cand: DepositCandidate) {
    setDepositting(cand.record_id)
    try {
      const r = await depositFromRecord(cand.session_id, cand.record_id)
      if (r.already) {
        message.info(`该内容已沉淀在库（${r.ref_id} v${r.row.version}，${r.row.status}）——重复事件不重复入库`)
      } else {
        message.success(`已沉淀为候选 ${r.ref_id} v${r.row.version}（pending）——请确认后参与检索`)
      }
      void queryClient.invalidateQueries({ queryKey: ['kb-ref-scripts'] })
      void openDepositRefresh()
    } catch (e) { message.error(errText(e, 200)) } finally { setDepositting(null) }
  }

  async function openDepositRefresh() {
    // 沉淀后刷新候选（幂等对照与 already 状态变化）
    try { setDepositData(await listDepositCandidates()) } catch { /* 扫描失败保留现状 */ }
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

  /** T17.6 图片检索（ADR-016 §5）：装备图 → VLM caption → 视觉向量同空间检索；
   * caption 回显=人工核对入口；flag 关（409）/瞬态失败如实提示。 */
  async function runImageSearch() {
    if (!imageFile) { message.warning('请先选择装备图片'); return }
    setImageSearching(true)
    setImageResult(null)
    try {
      const r = await searchRefByImage(imageFile)
      setImageResult({ matches: r.matches, caption: r.caption })
      if (!r.matches.length) message.info(r.detail || '视觉检索无命中（如实呈现）')
    } catch (e) { message.error(errText(e, 200)) } finally { setImageSearching(false) }
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
          <Button size="small" onClick={() => { setImageOpen(true); setImageResult(null); setImageFile(null); setImagePreview(null) }}>
            图片检索
          </Button>
          <Button size="small" onClick={() => void openDeposit()}>批准沉淀</Button>
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
        <div style={{ fontSize: 12.5, color: 'var(--text-2)', marginBottom: 6 }}>三视图（iso / side / top）</div>
        {viewsQuery.isLoading ? (
          <div style={{ marginBottom: 12 }}><Spin size="small" /> <span style={{ fontSize: 12, color: 'var(--text-3)' }}>加载三视图…</span></div>
        ) : viewsQuery.data ? (
          <Image
            src={viewsQuery.data} alt="三视图"
            width="100%" style={{ maxHeight: 320, objectFit: 'contain', borderRadius: 6, border: '1px solid var(--border)', marginBottom: 12 }}
          />
        ) : (
          <div style={{ fontSize: 12, color: 'var(--text-3)', marginBottom: 12 }}>
            暂无三视图（视觉通道未生成——REF_VISUAL_ENABLED 关闭或沙箱/渲染未产出，A-16 如实）
          </div>
        )}
        <div style={{ fontSize: 12.5, color: 'var(--text-2)', marginBottom: 6 }}>脚本正文</div>
        <pre style={{
          maxHeight: 480, overflow: 'auto', fontSize: 12, lineHeight: 1.5,
          background: 'var(--bg-2, #f6f7f9)', padding: 12, borderRadius: 6, margin: 0,
        }}>{viewContent?.content ?? '加载中…'}</pre>
      </Modal>
      <Modal
        title="图片检索范例（装备图 → VLM 描述 → 视觉向量检索）"
        open={imageOpen} footer={null} onCancel={() => setImageOpen(false)} width={720} destroyOnClose
      >
        <div style={{ fontSize: 12.5, color: 'var(--text-2)', marginBottom: 10 }}>
          上传装备照片/草图/三视图，服务端生成中文描述后与范例视觉索引同空间检索；
          命中的 caption 全文回显（入库描述，人工可核对，A-16）。
        </div>
        <Upload.Dragger
          accept="image/*" maxCount={1} showUploadList={false}
          beforeUpload={(f) => {
            setImageFile(f)
            setImagePreview(URL.createObjectURL(f))
            setImageResult(null)
            return false
          }}
          style={{ background: 'var(--card-deep, rgba(255,255,255,.02))' }}
        >
          {imagePreview ? (
            <Image src={imagePreview} alt="查询图预览" height={140} style={{ objectFit: 'contain' }} preview={false} />
          ) : (
            <>点击或拖拽图片到此处（png/jpg）</>
          )}
        </Upload.Dragger>
        <div style={{ marginTop: 10 }}>
          <Button type="primary" size="small" loading={imageSearching} disabled={!imageFile} onClick={() => void runImageSearch()}>
            检索
          </Button>
        </div>
        {imageResult && (
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {imageResult.matches.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无命中（如实呈现）" />
            ) : imageResult.matches.map((m) => (
              <div key={m.ref_id} style={{
                border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px',
                background: 'var(--card-deep, rgba(255,255,255,.02))',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span className="mono" style={{ fontSize: 11, color: 'var(--accent)', background: 'var(--accent-bg)', borderRadius: 4, padding: '1px 6px' }}>
                    {m.score.toFixed(3)}
                  </span>
                  <span style={{ fontSize: 12.5, fontFamily: 'monospace' }}>{m.ref_id}</span>
                  {m.active ? <Tag color="green" style={{ fontSize: 11 }}>active</Tag> : <Tag style={{ fontSize: 11 }}>非 active</Tag>}
                  <span style={{ flex: 1 }} />
                  {(() => {
                    const row = rows.find((r) => r.ref_id === m.ref_id && r.status === 'active')
                    return row ? <Button size="small" onClick={() => void openView(row)}>查看</Button> : null
                  })()}
                </div>
                <div style={{ marginTop: 6, fontSize: 12, color: 'var(--text-2)', lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>
                  {m.caption}
                </div>
              </div>
            ))}
          </div>
        )}
      </Modal>
      <Modal
        title="批准沉淀（gate2 真实批准 → pending 范例候选）" open={depositOpen} footer={null}
        onCancel={() => setDepositOpen(false)} width={860} destroyOnClose
      >
        <div style={{ fontSize: 12.5, color: 'var(--text-2)', marginBottom: 10 }}>
          沉淀源=真实 gate2 批准记录（组合凭证在手；finalize 不算）；沉淀前服务端复核组合有效性、
          IR4 结论（fail 不沉淀）并真沙箱重放——不合格不产生候选；同内容幂等不重复入库；
          落库一律 pending，须「确认」后才参与检索。
        </div>
        <Table
          rowKey="record_id" size="small" loading={depositScanning}
          dataSource={depositData?.candidates ?? []}
          pagination={false}
          locale={{ emptyText: depositScanning ? '扫描中…' : '暂无可沉淀候选（需存在 gate2 批准且组合有效）' }}
          columns={[
            {
              title: '会话', key: 'sess', render: (_: unknown, c: DepositCandidate) => (
                <div>
                  <div style={{ fontSize: 12.5 }}>{c.session_title || c.session_id.slice(0, 8)}</div>
                  <div style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--text-3)' }}>
                    {c.session_id.slice(0, 8)}… · 记录 {c.record_id.slice(0, 8)}…
                  </div>
                </div>
              ),
            },
            { title: '批准时间', dataIndex: 'decided_at', width: 140, render: (v: string | null) => (v ? v.slice(0, 16).replace('T', ' ') : '—') },
            { title: 'IR4', dataIndex: 'ir4_overall', width: 90, render: (v: string) => (v ? <Tag color={v === 'pass' ? 'green' : v === 'fail' ? 'red' : 'orange'}>{v}</Tag> : '—') },
            {
              title: '候选', key: 'cand', render: (_: unknown, c: DepositCandidate) => (
                <Space size={6}>
                  <span style={{ fontFamily: 'monospace', fontSize: 11.5 }}>{c.ref_id}</span>
                  {c.already_deposited && (
                    <Tooltip title={`既有行 v${c.existing_row?.version ?? '?'}（${c.existing_row?.status ?? '?'}）`}>
                      <Tag color="blue">已在库</Tag>
                    </Tooltip>
                  )}
                </Space>
              ),
            },
            {
              title: '操作', width: 120, key: 'op',
              render: (_: unknown, c: DepositCandidate) => (
                <Popconfirm
                  title="沉淀该批准会话？"
                  description="服务端将真沙箱重放脚本（失败不落行）；落库为 pending 待确认。"
                  onConfirm={() => void doDeposit(c)}
                  disabled={!!depositing}
                >
                  <Button size="small" loading={depositing === c.record_id}>
                    {c.already_deposited ? '再次沉淀' : '沉淀'}
                  </Button>
                </Popconfirm>
              ),
            },
          ]}
        />
        {(depositData?.blocked?.length ?? 0) > 0 && (
          <div style={{ marginTop: 12 }}>
            <div style={{ fontSize: 12.5, color: 'var(--text-2)', marginBottom: 6 }}>
              不可沉淀（如实报因，A-16）：
            </div>
            <Table
              rowKey="record_id" size="small"
              dataSource={depositData?.blocked ?? []} pagination={false}
              columns={[
                { title: '会话', key: 'sess', render: (_: unknown, b: DepositBlocked) => (b.session_title || b.session_id.slice(0, 8)) },
                { title: '原因', dataIndex: 'reason', width: 150, render: (v: string) => <Tag color="warning">{v}</Tag> },
                {
                  title: '明细', key: 'detail', render: (_: unknown, b: DepositBlocked) => (
                    <span style={{ fontSize: 11.5, color: 'var(--text-3)' }}>
                      {Array.isArray(b.detail) ? b.detail.join('、') : (b.detail ? String(b.detail) : '—')}
                    </span>
                  ),
                },
              ]}
            />
          </div>
        )}
      </Modal>
    </Card>
  )
}
