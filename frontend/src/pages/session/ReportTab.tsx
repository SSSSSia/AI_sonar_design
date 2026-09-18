import { useState } from 'react'
import { App, Alert, Button, Card, Input, Radio, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { postReview, type Ir3, type Ir4, type Ir4Issue, type Ir5, type ReviewRecord } from '../../api/workflow'
import { ApiError } from '../../api/client'
import { hasAnyRole, useAuthStore } from '../../stores/authStore'
import { annotationsText } from '../../lib/stageLabel'

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
  onDecided,
}: {
  sessionId: string
  ir4: Ir4 | null
  ir4Version?: number
  ir5: Ir5 | null
  ir5Version?: number
  ir3: Ir3 | null
  reviews: ReviewRecord[]
  onDecided: () => void
}) {
  const { message } = App.useApp()
  const roles = useAuthStore((s) => s.roles)
  const canDecide = hasAnyRole(roles, ['admin', 'reviewer'])
  const [annotations, setAnnotations] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const knowledgePending = ir5?.writeback_status === 'pending'
  const newCandidates = ir5?.candidates.filter((c) => c.dedup_status === 'new') ?? []

  async function decideKnowledge(decision: 'approved' | 'rejected') {
    setSubmitting(true)
    try {
      const effects = await postReview(sessionId, 'knowledge', {
        decision,
        annotations: annotations.trim() ? [annotations.trim()] : [],
      })
      message.success(effects.writeback ? `知识审阅完成：${JSON.stringify(effects.writeback).slice(0, 120)}` : '知识审阅完成')
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
          extra={<Tag color={ir5.writeback_status === 'written' ? 'success' : 'warning'}>writeback: {ir5.writeback_status}</Tag>}
          styles={{ body: { padding: 0 } }}
        >
          <CandidateTable candidates={ir5.candidates} ir3={ir3} />
          <div style={{ padding: '10px 16px', borderTop: '1px solid #f0f1f3' }}>
            <Alert
              type="info"
              showIcon
              message="知识审阅关（A-10）：未经人工确认不会写入图谱；重复项不静默覆盖。"
              style={{ marginBottom: 10 }}
            />
            {knowledgePending && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12, alignItems: 'start' }}>
                <div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>裁决</Typography.Text>
                  <div style={{ marginTop: 6 }}>
                    <Radio checked disabled>确认 {newCandidates.length} 条 new 候选入图（duplicate 放弃）</Radio>
                  </div>
                </div>
                <div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>批注</Typography.Text>
                  <Input.TextArea style={{ marginTop: 6 }} rows={2} value={annotations} onChange={(e) => setAnnotations(e.target.value)} placeholder="确认/放弃依据…" />
                  <SpaceForButtons>
                    <Button size="small" type="primary" disabled={!canDecide} loading={submitting} onClick={() => decideKnowledge('approved')}>批准写入</Button>
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
              { title: '审阅关', dataIndex: 'gate', width: 110 },
              { title: '裁决', dataIndex: 'decision', width: 90, render: (v) => <Tag color={v === 'approved' ? 'success' : 'error'}>{v === 'approved' ? '批准' : '驳回'}</Tag> },
              { title: '批注', key: 'a', render: (_, r) => <span style={{ fontSize: 12 }}>{annotationsText(r.annotations) || '—'}</span> },
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
                  <Tag color={SEVERITY_META[g.sev].color} style={{ marginInlineEnd: 6 }}>{i.constraint_id}</Tag>
                  {i.suggestion && <Tag color="warning">建议 {i.suggestion.replace('back_to_', '回退 ')}</Tag>}
                </div>
                {i.message && <div style={{ fontSize: 12, color: '#57606a', marginTop: 4, lineHeight: 1.7 }}>{i.message}</div>}
                <div style={{ fontSize: 11, color: '#8b949e', marginTop: 4, fontFamily: 'monospace' }}>
                  {[i.related_params.length > 0 && `参数 ${i.related_params.join('、')}`, i.related_features.length > 0 && `特征 ${i.related_features.join('、')}`].filter(Boolean).join(' · ')}
                </div>
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
    { title: '类型', dataIndex: 'type', width: 100, render: (v) => <Tag color="blue">{v}</Tag> },
    {
      title: '内容',
      key: 'payload',
      render: (_, r) => {
        const p = r.payload as Record<string, unknown>
        const text = (p.summary as string) ?? (p.description as string) ?? (p.name as string) ?? JSON.stringify(p)
        return <span style={{ fontSize: 12.5 }}>{String(text).slice(0, 160)}</span>
      },
    },
    {
      title: '去重',
      dataIndex: 'dedup_status',
      width: 110,
      render: (v) => (v === 'new' ? <Tag color="success">new</Tag> : <Tag color="warning">duplicate</Tag>),
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
