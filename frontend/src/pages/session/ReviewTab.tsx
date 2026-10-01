import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, App as AntdApp, Button, Card, Input, Modal, Radio, Space, Table, Tag, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  postReview, type Ir1, type Ir2, type Ir2Param, type Ir3, type Ir3Feature, type Ir4, type ReviewRecord,
} from '../../api/workflow'
import type { ParamProposal } from '../../api/cadAgent'
import { ApiError } from '../../api/client'
import { confirmParamProposals, ignoreParamProposal, listParamProposals, listTunes } from '../../api/cadAgent'
import { summarizeTunes } from './tuneSummary'
import { ParamTable, parseKbRef } from '../../components/ParamTable'
import { TraceDrawer } from '../../components/TraceDrawer'
import { KbRefDrawer } from '../../components/KbRefDrawer'
import { ReviewAnnotation } from '../../components/ReviewAnnotation'
import { hasAnyRole, useAuthStore } from '../../stores/authStore'
import { ir1Degraded, normalizeMissingItems, gateLabel, stageLabel } from '../../lib/stageLabel'

const GATE_TITLE: Record<string, string> = { gate1: '审阅关①（IR2 参数方案）', gate2: '审阅关②（IR3 + IR4）' }

/** E1 参数变更提案卡（T11.3）：微调定稿实测几何 vs 登记参数的偏差，人工确认后回写 IR2
 *  （value+derivation 同步）→ IR4/IR5 定向失效 → A4 重算 → 回审阅关②复审。 */
function ProposalCard({ sessionId }: { sessionId: string }) {
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const proposalsQuery = useQuery({
    queryKey: ['e1-proposals', sessionId],
    queryFn: () => listParamProposals(sessionId),
    refetchInterval: 30_000,
  })
  const proposals = proposalsQuery.data?.proposals ?? []
  if (proposalsQuery.isLoading || proposals.length === 0) return null
  const pending = proposals.filter((p) => p.status === 'proposed')

  async function apply(ids: string[]) {
    setBusy(true)
    try {
      const r = await confirmParamProposals(sessionId, ids)
      message.success(`已回写 IR2 v${r.ir2_version}（${r.applied} 项参数），A4 重算后将回到审阅关②复审`)
      void queryClient.invalidateQueries({ queryKey: ['ir', sessionId] })
      void queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
      void queryClient.invalidateQueries({ queryKey: ['e1-proposals', sessionId] })
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : 'E1 回写失败')
    } finally {
      setBusy(false)
    }
  }

  async function ignore(id: string) {
    setBusy(true)
    try {
      await ignoreParamProposal(id)
      void queryClient.invalidateQueries({ queryKey: ['e1-proposals', sessionId] })
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  const columns: ColumnsType<ParamProposal> = [
    { title: '参数', dataIndex: 'param_name', width: 150, render: (v, r) => <span>{v}<Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 6 }}>{r.param_id}</Typography.Text></span> },
    { title: '轮次', dataIndex: 'round_no', width: 60, render: (v) => `r${v}` },
    { title: '原登记值', width: 110, render: (_, r) => `${r.old_value} ${r.unit}` },
    { title: '实测值', width: 110, render: (_, r) => <b style={{ color: '#c92a2a' }}>{r.new_value} {r.unit}</b> },
    { title: '偏差', dataIndex: 'delta_pct', width: 80, render: (v) => <Tag color={v >= 20 ? 'red' : 'orange'}>+{v}%</Tag> },
    { title: '状态', dataIndex: 'status', width: 90, render: (s) => s === 'proposed' ? <Tag color="gold">待确认</Tag> : s === 'applied' ? <Tag color="green">已回写</Tag> : <Tag>已忽略</Tag> },
    {
      title: '操作', key: 'ops', width: 130,
      render: (_, r) => r.status === 'proposed' ? (
        <Space size={4}>
          <Button size="small" type="primary" ghost loading={busy} onClick={() => void apply([r.id])}>应用</Button>
          <Button size="small" loading={busy} onClick={() => void ignore(r.id)}>忽略</Button>
        </Space>
      ) : r.status === 'applied' ? <Typography.Text type="secondary" style={{ fontSize: 11 }}>IR2 v{r.applied_ir2_version}</Typography.Text> : null,
    },
  ]

  return (
    <Card
      size="small"
      title="参数变更提案（E1 反向回写）"
      extra={
        pending.length > 0 && (
          <Button size="small" type="primary" loading={busy} onClick={() => void apply(pending.map((p) => p.id))}>
            全部应用（{pending.length}）
          </Button>
        )
      }
    >
      <Alert
        type="info" showIcon style={{ marginBottom: 10 }}
        message="FreeCAD 微调实测几何与登记参数存在自动可映射的偏差（仅长度类联动项）。应用后 IR2 追加新版本并触发下游重算与审阅关②复审；忽略则维持原值。"
      />
      <Table rowKey="id" size="small" pagination={false} columns={columns} dataSource={proposals} />
    </Card>
  )
}
const REWORK_OPTIONS: Record<string, { value: string; label: string }[]> = {
  gate1: [
    { value: 'A2', label: 'A2 方案生成（默认）' },
    { value: 'A1', label: 'A1 需求解析' },
  ],
  gate2: [
    { value: 'A2', label: 'A2 方案生成' },
    { value: 'A3', label: 'A3 CAD 建模' },
  ],
}

export function ReviewTab({
  sessionId,
  gate,
  paused,
  ir1,
  ir2,
  ir3,
  ir4,
  reviews,
  onGoReport,
  onDecided,
}: {
  sessionId: string
  gate: 'gate1' | 'gate2' | null
  paused: boolean
  ir1: Ir1 | null
  ir2: Ir2 | null
  ir3: Ir3 | null
  ir4: Ir4 | null
  reviews: ReviewRecord[]
  onGoReport?: () => void
  onDecided: () => void
}) {
  const { message } = AntdApp.useApp()
  const roles = useAuthStore((s) => s.roles)
  const canDecide = hasAnyRole(roles, ['admin', 'reviewer'])
  // T8.5：gate2 裁决前提示活跃微调会话——确保审阅的即交付的版本（D8-3 衔接语义）
  const tunesQuery = useQuery({
    queryKey: ['cad-tune', sessionId],
    queryFn: () => listTunes(sessionId),
    enabled: gate === 'gate2',
  })
  const tuneSum = summarizeTunes(gate === 'gate2' ? tunesQuery.data?.sessions : undefined)

  const [traceParam, setTraceParam] = useState<Ir2Param | null>(null)
  const [kbRefParam, setKbRefParam] = useState<Ir2Param | null>(null)
  const [decision, setDecision] = useState<'approved' | 'rejected'>('approved')
  const [reworkStage, setReworkStage] = useState<string>(gate === 'gate2' ? (ir4?.issues.find((i) => i.suggestion)?.suggestion?.replace('back_to_', '') ?? 'A2') : 'A2')
  const [annotations, setAnnotations] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function submit() {
    if (decision === 'rejected' && !annotations.trim()) {
      message.warning('驳回必须填写批注（整改依据）')
      return
    }
    if (decision === 'approved' && ir2 && params(ir2).length === 0) {
      Modal.confirm({
        title: 'IR2 为 0 项参数（降级产物），确认放行空方案？',
        content: '空方案无法支撑 CAD 建模与合规校验，A4 的「pass」只是无可校验项。建议驳回并回退 A2（LLM 恢复后重算）；若批准，空方案将继续走到知识沉淀。',
        okText: '仍要批准',
        okButtonProps: { danger: true },
        cancelText: '返回驳回',
        onOk: () => doSubmit(),
      })
      return
    }
    doSubmit()
  }

  async function doSubmit() {
    setSubmitting(true)
    try {
      const effects = await postReview(sessionId, gate ?? 'gate1', {
        decision,
        annotations: annotations.trim() ? [annotations.trim()] : [],
        reworkStage: decision === 'rejected' ? reworkStage : undefined,
      })
      message.success(
        decision === 'approved'
          ? `已批准，流程进入${effects.resumed_to ? stageLabel(effects.resumed_to) : '下一阶段'}`
          : `已驳回，回退${stageLabel(effects.rework_stage)}重算（原 ${effects.invalidated?.length ?? 0} 份下游产物已作废）`,
      )
      setAnnotations('')
      onDecided()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '裁决提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  const errCount = ir4?.issues.filter((i) => i.severity === 'error').length ?? 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {paused && gate && (
        <Alert
          type="warning"
          showIcon
          message={`${GATE_TITLE[gate]} 待人工裁决`}
          description={
            <div style={{ fontSize: 12.5, lineHeight: 1.9 }}>
              {gate === 'gate1' ? (
                <>
                  ① 查看下方 IR1 需求解析与 IR2 参数方案表（<b>点击参数行</b>可打开溯源抽屉）；<br />
                  ② 滚动到本页底部「裁决」面板，选择批准或驳回整改；<br />
                  ③ 驳回须选择回退目标并填写批注，提交后触发局部回算。
                </>
              ) : (
                <>
                  ① 先到{' '}
                  <a onClick={() => onGoReport?.()}>「校验报告」页签查看 IR4 合规结论</a>
                  {ir4 && errCount > 0 ? `（当前 fail，${errCount} 项 error）` : '（含 IR3 建模产物）'}；<br />
                  ② 回到本页底部「裁决」面板，选择批准或驳回整改；<br />
                  ③ 驳回须选择回退目标并填写批注，IR4 的回退建议会标注在对应选项上。
                </>
              )}
              <div>
                <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
                  裁决按钮在本页底部；仅 admin / reviewer 可操作，其他角色可查看。
                </Typography.Text>
              </div>
            </div>
          }
        />
      )}
      {paused && gate && !canDecide && (
        <Alert type="info" showIcon message="当前角色为只读，可查看但不可裁决。" />
      )}
      {paused && gate === 'gate1' && ir2 && params(ir2).length === 0 && (
        <Alert
          type="error"
          showIcon
          message="IR2 参数方案为 0 项（降级流程生成）"
          description="空方案无法支撑 CAD 建模：建议驳回并回退 A2，待 LLM 恢复后重算；直接批准会在 A3 产生无效模型。"
        />
      )}

      {ir1 && (
        <Card
          title="IR1 需求解析"
          size="small"
          extra={ir1Degraded(ir1) ? <Tag color="warning">降级解析（LLM 规划不可用）</Tag> : undefined}
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(64px, 88px) 1fr', rowGap: 6, fontSize: 12.5 }}>
            {ir1.objectives.map((o) => (
              <div key={o.dimension} style={{ display: 'contents' }}>
                <span style={{ color: '#8b949e' }}>{o.dimension}</span>
                <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
                  {String(o.value)}{' '}
                  <Tag style={{ fontSize: 10, lineHeight: '14px', marginInlineEnd: 0 }}>{o.explicit ? '显式' : '推断'}</Tag>
                </span>
              </div>
            ))}
          </div>
          {(() => {
            const { dims } = normalizeMissingItems(ir1.missing_items)
            if (dims.length === 0) return null
            return (
              <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
                缺失项（A2 将提议并标注）：{dims.join('、')}
              </Typography.Text>
            )
          })()}
        </Card>
      )}

      {ir2 ? (
        <Card
          title={`IR2 参数方案表（${params(ir2).length} 项）`}
          extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>点击参数行溯源反查</Typography.Text>}
          styles={{ body: { padding: 0 } }}
        >
          {params(ir2).length === 0 ? (
            <div style={{ padding: '20px 16px' }}>
              <Alert
                type="error"
                showIcon
                message="IR2 无参数（A2 由降级流程生成）"
                description="建议驳回并回退 A2 / A1，待 LLM 恢复后重算；不建议批准空方案进入建模。"
              />
            </div>
          ) : (
            <ParamTable
              params={params(ir2)}
              onTrace={setTraceParam}
              onKbRef={(p) => setKbRefParam(p)}
            />
          )}
        </Card>
      ) : (
        <Card size="small"><Typography.Text type="secondary">IR2 尚未生成</Typography.Text></Card>
      )}

      {ir3 && <Ir3Card ir3={ir3} params={params(ir2 ?? { design_document: '', parameters: [] })} />}

      {gate === 'gate2' && <ProposalCard sessionId={sessionId} />}

      {gate && tuneSum.active && (
        <Alert
          type="warning"
          showIcon
          message={`存在进行中的 FreeCAD 微调会话（已 ${tuneSum.active.rounds?.length ?? 0} 轮）`}
          description="设计人员可能仍在修改模型——每轮微调会产生新的 IR3 版本。建议先让其「定稿」再裁决，确保审阅的即交付的版本。"
        />
      )}

      {gate && (
        <Card
          title="裁决"
          extra={<Typography.Text type="secondary" style={{ fontSize: 11.5 }}>批准 = 放行下一阶段 · 驳回 = 回退重算（下游快照失效）</Typography.Text>}
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>裁决</Typography.Text>
              <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <Radio checked={decision === 'approved'} onChange={() => setDecision('approved')}><b>批准</b>，管线恢复运行</Radio>
                <Radio checked={decision === 'rejected'} onChange={() => setDecision('rejected')}><b>驳回整改</b>，触发局部回算（A-11）</Radio>
              </div>
            </div>
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>回退目标{decision === 'rejected' ? '' : '（驳回时生效）'}</Typography.Text>
              <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 6 }}>
                {(REWORK_OPTIONS[gate] ?? REWORK_OPTIONS.gate1).map((o) => (
                  <Radio key={o.value} disabled={decision !== 'rejected'} checked={reworkStage === o.value} onChange={() => setReworkStage(o.value)}>
                    <b>{o.label.split('（')[0]}</b>
                    {gate === 'gate2' && ir4?.issues.some((i) => i.suggestion === `back_to_${o.value}`) && (
                      <Tag color="warning" style={{ marginLeft: 6 }}>IR4 建议</Tag>
                    )}
                  </Radio>
                ))}
              </div>
            </div>
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>批注{decision === 'rejected' ? '（必填）' : ''}</Typography.Text>
              <Input.TextArea
                style={{ marginTop: 6 }}
                rows={3}
                value={annotations}
                onChange={(e) => setAnnotations(e.target.value)}
                placeholder="裁决依据、整改要求…"
              />
              <Space style={{ marginTop: 8, justifyContent: 'flex-end', width: '100%' }}>
                <Button type="primary" loading={submitting} disabled={!canDecide} onClick={submit}>提交裁决</Button>
              </Space>
            </div>
          </div>
        </Card>
      )}

      {reviews.length > 0 && (
        <Card title="裁决历史" size="small" styles={{ body: { padding: 0 } }}>
          <ReviewTable records={reviews} />
        </Card>
      )}

      <TraceDrawer param={traceParam} onClose={() => setTraceParam(null)} />
      <KbRefDrawer
        ref={(() => {
          if (!kbRefParam) return null
          const parsed = parseKbRef(kbRefParam.source_ref)
          return parsed ? { ...parsed, paramName: kbRefParam.name } : null
        })()}
        onClose={() => setKbRefParam(null)}
      />
    </div>
  )
}

function params(ir2: Ir2): Ir2Param[] {
  return ir2.parameters ?? []
}

// ---- IR3 建模产物卡（PHASE-007 T7.5，呈现实测分段与参数联动）----
function fmtMm(v: number): string {
  return v.toLocaleString('zh-CN', { maximumFractionDigits: 1 })
}

function fmtVol(v: number): string {
  if (v >= 1e6) return `${(v / 1e6).toLocaleString('zh-CN', { maximumFractionDigits: 2 })} L`
  return `${(v / 1000).toLocaleString('zh-CN', { maximumFractionDigits: 1 })} cm³`
}

function geomSummary(f: Ir3Feature): string {
  const b = f.geometry.bbox_mm
  if (!Array.isArray(b) || b.length !== 3) return '—'
  const [dx, dy, dz] = b
  const round = typeof dx === 'number' && typeof dy === 'number'
    && Math.abs(dx - dy) / Math.max(dx, dy, 1e-9) < 0.02
  const dims = round
    ? `Ø${fmtMm(dx ?? 0)} × ${fmtMm(dz ?? 0)}`
    : `${fmtMm(dx ?? 0)} × ${fmtMm(dy ?? 0)} × ${fmtMm(dz ?? 0)}`
  const vol = f.geometry.volume_mm3
  const volStr = typeof vol === 'number' && vol > 0 ? ` · ${fmtVol(vol)}` : ''
  return `${dims} mm${volStr}`
}

function Ir3Card({ ir3, params }: { ir3: Ir3; params: Ir2Param[] }) {
  const summary = ir3.geometry_summary
  const paramMap = new Map(params.map((p) => [p.id, p]))
  const hasMeasured = (f: Ir3Feature) => Array.isArray(f.geometry.bbox_mm)

  const columns: ColumnsType<Ir3Feature> = [
    { title: '特征', dataIndex: 'feature_id', width: 90, render: (v) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span> },
    { title: '所属段', dataIndex: 'segment', width: 90, render: (v) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span> },
    { title: '几何摘要', key: 'geom', render: (_, f) => <span style={{ fontSize: 12.5 }}>{geomSummary(f)}</span> },
    {
      title: '联动参数', key: 'linked',
      render: (_, f) => {
        if (!f.linked_param_id) return <Typography.Text type="secondary" style={{ fontSize: 12 }}>—（未联动）</Typography.Text>
        const p = paramMap.get(f.linked_param_id)
        return (
          <span style={{ fontSize: 12.5 }}>
            <span style={{ fontFamily: 'monospace' }}>{f.linked_param_id}</span>
            {p ? ` ${p.name}` : ''}
          </span>
        )
      },
    },
  ]

  return (
    <Card
      title="IR3 建模产物"
      size="small"
      extra={
        summary?.source === 'extraction_failed'
          ? <Tag color="warning">提取未成功</Tag>
          : summary?.source === 'extracted'
            ? <Tag color="success">实测提取</Tag>
            : undefined
      }
      styles={{ body: { padding: 0 } }}
    >
      {summary?.source === 'extraction_failed' ? (
        <div style={{ padding: '12px 16px' }}>
          <Alert
            type="info"
            showIcon
            message="几何提取未成功，无实测特征"
            description={summary.detail || '本轮 A4 仅完成引用完整性校验。'}
          />
        </div>
      ) : ir3.features.length > 0 && !hasMeasured(ir3.features[0]) ? (
        <div style={{ padding: '12px 16px' }}>
          <Alert
            type="info"
            showIcon
            message="旧版本快照：特征未携带实测几何数值"
            description="本会话生成于几何特征真实化（PHASE-007）之前，仅记录参数镜像，无数值校验物。"
          />
        </div>
      ) : (
        <>
          <Table
            rowKey="feature_id"
            size="small"
            dataSource={ir3.features}
            columns={columns}
            pagination={false}
            locale={{ emptyText: '无特征（降级产物）' }}
          />
          {summary?.source === 'extracted' && summary.bbox_mm && (
            <div style={{ padding: '10px 16px', borderTop: '1px solid #f0f0f0', fontSize: 12.5, color: '#595959' }}>
              整体：{summary.part_count ?? '—'} 段 · 总包围盒{' '}
              {summary.bbox_mm.map((v) => fmtMm(v)).join(' × ')} mm
              {typeof summary.volume_mm3 === 'number' && summary.volume_mm3 > 0 ? ` · ${fmtVol(summary.volume_mm3)}` : ''}
            </div>
          )}
        </>
      )}
    </Card>
  )
}

function ReviewTable({ records }: { records: ReviewRecord[] }) {
  const columns: ColumnsType<ReviewRecord> = [
    { title: '审阅关', dataIndex: 'gate', width: 100, render: (v) => gateLabel(v) },
    { title: '裁决', dataIndex: 'decision', width: 90, render: (v) => <Tag color={v === 'approved' ? 'success' : 'error'}>{v === 'approved' ? '批准' : '驳回'}</Tag> },
    {
      title: '审阅人', key: 'reviewer', width: 110,
      render: (_, r) => <span style={{ fontSize: 12 }}>{r.reviewer_name || (r.reviewer_id ? `${r.reviewer_id.slice(0, 8)}…` : '—')}</span>,
    },
    { title: '批注', key: 'annotations', render: (_, r) => <ReviewAnnotation record={r} /> },
    { title: '时间', dataIndex: 'decided_at', width: 170, render: (v) => <span style={{ fontSize: 12 }}>{String(v).replace('T', ' ').slice(0, 19)}</span> },
  ]
  return <Table rowKey="id" size="small" dataSource={records} columns={columns} pagination={false} />
}
