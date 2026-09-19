import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Drawer, Space, Spin, Tag, Timeline, Typography } from 'antd'
import { getTrace, type Ir2Param, type TraceChain, type TraceStep } from '../api/workflow'
import { fmtTime } from '../lib/stageLabel'

const ACTION_LABEL: Record<string, string> = {
  parse: '解析', retrieve: '检索', derive: '推导', compute: '计算', generate: '生成',
  validate: '校验', confirm: '确认', assign: '关联', review: '审阅', extract: '抽取',
}

/** trace 步骤 payload → 可读句子（审查 P1-2）：不再整段 JSON 倾倒，未知形状退回截断 JSON。 */
function payloadToText(p: Record<string, unknown>): string {
  const lines: string[] = []
  const name = typeof p.name === 'string' ? p.name : ''
  const unit = typeof p.unit === 'string' && p.unit !== '-' ? ` ${p.unit}` : ''
  const value = p.value !== undefined ? String(p.value) : ''
  if (name || value) lines.push(`参数 ${name || '?'}${value ? ` = ${value}${unit}` : ''}`.replace('参数 ?', '参数'))
  for (const k of ['source_type', 'confidence']) {
    if (typeof p[k] === 'string' && p[k]) lines.push(`${k === 'source_type' ? '来源' : '置信度'} ${p[k]}`)
  }
  if (typeof p.derivation === 'string' && p.derivation) lines.push(p.derivation)
  if (typeof p.expression === 'string' && p.expression) lines.push(p.expression)
  if (typeof p.reason === 'string' && p.reason) lines.push(p.reason)
  if (typeof p.message === 'string' && p.message) lines.push(p.message)
  if (Array.isArray(p.related_params) && p.related_params.length > 0) {
    lines.push(`关联参数 ${p.related_params.slice(0, 6).join('、')}${p.related_params.length > 6 ? ` 等 ${p.related_params.length} 项` : ''}`)
  }
  if (Array.isArray(p.sub_queries) && p.sub_queries.length > 0) lines.push(`子查询 ${p.sub_queries.length} 条`)
  if (Array.isArray(p.retrieved) && p.retrieved.length > 0) lines.push(`命中知识 ${p.retrieved.length} 条`)
  if (lines.length > 0) return lines.join('\n')
  const json = JSON.stringify(p, null, 1)
  return json.length > 400 ? `${json.slice(0, 400)}…` : json
}

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
            <div><b>{param.id} {param.name}</b> = {String(param.value)} {param.unit && param.unit !== '-' ? param.unit : ''}</div>
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

function stepTitle(s: TraceStep): string {
  const actor = s.actor === 'a1' ? 'A1 需求解析' : s.actor === 'a2' ? 'A2 方案生成'
    : s.actor === 'a4' ? 'A4 合规校验' : s.actor === 'a5' ? 'A5 知识沉淀' : s.actor === 'review' ? '人工审阅' : s.actor
  return `${actor} · ${ACTION_LABEL[s.action] ?? s.action}`
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
              title={stepTitle(s)}
              meta={fmtTime(s.created_at)}
              box={s.payload ? payloadToText(s.payload) : undefined}
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
