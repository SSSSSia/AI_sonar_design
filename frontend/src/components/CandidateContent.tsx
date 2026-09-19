import { Tag } from 'antd'
import type { Ir5Candidate } from '../api/workflow'

const KIND_LABEL: Record<string, string> = { relation: '关系', case: '案例', constraint: '约束' }
const DEDUP_LABEL: Record<string, { text: string; color: string }> = {
  new: { text: '新增', color: 'success' },
  duplicate: { text: '重复', color: 'warning' },
}

/** 知识图谱关系谓词中文化（常用于 A5 候选）。 */
const RELATION_LABEL: Record<string, string> = {
  CONTAINS: '包含',
  DETERMINES: '决定',
  DEFINES_CONDITION_FOR: '构成条件',
  AFFECTS: '影响',
  REQUIRES: '要求',
  MEASURED_BY: '度量',
}

function relLabel(r: string): string {
  return RELATION_LABEL[r] ?? r.toLowerCase().replaceAll('_', ' ')
}

/**
 * IR5 知识候选内容（审查 P0-1）：按类型结构化渲染，不再兜底 JSON.stringify。
 *  relation → 「A —关系→ B」+ context 灰字副行；case/constraint → 名称 + context/表达式。
 */
export function CandidateContent({ candidate }: { candidate: Ir5Candidate }) {
  const p = (candidate.payload ?? {}) as Record<string, unknown>

  if (candidate.type === 'relation') {
    const from = (p.from ?? {}) as { name?: string }
    const to = (p.to ?? {}) as { name?: string }
    const relation = typeof p.relation === 'string' ? p.relation : ''
    const context = typeof p.context === 'string' ? p.context : ''
    return (
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 12.5 }}>
          <b>{from.name ?? '？'}</b>
          <span style={{ color: '#57606a', margin: '0 6px' }}>—{relLabel(relation)}→</span>
          <b>{to.name ?? '？'}</b>
        </div>
        {context && (
          <div style={{ fontSize: 11.5, color: '#8b949e', marginTop: 2, overflowWrap: 'anywhere' }}>└ {context}</div>
        )}
      </div>
    )
  }

  const name = typeof p.name === 'string' ? p.name : ''
  const expr = typeof p.expression === 'string' ? p.expression : ''
  const desc = typeof p.description === 'string' ? p.description : ''
  const context = typeof p.context === 'string' ? p.context : ''
  const params = Array.isArray(p.params) ? (p.params as { name?: string; value?: unknown; unit?: string }[]) : []
  const sub = context || desc || (params.length > 0
    ? `参数组 ${params.length} 项：${params.slice(0, 4).map((x) => `${x.name ?? '?'}=${String(x.value)}${x.unit ?? ''}`).join('、')}${params.length > 4 ? ' 等' : ''}`
    : '')
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 12.5, fontWeight: 500 }}>{name || '（未命名）'}</div>
      {expr && <div className="mono" style={{ fontSize: 11, color: '#57606a', marginTop: 2 }}>{expr}</div>}
      {sub && <div style={{ fontSize: 11.5, color: '#8b949e', marginTop: 2, overflowWrap: 'anywhere' }}>└ {sub}</div>}
    </div>
  )
}

/** 候选类型列（中文 Tag）。 */
export function CandidateKindTag({ type }: { type: string }) {
  return <Tag style={{ marginInlineEnd: 0 }}>{KIND_LABEL[type] ?? type}</Tag>
}

/** 候选去重状态列（中文）。 */
export function CandidateDedupTag({ status }: { status: string }) {
  const meta = DEDUP_LABEL[status] ?? { text: status, color: 'default' }
  return <Tag color={meta.color} style={{ marginInlineEnd: 0 }}>{meta.text}</Tag>
}

/** 知识关裁决 toast 文案（审查 P2-5 联动）：effects.writeback 摘要为人话。 */
export function writebackToastText(writeback: unknown): string {
  const w = (writeback ?? {}) as Record<string, unknown>
  const parts: string[] = []
  for (const k of ['formulas', 'relations', 'constraints', 'cases', 'entities', 'nodes', 'edges']) {
    const v = w[k]
    if (typeof v === 'number' && v > 0) parts.push(`${k} ${v}`)
  }
  const skipped = typeof w.skipped === 'number' ? w.skipped : undefined
  if (parts.length === 0 && skipped === undefined) {
    // 后端语义未枚举全时退回简短 JSON，但截断且不再整段倾倒
    return `知识回写完成（${JSON.stringify(w).slice(0, 80)}）`
  }
  return `已写入：${parts.join(' / ') || '无新知识'}${skipped ? `；${skipped} 条重复放弃` : ''}`
}
