import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Drawer, Space, Spin, Tag, Timeline, Typography } from 'antd'
import { getTrace, type Ir2Param, type TraceChain } from '../api/workflow'

/** 溯源反查抽屉：参数行点击 → trace 链（IR0 原文 → A1 检索引用 → A2 推导 → A4 复核）。 */
export function TraceDrawer({ param, onClose }: { param: Ir2Param | null; onClose: () => void }) {
  const enabled = !!param?.trace_id
  const { data, isLoading, error } = useQuery({
    queryKey: ['trace', param?.trace_id],
    queryFn: () => getTrace(param!.trace_id),
    enabled,
  })

  return (
    <Drawer
      open={!!param}
      onClose={onClose}
      width={460}
      title={<Space><span>溯源链</span><Typography.Text code style={{ fontSize: 12 }}>{param?.trace_id}</Typography.Text></Space>}
      destroyOnClose
    >
      {param && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>参数</Typography.Text>
            <div><b>{param.id} {param.name}</b> = {String(param.value)} {param.unit}</div>
            <div style={{ marginTop: 4 }}>
              <Tag>{param.source_type}</Tag>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>{param.source_ref}</Typography.Text>
            </div>
          </div>

          {isLoading && <Spin />}
          {error && <Alert type="warning" showIcon message="溯源链暂不可用" description={String(error)} />}
          {data && <TraceSteps chain={data} />}
        </div>
      )}
    </Drawer>
  )
}

function TraceSteps({ chain }: { chain: TraceChain }) {
  const ctx = chain.context ?? {}
  return (
    <Timeline
      items={[
        ...(ctx.ir0_raw_text
          ? [{
              color: 'blue',
              children: (
                <StepBox title="IR0 需求原文" box={String(ctx.ir0_raw_text).slice(0, 220)} />
              ),
            }]
          : []),
        ...(chain.steps ?? []).map((s) => ({
          color: 'blue',
          children: (
            <StepBox
              title={`${s.actor} · ${s.action}`}
              meta={s.created_at}
              box={s.payload ? JSON.stringify(s.payload, null, 1).slice(0, 400) : undefined}
            />
          ),
        })),
        {
          color: 'gray',
          children: (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              快照是权威出处，链路可随时重建（GET /api/v1/trace/{'{trace_id}'}）
            </Typography.Text>
          ),
        },
      ]}
    />
  )
}

function StepBox({ title, meta, box }: { title: string; meta?: string; box?: string }) {
  return (
    <div>
      <div style={{ fontSize: 13, fontWeight: 600 }}>
        {title} {meta && <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 6 }}>{meta}</Typography.Text>}
      </div>
      {box && (
        <div style={{ marginTop: 4, background: '#fafbfc', border: '1px solid #e5e7eb', borderRadius: 6, padding: '6px 10px', fontSize: 12, color: '#57606a', whiteSpace: 'pre-wrap', lineHeight: 1.7 }}>
          {box}
        </div>
      )}
    </div>
  )
}

/** 参数表行点击按钮（行本体可点击时用于悬停提示）。 */
export function TraceHint() {
  return <Button type="link" size="small" style={{ padding: 0 }}>溯源 ⤷</Button>
}
