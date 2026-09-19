import { useState } from 'react'
import {
  Alert, App as AntdApp, Button, Card, Input, Modal, Radio, Space, Table, Tag, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { postReview, type Ir1, type Ir2, type Ir2Param, type Ir4, type ReviewRecord } from '../../api/workflow'
import { ApiError } from '../../api/client'
import { ParamTable } from '../../components/ParamTable'
import { TraceDrawer } from '../../components/TraceDrawer'
import { ReviewAnnotation } from '../../components/ReviewAnnotation'
import { hasAnyRole, useAuthStore } from '../../stores/authStore'
import { ir1Degraded, normalizeMissingItems } from '../../lib/stageLabel'

const GATE_TITLE: Record<string, string> = { gate1: '审阅关①（IR2 参数方案）', gate2: '审阅关②（IR3 + IR4）' }
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
  ir4,
  reviews,
  onDecided,
}: {
  sessionId: string
  gate: 'gate1' | 'gate2' | null
  paused: boolean
  ir1: Ir1 | null
  ir2: Ir2 | null
  ir4: Ir4 | null
  reviews: ReviewRecord[]
  onDecided: () => void
}) {
  const { message } = AntdApp.useApp()
  const roles = useAuthStore((s) => s.roles)
  const canDecide = hasAnyRole(roles, ['admin', 'reviewer'])

  const [traceParam, setTraceParam] = useState<Ir2Param | null>(null)
  const [decision, setDecision] = useState<'approved' | 'rejected'>('approved')
  const [reworkStage, setReworkStage] = useState<string>(gate === 'gate2' ? (ir4?.issues.find((i) => i.suggestion)?.suggestion?.replace('back_to_', '') ?? 'A2') : 'A2')
  const [annotations, setAnnotations] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function submit() {
    if (decision === 'rejected' && !annotations.trim()) {
      message.warning('驳回必须填写批注（整改依据）')
      return
    }
    if (decision === 'approved' && gate === 'gate1' && ir2 && params(ir2).length === 0) {
      Modal.confirm({
        title: 'IR2 为 0 项参数，确认放行空方案？',
        content: '空方案无法支撑 CAD 建模，进入 A3 大概率失败。建议驳回并回退 A2（LLM 恢复后重算）。',
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
          ? `已批准，管线恢复至 ${effects.resumed_to ?? '下一阶段'}`
          : `已驳回，回退 ${effects.rework_stage}，失效 ${effects.invalidated?.length ?? 0} 份下游快照`,
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
          message={`${GATE_TITLE[gate]} 待裁决（仅 admin / reviewer 可操作）`}
          description={
            ir4 && errCount > 0
              ? `校验结论 fail（${errCount} 项 error）。批准进入下一阶段；驳回触发局部回算。`
              : '批准后管线恢复运行；驳回需选择回退目标并填写批注。'
          }
          action={
            <Button danger={decision === 'rejected'} type="primary" size="small" disabled={!canDecide} loading={submitting} onClick={submit}>
              提交裁决
            </Button>
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
            <ParamTable params={params(ir2)} onTrace={setTraceParam} />
          )}
        </Card>
      ) : (
        <Card size="small"><Typography.Text type="secondary">IR2 尚未生成</Typography.Text></Card>
      )}

      {gate && (
        <Card title="裁决">
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
    </div>
  )
}

function params(ir2: Ir2): Ir2Param[] {
  return ir2.parameters ?? []
}

function ReviewTable({ records }: { records: ReviewRecord[] }) {
  const columns: ColumnsType<ReviewRecord> = [
    { title: '审阅关', dataIndex: 'gate', width: 100 },
    { title: '裁决', dataIndex: 'decision', width: 90, render: (v) => <Tag color={v === 'approved' ? 'success' : 'error'}>{v === 'approved' ? '批准' : '驳回'}</Tag> },
    { title: '批注', key: 'annotations', render: (_, r) => <ReviewAnnotation record={r} /> },
    { title: '时间', dataIndex: 'decided_at', width: 170, render: (v) => <span style={{ fontSize: 12 }}>{String(v).replace('T', ' ').slice(0, 19)}</span> },
  ]
  return <Table rowKey="id" size="small" dataSource={records} columns={columns} pagination={false} />
}
