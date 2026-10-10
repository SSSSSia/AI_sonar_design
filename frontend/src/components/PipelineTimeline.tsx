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

/** 颜色即状态语义：蓝=进行中/已通过，琥珀=待人工裁决，红=失败，灰=未到。 */
const C = {
  track: 'rgba(148, 163, 184, .25)',
  progressed: '#06b6d4',
  gate: '#f59e0b',
  failed: '#ef4444',
  text1: '#e2e8f0',
  text3: '#64748b',
}

export function PipelineTimeline({ nodes, percent }: { nodes: PipelineNodeState[]; percent?: number }) {
  return (
    <div style={{ padding: '10px 4px 2px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', overflowX: 'auto' }}>
        {nodes.map((n, i) => {
          const active = n.state === 'active'
          const gateActive = active && n.gate
          const filled = n.state === 'done' || gateActive || n.state === 'failed'
          return (
            <div key={n.key} style={{ flex: 1, minWidth: 92, textAlign: 'center', position: 'relative' }}>
              {i > 0 && (
                <span
                  style={{
                    position: 'absolute', top: 13, left: '-50%', width: '100%', height: 2, borderRadius: 1,
                    background: n.state === 'pending' ? C.track : C.progressed,
                  }}
                />
              )}
              <div
                className={gateActive ? 'gate-pulse' : undefined}
                style={{
                  width: 26, height: 26, borderRadius: '50%', margin: '0 auto', position: 'relative', zIndex: 1,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: n.gate ? 11 : 10.5, fontWeight: 600, fontFamily: 'var(--mono)',
                  background: n.state === 'done' ? C.progressed : gateActive ? C.gate
                    : n.state === 'failed' ? C.failed : '#0f2035',
                  border: `2px solid ${
                    n.state === 'pending' ? C.track
                      : n.state === 'failed' ? C.failed
                      : gateActive ? C.gate : C.progressed}`,
                  color: filled ? '#04121f' : active ? C.progressed : C.text3,
                  boxShadow: active && !n.gate ? '0 0 0 3px rgba(6,182,212,.18), 0 0 12px rgba(6,182,212,.3)' : undefined,
                }}
              >
                {n.state === 'done' ? <CheckCircleFilled />
                  : gateActive ? <PauseCircleFilled />
                  : n.state === 'failed' ? <ExclamationCircleFilled />
                  : n.key}
              </div>
              <div
                style={{
                  marginTop: 6, fontSize: 12,
                  color: n.state === 'pending' ? C.text3 : active ? C.text1 : 'var(--text-2)',
                  fontWeight: active ? 600 : 400,
                }}
              >
                {n.label}
              </div>
              {n.sub && <div className="mono" style={{ fontSize: 10.5, color: C.text3, marginTop: 1 }}>{n.sub}</div>}
            </div>
          )
        })}
      </div>
      {percent !== undefined && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '10px 4px 0' }}>
          <Progress percent={Math.round(percent)} size="small" style={{ flex: 1, marginBottom: 0 }} showInfo={false} />
          <Typography.Text type="secondary" className="mono" style={{ fontSize: 12 }}>
            {Math.round(percent)}%
          </Typography.Text>
        </div>
      )}
    </div>
  )
}
