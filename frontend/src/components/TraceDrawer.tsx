import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Drawer, Space, Spin, Timeline, Typography } from 'antd'
import { getTrace, type Ir2Param, type TraceChain, type TraceStep } from '../api/workflow'
import { fmtTime } from '../lib/stageLabel'
import { SOURCE_CN, SourceBadge, ConfidenceBadge } from './sourceMeta'

const ACTION_LABEL: Record<string, string> = {
  parse: '解析', retrieve: '检索', derive: '推导', compute: '计算', generate: '生成',
  validate: '校验', confirm: '确认', assign: '关联', review: '审阅', extract: '抽取',
  transform: '建模',
}

const CONF_CN: Record<string, string> = { high: '高', medium: '中', low: '低' }
const SEV_CN: Record<string, string> = { error: '错误', warning: '警告', info: '提示' }

/** trace 步骤 payload → 可读句子（审查反馈：case/estimated 等裸枚举不可读、来源指向模糊）。
 *  A2 步骤 2026-09-20 起带四件套（source_ref/derivation/confidence），旧链路缺字段时降级显示。 */
function payloadToText(p: Record<string, unknown>): string {
  const lines: string[] = []
  const name = typeof p.name === 'string' ? p.name : ''
  const unit = typeof p.unit === 'string' && p.unit !== '-' ? ` ${p.unit}` : ''
  const value = p.value !== undefined ? String(p.value) : ''

  // A2 derive：参数取值 + 来源类型（带指向）+ 置信度 + 推导依据
  if (name || value) {
    lines.push(`参数 ${name || '？'}${value ? ` = ${value}${unit}` : ''}`)
  }
  if (p.source_type) {
    const cn = SOURCE_CN[p.source_type as string] ?? String(p.source_type)
    lines.push(`来源：${cn}${typeof p.source_ref === 'string' && p.source_ref ? `（${p.source_ref}）` : ''}`)
  }
  if (p.confidence) lines.push(`置信度：${CONF_CN[p.confidence as string] ?? p.confidence}`)
  if (typeof p.derivation === 'string' && p.derivation) lines.push(`推导依据：${p.derivation}`)

  // A3 transform：特征 ← 驱动参数
  if (p.feature_id) {
    lines.push(`特征 ${p.feature_id}${p.segment ? `（${p.segment}）` : ''}${p.linked_param_id ? ` ← 由参数 ${p.linked_param_id} 驱动` : ''}`)
  }

  // A4 validate：校验项 + 级别 + 消息 + 建议
  if (p.constraint_id) {
    lines.push(`合规校验 ${p.constraint_id}：${SEV_CN[p.severity as string] ?? p.severity ?? '—'}`)
  }
  if (typeof p.message === 'string' && p.message) lines.push(p.message)
  if (typeof p.suggestion === 'string' && p.suggestion) {
    lines.push(`建议：${p.suggestion.startsWith('back_to_') ? p.suggestion.replace('back_to_', '回退 ') : p.suggestion}`)
  }

  // A5 assign：知识候选
  if (p.candidate_index !== undefined) {
    lines.push(`提炼为知识候选 #${p.candidate_index}（${p.type ?? '—'}）`)
  }

  // 通用：关联参数/子查询/命中知识
  if (Array.isArray(p.related_params) && p.related_params.length > 0) {
    lines.push(`关联参数 ${p.related_params.slice(0, 6).join('、')}${p.related_params.length > 6 ? ` 等 ${p.related_params.length} 项` : ''}`)
  }
  if (Array.isArray(p.sub_queries) && p.sub_queries.length > 0) lines.push(`子查询 ${p.sub_queries.length} 条`)
  if (Array.isArray(p.retrieved) && p.retrieved.length > 0) lines.push(`命中知识 ${p.retrieved.length} 条`)

  if (lines.length > 0) return lines.join('\n')
  // 未识别的步骤类型：如实给出技术兜底，但明确标注这不是给人读的内容
  const json = JSON.stringify(p, null, 1)
  return `（未识别的步骤类型，原始数据如下）\n${json.length > 400 ? `${json.slice(0, 400)}…` : json}`
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
            <div style={{ marginTop: 4, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
              <SourceBadge type={param.source_type} />
              {param.confidence && <ConfidenceBadge value={param.confidence} />}
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>{param.source_ref}</Typography.Text>
            </div>
            {typeof param.derivation === 'string' && param.derivation && (
              <Typography.Paragraph
                type="secondary" style={{ fontSize: 11.5, marginTop: 6, marginBottom: 0 }}
                ellipsis={{ rows: 2, expandable: true, symbol: '展开推导依据' }}
              >
                {param.derivation}
              </Typography.Paragraph>
            )}
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
        <div style={{ marginTop: 4, background: 'var(--bg-deep)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', fontSize: 12, color: 'var(--text-2)', whiteSpace: 'pre-wrap', lineHeight: 1.7 }}>
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
