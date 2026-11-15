import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, App as AntdApp, Button, Card, Checkbox, Form, Input, Modal, Radio, Select, Space, Table, Tag, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  getIrVersion, parseIr, postReview, reviseIr1,
  type Ir1, type Ir2, type Ir2Param, type Ir3, type Ir3Feature, type Ir4, type ReviewRecord,
} from '../../api/workflow'
import { updateParameter, type ParamProposal } from '../../api/cadAgent'
import { ApiError } from '../../api/client'
import { confirmParamProposals, ignoreParamProposal, listParamProposals, listTunes } from '../../api/cadAgent'
import { summarizeTunes } from './tuneSummary'
import { formatReferenceUsed } from './referenceUsed'
import { formatDisplayTransforms, formatParamMapping } from './paramMapping'
import {
  aggregateUnresolved, buildVersionList, diffIr2Params, latestApprovedGate2,
  type ComboVersionRow, type ParamDiffRow,
} from './comboView'
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
    { title: '实测值', width: 110, render: (_, r) => <b style={{ color: '#ef4444' }}>{r.new_value} {r.unit}</b> },
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
  versions,
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
  /** T14.6 审阅版本条件：审阅人实际查看的各 IR 版本（缺省不携带=旧客户端行为） */
  versions?: { IR1?: number; IR2?: number; IR3?: number; IR4?: number; IR5?: number }
  onGoReport?: () => void
  onDecided: () => void
}) {
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()
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
  // T15.6/C-0：IR4 fail 批准的显式人工例外（后端强制校验，前端同步拦截）
  const [exceptionGranted, setExceptionGranted] = useState(false)
  const [exceptionReason, setExceptionReason] = useState('')
  const [editParam, setEditParam] = useState<Ir2Param | null>(null)
  const [editForm] = Form.useForm()
  const [ir1Editing, setIr1Editing] = useState(false)
  const [ir1Form] = Form.useForm()
  const [submitting, setSubmitting] = useState(false)

  async function submit() {
    if (decision === 'rejected' && !annotations.trim()) {
      message.warning('驳回必须填写批注（整改依据）')
      return
    }
    // T15.6/C-0（ADR-014 §5）：IR4 fail 批准必须显式人工例外（granted + 非空 reason）
    if (gate === 'gate2' && decision === 'approved' && ir4?.overall === 'fail'
        && (!exceptionGranted || !exceptionReason.trim())) {
      message.warning('IR4 总体结论为 fail：批准必须勾选「人工例外」确认并填写例外理由（不得静默放行）')
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
      // T14.6（审查用例 09）：携带审阅人实际查看的快照版本——后台生成新版本后
      // 旧页面提交会被 409 SNAPSHOT_STALE 拒绝，提示刷新复核，不自动代批。
      // T15.6/C-0：gate2 批准额外携带 IR4 版本条件与（fail 时）人工例外字段。
      const expected = gate === 'gate1' ? versions?.IR2 : gate === 'gate2' ? versions?.IR3 : versions?.IR5
      const failApproval = gate === 'gate2' && decision === 'approved' && ir4?.overall === 'fail'
      const effects = await postReview(sessionId, gate ?? 'gate1', {
        decision,
        annotations: annotations.trim() ? [annotations.trim()] : [],
        reworkStage: decision === 'rejected' ? reworkStage : undefined,
        expectedSnapshotVersion: expected,
        expectedIr4Version: gate === 'gate2' ? versions?.IR4 : undefined,
        exceptionGranted: failApproval ? true : undefined,
        exceptionReason: failApproval ? exceptionReason.trim() : undefined,
      })
      message.success(
        decision === 'approved'
          ? `已批准，流程进入${effects.resumed_to ? stageLabel(effects.resumed_to) : '下一阶段'}`
          : `已驳回，回退${stageLabel(effects.rework_stage)}重算（原 ${effects.invalidated?.length ?? 0} 份下游产物已作废）`,
      )
      setAnnotations('')
      setExceptionGranted(false)
      setExceptionReason('')
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
          extra={
            <Space size={8}>
              {gate === 'gate1' && paused && hasAnyRole(roles, ['admin', 'designer']) && (
                <Button size="small" onClick={() => {
                  ir1Form.setFieldsValue({
                    objectives: ir1.objectives.map((o) => ({ dimension: o.dimension, value: String(o.value) })),
                    missingText: (ir1.missing_items ?? []).join('\n'),
                  })
                  setIr1Editing(true)
                }}>修订需求（人工核对）</Button>
              )}
              {ir1Degraded(ir1) ? <Tag color="warning">降级解析（LLM 规划不可用）</Tag> : undefined}
            </Space>
          }
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(64px, 88px) 1fr', rowGap: 6, fontSize: 12.5 }}>
            {ir1.objectives.map((o) => (
              <div key={o.dimension} style={{ display: 'contents' }}>
                <span style={{ color: 'var(--text-3)' }}>{o.dimension}</span>
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
          extra={
            <Space size={8}>
              {gate === 'gate2' && paused && hasAnyRole(roles, ['admin', 'designer']) && (
                <Button size="small" onClick={() => { setEditParam(params(ir2)[0] ?? null); editForm.setFieldsValue({ pid: params(ir2)[0]?.id }) }}>
                  修改参数（E1 正向）
                </Button>
              )}
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>点击参数行溯源反查</Typography.Text>
            </Space>
          }
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

      <Modal
        title="IR1 需求人工核对修订（P2-10）"
        open={ir1Editing}
        onCancel={() => setIr1Editing(false)}
        okText="提交并重算 A2"
        width={640}
        onOk={async () => {
          const values = await ir1Form.validateFields()
          const objectives = (values.objectives as { dimension: string; value: string }[])
            .filter((o) => o.dimension?.trim())
            .map((o) => ({ dimension: o.dimension.trim(), value: Number.isNaN(Number(o.value)) ? o.value : Number(o.value), explicit: true }))
          try {
            const r = await reviseIr1(sessionId, {
              objectives,
              missing_items: String(values.missingText ?? '').split('\n').map((x: string) => x.trim()).filter(Boolean),
              reason: '人工核对修订',
            })
            message.success(`IR1 v${r.ir1_version} 已追加（${r.objectives} 项目标），下游失效并重算 A2——完成后回到审阅关①`)
            setIr1Editing(false)
            void queryClient.invalidateQueries({ queryKey: ['ir', sessionId] })
            void queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
          } catch (e) {
            message.error(e instanceof ApiError ? e.message : 'IR1 修订失败')
          }
        }}
      >
        <Alert
          type="info" showIcon style={{ marginBottom: 12 }}
          message="人工核对解析结果（设计 §5.1）：修订目标与缺失项后，IR1 追加新版本并重算 A2（不重新解析原文）。修订的目标将标记为「显式」。"
        />
        <Form form={ir1Form} layout="vertical">
          <Form.List name="objectives">
            {(fields, { add, remove }) => (
              <>
                {fields.map((field) => (
                  <Space key={field.key} align="baseline" style={{ display: 'flex', marginBottom: 4 }}>
                    <Form.Item name={[field.name, 'dimension']} rules={[{ required: true, message: '维度必填' }]}
                      style={{ marginBottom: 0, width: 180 }}>
                      <Input placeholder="维度（如 声阵规模）" />
                    </Form.Item>
                    <Form.Item name={[field.name, 'value']} rules={[{ required: true, message: '取值必填' }]}
                      style={{ marginBottom: 0, width: 200 }}>
                      <Input placeholder="取值" />
                    </Form.Item>
                    <Button type="text" size="small" danger onClick={() => remove(field.name)}>删除</Button>
                  </Space>
                ))}
                <Button type="dashed" size="small" onClick={() => add({ dimension: '', value: '' })} style={{ marginTop: 4 }}>
                  + 添加目标
                </Button>
              </>
            )}
          </Form.List>
          <Form.Item name="missingText" label="缺失项（每行一条）" style={{ marginTop: 12 }}>
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="E1 正向参数更新（文档 → CAD）"
        open={!!editParam}
        onCancel={() => setEditParam(null)}
        okText="更新并重新建模"
        onOk={async () => {
          if (!editParam) return
          const values = await editForm.validateFields()
          if (values.value === undefined || Number.isNaN(Number(values.value)) || Number(values.value) <= 0) {
            message.warning('参数值必须为正数')
            return
          }
          try {
            const r = await updateParameter(sessionId, editParam.id, { value: Number(values.value), reason: values.reason })
            const casc = r.cascaded?.length ? `，依赖重算 ${r.cascaded.length} 项下游公式参数` : ''
            message.success(`已回写 IR2 v${r.ir2_version}（深度 ${r.depth}${casc}），A3 将按新参数重新建模，完成后回到审阅关②复审`)
            setEditParam(null)
            void queryClient.invalidateQueries({ queryKey: ['ir', sessionId] })
            void queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
          } catch (e) {
            message.error(e instanceof ApiError ? e.message : '正向更新失败')
          }
        }}
      >
        <Alert
          type="warning" showIcon style={{ marginBottom: 12 }}
          message={`将修改 ${editParam?.name ?? ''}（${editParam?.id ?? ''}，当前 ${String(editParam?.value ?? '')} ${editParam?.unit ?? ''}）`}
          description="提交后 IR2 追加新版本，IR3/IR4/IR5 失效并重跑 A3/A4——完成后自动回到审阅关②复审（传播深度上限 3）。"
        />
        <Form form={editForm} layout="vertical">
          <Form.Item name="pid" label="参数">
            <Select
              options={params(ir2 ?? { design_document: '', parameters: [] }).map((p) => ({ value: p.id, label: `${p.name}（${p.id}）= ${String(p.value)} ${p.unit}` }))}
              onChange={(v) => setEditParam(params(ir2 ?? { design_document: '', parameters: [] }).find((p) => p.id === v) ?? null)}
            />
          </Form.Item>
          <Form.Item name="value" label="新值" rules={[{ required: true }]}>
            <Input type="number" />
          </Form.Item>
          <Form.Item name="reason" label="变更理由">
            <Input.TextArea rows={2} placeholder="如：任务书修订 / 评审意见" />
          </Form.Item>
        </Form>
      </Modal>

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
            {gate === 'gate2' && decision === 'approved' && ir4?.overall === 'fail' && (
              <div style={{ gridColumn: '1 / -1', border: '1px solid rgba(239,68,68,.4)', borderRadius: 8, padding: '10px 12px' }}>
                <Alert
                  type="error" showIcon style={{ marginBottom: 8 }}
                  message={`IR4 总体结论为 fail（${errCount} 项 error）——按 ADR-014 §5，批准必须携带显式人工例外，不得静默放行`}
                />
                <Checkbox checked={exceptionGranted} onChange={(e) => setExceptionGranted(e.target.checked)}>
                  确认人工例外：本人已复核全部 error 项并决定放行
                </Checkbox>
                <Input.TextArea
                  style={{ marginTop: 8 }} rows={2}
                  value={exceptionReason}
                  onChange={(e) => setExceptionReason(e.target.value)}
                  placeholder="例外理由（必填，将随批准组合凭证入档，并出现在正式批准版文档中）"
                />
              </div>
            )}
          </div>
        </Card>
      )}

      {gate === 'gate2' && (
        <ComboCard
          sessionId={sessionId}
          reviews={reviews}
          versions={versions}
          ir1={ir1}
          ir2={ir2}
          ir4={ir4}
        />
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
  const refLine = formatReferenceUsed(ir3.reference_used)
  const mapLine = formatParamMapping(ir3.param_mapping)
  const displayLine = formatDisplayTransforms(ir3.display_transforms)

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
            <div style={{ padding: '10px 16px', borderTop: '1px solid var(--hairline)', fontSize: 12.5, color: 'var(--text-2)' }}>
              整体：{summary.part_count ?? '—'} 段 · 总包围盒{' '}
              {summary.bbox_mm.map((v) => fmtMm(v)).join(' × ')} mm
              {typeof summary.volume_mm3 === 'number' && summary.volume_mm3 > 0 ? ` · ${fmtVol(summary.volume_mm3)}` : ''}
            </div>
          )}
        </>
      )}
      {refLine && (
        <div style={{ padding: '8px 16px', borderTop: '1px solid var(--hairline)', fontSize: 12.5, color: 'var(--text-2)' }}>
          {refLine}
        </div>
      )}
      {mapLine && (
        <div style={{ padding: '8px 16px', borderTop: '1px solid var(--hairline)', fontSize: 12.5, color: 'var(--text-2)' }}>
          {mapLine}
        </div>
      )}
      {displayLine && (
        <div style={{ padding: '8px 16px', borderTop: '1px solid var(--hairline)', fontSize: 12.5, color: 'var(--text-2)' }}>
          {displayLine}
        </div>
      )}
    </Card>
  )
}

// ---- 批准组合卡（T15.6/C-2 首批读侧，ADR-014 §5）：组合版本清单/未决项聚合/全参数 diff。
//      只读视图——不承担批准判定（服务端政策校验为准）；无批准凭证（历史会话）时整体隐藏。 ----
function ComboCard({ sessionId, reviews, versions, ir1, ir2, ir4 }: {
  sessionId: string
  reviews: ReviewRecord[]
  versions?: { IR1?: number; IR2?: number; IR3?: number; IR4?: number; IR5?: number }
  ir1: Ir1 | null
  ir2: Ir2 | null
  ir4: Ir4 | null
}) {
  const combo = latestApprovedGate2(reviews)
  const comboIr2Version = combo?.metadata.snapshot_combo?.IR2?.version ?? null
  const latestIr2Version = versions?.IR2 ?? null
  // 组合 IR2 ≠ 当前 latest IR2 → 按版本拉取批准时内容做 diff（getIrVersion，C-2 读侧配套端点）
  const needsHistorical = comboIr2Version != null && latestIr2Version != null && comboIr2Version !== latestIr2Version
  const historicalQuery = useQuery({
    queryKey: ['ir-version', sessionId, 'IR2', comboIr2Version],
    queryFn: () => getIrVersion(sessionId, 'IR2', comboIr2Version as number),
    enabled: needsHistorical,
  })
  if (!combo) return null
  const policy = combo.metadata.combo_policy
  const comboIr2: Ir2 | null = needsHistorical ? parseIr<Ir2>(historicalQuery.data) : ir2
  const diffRows = diffIr2Params(comboIr2?.parameters, ir2?.parameters)
  const changedRows = diffRows.filter((r) => r.state !== 'same')
  const unresolved = aggregateUnresolved(policy, ir4, ir1)
  const versionRows = buildVersionList(combo.metadata.snapshot_combo, {
    IR2: versions?.IR2, IR3: versions?.IR3, IR4: versions?.IR4,
  })
  const exception = policy?.exception
  const overallColor = policy?.ir4_overall === 'pass' ? 'success'
    : policy?.ir4_overall === 'fail' ? 'error' : 'warning'

  const versionColumns: ColumnsType<ComboVersionRow> = [
    { title: 'IR', dataIndex: 'ir_type', width: 70, render: (v) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span> },
    { title: '组合版本', dataIndex: 'combo_version', width: 90, render: (v) => (v == null ? '—' : `v${v}`) },
    { title: '当前最新', dataIndex: 'latest_version', width: 90, render: (v) => (v == null ? '—' : `v${v}`) },
    {
      title: '钉定状态', key: 'pinned', width: 150,
      render: (_, r) => r.pinned_is_latest == null
        ? <Tag>—</Tag>
        : r.pinned_is_latest
          ? <Tag color="success">组合即最新</Tag>
          : <Tag color="warning">已有新版本（正式导出将被拒绝）</Tag>,
    },
    { title: '快照ID', dataIndex: 'snapshot_id', render: (v) => <span style={{ fontFamily: 'monospace', fontSize: 11.5 }}>{v ?? '—'}</span> },
    { title: '内容哈希', dataIndex: 'sha16', width: 160, render: (v) => <span style={{ fontFamily: 'monospace', fontSize: 11.5 }}>{v ? `${v}…` : '—'}</span> },
  ]
  const diffColumns: ColumnsType<ParamDiffRow> = [
    { title: '参数ID', dataIndex: 'param_id', width: 90, render: (v) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span> },
    { title: '名称', dataIndex: 'name', width: 150, ellipsis: true },
    { title: '批准时值', dataIndex: 'combo_value', width: 130 },
    { title: '当前值', dataIndex: 'latest_value', width: 130 },
    {
      title: '状态', dataIndex: 'state', width: 100,
      render: (s) => s === 'changed' ? <Tag color="orange">已变更</Tag>
        : s === 'added' ? <Tag color="blue">批准后新增</Tag>
        : s === 'removed' ? <Tag color="red">批准后移除</Tag>
        : <Tag>一致</Tag>,
    },
  ]

  return (
    <Card
      title="批准版本组合（gate2 批准凭证）"
      size="small"
      extra={
        <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
          批准于 {String(combo.record.decided_at).replace('T', ' ').slice(0, 19)} · 政策 v{String(combo.metadata.policy_version ?? '1')}
        </Typography.Text>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            批准时 IR4 总体结论：
          </Typography.Text>{' '}
          <Tag color={overallColor}>{String(policy?.ir4_overall ?? 'unknown')}</Tag>
          {exception?.granted && (
            <Typography.Text style={{ fontSize: 12.5, color: '#ef4444' }}>
              人工例外已授予：{exception.reason}
            </Typography.Text>
          )}
          <Table rowKey="ir_type" size="small" pagination={false} columns={versionColumns} dataSource={versionRows} />
        </div>
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            未决项（凭证入档 + 现场聚合，{unresolved.length} 项）
          </Typography.Text>
          <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {unresolved.length === 0 ? (
              <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>无未决项</Typography.Text>
            ) : unresolved.map((u) => (
              <Tag key={`${u.source}::${u.ref}`} color={u.source === 'IR1' ? 'blue' : 'gold'} style={{ fontSize: 11 }}>
                {u.source}：{u.ref}{u.from_record ? '' : '（现场）'}
              </Tag>
            ))}
          </div>
        </div>
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            全参数对照（批准组合 IR2{comboIr2Version != null ? ` v${comboIr2Version}` : ''} vs 当前{latestIr2Version != null ? ` v${latestIr2Version}` : ''}）——{changedRows.length} 项差异
          </Typography.Text>
          {needsHistorical && historicalQuery.isLoading ? (
            <Typography.Text type="secondary" style={{ fontSize: 12.5, display: 'block', marginTop: 6 }}>组合 IR2 内容读取中…</Typography.Text>
          ) : comboIr2 == null ? (
            <Typography.Text type="secondary" style={{ fontSize: 12.5, display: 'block', marginTop: 6 }}>
              批准组合 IR2 内容不可读（可能已被清理），参数对照不可用——正式导出同样将被拒绝。
            </Typography.Text>
          ) : changedRows.length === 0 ? (
            <Typography.Text type="secondary" style={{ fontSize: 12.5, display: 'block', marginTop: 6 }}>
              参数与批准组合一致（批准后未发生参数变更）。
            </Typography.Text>
          ) : (
            <Table rowKey="param_id" size="small" pagination={false} columns={diffColumns} dataSource={changedRows} style={{ marginTop: 6 }} />
          )}
        </div>
      </div>
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
