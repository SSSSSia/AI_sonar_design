import { useState } from 'react'
import { App, Alert, Button, Card, Input, Modal, Radio, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { postReview, type Ir3, type Ir4, type Ir4Issue, type Ir5, type ReviewRecord } from '../../api/workflow'
import { ApiError } from '../../api/client'
import { hasAnyRole, useAuthStore } from '../../stores/authStore'
import { ReviewAnnotation } from '../../components/ReviewAnnotation'
import { CandidateContent, CandidateDedupTag, CandidateKindTag, writebackToastText } from '../../components/CandidateContent'
import { gateLabel, WRITEBACK_LABEL, checkLabel } from '../../lib/stageLabel'

const SEVERITY_META: Record<string, { label: string; color: string }> = {
  error: { label: '错误', color: 'error' },
  warning: { label: '警告', color: 'warning' },
  info: { label: '提示', color: 'default' },
}

export function ReportTab({
  sessionId,
  ir4,
  ir4Version,
  ir5,
  ir5Version,
  ir3,
  reviews,
  pipelineStatus,
  onDecided,
}: {
  sessionId: string
  ir4: Ir4 | null
  ir4Version?: number
  ir5: Ir5 | null
  ir5Version?: number
  ir3: Ir3 | null
  reviews: ReviewRecord[]
  pipelineStatus?: string
  onDecided: () => void
}) {
  const { message } = App.useApp()
  const roles = useAuthStore((s) => s.roles)
  const canDecide = hasAnyRole(roles, ['admin', 'reviewer'])
  const [annotations, setAnnotations] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const knowledgePending = ir5?.writeback_status === 'pending'
  const newCandidates = ir5?.candidates.filter((c) => c.dedup_status === 'new') ?? []
  const dupCount = ir5 ? ir5.candidates.length - newCandidates.length : 0
  const allDuplicates = !!ir5 && ir5.candidates.length > 0 && newCandidates.length === 0

  function confirmWrite() {
    Modal.confirm({
      title: allDuplicates
        ? `确认本会话无新增知识（${dupCount} 条重复）？`
        : `确认写入 ${newCandidates.length} 条知识候选？`,
      content: allDuplicates
        ? '候选均与库内已有知识重复，批准后不会写入任何内容，仅记录本次确认归档。也可选「全部跳过」直接关闭。'
        : `写入知识图谱后成为共享知识，后续新会话即可复用；${dupCount > 0 ? `${dupCount} 条重复项自动放弃（不会重复写入）。` : ''}驳回则本会话候选不入图。`,
      okText: allDuplicates ? '确认归档' : '批准写入',
      cancelText: '再想想',
      onOk: () => decideKnowledge('approved'),
    })
  }

  async function decideKnowledge(decision: 'approved' | 'rejected') {
    setSubmitting(true)
    try {
      const effects = await postReview(sessionId, 'knowledge', {
        decision,
        annotations: annotations.trim() ? [annotations.trim()] : [],
      })
      if (decision === 'rejected') message.success('本次知识沉淀已跳过——候选不入库，会话流程结束')
      else if (effects.writeback === 'failed') message.warning(writebackToastText(effects.writeback))
      else message.success(effects.writeback ? writebackToastText(effects.writeback) : '知识归档完成')
      onDecided()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '裁决提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card
        title={<>IR4 合规校验报告 {ir4Version ? <Typography.Text code style={{ fontSize: 12 }}>v{ir4Version}</Typography.Text> : null}</>}
        extra={ir4 ? <Tag color={ir4.overall === 'fail' ? 'error' : 'success'}>{ir4.overall === 'fail' ? '总体结论 fail' : '总体结论 pass'}</Tag> : null}
      >
        {!ir4 ? (
          <Typography.Text type="secondary">IR4 尚未生成</Typography.Text>
        ) : (
          <IssueGroups issues={ir4.issues} />
        )}
      </Card>

      {ir5 && (
        <Card
          title={<>IR5 知识沉淀候选 {ir5Version ? <Typography.Text code style={{ fontSize: 12 }}>v{ir5Version}</Typography.Text> : null}</>}
          extra={<Tag color={ir5.writeback_status === 'written' ? 'success' : 'warning'}>{WRITEBACK_LABEL[ir5.writeback_status] ?? ir5.writeback_status}</Tag>}
          styles={{ body: { padding: 0 } }}
        >
          <CandidateTable candidates={ir5.candidates} ir3={ir3} />
          <div style={{ padding: '10px 16px', borderTop: '1px solid #f0f1f3' }}>
            <Alert
              type="info"
              showIcon
              message={
                pipelineStatus === 'completed' && knowledgePending
                  ? '管线已完成，知识沉淀确认可随时补做（完成前未确认的候选不会自动入图）'
                  : '知识审阅关（A-10）：未经人工确认不会写入图谱；重复项不静默覆盖。'
              }
              description="沉淀作用：过审方案提炼为知识（参数关系 / 设计案例 / 约束经验）写入图谱后，后续会话的 A1 检索与 A2 参数参考即可复用——系统随使用积累越用越准。"
              style={{ marginBottom: 10 }}
            />
            {knowledgePending && allDuplicates && (
              <Alert
                type="info" showIcon style={{ marginBottom: 10 }}
                message={`本会话 ${dupCount} 条候选均与知识库已有内容重复`}
                description="A5 会把本会话知识与库内（图谱+语料）自动比对：全部重复说明方案参数均源自现有库、未产生新知识，属正常结果。可「全部跳过」直接关闭本次沉淀（推荐），「确认归档」也不会重复写入任何内容。"
              />
            )}
            {knowledgePending && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12, alignItems: 'start' }}>
                <div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>裁决</Typography.Text>
                  <div style={{ marginTop: 6 }}>
                    <Radio checked disabled>
                      {allDuplicates
                        ? `无新增知识（${dupCount} 条候选均与库内重复）`
                        : `确认 ${newCandidates.length} 条新增知识入图${dupCount > 0 ? `（另 ${dupCount} 条重复自动放弃）` : ''}`}
                    </Radio>
                  </div>
                </div>
                <div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>批注（可选，留痕到裁决记录）</Typography.Text>
                  <Input.TextArea style={{ marginTop: 6 }} rows={2} value={annotations} onChange={(e) => setAnnotations(e.target.value)} placeholder="确认/放弃依据…" />
                  <SpaceForButtons>
                    <Button size="small" type="primary" disabled={!canDecide} loading={submitting} onClick={confirmWrite}>
                      {allDuplicates ? '确认归档' : '批准写入'}
                    </Button>
                    <Button size="small" disabled={!canDecide} loading={submitting} onClick={() => decideKnowledge('rejected')}>全部跳过</Button>
                  </SpaceForButtons>
                </div>
              </div>
            )}
          </div>
        </Card>
      )}

      {reviews.length > 0 && (
        <Card title="裁决历史" size="small" styles={{ body: { padding: 0 } }}>
          <Table<ReviewRecord>
            rowKey="id" size="small" dataSource={reviews} pagination={false}
            columns={[
              { title: '审阅关', dataIndex: 'gate', width: 110, render: (v) => gateLabel(v) },
              { title: '裁决', dataIndex: 'decision', width: 90, render: (v) => <Tag color={v === 'approved' ? 'success' : 'error'}>{v === 'approved' ? '批准' : '驳回'}</Tag> },
              {
                title: '审阅人', key: 'reviewer', width: 110,
                render: (_, r) => <span style={{ fontSize: 12 }}>{r.reviewer_name || (r.reviewer_id ? `${r.reviewer_id.slice(0, 8)}…` : '—')}</span>,
              },
              { title: '批注', key: 'a', render: (_, r) => <ReviewAnnotation record={r} /> },
              { title: '时间', dataIndex: 'decided_at', width: 170, render: (v) => <span style={{ fontSize: 12 }}>{String(v).replace('T', ' ').slice(0, 19)}</span> },
            ]}
          />
        </Card>
      )}
    </div>
  )
}

function SpaceForButtons({ children }: { children: React.ReactNode }) {
  return <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>{children}</div>
}

function IssueGroups({ issues }: { issues: Ir4Issue[] }) {
  const order: Ir4Issue['severity'][] = ['error', 'warning', 'info']
  const groups = order
    .map((sev) => ({ sev, items: issues.filter((i) => i.severity === sev) }))
    .filter((g) => g.items.length > 0)
  if (issues.length === 0) {
    return <Typography.Text type="secondary">无问题项——各项检查通过。</Typography.Text>
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {groups.map((g) => (
        <div key={g.sev}>
          <Typography.Text strong style={{ fontSize: 12.5 }}>
            {SEVERITY_META[g.sev].label}（{g.items.length}）
          </Typography.Text>
          <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {g.items.map((i) => (
              <div key={i.constraint_id} style={{ border: '1px solid #f0f1f3', borderLeft: `3px solid ${g.sev === 'error' ? '#c92a2a' : g.sev === 'warning' ? '#d97706' : '#d1d5db'}`, borderRadius: 6, padding: '8px 12px' }}>
                <div style={{ fontSize: 12.5 }}>
                  <Tag color={SEVERITY_META[g.sev].color} style={{ marginInlineEnd: 6 }}>{checkLabel(i.constraint_id)}</Tag>
                  <span className="mono" style={{ fontSize: 10.5, color: '#8b949e', marginRight: 6 }}>{i.constraint_id}</span>
                  {i.suggestion && <Tag color="warning">建议 {i.suggestion.replace('back_to_', '回退 ')}</Tag>}
                </div>
                {i.message && <div style={{ fontSize: 12, color: '#57606a', marginTop: 4, lineHeight: 1.7 }}>{i.message}</div>}
                {(() => {
                  // 关联参数/特征去重 + 超量截断（审查 P2-16：避免同名重复列举占行）
                  const cap = (arr: string[]) => {
                    const uniq = [...new Set(arr.filter(Boolean))]
                    return uniq.length > 6 ? `${uniq.slice(0, 6).join('、')} 等 ${uniq.length} 项` : uniq.join('、')
                  }
                  const params = cap(i.related_params ?? [])
                  const feats = cap(i.related_features ?? [])
                  if (!params && !feats) return null
                  return (
                    <div style={{ fontSize: 11, color: '#8b949e', marginTop: 4, fontFamily: 'monospace' }}>
                      {[params && `参数 ${params}`, feats && `特征 ${feats}`].filter(Boolean).join(' · ')}
                    </div>
                  )
                })()}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

function CandidateTable({ candidates, ir3 }: { candidates: Ir5['candidates']; ir3: Ir3 | null }) {
  const columns: ColumnsType<Ir5['candidates'][number]> = [
    { title: '类型', dataIndex: 'type', width: 80, render: (v) => <CandidateKindTag type={v} /> },
    {
      title: '内容',
      key: 'payload',
      render: (_, r) => <CandidateContent candidate={r} />,
    },
    {
      title: '去重',
      dataIndex: 'dedup_status',
      width: 90,
      render: (v) => <CandidateDedupTag status={v} />,
    },
  ]
  return (
    <Table
      rowKey={(_, i) => String(i)}
      size="small"
      dataSource={candidates}
      columns={columns}
      pagination={false}
      locale={{ emptyText: ir3 ? '本会话无候选知识' : 'IR5 尚未生成' }}
    />
  )
}
