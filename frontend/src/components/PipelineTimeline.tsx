import { Progress, Typography } from 'antd'
import { CheckCircleFilled, PauseCircleFilled, ExclamationCircleFilled } from '@ant-design/icons'

export interface PipelineNodeState {
  key: string
  label: string
  sub?: string
  gate?: boolean
  state: 'done' | 'active' | 'pending' | 'failed'
}

/**
 * 固定管线时间线（A-01）：A1→A2→gate1→A3→A4→gate2→A5。
 * currentStage 语义：运行中 = 该阶段进行中；gate1/gate2 = 暂停待裁决；completed = 全部完成。
 */
export function buildTimeline(currentStage: string | null | undefined, status: string): PipelineNodeState[] {
  const nodes: { key: string; label: string; gate?: boolean }[] = [
    { key: 'A1', label: 'A1 需求解析' },
    { key: 'A2', label: 'A2 方案生成' },
    { key: 'gate1', label: '审阅关①', gate: true },
    { key: 'A3', label: 'A3 CAD 建模' },
    { key: 'A4', label: 'A4 合规校验' },
    { key: 'gate2', label: '审阅关②', gate: true },
    { key: 'A5', label: 'A5 知识沉淀' },
  ]
  const idx = nodes.findIndex((n) => n.key === currentStage)
  const failed = status === 'failed'
  return nodes.map((n, i) => {
    let state: PipelineNodeState['state'] = 'pending'
    if (status === 'completed') state = 'done'
    else if (failed && idx >= 0 && i <= idx) state = i === idx ? 'failed' : 'done'
    else if (idx >= 0 && i < idx) state = 'done'
    else if (i === idx) state = 'active'
    return { ...n, state }
  })
}

export function PipelineTimeline({ nodes, percent }: { nodes: PipelineNodeState[]; percent?: number }) {
  return (
    <div style={{ padding: '8px 4px 2px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', overflowX: 'auto' }}>
        {nodes.map((n, i) => (
          <div key={n.key} style={{ flex: 1, minWidth: 92, textAlign: 'center', position: 'relative' }}>
            {i > 0 && (
              <span
                style={{
                  position: 'absolute', top: 14, left: '-50%', width: '100%', height: 2,
                  background: n.state === 'pending' ? '#d1d5db' : '#2563eb',
                }}
              />
            )}
            <div
              style={{
                width: 28, height: 28, borderRadius: '50%', margin: '0 auto', position: 'relative', zIndex: 1,
                display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 600,
                background: n.state === 'done' ? '#2563eb' : n.state === 'active' && n.gate ? '#d97706'
                  : n.state === 'failed' ? '#c92a2a' : '#fff',
                border: `2px solid ${n.state === 'pending' ? '#d1d5db' : n.state === 'failed' ? '#c92a2a'
                  : n.state === 'active' && n.gate ? '#d97706' : '#2563eb'}`,
                color: n.state === 'done' || (n.state === 'active' && n.gate) || n.state === 'failed' ? '#fff'
                  : n.state === 'active' ? '#2563eb' : '#8b949e',
                boxShadow: n.state === 'active' && !n.gate ? '0 0 0 3px rgba(37,99,235,.15)' : undefined,
              }}
            >
              {n.state === 'done' ? <CheckCircleFilled /> : n.state === 'active' && n.gate ? <PauseCircleFilled />
                : n.state === 'failed' ? <ExclamationCircleFilled /> : n.key}
            </div>
            <div style={{ marginTop: 6, fontSize: 12, color: n.state === 'pending' ? '#8b949e' : '#1f2329', fontWeight: n.state === 'active' ? 600 : 400 }}>
              {n.label}
            </div>
            {n.sub && <div style={{ fontSize: 11, color: '#8b949e', marginTop: 1 }}>{n.sub}</div>}
          </div>
        ))}
      </div>
      {percent !== undefined && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '10px 4px 0' }}>
          <Progress percent={Math.round(percent)} size="small" style={{ flex: 1, marginBottom: 0 }} showInfo={false} />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>{Math.round(percent)}%</Typography.Text>
        </div>
      )}
    </div>
  )
}
